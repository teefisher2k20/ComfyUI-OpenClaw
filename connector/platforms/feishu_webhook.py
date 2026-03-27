"""
Feishu / Lark connector baseline adapter (F67).

Implements:
- webhook ingress with verification-token challenge response
- shared event normalization for webhook + long-connection transports
- DM / group mention gating into CommandRequest
- text / image delivery through Feishu Open API

Notes:
- Feishu "workspace" is represented by tenant_key for connector diagnostics.
- group traffic is gated by explicit bot mention unless disabled in config.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from ..config import ConnectorConfig
from ..contract import CommandRequest
from ..router import CommandRouter
from ..security_profile import AllowlistPolicy, ReplayGuard
from .feishu_installation_manager import FeishuBinding, FeishuInstallationManager

try:
    from services.safe_io import (
        STANDARD_OUTBOUND_POLICY,
        SafeIOHTTPError,
        safe_request_json,
    )
except ImportError:  # pragma: no cover
    from services.safe_io import (  # type: ignore
        STANDARD_OUTBOUND_POLICY,
        SafeIOHTTPError,
        safe_request_json,
    )

logger = logging.getLogger(__name__)

FEISHU_WEBHOOK_MAX_BODY_BYTES = 256 * 1024
FEISHU_TOKEN_TTL_SEC = 3600
FEISHU_DOMAIN_BASES = {
    "feishu": "https://open.feishu.cn",
    "lark": "https://open.larksuite.com",
}
_SUPPORTED_EVENT_TYPES = frozenset({"im.message.receive_v1"})
_PLACEHOLDER_TYPES = {
    "image": "<image>",
    "audio": "<audio>",
    "file": "<file>",
    "media": "<media>",
    "sticker": "<sticker>",
}


def _import_aiohttp_web():
    # CRITICAL: do not replace with direct import; connector tests and minimal
    # CI envs intentionally exercise adapter startup without aiohttp installed.
    try:
        import aiohttp  # type: ignore
        from aiohttp import web  # type: ignore
    except ModuleNotFoundError:
        return None, None
    return aiohttp, web


class _CompatResponse:
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


def _make_json_response(web_mod, data: Dict[str, Any], *, status: int = 200):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    if web_mod is not None:
        return web_mod.json_response(data, status=status)
    return _CompatResponse(
        status=status,
        text=body.decode("utf-8"),
        content_type="application/json",
        body=body,
    )


def _resolve_domain_base(domain: str) -> str:
    normalized = str(domain or "feishu").strip().lower()
    return FEISHU_DOMAIN_BASES.get(normalized, FEISHU_DOMAIN_BASES["feishu"])


def _allowed_api_hosts(domain: str) -> set[str]:
    host = urlparse(_resolve_domain_base(domain)).hostname or ""
    return {host} if host else set()


def _build_multipart_form(
    *,
    fields: Dict[str, str],
    file_field: str,
    filename: str,
    file_bytes: bytes,
    file_content_type: str,
) -> Tuple[bytes, str]:
    boundary = f"----openclaw-feishu-{secrets.token_hex(8)}"
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                (f'Content-Disposition: form-data; name="{key}"\r\n\r\n').encode(
                    "utf-8"
                ),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    parts.extend(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{filename}"\r\n'
            ).encode("utf-8"),
            f"Content-Type: {file_content_type}\r\n\r\n".encode("utf-8"),
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _json_loads_safe(raw: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {}


def _normalize_mentions(message: Dict[str, Any]) -> list[dict]:
    mentions = message.get("mentions") or []
    return mentions if isinstance(mentions, list) else []


def _strip_bot_mention(text: str, mentions: list[dict], bot_open_id: str) -> str:
    cleaned = text or ""
    for mention in mentions:
        key = str(mention.get("key", "") or "").strip()
        open_id = str(((mention.get("id") or {}).get("open_id")) or "").strip()
        if key and bot_open_id and open_id == bot_open_id:
            cleaned = cleaned.replace(key, " ")
    return " ".join(cleaned.split())


def _post_text_to_plain(parsed: Dict[str, Any]) -> str:
    pieces: list[str] = []
    title = str(parsed.get("title", "") or "").strip()
    if title:
        pieces.append(title)
    for row in parsed.get("content") or []:
        if not isinstance(row, list):
            continue
        row_pieces: list[str] = []
        for item in row:
            if not isinstance(item, dict):
                continue
            tag = str(item.get("tag", "") or "").strip().lower()
            if tag == "text":
                row_pieces.append(str(item.get("text", "") or ""))
            elif tag == "at":
                name = str(item.get("user_name", "") or "").strip()
                row_pieces.append(f"@{name}" if name else "@mentioned")
        line = "".join(row_pieces).strip()
        if line:
            pieces.append(line)
    return "\n".join(piece for piece in pieces if piece).strip()


def parse_feishu_message_text(message: Dict[str, Any]) -> str:
    msg_type = str(message.get("message_type", "") or "").strip().lower()
    raw_content = str(message.get("content", "") or "")
    parsed = _json_loads_safe(raw_content)
    if msg_type == "text":
        return str(parsed.get("text", "") or "").strip()
    if msg_type == "post":
        return _post_text_to_plain(parsed)
    if msg_type in _PLACEHOLDER_TYPES:
        return _PLACEHOLDER_TYPES[msg_type]
    return str(parsed.get("text", "") or "").strip()


@dataclass
class FeishuDeliveryTarget:
    channel_id: str
    reply_to_message_id: str = ""
    workspace_id: str = ""
    account_id: str = ""


class FeishuWebhookServer:
    REPLAY_WINDOW_SEC = 300
    NONCE_CACHE_SIZE = 5000

    def __init__(
        self,
        config: ConnectorConfig,
        router: CommandRouter,
        *,
        installation_manager: Optional[FeishuInstallationManager] = None,
        bound_account_id: str = "",
    ):
        self.config = config
        self.router = router
        self._installation_manager = installation_manager or FeishuInstallationManager(
            config
        )
        self._bound_account_id = str(bound_account_id or "").strip()
        self.app = None
        self.runner = None
        self.site = None
        self._replay_guard = ReplayGuard(
            window_sec=self.REPLAY_WINDOW_SEC,
            max_entries=self.NONCE_CACHE_SIZE,
        )
        self._user_allowlist = AllowlistPolicy(
            config.feishu_allowed_users, strict=False
        )
        self._chat_allowlist = AllowlistPolicy(
            config.feishu_allowed_chats, strict=False
        )
        self._tenant_access_tokens: Dict[str, str] = {}
        self._tenant_access_token_expires_at: Dict[str, float] = {}
        self._bot_open_ids: Dict[str, str] = {}
        self._bot_open_id: str = ""

    async def start(self):
        aiohttp, web = _import_aiohttp_web()
        if aiohttp is None or web is None:
            logger.warning("aiohttp not installed. Skipping Feishu webhook adapter.")
            return
        if not self._installation_manager.has_bindings():
            logger.info(
                "Feishu adapter disabled "
                "(OPENCLAW_CONNECTOR_FEISHU_APP_ID / APP_SECRET missing)"
            )
            return
        if not any(
            binding.verification_token
            for binding in self._installation_manager.bindings()
        ):
            logger.info(
                "Feishu webhook adapter disabled "
                "(OPENCLAW_CONNECTOR_FEISHU_VERIFICATION_TOKEN missing)"
            )
            return
        logger.info(
            "Starting Feishu webhook on %s:%s%s (%s)",
            self.config.feishu_bind_host,
            self.config.feishu_bind_port,
            self.config.feishu_webhook_path,
            self.config.feishu_domain,
        )
        self.app = web.Application(client_max_size=FEISHU_WEBHOOK_MAX_BODY_BYTES)
        self.app.router.add_post(self.config.feishu_webhook_path, self.handle_event)
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(
            self.runner,
            self.config.feishu_bind_host,
            self.config.feishu_bind_port,
        )
        await self.site.start()

    async def stop(self):
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()

    async def handle_event(self, request):
        _, web = _import_aiohttp_web()
        try:
            body = await request.read()
        except Exception:
            return _make_response(web, status=400, text="Bad request")
        if len(body) > FEISHU_WEBHOOK_MAX_BODY_BYTES:
            return _make_response(web, status=413, text="Payload too large")
        try:
            payload = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return _make_response(web, status=400, text="Bad JSON")
        if self._is_challenge(payload):
            if not self._verify_request_token(payload):
                return _make_response(
                    web, status=401, text="Invalid verification token"
                )
            return _make_json_response(
                web, {"challenge": str(payload.get("challenge", "") or "")}
            )
        if not self._verify_request_token(payload):
            return _make_response(web, status=401, text="Invalid verification token")
        try:
            await self.process_event_payload(payload)
        except ValueError as exc:
            logger.warning("Feishu event rejected: %s", exc)
            return _make_response(web, status=400, text=str(exc))
        return _make_response(web, status=200, text="OK")

    def _is_challenge(self, payload: Dict[str, Any]) -> bool:
        return bool(
            payload.get("challenge")
            and str(payload.get("type", "") or "").strip().lower() == "url_verification"
        )

    def _verify_request_token(self, payload: Dict[str, Any]) -> bool:
        try:
            self._resolve_inbound_binding(payload)
            return True
        except ValueError:
            return False

    def _resolve_inbound_binding(self, payload: Dict[str, Any]) -> FeishuBinding:
        header = payload.get("header") or {}
        verification_token = (
            str(payload.get("token", "") or "").strip()
            or str(header.get("token", "") or "").strip()
            or str(((payload.get("event") or {}).get("token")) or "").strip()
        )
        workspace_id = str(header.get("tenant_key", "") or "").strip()
        return self._installation_manager.resolve_inbound_binding(
            verification_token=verification_token,
            workspace_id=workspace_id,
            account_id=self._bound_account_id,
        )

    def _cache_key_for_binding(self, binding: FeishuBinding) -> str:
        return binding.installation_id or binding.account_id

    def _cached_bot_open_id(self, binding: FeishuBinding) -> str:
        return (
            self._bot_open_ids.get(self._cache_key_for_binding(binding), "")
            or self._bot_open_id
        )

    def _build_request(
        self,
        payload: Dict[str, Any],
        *,
        binding: FeishuBinding,
        bot_open_id: str,
    ) -> Optional[CommandRequest]:
        header = payload.get("header") or {}
        if (
            str(header.get("event_type", "") or "").strip()
            not in _SUPPORTED_EVENT_TYPES
        ):
            return None
        event = payload.get("event") or {}
        message = event.get("message") or {}
        sender = event.get("sender") or {}
        sender_id = sender.get("sender_id") or {}
        mentions = _normalize_mentions(message)
        sender_user_id = str(sender_id.get("user_id", "") or "").strip()
        sender_open_id = str(sender_id.get("open_id", "") or "").strip()
        chat_id = str(message.get("chat_id", "") or "").strip()
        chat_type = str(message.get("chat_type", "") or "").strip().lower()
        message_id = str(message.get("message_id", "") or "").strip()
        workspace_id = (
            str(header.get("tenant_key", "") or "").strip() or binding.workspace_id
        )
        if not sender_user_id and not sender_open_id:
            return None
        if not chat_id or not message_id:
            return None
        if sender_open_id and bot_open_id and sender_open_id == bot_open_id:
            return None
        raw_text = parse_feishu_message_text(message)
        if not raw_text:
            return None
        mentioned_bot = False
        if bot_open_id:
            for mention in mentions:
                open_id = str(((mention.get("id") or {}).get("open_id")) or "").strip()
                if open_id and open_id == bot_open_id:
                    mentioned_bot = True
                    break
        text = _strip_bot_mention(raw_text, mentions, bot_open_id)
        if (
            chat_type == "group"
            and self.config.feishu_require_mention
            and not mentioned_bot
        ):
            return None
        effective_sender = sender_user_id or sender_open_id
        return CommandRequest(
            platform="feishu",
            sender_id=effective_sender,
            channel_id=chat_id,
            username=effective_sender,
            message_id=message_id,
            text=text,
            timestamp=time.time(),
            workspace_id=workspace_id,
            thread_id=(
                str(message.get("root_id", "") or "").strip()
                or (message_id if self.config.feishu_reply_in_thread else "")
            ),
            metadata={
                "account_id": binding.account_id,
                "chat_type": chat_type,
                "message_type": str(message.get("message_type", "") or "").strip(),
                "sender_open_id": sender_open_id,
            },
        )

    async def process_event_payload(
        self,
        payload: Dict[str, Any],
        *,
        binding: Optional[FeishuBinding] = None,
    ) -> None:
        header = payload.get("header") or {}
        event_id = str(header.get("event_id", "") or "").strip()
        if not event_id:
            raise ValueError("Missing event_id")
        if not self._replay_guard.check_and_record(event_id):
            return
        effective_binding = binding or self._resolve_inbound_binding(payload)
        bot_open_id = self._cached_bot_open_id(effective_binding)
        message = (payload.get("event") or {}).get("message") or {}
        chat_type = str(message.get("chat_type", "") or "").strip().lower()
        if not bot_open_id and chat_type == "group":
            bot_open_id = await self._fetch_bot_open_id(
                binding=effective_binding, allow_degrade=True
            )
        request = self._build_request(
            payload,
            binding=effective_binding,
            bot_open_id=bot_open_id,
        )
        if request is None:
            return
        if self._user_allowlist.entries:
            user_result = self._user_allowlist.evaluate(str(request.sender_id))
            if user_result.decision == "deny":
                return
        if self._chat_allowlist.entries:
            chat_result = self._chat_allowlist.evaluate(str(request.channel_id))
            if chat_result.decision == "deny":
                return
        response = await self.router.handle(request)
        resp_text = str(getattr(response, "text", "") or "").strip()
        if resp_text:
            await self._send_reply(
                FeishuDeliveryTarget(
                    channel_id=request.channel_id,
                    reply_to_message_id=request.thread_id,
                    workspace_id=request.workspace_id,
                    account_id=str(request.metadata.get("account_id", "") or ""),
                ),
                resp_text,
            )

    def _resolve_delivery_binding(
        self, *, workspace_id: str = "", account_id: str = ""
    ) -> Tuple[InstallationResolution, Optional[FeishuBinding], Dict[str, str]]:
        return self._installation_manager.resolve_binding(
            workspace_id=workspace_id,
            account_id=account_id or self._bound_account_id,
        )

    async def _get_tenant_access_token(
        self,
        *,
        binding: Optional[FeishuBinding] = None,
        workspace_id: str = "",
        account_id: str = "",
    ) -> str:
        resolution, effective_binding, secrets = self._resolve_delivery_binding(
            workspace_id=workspace_id,
            account_id=account_id or (binding.account_id if binding else ""),
        )
        if effective_binding is None or not resolution.ok:
            raise RuntimeError(
                f"feishu_binding_resolution_failed:{resolution.reject_reason or 'missing_binding'}"
            )
        cache_key = self._cache_key_for_binding(effective_binding)
        if self._tenant_access_tokens.get(
            cache_key
        ) and self._tenant_access_token_expires_at.get(cache_key, 0.0) > (
            time.time() + 30
        ):
            return self._tenant_access_tokens[cache_key]
        app_secret = str(
            secrets.get("app_secret", "") or effective_binding.app_secret
        ).strip()
        payload = {
            "app_id": effective_binding.app_id,
            "app_secret": app_secret,
        }
        url = f"{_resolve_domain_base(effective_binding.domain)}/open-apis/auth/v3/tenant_access_token/internal"
        try:
            data = safe_request_json(
                method="POST",
                url=url,
                json_body=payload,
                headers={"Accept": "application/json"},
                content_type="application/json; charset=utf-8",
                timeout_sec=15,
                allow_hosts=_allowed_api_hosts(effective_binding.domain),
                policy=STANDARD_OUTBOUND_POLICY,
            )
        except SafeIOHTTPError as exc:
            if resolution.installation is not None:
                self._installation_manager.mark_api_error(
                    resolution.installation.installation_id,
                    error_code=exc.reason,
                    status_code=exc.status_code,
                    details={"phase": "tenant_access_token"},
                )
            raise RuntimeError(
                f"feishu_token_fetch_failed:{exc.status_code}:{exc.reason}"
            ) from exc
        if data.get("code", 0) != 0:
            if resolution.installation is not None:
                self._installation_manager.mark_api_error(
                    resolution.installation.installation_id,
                    error_code=str(data.get("msg", "unknown") or "unknown"),
                    status_code=200,
                    details={"phase": "tenant_access_token"},
                )
            raise RuntimeError(
                f"feishu_token_fetch_failed:200:{data.get('msg', 'unknown')}"
            )
        token = str(data.get("tenant_access_token", "") or "").strip()
        if not token:
            raise RuntimeError("feishu_token_fetch_failed:missing_token")
        expire = int(data.get("expire", FEISHU_TOKEN_TTL_SEC) or FEISHU_TOKEN_TTL_SEC)
        self._tenant_access_tokens[cache_key] = token
        self._tenant_access_token_expires_at[cache_key] = time.time() + max(60, expire)
        if resolution.installation is not None:
            self._installation_manager.mark_resolution_success(
                resolution.installation.installation_id,
                effective_binding.workspace_id,
            )
        return token

    async def _fetch_bot_open_id(
        self,
        *,
        binding: Optional[FeishuBinding] = None,
        workspace_id: str = "",
        account_id: str = "",
        allow_degrade: bool = False,
    ) -> str:
        resolution, effective_binding, _ = self._resolve_delivery_binding(
            workspace_id=workspace_id,
            account_id=account_id or (binding.account_id if binding else ""),
        )
        if effective_binding is None or not resolution.ok:
            return ""
        cache_key = self._cache_key_for_binding(effective_binding)
        if self._bot_open_ids.get(cache_key):
            return self._bot_open_ids[cache_key]
        token = await self._get_tenant_access_token(binding=effective_binding)
        url = f"{_resolve_domain_base(effective_binding.domain)}/open-apis/bot/v3/info"
        try:
            data = safe_request_json(
                method="GET",
                url=url,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                },
                timeout_sec=15,
                allow_hosts=_allowed_api_hosts(effective_binding.domain),
                policy=STANDARD_OUTBOUND_POLICY,
            )
        except SafeIOHTTPError as exc:
            if resolution.installation is not None:
                self._installation_manager.mark_api_error(
                    resolution.installation.installation_id,
                    error_code=exc.reason,
                    status_code=exc.status_code,
                    details={"phase": "bot_info"},
                )
            if allow_degrade:
                return ""
            return ""
        if data.get("code", 0) != 0:
            if resolution.installation is not None:
                self._installation_manager.mark_api_error(
                    resolution.installation.installation_id,
                    error_code=str(data.get("msg", "unknown") or "unknown"),
                    status_code=200,
                    details={"phase": "bot_info"},
                )
            return ""
        bot_open_id = str(
            (((data.get("data") or {}).get("bot") or {}).get("open_id")) or ""
        ).strip()
        if bot_open_id:
            self._bot_open_ids[cache_key] = bot_open_id
            self._bot_open_id = bot_open_id
        return bot_open_id

    async def _send_reply(self, target: FeishuDeliveryTarget, text: str) -> None:
        resolution, binding, _ = self._resolve_delivery_binding(
            workspace_id=target.workspace_id,
            account_id=target.account_id,
        )
        if binding is None or not resolution.ok:
            logger.warning(
                "Feishu reply dropped: no workspace binding available (%s / %s)",
                target.workspace_id or "no-workspace",
                target.account_id or "no-account",
            )
            return
        token = await self._get_tenant_access_token(
            binding=binding,
            workspace_id=target.workspace_id,
            account_id=target.account_id,
        )
        api_base = _resolve_domain_base(binding.domain)
        payload = {
            "content": json.dumps({"text": text}, ensure_ascii=False),
            "msg_type": "text",
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        if target.reply_to_message_id:
            url = (
                f"{api_base}/open-apis/im/v1/messages/"
                f"{target.reply_to_message_id}/reply"
            )
        else:
            url = f"{api_base}/open-apis/im/v1/messages?receive_id_type=chat_id"
            payload["receive_id"] = target.channel_id
        try:
            data = safe_request_json(
                method="POST",
                url=url,
                json_body=payload,
                headers=headers,
                content_type="application/json; charset=utf-8",
                timeout_sec=15,
                allow_hosts=_allowed_api_hosts(binding.domain),
                policy=STANDARD_OUTBOUND_POLICY,
            )
        except SafeIOHTTPError as exc:
            if resolution.installation is not None:
                self._installation_manager.mark_api_error(
                    resolution.installation.installation_id,
                    error_code=exc.reason,
                    status_code=exc.status_code,
                    details={"phase": "reply"},
                )
            logger.warning("Feishu reply failed: status=%s", exc.status_code)
            return
        if data.get("code", 0) != 0:
            if resolution.installation is not None:
                self._installation_manager.mark_api_error(
                    resolution.installation.installation_id,
                    error_code=str(data.get("msg", "unknown") or "unknown"),
                    status_code=200,
                    details={"phase": "reply"},
                )
            logger.warning(
                "Feishu reply failed: %s",
                data.get("msg", "unknown"),
            )

    async def send_message(
        self,
        channel_id: str,
        text: str,
        delivery_context: Optional[Dict[str, Any]] = None,
    ):
        ctx = dict(delivery_context or {})
        await self._send_reply(
            FeishuDeliveryTarget(
                channel_id=channel_id,
                reply_to_message_id=str(ctx.get("thread_id", "") or "").strip(),
                workspace_id=str(ctx.get("workspace_id", "") or "").strip(),
                account_id=str(ctx.get("account_id", "") or "").strip(),
            ),
            text,
        )

    async def send_image(
        self,
        channel_id: str,
        image_data: bytes,
        filename: str = "image.png",
        caption: Optional[str] = None,
        delivery_context: Optional[Dict[str, Any]] = None,
    ):
        ctx = dict(delivery_context or {})
        resolution, binding, _ = self._resolve_delivery_binding(
            workspace_id=str(ctx.get("workspace_id", "") or "").strip(),
            account_id=str(ctx.get("account_id", "") or "").strip(),
        )
        if binding is None or not resolution.ok:
            logger.warning(
                "Feishu image dropped: no workspace binding available (%s / %s)",
                str(ctx.get("workspace_id", "") or "").strip() or "no-workspace",
                str(ctx.get("account_id", "") or "").strip() or "no-account",
            )
            return
        token = await self._get_tenant_access_token(
            binding=binding,
            workspace_id=str(ctx.get("workspace_id", "") or "").strip(),
            account_id=str(ctx.get("account_id", "") or "").strip(),
        )
        api_base = _resolve_domain_base(binding.domain)
        upload_headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        upload_body, upload_content_type = _build_multipart_form(
            fields={"image_type": "message"},
            file_field="image",
            filename=filename,
            file_bytes=image_data,
            file_content_type="image/png",
        )
        try:
            upload_payload = safe_request_json(
                method="POST",
                url=f"{api_base}/open-apis/im/v1/images",
                raw_body=upload_body,
                headers=upload_headers,
                content_type=upload_content_type,
                timeout_sec=30,
                allow_hosts=_allowed_api_hosts(binding.domain),
                policy=STANDARD_OUTBOUND_POLICY,
            )
        except SafeIOHTTPError as exc:
            if resolution.installation is not None:
                self._installation_manager.mark_api_error(
                    resolution.installation.installation_id,
                    error_code=exc.reason,
                    status_code=exc.status_code,
                    details={"phase": "image_upload"},
                )
            logger.warning("Feishu image upload failed: status=%s", exc.status_code)
            return
        image_key = str(
            (upload_payload.get("data") or {}).get("image_key", "") or ""
        ).strip()
        if upload_payload.get("code", 0) != 0 or not image_key:
            if resolution.installation is not None:
                self._installation_manager.mark_api_error(
                    resolution.installation.installation_id,
                    error_code=str(upload_payload.get("msg", "unknown") or "unknown"),
                    status_code=200,
                    details={"phase": "image_upload"},
                )
            logger.warning(
                "Feishu image upload failed: %s",
                upload_payload.get("msg", "unknown"),
            )
            return
        message_payload = {
            "content": json.dumps({"image_key": image_key}, ensure_ascii=False),
            "msg_type": "image",
        }
        thread_id = str(ctx.get("thread_id", "") or "").strip()
        if thread_id:
            send_url = f"{api_base}/open-apis/im/v1/messages/{thread_id}/reply"
        else:
            send_url = f"{api_base}/open-apis/im/v1/messages?receive_id_type=chat_id"
            message_payload["receive_id"] = channel_id
        try:
            safe_request_json(
                method="POST",
                url=send_url,
                json_body=message_payload,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                },
                content_type="application/json; charset=utf-8",
                timeout_sec=30,
                allow_hosts=_allowed_api_hosts(binding.domain),
                policy=STANDARD_OUTBOUND_POLICY,
            )
        except SafeIOHTTPError as exc:
            if resolution.installation is not None:
                self._installation_manager.mark_api_error(
                    resolution.installation.installation_id,
                    error_code=exc.reason,
                    status_code=exc.status_code,
                    details={"phase": "image_send"},
                )
            logger.warning("Feishu image send failed: status=%s", exc.status_code)
        if caption:
            await self.send_message(
                channel_id,
                caption,
                delivery_context=ctx,
            )

    async def prime_bot_identity(self) -> None:
        try:
            await self._fetch_bot_open_id(
                account_id=self._bound_account_id
                or str(self.config.feishu_account_id or "").strip()
                or str(self.config.feishu_default_account_id or "").strip(),
                workspace_id=str(self.config.feishu_workspace_id or "").strip(),
                allow_degrade=True,
            )
        except Exception as exc:
            logger.debug("Feishu bot identity fetch failed: %s", exc)
