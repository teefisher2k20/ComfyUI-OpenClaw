"""
S33/R76/R78 auth hardening contract tests.

Covers:
- S33: strict localhost same-origin behavior for state-changing requests.
- R76: observability auth denial parity for events poll/stream handlers.
- R78: route auth-class manifest coverage + handler contract checks.
"""

import inspect
import os
import unittest
from unittest.mock import MagicMock, patch

# CI guardrail: keep test importable when aiohttp is absent.
try:
    from aiohttp import web
except ImportError:  # pragma: no cover
    web = None

from services.csrf_protection import is_same_origin_request

# R78: Explicit auth class contract per method+route suffix.
# Any non-optional /openclaw|/moltbot route added in register_routes must be
# classified here, otherwise tests fail.
AUTH_CLASS_BY_ROUTE = {
    ("GET", "/admin"): "public-safe",
    ("GET", "/health"): "public-safe",
    # IMPORTANT:
    # `/logs/tail` was hardened to admin-only because log payload can expose
    # high-sensitivity prompt/runtime context (S34). Keep this auth class in
    # sync with api/logs_tail.py to avoid accidental privilege regression.
    ("GET", "/logs/tail"): "admin",
    ("GET", "/jobs"): "public-safe",
    # IMPORTANT:
    # Trace endpoint now returns high-sensitivity execution context and is
    # intentionally admin-only (S34). Keep as admin to prevent data leakage.
    ("GET", "/trace/{prompt_id}"): "admin",
    ("POST", "/webhook"): "webhook-auth",
    ("POST", "/webhook/submit"): "webhook-auth",
    ("POST", "/webhook/validate"): "webhook-auth",
    ("GET", "/capabilities"): "public-safe",
    ("GET", "/config"): "observability",
    ("PUT", "/config"): "admin",
    ("POST", "/llm/test"): "admin",
    ("POST", "/llm/chat"): "admin",
    ("GET", "/llm/models"): "admin",
    ("GET", "/templates"): "observability",
    ("POST", "/preflight"): "admin",
    ("GET", "/preflight/inventory"): "admin",
    ("GET", "/checkpoints"): "admin",
    ("POST", "/checkpoints"): "admin",
    ("GET", "/checkpoints/{id}"): "admin",
    ("DELETE", "/checkpoints/{id}"): "admin",
    ("GET", "/rewrite/recipes"): "admin",
    ("POST", "/rewrite/recipes"): "admin",
    ("GET", "/rewrite/recipes/{recipe_id}"): "admin",
    ("PUT", "/rewrite/recipes/{recipe_id}"): "admin",
    ("DELETE", "/rewrite/recipes/{recipe_id}"): "admin",
    ("POST", "/rewrite/recipes/{recipe_id}/dry-run"): "admin",
    ("POST", "/rewrite/recipes/{recipe_id}/apply"): "admin",
    ("GET", "/models/search"): "admin",
    ("POST", "/models/downloads"): "admin",
    ("GET", "/models/downloads"): "admin",
    ("GET", "/models/downloads/{task_id}"): "admin",
    ("POST", "/models/downloads/{task_id}/cancel"): "admin",
    ("POST", "/models/import"): "admin",
    ("GET", "/models/installations"): "admin",
    ("GET", "/secrets/status"): "admin",
    ("PUT", "/secrets"): "admin",
    ("DELETE", "/secrets/{provider}"): "admin",
    ("GET", "/events/stream"): "observability",
    ("GET", "/events"): "observability",
    ("GET", "/security/doctor"): "admin",
    ("GET", "/tools"): "admin",
    ("POST", "/tools/{name}/run"): "admin",
    ("GET", "/connector/installations"): "admin",
    ("GET", "/connector/installations/resolve"): "admin",
    ("GET", "/connector/installations/audit"): "admin",
    ("GET", "/connector/installations/{installation_id}"): "admin",
    ("POST", "/lab/sweep"): "admin",
    ("GET", "/lab/experiments"): "admin",
    ("GET", "/lab/experiments/{exp_id}"): "admin",
    ("POST", "/lab/experiments/{exp_id}/runs/{run_id}"): "admin",
    ("POST", "/lab/experiments/{exp_id}/winner"): "admin",
    ("POST", "/lab/compare"): "admin",
}

OPTIONAL_SUFFIX_PREFIXES = (
    "/assist/",
    "/packs",
)


def _strip_prefix(path: str):
    for prefix in ("/openclaw", "/moltbot"):
        if path.startswith(prefix):
            return prefix, path[len(prefix) :]
    return None, None


@unittest.skipIf(web is None, "aiohttp not installed")
class TestS33LocalhostHardening(unittest.TestCase):
    def setUp(self):
        self.patcher = patch.dict(os.environ, {}, clear=True)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    @staticmethod
    def _make_req(headers):
        req = MagicMock()
        req.headers = headers
        return req

    def test_strict_origin_defaults(self):
        req = self._make_req({"Sec-Fetch-Site": "same-origin"})
        self.assertTrue(is_same_origin_request(req))

        req = self._make_req({"Sec-Fetch-Site": "cross-site"})
        self.assertFalse(is_same_origin_request(req))

        req = self._make_req({})
        self.assertFalse(is_same_origin_request(req))

    def test_legacy_origin_flag(self):
        os.environ["OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN"] = "true"
        req = self._make_req({})
        self.assertTrue(is_same_origin_request(req))


@unittest.skipIf(web is None, "aiohttp not installed")
class TestR76ObservabilityAuth(unittest.IsolatedAsyncioTestCase):
    async def test_events_handlers_return_403_on_denial(self):
        with (
            patch(
                "api.events.require_observability_access",
                return_value=(False, "denied"),
            ),
            patch("api.events.check_rate_limit", return_value=True),
        ):
            from api.events import events_poll_handler, events_stream_handler

            req = MagicMock()
            req.headers = {}
            req.query = {}

            poll_resp = await events_poll_handler(req)
            self.assertEqual(poll_resp.status, 403)

            stream_resp = await events_stream_handler(req)
            self.assertEqual(stream_resp.status, 403)


@unittest.skipIf(web is None, "aiohttp not installed")
class TestR78AuthMatrix(unittest.TestCase):
    @staticmethod
    def _register_and_collect_routes():
        from api.routes import register_routes

        server = MagicMock()
        server.routes.get = MagicMock()
        server.routes.post = MagicMock()
        server.routes.put = MagicMock()
        server.routes.delete = MagicMock()
        server.app.router.add_route = MagicMock()

        register_routes(server)

        rows = []
        for call in server.app.router.add_route.call_args_list:
            method, path, handler = call.args
            rows.append((method, path, handler))
        return rows

    def test_auth_manifest_covers_registered_core_routes(self):
        rows = self._register_and_collect_routes()
        by_method_path = {(method, path) for method, path, _handler in rows}

        # 1) Explicit expectations for all mandatory core routes (both namespaces + /api).
        for method, suffix in AUTH_CLASS_BY_ROUTE:
            for prefix in ("/openclaw", "/moltbot"):
                base = f"{prefix}{suffix}"
                api = f"/api{base}"
                self.assertIn((method, base), by_method_path)
                self.assertIn((method, api), by_method_path)

        # 2) Drift guard: any newly-registered non-optional core route must be classified.
        unclassified = []
        for method, path, _handler in rows:
            if path.startswith("/api"):
                continue
            prefix, suffix = _strip_prefix(path)
            if prefix is None:
                continue
            if any(suffix.startswith(p) for p in OPTIONAL_SUFFIX_PREFIXES):
                continue
            if (method, suffix) not in AUTH_CLASS_BY_ROUTE:
                unclassified.append((method, path))

        self.assertEqual(
            [],
            unclassified,
            msg=f"Unclassified core routes detected: {unclassified}",
        )

    def test_handler_auth_contract_matches_declared_class(self):
        rows = self._register_and_collect_routes()

        # Pick /openclaw base routes only to avoid duplicate checks across aliases.
        handler_by_route = {}
        for method, path, handler in rows:
            if not path.startswith("/openclaw"):
                continue
            _prefix, suffix = _strip_prefix(path)
            if suffix is None:
                continue
            if any(suffix.startswith(p) for p in OPTIONAL_SUFFIX_PREFIXES):
                continue
            handler_by_route[(method, suffix)] = handler

        missing = [k for k in AUTH_CLASS_BY_ROUTE if k not in handler_by_route]
        self.assertEqual([], missing, msg=f"Missing handlers for routes: {missing}")

        for route_key, auth_class in AUTH_CLASS_BY_ROUTE.items():
            handler = handler_by_route[route_key]
            source = inspect.getsource(handler)

            if auth_class == "observability":
                self.assertIn("require_observability_access", source, msg=route_key)
            elif auth_class == "admin":
                self.assertTrue(
                    ("require_admin_token(" in source or "_require_admin(" in source),
                    msg=f"{route_key} expected admin guard",
                )
            elif auth_class == "webhook-auth":
                self.assertIn("require_auth(", source, msg=route_key)
            elif auth_class == "public-safe":
                self.assertNotIn("require_admin_token(", source, msg=route_key)
                self.assertNotIn("_require_admin(", source, msg=route_key)
                self.assertNotIn("require_observability_access", source, msg=route_key)
                self.assertNotIn("require_auth(", source, msg=route_key)
            else:
                self.fail(f"Unknown auth class for {route_key}: {auth_class}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
