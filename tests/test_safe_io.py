import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

sys.path.append(os.getcwd())

from services.safe_io import (
    PathTraversalError,
    SafeIOHTTPError,
    SSRFError,
    _normalize_host,
    is_private_ip,
    resolve_under_root,
    safe_read_bytes,
    safe_read_text,
    safe_request_json,
    safe_request_text_stream,
    safe_write_text,
    validate_outbound_url,
)


class TestPathSafety(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "subdir"), exist_ok=True)
        with open(os.path.join(self.root, "test.txt"), "w") as f:
            f.write("test content")
        with open(os.path.join(self.root, "subdir", "nested.txt"), "w") as f:
            f.write("nested content")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_resolve_valid_path(self):
        """Test resolving a valid relative path."""
        result = resolve_under_root(self.root, "test.txt")
        self.assertEqual(result, os.path.join(self.root, "test.txt"))

    def test_resolve_nested_path(self):
        """Test resolving a nested path."""
        result = resolve_under_root(self.root, "subdir/nested.txt")
        self.assertTrue(result.endswith("nested.txt"))

    def test_reject_absolute_path(self):
        """Test that absolute paths are rejected."""
        with self.assertRaises(PathTraversalError):
            resolve_under_root(self.root, "/etc/passwd")

    def test_reject_traversal_basic(self):
        """Test that basic traversal is rejected."""
        with self.assertRaises(PathTraversalError):
            resolve_under_root(self.root, "../../../etc/passwd")

    def test_reject_traversal_mixed(self):
        """Test traversal with valid prefix."""
        with self.assertRaises(PathTraversalError):
            resolve_under_root(self.root, "subdir/../../etc/passwd")

    def test_safe_read_text(self):
        """Test safe reading of a file."""
        content = safe_read_text(self.root, "test.txt")
        self.assertEqual(content, "test content")

    def test_safe_read_bytes(self):
        """Test safe reading of a file as bytes."""
        content = safe_read_bytes(self.root, "test.txt")
        self.assertEqual(content, b"test content")

    def test_safe_read_bytes_capped(self):
        """Test that max_bytes actually caps bytes, not chars."""
        content = safe_read_bytes(self.root, "test.txt", max_bytes=4)
        self.assertEqual(len(content), 4)
        self.assertEqual(content, b"test")

    def test_safe_read_traversal_blocked(self):
        """Test that read blocks traversal."""
        with self.assertRaises(PathTraversalError):
            safe_read_text(self.root, "../../../etc/passwd")

    def test_safe_write_text(self):
        """Test safe writing of a file."""
        safe_write_text(self.root, "new_file.txt", "new content")
        content = safe_read_text(self.root, "new_file.txt")
        self.assertEqual(content, "new content")

    def test_safe_write_traversal_blocked(self):
        """Test that write blocks traversal."""
        with self.assertRaises(PathTraversalError):
            safe_write_text(self.root, "../escape.txt", "malicious")

    def test_reject_windows_drive_relative(self):
        """Test that Windows drive-relative paths (C:foo) are rejected."""
        with self.assertRaises(PathTraversalError):
            resolve_under_root(self.root, "C:foo")
        with self.assertRaises(PathTraversalError):
            resolve_under_root(self.root, "D:bar\\baz")

    @unittest.skipUnless(hasattr(os, "symlink"), "Symlinks not supported")
    def test_symlink_escape_blocked(self):
        """Test that symlinks pointing outside root are blocked."""
        # Create a symlink inside root pointing outside
        external_file = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
        external_file.write(b"secret data")
        external_file.close()

        symlink_path = os.path.join(self.root, "escape_link")
        try:
            os.symlink(external_file.name, symlink_path)
            # Attempting to read through symlink should fail (realpath resolves outside root)
            with self.assertRaises(PathTraversalError):
                safe_read_text(self.root, "escape_link")
        except OSError:
            # Symlink creation may fail on Windows without admin privileges
            self.skipTest("Symlink creation requires elevated privileges")
        finally:
            os.unlink(external_file.name)
            if os.path.exists(symlink_path):
                os.unlink(symlink_path)


class TestURLSafety(unittest.TestCase):

    def test_reject_no_allowlist(self):
        """Test that URLs are rejected when no allowlist is provided."""
        with self.assertRaises(SSRFError) as ctx:
            validate_outbound_url("https://example.com")
        self.assertIn("denied by default", str(ctx.exception))

    @patch("socket.getaddrinfo")
    def test_reject_not_in_allowlist(self, mock_dns):
        """Test that URLs not in allowlist are rejected."""
        with self.assertRaises(SSRFError):
            validate_outbound_url("https://evil.com", allow_hosts={"example.com"})

    def test_reject_non_http_scheme(self):
        """Test that non-HTTP schemes are rejected."""
        with self.assertRaises(SSRFError):
            validate_outbound_url("file:///etc/passwd", allow_hosts={"localhost"})

    def test_reject_credentials(self):
        """Test that credentials in URL are rejected."""
        with self.assertRaises(SSRFError):
            validate_outbound_url(
                "https://user:pass@example.com", allow_hosts={"example.com"}
            )

    def test_private_ip_detection(self):
        """Test private IP detection."""
        self.assertTrue(is_private_ip("127.0.0.1"))
        self.assertTrue(is_private_ip("10.0.0.1"))
        self.assertTrue(is_private_ip("192.168.1.1"))
        self.assertTrue(is_private_ip("172.16.0.1"))
        self.assertTrue(is_private_ip("::1"))

    def test_public_ip_allowed(self):
        """Test that public IPs are not blocked."""
        self.assertFalse(is_private_ip("8.8.8.8"))
        self.assertFalse(is_private_ip("1.1.1.1"))

    @patch("socket.getaddrinfo")
    def test_validate_with_mocked_dns(self, mock_dns):
        """Test URL validation with mocked DNS (deterministic)."""
        # Mock DNS to return a public IP
        mock_dns.return_value = [(2, 1, 6, "", ("93.184.216.34", 443))]

        result = validate_outbound_url(
            "https://example.com", allow_hosts={"example.com"}
        )
        self.assertEqual(result, ("https", "example.com", 443, ["93.184.216.34"]))

    @patch("socket.getaddrinfo")
    def test_reject_private_ip_from_dns(self, mock_dns):
        """Test that private IPs from DNS are blocked."""
        mock_dns.return_value = [(2, 1, 6, "", ("127.0.0.1", 443))]

        with self.assertRaises(SSRFError) as ctx:
            validate_outbound_url("https://example.com", allow_hosts={"example.com"})
        self.assertIn("Private/reserved IP", str(ctx.exception))

    @patch("socket.getaddrinfo")
    def test_allow_loopback_private_ip_with_explicit_host_gate(self, mock_dns):
        """Loopback may be allowed only with explicit allow_loopback_hosts host gate."""
        mock_dns.return_value = [(2, 1, 6, "", ("127.0.0.1", 443))]

        result = validate_outbound_url(
            "https://localhost",
            allow_hosts={"localhost"},
            allow_loopback_hosts={"localhost"},
        )
        self.assertEqual(result, ("https", "localhost", 443, ["127.0.0.1"]))

    @patch("socket.getaddrinfo")
    def test_loopback_allowlist_does_not_allow_other_private_ranges(self, mock_dns):
        """Loopback exception must not allow non-loopback private IPs."""
        mock_dns.return_value = [(2, 1, 6, "", ("192.168.1.9", 443))]

        with self.assertRaises(SSRFError) as ctx:
            validate_outbound_url(
                "https://localhost",
                allow_hosts={"localhost"},
                allow_loopback_hosts={"localhost"},
            )
        self.assertIn("Private/reserved IP", str(ctx.exception))

    @patch("services.safe_io._build_pinned_opener")
    @patch("services.safe_io.validate_outbound_url")
    def test_safe_request_json_get_without_body(self, mock_validate, mock_build):
        """GET requests should work when json_body is omitted/None."""
        mock_validate.return_value = ("https", "example.com", 443, ["93.184.216.34"])

        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.read.return_value = b'{"ok": true}'

        mock_opener = MagicMock()
        mock_opener.open.return_value.__enter__.return_value = mock_response
        mock_build.return_value = mock_opener

        out = safe_request_json(
            method="GET",
            url="https://example.com/models",
            json_body=None,
            allow_hosts={"example.com"},
        )
        self.assertEqual(out["ok"], True)

    @patch("services.safe_io._build_pinned_opener")
    @patch("services.safe_io.validate_outbound_url")
    def test_safe_request_json_http_error_preserves_retry_headers_and_body(
        self, mock_validate, mock_build
    ):
        """HTTP errors should surface structured metadata for provider retry logic."""
        mock_validate.return_value = ("https", "example.com", 443, ["93.184.216.34"])

        mock_opener = MagicMock()
        mock_opener.open.side_effect = urllib.error.HTTPError(
            url="https://example.com/fail",
            code=429,
            msg="Too Many Requests",
            hdrs={"Retry-After": "17", "Content-Type": "application/json"},
            fp=BytesIO(b'{"error":{"retry_after":11}}'),
        )
        mock_build.return_value = mock_opener

        with self.assertRaises(SafeIOHTTPError) as ctx:
            safe_request_json(
                method="POST",
                url="https://example.com/fail",
                json_body={"x": 1},
                allow_hosts={"example.com"},
            )

        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(ctx.exception.headers.get("Retry-After"), "17")
        self.assertIn("retry_after", ctx.exception.body or "")

    @patch("services.safe_io._build_pinned_opener")
    @patch("services.safe_io.validate_outbound_url")
    def test_safe_request_json_accept_header_is_allowed(
        self, mock_validate, mock_build
    ):
        """JSON request path should allow Accept header (parity with stream path)."""
        mock_validate.return_value = ("https", "example.com", 443, ["93.184.216.34"])

        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.read.return_value = b'{"ok": true}'

        mock_opener = MagicMock()
        mock_opener.open.return_value.__enter__.return_value = mock_response
        mock_build.return_value = mock_opener

        out = safe_request_json(
            method="POST",
            url="https://example.com/accept",
            json_body={"x": 1},
            headers={
                "Accept": "application/json",
                "X-Test": "ok",
                "Bad-Header": "blocked",
            },
            allow_hosts={"example.com"},
        )

        self.assertEqual(out["ok"], True)
        request_arg = mock_opener.open.call_args.args[0]
        header_map = {k.lower(): v for k, v in request_arg.header_items()}
        self.assertEqual(header_map.get("accept"), "application/json")
        self.assertEqual(header_map.get("x-test"), "ok")
        self.assertNotIn("bad-header", header_map)

    @patch("services.safe_io._build_pinned_opener")
    @patch("services.safe_io.validate_outbound_url")
    def test_safe_request_json_supports_form_encoded_raw_body(
        self, mock_validate, mock_build
    ):
        """Non-JSON callers should still use safe_io without direct client sessions."""
        mock_validate.return_value = ("https", "slack.com", 443, ["93.184.216.34"])

        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.read.return_value = b'{"ok": true}'

        mock_opener = MagicMock()
        mock_opener.open.return_value.__enter__.return_value = mock_response
        mock_build.return_value = mock_opener

        out = safe_request_json(
            method="POST",
            url="https://slack.com/api/oauth.v2.access",
            raw_body=b"code=test&client_id=abc",
            content_type="application/x-www-form-urlencoded",
            headers={"Accept": "application/json"},
            allow_hosts={"slack.com"},
        )

        self.assertEqual(out["ok"], True)
        request_arg = mock_opener.open.call_args.args[0]
        self.assertEqual(request_arg.data, b"code=test&client_id=abc")
        header_map = {k.lower(): v for k, v in request_arg.header_items()}
        self.assertEqual(
            header_map.get("content-type"), "application/x-www-form-urlencoded"
        )
        self.assertEqual(header_map.get("accept"), "application/json")

    @patch("services.safe_io._build_pinned_opener")
    @patch("services.safe_io.validate_outbound_url")
    def test_safe_request_text_stream_accept_header_is_allowed(
        self, mock_validate, mock_build
    ):
        """Stream request path should share same allowed-header contract."""
        mock_validate.return_value = ("https", "example.com", 443, ["93.184.216.34"])

        class _FakeStreamResponse:
            def __init__(self):
                self.headers = {}
                self._lines = [b"data: one\n", b""]

            def getcode(self):
                return 200

            def readline(self, _max_bytes):
                return self._lines.pop(0)

            def close(self):
                return None

        fake_response = _FakeStreamResponse()
        mock_opener = MagicMock()
        mock_opener.open.return_value = fake_response
        mock_build.return_value = mock_opener

        lines = list(
            safe_request_text_stream(
                method="POST",
                url="https://example.com/stream",
                json_body={"x": 1},
                headers={
                    "Accept": "text/event-stream",
                    "X-Test": "ok",
                    "Bad-Header": "blocked",
                },
                allow_hosts={"example.com"},
            )
        )

        self.assertEqual(lines, ["data: one\n"])
        request_arg = mock_opener.open.call_args.args[0]
        header_map = {k.lower(): v for k, v in request_arg.header_items()}
        self.assertEqual(header_map.get("accept"), "text/event-stream")
        self.assertEqual(header_map.get("x-test"), "ok")
        self.assertNotIn("bad-header", header_map)

    def test_host_normalization_case(self):
        """Test host normalization is case-insensitive."""
        self.assertEqual(_normalize_host("Example.COM"), "example.com")

    def test_host_normalization_trailing_dot(self):
        """Test host normalization strips trailing dot."""
        self.assertEqual(_normalize_host("example.com."), "example.com")

    @patch("socket.getaddrinfo")
    def test_allowlist_case_insensitive(self, mock_dns):
        """Test that allowlist matching is case-insensitive."""
        mock_dns.return_value = [(2, 1, 6, "", ("93.184.216.34", 443))]

        # Should match despite case difference
        result = validate_outbound_url(
            "https://EXAMPLE.COM", allow_hosts={"example.com"}
        )
        self.assertEqual(result[1], "example.com")


if __name__ == "__main__":
    unittest.main()
