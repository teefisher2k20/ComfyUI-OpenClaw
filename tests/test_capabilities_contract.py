"""
Contract test for Capabilities API (R83).
Verifies that runtime_profile is correctly exposed in the capabilities surface.
"""

import os
import unittest
from unittest.mock import patch

from services.capabilities import get_capabilities
from services.runtime_profile import RuntimeProfile


class TestCapabilitiesContract(unittest.TestCase):

    def test_capabilities_structure(self):
        """Verify capabilities response structure and required fields."""
        caps = get_capabilities()
        self.assertIn("runtime_profile", caps)
        self.assertIn("api_version", caps)
        self.assertIn("pack", caps)
        self.assertIn("features", caps)
        self.assertIn("png_info", caps["features"])

    def test_runtime_profile_exposure(self):
        """Verify runtime_profile reflects the actual resolved profile."""
        # Case 1: Default (Minimal)
        with patch.dict(os.environ, {}, clear=True):
            caps = get_capabilities()
            self.assertEqual(caps["runtime_profile"], "minimal")

        # Case 2: Hardened
        with patch.dict(os.environ, {"OPENCLAW_RUNTIME_PROFILE": "hardened"}):
            caps = get_capabilities()
            self.assertEqual(caps["runtime_profile"], "hardened")


if __name__ == "__main__":
    unittest.main()
