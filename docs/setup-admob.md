# AdMob API setup

One-time setup for the `admob` skill. It follows Google's [AdMob API Getting Started](https://developers.google.com/admob/api/v1/getting-started) guide: you use **your own** Google Cloud project and OAuth client. This project does not ship a client ID, and your tokens never leave your machine.

You need: a Google account that can sign in to your AdMob account, and Python 3.9 or newer.

## Steps

1. **Create a Google Cloud project.** Open https://console.cloud.google.com/projectcreate, give it any name (e.g. `admob-cli`) and create it. Make sure it is selected in the project picker for the following steps.

2. **Enable the AdMob API.** Open https://console.cloud.google.com/apis/library/admob.googleapis.com and click **Enable**.

3. **Configure the OAuth consent screen.** Go to **APIs & Services > OAuth consent screen** (in newer consoles: **Google Auth Platform > Branding / Audience**).
   - User type: **External**.
   - App name and support email: anything; only you will see it.
   - Publishing status: leave it in **Testing**.
   - **Test users: add the Google account you use for AdMob.** Without this, sign-in fails with `access_denied`.
   - You do not have to add scopes here; the CLI requests them at sign-in.

4. **Create an OAuth client.** Go to **APIs & Services > Credentials > Create credentials > OAuth client ID** (or **Google Auth Platform > Clients > Create client**).
   - Application type: **Desktop app**.
   - Name: anything.

5. **Download the client JSON.** Click **Download JSON** on the client you just created. The file is named like `client_secret_XXXX.apps.googleusercontent.com.json` and starts with `{"installed": ...}`. Keep it out of any git repository; `~/.config/app-shipping/` is a good place.

6. **Sign in from the CLI.**

   ```bash
   python3 skills/admob/scripts/admob.py auth --client-secret ~/Downloads/client_secret_XXXX.json
   ```

   Your browser opens. Choose your AdMob Google account. Google warns that the app is unverified: click **Continue**. Leave **every** permission checked (read AdMob data, read reports, and manage your AdMob apps and ad units) and allow. The CLI listens on a random free port on 127.0.0.1, receives the code, and saves the refresh token to `~/.config/app-shipping/admob-token.json` with mode 600. Set `ADMOB_TOKEN_PATH` to store it elsewhere.

   Instead of `--client-secret` you can export `ADMOB_CLIENT_ID` and `ADMOB_CLIENT_SECRET`. After the first sign-in the client is remembered in the token file.

7. **Check it works.**

   ```bash
   python3 skills/admob/scripts/admob.py accounts --table
   python3 skills/admob/scripts/admob.py apps list --table
   ```

   If several publisher accounts are listed, put the one you want in your project's `ship.env`:

   ```
   ADMOB_PUBLISHER_ID=pub-9876543210987654
   ```

## You will re-authorize every 7 days

While the consent screen is in **Testing** mode, Google expires refresh tokens after 7 days. When that happens the CLI notices the `invalid_grant` error and opens the browser sign-in again by itself; click Allow and the command continues. In a non-interactive shell it prints the `auth` command to run instead.

Publishing the consent screen ("In production") would remove the 7-day limit, but the AdMob scopes are **restricted** scopes: publishing requires Google's OAuth app verification, including a paid third-party security assessment. That is not realistic for a personal tool, so most individuals stay in Testing and accept the weekly click.

## Scopes the CLI requests

| Scope | Used for |
|---|---|
| `https://www.googleapis.com/auth/admob.readonly` | accounts, apps and ad units lists |
| `https://www.googleapis.com/auth/admob.report` | reports (not used by v0.1, requested so you do not need to re-consent later) |
| `https://www.googleapis.com/auth/admob.monetization` | creating apps and ad units (v1beta) |

`admob.monetization` is documented on the [apps.create](https://developers.google.com/admob/api/reference/rest/v1beta/accounts.apps/create) and [adUnits.create](https://developers.google.com/admob/api/reference/rest/v1beta/accounts.adUnits/create) reference pages, not in Getting Started. Those methods are marked as limited access; if creating returns a 403 that is not a scope problem, your account is not allowlisted. Ask your AdMob account manager, or create the app and units in the AdMob console and use the list commands to fetch the IDs.

## Troubleshooting

- `access_denied` in the browser: your account is not a test user on the consent screen (step 3).
- `redirect_uri_mismatch`: the client is not a Desktop app client. Create a Desktop app client (step 4).
- `403 ... SERVICE_DISABLED` / "has not been used in project": the AdMob API is not enabled (step 2), or was enabled a few minutes ago.
- `403 ... ACCESS_TOKEN_SCOPE_INSUFFICIENT`: a permission box was unchecked during sign-in. Re-run `auth` and keep all of them.
- No accounts returned: sign in to https://apps.admob.com once with the same Google account.
