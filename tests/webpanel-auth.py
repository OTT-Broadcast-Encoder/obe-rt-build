#!/usr/bin/env python3
"""Authentication contracts with temporary accounts, files and real hashing."""

import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "webpanel"))
import auth


PASSWORD = "temporary-fixture-only-password"


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # One real setup hash shared only as immutable fixture input.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.json"
            auth.setup_auth(path, "operator", PASSWORD)
            cls.fixture = path.read_bytes()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "auth.json"
        self.path.write_bytes(self.fixture)
        self.path.chmod(0o600)
        self.auth = auth.Auth(self.path)

    def test_disabled_is_opt_in_and_later_setup_is_detected(self):
        path = self.path.with_name("absent.json")
        controller = auth.Auth(path)
        self.assertFalse(controller.enabled)
        self.assertFalse(controller.verify("x" * 43))
        with self.assertRaises(auth.AuthError) as raised:
            controller.login("operator", PASSWORD, "client")
        self.assertEqual(raised.exception.code, "auth_disabled")
        path.write_bytes(self.fixture)
        path.chmod(0o600)
        self.assertTrue(controller.enabled)

    def test_login_survives_restart_and_never_persists_bearer_or_password(self):
        token = self.auth.login("operator", PASSWORD, "client")
        self.assertTrue(self.auth.verify(token))
        self.assertTrue(auth.Auth(self.path).verify(token))
        self.assertNotIn(token, self.path.read_text())
        self.assertNotIn(PASSWORD, self.path.read_text())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        digest = hashlib.sha256(token.encode()).hexdigest()
        self.assertIn(digest, json.loads(self.path.read_text())["sessions"])
        self.assertFalse(self.auth.verify(token[:-1] + ("a" if token[-1] != "a" else "b")))
        self.assertFalse(self.auth.verify("bad\r\nSet-Cookie: x=y"))

    def test_logout_revokes_persistently_and_is_idempotent(self):
        token = self.auth.login("operator", PASSWORD, "client")
        another = auth.Auth(self.path)
        self.auth.logout(token)
        self.auth.logout(token)
        self.auth.logout(None)
        self.assertFalse(another.verify(token))
        self.assertFalse(auth.Auth(self.path).verify(token))

    def test_password_change_revokes_and_live_instance_reload(self):
        token = self.auth.login("operator", PASSWORD, "client")
        replacement = "replacement-fixture-only-password"
        auth.setup_auth(self.path, "operator", replacement, overwrite=True)
        self.assertFalse(self.auth.verify(token))
        with self.assertRaises(auth.AuthError) as raised:
            self.auth.login("operator", PASSWORD, "client")
        self.assertEqual(raised.exception.status, 401)
        self.assertTrue(self.auth.verify(self.auth.login("operator", replacement, "client")))

    def test_unknown_username_and_bad_password_share_error(self):
        errors = []
        for username, password in (("other", PASSWORD), ("operator", "incorrect")):
            with self.assertRaises(auth.AuthError) as raised:
                self.auth.login(username, password, "client")
            errors.append(raised.exception.body())
        self.assertEqual(errors[0], errors[1])
        self.assertEqual(self.auth._hashing, 0)

    def test_expiry_and_bounded_sessions(self):
        with mock.patch("auth.time.time", return_value=10_000):
            token = self.auth.login("operator", PASSWORD, "client")
        with mock.patch("auth.time.time", return_value=10_000 + auth.SESSION_TTL):
            self.assertFalse(self.auth.verify(token))
        data = json.loads(self.path.read_text())
        data["sessions"] = {hashlib.sha256(str(i).encode()).hexdigest(): 20_000 + i
                            for i in range(auth.MAX_SESSIONS)}
        self.path.write_text(json.dumps(data))
        with mock.patch("auth.time.time", return_value=15_000):
            token = self.auth.login("operator", PASSWORD, "client")
            self.assertTrue(self.auth.verify(token))
        saved = json.loads(self.path.read_text())["sessions"]
        self.assertEqual(len(saved), auth.MAX_SESSIONS)
        self.assertNotIn(hashlib.sha256(b"0").hexdigest(), saved)

    def test_rate_limits_precede_hashing_and_recover_after_window(self):
        with mock.patch("auth.time.monotonic", return_value=100), \
             mock.patch("auth.hashlib.pbkdf2_hmac", wraps=hashlib.pbkdf2_hmac) as hashing:
            for _ in range(auth.CLIENT_ATTEMPTS):
                with self.assertRaises(auth.AuthError) as raised:
                    self.auth.login("operator", "wrong", "client")
                self.assertEqual(raised.exception.status, 401)
            with self.assertRaises(auth.AuthError) as raised:
                self.auth.login("operator", PASSWORD, "client")
            self.assertEqual(raised.exception.status, 429)
            self.assertGreater(raised.exception.retry_after, 0)
            self.assertEqual(hashing.call_count, auth.CLIENT_ATTEMPTS)
        with mock.patch("auth.time.monotonic", return_value=100 + auth.CLIENT_WINDOW):
            self.assertTrue(self.auth.verify(self.auth.login("operator", PASSWORD, "client")))

    def test_global_rate_limit_and_client_memory_bound(self):
        # Rate bookkeeping is separate from the independently tested real KDF.
        with self.auth.lock, mock.patch("auth.time.monotonic", return_value=100):
            for i in range(auth.GLOBAL_ATTEMPTS):
                self.auth._rate_limit(str(i))
            for i in range(auth.MAX_CLIENTS * 2):
                with self.assertRaises(auth.AuthError):
                    self.auth._rate_limit("rotating-" + str(i))
            self.assertEqual(len(self.auth._global), auth.GLOBAL_ATTEMPTS)
            self.assertLessEqual(len(self.auth._clients), auth.MAX_CLIENTS)

    def test_hashing_does_not_hold_lock_and_password_rotation_wins(self):
        entered, release = threading.Event(), threading.Event()
        original = hashlib.pbkdf2_hmac

        def delayed(*args):
            entered.set()
            release.wait(3)
            return original(*args)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            with mock.patch("auth.hashlib.pbkdf2_hmac", side_effect=delayed):
                future = executor.submit(self.auth.login, "operator", PASSWORD, "client")
                self.assertTrue(entered.wait(1))
                self.assertTrue(self.auth.lock.acquire(timeout=0.1))
                self.auth.lock.release()
                data = json.loads(self.path.read_text())
                data["generation"] = "0" * 32
                self.path.write_text(json.dumps(data))
                release.set()
                with self.assertRaises(auth.AuthError) as raised:
                    future.result(timeout=3)
                self.assertEqual(raised.exception.status, 401)
        self.assertEqual(json.loads(self.path.read_text())["sessions"], {})

    def test_two_instances_preserve_concurrent_sessions(self):
        another = auth.Auth(self.path)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(controller.login, "operator", PASSWORD, "client")
                       for controller in (self.auth, another)]
            tokens = [future.result(timeout=5) for future in futures]
        self.assertNotEqual(tokens[0], tokens[1])
        self.assertTrue(all(self.auth.verify(token) for token in tokens))
        self.assertEqual(len(json.loads(self.path.read_text())["sessions"]), 2)

    def test_failed_session_write_does_not_authenticate(self):
        with mock.patch("auth.os.replace", side_effect=OSError("private diagnostic")):
            with self.assertRaises(auth.AuthError) as raised:
                self.auth.login("operator", PASSWORD, "client")
        self.assertEqual(raised.exception.status, 503)
        self.assertNotIn("private diagnostic", str(raised.exception))
        self.assertEqual(json.loads(self.path.read_text())["sessions"], {})
        self.assertEqual(list(self.path.parent.glob(".auth-*")), [])

    def test_config_corruption_permissions_and_removal_fail_closed(self):
        self.path.chmod(0o644)
        with self.assertRaises(auth.AuthError):
            _ = self.auth.enabled
        self.path.chmod(0o600)
        self.path.write_text("{malformed")
        with self.assertRaises(auth.AuthError):
            _ = self.auth.enabled
        self.path.unlink()
        with self.assertRaises(auth.AuthError):
            _ = self.auth.enabled

    def test_cookie_security_and_injection(self):
        token = self.auth.login("operator", PASSWORD, "client")
        cookie = self.auth.cookie(token, secure=True)
        for value in ("HttpOnly", "SameSite=Strict", "Secure", "Max-Age=604800", "Path=/"):
            self.assertIn(value, cookie)
        self.assertNotIn("; Secure", self.auth.cookie(token, secure=False))
        self.assertIn("Max-Age=0", self.auth.clear_cookie(secure=True))
        with self.assertRaises(ValueError):
            self.auth.cookie("x\r\nInjected: yes", secure=False)

    def test_symlink_auth_file_is_refused(self):
        link = self.path.with_name("link.json")
        link.symlink_to(self.path)
        with self.assertRaises(auth.AuthError):
            auth.Auth(link)

    def test_setup_rejects_weak_password_and_unconfirmed_replacement(self):
        for username, password in (("bad\nname", PASSWORD), ("operator", "short"), ("operator", "x" * 1025)):
            with self.assertRaises(ValueError):
                auth.setup_auth(self.path, username, password)
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            auth.setup_auth(self.path, "operator", PASSWORD)
        self.assertEqual(self.path.read_bytes(), before)

    def test_cli_password_stdin_never_prints_password(self):
        path = self.path.with_name("cli.json")
        script = Path(__file__).resolve().parent.parent / "webpanel" / "setup-auth.py"
        result = subprocess.run([sys.executable, str(script), "--auth-file", str(path), "--username", "operator",
                                 "--password-stdin"], input=PASSWORD + "\n", text=True, capture_output=True,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(PASSWORD, result.stdout + result.stderr)
        self.assertTrue(auth.Auth(path).enabled)


if __name__ == "__main__":
    unittest.main()
