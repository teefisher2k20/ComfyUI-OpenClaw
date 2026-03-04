import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api import connector_contracts as mod


class TestAPIConnectorContracts(unittest.IsolatedAsyncioTestCase):
    async def test_list_installations_success(self):
        request = AsyncMock()
        request.query = {"platform": "slack", "workspace_id": "T1"}

        installation = MagicMock()
        installation.to_public_dict.return_value = {
            "installation_id": "inst-1",
            "platform": "slack",
            "workspace_id": "T1",
            "status": "active",
            "token_refs": {"bot_token": "ref"},
        }

        registry = MagicMock()
        registry.list_installations.return_value = [installation]
        registry.diagnostics.return_value = {"installation_count": 1}

        with (
            patch("api.connector_contracts.check_rate_limit", return_value=True),
            patch(
                "api.connector_contracts.require_admin_token", return_value=(True, None)
            ),
            patch(
                "api.connector_contracts.get_connector_installation_registry",
                return_value=registry,
            ),
        ):
            resp = await mod.connector_installations_list_handler(request)

        self.assertEqual(resp.status, 200)
        body = json.loads(resp.body)
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["installations"]), 1)
        self.assertEqual(body["diagnostics"]["installation_count"], 1)

    async def test_get_installation_not_found(self):
        request = AsyncMock()
        request.query = {}
        request.match_info = {"installation_id": "missing"}

        registry = MagicMock()
        registry.get_installation.return_value = None

        with (
            patch("api.connector_contracts.check_rate_limit", return_value=True),
            patch(
                "api.connector_contracts.require_admin_token", return_value=(True, None)
            ),
            patch(
                "api.connector_contracts.get_connector_installation_registry",
                return_value=registry,
            ),
        ):
            resp = await mod.connector_installation_get_handler(request)

        self.assertEqual(resp.status, 404)

    async def test_resolve_installation_conflict(self):
        request = AsyncMock()
        request.query = {"platform": "slack", "workspace_id": "T1"}

        resolution = MagicMock()
        resolution.ok = False
        resolution.to_public_dict.return_value = {
            "ok": False,
            "reject_reason": "ambiguous_binding",
            "audit_code": "conn_install.resolve_ambiguous",
        }

        registry = MagicMock()
        registry.resolve_installation.return_value = resolution

        with (
            patch("api.connector_contracts.check_rate_limit", return_value=True),
            patch(
                "api.connector_contracts.require_admin_token", return_value=(True, None)
            ),
            patch(
                "api.connector_contracts.get_connector_installation_registry",
                return_value=registry,
            ),
        ):
            resp = await mod.connector_installation_resolve_handler(request)

        self.assertEqual(resp.status, 409)
        body = json.loads(resp.body)
        self.assertFalse(body["ok"])

    async def test_audit_handler_unauthorized(self):
        request = AsyncMock()
        request.query = {}

        with (
            patch("api.connector_contracts.check_rate_limit", return_value=True),
            patch(
                "api.connector_contracts.require_admin_token",
                return_value=(False, "Unauthorized"),
            ),
        ):
            resp = await mod.connector_installation_audit_handler(request)

        self.assertEqual(resp.status, 403)


if __name__ == "__main__":
    unittest.main()
