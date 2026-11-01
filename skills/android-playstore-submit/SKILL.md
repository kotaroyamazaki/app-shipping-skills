---
name: android-playstore-submit
description: >-
  Build an Android release App Bundle (AAB) with the project's own toolchain (or
  take a pre-built one) and upload it to Google Play
  (production by default, or internal/alpha/beta) via fastlane supply + a
  service-account JSON key. Use whenever the user wants to ship an Android build
  to Google Play — phrases like "Play Storeに提出", "プレイストア審査提出",
  "Google Playにあげて", "Androidをリリース", "AABをアップロード", "submit to Google Play",
  "push an Android release". It encodes the failures that actually block uploads:
  stale AGP/Gradle configs, Play Billing Library < 8.0 rejection, the
  social/dating child-safety declaration block, service-account per-app
  permission, and the fact that browser-based upload automation does not work.
---

# Android Play Store Submit

Build a release AAB and upload it to Google Play reliably via the Play Developer API
(fastlane supply). This skill encodes a
workflow that has repeatedly bitten on the details — read the **Gotchas** section,
because a stale Gradle config or an old Billing Library will fail the upload
after the (slow) build already succeeded.

## Upload path: fastlane supply, NOT the browser

**Never use browser automation to upload to Google Play Console.** It has been
tried and fails: file-input uploads via browser automation / CDP fail with a generic
"Upload failed" error. Worse — opening sensitive Console pages (Users and permissions
etc.) in an automated browser triggers Google's re-auth flow, which **rejects**
automated browsers and **logs out every Google account in that Chrome profile**.
At most, use a browser for read-only dashboard checks — never for writes.

The reliable path is the Play Developer API via `fastlane run upload_to_play_store`
(install with `brew install fastlane`). Always `export FASTLANE_SKIP_UPDATE_CHECK=1`,
and run from a neutral cwd (an empty temp dir) — a `Fastfile` is not required to use
`fastlane run`.

## Prerequisites (one-time, account-level)

See `docs/setup-android.md` in this repository. In short:

- A service-account JSON key stored outside the repo, e.g.
  `~/.config/app-shipping/play-service-account.json`, with its path in `ship.env` as
  `PLAY_SERVICE_ACCOUNT_JSON`. Validate connectivity with:
  ```bash
  fastlane run validate_play_store_json_key json_key:"$PLAY_SERVICE_ACCOUNT_JSON"
  ```
  → `"Successfully established connection to Google Play Store."`
- **This only proves account-level auth.** Each app must *individually* grant the
  service account access in Play Console → Users and permissions → invite the
  service-account email with release permissions for that app. Otherwise upload
  fails with `Google Api Error: Invalid request - The caller does not have
  permission`. This step is the user's action (requires a browser login) — tell
  them the exact service-account email (the `client_email`; ask the user for it or
  have them copy it from Google Cloud Console → IAM → Service accounts) and re-run the
  upload after they confirm. Permission takes effect immediately, no propagation delay.
- **Never read the JSON key's contents** (`cat`, `python -m json.tool`, etc.) — it
  contains a private key. Only ever pass the *path* to fastlane.

## Parameters: `ship.env` and the project

- `ANDROID_PACKAGE` (in `ship.env`) — the `applicationId` in the app module's
  `build.gradle` (or `build.gradle.kts`), e.g. `com.example.myapp`.
- `PLAY_SERVICE_ACCOUNT_JSON` (in `ship.env`) — path to the key.
- `ANDROID_BUILD_CMD`, `ANDROID_AAB_PATH`, `ANDROID_CLEAN_CMD` (in `ship.env`, optional)
  — the command that builds a signed release AAB, where the AAB lands, and a
  cache-clearing command. `ANDROID_AAB_PATH` alone uploads a pre-built AAB. If no build
  keys are set and the script can't detect a default for the project, it stops and lists
  the keys to set; ask the user how they build a release AAB and add them, using
  `docs/build-recipes.md`.
- If `ship.env` is missing, ask the user and create it (propose the `applicationId` you
  read from Gradle). Load it with `set -a; source ship.env; set +a`.
- versionCode / versionName — `BUILD_NUMBER` / `VERSION_NAME` in `ship.env` if set,
  otherwise your project's version source (`versionCode` / `versionName` in Gradle, or
  the file your toolchain generates them from). **versionCode must be strictly greater**
  than every code previously uploaded to Play for that app, or Play rejects the
  upload. The script needs the versionCode before it builds; if it can't find one it
  stops and asks for `BUILD_NUMBER`.
- Current Play state — **check the track's version codes first** (no browser
  needed):
  ```bash
  cd "$(mktemp -d)" && FASTLANE_SKIP_UPDATE_CHECK=1 fastlane run google_play_track_version_codes \
    package_name:"$ANDROID_PACKAGE" track:production json_key:"$PLAY_SERVICE_ACCOUNT_JSON"   # → Result: [179]
  ```
  If your versionCode is ≤ the highest code listed, bump it before building
  (a code already shipped to Play from an earlier session is common, especially when
  iOS and Android share one version source). Otherwise the upload dies late with
  `Version code NN has already been used`. Note the Android release can be far behind
  iOS for a given app (seen: Android stuck on a version from a year earlier while iOS
  had moved on several releases) — check before assuming parity.
- Signing — the app module's release `signingConfig` must point at your upload
  keystore (commonly via a `key.properties` file kept out of git).

## Workflow

### 1. Build

```bash
cd <project>
bash -c "$ANDROID_BUILD_CMD"    # fix compile / lint errors first, don't build on top of them
# → the AAB at $ANDROID_AAB_PATH
```

### 2. Verify signing

```bash
jarsigner -verify "$ANDROID_AAB_PATH"
# → "jar verified"
keytool -printcert -jarfile "$ANDROID_AAB_PATH"
```
If verification fails, the release signingConfig isn't wired up — check it and the
keystore it reads before going further.

### 3. Write release notes

Write a plain-text notes file (≤500 chars, short bullet lines). Compose it from the
actual `git log` since the last Play release, not a generic message. The script stages
it as `<metadata>/android/<locale>/changelogs/<versionCode>.txt` (locale default
`en-US`; pass `--notes-locale ja-JP` etc. for the app's default language).

### 4. Ask, then upload

**Stop and ask the user before any production release.** Show: package, version
`x.y.z+NN`, track, release status, and the notes. `--track production --status completed`
sends the build to Google review and **rolls it out to all users automatically on
approval**. Testing tracks (`internal`, `alpha`, `beta`) and `--status draft` are lower
risk, but still confirm the track the user wants.

Prefer the script — it wraps the steps above plus the fastlane call and maps
known failures to fixes. It refuses a production release unless
`--confirm-production` is passed; pass it only after the user said yes.

```bash
# testing track
bash <skill-dir>/scripts/build_and_upload.sh /path/to/project \
  --track internal --notes /path/to/notes.txt

# production, after explicit user approval
bash <skill-dir>/scripts/build_and_upload.sh /path/to/project \
  --track production --status completed --notes /path/to/notes.txt --confirm-production
```

Or run the fastlane command directly:

```bash
cd "$(mktemp -d)" && FASTLANE_SKIP_UPDATE_CHECK=1 fastlane run upload_to_play_store \
  package_name:"$ANDROID_PACKAGE" \
  aab:<abs path to app-release.aab> \
  track:production release_status:completed \
  json_key:"$PLAY_SERVICE_ACCOUNT_JSON" \
  metadata_path:<META_DIR>/android \
  skip_upload_metadata:true skip_upload_images:true skip_upload_screenshots:true \
  skip_upload_apk:true skip_upload_changelogs:false \
  | grep -vE "^\s*$|^\|"
```

- `release_status:completed` = submit for review + full rollout on approval.
  Use `draft` to only stage without sending for review, or `track:internal` /
  `alpha` / `beta` for a testing track instead of production.
- `metadata_path` only needs the `android/<locale>/changelogs/<versionCode>.txt`
  file present; the other `skip_upload_*` flags avoid touching store metadata,
  images, and screenshots this skill doesn't manage.
- Success output: `"Successfully finished the upload to Google Play"` /
  `"Result: true"`.
- Pipe through `grep -vE "^\s*$|^\|"` to drop the giant fastlane options table
  from the log.

### 5. After success

- Report track, status, and version to the user. Review typically takes hours to a
  few days; with `release_status:completed` the release goes live automatically on
  approval.
- Check for uncommitted build-config changes in the app repo (Gradle version
  bumps, version bump, etc.) and offer to commit them with the repo's convention,
  e.g. `chore: bump version to x.y.z+NN`. Never commit `key.properties` or keystores.
- If the Android version lagged iOS going in, flag that the gap should be
  closed going forward rather than left to drift again.

## Build failures on stale Android projects (fix in this order)

1. **`Plugin with id 'com.google.firebase.crashlytics' was already requested`**
   → a duplicate line in `settings.gradle`'s plugins block. Delete one.

2. **An "Android Gradle Plugin version ... is lower than the minimum" error**, or
   **`Dependency 'androidx.activity:activity-ktx:1.12.4' requires Android Gradle
   plugin 8.9.1 or higher`** → bump the toolchain in lockstep:
   - `settings.gradle` plugins: `com.android.application` → `8.9.1`,
     `org.jetbrains.kotlin.android` → `2.1.0`, `com.google.gms.google-services`
     → `4.4.2`, `com.google.firebase.crashlytics` → `3.0.2`.
   - `gradle/wrapper/gradle-wrapper.properties`: `gradle-8.11.1-all.zip`.
   - the app module's `build.gradle`: `JavaVersion.VERSION_17` for
     `sourceCompatibility` / `targetCompatibility` / `jvmTarget`.
   - Verified working with JDK 17. This bump also satisfies Play's 16 KB
     native-library alignment recommendation as a side effect.

Toolchain-specific build failures (plugin caches, framework-managed SDK levels) are in
`docs/build-recipes.md` under each recipe.

## Play-side rejections at upload time (`Google Api Error: ...`)

1. **`Artifact ... uses Play Billing Library version 7.1.1 and must update to at
   least version 8.0.0`** → the in-app purchase SDK is too old. Upgrade it (RevenueCat,
   the Play Billing Library itself, or whatever wraps it in your toolchain) to a release
   that bundles BillingClient 8, then rebuild. Expect a code change: purchase calls
   deprecated in older SDKs may change signature or return type. Where known, package
   versions and the exact code change are in `docs/build-recipes.md` under the recipe.

2. **`All apps within social or dating categories must let us know how they
   comply with our child safety standards policy`** → triggered purely by the
   app's Play Store *category* (Social / Dating), independent of what the app
   actually does. Even `release_status:draft` with
   `changes_not_sent_for_review:true` is blocked by this. Two fixes:
   - **(A)** If the app isn't really user-to-user social (e.g. an AI chat app),
     change the category to Entertainment in Play Console → Grow users → Store
     presence → Store settings → App category. The API block lifts immediately on
     save — no need to wait for a store-listing review.
   - **(B)** Otherwise, complete Policy and programs → App content → Child safety
     standards (requires a published CSAE-standards URL, e.g. a section on your
     terms-of-service page, plus a contact email).
   - Both are user actions; hand off with the exact menu path above (Console menu
     names shift over time — search the Console for "App category" / "Child safety"
     if the path has moved).

3. **`Target SDK of artifact is too low: <versionCode>`** → the app module's Gradle
   config pins `targetSdkVersion 35` (or lower). Raise `targetSdk` to the level Play
   currently requires (or switch to your toolchain's managed value) and rebuild; the
   rebuild is incremental (seconds). Play requires `targetSdk` to track the latest each year.
   Deadline emails for target API level / Billing Library compliance land around the
   end of August each year — check the Console inbox if an upload is rejected for a
   compliance reason not listed above.

## Gotchas quick reference

| Symptom | Cause | Fix |
|---|---|---|
| Generic "Upload failed" via browser upload | Console file-input upload doesn't work reliably via automation | Don't use browser automation for Play Console writes; use fastlane supply |
| All Google accounts logged out of the Chrome profile after an automated Console visit | opening sensitive pages (Users and permissions etc.) in an automated browser triggers re-auth, which rejects automated browsers | Never open sensitive Play Console pages in an automated browser |
| `Google Api Error: Invalid request - The caller does not have permission` | service account not granted access to this specific app | invite the service-account email in Play Console → Users and permissions for that app; retry |
| `Artifact ... uses Play Billing Library version 7.1.1 and must update to at least version 8.0.0` | IAP SDK predates BillingClient 8 | upgrade the IAP SDK to a BillingClient 8 release and update the deprecated purchase call |
| `All apps within social or dating categories must let us know how they comply with our child safety standards policy` | Play Store category is Social/Dating | change category to Entertainment, or complete the Child safety standards declaration |
| `Dependency 'androidx.activity:activity-ktx:1.12.4' requires Android Gradle plugin 8.9.1 or higher` | stale AGP/Gradle/Kotlin toolchain | bump AGP 8.9.1, Kotlin 2.1.0, gradle-8.11.1-all.zip, JavaVersion 17 (see Build failures §2) |
| `Version code NN has already been used` / upload rejected with no clear reason | versionCode not strictly greater than the last uploaded one (on any track) | bump the versionCode in your project's version source (and `BUILD_NUMBER` if set) |
| `Target SDK of artifact is too low` | old literal `targetSdkVersion` in Gradle | raise `targetSdk` to the current requirement |
| Script stops with `Don't know how to build this project's AAB` or `unknown versionCode` | no build keys / version source for this project | set `ANDROID_BUILD_CMD` + `ANDROID_AAB_PATH` (or just `ANDROID_AAB_PATH`) and `BUILD_NUMBER`; see `docs/build-recipes.md` |
| Script stops with `Refusing to release to production without --confirm-production` | safety gate | ask the user; re-run with the flag only after a yes |
