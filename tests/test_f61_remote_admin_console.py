import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import api.remote_admin as remote_admin


class TestF61RemoteAdminConsole(unittest.IsolatedAsyncioTestCase):
    async def test_remote_admin_page_handler_serves_html(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            html_path = Path(tmpdir) / "admin_console.html"
            html_path.write_text("<html><body>ok</body></html>", encoding="utf-8")

            request = MagicMock()
            with patch(
                "api.remote_admin._admin_console_html_path", return_value=html_path
            ):
                resp = await remote_admin.remote_admin_page_handler(request)

            self.assertEqual(resp.status, 200)
            self.assertIn("text/html", resp.content_type)
            self.assertIn("ok", resp.text)

    async def test_remote_admin_page_handler_missing_file(self):
        request = MagicMock()
        with patch(
            "api.remote_admin._admin_console_html_path",
            return_value=Path("__missing_admin_console__.html"),
        ):
            resp = await remote_admin.remote_admin_page_handler(request)

        self.assertEqual(resp.status, 500)
        self.assertIn("remote_admin_console_not_found", resp.text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
