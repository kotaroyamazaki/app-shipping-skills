# iOS setup (one time)

What you need before `ios-testflight-upload`, `ios-appstore-submit`, `app-store-header-image`
or `/ship ios` can run. iOS skills require **macOS with Xcode**.

## 1. Tools

1. Install Xcode from the App Store, open it once, and accept the license
   (`sudo xcodebuild -license accept`). It provides `xcrun altool`, `lipo` and `plutil`.
2. Install the toolchain that builds your app for iOS and make sure a release build
   works locally. Unless the scripts detect a default for your project, tell them how
   to build with `IOS_BUILD_CMD` / `IOS_IPA_PATH` in `ship.env`
   ([build-recipes.md](build-recipes.md)).
3. Install the App Store Connect CLI:
   ```bash
   brew install asc
   asc version          # Asset Library (header images) needs 5.12.0+, video 5.13.0+
   ```
   asc releases very often. When a command or flag in a skill is missing, run
   `brew update && brew upgrade asc` (`brew update` first, or Homebrew may stay on a stale version).

## 2. App Store Connect API key

1. In App Store Connect, open **Users and Access → Integrations → App Store Connect API**
   (you need the Account Holder or Admin role to create team keys).
2. Under **Team Keys**, click **+**, give it a name (e.g. `ship`), and choose the
   **App Manager** role (enough to upload builds, edit versions and submit for review).
3. Download the `AuthKey_<KEY_ID>.p8` file. **Apple lets you download it only once.**
4. Note the **Key ID** (shown in the row) and the **Issuer ID** (shown above the table).
5. Store the key where `altool` finds it automatically, readable only by you:
   ```bash
   mkdir -p ~/.appstoreconnect/private_keys
   mv ~/Downloads/AuthKey_<KEY_ID>.p8 ~/.appstoreconnect/private_keys/
   chmod 600 ~/.appstoreconnect/private_keys/AuthKey_<KEY_ID>.p8
   ```
   Never commit `.p8` files or copy them into an app repository.

## 3. Authenticate asc

```bash
asc auth login --name "ship" \
  --key-id "<KEY_ID>" --issuer-id "<ISSUER_ID>" \
  --private-key ~/.appstoreconnect/private_keys/AuthKey_<KEY_ID>.p8
asc auth status        # shows the stored credential and its key ID
asc auth issuer-id     # prints the issuer ID (the upload script reads it from here)
asc apps list --output table   # your apps; the numeric ID column is IOS_APP_ID
```

asc stores the credential in the system keychain (or `~/.asc/config.json` with
`--bypass-keychain`). If the key file is too permissive, add `--fix-permissions`.

## 4. Signing

1. In Xcode, open your app's workspace (or project), select the app target →
   **Signing & Capabilities**, enable **Automatically manage signing**, and pick your team.
   The upload script exports with automatic signing for `IOS_TEAM_ID`.
2. Make sure the app record exists in App Store Connect (**Apps → +**) with the same
   bundle ID. The skills do not create app records.

## 5. ship.env

In the root of your app project, copy `ship.env.example` to `ship.env` and fill in:

```
IOS_BUNDLE_ID=com.example.myapp     # PRODUCT_BUNDLE_IDENTIFIER in the Xcode project's project.pbxproj
IOS_APP_ID=1234567890               # from `asc apps list`
IOS_TEAM_ID=ABCDE12345              # DEVELOPMENT_TEAM in the same file, or developer.apple.com → Membership
```

Optional overrides if you don't want the scripts to read them from `asc auth`:
`ASC_KEY_ID=...`, `ASC_ISSUER_ID=...`. Optional build keys (`IOS_BUILD_CMD`,
`IOS_IPA_PATH`, `IOS_CLEAN_CMD`) are described in [build-recipes.md](build-recipes.md).
`ship.env` holds no secrets.

## 6. Smoke test

```bash
asc builds list --app 1234567890 --output table | head -5   # use your IOS_APP_ID
```

If this lists builds (or an empty table) without an auth error, you're ready.
