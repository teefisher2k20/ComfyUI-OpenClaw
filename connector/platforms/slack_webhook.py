"""
Slack Events API Webhook Adapter (F56).

Implements:
- Events API POST ingress with ``url_verification`` challenge response.
- Slack request authenticity via ``X-Slack-Signature`` + ``X-Slack-Request-Timestamp``
  (``v0:{ts}:{raw_body}`` HMAC-SHA256).
- Replay / duplicate guard (event_id + timestamp window).
- ``message`` / ``app_mention`` event normalization with de-duplication
  (avoid double-trigger when bot is mentioned in a regular message).
- CommandRequest conversion -> CommandRouter.
- Slack Web API thread or channel reply.

S67 Safety Profile:
- AllowlistPolicy for users and channels (fail-closed when configured).
- Bot-loop prevention (ignore messages from bot itself).
- Rate-limit delegation to CommandRouter (R80 authz + F32 rate limiter).
- Require-mention policy for group conversations.

Setup:
1. Create a Slack App at https://api.slack.com/apps.
2. Enable Events API; set Request URL to ``https://<host>/slack/events``.
3. Subscribe to ``message.channels``, ``message.groups``, ``message.im``,
   ``app_mention`` bot events.
4. Install app to workspace; copy Bot Token and Signing Secret.
5. Set env vars:
   - ``OPENCLAW_CONNECTOR_SLACK_BOT_TOKEN``
   - ``OPENCLAW_CONNECTOR_SLACK_SIGNING_SECRET``
"""

import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, Optional, Tuple

from ..config import ConnectorConfig
from ..contract import CommandRequest, CommandResponse
from ..router import CommandRouter
from ..security_profile import AllowlistPolicy, ReplayGuard
from .slack_installation_manager import SlackInstallationManager

logger = logging.getLogger(__name__)


# -- aiohttp compat layer (same pattern as kakao/whatsapp/wechat) -----------


def _import_aiohttp_web():
    try:
        import aiohttp
        from aiohttp import web
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


def _make_response(web_mod, *, status: int = 200, text: str = "OK"):
    if web_mod is not None:
        return web_mod.Response(status=status, text=text)
    return _CompatResponse(status=status, text=text)


def _make_json_response(web_mod, data: dict, *, status: int = 200):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    if web_mod is not None:
        return web_mod.json_response(data, status=status)
    return _CompatResponse(
        status=status,
        text=body.decode("utf-8"),
        content_type="application/json",
        body=body,
    )


def _make_redirect_response(web_mod, url: str):
    if web_mod is not None:
        raise web_mod.HTTPFound(location=url)
    return _CompatResponse(status=302, text=url)


# -- Slack signature verification -------------------------------------------

# Maximum acceptable clock skew for timestamp validation (5 minutes).
SLACK_TIMESTAMP_MAX_DRIFT_SEC = 300
SLACK_SIGNING_VERSION = "v0"


def verify_slack_signature(
    *,
    signing_secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
) -> bool:
    """
    Verify Slack ``X-Slack-Signature`` using ``v0:{ts}:{body}`` HMAC-SHA256.

    Fail-closed: returns False on any missing/invalid input.
    """
    if not signing_secret or not timestamp or not signature:
        return False

    # Timestamp freshness check
    try:
        ts_int = int(timestamp)
    except (ValueError, TypeError):
        return False

    if abs(time.time() - ts_int) > SLACK_TIMESTAMP_MAX_DRIFT_SEC:
        return False

    # Compute expected signature
    sig_basestring = f"{SLACK_SIGNING_VERSION}:{timestamp}:{body.decode('utf-8')}"
    expected = (
        SLACK_SIGNING_VERSION
        + "="
        + hmac.new(
            signing_secret.encode("utf-8"),
            sig_basestring.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    )

    return hmac.compare_digest(expected, signature)


# -- Slack adapter ----------------------------------------------------------


class SlackWebhookServer:
    """
    F56 -- Slack Events API adapter.

    Security invariants (S67 / R124):
    - CRITICAL: Reject unsigned or replay requests (fail-closed).
    - CRITICAL: Ignore bot's own messages (bot-loop prevention).
    - IMPORTANT: Deduplicate ``message`` + ``app_mention`` for the same event
      to prevent double command execution.
    - IMPORTANT: Respect ``require_mention`` policy for group channels.
    """

    REPLAY_WINDOW_SEC = 300
    NONCE_CACHE_SIZE = 5000

    def __init__(self, config: ConnectorConfig, router: CommandRouter):
        self.config = config
        self.router = router
        self.app = None
        self.runner = None
        self.site = None

        # S67: Replay / dedupe guard keyed by Slack event_id
        self._replay_guard = ReplayGuard(
            window_sec=self.REPLAY_WINDOW_SEC,
            max_entries=self.NONCE_CACHE_SIZE,
        )

        # S67: Allowlists (fail-closed when configured)
        self._user_allowlist = AllowlistPolicy(config.slack_allowed_users, strict=False)
        self._channel_allowlist = AllowlistPolicy(
            config.slack_allowed_channels, strict=False
        )
        self._installation_manager = SlackInstallationManager(config)

        # Bot user ID (resolved on first event or set from config)
        self._bot_user_id: Optional[str] = None
        self._bot_user_ids: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        aiohttp, web = _import_aiohttp_web()
        if aiohttp is None or web is None:
            logger.warning("aiohttp not installed. Skipping Slack adapter.")
            return

        if not self.config.slack_signing_secret:
            logger.info(
                "Slack adapter disabled "
                "(OPENCLAW_CONNECTOR_SLACK_SIGNING_SECRET missing)"
            )
            return
        if (
            not self.config.slack_bot_token
            and not self._installation_manager.can_handle_oauth()
        ):
            logger.info(
                "Slack adapter disabled "
                "(legacy bot token missing and Slack OAuth flow not configured)"
            )
            return

        logger.info(
            f"Starting Slack Webhook on "
            f"{self.config.slack_bind_host}:{self.config.slack_bind_port}"
            f"{self.config.slack_webhook_path}"
        )

        self.app = web.Application()
        self.app.router.add_post(self.config.slack_webhook_path, self.handle_event)
        if self._installation_manager.can_handle_oauth():
            self.app.router.add_get(
                self.config.slack_oauth_install_path, self.handle_oauth_install
            )
            self.app.router.add_get(
                self.config.slack_oauth_callback_path, self.handle_oauth_callback
            )

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(
            self.runner, self.config.slack_bind_host, self.config.slack_bind_port
        )
        await self.site.start()

    async def stop(self):
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()

    # ------------------------------------------------------------------
    # Event handler
    # ------------------------------------------------------------------

    async def handle_oauth_install(self, request):
        _, web = _import_aiohttp_web()
        if not self._installation_manager.can_handle_oauth():
            return _make_response(web, status=503, text="Slack OAuth not configured")
        state = self._installation_manager.issue_install_state()
        return _make_redirect_response(
            web, self._installation_manager.build_install_url(state)
        )

    async def handle_oauth_callback(self, request):
        _, web = _import_aiohttp_web()
        if not self._installation_manager.can_handle_oauth():
            return _make_response(web, status=503, text="Slack OAuth not configured")
        query = getattr(request, "query", {}) or {}
        if query.get("error"):
            return _make_response(
                web,
                status=400,
                text=f"Slack OAuth rejected: {query.get('error')}",
            )
        state = str(query.get("state", "") or "").strip()
        code = str(query.get("code", "") or "").strip()
        if not state or not code:
            return _make_response(web, status=400, text="Missing OAuth callback fields")
        if not self._installation_manager.consume_install_state(state):
            return _make_response(
                web, status=400, text="Invalid or replayed OAuth state"
            )
        try:
            payload = await self._installation_manager.exchange_code(code)
            installation = self._installation_manager.upsert_from_oauth_payload(payload)
            return _make_response(
                web,
                status=200,
                text=(
                    "Slack installation complete for "
                    f"{installation.workspace_id} ({installation.installation_id})."
                ),
            )
        except Exception as exc:
            logger.warning("Slack OAuth callback failed: %s", exc)
            return _make_response(web, status=502, text=str(exc))

    def _get_bot_user_id(self, payload: Dict[str, Any], workspace_id: str) -> str:
        candidate = ""
        if workspace_id and workspace_id in self._bot_user_ids:
            return self._bot_user_ids[workspace_id]
        if self._bot_user_id:
            return self._bot_user_id
        authorizations = payload.get("authorizations", [])
        if authorizations and isinstance(authorizations, list):
            candidate = str((authorizations[0] or {}).get("user_id", "") or "").strip()
            if candidate:
                self._bot_user_id = candidate
                if workspace_id:
                    self._bot_user_ids[workspace_id] = candidate
                return candidate
        if workspace_id:
            workspace_resolution, _ = (
                self._installation_manager.resolve_workspace_tokens(workspace_id)
            )
            candidate = self._installation_manager.bot_user_id_for_installation(
                workspace_resolution.installation if workspace_resolution.ok else None
            )
            if candidate:
                self._bot_user_ids[workspace_id] = candidate
                if self._bot_user_id is None:
                    self._bot_user_id = candidate
        return candidate

    def _resolve_workspace_credentials(
        self, workspace_id: str
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        workspace_id = str(workspace_id or "").strip()
        if workspace_id:
            resolution, tokens = self._installation_manager.resolve_workspace_tokens(
                workspace_id
            )
            if resolution.ok and resolution.installation is not None:
                bot_token = tokens.get("bot_token")
                if bot_token:
                    self._installation_manager.mark_resolution_success(
                        resolution.installation.installation_id, workspace_id
                    )
                    return (
                        resolution.installation.installation_id,
                        bot_token,
                        workspace_id,
                    )
                logger.warning(
                    "Slack workspace %s resolved without bot token secret", workspace_id
                )
                return (
                    resolution.installation.installation_id,
                    None,
                    workspace_id,
                )
            if (
                not self._installation_manager.oauth_enabled
                and self.config.slack_bot_token
            ):
                return (None, self.config.slack_bot_token, workspace_id)
            logger.warning(
                "Slack workspace resolution failed for %s: %s (%s)",
                workspace_id,
                resolution.reject_reason,
                resolution.health_code,
            )
            return (None, None, workspace_id)
        if self.config.slack_bot_token:
            return (None, self.config.slack_bot_token, "")
        return (None, None, workspace_id)

    def _handle_lifecycle_event(self, workspace_id: str, event_type: str) -> None:
        installation_id = self._installation_manager.installation_id_for_workspace(
            workspace_id
        )
        try:
            if event_type == "app_uninstalled":
                self._installation_manager.mark_installation_health(
                    installation_id,
                    health_code="revoked",
                    reason="slack_app_uninstalled",
                    details={"workspace_id": workspace_id},
                )
                self._installation_manager.uninstall_installation(
                    installation_id, reason="slack_app_uninstalled"
                )
            elif event_type == "tokens_revoked":
                self._installation_manager.mark_installation_health(
                    installation_id,
                    health_code="invalid_token",
                    reason="slack_tokens_revoked",
                    details={"workspace_id": workspace_id},
                )
            elif event_type == "app_rate_limited":
                self._installation_manager.mark_installation_health(
                    installation_id,
                    health_code="degraded",
                    reason="slack_app_rate_limited",
                    details={"workspace_id": workspace_id},
                )
        except ValueError:
            logger.warning(
                "Slack lifecycle event for unbound workspace %s (%s)",
                workspace_id,
                event_type,
            )

    async def handle_event(self, request):
        """POST handler for Slack Events API."""
        _, web = _import_aiohttp_web()

        try:
            body_bytes = await request.read()
        except Exception:
            return _make_response(web, status=400, text="Bad request")

        # -- Step 1: Signature verification (fail-closed) --
        timestamp = ""
        signature = ""
        if hasattr(request, "headers"):
            timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
            signature = request.headers.get("X-Slack-Signature", "")

        if not verify_slack_signature(
            signing_secret=self.config.slack_signing_secret or "",
            timestamp=timestamp,
            body=body_bytes,
            signature=signature,
        ):
            logger.warning("Slack signature verification failed (rejected)")
            return _make_response(web, status=401, text="Invalid signature")

        # -- Step 2: Parse payload --
        try:
            payload = json.loads(body_bytes)
        except json.JSONDecodeError:
            return _make_response(web, status=400, text="Bad JSON")

        # -- Step 3: url_verification challenge (Webhook only) --
        if payload.get("type") == "url_verification":
            challenge = payload.get("challenge", "")
            return _make_json_response(web, {"challenge": challenge})

        # -- Step 4: Process event --
        try:
            await self.process_event_payload(payload)
        except ValueError:
            return _make_response(web, status=400, text="Bad Request")
        return _make_response(web, status=200, text="OK")

    async def process_event_payload(self, payload: Dict[str, Any]) -> None:
        """
        Shared event processing path for both webhook and socket mode transports.
        """
        if payload.get("type") != "event_callback":
            return

        event = payload.get("event", {})
        event_id = payload.get("event_id", "")
        event_type = event.get("type", "")
        workspace_id = self._installation_manager.extract_workspace_id(payload)

        if event_type in ("app_uninstalled", "tokens_revoked", "app_rate_limited"):
            if workspace_id:
                self._handle_lifecycle_event(workspace_id, event_type)
            return

        # -- Step 5: Replay / dedupe guard --
        if not event_id:
            logger.warning("Slack event missing event_id (rejected)")
            raise ValueError("Missing event_id")

        if not self._replay_guard.check_and_record(event_id):
            logger.debug(f"Slack duplicate event_id={event_id} (accepted, no-op)")
            return

        # -- Step 6: Bot-loop prevention --
        # Resolve bot user ID from authorizations or cache.
        bot_user_id = self._get_bot_user_id(payload, workspace_id)

        sender_id = event.get("user", "")
        if sender_id and bot_user_id and sender_id == bot_user_id:
            return

        if event.get("bot_id"):
            return

        subtype = event.get("subtype", "")
        if subtype and subtype not in ("", "file_share"):
            return

        # -- Step 7: Event normalization --
        text = event.get("text", "").strip()
        channel_id = event.get("channel", "")
        thread_ts = event.get("thread_ts", "")
        message_ts = event.get("ts", "")

        if event_type not in ("message", "app_mention"):
            return

        if not text or not sender_id:
            return

        # S67: Require mention in group channels.
        is_dm = channel_id.startswith("D")
        if not is_dm and self.config.slack_require_mention:
            if event_type != "app_mention":
                if bot_user_id and f"<@{bot_user_id}>" not in text:
                    return

        if bot_user_id:
            text = text.replace(f"<@{bot_user_id}>", "").strip()

        # -- Step 8: Allowlist checks (S67) --
        if self._user_allowlist.entries:
            user_result = self._user_allowlist.evaluate(sender_id)
            if user_result.decision == "deny":
                logger.warning(f"Slack user {sender_id} denied by allowlist")
                return

        if self._channel_allowlist.entries and channel_id:
            chan_result = self._channel_allowlist.evaluate(channel_id)
            if chan_result.decision == "deny":
                logger.warning(f"Slack channel {channel_id} denied by allowlist")
                return

        # -- Step 9: Build CommandRequest and route --
        req = CommandRequest(
            platform="slack",
            sender_id=sender_id,
            channel_id=channel_id,
            username=sender_id,
            message_id=event_id,
            text=text,
            timestamp=float(message_ts) if message_ts else time.time(),
            workspace_id=workspace_id,
            thread_id=thread_ts
            or (message_ts if self.config.slack_reply_in_thread else ""),
        )

        try:
            resp = await self.router.handle(req)
            resp_text = getattr(resp, "text", "")
            if not isinstance(resp_text, str):
                resp_text = str(resp_text) if resp_text is not None else ""

            if resp_text:
                await self._send_reply(
                    channel_id=channel_id,
                    text=resp_text,
                    thread_ts=req.thread_id,
                    delivery_context={
                        "workspace_id": workspace_id,
                        "thread_id": req.thread_id,
                    },
                )
        except Exception as e:
            logger.exception(f"Error handling Slack event: {e}")

    # ------------------------------------------------------------------
    # Slack Web API reply
    # ------------------------------------------------------------------

    async def _send_reply(
        self,
        channel_id: str,
        text: str,
        thread_ts: str = "",
        delivery_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Send a message via Slack Web API (chat.postMessage)."""
        try:
            import aiohttp as _aiohttp
        except ImportError:
            logger.warning("aiohttp not available; cannot send Slack reply")
            return

        ctx = dict(delivery_context or {})
        if not thread_ts:
            thread_ts = str(ctx.get("thread_id", "") or "").strip()
        installation_id, bot_token, workspace_id = self._resolve_workspace_credentials(
            str(ctx.get("workspace_id", "") or "").strip()
        )
        if not bot_token:
            logger.warning(
                "Slack reply dropped: no workspace token available (workspace=%s)",
                workspace_id or "legacy",
            )
            return

        url = "https://slack.com/api/chat.postMessage"
        headers = {
            "Authorization": f"Bearer {bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        payload: Dict[str, Any] = {
            "channel": channel_id,
            "text": text,
        }
        if thread_ts:
            payload["thread_ts"] = thread_ts

        try:
            async with _aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=_aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        if installation_id:
                            self._installation_manager.mark_api_error(
                                installation_id,
                                error_code=f"http_{resp.status}",
                                status_code=resp.status,
                                details={
                                    "workspace_id": workspace_id,
                                    "path": "chat.postMessage",
                                },
                            )
                        logger.warning(
                            f"Slack API error: status={resp.status} body={body[:200]}"
                        )
                    else:
                        data = await resp.json()
                        if not data.get("ok"):
                            if installation_id:
                                self._installation_manager.mark_api_error(
                                    installation_id,
                                    error_code=str(data.get("error", "unknown")),
                                    details={
                                        "workspace_id": workspace_id,
                                        "path": "chat.postMessage",
                                    },
                                )
                            logger.warning(
                                f"Slack API error: {data.get('error', 'unknown')}"
                            )
                        elif installation_id:
                            self._installation_manager.mark_installation_health(
                                installation_id,
                                health_code="ok",
                                reason="chat_post_message_ok",
                                details={"workspace_id": workspace_id},
                            )
        except Exception as e:
            logger.warning(f"Slack reply failed: {e}")

    # ------------------------------------------------------------------
    # Platform contract: send_message / send_image
    # ------------------------------------------------------------------

    async def send_message(
        self,
        channel_id: str,
        text: str,
        delivery_context: Optional[Dict[str, Any]] = None,
    ):
        """Platform contract: send text message."""
        await self._send_reply(
            channel_id=channel_id,
            text=text,
            delivery_context=delivery_context,
        )

    async def send_image(
        self,
        channel_id: str,
        image_data: bytes,
        filename: str = "image.png",
        caption: Optional[str] = None,
        delivery_context: Optional[Dict[str, Any]] = None,
    ):
        """Platform contract: send image (Slack files.upload)."""
        try:
            import aiohttp as _aiohttp
        except ImportError:
            logger.warning("aiohttp not available; cannot upload Slack image")
            return

        ctx = dict(delivery_context or {})
        thread_ts = str(ctx.get("thread_id", "") or "").strip()
        installation_id, bot_token, workspace_id = self._resolve_workspace_credentials(
            str(ctx.get("workspace_id", "") or "").strip()
        )
        if not bot_token:
            logger.warning(
                "Slack image dropped: no workspace token available (workspace=%s)",
                workspace_id or "legacy",
            )
            return

        url = "https://slack.com/api/files.upload"
        headers = {
            "Authorization": f"Bearer {bot_token}",
        }
        data = _aiohttp.FormData()
        data.add_field("file", image_data, filename=filename, content_type="image/png")
        data.add_field("channels", channel_id)
        if caption:
            data.add_field("initial_comment", caption)
        if thread_ts:
            data.add_field("thread_ts", thread_ts)

        try:
            async with _aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    data=data,
                    headers=headers,
                    timeout=_aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        if installation_id:
                            self._installation_manager.mark_api_error(
                                installation_id,
                                error_code=f"http_{resp.status}",
                                status_code=resp.status,
                                details={
                                    "workspace_id": workspace_id,
                                    "path": "files.upload",
                                },
                            )
                        logger.warning(f"Slack file upload error: status={resp.status}")
                    else:
                        resp_data = await resp.json()
                        if not resp_data.get("ok"):
                            if installation_id:
                                self._installation_manager.mark_api_error(
                                    installation_id,
                                    error_code=str(resp_data.get("error", "unknown")),
                                    details={
                                        "workspace_id": workspace_id,
                                        "path": "files.upload",
                                    },
                                )
                            logger.warning(
                                f"Slack file upload error: {resp_data.get('error')}"
                            )
                        elif installation_id:
                            self._installation_manager.mark_installation_health(
                                installation_id,
                                health_code="ok",
                                reason="files_upload_ok",
                                details={"workspace_id": workspace_id},
                            )
        except Exception as e:
            logger.warning(f"Slack image upload failed: {e}")
