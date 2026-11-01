# Android setup (one time)

What you need before `android-playstore-submit` or `/ship android` can run. Works on
macOS and Linux.

## 1. Tools

1. Install the toolchain that builds your app (the Android SDK and a JDK, plus anything
   your build uses on top) and make sure a release build works locally. Use **JDK 17**
   (`jarsigner` and `keytool` come with it). Unless the scripts detect a default for
   your project, tell them how to build with `ANDROID_BUILD_CMD` / `ANDROID_AAB_PATH` in
   `ship.env` ([build-recipes.md](build-recipes.md)).
2. Install fastlane (the Play upload goes through its `supply` actions):
   ```bash
   brew install fastlane       # or: gem install fastlane
   export FASTLANE_SKIP_UPDATE_CHECK=1
   ```

## 2. Create a Google Play service account

1. In [Google Cloud Console](https://console.cloud.google.com/), pick (or create) a project.
2. **APIs & Services → Library** → enable **Google Play Android Developer API**.
3. **IAM & Admin → Service accounts → Create service account**. Any name works
   (e.g. `play-upload`). Skip the optional project roles — Play permissions are granted
   in Play Console, not in Cloud IAM.
4. Note the service account's email, e.g.
   `play-upload@your-project.iam.gserviceaccount.com`. You'll invite it in step 3.
5. Open the service account → **Keys → Add key → Create new key → JSON**. A JSON file
   downloads.
6. Store it outside any repository, readable only by you:
   ```bash
   mkdir -p ~/.config/app-shipping
   mv ~/Downloads/<downloaded-key>.json ~/.config/app-shipping/play-service-account.json
   chmod 600 ~/.config/app-shipping/play-service-account.json
   ```
   This file contains a private key. Never commit it, paste it into chat, or let an
   agent `cat` it — only its path goes into `ship.env`.

   If your organization blocks service-account key creation (the
   `iam.disableServiceAccountKeyCreation` policy), an org admin has to allow it for
   this project.

## 3. Invite the service account in Play Console (per app)

1. In [Play Console](https://play.google.com/console), open **Users and permissions** →
   **Invite new users**.
2. Enter the service-account email from step 2.4.
3. Under **App permissions**, add each app you want to ship, and grant at least
   **Release apps to testing tracks** and **Release to production, exclude devices, and use
   Play App Signing** (plus **Manage testing tracks and edit tester lists** if you use
   testing tracks). Do not grant account-wide Admin unless you mean to.
4. **Invite user**. The permission applies immediately.

Repeat step 3 for every new app. Without it, uploads fail with
`The caller does not have permission` even though the key itself is valid.

Do this step by hand. Don't drive Play Console's **Users and permissions** page with an
automated browser: Google's re-auth check rejects automated browsers and can sign every
Google account out of that browser profile.

## 4. Check the key

```bash
cd "$(mktemp -d)" && fastlane run validate_play_store_json_key \
  json_key:~/.config/app-shipping/play-service-account.json
```

Expect `Successfully established connection to Google Play Store.` This proves the key
works; per-app access is only proven by an actual upload or by:

```bash
cd "$(mktemp -d)" && fastlane run google_play_track_version_codes \
  package_name:com.example.myapp track:production \
  json_key:~/.config/app-shipping/play-service-account.json
```

## 5. The app must already exist on Play

The Play Developer API cannot create an app or upload its very first build. Create the
app in Play Console and upload the first AAB manually once (any track); after that the
skill can upload every release.

## 6. Release signing

The app needs an upload keystore wired into the release `signingConfig` of the app
module's `build.gradle(.kts)`, usually through a `key.properties` file that holds the
keystore path and passwords (see Android's
[Sign your app](https://developer.android.com/studio/publish/app-signing)). The release
AAB must come out signed; the upload script stops if `jarsigner -verify` fails.
Keep the keystore and `key.properties` out of git.

## 7. ship.env

In the root of your app project, copy `ship.env.example` to `ship.env` and fill in:

```
ANDROID_PACKAGE=com.example.myapp     # applicationId in the app module's build.gradle(.kts)
PLAY_SERVICE_ACCOUNT_JSON=~/.config/app-shipping/play-service-account.json
```

Optional build keys (`ANDROID_BUILD_CMD`, `ANDROID_AAB_PATH`, `ANDROID_CLEAN_CMD`,
`BUILD_NUMBER`) are described in [build-recipes.md](build-recipes.md).
