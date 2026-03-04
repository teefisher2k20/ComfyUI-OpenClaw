import os
import ssl
import sys
import unittest
import urllib.request
from unittest.mock import MagicMock, call, patch

sys.path.append(os.getcwd())

from services.safe_io import _build_pinned_opener


class TestS70SSRFPinningRegression(unittest.TestCase):
    def _capture_connection_class(self, scheme: str, pinned_ips: list[str]):
        opener = _build_pinned_opener(pinned_ips)
        request = urllib.request.Request(f"{scheme}://example.com/resource")
        handler = next(
            h
            for h in opener.handlers
            if type(h).__name__
            == ("PinnedHTTPHandler" if scheme == "http" else "PinnedHTTPSHandler")
        )
        method = handler.http_open if scheme == "http" else handler.https_open
        captured = {}

        def _capture_do_open(_self, http_class, req, **kwargs):
            captured["http_class"] = http_class
            captured["kwargs"] = kwargs
            raise RuntimeError("captured")

        with patch.object(urllib.request.AbstractHTTPHandler, "do_open", _capture_do_open):
            with self.assertRaises(RuntimeError) as ctx:
                method(request)

        self.assertEqual(str(ctx.exception), "captured")
        return captured["http_class"], captured["kwargs"]

    def test_http_pinned_connect_uses_resolved_ip_order(self):
        http_class, _kwargs = self._capture_connection_class(
            "http", ["203.0.113.10", "203.0.113.11"]
        )
        conn = http_class("example.com", 80, timeout=5)
        final_sock = MagicMock(name="http_sock")

        with patch(
            "socket.create_connection",
            side_effect=[OSError("first ip failed"), final_sock],
        ) as mock_create:
            conn.connect()

        self.assertIs(conn.sock, final_sock)
        self.assertEqual(
            mock_create.call_args_list,
            [
                call(("203.0.113.10", 80), 5, None),
                call(("203.0.113.11", 80), 5, None),
            ],
        )
        self.assertNotIn(call(("example.com", 80), 5, None), mock_create.call_args_list)

    def test_http_pinned_connect_raises_last_socket_error(self):
        http_class, _kwargs = self._capture_connection_class(
            "http", ["203.0.113.10", "203.0.113.11"]
        )
        conn = http_class("example.com", 80, timeout=5)

        with patch(
            "socket.create_connection",
            side_effect=[OSError("ip1 refused"), OSError("ip2 refused")],
        ):
            with self.assertRaises(OSError) as ctx:
                conn.connect()

        self.assertEqual(str(ctx.exception), "ip2 refused")

    def test_https_pinned_connect_preserves_sni_and_ip_failover(self):
        https_class, _kwargs = self._capture_connection_class(
            "https", ["198.51.100.20", "198.51.100.21"]
        )
        context = ssl.create_default_context()
        conn = https_class("example.com", 443, timeout=5, context=context)
        raw_sock = MagicMock(name="raw_sock")
        wrapped_sock = MagicMock(name="wrapped_sock")

        with (
            patch(
                "socket.create_connection",
                side_effect=[OSError("first ip down"), raw_sock],
            ) as mock_create,
            patch.object(context, "wrap_socket", return_value=wrapped_sock) as mock_wrap,
        ):
            conn.connect()

        self.assertIs(conn.sock, wrapped_sock)
        self.assertEqual(
            mock_create.call_args_list,
            [
                call(("198.51.100.20", 443), 5, None),
                call(("198.51.100.21", 443), 5, None),
            ],
        )
        mock_wrap.assert_called_once_with(raw_sock, server_hostname="example.com")

    def test_https_tls_wrap_failure_retries_next_pinned_ip(self):
        https_class, _kwargs = self._capture_connection_class(
            "https", ["198.51.100.20", "198.51.100.21"]
        )
        context = ssl.create_default_context()
        conn = https_class("example.com", 443, timeout=5, context=context)
        raw_sock_1 = MagicMock(name="raw_sock_1")
        raw_sock_2 = MagicMock(name="raw_sock_2")
        wrapped_sock = MagicMock(name="wrapped_sock")

        with (
            patch(
                "socket.create_connection",
                side_effect=[raw_sock_1, raw_sock_2],
            ) as mock_create,
            patch.object(
                context,
                "wrap_socket",
                side_effect=[OSError("tls wrap failed"), wrapped_sock],
            ) as mock_wrap,
        ):
            conn.connect()

        self.assertIs(conn.sock, wrapped_sock)
        self.assertEqual(
            mock_create.call_args_list,
            [
                call(("198.51.100.20", 443), 5, None),
                call(("198.51.100.21", 443), 5, None),
            ],
        )
        self.assertEqual(
            mock_wrap.call_args_list,
            [
                call(raw_sock_1, server_hostname="example.com"),
                call(raw_sock_2, server_hostname="example.com"),
            ],
        )

    def test_https_pinned_connect_raises_last_tls_error(self):
        https_class, _kwargs = self._capture_connection_class(
            "https", ["198.51.100.20", "198.51.100.21"]
        )
        context = ssl.create_default_context()
        conn = https_class("example.com", 443, timeout=5, context=context)
        raw_sock_1 = MagicMock(name="raw_sock_1")
        raw_sock_2 = MagicMock(name="raw_sock_2")

        with (
            patch(
                "socket.create_connection",
                side_effect=[raw_sock_1, raw_sock_2],
            ),
            patch.object(
                context,
                "wrap_socket",
                side_effect=[OSError("tls failed 1"), OSError("tls failed 2")],
            ),
        ):
            with self.assertRaises(OSError) as ctx:
                conn.connect()

        self.assertEqual(str(ctx.exception), "tls failed 2")


if __name__ == "__main__":
    unittest.main()
