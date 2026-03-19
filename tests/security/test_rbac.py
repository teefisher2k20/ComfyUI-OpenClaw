import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from services.access_control import (
    get_current_auth_tier,
    is_any_token_configured,
    is_auth_configured,
    resolve_token_info,
    verify_scope_access,
    verify_tier_access,
)
from services.endpoint_manifest import AuthTier


class TestRBAC(unittest.TestCase):

    def setUp(self):
        self.mock_req = MagicMock()
        # Default keys for tests
        self.env_patcher = patch.dict(
            os.environ,
            {
                "OPENCLAW_ADMIN_TOKEN": "admin-secret",
                "OPENCLAW_OBSERVABILITY_TOKEN": "obs-secret",
            },
        )
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()

    def test_get_current_tier(self):
        # 1. Admin
        self.mock_req.headers = {"X-OpenClaw-Admin-Token": "admin-secret"}
        self.assertEqual(get_current_auth_tier(self.mock_req), AuthTier.ADMIN)

        # 2. Obs
        self.mock_req.headers = {"X-OpenClaw-Obs-Token": "obs-secret"}
        self.assertEqual(get_current_auth_tier(self.mock_req), AuthTier.OBSERVABILITY)

        # 3. Internal (Loopback)
        self.mock_req.headers = {}
        # We need to ensure get_client_ip returns 127.0.0.1
        with patch("services.access_control.get_client_ip", return_value="127.0.0.1"):
            self.assertEqual(get_current_auth_tier(self.mock_req), AuthTier.INTERNAL)

        # 4. Public (Remote)
        with patch("services.access_control.get_client_ip", return_value="1.2.3.4"):
            self.assertEqual(get_current_auth_tier(self.mock_req), AuthTier.PUBLIC)

    def test_verify_access_hierarchy(self):
        """Test verify_tier_access logic (Admin > Obs > Internal > Public)"""
        # Admin User -> Access Admin, Obs. NOT Internal (Strict)
        with patch(
            "services.access_control.get_current_auth_tier", return_value=AuthTier.ADMIN
        ):
            self.assertTrue(verify_tier_access(self.mock_req, AuthTier.ADMIN)[0])
            self.assertTrue(
                verify_tier_access(self.mock_req, AuthTier.OBSERVABILITY)[0]
            )

            # Strict S46: Admin Token (Remote) != Internal (Localhost Safe)
            self.assertFalse(verify_tier_access(self.mock_req, AuthTier.INTERNAL)[0])

        # Obs User -> Access Obs. NOT Admin, NOT Internal.
        with patch(
            "services.access_control.get_current_auth_tier",
            return_value=AuthTier.OBSERVABILITY,
        ):
            self.assertFalse(verify_tier_access(self.mock_req, AuthTier.ADMIN)[0])
            self.assertTrue(
                verify_tier_access(self.mock_req, AuthTier.OBSERVABILITY)[0]
            )
            self.assertFalse(verify_tier_access(self.mock_req, AuthTier.INTERNAL)[0])

        # Internal User (Loopback) -> Access Internal, Obs (implicit).
        with patch(
            "services.access_control.get_current_auth_tier",
            return_value=AuthTier.INTERNAL,
        ):
            # We must also mock get_client_ip because verifying INTERNAL tier checks IP directly now
            with patch(
                "services.access_control.get_client_ip", return_value="127.0.0.1"
            ):
                self.assertTrue(verify_tier_access(self.mock_req, AuthTier.INTERNAL)[0])
                self.assertTrue(
                    verify_tier_access(self.mock_req, AuthTier.OBSERVABILITY)[0]
                )

    def test_scope_enforcement_wildcard(self):
        # Admin Token (Scope *)
        self.mock_req.headers = {"X-OpenClaw-Admin-Token": "admin-secret"}
        passed, err = verify_scope_access(
            self.mock_req, ["read:logs", "write:config", "random:scope"]
        )
        self.assertTrue(passed, "Admin (*) should pass everything")

        # Obs Token (Scope read:*) - New behavior
        self.mock_req.headers = {"X-OpenClaw-Obs-Token": "obs-secret"}

        # Should pass read scopes
        passed, err = verify_scope_access(self.mock_req, ["read:logs", "read:metrics"])
        self.assertTrue(passed, "Obs (read:*) should pass read:logs")

        # Should fail write scopes
        passed, err = verify_scope_access(self.mock_req, ["write:config"])
        self.assertFalse(passed, "Obs (read:*) should fail write:config")

    def test_token_registry_lifecycle(self):
        """Test Issue/Revoke"""
        from services.access_control import TokenRegistry

        # Issue
        secret, info = TokenRegistry.issue(AuthTier.OBSERVABILITY, ["custom:scope"])
        self.assertIsNotNone(secret)
        self.assertEqual(info.role, AuthTier.OBSERVABILITY)
        self.assertIn("custom:scope", info.scopes)

        # Resolve using issued token
        self.mock_req.headers = {"X-OpenClaw-Obs-Token": secret}
        # Note: resolve_token_info checks all headers.

        # We need to make sure verify_scope_access calls resolve_token_info which checks registry
        # The env patcher is still active, but registry check comes first

        # We need to real-call resolve_token_info, not verify_scope_access directly to test lookup?
        # verify_scope_access calls resolve_token_info.

        passed, err = verify_scope_access(self.mock_req, ["custom:scope"])
        self.assertTrue(passed, "Issued token should work")

        # Revoke
        TokenRegistry.revoke(info.token_id)

        # Should fail now
        passed, err = verify_scope_access(self.mock_req, ["custom:scope"])
        self.assertFalse(
            passed,
            "Revoked token should fail (fallback to Env token which lacks custom:scope)",
        )

    def test_convenience_mode_scopes(self):
        """If no admin token is set, Loopback should be Admin (Scope *)"""
        with patch.dict(os.environ, {}, clear=True):
            self.mock_req.headers = {}
            with patch(
                "services.access_control.get_client_ip", return_value="127.0.0.1"
            ):
                passed, err = verify_scope_access(
                    self.mock_req, ["write:config", "system:root"]
                )
                self.assertTrue(
                    passed, "Loopback in Convenience Mode should have Admin scopes"
                )

    def test_is_auth_configured_accepts_legacy_admin_alias(self):
        # CRITICAL: mutation/adversarial gate relies on this legacy alias invariant.
        with patch.dict(
            os.environ,
            {"MOLTBOT_ADMIN_TOKEN": "legacy-admin-secret"},
            clear=True,
        ):
            self.assertTrue(is_auth_configured())

    def test_is_any_token_configured_accepts_legacy_obs_alias(self):
        # CRITICAL: keep coverage for OPENCLAW/MOLTBOT observability fallback parity.
        with patch.dict(
            os.environ,
            {"MOLTBOT_OBSERVABILITY_TOKEN": "legacy-obs-secret"},
            clear=True,
        ):
            self.assertTrue(is_any_token_configured())

    def test_resolve_token_info_preserves_header_tenant_for_env_token(self):
        # IMPORTANT: multi-tenant env-token auth must keep header tenant, not default.
        req = MagicMock()
        req.headers = {
            "X-OpenClaw-Admin-Token": "admin-secret",
            "X-OpenClaw-Tenant-Id": "Team-A",
        }
        with patch.dict(
            os.environ,
            {
                "OPENCLAW_ADMIN_TOKEN": "admin-secret",
                "OPENCLAW_MULTI_TENANT_ENABLED": "1",
            },
            clear=True,
        ):
            token_info = resolve_token_info(req)

        self.assertIsNotNone(token_info)
        self.assertEqual(token_info.tenant_id, "team-a")

    def test_resolve_token_info_accepts_legacy_admin_header(self):
        req = MagicMock()
        req.headers = {"X-Moltbot-Admin-Token": "admin-secret"}

        with patch("services.access_control.logger.warning") as warn:
            token_info = resolve_token_info(req)

        self.assertIsNotNone(token_info)
        self.assertEqual(token_info.role, AuthTier.ADMIN)
        warn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
