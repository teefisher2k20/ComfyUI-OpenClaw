import os
import unittest
from unittest.mock import patch

import services.audit as audit_module
from services.bridge_token_lifecycle import BridgeTokenStore


class TestS79AuditHashing(unittest.TestCase):
    def test_chain_hash_is_keyed(self):
        entry = {"action": "config.update", "target": "settings.json"}

        with patch.object(audit_module, "_AUDIT_CHAIN_KEY", b"key-a"):
            hash_a = audit_module._chain_hash("GENESIS", entry)

        with patch.object(audit_module, "_AUDIT_CHAIN_KEY", b"key-b"):
            hash_b = audit_module._chain_hash("GENESIS", entry)

        self.assertNotEqual(hash_a, hash_b)

    def test_chain_hash_is_stable_for_same_key(self):
        entry = {"action": "config.update", "target": "settings.json"}

        with patch.object(audit_module, "_AUDIT_CHAIN_KEY", b"fixed-key"):
            hash_a = audit_module._chain_hash("GENESIS", entry)
            hash_b = audit_module._chain_hash("GENESIS", entry)

        self.assertEqual(hash_a, hash_b)


class TestS79BridgeTokenHashing(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("OPENCLAW_BRIDGE_TOKEN_INDEX_KEY", None)
        os.environ.pop("MOLTBOT_BRIDGE_TOKEN_INDEX_KEY", None)

    def test_token_hash_differs_across_store_instances_without_override(self):
        store_a = BridgeTokenStore()
        store_b = BridgeTokenStore()

        self.assertNotEqual(
            store_a._hash_token("secret-token"), store_b._hash_token("secret-token")
        )

    def test_token_hash_can_be_pinned_via_env_override(self):
        with patch.dict(
            os.environ,
            {"OPENCLAW_BRIDGE_TOKEN_INDEX_KEY": "fixed-token-index-key"},
            clear=False,
        ):
            store_a = BridgeTokenStore()
            store_b = BridgeTokenStore()

        self.assertEqual(
            store_a._hash_token("secret-token"), store_b._hash_token("secret-token")
        )
