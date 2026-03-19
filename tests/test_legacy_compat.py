import unittest
from unittest.mock import MagicMock, patch

from services.legacy_compat import (
    ADMIN_TOKEN_HEADERS,
    OPENCLAW_API_PREFIX,
    LEGACY_API_PREFIX,
    get_api_path_candidates,
    get_header_alias_value,
)


class TestLegacyCompat(unittest.TestCase):
    def test_get_header_alias_value_prefers_primary(self):
        logger = MagicMock()
        value, used_legacy = get_header_alias_value(
            {
                ADMIN_TOKEN_HEADERS.primary: "new-token",
                ADMIN_TOKEN_HEADERS.legacy: "old-token",
            },
            ADMIN_TOKEN_HEADERS,
            logger=logger,
        )

        self.assertEqual(value, "new-token")
        self.assertFalse(used_legacy)
        logger.warning.assert_not_called()

    def test_get_header_alias_value_uses_legacy_and_logs(self):
        logger = MagicMock()
        with patch("services.legacy_compat._increment_legacy_api_hits") as inc:
            value, used_legacy = get_header_alias_value(
                {ADMIN_TOKEN_HEADERS.legacy: "old-token"},
                ADMIN_TOKEN_HEADERS,
                logger=logger,
            )

        self.assertEqual(value, "old-token")
        self.assertTrue(used_legacy)
        inc.assert_called_once()
        logger.warning.assert_called_once()

    def test_get_api_path_candidates_handles_canonical_and_legacy_prefixes(self):
        self.assertEqual(
            get_api_path_candidates(f"{OPENCLAW_API_PREFIX}/health"),
            (
                f"{OPENCLAW_API_PREFIX}/health",
                f"{LEGACY_API_PREFIX}/health",
            ),
        )
        self.assertEqual(
            get_api_path_candidates(f"{LEGACY_API_PREFIX}/health"),
            (
                f"{LEGACY_API_PREFIX}/health",
                f"{OPENCLAW_API_PREFIX}/health",
            ),
        )
        self.assertEqual(get_api_path_candidates("/history/abc"), ("/history/abc",))


if __name__ == "__main__":
    unittest.main()
