#!/usr/bin/env python3
"""Public profile and private rollback tests, using temporary directories only."""
import copy
import json
import math
import os
from pathlib import Path
import secrets
import stat
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webpanel"))
import profiles


CONFIG = {"name": "Profilo 1080i", "input_url": "srt://127.0.0.1:12000",
          "output_url": "udp://127.0.0.1:13000", "video_bitrate_kbps": 8000,
          "outputs": [{"id": "main", "url": "srt://127.0.0.1:14000", "protocol": "srt"}]}


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="obe-profile-test-")
        self.folder = Path(self.temp.name) / "private"
        self.profiles = profiles.Profiles(self.folder)
        self.input_key, self.output_key = secrets.token_hex(16), secrets.token_hex(16)

    def tearDown(self):
        self.temp.cleanup()

    def backup(self, config=None):
        self.profiles.backup(config or CONFIG, {"passphrase": self.input_key}, {"main": self.output_key})

    def test_export_is_versioned_and_detached(self):
        original = copy.deepcopy(CONFIG)
        document = self.profiles.export(original)
        self.assertEqual(document["format"], "obe-webpanel-profile")
        self.assertEqual(document["version"], 1)
        self.assertEqual(set(document), {"format", "version", "config"})
        document["config"]["outputs"][0]["id"] = "changed"
        self.assertEqual(original, CONFIG)
        self.assertFalse(self.folder.exists())

    def test_parse_delegates_semantic_validation(self):
        seen = []
        def validator(config):
            seen.append(config)
            return {**config, "preset": "veryfast"}
        document = self.profiles.export(CONFIG)
        parsed = self.profiles.parse(document, validator)
        self.assertEqual(parsed["preset"], "veryfast")
        self.assertEqual(seen, [CONFIG])
        self.assertNotIn("preset", document["config"])

    def test_srt_output_options_roundtrip_without_credentials(self):
        config = copy.deepcopy(CONFIG)
        config["outputs"][0].update(srt_key_length=16, srt_max_bandwidth_kbps=12000,
            srt_connect_timeout_ms=3000, srt_send_timeout_ms=2000,
            srt_drop_late_packets=False)
        document = self.profiles.export(config)
        self.assertEqual(self.profiles.parse(document, lambda value: value), config)
        self.assertNotIn(self.output_key, json.dumps(document))

    def test_parse_propagates_config_error_without_saving(self):
        class ConfigError(ValueError):
            pass
        def reject(config):
            raise ConfigError("invalid bitrate")
        with self.assertRaises(ConfigError):
            self.profiles.parse(self.profiles.export(CONFIG), reject)
        self.assertFalse(self.folder.exists())

    def test_unknown_envelopes_and_versions_are_rejected(self):
        valid = self.profiles.export(CONFIG)
        for document in ({}, [], "document", {**valid, "unknown": True},
                         {**valid, "version": True}, {**valid, "version": "1"},
                         {**valid, "version": 2}, {**valid, "format": "other"},
                         {**valid, "config": []}, {**valid, "credentials": {}}):
            with self.assertRaises(profiles.ProfileError):
                self.profiles.parse(document, lambda config: config)

    def test_private_fields_and_credentials_in_urls_are_rejected(self):
        for config in ({**CONFIG, "credentials": {}}, {**CONFIG, "output_credentials": {}},
                       {**CONFIG, "password": self.input_key},
                       {**CONFIG, "outputs": [{"passphrase": self.output_key}]},
                       {**CONFIG, "input_url": "https://user:password@host/video"},
                       {**CONFIG, "input_url": "srt://host:14000?passphrase=credential-value"},
                       {**CONFIG, "input_url": "https://host/video?%2574oken=credential-value"}):
            for operation in (lambda: self.profiles.export(config),
                              lambda: self.profiles.parse({"format": profiles.FORMAT, "version": 1, "config": config}, lambda value: value)):
                try:
                    operation()
                except profiles.ProfileError as error:
                    self.assertTrue(self.input_key not in str(error) and self.output_key not in str(error))
                else:
                    self.fail("Credenziali accettate nel profilo pubblico")

    def test_validator_cannot_insert_private_data(self):
        with self.assertRaises(profiles.ProfileError):
            self.profiles.parse(self.profiles.export(CONFIG), lambda config: {**config, "password": self.input_key})

    def test_size_depth_types_and_nan_are_bounded(self):
        nested = []
        for _ in range(10):
            nested = [nested]
        for value in ("x" * 65537, [0] * 4097, nested, math.nan, math.inf, object(), 2**54, "\ud800"):
            with self.assertRaises(profiles.ProfileError):
                self.profiles.export({"value": value})
        with self.assertRaises(profiles.ProfileError):
            self.profiles.export({"value": "é" * 40000})

    def test_snapshot_is_private_atomic_and_not_in_export(self):
        self.assertIsNone(self.profiles.previous())
        self.backup()
        previous = self.profiles.previous()
        self.assertEqual(previous["config"], CONFIG)
        self.assertTrue(previous["credentials"]["passphrase"] == self.input_key)
        self.assertTrue(previous["output_credentials"]["main"] == self.output_key)
        self.assertEqual(stat.S_IMODE(self.folder.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.profiles.path.stat().st_mode), 0o600)
        exported = json.dumps(self.profiles.export(previous["config"]))
        self.assertTrue(self.input_key not in exported and self.output_key not in exported)
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), ["previous.json"])

    def test_only_the_last_snapshot_is_retained(self):
        self.backup()
        new_config = {**CONFIG, "name": "Profilo aggiornato"}
        self.profiles.backup(new_config, {}, {})
        self.assertEqual(self.profiles.previous()["config"], new_config)
        self.assertEqual(self.profiles.previous()["credentials"], {})
        self.assertEqual(len(list(self.folder.iterdir())), 1)

    def test_write_failure_preserves_previous_and_removes_temporary(self):
        self.backup()
        original = self.profiles.path.read_bytes()
        with patch.object(profiles.os, "replace", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                self.profiles.backup({**CONFIG, "name": "new"}, {}, {})
        self.assertTrue(self.profiles.path.read_bytes() == original)
        self.assertEqual([item.name for item in self.folder.iterdir()], ["previous.json"])

    def test_restricts_existing_permissions(self):
        self.backup()
        self.profiles.path.chmod(0o644)
        self.profiles.previous()
        self.assertEqual(stat.S_IMODE(self.profiles.path.stat().st_mode), 0o600)

    def test_invalid_private_credentials_do_not_replace_snapshot(self):
        self.backup()
        original = self.profiles.path.read_bytes()
        for source, outputs in (({"unexpected": "value"}, {}), ({"password": "x" * 1025}, {}),
                                ({"passphrase": "short"}, {}), ({}, {"main": "short"}),
                                ({}, {"../main": "long-enough"}), ({}, {"main": None})):
            with self.assertRaises(profiles.ProfileError):
                self.profiles.backup(CONFIG, source, outputs)
        self.assertTrue(self.profiles.path.read_bytes() == original)

    def test_corrupt_unknown_duplicate_and_nonfinite_snapshots_fail_closed(self):
        self.backup()
        document = self.profiles.previous()
        for raw in (b"broken", b"\xff", b"x" * 65537,
                    json.dumps({**document, "version": 2}).encode(),
                    json.dumps({**document, "unknown": 1}).encode(),
                    b'{"format":"one","format":"two"}',
                    b'{"value":NaN}', b"[" * 1000 + b"]" * 1000):
            self.profiles.path.write_bytes(raw)
            with self.assertRaises(profiles.ProfileError):
                self.profiles.previous()

    def test_symlink_snapshot_and_directory_are_rejected(self):
        self.folder.mkdir()
        external = Path(self.temp.name) / "external.json"
        external.write_text("{}")
        self.profiles.path.symlink_to(external)
        with self.assertRaises(profiles.ProfileError):
            self.profiles.previous()
        alias = Path(self.temp.name) / "alias"
        alias.symlink_to(self.folder, target_is_directory=True)
        with self.assertRaises(profiles.ProfileError):
            profiles.Profiles(alias).backup(CONFIG, {}, {})
        with self.assertRaises(profiles.ProfileError):
            profiles.Profiles(alias).previous()
        self.assertEqual(external.read_text(), "{}")

    def test_fifo_is_rejected_without_blocking(self):
        self.folder.mkdir()
        os.mkfifo(self.profiles.path)
        with self.assertRaises(profiles.ProfileError):
            self.profiles.previous()

    def test_concurrent_read_and_backup_are_whole_snapshots(self):
        self.backup()
        failures = []
        def writer():
            try:
                for index in range(20):
                    self.profiles.backup({**CONFIG, "name": f"Profilo {index}"}, {}, {})
            except Exception:
                failures.append("writer")
        thread = threading.Thread(target=writer)
        thread.start()
        while thread.is_alive():
            snapshot = self.profiles.previous()
            self.assertIsInstance(snapshot["config"], dict)
            self.assertEqual(snapshot["format"], profiles.BACKUP_FORMAT)
        thread.join(timeout=3)
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
