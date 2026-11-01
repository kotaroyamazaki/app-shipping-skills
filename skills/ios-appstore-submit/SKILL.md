---
name: ios-appstore-submit
description: >-
  Submit an iOS app version to App Store review — the full pipeline:
  verify/choose the version, build and upload the binary to TestFlight, create the
  App Store version with metadata, check for blockers, and submit for review. Use
  this whenever the user wants to ship an update to the App Store / put a version up
  for review — phrases like "ストア提出", "App Storeに提出", "審査に出して", "リリース提出して",
  "submit to the App Store", "send it for review", "push an update to the store". It
  always does a version check first (so you never collide with an in-review or
  already-approved version) and includes the TestFlight upload as a built-in step,
  delegating the build+upload mechanics to ios-testflight-upload. macOS only.
---

# iOS App Store Submission

End-to-end: take an iOS app from working tree → a build in review on the App
Store. This **includes** the TestFlight upload — it does not assume a build already
exists. The build+upload mechanics (clean build, arch verification, altool upload,
the ITMS-90087 / ITMS-90189 gotchas) live in the **ios-testflight-upload** skill; read
that skill and reuse its `scripts/build_and_upload.sh`. This skill adds the
version-checking and App-Store-version/submission layer on top.

Prerequisites are the same as ios-testflight-upload (`docs/setup-ios.md`): `asc`
authenticated, signing set up, macOS with Xcode.

## Parameters: `ship.env`

Read `IOS_APP_ID`, `IOS_BUNDLE_ID`, `IOS_TEAM_ID` from `ship.env` at the app root (see
`ship.env.example`). If it is missing, ask the user and create it. Load it with
`set -a; source ship.env; set +a`.

## Step 1 — Version check (always do this first)

The point of this step is to never submit a build number that's already taken, and to
choose the right *marketing* version given what's already on the store.

```bash
asc version                                                  # see the "Old asc versions" gotcha below
asc status --app "$IOS_APP_ID" --output table                # current App Store version + state
asc builds list --app "$IOS_APP_ID" --output table | head -6 # latest uploaded build number
```
Read the version `x.y.z` and build number `NN` you are about to ship — `VERSION_NAME` /
`BUILD_NUMBER` in `ship.env` if set, otherwise your project's version source (the app
target's `MARKETING_VERSION` / `CURRENT_PROJECT_VERSION`, or whatever file your toolchain
generates them from) — and decide:

- **Build number `NN`** must be **strictly greater** than the latest uploaded build
  (else ITMS-90189 on upload).
- **Marketing version `x.y.z`**: choose based on the current App Store version's state
  (the `state` field from `asc status`):
  - `READY_FOR_DISTRIBUTION` / `PENDING_DEVELOPER_RELEASE` / any *live or approved*
    version, **or** a version currently `WAITING_FOR_REVIEW` / `IN_REVIEW`:
    you cannot reuse that version string for new work — **bump to a new version**
    (e.g. 3.6.3 → 3.6.4). A submission needs its own distinct version.
  - `REJECTED` / `DEVELOPER_REJECTED` (a failed review you're fixing): you may **keep
    the same marketing version** and only bump the build number — this is the
    "revise and resubmit" path. If a stale review submission is still attached, cancel
    it first (Step 4 note).
- Update the project's version source (and `ship.env` if it pins them) to the chosen
  `x.y.z` / `NN`. **Confirm the plan with the user** if
  the marketing-version decision is non-obvious.

## Step 2 — Build + upload to TestFlight

Run the ios-testflight-upload flow. The binary you submit to the App Store is the same
one TestFlight processes — uploading once covers both.

```bash
bash <ios-testflight-upload-skill-dir>/scripts/build_and_upload.sh /path/to/project
```
Then wait until the build is processed (poll `asc builds list`, Processing `VALID`),
and capture its **build ID** (the leftmost UUID column) — you need it to attach.

```bash
BUILD_ID=$(asc builds list --app "$IOS_APP_ID" --output json | \
  python3 -c 'import sys,json;d=json.load(sys.stdin);rows=d if isinstance(d,list) else d.get("data",[]);print(next(b.get("id") for b in rows if (b.get("attributes",b)).get("version")=="<NN>"))')
```
(Or just read it from the table.)

## Step 3 — Stage the App Store version

Create the new version, copy metadata (description, keywords, privacy, What's New)
from the most recent approved version, and attach the build — all in one deterministic
command:

```bash
asc release stage --app "$IOS_APP_ID" --version <x.y.z> --build <BUILD_ID> \
  --copy-metadata-from <PREVIOUS_VERSION> --confirm
```
**Why copy-metadata-from:** a brand-new App Store version starts with empty
localizations; Apple blocks submission without description/keywords/privacy. Copying
from the last shipped version (e.g. 3.6.3) fills every locale instantly. The copied
"What's New" is a valid fallback — if it doesn't describe this version's changes, update
it (Step 3a).

This only creates/edits a draft version; nothing is sent to Apple yet.

### Step 3a — (optional) update "What's New"

If the release notes should reflect this version specifically, update the primary
locale(s). Get the version ID from `asc status` (the `versionId`), list localizations,
then update:
```bash
asc localizations list --app "$IOS_APP_ID" --version <VERSION_ID> --locale en-US,ja --output json
asc localizations update --localization-id <LOC_ID> --whats-new "..."   # check exact flag with --help
```
Keep it light — the primary language plus English usually suffice; other locales can keep
the copied notes.

## Step 4 — Check for blockers, ask, then submit

```bash
asc review doctor --app "$IOS_APP_ID" --output table     # want blockingCount: 0
```
Warnings like "subscription has no promotional image" are **non-blocking** (only matter
if you promote the subscription) — don't let them stop you. Only `blockingCount > 0`
needs fixing; the doctor's remediation text tells you what.

If `asc status` shows an existing `inFlight` submission from a previous attempt that's
in the way, cancel it first (ask the user — this withdraws something already with Apple):
```bash
asc submit cancel --id <SUBMISSION_ID> --confirm
```

**Stop and ask the user before submitting.** Show: app, version `x.y.z`, build `NN`,
build ID, `blockingCount`, and the What's New text. Submitting sends the app to Apple
review and, on approval, may release it to the public. Only continue on an explicit yes.

Then submit (this attaches the build if needed and creates the review submission):
```bash
asc review submit --app "$IOS_APP_ID" --version <x.y.z> --build <BUILD_ID> --confirm
```

Some agent harnesses' safety checks block this command in autonomous mode. If that
happens, do not try to work around it — hand the exact command (with real IDs) to the
user to run themselves.

## Step 5 — Verify

```bash
asc status --app "$IOS_APP_ID" --output table
```
Success looks like: App Store `state: WAITING_FOR_REVIEW`, `SUBMISSION inFlight: true`,
`blockingIssueCount: 0`. Report the submission ID and version to the user. Review
typically takes a day to a few days; outcomes arrive by email and show up in `asc status`.

## Notes & gotchas

- **This includes TestFlight.** One upload serves both — don't upload twice.
- **A version pending developer release** still occupies that version string; a new
  submission must use a higher version. If the user also wants to *release* an
  already-approved version, that's `asc versions release` — a separate action from
  submitting a new one (and it publishes to users, so ask first).
- **Don't re-upload an existing build** to "attach" it. `asc publish appstore`
  insists on `--ipa` and would try to re-upload (→ ITMS-90189). Use
  `asc release stage --build <ID>` / `asc review submit --build <ID>`, which reference
  the already-uploaded build.
- **Old `asc` versions** lack or rename commands (`release stage`, `review doctor`,
  `asset-library`, ...). `asc` releases very often; if a command in this skill is
  missing or a flag is rejected, run `brew update && brew upgrade asc` (run
  `brew update` first, or Homebrew may "upgrade" to a stale version), then check
  `asc <command> --help`.
- All the build/upload failure modes (ITMS-90087 simulator-arch contamination,
  ITMS-90189, altool vs asc uploader) are handled by ios-testflight-upload — if Step 2
  fails, debug it there.
