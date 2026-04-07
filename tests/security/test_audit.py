import json
import os
import unittest
from unittest.mock import MagicMock, patch

import services.redaction as redaction_module
from services.access_control import AuthTier, TokenInfo
from services.audit import (
    audit_config_write,
    audit_llm_test,
    audit_secret_delete,
    audit_secret_write,
    emit_audit_event,
)


class TestAudit(unittest.TestCase):
    def setUp(self):
        self.test_log = "test_audit.log"
        self.path_patcher = patch("services.audit.AUDIT_LOG_PATH", self.test_log)
        self.path_patcher.start()
        self.hash_patcher = patch("services.audit._LAST_HASH", None)
        self.hash_patcher.start()
        self.tag_key_patcher = patch.object(
            redaction_module, "_REDACTION_TAG_KEY", b"audit-test-redaction-key"
        )
        self.tag_key_patcher.start()
        if os.path.exists(self.test_log):
            os.remove(self.test_log)

    def tearDown(self):
        self.path_patcher.stop()
        self.hash_patcher.stop()
        self.tag_key_patcher.stop()
        if os.path.exists(self.test_log):
            os.remove(self.test_log)

    def _read_entries(self):
        with open(self.test_log, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_emit_audit_structure(self):
        token = TokenInfo(token_id="adm-1", role=AuthTier.ADMIN, scopes={"*"})
        emit_audit_event(
            action="config.update",
            target="settings.json",
            outcome="allow",
            token_info=token,
            status_code=200,
            details={"key": "value"},
        )

        entries = self._read_entries()
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        for field in (
            "ts",
            "source",
            "trace_id",
            "action",
            "target",
            "outcome",
            "status_code",
            "details",
            "prev_hash",
            "entry_hash",
        ):
            self.assertIn(field, entry)
        self.assertNotIn("adm-1", json.dumps(entry))
        self.assertEqual(entry["action"], "config.update")
        self.assertEqual(entry["target"], "settings.json")
        self.assertEqual(entry["outcome"], "allow")
        self.assertNotIn("role", entry)
        self.assertNotIn("scope", entry)
        self.assertNotIn("scopes", entry)

    def test_append_only_hash_chain(self):
        emit_audit_event("settings.config_write", "127.0.0.1", True)
        emit_audit_event("settings.config_write", "127.0.0.1", False, error="x")
        entries = self._read_entries()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["prev_hash"], "GENESIS")
        self.assertEqual(entries[1]["prev_hash"], entries[0]["entry_hash"])

    def test_legacy_shims_single_emit_contract(self):
        audit_config_write("1.2.3.4", ok=True)
        audit_llm_test("1.2.3.4", ok=False, error="bad")
        audit_secret_write("1.2.3.4", "openai", ok=True)
        audit_secret_delete("1.2.3.4", "openai", ok=False, error="not_found")

        entries = self._read_entries()
        self.assertEqual(len(entries), 4)
        actions = [e["action"] for e in entries]
        self.assertIn("config.update", actions)
        self.assertIn("llm.test_connection", actions)
        self.assertIn("secrets.write", actions)
        self.assertIn("secrets.delete", actions)

    def test_write_path_uses_atomic_lock(self):
        lock = MagicMock()
        lock.__enter__ = MagicMock(return_value=lock)
        lock.__exit__ = MagicMock(return_value=False)

        with patch("services.audit._AUDIT_WRITE_LOCK", lock):
            emit_audit_event("settings.config_write", "127.0.0.1", True)

        lock.__enter__.assert_called_once()
        lock.__exit__.assert_called_once()
