"""Unit tests for skills/admob/scripts/admob.py. No network: urllib is mocked."""
import io
import json
import os
import stat
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "skills", "admob", "scripts"))

import admob  # noqa: E402

PUB = "pub-9876543210987654"
APP_ID = "ca-app-pub-9876543210987654~0123456789"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def http_error(url, code, payload):
    body = json.dumps(payload).encode()
    return urllib.error.HTTPError(url, code, "error", {}, io.BytesIO(body))


class FakeAdmob:
    """Routes urllib.request.urlopen calls to canned responses and records them."""

    def __init__(self, apps=None, units=None, accounts=None, errors=None, token_error=None):
        self.apps = apps or []
        self.units = units or []
        self.accounts = accounts if accounts is not None else [{"name": f"accounts/{PUB}", "publisherId": PUB}]
        self.errors = errors or {}  # (method, path-prefix) -> (code, payload)
        self.token_error = token_error
        self.calls = []
        self.counter = 0

    def __call__(self, req, timeout=None):
        url = req.full_url
        method = req.get_method()
        body = json.loads(req.data.decode()) if req.data and "googleapis.com/v1beta" in url else req.data
        self.calls.append((method, url, body))
        if url == admob.TOKEN_URL:
            if self.token_error:
                raise http_error(url, 400, self.token_error)
            return FakeResponse(json.dumps({"access_token": "ya29.test"}).encode())
        path = urllib.parse.urlparse(url).path.replace("/v1beta/", "", 1)
        for (m, prefix), (code, payload) in self.errors.items():
            if m == method and path.startswith(prefix):
                raise http_error(url, code, payload)
        if method == "GET" and path == "accounts":
            data = {"account": self.accounts}
        elif method == "GET" and path.endswith("/apps"):
            data = {"apps": self.apps}
        elif method == "GET" and path.endswith("/adUnits"):
            data = {"adUnits": self.units}
        elif method == "POST" and path.endswith("/apps"):
            self.counter += 1
            data = dict(body, appId=f"ca-app-pub-9876543210987654~000000000{self.counter}",
                        appApprovalState="ACTION_REQUIRED")
        elif method == "POST" and path.endswith("/adUnits"):
            self.counter += 1
            data = dict(body, adUnitId=f"ca-app-pub-9876543210987654/000000000{self.counter}")
        else:
            raise AssertionError(f"unexpected request {method} {url}")
        return FakeResponse(json.dumps(data).encode())

    def posts(self):
        return [c for c in self.calls if c[0] == "POST" and "v1beta" in c[1]]


class TempTokenMixin:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.token_path = os.path.join(self.tmp.name, "sub", "admob-token.json")
        self.env = mock.patch.dict(os.environ, {"ADMOB_TOKEN_PATH": self.token_path}, clear=False)
        self.env.start()
        for k in ("ADMOB_CLIENT_ID", "ADMOB_CLIENT_SECRET"):
            os.environ.pop(k, None)
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self.cwd)
        self.env.stop()
        self.tmp.cleanup()

    def write_token(self):
        admob.save_token({"client_id": "cid.apps.googleusercontent.com", "client_secret": "sec",
                          "refresh_token": "1//refresh"})

    def run_cli(self, fake, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("urllib.request.urlopen", fake), redirect_stdout(out), redirect_stderr(err):
            code = admob.main(list(argv))
        return code, out.getvalue(), err.getvalue()


class ShipEnvTest(TempTokenMixin, unittest.TestCase):
    def test_parse(self):
        with open("ship.env", "w") as f:
            f.write("# comment\n\nIOS_BUNDLE_ID=com.example.myapp\n"
                    "IOS_APP_ID=1234567890          # App Store numeric id\n"
                    "export ADMOB_PUBLISHER_ID=\"pub-9876543210987654\"\n"
                    "QUOTED='a # not a comment'\nURL=http://x/#frag\nnot a line\n")
        env = admob.parse_ship_env()
        self.assertEqual(env["IOS_BUNDLE_ID"], "com.example.myapp")
        self.assertEqual(env["IOS_APP_ID"], "1234567890")
        self.assertEqual(env["ADMOB_PUBLISHER_ID"], PUB)
        self.assertEqual(env["QUOTED"], "a # not a comment")
        self.assertEqual(env["URL"], "http://x/#frag")
        self.assertNotIn("not a line", env)

    def test_missing_file(self):
        self.assertEqual(admob.parse_ship_env(), {})

    def test_publisher_from_ship_env_skips_account_listing(self):
        with open("ship.env", "w") as f:
            f.write(f"ADMOB_PUBLISHER_ID={PUB}\n")
        self.write_token()
        fake = FakeAdmob(accounts=[{"name": "accounts/pub-1"}, {"name": "accounts/pub-2"}])
        code, out, _ = self.run_cli(fake, "apps", "list")
        self.assertEqual(code, 0)
        urls = [c[1] for c in fake.calls]
        self.assertTrue(any(f"accounts/{PUB}/apps" in u for u in urls))
        self.assertFalse(any(u.endswith("/v1beta/accounts") for u in urls))


class PrefixTest(unittest.TestCase):
    def test_store_id(self):
        self.assertEqual(admob.default_prefix("com.example.myapp"), "comexamplemyapp")
        self.assertEqual(admob.default_prefix("1234567890", "My App"), "MyApp")

    def test_name_fallback(self):
        self.assertEqual(admob.default_prefix(None, "My App!"), "MyApp")
        self.assertEqual(admob.default_prefix(None, "日本語の名前"), "App")
        self.assertEqual(admob.default_prefix(None, None), "App")

    def test_display_name(self):
        self.assertEqual(admob.unit_display_name("comexamplemyapp", "BANNER"), "comexamplemyapp_Banner")
        self.assertEqual(len(admob.unit_display_name("x" * 100, "REWARDED_INTERSTITIAL")), 80)


class SetupTest(TempTokenMixin, unittest.TestCase):
    existing_app = {"name": f"accounts/{PUB}/apps/0123456789", "appId": APP_ID, "platform": "ANDROID",
                    "linkedAppInfo": {"appStoreId": "com.example.myapp", "displayName": "My App"},
                    "appApprovalState": "APPROVED"}
    existing_banner = {"adUnitId": "ca-app-pub-9876543210987654/1111111111", "appId": APP_ID,
                       "adFormat": "BANNER", "displayName": "comexamplemyapp_Banner"}

    def test_reuses_app_and_skips_existing_formats(self):
        self.write_token()
        fake = FakeAdmob(apps=[self.existing_app], units=[self.existing_banner])
        code, out, err = self.run_cli(fake, "setup", "--platform", "android", "--store-id", "com.example.myapp",
                                      "--format", "banner", "interstitial", "rewarded", "--yes")
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        posts = fake.posts()
        self.assertFalse(any(c[1].endswith("/apps") for c in posts), "must not create a second app")
        self.assertEqual([c[2]["adFormat"] for c in posts], ["INTERSTITIAL", "REWARDED"])
        self.assertEqual(posts[0][2]["displayName"], "comexamplemyapp_Interstitial")
        self.assertEqual(posts[1][2]["rewardSettings"], {"unitAmount": "1", "unitType": "Reward"})
        self.assertEqual(result["appId"], APP_ID)
        self.assertFalse(result["appCreated"])
        self.assertEqual(result["skippedFormats"], ["BANNER"])
        self.assertEqual(result["adUnits"]["BANNER"], self.existing_banner["adUnitId"])
        self.assertEqual(set(result["adUnits"]), {"BANNER", "INTERSTITIAL", "REWARDED"})

    def test_creates_app_when_missing(self):
        self.write_token()
        fake = FakeAdmob()
        code, out, err = self.run_cli(fake, "setup", "--platform", "ios", "--store-id", "1234567890",
                                      "--name", "My App", "--format", "banner", "--yes")
        self.assertEqual(code, 0, err)
        posts = fake.posts()
        self.assertEqual(posts[0][2], {"platform": "IOS", "linkedAppInfo": {"appStoreId": "1234567890"},
                                       "manualAppInfo": {"displayName": "My App"}})
        self.assertEqual(posts[1][2]["appId"], json.loads(out)["appId"])
        self.assertTrue(json.loads(out)["appCreated"])

    def test_dry_run_does_not_post(self):
        self.write_token()
        fake = FakeAdmob(apps=[self.existing_app], units=[self.existing_banner])
        code, out, err = self.run_cli(fake, "setup", "--platform", "android", "--store-id", "com.example.myapp",
                                      "--format", "banner", "native")
        self.assertEqual(code, 0, err)
        self.assertEqual(fake.posts(), [])
        result = json.loads(out)
        self.assertTrue(result["dryRun"])
        self.assertEqual(result["requests"][0]["body"]["adFormat"], "NATIVE")
        self.assertIn("[dry-run] POST", err)
        self.assertIn("--yes", err)

    def test_dry_run_new_app_does_not_post(self):
        self.write_token()
        fake = FakeAdmob()
        code, out, err = self.run_cli(fake, "apps", "create", "--platform", "android",
                                      "--store-id", "com.example.myapp")
        self.assertEqual(code, 0, err)
        self.assertEqual(fake.posts(), [])
        self.assertEqual(json.loads(out)["requests"][0]["body"]["platform"], "ANDROID")

    def test_units_create_default_prefix_from_app_name(self):
        self.write_token()
        fake = FakeAdmob(apps=[self.existing_app])
        code, out, err = self.run_cli(fake, "units", "create", "--app", APP_ID, "--format", "banner")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["requests"][0]["body"]["displayName"], "MyApp_Banner")

    def test_multiple_accounts_requires_publisher(self):
        self.write_token()
        fake = FakeAdmob(accounts=[{"name": "accounts/pub-1111", "publisherId": "pub-1111"},
                                   {"name": "accounts/pub-2222", "publisherId": "pub-2222"}])
        code, _, err = self.run_cli(fake, "apps", "list")
        self.assertEqual(code, 1)
        self.assertIn("pub-1111", err)
        self.assertIn("--publisher", err)
        code, _, err = self.run_cli(fake, "apps", "list", "--publisher", "pub-2222")
        self.assertEqual(code, 0, err)

    def test_table_output(self):
        self.write_token()
        fake = FakeAdmob(apps=[self.existing_app])
        code, out, _ = self.run_cli(fake, "apps", "list", "--table")
        self.assertEqual(code, 0)
        self.assertIn("APP_ID", out.splitlines()[0])
        self.assertIn(APP_ID, out)


class ErrorMessageTest(TempTokenMixin, unittest.TestCase):
    def test_scope_insufficient(self):
        payload = {"error": {"code": 403, "message": "Request had insufficient authentication scopes.",
                             "status": "PERMISSION_DENIED",
                             "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo",
                                          "reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}}
        msg = admob.explain_http_error("POST", "accounts/x/apps", 403, json.dumps(payload))
        self.assertIn("admob.monetization", msg)
        self.assertIn("auth", msg)
        self.assertNotIn("account manager", msg)

    def test_limited_access(self):
        payload = {"error": {"code": 403, "message": "The caller does not have permission",
                             "status": "PERMISSION_DENIED"}}
        msg = admob.explain_http_error("POST", "accounts/x/adUnits", 403, json.dumps(payload))
        self.assertIn("limited access", msg)
        self.assertIn("account manager", msg)
        self.assertNotIn("lacks a required scope", msg)

    def test_403_via_cli_exits_nonzero(self):
        self.write_token()
        fake = FakeAdmob(errors={("POST", f"accounts/{PUB}/apps"): (403, {"error": {
            "code": 403, "message": "The caller does not have permission", "status": "PERMISSION_DENIED"}})})
        code, out, err = self.run_cli(fake, "apps", "create", "--platform", "ios", "--store-id", "1234567890",
                                      "--yes")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("account manager", err)


class TokenTest(TempTokenMixin, unittest.TestCase):
    def test_token_file_mode_600(self):
        path = admob.save_token({"refresh_token": "x"})
        self.assertEqual(path, self.token_path)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        # Overwriting a pre-existing world-readable file still ends at 600.
        os.chmod(path, 0o644)
        admob.save_token({"refresh_token": "y"})
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_default_token_path(self):
        os.environ.pop("ADMOB_TOKEN_PATH")
        self.assertEqual(admob.token_path(), os.path.expanduser("~/.config/app-shipping/admob-token.json"))

    def test_client_secret_json(self):
        p = os.path.join(self.tmp.name, "client.json")
        with open(p, "w") as f:
            json.dump({"installed": {"client_id": "cid", "client_secret": "sec"}}, f)
        self.assertEqual(admob.client_credentials(p), ("cid", "sec"))
        with open(p, "w") as f:
            json.dump({"foo": {}}, f)
        with self.assertRaises(admob.AdmobError):
            admob.client_credentials(p)

    def test_expired_token_non_tty_prints_command(self):
        self.write_token()
        fake = FakeAdmob(token_error={"error": "invalid_grant", "error_description": "Token has been expired"})
        with mock.patch.object(admob, "run_auth_flow") as flow, \
                mock.patch.object(sys, "stdin", io.StringIO()):
            code, _, err = self.run_cli(fake, "apps", "list")
        flow.assert_not_called()
        self.assertEqual(code, 1)
        self.assertIn("auth", err)
        self.assertIn("7 days", err)

    def test_expired_token_tty_starts_auth(self):
        self.write_token()
        fake = FakeAdmob(token_error={"error": "invalid_grant"})
        tty = mock.Mock()
        tty.isatty.return_value = True
        with mock.patch.object(admob, "run_auth_flow", return_value={"access_token": "ya29.new"}) as flow, \
                mock.patch.object(sys, "stdin", tty):
            code, _, err = self.run_cli(fake, "apps", "list")
        flow.assert_called_once()
        self.assertEqual(code, 0, err)

    def test_no_auth_flag_prevents_auto_auth(self):
        self.write_token()
        fake = FakeAdmob(token_error={"error": "invalid_grant"})
        tty = mock.Mock()
        tty.isatty.return_value = True
        with mock.patch.object(admob, "run_auth_flow") as flow, mock.patch.object(sys, "stdin", tty):
            code, _, _ = self.run_cli(fake, "apps", "list", "--no-auth")
        flow.assert_not_called()
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
