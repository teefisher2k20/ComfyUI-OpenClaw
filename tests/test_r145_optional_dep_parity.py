"""
R145 optional-dependency lazy-import parity regressions.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


MODULES_UNDER_TEST = [
    "api.approvals",
    "api.assist",
    "api.capabilities",
    "api.model_manager",
    "api.presets",
    "api.remote_admin",
    "api.rewrite_recipes",
    "api.schedules",
    "api.secrets",
    "api.tools",
    "api.triggers",
    "api.webhook",
    "api.webhook_submit",
    "services.surface_guard",
]


def _purge_module(name: str) -> None:
    sys.modules.pop(name, None)


def _capture_parent_binding(name: str):
    parent_name, _, attr_name = name.rpartition(".")
    if not parent_name:
        return None, attr_name, False, None
    parent = sys.modules.get(parent_name)
    if parent is None:
        parent = importlib.import_module(parent_name)
    had_attr = hasattr(parent, attr_name)
    return parent, attr_name, had_attr, getattr(parent, attr_name, None)


def _restore_parent_binding(parent, attr_name: str, had_attr: bool, value) -> None:
    if parent is None:
        return
    if had_attr:
        setattr(parent, attr_name, value)
    else:
        parent.__dict__.pop(attr_name, None)


def _import_without_aiohttp(module_name: str):
    original_module = sys.modules.get(module_name)
    original_compat = sys.modules.get("services.aiohttp_compat")
    original_module_parent = _capture_parent_binding(module_name)
    original_compat_parent = _capture_parent_binding("services.aiohttp_compat")
    _purge_module(module_name)
    _purge_module("services.aiohttp_compat")
    try:
        with patch.dict(sys.modules, {"aiohttp": None}):
            return importlib.import_module(module_name)
    finally:
        if original_module is not None:
            sys.modules[module_name] = original_module
        else:
            _purge_module(module_name)
        _restore_parent_binding(*original_module_parent)

        if original_compat is not None:
            sys.modules["services.aiohttp_compat"] = original_compat
        else:
            _purge_module("services.aiohttp_compat")
        _restore_parent_binding(*original_compat_parent)


class TestR145OptionalDependencyParity(unittest.TestCase):
    def test_route_modules_import_without_aiohttp(self):
        for module_name in MODULES_UNDER_TEST:
            with self.subTest(module=module_name):
                module = _import_without_aiohttp(module_name)
                self.assertTrue(hasattr(module, "web"))
                self.assertFalse(
                    getattr(module.web, "__openclaw_aiohttp_available__", True)
                )

    def test_json_helpers_fail_explicitly_without_aiohttp(self):
        model_manager = _import_without_aiohttp("api.model_manager")
        webhook_submit = _import_without_aiohttp("api.webhook_submit")

        with self.assertRaisesRegex(RuntimeError, "aiohttp not available"):
            model_manager._json({"ok": True})

        with self.assertRaisesRegex(RuntimeError, "aiohttp not available"):
            webhook_submit.safe_error_response(400, "bad_request")

    def test_async_handlers_fail_explicitly_without_aiohttp(self):
        capabilities = _import_without_aiohttp("api.capabilities")
        remote_admin = _import_without_aiohttp("api.remote_admin")

        with self.assertRaisesRegex(RuntimeError, "aiohttp not available"):
            asyncio.run(capabilities.capabilities_handler(SimpleNamespace()))

        with self.assertRaisesRegex(RuntimeError, "aiohttp not available"):
            asyncio.run(remote_admin.remote_admin_page_handler(SimpleNamespace()))

    def test_surface_guard_stays_importable_in_local_mode(self):
        surface_guard = _import_without_aiohttp("services.surface_guard")
        with patch.dict(
            os.environ, {"OPENCLAW_DEPLOYMENT_PROFILE": "local"}, clear=False
        ):
            self.assertIsNone(surface_guard.check_surface("nonexistent-surface"))

    def test_remote_admin_fixture_exists(self):
        remote_admin = importlib.import_module("api.remote_admin")
        self.assertTrue(Path(remote_admin._admin_console_html_path()).exists())


if __name__ == "__main__":
    unittest.main()
