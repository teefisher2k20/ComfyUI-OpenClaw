"""
Tests for Capabilities Service (R19).
"""

import os
import sys
import unittest

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCapabilities(unittest.TestCase):
    """Test get_capabilities returns expected shape and features."""

    def test_capabilities_shape(self):
        from services.capabilities import get_capabilities

        caps = get_capabilities()

        # Must have api_version (integer)
        self.assertIn("api_version", caps)
        self.assertIsInstance(caps["api_version"], int)
        self.assertGreaterEqual(caps["api_version"], 1)

        # Must have pack info
        self.assertIn("pack", caps)
        self.assertIn("name", caps["pack"])
        self.assertIn("version", caps["pack"])

        # Must have features dict with booleans
        self.assertIn("features", caps)
        features = caps["features"]
        self.assertIsInstance(features, dict)

        # Required feature flags
        expected_features = [
            "webhook_submit",
            "logs_tail",
            "doctor",
            "job_monitor",
            "callback_delivery",
            "assist_automation_compose",
            "png_info",
        ]
        for feat in expected_features:
            self.assertIn(feat, features, f"Missing feature: {feat}")
            self.assertIsInstance(features[feat], bool)

    def test_api_version_is_positive(self):
        from services.capabilities import get_capabilities

        caps = get_capabilities()
        self.assertGreater(caps["api_version"], 0)


if __name__ == "__main__":
    unittest.main()
