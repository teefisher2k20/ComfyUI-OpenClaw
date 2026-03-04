import os
import tempfile
import unittest

from services.connector_installation_registry import (
    ConnectorInstallationRegistry,
    InstallationStatus,
)
from services.secret_store import SecretStore


class TestConnectorInstallationRegistry(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_dir = self.tmpdir.name
        self.secret_store = SecretStore(state_dir=self.state_dir)
        self.registry = ConnectorInstallationRegistry(
            state_dir=self.state_dir,
            secret_store=self.secret_store,
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_lifecycle_state_matrix(self):
        inst = self.registry.upsert_installation(
            platform="slack",
            workspace_id="T1",
            installation_id="inst-1",
            token_values={"bot_token": "xoxb-abc"},
            status=InstallationStatus.CREATED.value,
        )
        self.assertEqual(inst.status, InstallationStatus.CREATED.value)

        inst = self.registry.activate_installation("inst-1")
        self.assertEqual(inst.status, InstallationStatus.ACTIVE.value)

        inst = self.registry.rotate_installation_tokens(
            "inst-1", {"bot_token": "xoxb-rotated"}
        )
        self.assertEqual(inst.status, InstallationStatus.ROTATING.value)

        inst = self.registry.revoke_installation("inst-1")
        self.assertEqual(inst.status, InstallationStatus.REVOKED.value)

        inst = self.registry.deactivate_installation("inst-1")
        self.assertEqual(inst.status, InstallationStatus.DEACTIVATED.value)

        inst = self.registry.uninstall_installation("inst-1")
        self.assertEqual(inst.status, InstallationStatus.UNINSTALLED.value)

    def test_resolution_known_workspace(self):
        self.registry.upsert_installation(
            platform="slack",
            workspace_id="T1",
            installation_id="inst-1",
            token_values={"bot_token": "xoxb-abc"},
            status=InstallationStatus.ACTIVE.value,
        )

        res = self.registry.resolve_installation("slack", "T1")
        self.assertTrue(res.ok)
        self.assertEqual(res.installation.installation_id, "inst-1")

    def test_resolution_missing_workspace(self):
        res = self.registry.resolve_installation("slack", "missing")
        self.assertFalse(res.ok)
        self.assertEqual(res.reject_reason, "missing_binding")

    def test_resolution_duplicate_binding_fails_closed(self):
        self.registry.upsert_installation(
            platform="slack",
            workspace_id="T1",
            installation_id="inst-a",
            token_values={"bot_token": "xoxb-a"},
            status=InstallationStatus.ACTIVE.value,
        )
        self.registry.upsert_installation(
            platform="slack",
            workspace_id="T1",
            installation_id="inst-b",
            token_values={"bot_token": "xoxb-b"},
            status=InstallationStatus.ACTIVE.value,
        )

        res = self.registry.resolve_installation("slack", "T1")
        self.assertFalse(res.ok)
        self.assertEqual(res.reject_reason, "ambiguous_binding")

    def test_resolution_stale_token_ref_fails_closed(self):
        self.registry.upsert_installation(
            platform="slack",
            workspace_id="T2",
            installation_id="inst-stale",
            token_refs={"bot_token": "connector_installation:inst-stale:bot_token"},
            status=InstallationStatus.ACTIVE.value,
        )

        res = self.registry.resolve_installation("slack", "T2")
        self.assertFalse(res.ok)
        self.assertTrue(res.reject_reason.startswith("stale_token_ref"))

    def test_resolution_inactive_binding_fails_closed(self):
        self.registry.upsert_installation(
            platform="slack",
            workspace_id="T3",
            installation_id="inst-revoked",
            token_values={"bot_token": "xoxb-revoked"},
            status=InstallationStatus.REVOKED.value,
        )

        res = self.registry.resolve_installation("slack", "T3")
        self.assertFalse(res.ok)
        self.assertEqual(res.reject_reason, "inactive_binding")

    def test_persistence_reload_and_redaction(self):
        self.registry.upsert_installation(
            platform="slack",
            workspace_id="T9",
            installation_id="inst-persist",
            token_values={"bot_token": "xoxb-secret"},
            status=InstallationStatus.ACTIVE.value,
        )

        reloaded = ConnectorInstallationRegistry(
            state_dir=self.state_dir,
            secret_store=SecretStore(state_dir=self.state_dir),
        )
        listed = reloaded.list_installations(platform="slack", workspace_id="T9")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].installation_id, "inst-persist")

        raw = open(
            os.path.join(self.state_dir, "connector_installations.json"),
            "r",
            encoding="utf-8",
        ).read()
        self.assertNotIn("xoxb-secret", raw)


if __name__ == "__main__":
    unittest.main()
