"""
WeChat Official Account Webhook Adapter (R74 + S31 + F43 + R82).

Implements:
- R74: GET verification handshake + POST XML normalization into CommandRequest.
- S31: Fail-closed ingress security — signature verification, replay/nonce
        dedup, XML parser budgets, allowlist enforcement via S32 primitives.
- F43: Adapter wiring into connector command router with text-first delivery.
- R82: AES encrypted ingress (encrypt_type=aes), expanded event normalization
       (unsubscribe, CLICK, VIEW, SCAN), 5s ack guard with deferred processing,
       no-MsgId dedupe keys.

Setup:
1. Configure a WeChat Official Account (subscription or service account).
2. Set env vars:
   - OPENCLAW_CONNECTOR_WECHAT_TOKEN          (verification token)
   - OPENCLAW_CONNECTOR_WECHAT_APP_ID         (AppID)
   - OPENCLAW_CONNECTOR_WECHAT_APP_SECRET     (AppSecret)
   - OPENCLAW_CONNECTOR_WECHAT_ENCODING_AES_KEY (R82: AES key, optional)
3. Configure webhook URL in WeChat MP admin:
   https://<public-host>/wechat/webhook
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import struct
import time
from typing import Optional
from xml.etree import ElementTree as ET

from defusedxml import ElementTree as DefusedET
from defusedxml.common import DefusedXmlException

from ..config import ConnectorConfig
from ..contract import CommandRequest, CommandResponse
from ..router import CommandRouter
from ..security_profile import AllowlistPolicy, ReplayGuard
from ..transport_contract import RelayResponseClassifier

logger = logging.getLogger(__name__)


def _import_aiohttp_web():
    """
    Import aiohttp + aiohttp.web lazily.

    Keeps unit tests runnable in environments where aiohttp isn't installed.
    """
    try:
        import aiohttp  # type: ignore
        from aiohttp import web  # type: ignore
    except ModuleNotFoundError:
        return None, None
    return aiohttp, web


class _CompatResponse:
    """Minimal response shim for unit tests when aiohttp is unavailable."""

    def __init__(
        self,
        *,
        status: int = 200,
        text: str = "",
        content_type: str = "text/plain",
        body: Optional[bytes] = None,
    ):
        self.status = status
        self.text = text
        self.content_type = content_type
        self.body = body if body is not None else text.encode("utf-8")


def _make_response(
    web,
    *,
    status: int = 200,
    text: str = "",
    content_type: str = "text/plain",
):
    if web is not None:
        return web.Response(status=status, text=text, content_type=content_type)
    return _CompatResponse(status=status, text=text, content_type=content_type)


# ---------------------------------------------------------------------------
# R74 — Protocol constants
# ---------------------------------------------------------------------------

# WeChat XML payload hard limits (S31 parser budgets)
XML_MAX_PAYLOAD_BYTES = 64 * 1024  # 64 KB
XML_MAX_DEPTH = 3  # WeChat XML is flat (<xml><Tag>value</Tag></xml>)
XML_MAX_FIELDS = 30  # More than enough for any WeChat event type
XML_MAX_FIELD_VALUE_LEN = 10_000  # Single field value cap

# WeChat Customer Service API (text-first delivery)
WECHAT_API_BASE = "https://api.weixin.qq.com/cgi-bin"

# Supported message/event types for command extraction
SUPPORTED_MSG_TYPES = {"text"}
# R82: expanded event type coverage
SUPPORTED_EVENT_TYPES = {"subscribe", "unsubscribe", "click", "view", "scan"}


# ---------------------------------------------------------------------------
# S31 — XML Runtime Security Gate
# ---------------------------------------------------------------------------


def _check_xml_security() -> None:
    """
    S31: Verify underlying XML parser security baseline.
    Must fail closed if Expat version is strictly < 2.4.1 (CVE-2021-45960).
    """
    try:
        import xml.parsers.expat

        ver_str = getattr(xml.parsers.expat, "EXPAT_VERSION", "")
        # Format can be "expat_2.4.1" or just "2.4.1"
        if ver_str.lower().startswith("expat_"):
            clean_ver = ver_str[6:]
        else:
            clean_ver = ver_str

        # Parse version tuple
        parts = [int(p) for p in clean_ver.split(".") if p.isdigit()]

        # Tuple comparison (major, minor, patch)
        # S31 Baseline: 2.4.1 (released 2022-01)
        if tuple(parts) < (2, 4, 1):
            raise RuntimeError(
                f"Unsafe Expat version {ver_str}. Upgrade Python/libexpat to >= 2.4.1."
            )
    except (ImportError, AttributeError, ValueError) as e:
        # Fail closed on any check failure
        raise RuntimeError(f"XML Security Gate Failed: {e}") from e


# ---------------------------------------------------------------------------
# S31 — WeChat signature verification
# ---------------------------------------------------------------------------


def verify_wechat_signature(
    token: str, timestamp: str, nonce: str, signature: str
) -> bool:
    """
    Verify WeChat webhook signature.

    WeChat signs with: sort([token, timestamp, nonce]) → join → SHA1.
    Returns True if valid, False otherwise.
    """
    check_list = sorted([token, timestamp, nonce])
    check_str = "".join(check_list)
    expected = hashlib.sha1(check_str.encode("utf-8")).hexdigest()
    return hmac.compare_digest(expected, signature.lower())


# ---------------------------------------------------------------------------
# R82 — Encrypted message signature verification
# ---------------------------------------------------------------------------


def verify_msg_signature(token: str, timestamp: str, nonce: str, encrypt: str) -> str:
    """
    Verify msg_signature for WeChat AES encrypted mode.

    WeChat signs with: sort([token, timestamp, nonce, encrypt]) → join → SHA1.
    Returns the expected signature string.
    """
    check_list = sorted([token, timestamp, nonce, encrypt])
    check_str = "".join(check_list)
    return hashlib.sha1(check_str.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# R82 — AES decrypt for encrypted mode
# ---------------------------------------------------------------------------


def decrypt_wechat_message(
    encoding_aes_key: str, app_id: str, ciphertext_b64: str
) -> bytes:
    """
    Decrypt WeChat AES-CBC-256 encrypted message.

    WeChat uses:
    - Key: base64decode(EncodingAESKey + "=") → 32 bytes
    - IV: first 16 bytes of key
    - Padding: PKCS#7
    - Plaintext format: random(16) + msg_len(4, network byte order) + msg + app_id

    Returns the decrypted XML message bytes.
    Raises ValueError on any failure (fail-closed).
    """
    try:
        from Cryptodome.Cipher import AES
    except ImportError:
        try:
            from Crypto.Cipher import AES
        except ImportError:
            raise ValueError(
                "R82: pycryptodomex or pycryptodome required for AES decrypt. "
                "Install with: pip install pycryptodomex"
            )

    # Derive key (32 bytes) and IV (first 16 bytes)
    key = base64.b64decode(encoding_aes_key + "=")
    if len(key) != 32:
        raise ValueError(f"R82: AES key must be 32 bytes, got {len(key)}")
    iv = key[:16]

    # Decrypt
    ciphertext = base64.b64decode(ciphertext_b64)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    plaintext = cipher.decrypt(ciphertext)

    # Remove PKCS#7 padding
    pad_len = plaintext[-1]
    if not (1 <= pad_len <= 32):
        raise ValueError("R82: Invalid PKCS#7 padding")
    plaintext = plaintext[:-pad_len]

    # Parse: random(16) + msg_len(4) + msg + app_id
    # Skip 16 bytes random prefix
    content = plaintext[16:]
    msg_len = struct.unpack("!I", content[:4])[0]
    msg = content[4 : 4 + msg_len]
    from_app_id = content[4 + msg_len :].decode("utf-8")

    if from_app_id != app_id:
        raise ValueError(
            f"R82: AppID mismatch in decrypted message: "
            f"expected={app_id}, got={from_app_id}"
        )

    return msg


# ---------------------------------------------------------------------------
# R74 — XML parsing with S31 budgets
# ---------------------------------------------------------------------------


class XMLBudgetExceeded(Exception):
    """Raised when XML payload exceeds parser budget."""


def parse_wechat_xml(raw: bytes) -> dict:
    """
    Parse WeChat XML payload with S31 parser budgets enforced.

    Budgets:
    - Payload size: XML_MAX_PAYLOAD_BYTES
    - Tree depth: XML_MAX_DEPTH
    - Field count: XML_MAX_FIELDS
    - Field value length: XML_MAX_FIELD_VALUE_LEN

    Returns flat dict of tag → text value.
    Raises XMLBudgetExceeded on any violation.
    """
    if len(raw) > XML_MAX_PAYLOAD_BYTES:
        raise XMLBudgetExceeded(
            f"Payload size {len(raw)} exceeds limit {XML_MAX_PAYLOAD_BYTES}"
        )

    lowered = raw.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        # IMPORTANT: reject DTD / ENTITY declarations before parser entry to
        # keep entity-expansion bombs fail-closed.
        raise XMLBudgetExceeded("DTD/entity declarations are not allowed")

    try:
        # IMPORTANT: keep defusedxml here. Reverting to the stdlib parser path
        # reopens the residual CodeQL xml-bomb finding on this ingress seam.
        root = DefusedET.fromstring(raw.decode("utf-8"))
    except (ET.ParseError, DefusedXmlException, UnicodeDecodeError) as e:
        raise XMLBudgetExceeded(f"XML parse error: {e}") from e

    # Depth check — WeChat envelopes are <xml><Tag>val</Tag></xml>, depth=2
    def _check_depth(el, depth=1):
        if depth > XML_MAX_DEPTH:
            raise XMLBudgetExceeded(f"XML depth {depth} exceeds limit {XML_MAX_DEPTH}")
        for child in el:
            _check_depth(child, depth + 1)

    _check_depth(root)

    result = {}
    field_count = 0
    for child in root:
        field_count += 1
        if field_count > XML_MAX_FIELDS:
            raise XMLBudgetExceeded(f"Field count exceeds limit {XML_MAX_FIELDS}")
        value = (child.text or "").strip()
        if len(value) > XML_MAX_FIELD_VALUE_LEN:
            raise XMLBudgetExceeded(
                f"Field '{child.tag}' value length {len(value)} exceeds limit"
            )
        result[child.tag] = value

    return result


# ---------------------------------------------------------------------------
# R74 — Canonical event mapping
# ---------------------------------------------------------------------------


def normalize_wechat_event(fields: dict) -> Optional[dict]:
    """
    Map WeChat XML fields to a canonical event dict.

    Returns dict with keys: msg_type, event_type, sender_id, text,
    message_id, timestamp, create_time, dedupe_key.
    Returns None for unsupported/empty events.

    R82: Expanded event coverage — unsubscribe, CLICK, VIEW, SCAN.
    R82: Generates dedupe_key for events without MsgId.
    """
    msg_type = fields.get("MsgType", "").lower()
    sender_id = fields.get("FromUserName", "")
    to_user = fields.get("ToUserName", "")
    create_time = fields.get("CreateTime", "0")

    if not sender_id:
        return None

    event = {
        "msg_type": msg_type,
        "event_type": fields.get("Event", "").lower(),
        "sender_id": sender_id,
        "to_user": to_user,
        "text": "",
        "message_id": fields.get("MsgId", ""),
        "timestamp": int(create_time) if create_time.isdigit() else 0,
        "create_time": create_time,
        "dedupe_key": "",  # R82: populated for no-MsgId events
    }

    if msg_type == "text":
        event["text"] = fields.get("Content", "").strip()
    elif msg_type == "event":
        sub_event = event["event_type"]
        event_key = fields.get("EventKey", "")

        if sub_event == "subscribe":
            if event_key:
                # QR code scan + subscribe (R82)
                event["text"] = f"/qr {event_key}"
            else:
                event["text"] = "/help"  # Map subscribe to help command
        elif sub_event == "unsubscribe":
            # R82: log-only, no text routing
            event["text"] = ""
            event["_log_only"] = True
        elif sub_event == "click":
            # R82: menu CLICK → route EventKey as command
            event["text"] = event_key if event_key else ""
        elif sub_event == "view":
            # R82: menu VIEW → log redirect URL, no routing
            event["text"] = ""
            event["_log_only"] = True
            event["_url"] = event_key
        elif sub_event == "scan":
            # R82: QR scan (already subscribed)
            event["text"] = f"/qr {event_key}" if event_key else ""
        else:
            return None  # Unsupported event

        # R82: No-MsgId dedupe key for events
        if not event["message_id"]:
            event["dedupe_key"] = f"{sender_id}:{create_time}:{sub_event}:{event_key}"
    else:
        return None  # Unsupported message type

    # Allow log-only events to pass through (R82)
    if event.get("_log_only"):
        return event

    if not event["text"]:
        return None

    return event


# ---------------------------------------------------------------------------
# R74 — XML reply builder
# ---------------------------------------------------------------------------


def build_text_reply_xml(to_user: str, from_user: str, content: str) -> str:
    """Build WeChat passive reply XML for text messages."""
    ts = str(int(time.time()))
    # XML-escape content
    content_escaped = (
        content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return (
        "<xml>"
        f"<ToUserName><![CDATA[{to_user}]]></ToUserName>"
        f"<FromUserName><![CDATA[{from_user}]]></FromUserName>"
        f"<CreateTime>{ts}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{content_escaped}]]></Content>"
        "</xml>"
    )


# ---------------------------------------------------------------------------
# F43 — WeChat Official Account Adapter
# ---------------------------------------------------------------------------


class WeChatWebhookServer:
    """
    WeChat Official Account webhook adapter.

    GET  /wechat/webhook  →  signature verification echostr handshake
    POST /wechat/webhook  →  XML message/event handling + security checks
    """

    REPLAY_WINDOW_SEC = 300
    NONCE_CACHE_SIZE = 1000

    def __init__(self, config: ConnectorConfig, router: CommandRouter):
        self.config = config
        self.router = router
        self.app = None
        self.runner = None
        self.site = None
        self.session = None

        # S31: replay guard for nonce dedup
        self._replay_guard = ReplayGuard(
            window_sec=self.REPLAY_WINDOW_SEC,
            max_entries=self.NONCE_CACHE_SIZE,
        )

        # S31: allowlist policy (soft-deny)
        self._user_allowlist = AllowlistPolicy(
            config.wechat_allowed_users, strict=False
        )
        self._session_invalid = False  # R93: Track session validity

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        """Start the webhook server."""
        # S31: Fail-closed XML security gate check
        _check_xml_security()

        aiohttp, web = _import_aiohttp_web()
        if aiohttp is None or web is None:
            logger.warning("aiohttp not installed. Skipping WeChat adapter.")
            return

        if not self.config.wechat_token:
            logger.warning("WeChat Token missing. Skipping WeChat adapter.")
            return

        logger.info(
            f"Starting WeChat Webhook on "
            f"{self.config.wechat_bind_host}:{self.config.wechat_bind_port}"
            f"{self.config.wechat_webhook_path}"
        )

        self.session = aiohttp.ClientSession()
        self._session_invalid = False  # Reset on start

        self.app = web.Application()
        self.app.router.add_get(self.config.wechat_webhook_path, self.handle_verify)
        self.app.router.add_post(self.config.wechat_webhook_path, self.handle_webhook)

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(
            self.runner, self.config.wechat_bind_host, self.config.wechat_bind_port
        )
        await self.site.start()

    async def stop(self):
        """Stop the server."""
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        if self.session:
            await self.session.close()

    # ------------------------------------------------------------------
    # GET — Verification Handshake (R74)
    # ------------------------------------------------------------------

    async def handle_verify(self, request):
        """
        GET verification handshake.

        WeChat sends: ?signature=<sig>&timestamp=<ts>&nonce=<n>&echostr=<echo>
        Must return echostr as plain text if signature is valid.
        """
        _, web = _import_aiohttp_web()
        # IMPORTANT:
        # CI unit tests invoke handler logic directly without aiohttp installed.
        # Do not hard-raise here; return compat responses so security logic remains testable.

        signature = request.query.get("signature", "")
        timestamp = request.query.get("timestamp", "")
        nonce = request.query.get("nonce", "")
        echostr = request.query.get("echostr", "")

        token = self.config.wechat_token or ""

        if verify_wechat_signature(token, timestamp, nonce, signature):
            logger.info("WeChat webhook verification succeeded")
            return _make_response(web, text=echostr, content_type="text/plain")

        logger.warning("WeChat webhook verification failed")
        return _make_response(web, status=403, text="Verification failed")

    # ------------------------------------------------------------------
    # POST — Inbound Messages (R74 + S31)
    # ------------------------------------------------------------------

    async def handle_webhook(self, request):
        """POST handler for WeChat XML messages/events (R74 + S31 + R82)."""
        _, web = _import_aiohttp_web()
        # IMPORTANT:
        # Keep handler behavior testable in environments without aiohttp.
        # Server startup still requires aiohttp, but direct handler unit tests should not crash.

        # S31: Signature verification
        signature = request.query.get("signature", "")
        timestamp = request.query.get("timestamp", "")
        nonce = request.query.get("nonce", "")
        token = self.config.wechat_token or ""

        if not verify_wechat_signature(token, timestamp, nonce, signature):
            logger.warning("Invalid WeChat POST signature")
            return _make_response(web, status=401, text="Invalid Signature")

        # S31: Replay protection — nonce dedup
        if nonce and not self._replay_guard.check_and_record(nonce):
            logger.warning(f"Replay rejected for WeChat nonce: {nonce}")
            return _make_response(web, status=403, text="Replay Rejected")

        # S31: Timestamp freshness
        try:
            ts_val = int(timestamp)
        except (ValueError, TypeError):
            ts_val = 0
        now = int(time.time())
        age_sec = now - ts_val
        if age_sec > self.REPLAY_WINDOW_SEC or age_sec < -60:
            logger.warning(f"Stale WeChat request: age={age_sec}s")
            return _make_response(web, status=403, text="Stale Request")

        # Read body
        body_bytes = await request.read()

        # R82: Detect encrypted mode
        encrypt_type = request.query.get("encrypt_type", "")
        if encrypt_type == "aes":
            encoding_aes_key = self.config.wechat_encoding_aes_key
            app_id = self.config.wechat_app_id
            if not encoding_aes_key or not app_id:
                logger.error(
                    "R82: AES mode requested but encoding_aes_key/app_id missing"
                )
                return _make_response(web, status=500, text="AES config missing")

            try:
                # Parse outer XML to get <Encrypt>
                outer_fields = parse_wechat_xml(body_bytes)
                encrypted_content = outer_fields.get("Encrypt", "")
                if not encrypted_content:
                    logger.warning("R82: encrypt_type=aes but no <Encrypt> in body")
                    return _make_response(web, status=400, text="Bad Request")

                # R82: Verify msg_signature (fail-closed)
                msg_signature = request.query.get("msg_signature", "")
                expected_sig = verify_msg_signature(
                    token, timestamp, nonce, encrypted_content
                )
                if not hmac.compare_digest(expected_sig, msg_signature):
                    logger.warning("R82: msg_signature verification failed")
                    return _make_response(web, status=401, text="Invalid msg_signature")

                # R82: Decrypt
                body_bytes = decrypt_wechat_message(
                    encoding_aes_key, app_id, encrypted_content
                )
            except XMLBudgetExceeded as e:
                logger.warning(f"R82: Outer XML budget exceeded: {e}")
                return _make_response(web, status=400, text="Bad Request")
            except ValueError as e:
                logger.warning(f"R82: Decrypt failed (fail-closed): {e}")
                return _make_response(web, status=400, text="Decrypt Failed")

        # Parse XML with S31 budgets
        try:
            fields = parse_wechat_xml(body_bytes)
        except XMLBudgetExceeded as e:
            logger.warning(f"WeChat XML budget exceeded: {e}")
            return _make_response(web, status=400, text="Bad Request")

        # R74: Normalize to canonical event
        event = normalize_wechat_event(fields)
        if event is None:
            # Unsupported message type — return empty success to WeChat
            return _make_response(web, text="success", content_type="text/plain")

        # R82: Log-only events (unsubscribe, VIEW) — ack without routing
        if event.get("_log_only"):
            sub_event = event.get("event_type", "")
            sender_id = event["sender_id"]
            url_info = f" url={event.get('_url', '')}" if event.get("_url") else ""
            logger.info(f"R82: Log-only event={sub_event} from={sender_id}{url_info}")
            return _make_response(web, text="success", content_type="text/plain")

        # S31: Allowlist check (soft-deny)
        sender_id = event["sender_id"]
        user_result = self._user_allowlist.evaluate(sender_id)
        is_allowed = user_result.decision == "allow"

        if not is_allowed:
            msg_info = f"Untrusted WeChat message from user={sender_id}."
            if not self.config.wechat_allowed_users:
                msg_info += " (Allow list empty; all users will require approval)"
            else:
                msg_info += " (Not in allowlist; approval required)"
            logger.warning(msg_info)

        # F43: Dedup — MsgId-based or R82 dedupe_key
        message_id = event.get("message_id", "")
        dedupe_key = event.get("dedupe_key", "")
        dedup_value = f"msg:{message_id}" if message_id else f"evt:{dedupe_key}"
        if dedup_value and dedup_value not in ("msg:", "evt:"):
            if not self._replay_guard.check_and_record(dedup_value):
                logger.debug(f"Duplicate WeChat message/event: {dedup_value}")
                return _make_response(web, text="success", content_type="text/plain")

        req = CommandRequest(
            platform="wechat",
            sender_id=str(sender_id),
            channel_id=str(sender_id),  # WeChat OA is 1:1
            username=sender_id,  # OpenID, no profile info in XML
            message_id=message_id,
            text=event["text"],
            timestamp=float(event["timestamp"]),
        )

        # R82: 5s ack discipline — try fast reply, defer if slow
        try:
            resp = await asyncio.wait_for(self.router.handle(req), timeout=4.5)
            if resp.text:
                # Passive reply (within 5-second window)
                to_user = event["sender_id"]
                from_user = event["to_user"]
                reply_xml = build_text_reply_xml(to_user, from_user, resp.text)
                return _make_response(
                    web,
                    text=reply_xml,
                    content_type="application/xml",
                )
        except asyncio.TimeoutError:
            # R82: Defer processing — ack immediately, send result via Customer Service API
            logger.info(f"R82: 5s ack timeout, deferring response for {sender_id}")
            asyncio.create_task(self._deferred_reply(req, event))
        except Exception as e:
            logger.exception(f"Error handling WeChat command: {e}")

        return _make_response(web, text="success", content_type="text/plain")

    async def _deferred_reply(self, req: CommandRequest, event: dict):
        """R82: Handle deferred processing after 5s ack window."""
        try:
            resp = await self.router.handle(req)
            if resp.text:
                await self.send_message(event["sender_id"], resp.text)
        except Exception as e:
            logger.exception(f"R82: Deferred reply error: {e}")

    # ------------------------------------------------------------------
    # Outbound: Text (Customer Service Message API)
    # ------------------------------------------------------------------

    async def send_message(
        self,
        recipient_openid: str,
        text: str,
        delivery_context: Optional[dict] = None,
    ):
        """
        Send text via WeChat Customer Service Message API.

        Requires service account with customer service permission.
        Falls back silently if access_token unavailable.
        """
        if self._session_invalid:
            logger.warning("R93: Connector session invalid - blocking outbound")
            return

        if not self.session:
            return

        access_token = await self._get_access_token()
        if not access_token:
            logger.warning("WeChat send_message: no access_token available")
            return

        url = f"{WECHAT_API_BASE}/message/custom/send?access_token={access_token}"

        # WeChat text limit
        if len(text) > 2048:
            text = text[:2045] + "..."

        body = {
            "touser": recipient_openid,
            "msgtype": "text",
            "text": {"content": text},
        }

        try:
            async with self.session.post(url, json=body) as resp:
                if RelayResponseClassifier.is_auth_invalid(resp.status):
                    self._session_invalid = True
                    logger.error(
                        f"R93: Auth Invalid (WeChat {resp.status}) - Locking session"
                    )
                    return

                data = await resp.json(content_type=None)
                errcode = data.get("errcode", 0)
                if errcode != 0:
                    logger.error(
                        f"WeChat send_message failed: errcode={errcode} "
                        f"errmsg={data.get('errmsg')}"
                    )
        except Exception as e:
            logger.error(f"WeChat send_message error: {e}")

    async def send_image(
        self,
        channel_id: str,
        image_data: bytes,
        filename: str = "image.png",
        caption: Optional[str] = None,
        delivery_context: Optional[dict] = None,
    ):
        """
        Send image via WeChat.

        Text-first: sends caption/notification text. Actual media upload
        requires media API and is not implemented in phase 1.
        """
        if self._session_invalid:
            logger.warning("R93: Connector session invalid - blocking outbound image")
            return

        if caption:
            await self.send_message(channel_id, caption)
        else:
            await self.send_message(
                channel_id,
                "[OpenClaw] Image generated. Media delivery not yet supported for WeChat.",
            )

    # ------------------------------------------------------------------
    # Access Token Management
    # ------------------------------------------------------------------

    _cached_token: Optional[str] = None
    _token_expires: float = 0.0

    async def _get_access_token(self) -> Optional[str]:
        """
        Get WeChat API access_token with simple caching.

        Token is valid for ~7200 seconds. We refresh at 90% lifetime.
        """
        now = time.time()
        if self._cached_token and now < self._token_expires:
            return self._cached_token

        # R93: Block if session invalid
        if self._session_invalid:
            return None

        app_id = self.config.wechat_app_id
        app_secret = self.config.wechat_app_secret
        if not app_id or not app_secret:
            return None

        url = (
            f"{WECHAT_API_BASE}/token"
            f"?grant_type=client_credential"
            f"&appid={app_id}"
            f"&secret={app_secret}"
        )

        try:
            if not self.session:
                return None
            async with self.session.get(url) as resp:
                if RelayResponseClassifier.is_auth_invalid(resp.status):
                    self._session_invalid = True
                    logger.error(
                        f"R93: Auth Invalid (WeChat Token {resp.status}) - Locking session"
                    )
                    return None

                data = await resp.json(content_type=None)
                token = data.get("access_token")
                expires_in = data.get("expires_in", 7200)
                if token:
                    self._cached_token = token
                    # Refresh at 90% of lifetime
                    self._token_expires = now + (expires_in * 0.9)
                    return token
                else:
                    logger.error(
                        f"WeChat access_token fetch failed: {data.get('errmsg')}"
                    )
                    return None
        except Exception as e:
            logger.error(f"WeChat access_token error: {e}")
            return None
