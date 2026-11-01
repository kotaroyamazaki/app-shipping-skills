#!/usr/bin/env python3
"""AdMob API CLI (Python 3.9+, standard library only).

Not an official Google tool. Talks to https://admob.googleapis.com/v1beta/.

  admob.py auth [--client-secret client_secret_xxx.json]
  admob.py accounts [--table]
  admob.py apps list [--table]
  admob.py apps create --platform ios --store-id 1234567890 [--name MyApp] [--yes]
  admob.py units list [--app ca-app-pub-...~...] [--table]
  admob.py units create --app ca-app-pub-...~... --format banner interstitial [--prefix MyApp] [--yes]
  admob.py setup --platform android --store-id com.example.myapp --format banner interstitial [--yes]

Common options (after the subcommand): --publisher pub-xxx, --no-auth.
Create commands are dry-run unless --yes is given.

Environment:
  ADMOB_CLIENT_ID / ADMOB_CLIENT_SECRET   OAuth Desktop client (alternative to --client-secret)
  ADMOB_TOKEN_PATH                        token file (default ~/.config/app-shipping/admob-token.json)
  ship.env (current directory)            ADMOB_PUBLISHER_ID=pub-... is used when --publisher is absent
"""
import argparse
import base64
import hashlib
import http.server
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

BASE = "https://admob.googleapis.com/v1beta/"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = [
    "https://www.googleapis.com/auth/admob.readonly",
    "https://www.googleapis.com/auth/admob.report",
    "https://www.googleapis.com/auth/admob.monetization",
]
DEFAULT_TOKEN_PATH = "~/.config/app-shipping/admob-token.json"
AUTH_TIMEOUT_SEC = 300
FORMATS = {
    "app_open": "APP_OPEN",
    "banner": "BANNER",
    "interstitial": "INTERSTITIAL",
    "native": "NATIVE",
    "rewarded": "REWARDED",
    "rewarded_interstitial": "REWARDED_INTERSTITIAL",
}
FORMAT_LABEL = {
    "APP_OPEN": "AppOpen",
    "BANNER": "Banner",
    "INTERSTITIAL": "Interstitial",
    "NATIVE": "Native",
    "REWARDED": "Rewarded",
    "REWARDED_INTERSTITIAL": "RewardedInterstitial",
}
SCRIPT = "admob.py"


class AdmobError(Exception):
    """User-facing error. main() prints it and exits with `code`."""

    def __init__(self, msg, code=1):
        super().__init__(msg)
        self.code = code


def log(*args):
    print(*args, file=sys.stderr)


# ---------------------------------------------------------------- config


def token_path():
    return os.path.expanduser(os.environ.get("ADMOB_TOKEN_PATH") or DEFAULT_TOKEN_PATH)


def parse_ship_env(path="ship.env"):
    """Parse a flat KEY=VALUE file. Supports comments, `export `, quotes and inline ` # comments`."""
    env = {}
    if not os.path.isfile(path):
        return env
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                continue
            if value[:1] in ("'", '"') and value[1:].find(value[0]) >= 0:
                value = value[1:1 + value[1:].find(value[0])]
            else:
                value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
            env[key] = value
    return env


def normalize_publisher(pub):
    pub = pub.strip()
    if pub.startswith("accounts/"):
        pub = pub[len("accounts/"):]
    if not re.match(r"^pub-\d+$", pub):
        raise AdmobError(f"invalid publisher id '{pub}' (expected pub-XXXXXXXXXXXXXXXX)")
    return pub


def default_prefix(store_id=None, name=None):
    """Ad unit name prefix: ASCII alphanumerics of the name, else of the store id, else 'App'."""
    for src in (name, store_id):
        if src:
            cleaned = re.sub(r"[^A-Za-z0-9]", "", src)
            if cleaned:
                return cleaned
    return "App"


def unit_display_name(prefix, ad_format):
    return f"{prefix}_{FORMAT_LABEL[ad_format]}"[:80]


# ---------------------------------------------------------------- token storage


def load_token():
    path = token_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise AdmobError(f"cannot read token file {path}: {e}")


def save_token(data):
    path = token_path()
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.chmod(path, 0o600)
    return path


def read_client_secret_file(path):
    path = os.path.expanduser(path)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise AdmobError(f"cannot read client secret JSON {path}: {e}")
    block = data.get("installed") or data.get("web")
    if not block or not block.get("client_id") or not block.get("client_secret"):
        raise AdmobError(
            f"{path} is not an OAuth client JSON. Download it from Google Cloud Console > "
            "APIs & Services > Credentials (OAuth client ID, type 'Desktop app').")
    if "web" in data and "installed" not in data:
        log("warning: this is a 'Web application' client; create a 'Desktop app' client if the "
            "loopback redirect is rejected (redirect_uri_mismatch).")
    return block["client_id"], block["client_secret"]


def client_credentials(client_secret_path=None):
    """Order: --client-secret file, env vars, the client saved in the token file."""
    if client_secret_path:
        return read_client_secret_file(client_secret_path)
    cid, sec = os.environ.get("ADMOB_CLIENT_ID"), os.environ.get("ADMOB_CLIENT_SECRET")
    if cid and sec:
        return cid, sec
    tok = load_token()
    if tok and tok.get("client_id") and tok.get("client_secret"):
        return tok["client_id"], tok["client_secret"]
    raise AdmobError(
        "OAuth client not found. Run:\n"
        f"  python3 {SCRIPT} auth --client-secret ~/Downloads/client_secret_XXXX.json\n"
        "or set ADMOB_CLIENT_ID and ADMOB_CLIENT_SECRET. Setup guide: docs/setup-admob.md")


# ---------------------------------------------------------------- HTTP helpers


def _read_error(e):
    try:
        return e.read().decode("utf-8", "replace")
    except Exception:  # pragma: no cover - defensive
        return ""


def post_form(url, data):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def explain_http_error(method, path, status, body_text):
    """Turn an AdMob API error into a message with the next action."""
    try:
        err = json.loads(body_text).get("error", {})
    except (ValueError, AttributeError):
        err = {}
    message = err.get("message") or body_text.strip() or "(no body)"
    reasons = {d.get("reason") for d in err.get("details", []) if isinstance(d, dict)}
    head = f"{method} {path} -> HTTP {status}: {message}"
    if status == 403:
        if "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in reasons or "insufficient authentication scopes" in message.lower():
            return (f"{head}\n\nThe saved token lacks a required scope (usually admob.monetization, "
                    "needed for creating apps and ad units).\n"
                    f"Next: re-run `python3 {SCRIPT} auth` and approve every requested permission "
                    "(do not uncheck any box on the consent screen).")
        if "SERVICE_DISABLED" in reasons or "has not been used in project" in message:
            return (f"{head}\n\nThe AdMob API is not enabled in your Google Cloud project.\n"
                    "Next: enable it at https://console.cloud.google.com/apis/library/admob.googleapis.com "
                    "and retry in a few minutes.")
        return (f"{head}\n\nThis AdMob API method has limited access on your account "
                "(the v1beta create methods are allowlisted per account).\n"
                "Next: contact your AdMob account manager to request access, or create the app / "
                "ad units in the AdMob console (https://apps.admob.com) and use `apps list` / "
                "`units list` to fetch the IDs.")
    if status == 401:
        return f"{head}\n\nNext: re-run `python3 {SCRIPT} auth`."
    if status == 404 and "accounts/" in path:
        return f"{head}\n\nNext: check the publisher id (`python3 {SCRIPT} accounts`)."
    return head


# ---------------------------------------------------------------- OAuth


def _pkce_pair():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def run_auth_flow(client_secret_path=None):
    """Loopback OAuth flow on a free port. Saves the refresh token (mode 600) and returns the token dict."""
    cid, sec = client_credentials(client_secret_path)
    state = secrets.token_urlsafe(16)
    verifier, challenge = _pkce_pair()
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            query = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()}
            if query.get("state") == state and ("code" in query or "error" in query):
                got.update(query)
                body = b"AdMob CLI: authorization received. You can close this tab."
                self.send_response(200)
            else:
                body = b"Not found"
                self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 1
    redirect = f"http://127.0.0.1:{server.server_port}"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
    log("Opening the browser for Google sign-in. If it does not open, visit:\n  " + url)
    try:
        webbrowser.open(url)
    except Exception:  # pragma: no cover - depends on the desktop
        pass
    deadline = time.time() + AUTH_TIMEOUT_SEC
    try:
        while "code" not in got and "error" not in got:
            if time.time() > deadline:
                raise AdmobError(f"timed out after {AUTH_TIMEOUT_SEC}s waiting for the browser consent")
            server.handle_request()
    finally:
        server.server_close()
    if "error" in got:
        raise AdmobError(f"authorization failed: {got['error']}")
    try:
        tok = post_form(TOKEN_URL, {
            "code": got["code"], "client_id": cid, "client_secret": sec, "redirect_uri": redirect,
            "grant_type": "authorization_code", "code_verifier": verifier})
    except urllib.error.HTTPError as e:
        raise AdmobError(f"token exchange failed (HTTP {e.code}): {_read_error(e)}")
    if not tok.get("refresh_token"):
        raise AdmobError("Google returned no refresh token. Re-run auth and approve the consent screen.")
    granted = set((tok.get("scope") or "").split())
    data = {"client_id": cid, "client_secret": sec, "refresh_token": tok["refresh_token"],
            "scope": tok.get("scope"), "created_at": int(time.time())}
    path = save_token(data)
    missing = [s.rsplit("/", 1)[-1] for s in SCOPES if granted and s not in granted]
    log(f"Saved {path} (mode 600).")
    if missing:
        log("warning: not granted: " + ", ".join(missing) + ". Re-run auth and keep every box checked.")
    data["access_token"] = tok.get("access_token")
    return data


def can_prompt(no_auth):
    return not no_auth and sys.stdin is not None and sys.stdin.isatty()


def reauth_or_fail(reason, no_auth):
    if can_prompt(no_auth):
        log(f"{reason} Starting the browser sign-in now...")
        return run_auth_flow()
    raise AdmobError(
        f"{reason}\nRun this in an interactive terminal and approve in the browser:\n"
        f"  python3 {SCRIPT} auth\n"
        "(Tokens expire every 7 days while the OAuth consent screen is in Testing mode.)")


def get_access_token(no_auth=False):
    tok = load_token()
    if not tok or not tok.get("refresh_token"):
        tok = reauth_or_fail("Not authenticated.", no_auth)
        if tok.get("access_token"):
            return tok["access_token"]
    try:
        return post_form(TOKEN_URL, {
            "client_id": tok["client_id"], "client_secret": tok["client_secret"],
            "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"})["access_token"]
    except urllib.error.HTTPError as e:
        body = _read_error(e)
        if "invalid_grant" in body or "invalid_client" in body or e.code == 401:
            new = reauth_or_fail("The saved AdMob token has expired or was revoked.", no_auth)
            return new["access_token"]
        raise AdmobError(f"token refresh failed (HTTP {e.code}): {body}")
    except urllib.error.URLError as e:
        raise AdmobError(f"network error while refreshing the token: {e.reason}")


# ---------------------------------------------------------------- API


class Api:
    def __init__(self, publisher=None, no_auth=False, access_token=None):
        self.token = access_token or get_access_token(no_auth)
        self.publisher = publisher
        self._account = None

    def call(self, method, path, body=None):
        url = BASE + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            raise AdmobError(explain_http_error(method, path.split("?")[0], e.code, _read_error(e)))
        except urllib.error.URLError as e:
            raise AdmobError(f"network error: {method} {url}: {e.reason}")

    def accounts(self):
        return self.call("GET", "accounts").get("account", [])

    @property
    def account(self):
        if self._account:
            return self._account
        if self.publisher:
            self._account = "accounts/" + normalize_publisher(self.publisher)
            return self._account
        accts = self.accounts()
        if not accts:
            raise AdmobError("no AdMob account is visible to this Google account. Sign in to "
                             "https://apps.admob.com once with the same account, then retry.")
        if len(accts) > 1:
            lines = "\n".join(f"  {a.get('publisherId')}  {a.get('name')}" for a in accts)
            raise AdmobError("multiple AdMob accounts found; choose one with --publisher "
                             "(or ADMOB_PUBLISHER_ID in ship.env):\n" + lines)
        self._account = accts[0]["name"]
        return self._account

    def list_all(self, collection):
        items, page = [], ""
        while True:
            params = {"pageSize": 1000}
            if page:
                params["pageToken"] = page
            res = self.call("GET", f"{self.account}/{collection}?{urllib.parse.urlencode(params)}")
            items += res.get(collection, [])
            page = res.get("nextPageToken")
            if not page:
                return items


# ---------------------------------------------------------------- operations


def app_label(app):
    linked = app.get("linkedAppInfo") or {}
    return linked.get("displayName") or (app.get("manualAppInfo") or {}).get("displayName", "")


def find_app(apps, platform, store_id):
    for a in apps:
        if a.get("platform") == platform and (a.get("linkedAppInfo") or {}).get("appStoreId") == store_id:
            return a
    return None


def app_body(platform, store_id, name):
    body = {"platform": platform.upper()}
    if store_id:
        body["linkedAppInfo"] = {"appStoreId": store_id}
    if name:
        body["manualAppInfo"] = {"displayName": name}
    return body


def unit_body(app_id, ad_format, prefix):
    body = {"appId": app_id, "adFormat": ad_format, "displayName": unit_display_name(prefix, ad_format),
            "adTypes": ["RICH_MEDIA", "VIDEO"]}
    if ad_format == "REWARDED":
        body["rewardSettings"] = {"unitAmount": "1", "unitType": "Reward"}
    return body


class Plan:
    """Collects the POST requests; executes them only when apply=True."""

    def __init__(self, api, apply):
        self.api, self.apply, self.requests = api, apply, []

    def post(self, collection, body, placeholder):
        path = f"{self.api.account}/{collection}"
        self.requests.append({"method": "POST", "path": path, "body": body})
        if not self.apply:
            log(f"[dry-run] POST {BASE}{path}\n{json.dumps(body, ensure_ascii=False, indent=1)}")
            return dict(body, **placeholder)
        return self.api.call("POST", path, body)


def ensure_app(plan, platform, store_id, name, apps=None):
    platform = platform.upper()
    if store_id:
        apps = plan.api.list_all("apps") if apps is None else apps
        existing = find_app(apps, platform, store_id)
        if existing:
            log(f"Reusing existing app: {app_label(existing) or '-'} {existing['appId']}")
            return existing, False
    elif not name:
        raise AdmobError("give --store-id, or --name for an app that is not in a store yet")
    return plan.post("apps", app_body(platform, store_id, name), {"appId": "<new-app-id>"}), True


def create_units(plan, app_id, formats, prefix):
    out = []
    for f in formats:
        fmt = FORMATS[f]
        out.append(plan.post("adUnits", unit_body(app_id, fmt, prefix), {"adUnitId": f"<new-{f}-id>"}))
    return out


def setup(plan, platform, store_id, name, prefix, formats):
    app, created = ensure_app(plan, platform, store_id, name)
    prefix = prefix or default_prefix(store_id, name)
    wanted = [FORMATS[f] for f in dict.fromkeys(formats)]
    existing_units = {}
    if not created:
        for u in plan.api.list_all("adUnits"):
            if u.get("appId") == app["appId"] and u.get("adFormat") in wanted:
                existing_units.setdefault(u["adFormat"], u)
    todo = [f for f in dict.fromkeys(formats) if FORMATS[f] not in existing_units]
    for fmt in existing_units:
        log(f"Ad unit already exists for {fmt}: {existing_units[fmt]['adUnitId']} (skipped)")
    created_units = create_units(plan, app["appId"], todo, prefix)
    ad_units = {fmt: u["adUnitId"] for fmt, u in existing_units.items()}
    ad_units.update({u["adFormat"]: u["adUnitId"] for u in created_units})
    return {
        "dryRun": not plan.apply,
        "publisher": plan.api.account.split("/", 1)[-1],
        "appId": app["appId"],
        "platform": app.get("platform"),
        "appCreated": created,
        "approvalState": app.get("appApprovalState"),
        "adUnits": {fmt: ad_units[fmt] for fmt in wanted if fmt in ad_units},
        "skippedFormats": list(existing_units),
        "requests": plan.requests,
    }


# ---------------------------------------------------------------- output


def emit_json(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def emit_table(rows, headers):
    rows = [[str(c if c is not None else "") for c in r] for r in rows]
    widths = [max([len(h)] + [len(r[i]) for r in rows]) for i, h in enumerate(headers)]
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)).rstrip())
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())


def dry_run_note(plan):
    if not plan.apply and plan.requests:
        log("Dry run: nothing was created. Re-run with --yes to create the above.")


# ---------------------------------------------------------------- commands


def resolve_publisher(args):
    if getattr(args, "publisher", None):
        return args.publisher
    return parse_ship_env().get("ADMOB_PUBLISHER_ID") or None


def make_api(args):
    return Api(publisher=resolve_publisher(args), no_auth=getattr(args, "no_auth", False))


def cmd_auth(args):
    data = run_auth_flow(args.client_secret)
    emit_json({"tokenPath": token_path(), "scope": data.get("scope")})


def cmd_accounts(args):
    accts = make_api(args).accounts()
    if args.table:
        emit_table([[a.get("publisherId"), a.get("currencyCode"), a.get("reportingTimeZone")] for a in accts],
                   ["PUBLISHER", "CURRENCY", "TIMEZONE"])
    else:
        emit_json(accts)


def cmd_apps_list(args):
    apps = make_api(args).list_all("apps")
    if args.table:
        emit_table([[a.get("platform"), a.get("appId"), (a.get("linkedAppInfo") or {}).get("appStoreId", "-"),
                     a.get("appApprovalState", ""), app_label(a)] for a in apps],
                   ["PLATFORM", "APP_ID", "STORE_ID", "APPROVAL", "NAME"])
    else:
        emit_json(apps)


def cmd_apps_create(args):
    plan = Plan(make_api(args), args.yes)
    app, created = ensure_app(plan, args.platform, args.store_id, args.name)
    dry_run_note(plan)
    emit_json({"dryRun": not plan.apply, "appCreated": created, "app": app, "requests": plan.requests})


def cmd_units_list(args):
    api = make_api(args)
    units = [u for u in api.list_all("adUnits") if not args.app or u.get("appId") == args.app]
    if args.table:
        emit_table([[u.get("adFormat"), u.get("adUnitId"), u.get("appId"), u.get("displayName", "")] for u in units],
                   ["FORMAT", "AD_UNIT_ID", "APP_ID", "NAME"])
    else:
        emit_json(units)


def cmd_units_create(args):
    api = make_api(args)
    prefix = args.prefix
    if not prefix:
        app = next((a for a in api.list_all("apps") if a.get("appId") == args.app), None)
        if app is None:
            raise AdmobError(f"app {args.app} not found in {api.account} (see `apps list`)")
        prefix = default_prefix((app.get("linkedAppInfo") or {}).get("appStoreId"), app_label(app))
    plan = Plan(api, args.yes)
    units = create_units(plan, args.app, list(dict.fromkeys(args.format)), prefix)
    dry_run_note(plan)
    emit_json({"dryRun": not plan.apply, "adUnits": units, "requests": plan.requests})


def cmd_setup(args):
    plan = Plan(make_api(args), args.yes)
    result = setup(plan, args.platform, args.store_id, args.name, args.prefix, args.format)
    dry_run_note(plan)
    emit_json(result)


# ---------------------------------------------------------------- CLI


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--publisher", help="AdMob publisher id (pub-...). Default: ADMOB_PUBLISHER_ID in ./ship.env")
    common.add_argument("--no-auth", action="store_true",
                        help="never start the browser sign-in automatically; fail instead")
    table = argparse.ArgumentParser(add_help=False)
    table.add_argument("--table", action="store_true", help="human-readable table instead of JSON")
    create = argparse.ArgumentParser(add_help=False)
    create.add_argument("--yes", action="store_true", help="actually create (default is a dry run)")
    fmt_help = "one or more of: " + " ".join(FORMATS)

    p = argparse.ArgumentParser(prog=SCRIPT, description="AdMob API CLI (unofficial).")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("auth", help="sign in with Google and save a refresh token")
    s.add_argument("--client-secret", help="OAuth client JSON downloaded from Google Cloud (Desktop app)")
    s.set_defaults(fn=cmd_auth)

    s = sub.add_parser("accounts", parents=[common, table], help="list AdMob accounts")
    s.set_defaults(fn=cmd_accounts)

    apps = sub.add_parser("apps", help="list / create apps").add_subparsers(dest="sub", required=True)
    s = apps.add_parser("list", parents=[common, table])
    s.set_defaults(fn=cmd_apps_list)
    s = apps.add_parser("create", parents=[common, create])
    s.add_argument("--platform", required=True, choices=["ios", "android"])
    s.add_argument("--store-id", help="iOS: numeric App Store id / Android: package name. Omit if not in a store yet")
    s.add_argument("--name", help="display name (required when --store-id is omitted)")
    s.set_defaults(fn=cmd_apps_create)

    units = sub.add_parser("units", help="list / create ad units").add_subparsers(dest="sub", required=True)
    s = units.add_parser("list", parents=[common, table])
    s.add_argument("--app", help="filter by app id (ca-app-pub-...~...)")
    s.set_defaults(fn=cmd_units_list)
    s = units.add_parser("create", parents=[common, create])
    s.add_argument("--app", required=True, help="ca-app-pub-...~...")
    s.add_argument("--format", nargs="+", required=True, choices=list(FORMATS), metavar="FORMAT", help=fmt_help)
    s.add_argument("--prefix", help="display name prefix ({prefix}_Banner ...). Default: alphanumerics of the store id")
    s.set_defaults(fn=cmd_units_create)

    s = sub.add_parser("setup", parents=[common, create],
                       help="create the app (or reuse the one with the same store id) + missing ad units")
    s.add_argument("--platform", required=True, choices=["ios", "android"])
    s.add_argument("--store-id")
    s.add_argument("--name")
    s.add_argument("--prefix")
    s.add_argument("--format", nargs="+", required=True, choices=list(FORMATS), metavar="FORMAT", help=fmt_help)
    s.set_defaults(fn=cmd_setup)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except AdmobError as e:
        log(f"error: {e}")
        return e.code
    except KeyboardInterrupt:
        log("interrupted")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
