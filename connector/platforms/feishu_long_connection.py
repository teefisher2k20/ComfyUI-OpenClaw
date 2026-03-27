"""
Feishu long-connection client (F67).

This keeps the event-normalization path shared with the webhook adapter so
transport choice does not change router or delivery behavior.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Optional

from ..config import ConnectorConfig
from ..router import CommandRouter
from .feishu_webhook import FeishuWebhookServer

logger = logging.getLogger(__name__)


def _import_feishu_sdk():
    # CRITICAL: keep this optional import lazy; CI and unit tests must remain
    # runnable without the Feishu SDK installed.
    try:
        import lark_oapi as sdk  # type: ignore
    except ModuleNotFoundError:
        try:
            import larksuiteoapi as sdk  # type: ignore
        except ModuleNotFoundError:
            return None
    return sdk


class FeishuLongConnectionClient(FeishuWebhookServer):
    def __init__(self, config: ConnectorConfig, router: CommandRouter):
        super().__init__(config, router)
        self._ws_client: Any = None
        self._run_task: Optional[asyncio.Task] = None

    async def start(self):
        if not self.config.feishu_app_id or not self.config.feishu_app_secret:
            logger.info(
                "Feishu long-connection disabled "
                "(OPENCLAW_CONNECTOR_FEISHU_APP_ID / APP_SECRET missing)"
            )
            return
        sdk = _import_feishu_sdk()
        if sdk is None:
            logger.warning(
                "Feishu SDK not installed. Skipping long-connection adapter."
            )
            return
        await self.prime_bot_identity()
        self._ws_client = self._build_ws_client(sdk)
        starter = getattr(self._ws_client, "start", None)
        if not callable(starter):
            logger.error("Feishu SDK client does not expose a start() method.")
            self._ws_client = None
            return
        logger.info("Starting Feishu long-connection client (%s)", self.config.feishu_domain)
        maybe = self._start_client(starter)
        if inspect.isawaitable(maybe):
            self._run_task = asyncio.create_task(maybe)

    async def stop(self):
        if self._run_task:
            self._run_task.cancel()
            try:
                await self._run_task
            except asyncio.CancelledError:
                pass
        stopper = getattr(self._ws_client, "stop", None)
        if callable(stopper):
            maybe = stopper()
            if inspect.isawaitable(maybe):
                await maybe

    def _build_ws_client(self, sdk):
        domain = getattr(getattr(sdk, "Domain", object()), "Lark", None)
        if str(self.config.feishu_domain or "").strip().lower() != "lark":
            domain = getattr(getattr(sdk, "Domain", object()), "Feishu", domain)
        kwargs = {
            "app_id": self.config.feishu_app_id,
            "app_secret": self.config.feishu_app_secret,
        }
        if domain is not None:
            kwargs["domain"] = domain
        ws_cls = getattr(sdk, "WSClient", None)
        if ws_cls is None:
            raise RuntimeError("Feishu SDK missing WSClient")
        return ws_cls(**kwargs)

    def _start_client(self, starter):
        try:
            return starter(event_handler=self._handle_long_connection_event)
        except TypeError:
            return starter(self._handle_long_connection_event)

    async def _handle_long_connection_event(self, payload: Any):
        if hasattr(payload, "to_dict"):
            payload = payload.to_dict()
        if not isinstance(payload, dict):
            return
        await self.process_event_payload(payload)
