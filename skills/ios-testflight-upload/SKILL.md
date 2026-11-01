---
name: ios-testflight-upload
description: >-
  Build an iOS release IPA (with the project's own toolchain, or take a pre-built
  one) and upload it to TestFlight. Use this whenever
  the user wants to ship a build to TestFlight / beta testers — phrases like
  "TestFlightにあげて", "TestFlightにアップロード", "ベータ配信して", "upload to TestFlight",
  "push a beta build", or "get this on TestFlight". Also use it as the build+upload
  step inside an App Store submission (see ios-appstore-submit). It handles the
  common Apple upload failures (ITMS-90087 unsupported architecture from simulator
  build contamination, ITMS-90189 redundant build number) and prefers Apple's
  altool because the asc uploader sometimes stalls. macOS only.
---

# iOS TestFlight Upload

Build a release IPA and upload it to TestFlight reliably. This skill encodes a
workflow that has repeatedly bitten on the details — read the **Gotchas** section,
because skipping the clean step or using the wrong uploader is what turns a 5-minute
task into an hour of failed uploads.

**macOS required** (Xcode provides `altool`, `lipo`, `plutil`).

## Prerequisites (one-time, account-level)

See `docs/setup-ios.md` in this repository. In short:
- `asc` CLI installed and authenticated (`asc auth status` shows a credential). The
  App Store Connect API key ID comes from there.
- The matching `.p8` private key is in `~/.appstoreconnect/private_keys/AuthKey_<KEYID>.p8`
  (altool looks there automatically).
- Issuer ID resolvable via `asc auth issuer-id`.
- Xcode plus whatever toolchain builds the app; automatic signing configured for the
  app's bundle ID + team.

## Parameters: `ship.env`

All per-app values come from `ship.env` at the root of the app project (see
`ship.env.example`). Never hardcode them in commands.

| Key | Meaning |
|---|---|
| `IOS_APP_ID` | App Store Connect numeric app ID, e.g. `1234567890` (`asc apps list --output table`) |
| `IOS_BUNDLE_ID` | e.g. `com.example.myapp` (`PRODUCT_BUNDLE_IDENTIFIER` in the Xcode project) |
| `IOS_TEAM_ID` | Apple Developer Team ID, e.g. `ABCDE12345` (`DEVELOPMENT_TEAM` in the Xcode project's `project.pbxproj`) |
| `ASC_KEY_ID`, `ASC_ISSUER_ID` | optional overrides; normally read from `asc auth` |
| `IOS_BUILD_CMD`, `IOS_IPA_PATH`, `IOS_CLEAN_CMD` | optional: the command that builds a release IPA, where the IPA lands, and a cache-clearing command. `IOS_IPA_PATH` alone uploads a pre-built IPA. Values per toolchain: `docs/build-recipes.md` |
| `VERSION_NAME`, `BUILD_NUMBER` | optional: the version / build you intend to ship; the script stops if the IPA says otherwise |

If no build keys are set and the script can't detect a default for the project, it
stops and lists the keys to set. Ask the user how they build a release IPA (or where a
pre-built one is) and add the keys, using `docs/build-recipes.md`.

If `ship.env` is missing, ask the user for the values (propose what you can infer from
the Xcode project and `asc apps list`) and create it. Load it in your shell with
`set -a; source ship.env; set +a` before running the manual commands below.

## Workflow

### 1. Pick a build number that won't collide

The build number (CFBundleVersion) must be **strictly greater** than every build
already uploaded, or the upload fails with **ITMS-90189 (Redundant Binary Upload)**.

```bash
asc builds list --app "$IOS_APP_ID" --output table | head -6   # see the latest build number
```
Bump the build number in your project's version source so it exceeds that (and update
`BUILD_NUMBER` in `ship.env` if you use it). The marketing
version (`x.y.z`) only needs bumping if a distinct store version is wanted — for a
pure TestFlight build, bumping just the build number is fine.

### 2. Build + verify + upload

The script `scripts/build_and_upload.sh` does the whole reliable path: clean → release
IPA via a disk-export ExportOptions → architecture verification → `altool` upload.
Prefer it over running steps by hand.

```bash
bash <skill-dir>/scripts/build_and_upload.sh /path/to/project
```

It reads `ship.env` from the project root (override with `SHIP_ENV=/path/to/ship.env`)
and fails with a message naming any missing variable. It runs `IOS_CLEAN_CMD` and
`IOS_BUILD_CMD` (or the default it detects for the project), then picks up the IPA at
`IOS_IPA_PATH` and refuses a file the build didn't just write. With only `IOS_IPA_PATH`
set it skips clean and build and verifies + uploads that file. It auto-discovers the
key ID (`asc auth status`) and issuer (`asc auth issuer-id`). If you run it manually,
the steps are below — the order and the two verification gates matter.

#### 2a. Clean first — this is not optional

```bash
cd <project>
bash -c "$IOS_CLEAN_CMD"    # or your toolchain's clean
```
**Why:** if the project was recently built for the simulator, some frameworks can
stay cached as **x86_64-only** simulator slices, and the release archive embeds them.
The upload then dies with **ITMS-90087 (Unsupported Architecture)** — sometimes
reported together with 90086/90203/91169. Clearing the build caches forces a fresh
device build so every framework is arm64.

#### 2b. Build the IPA to disk (don't auto-upload)

Use an export-mode `ExportOptions.plist`. A repo's own `ExportOptions.plist` often has
`destination: upload`, which makes the export upload directly and silently, leaving no
IPA on disk — that costs you control and can cause build-number conflicts. Write a
disk-export plist instead (the script writes this one and passes its path to the build
command as `$EXPORT_OPTIONS_PLIST`):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>destination</key><string>export</string>
  <key>method</key><string>app-store-connect</string>
  <key>manageAppVersionAndBuildNumber</key><false/>
  <key>signingStyle</key><string>automatic</string>
  <key>teamID</key><string>ABCDE12345</string>
  <key>stripSwiftSymbols</key><true/>
  <key>uploadSymbols</key><true/>
</dict></plist>
```
(Replace `ABCDE12345` with `$IOS_TEAM_ID`.)
```bash
export EXPORT_OPTIONS_PLIST="$TMPDIR/ExportOptions_disk.plist"
bash -c "$IOS_BUILD_CMD"    # → the IPA at $IOS_IPA_PATH
```

#### 2c. Verify the IPA before uploading

Two cheap checks that save a failed 50 MB upload:

```bash
IPA=<the IPA from 2b>
# version + build are what you intended
unzip -W -p "$IPA" 'Payload/*.app/Info.plist' | plutil -extract CFBundleShortVersionString raw -
unzip -W -p "$IPA" 'Payload/*.app/Info.plist' | plutil -extract CFBundleVersion raw -
# NO binary has a simulator slice (x86_64/i386/armv7). If any does → 2a again.
```
The script does the full `lipo -info` sweep over the main executable and
`Payload/*.app/Frameworks/*`. If a stray architecture shows up, do **not** upload —
clean and rebuild.

#### 2d. Upload with altool (reliable)

```bash
xcrun altool --upload-app --type ios -f "$IPA" \
  --apiKey <KEY_ID> --apiIssuer <ISSUER_ID>
```
Look for `UPLOAD SUCCEEDED with no errors`. **Why altool over `asc publish
testflight`:** the `asc` uploader has been observed to stall/retry-loop for many
minutes ("Uploading…" with no commit) while altool finishes the same transfer in
seconds. Both ultimately use Apple's ContentDelivery service, so if altool also
loops it's a transient Apple/network issue — wait and retry rather than switching
tools again.

### 3. Confirm it landed

altool only uploads; Apple then processes the build (about 5–15 min). Poll until it
appears VALID:

```bash
asc builds list --app "$IOS_APP_ID" --output table | head -4   # new build, Processing VALID
```

Distribution notes:
- **Internal** TestFlight testers (App Store Connect users) automatically get every
  processed build — no group assignment needed.
- Trying to assign a build to an *internal* group errors with "Cannot add internal
  group to a build." That's expected; ignore it.
- For **external** testers you must add the build to an external group and pass beta
  review: `asc publish testflight --app "$IOS_APP_ID" --build <BUILD_ID> --group <EXTERNAL_GROUP_ID>`.
  Adding a build to an external group sends it to Beta App Review and notifies
  testers — **ask the user before doing this.**

### 4. Always add a "What to Test" diff note (REQUIRED)

Every TestFlight upload must get a **What to Test note describing the diff** — what
changed in this build and what the tester should verify — so testers know what's new
vs. the previous build. Do not skip this; it's part of shipping a build.

The build must be registered first (wait until it appears in `asc builds list`, even
if still Processing). Then set the note, written in the language your testers read:

```bash
asc builds test-notes create --app "$IOS_APP_ID" --build-number <NN> --version <x.y.z> \
  --locale "en-US" --whats-new "- Fixed XX (crash when YY)
- Added ZZ
Please check: ..."
```

`--locale en-US` is just the notes' localization slot — the *content* can be in any
language. Use `test-notes update` instead of `create` if a note for that locale already
exists. Compose the note from the actual diff since the last uploaded build (the
fixes/features in this session), not a generic message.

### 5. Report

Tell the user the version, build number, and processing state. If anything is left for
them (external group review, a manual click in App Store Connect), say exactly what and
where.

## Gotchas quick reference

| Symptom | Cause | Fix |
|---|---|---|
| ITMS-90087 / 90086 / 90203 / 91169 on upload | simulator (x86_64) framework slice in the IPA | clean the build caches (`IOS_CLEAN_CMD` or your toolchain's clean) → rebuild; verify no x86_64 slice with `lipo -info` over all frameworks |
| ITMS-90189 Redundant Binary | build number already uploaded | bump the build number (your project's version source, and `BUILD_NUMBER` if set), rebuild |
| `asc` upload stuck at "Uploading…" for many minutes | flaky asc uploader | kill it, use `altool` |
| Build "uploaded" by itself but no IPA on disk | the project's `ExportOptions.plist` has `destination: upload` | use the disk-export plist above (`$EXPORT_OPTIONS_PLIST`) |
| Script stops with `Don't know how to build this project's IPA` | no build keys and no default for this project | set `IOS_BUILD_CMD` + `IOS_IPA_PATH` (or just `IOS_IPA_PATH` for a pre-built IPA); see `docs/build-recipes.md` |
| Script stops with `... is older than this build` | the build wrote its IPA somewhere other than `IOS_IPA_PATH` | fix `IOS_IPA_PATH` |
| Build never appears after upload | still processing, or processing failed | wait; if it vanishes, processing rejected it — check the email from Apple and re-verify the architectures |
| "Cannot add internal group to a build." | internal testers get every build automatically | ignore |
| Script stops with `IOS_TEAM_ID is not set in .../ship.env` | missing config | fill in `ship.env` (see `ship.env.example`) |
