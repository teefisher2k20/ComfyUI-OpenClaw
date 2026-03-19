"""
LINE Webhook Platform Adapter (F29).
Receives webhooks from LINE, verifies signature, and routes commands.

Security: uses shared S32 primitives (verify_hmac_signature, ReplayGuard,
AllowlistPolicy) instead of inline implementations.
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

    This keeps unit tests runnable in environments where aiohttp isn't installed
    (CI/unit tests can still validate pure logic like signature verification).
    """
    try:
        import aiohttp  # type: ignore
        from aiohttp import web  # type: ignore
    except ModuleNotFoundError:
        return None, None
    return aiohttp, web


class LINEWebhookServer:
    # Replay protection config — shared via S32 ReplayGuard
    REPLAY_WINDOW_SEC = 300  # 5 minutes
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

        # S32: shared allowlist policies (soft-deny: strict=False)
        self._user_allowlist = AllowlistPolicy(config.line_allowed_users, strict=False)
        self._group_allowlist = AllowlistPolicy(
            config.line_allowed_groups, strict=False
        )

        # F33 Media Store
        from ..media_store import MediaStore

        self.media_store = MediaStore(config)

    async def start(self):
        """Start the webhook server."""
        aiohttp, web = _import_aiohttp_web()
        if aiohttp is None or web is None:
            logger.warning("aiohttp not installed. Skipping LINE adapter.")
            return

        if (
            not self.config.line_channel_secret
            or not self.config.line_channel_access_token
        ):
            logger.warning(
                "LINE Channel Secret or Access Token missing. Skipping LINE adapter."
            )
            return

        logger.info(
            f"Starting LINE Webhook on {self.config.line_bind_host}:{self.config.line_bind_port}{self.config.line_webhook_path}"
        )
        self.session = aiohttp.ClientSession()
        self._session_invalid = False  # Reset on start

        self.app = web.Application()
        self.app.router.add_post(self.config.line_webhook_path, self.handle_webhook)
        # F33 Media Route
        media_route = f"{self.config.media_path}/{{token}}"
        self.app.router.add_get(media_route, self._handle_media_request)

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(
            self.runner, self.config.line_bind_host, self.config.line_bind_port
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

    async def handle_webhook(self, request):
        aiohttp, web = _import_aiohttp_web()
        if aiohttp is None or web is None:
            raise RuntimeError("aiohttp not available")

        # 1. Signature Verification — S32 shared verifier (base64 digest)
        body_bytes = await request.read()
        body_text = body_bytes.decode("utf-8")
        signature = request.headers.get("X-Line-Signature", "")

        auth_result = verify_hmac_signature(
            body_bytes,
            signature_header=signature,
            secret=self.config.line_channel_secret or "",
            algorithm="sha256",
            digest_encoding="base64",
        )
        if not auth_result.ok:
            logger.warning(f"Invalid LINE Signature: {auth_result.error}")
            return web.Response(status=401, text="Invalid Signature")

        # 2. Parse Event
        try:
            payload = json.loads(body_text)
        except json.JSONDecodeError:
            return web.Response(status=400, text="Bad JSON")

        events = payload.get("events", [])

        # 3. Per-event replay + timestamp check — S32 ReplayGuard
        now = time.time() * 1000  # LINE timestamps are in ms
        for event in events:
            # Timestamp freshness
            ts = event.get("timestamp", 0)
            age_sec = (now - ts) / 1000
            if age_sec > self.REPLAY_WINDOW_SEC or age_sec < -60:
                logger.debug(f"Stale or future event: age={age_sec:.1f}s")
                logger.warning("Replay attack detected or stale request")
                return web.Response(status=403, text="Replay Rejected")

            # Nonce dedup via S32 ReplayGuard
            nonce = event.get("webhookEventId") or event.get("replyToken")
            if nonce and not self._replay_guard.check_and_record(nonce):
                logger.warning("Replay attack detected or stale request")
                return web.Response(status=403, text="Replay Rejected")

        # 4. Process events
        for event in events:
            if (
                event.get("type") == "message"
                and event.get("message", {}).get("type") == "text"
            ):
                await self._process_event(event)

        return web.Response(text="OK")

    async def _handle_media_request(self, request):
        """Serve media files for verified tokens."""
        aiohttp, web = _import_aiohttp_web()
        token = request.match_info.get("token")
        path = self.media_store.get_image_path(token)

        if not path:
            return web.Response(status=404, text="Media Not Found or Expired")

        return web.FileResponse(path)

    async def _process_event(self, event: dict):
        """Convert LINE event to CommandRequest and route."""
        source = event.get("source", {})
        user_id = source.get("userId")
        group_id = source.get("groupId")
        room_id = source.get("roomId")  # Remediation: Support RoomId

        # Identity Logic:
        # For LINE, we use user_id as sender_id.
        # channel_id: if group/room, use that ID; else use userId (DM).
        channel_id = group_id or room_id or user_id

        text = event["message"]["text"]
        reply_token = event.get("replyToken")

        # Security allowlist — S32 AllowlistPolicy (soft-deny: strict=False)
        is_allowed = False
        if user_id:
            user_result = self._user_allowlist.evaluate(user_id)
            if user_result.decision == "allow":
                is_allowed = True
        if group_id:
            group_result = self._group_allowlist.evaluate(group_id)
            if group_result.decision == "allow":
                is_allowed = True
        if room_id:
            room_result = self._group_allowlist.evaluate(room_id)
            if room_result.decision == "allow":
                is_allowed = True

        if not is_allowed:
            # Informational only: untrusted messages are accepted but will require approval.
            msg = f"Untrusted LINE message from user={user_id} in channel={channel_id}."
            if (
                not self.config.line_allowed_users
                and not self.config.line_allowed_groups
            ):
                msg += " (Allow lists are empty; all users will require approval)"
            else:
                msg += " (Not in allowlist; approval required)"
            logger.warning(msg)

        req = CommandRequest(
            platform="line",
            sender_id=str(user_id),
            channel_id=str(channel_id),
            username="line_user",
            message_id=event.get("webhookEventId", str(time.time())),
            text=text,
            timestamp=event.get("timestamp", 0) / 1000,
        )

        try:
            resp = await self.router.handle(req)
            if resp.text:
                await self._reply_message(reply_token, resp.text)
        except Exception as e:
            logger.exception(f"Error handling LINE command: {e}")
            await self._reply_message(reply_token, "[Internal Error]")

    async def _reply_message(self, reply_token: str, text: str):
        """Send reply via LINE Messaging API."""
        if self._session_invalid:
            logger.warning("R93: Connector session invalid - blocking outbound")
            return

        aiohttp, _ = _import_aiohttp_web()
        if aiohttp is None:
            raise RuntimeError("aiohttp not available")

        if not reply_token or reply_token == "00000000000000000000000000000000":
            return

        url = "https://api.line.me/v2/bot/message/reply"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.line_channel_access_token}",
        }

        if len(text) > 4000:
            text = text[:4000] + "\n...(truncated)"

        body = {"replyToken": reply_token, "messages": [{"type": "text", "text": text}]}

        # Remediation: Use persistent session
        try:
            async with self.session.post(url, headers=headers, json=body) as resp:
                if RelayResponseClassifier.is_auth_invalid(resp.status):
                    self._session_invalid = True
                    logger.error(
                        f"R93: Auth Invalid (LINE {resp.status}) - Locking session"
                    )
                    return

                if resp.status == 429:  # Check Rate Limit first
                    logger.warning("LINE API Rate Limit Hit")
                elif resp.status != 200:
                    logger.error(
                        f"Failed to send LINE reply: {resp.status} {await resp.text()}"
                    )
        except Exception as e:
            logger.error(f"LINE reply exception: {e}")

    async def send_image(
        self,
        channel_id: str,
        image_data: bytes,
        filename: str = "image.png",
        caption: Optional[str] = None,
        delivery_context: Optional[dict] = None,
    ):
        """
        Send image via LINE using public URL.
        """
        if self._session_invalid:
            logger.warning("R93: Connector session invalid - blocking outbound image")
            return

        if not self.config.public_base_url:
            logger.warning("LINE send_image: No public_base_url configured.")
            text = (
                "[OpenClaw] Image ready but cannot be delivered.\n"
                "⚠️ Admin: Set OPENCLAW_CONNECTOR_PUBLIC_BASE_URL to enable image delivery."
            )
            await self.send_message(channel_id, text)
            return

        try:
            ext = "." + filename.split(".")[-1] if "." in filename else ".png"
            token = self.media_store.store_image(image_data, ext, channel_id)

            # Construct URL
            base = self.config.public_base_url.rstrip("/")
            path = self.config.media_path.strip("/")
            image_url = f"{base}/{path}/{token}"

            preview_url = image_url
            # NOTE: LINE thumbnails rely on previewImageUrl; we generate a JPEG preview
            # when Pillow is available to improve in-chat rendering.
            preview_bytes = self.media_store.build_preview(image_data)
            if preview_bytes:
                preview_token = self.media_store.store_image(
                    preview_bytes, ".jpg", channel_id
                )
                preview_url = f"{base}/{path}/{preview_token}"

            await self._send_line_image_payload(channel_id, image_url, preview_url)

        except Exception as e:
            logger.error(f"Failed to send LINE image: {e}")
            await self.send_message(channel_id, "[OpenClaw] Error delivering image.")

    async def _send_line_image_payload(
        self, channel_id: str, url: str, preview_url: Optional[str] = None
    ):
        """Low-level push image."""
        if self._session_invalid:
            return

        aiohttp, _ = _import_aiohttp_web()
        if not self.session:
            return

        api_url = "https://api.line.me/v2/bot/message/push"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.line_channel_access_token}",
        }

        body = {
            "to": channel_id,
            "messages": [
                {
                    "type": "image",
                    "originalContentUrl": url,
                    "previewImageUrl": preview_url or url,
                }
            ],
        }

        async with self.session.post(api_url, headers=headers, json=body) as resp:
            if RelayResponseClassifier.is_auth_invalid(resp.status):
                self._session_invalid = True
                logger.error(
                    f"R93: Auth Invalid (LINE {resp.status}) - Locking session"
                )
                return

            if resp.status != 200:
                err = await resp.text()
                logger.error(f"LINE image push failed: {resp.status} {err}")

    async def send_message(
        self,
        channel_id: str,
        text: str,
        delivery_context: Optional[dict] = None,
    ):
        """Send push message."""
        if self._session_invalid:
            logger.warning("R93: Connector session invalid - blocking outbound message")
            return

        aiohttp, _ = _import_aiohttp_web()
        if not aiohttp or not self.session:
            return

        url = "https://api.line.me/v2/bot/message/push"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.line_channel_access_token}",
        }

        body = {
            "to": channel_id,
            "messages": [{"type": "text", "text": text[:2000]}],  # LINE limit handling
        }

        try:
            async with self.session.post(url, headers=headers, json=body) as resp:
                if RelayResponseClassifier.is_auth_invalid(resp.status):
                    self._session_invalid = True
                    logger.error(
                        f"R93: Auth Invalid (LINE {resp.status}) - Locking session"
                    )
                    return

                if resp.status != 200:
                    err = await resp.text()
                    logger.error(f"LINE send_message failed: {resp.status} {err}")
        except Exception as e:
            logger.error(f"LINE send_message error: {e}")
