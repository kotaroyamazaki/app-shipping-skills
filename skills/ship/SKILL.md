---
name: ship
description: >-
  Entry point that ships a mobile app end to end: reads ship.env and runs the other
  app-shipping skills in the right order — iOS (TestFlight upload → App Store version →
  review submission), Android (AAB → Google Play track), AdMob (app registration + ad
  units + IDs into the code), or all of them. Use when the user says "/ship", "ship it",
  "ship ios", "ship android", "release this app", "出荷して", "リリースして",
  "ストアに出して", "iOSもAndroidも出して", "全部提出して", "審査まで持っていって",
  or wants one command that goes from working tree to submitted for review. Stops and
  asks before every outward-facing step (review submission, production rollout, AdMob
  creation).
---

# ship — orchestrator

`/ship` runs the other skills in this repository in order. It owns **order, stop
conditions and resume points only**; every actual step lives in the skill it names.
Do not re-implement those steps here — open the named skill and follow it.

```
/ship ios                      # TestFlight → App Store version → preflight → ask → submit
/ship android [--track T]      # AAB → Play track (default production; internal/alpha/beta) → ask before production
/ship admob                    # AdMob app + ad units → IDs into the code (asks before creating)
/ship all                      # admob first (IDs go into the build), then ios, then android
```

## 0. Load and validate `ship.env` (every run)

1. Find `ship.env` at the project root (the directory containing the app's build
   config).
2. If it is missing, create it from `ship.env.example`: infer what you can and show the
   proposed values to the user for confirmation before writing:
   - `IOS_BUNDLE_ID` — `PRODUCT_BUNDLE_IDENTIFIER` in the Xcode project's `project.pbxproj`
   - `IOS_TEAM_ID` — `DEVELOPMENT_TEAM` in the same file
   - `IOS_APP_ID` — `asc apps list --output table`, matched by bundle ID
   - `ANDROID_PACKAGE` — `applicationId` in the app module's `build.gradle(.kts)`
   - `PLAY_SERVICE_ACCOUNT_JSON` — default `~/.config/app-shipping/play-service-account.json`
   - `ADMOB_PUBLISHER_ID` — ask, or list accounts with the admob skill
   - Build keys (`IOS_BUILD_CMD` / `IOS_IPA_PATH`, `ANDROID_BUILD_CMD` / `ANDROID_AAB_PATH`,
     optional clean commands) — only when the upload scripts can't detect a default for
     the project. Ask the user how they build release artifacts and use
     `docs/build-recipes.md`.
3. Check only the keys the requested target needs:

   | Target | Required keys | Also required |
   |---|---|---|
   | ios | `IOS_APP_ID`, `IOS_BUNDLE_ID`, `IOS_TEAM_ID` | macOS, Xcode, `asc auth status` OK (`docs/setup-ios.md`) |
   | android | `ANDROID_PACKAGE`, `PLAY_SERVICE_ACCOUNT_JSON` (file exists) | `fastlane`, signing config (`docs/setup-android.md`) |
   | admob | `ADMOB_PUBLISHER_ID` (optional if the account has one publisher) | AdMob OAuth set up (`docs/setup-admob.md`) |

4. `ship.env` must never contain secrets — only IDs and paths to keys stored under
   `~/.config/app-shipping/`. Never put a key's contents in it, and never read the
   contents of the key files themselves. Whether to commit `ship.env` is the user's call.

If a prerequisite is missing, **stop** and point the user to the matching `docs/setup-*.md`.

## 1. Read the version once

Read the version `x.y.z` and build number `NN` once: `VERSION_NAME` / `BUILD_NUMBER` in
`ship.env` if set, otherwise your project's version source. When iOS and Android share
one version source, decide the bump before either build:
- iOS build number must exceed the latest in `asc builds list` (ios-appstore-submit Step 1).
- Android versionCode must exceed the highest on Play (android-playstore-submit, Parameters).
Bump to a value that satisfies both, show the user the chosen `x.y.z` / `NN`, and continue.

## 2. `/ship ios`

| # | Stage | Skill / section | Stop if |
|---|---|---|---|
| 1 | Version check | ios-appstore-submit, Step 1 | the marketing-version choice is non-obvious → ask |
| 2 | Build + TestFlight upload + What to Test note | ios-testflight-upload, Workflow 1–4 | script exits non-zero (ITMS-90087, ITMS-90189, signing, altool error) |
| 3 | Wait for processing | ios-testflight-upload, Step 3 | build not `VALID` after ~30 min, or it disappears (processing rejected it) |
| 4 | Create App Store version + attach build | ios-appstore-submit, Step 3 (and 3a) | `asc release stage` fails (often an old `asc`: upgrade and retry) |
| 5 | Preflight | ios-appstore-submit, Step 4 (`asc review doctor`) | `blockingCount > 0` → fix per the doctor's remediation, re-run |
| 6 | **Ask the user** | — | no explicit yes → stop here; the version stays staged, nothing is sent |
| 7 | Submit for review | ios-appstore-submit, Step 4 | harness blocks the command → hand the exact command to the user |
| 8 | Verify | ios-appstore-submit, Step 5 | state is not `WAITING_FOR_REVIEW` |

Optional: header image for the product page → app-store-header-image (has its own
review and its own "ask first" step). Not part of the default `/ship ios` run.

## 3. `/ship android`

| # | Stage | Skill / section | Stop if |
|---|---|---|---|
| 1 | Choose track | default `production`; `--track internal/alpha/beta` from the user | — |
| 2 | Version check | android-playstore-submit, Parameters (`google_play_track_version_codes`) | versionCode not greater than the highest on Play |
| 3 | Release notes | android-playstore-submit, Workflow 3 | — |
| 4 | **Ask the user** (production only; confirm track for others) | android-playstore-submit, Workflow 4 | no explicit yes → stop; nothing uploaded |
| 5 | Build + verify + upload | android-playstore-submit `scripts/build_and_upload.sh` (`--confirm-production` only after the yes) | non-zero exit; the script prints a HINT for permission / Billing 8.0 / child-safety / target SDK errors |
| 6 | Report | android-playstore-submit, Workflow 5 | — |

Never fall back to browser automation for Play Console uploads (see that skill).
Permission errors, the child-safety declaration and category changes are **user actions**
in Play Console — hand them off with the exact menu path and wait.

## 4. `/ship admob`

Delegate entirely to the **admob** skill: list/create the AdMob app for the store ID,
create the ad units, and write the IDs into the app's code/config. The admob CLI is
dry-run by default; **ask the user before running anything with `--yes`**. If the token
has expired (weekly), follow the admob skill's re-auth instructions.

## 5. `/ship all`

Run `admob` first (its IDs must be in the code before the builds), then `ios`, then
`android`, each with its own stop conditions and confirmations. Skip `admob` when the
app already has its AdMob IDs in place. One confirmation never covers another stage: ask separately before AdMob
creation, the App Store submission, and the Play production release. If one target
stops, report it and ask whether to continue with the next target.

## 6. Resuming after a failure

Every stage is idempotent or detectable from the store's state, so resume by
re-checking state rather than re-running everything:

| Failed at | Check | Resume from |
|---|---|---|
| iOS build/upload | `asc builds list --app "$IOS_APP_ID"` — is build `NN` there? | present → stage 3 (wait); absent → fix the error, re-run stage 2. Never re-upload the same `NN` (ITMS-90189): bump it |
| iOS processing | build missing after upload | processing rejected it: check Apple's email, fix, bump `NN`, stage 2 |
| iOS version staging | `asc status --app "$IOS_APP_ID"` — does version `x.y.z` exist with the build? | exists → stage 5; otherwise re-run stage 4 |
| iOS submit | `asc status` shows `inFlight` submission? | `WAITING_FOR_REVIEW` → done; a stale submission in the way → cancel it (ask), then stage 7 |
| Android build | — | fix (see the skill's "Build failures" section), re-run the script; nothing reached Play |
| Android upload | `google_play_track_version_codes` for the track | code present → done; absent → fix the Play-side error, re-run the script (it rebuilds; pass `--skip-clean` to save time) |
| AdMob | admob list commands | create only what is missing |

Always tell the user which stage stopped, why, and the exact next action (theirs or
yours).
