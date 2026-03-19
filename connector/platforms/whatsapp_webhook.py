"""
WhatsApp Cloud API Platform Adapter (F36).
Receives webhooks from WhatsApp Cloud API, verifies signature, and routes commands.

Security: uses shared S32 primitives (verify_hmac_signature, ReplayGuard,
AllowlistPolicy) instead of inline implementations.

Setup:
1. Create a Meta App → WhatsApp → Add a phone number.
2. Set env vars: OPENCLAW_CONNECTOR_WHATSAPP_ACCESS_TOKEN, VERIFY_TOKEN, PHONE_NUMBER_ID.
3. Configure webhook URL: https://<public>/whatsapp/webhook
"""

import json
import logging
import time
from typing import Optional

from ..config import ConnectorConfig
from ..contract import CommandRequest, CommandResponse
from ..router import CommandRouter
from ..security_profile import AllowlistPolicy, ReplayGuard, verify_hmac_signature
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


# Graph API version (can be overridden via env)
GRAPH_API_VERSION = "v19.0"
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


class WhatsAppWebhookServer:
    """
    WhatsApp Cloud API webhook adapter.

    GET  /whatsapp/webhook  →  hub.challenge verification
    POST /whatsapp/webhook  →  message handling + signature check
    """

    # Replay protection config — shared via S32 ReplayGuard
    REPLAY_WINDOW_SEC = 300
    NONCE_CACHE_SIZE = 1000

    def __init__(self, config: ConnectorConfig, router: CommandRouter):
        self.config = config
        self.router = router
        self.app = None
        self.runner = None
        self.site = None
        self.session = None
        self._session_invalid = False  # R93: Track session validity

        # S32: shared replay guard (replaces inline F32 nonce cache)
        self._replay_guard = ReplayGuard(
            window_sec=self.REPLAY_WINDOW_SEC,
            max_entries=self.NONCE_CACHE_SIZE,
        )

        # S32: shared allowlist policy (soft-deny: strict=False)
        self._user_allowlist = AllowlistPolicy(
            config.whatsapp_allowed_users, strict=False
        )

        # F33 Media Store
        from ..media_store import MediaStore

        self.media_store = MediaStore(config)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        """Start the webhook server."""
        aiohttp, web = _import_aiohttp_web()
        if aiohttp is None or web is None:
            logger.warning("aiohttp not installed. Skipping WhatsApp adapter.")
            return

        if not self.config.whatsapp_access_token:
            logger.warning("WhatsApp Access Token missing. Skipping WhatsApp adapter.")
            return

        if not self.config.whatsapp_verify_token:
            logger.warning("WhatsApp Verify Token missing. Skipping WhatsApp adapter.")
            return

        logger.info(
            f"Starting WhatsApp Webhook on "
            f"{self.config.whatsapp_bind_host}:{self.config.whatsapp_bind_port}"
            f"{self.config.whatsapp_webhook_path}"
        )
        self.session = aiohttp.ClientSession()
        self._session_invalid = False  # Reset on start

        self.app = web.Application()
        self.app.router.add_get(self.config.whatsapp_webhook_path, self.handle_verify)
        self.app.router.add_post(self.config.whatsapp_webhook_path, self.handle_webhook)
        # F33 Media Route
        media_route = f"{self.config.media_path}/{{token}}"
        self.app.router.add_get(media_route, self._handle_media_request)

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(
            self.runner, self.config.whatsapp_bind_host, self.config.whatsapp_bind_port
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
    # Webhook Handlers
    # ------------------------------------------------------------------

    async def handle_verify(self, request):
        """
        GET webhook verification (Meta hub.challenge handshake).

        Meta sends:
          ?hub.mode=subscribe&hub.verify_token=<token>&hub.challenge=<challenge>
        """
        _, web = _import_aiohttp_web()
        if web is None:
            raise RuntimeError("aiohttp not available")

        mode = request.query.get("hub.mode")
        token = request.query.get("hub.verify_token")
        challenge = request.query.get("hub.challenge")

        if mode == "subscribe" and token == self.config.whatsapp_verify_token:
            logger.info("WhatsApp webhook verified successfully")
            return web.Response(text=challenge or "", content_type="text/plain")

        logger.warning(f"WhatsApp verification failed: mode={mode}")
        return web.Response(status=403, text="Verification failed")

    async def handle_webhook(self, request):
        """POST webhook handler for inbound messages."""
        _, web = _import_aiohttp_web()
        if web is None:
            raise RuntimeError("aiohttp not available")

        body_bytes = await request.read()

        # Signature verification — S32 shared verifier (hex digest)
        if self.config.whatsapp_app_secret:
            signature = request.headers.get("X-Hub-Signature-256", "")
            auth_result = verify_hmac_signature(
                body_bytes,
                signature_header=signature,
                secret=self.config.whatsapp_app_secret,
                algorithm="sha256",
                digest_encoding="hex",
            )
            if not auth_result.ok:
                logger.warning(
                    f"Invalid WhatsApp webhook signature: {auth_result.error}"
                )
                return web.Response(status=401, text="Invalid Signature")

        try:
            payload = json.loads(body_bytes.decode("utf-8"))
        except json.JSONDecodeError:
            return web.Response(status=400, text="Bad JSON")

        # WhatsApp Cloud API payload structure:
        # { "object": "whatsapp_business_account", "entry": [...] }
        if payload.get("object") != "whatsapp_business_account":
            return web.Response(status=200, text="OK")  # Ignore non-WA events

        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                if change.get("field") != "messages":
                    continue

                messages = value.get("messages", [])
                contacts = value.get("contacts", [])
                metadata = value.get("metadata", {})

                for msg in messages:
                    await self._process_message(msg, contacts, metadata)

        return web.Response(status=200, text="OK")

    # ------------------------------------------------------------------
    # Media
    # ------------------------------------------------------------------

    async def _handle_media_request(self, request):
        """Serve media files for verified tokens."""
        _, web = _import_aiohttp_web()
        token = request.match_info.get("token")
        path = self.media_store.get_image_path(token)

        if not path:
            return web.Response(status=404, text="Media Not Found or Expired")

        return web.FileResponse(path)

    # ------------------------------------------------------------------
    # Message Processing
    # ------------------------------------------------------------------

    async def _process_message(self, msg: dict, contacts: list, metadata: dict):
        """Convert WhatsApp message to CommandRequest and route."""
        msg_type = msg.get("type")
        if msg_type != "text":
            # Only handle text messages in this phase
            logger.debug(f"Ignoring WhatsApp message type: {msg_type}")
            return

        sender_id = msg.get("from", "")
        message_id = msg.get("id", "")
        timestamp = int(msg.get("timestamp", 0))
        text = msg.get("text", {}).get("body", "")
        phone_number_id = metadata.get("phone_number_id", "")

        if not text or not sender_id:
            return

        # Replay protection — S32 ReplayGuard (replaces inline F32 nonce cache)
        now = time.time()
        age_sec = now - timestamp
        if age_sec > self.REPLAY_WINDOW_SEC or age_sec < -60:
            logger.debug(f"Stale or future WhatsApp message: age={age_sec:.1f}s")
            logger.warning(f"Replay rejected for WhatsApp message {message_id}")
            return

        if not self._replay_guard.check_and_record(message_id):
            logger.warning(f"Replay rejected for WhatsApp message {message_id}")
            return

        # Resolve contact name
        username = "whatsapp_user"
        for contact in contacts:
            if contact.get("wa_id") == sender_id:
                profile = contact.get("profile", {})
                username = profile.get("name", username)
                break

        # Security: Allowlist — S32 AllowlistPolicy (soft-deny: strict=False)
        user_result = self._user_allowlist.evaluate(sender_id)
        is_allowed = user_result.decision == "allow"

        if not is_allowed:
            msg_info = f"Untrusted WhatsApp message from user={sender_id}."
            if not self.config.whatsapp_allowed_users:
                msg_info += " (Allow list empty; all users will require approval)"
            else:
                msg_info += " (Not in allowlist; approval required)"
            logger.warning(msg_info)

        req = CommandRequest(
            platform="whatsapp",
            sender_id=str(sender_id),
            channel_id=str(sender_id),  # WhatsApp DMs use phone number
            username=username,
            message_id=message_id,
            text=text,
            timestamp=float(timestamp),
        )

        try:
            resp = await self.router.handle(req)
            if resp.text:
                await self.send_message(sender_id, resp.text)
        except Exception as e:
            logger.exception(f"Error handling WhatsApp command: {e}")
            await self.send_message(sender_id, "[Internal Error]")

    # ------------------------------------------------------------------
    # Outbound: Text
    # ------------------------------------------------------------------

    async def send_message(
        self,
        recipient_id: str,
        text: str,
        delivery_context: Optional[dict] = None,
    ):
        """Send text message via WhatsApp Cloud API."""
        if self._session_invalid:
            logger.warning("R93: Connector session invalid - blocking outbound")
            return

        if not self.session:
            return

        url = f"{GRAPH_API_BASE}/{self.config.whatsapp_phone_number_id}/messages"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.whatsapp_access_token}",
        }

        # WhatsApp text limit is ~4096 chars
        if len(text) > 4000:
            text = text[:4000] + "\n...(truncated)"

        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient_id,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }

        try:
            async with self.session.post(url, headers=headers, json=body) as resp:
                if RelayResponseClassifier.is_auth_invalid(resp.status):
                    self._session_invalid = True
                    logger.error(
                        f"R93: Auth Invalid (WhatsApp {resp.status}) - Locking session"
                    )
                    return

                if resp.status == 429:
                    logger.warning("WhatsApp API Rate Limit Hit")
                elif resp.status not in (200, 201):
                    err = await resp.text()
                    logger.error(f"WhatsApp send_message failed: {resp.status} {err}")
        except Exception as e:
            logger.error(f"WhatsApp send_message error: {e}")

    # ------------------------------------------------------------------
    # Outbound: Image
    # ------------------------------------------------------------------

    async def send_image(
        self,
        channel_id: str,
        image_data: bytes,
        filename: str = "image.png",
        caption: Optional[str] = None,
        delivery_context: Optional[dict] = None,
    ):
        """
        Send image via WhatsApp using public media URL.
        Reuses F33 media store to host the image, then sends a link message.
        """
        if self._session_invalid:
            logger.warning("R93: Connector session invalid - blocking outbound image")
            return

        if not self.config.public_base_url:
            logger.warning("WhatsApp send_image: No public_base_url configured.")
            text = (
                "[OpenClaw] Image ready but cannot be delivered.\n"
                "⚠️ Admin: Set OPENCLAW_CONNECTOR_PUBLIC_BASE_URL to enable image delivery."
            )
            await self.send_message(channel_id, text)
            return

        try:
            ext = "." + filename.split(".")[-1] if "." in filename else ".png"
            token = self.media_store.store_image(image_data, ext, channel_id)

            # Construct public URL
            base = self.config.public_base_url.rstrip("/")
            path = self.config.media_path.strip("/")
            image_url = f"{base}/{path}/{token}"

            await self._send_whatsapp_image(channel_id, image_url, caption)

        except Exception as e:
            logger.error(f"Failed to send WhatsApp image: {e}")
            await self.send_message(channel_id, "[OpenClaw] Error delivering image.")

    async def _send_whatsapp_image(
        self,
        recipient_id: str,
        image_url: str,
        caption: Optional[str] = None,
    ):
        """Send image message via Graph API /messages endpoint."""
        if self._session_invalid:
            return

        if not self.session:
            return

        url = f"{GRAPH_API_BASE}/{self.config.whatsapp_phone_number_id}/messages"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.whatsapp_access_token}",
        }

        image_payload = {"link": image_url}
        if caption:
            image_payload["caption"] = caption[:1024]

        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient_id,
            "type": "image",
            "image": image_payload,
        }

        try:
            async with self.session.post(url, headers=headers, json=body) as resp:
                if RelayResponseClassifier.is_auth_invalid(resp.status):
                    self._session_invalid = True
                    logger.error(
                        f"R93: Auth Invalid (WhatsApp {resp.status}) - Locking session"
                    )
                    return

                if resp.status not in (200, 201):
                    err = await resp.text()
                    logger.error(f"WhatsApp image send failed: {resp.status} {err}")
        except Exception as e:
            logger.error(f"WhatsApp image send error: {e}")
