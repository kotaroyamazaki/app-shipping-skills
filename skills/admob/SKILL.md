---
name: admob
description: Register apps and create ad units in AdMob from the command line through the AdMob API (list accounts, apps and ad units; create an app; create banner / interstitial / rewarded / native / app open units), then hand the IDs to the app code. Use when the user says "register the app in AdMob", "create ad units", "set up AdMob for this app", "give me the AdMob app ID / ad unit IDs", "list my AdMob apps", or in Japanese "AdMob にアプリ登録して", "広告ユニット作って", "App ID / Unit ID ちょうだい", "バナーとインタースティシャルを用意して", "AdMob のアプリ一覧", "このアプリの広告ユニット ID は？". Works from any repository; reads ADMOB_PUBLISHER_ID from ship.env.
---

# AdMob (register apps and create ad units via the API)

CLI: `scripts/admob.py` in this skill folder. One file, Python 3.9+, standard library only. Run it with `python3 -I <skill-dir>/scripts/admob.py ...`. Output is JSON on stdout (messages go to stderr); add `--table` on list commands for a human-readable view.

This is not an official Google tool.

## Prerequisites

- The user has their own Google Cloud project with the AdMob API enabled and a **Desktop app** OAuth client. One-time setup: [docs/setup-admob.md](../../docs/setup-admob.md).
- Sign in once: `python3 -I scripts/admob.py auth --client-secret ~/Downloads/client_secret_XXXX.json` (or set `ADMOB_CLIENT_ID` / `ADMOB_CLIENT_SECRET`). The refresh token is saved to `~/.config/app-shipping/admob-token.json` (mode 600; override with `ADMOB_TOKEN_PATH`).
- Optional: `ADMOB_PUBLISHER_ID=pub-9876543210987654` in the project's `ship.env` selects the AdMob account.

### Expect to re-authorize every week

The AdMob scopes are restricted scopes, so an individual's OAuth consent screen stays in **Testing** mode, and Google expires Testing-mode refresh tokens after 7 days. There is no workaround short of Google's app verification. The CLI handles it: when the refresh fails with `invalid_grant` it opens the browser sign-in immediately and continues. If it is running without a TTY (or with `--no-auth`) it prints `python3 admob.py auth` and exits 1. In that case, ask the user to run that command in their terminal and click Allow (about 10 seconds), then retry.

## Commands

```bash
A=<skill-dir>/scripts/admob.py

python3 -I $A auth [--client-secret client.json]   # browser sign-in, saves the refresh token
python3 -I $A accounts [--table]                   # publisher ids visible to this Google account
python3 -I $A apps list [--table]                  # platform / app id / store id / approval / name
python3 -I $A units list [--app ca-app-pub-...~...] [--table]

# Recommended: app (reused if an app with the same platform + store id exists) + missing ad units
python3 -I $A setup --platform android --store-id com.example.myapp \
  --format banner interstitial rewarded            # dry run: prints the exact POST bodies
python3 -I $A setup ... --yes                      # actually creates

# Individual steps
python3 -I $A apps create --platform ios --store-id 1234567890 [--name "My App"] [--yes]
python3 -I $A units create --app ca-app-pub-9876543210987654~0123456789 --format native [--prefix MyApp] [--yes]
```

Common options (after the subcommand): `--publisher pub-...` (overrides ship.env), `--no-auth` (never open the browser; fail instead).

- `--format`: `app_open` `banner` `interstitial` `native` `rewarded` `rewarded_interstitial`.
- `--store-id`: iOS = the numeric App Store id (App Store Connect "Apple ID"); Android = the package name. Omit it for an app that is not in a store yet and pass `--name` instead.
- Ad unit names are `{prefix}_Banner`, `{prefix}_Interstitial`, `{prefix}_Rewarded`, ... The default prefix is the ASCII alphanumerics of the app name (`--name`, or the store listing name), falling back to the store id, e.g. `My App` -> `MyApp_Banner`, `com.example.myapp` -> `comexamplemyapp_Banner`. Pass `--prefix` to override.
- `setup` skips a format that already has a unit on that app. Use `units create` for a second unit of the same format.
- Rewarded units are created with a reward of `1 Reward`; change it in the AdMob console if needed.
- Creating is **dry-run by default**. Only `--yes` sends POST requests.
- If more than one AdMob account is visible and none is selected, the CLI lists them and exits 1. Ask the user which one, then pass `--publisher` (or add it to ship.env).

## Procedure

1. Work out the platform(s), store id(s) and ad formats. Read them from `ship.env` (`IOS_APP_ID`, `ANDROID_PACKAGE`), the existing ad code, or `apps list`. iOS and Android are separate AdMob apps: run `setup` once per platform.
2. Run `setup` **without `--yes`** and show the user the dry-run output: whether an existing app is reused, which formats are skipped, and the exact request bodies. Anything created stays in their AdMob account forever (the API cannot delete or rename), so get an explicit yes.
3. Run the same command with `--yes`. Keep the `appId` and `adUnits` from the JSON output.
4. Put the IDs into the app:
   - iOS app ID: the app's `Info.plist` -> `<key>GADApplicationIdentifier</key><string>ca-app-pub-...~...</string>`
   - Android app ID: the app's `AndroidManifest.xml`, inside `<application>` -> `<meta-data android:name="com.google.android.gms.ads.APPLICATION_ID" android:value="ca-app-pub-...~..."/>`
   - Ad unit IDs: in the code where ads are loaded, chosen per platform.
   - Keep Google's test ad unit IDs in debug builds and switch to the real ones only for release builds. Real IDs in development builds risk invalid-traffic flags on the account. Google's per-platform guides cover the setup and list the test IDs: [iOS](https://developers.google.com/admob/ios/quick-start), [Android](https://developers.google.com/admob/android/quick-start).
5. Tell the user that a new app starts with `approvalState` `ACTION_REQUIRED` or `IN_REVIEW`. Ad serving is limited until the app is in the store, linked, and approved by AdMob. They also need an `app-ads.txt` on their developer website.

## What the API cannot do

- Delete or rename apps and ad units. Do that in the AdMob console (https://apps.admob.com).
- Link a store listing later. `linkedAppInfo` is immutable through the API, so an app created without `--store-id` must be linked in the AdMob console after the app is published. Prefer waiting for the store id when it is close.
- Mediation groups and ad unit mappings. The API exists but is allowlisted and not implemented in this CLI.
- app-ads.txt, payments, policy center.

## 403 errors and "limited access"

`apps.create` and `adUnits.create` are v1beta methods. They need the `https://www.googleapis.com/auth/admob.monetization` scope, which is listed on the method reference pages but not in the API's Getting Started guide (that guide only mentions `admob.readonly` and `admob.report`). The reference pages also say these methods have **limited access** and to contact your account manager on a 403. Many personal accounts can create with the monetization scope alone, but some cannot.

The CLI tells the two cases apart:

- `ACCESS_TOKEN_SCOPE_INSUFFICIENT`: the token lacks `admob.monetization`. Re-run `auth` and keep every permission box checked on the consent screen.
- Any other 403: the method is not enabled for this account. The user has to ask their AdMob account manager, or create the app / units in the AdMob console and use `apps list` / `units list` to fetch the IDs. Reading still works.
- `SERVICE_DISABLED`: enable the AdMob API in the Google Cloud project.

Note that `accounts.list` returns the publisher account the Google account last opened in the AdMob console; use `--publisher` when the user has several.
