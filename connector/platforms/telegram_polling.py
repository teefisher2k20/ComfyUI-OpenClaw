"""
Telegram Polling Platform (F29 Remediation).
Long-polling implementation for Telegram Bot API.
"""

import asyncio
import logging
import time
from typing import Optional

from ..config import ConnectorConfig
from ..contract import CommandRequest, CommandResponse
from ..router import CommandRouter
from ..state import ConnectorState

logger = logging.getLogger(__name__)


def _import_aiohttp():
    try:
        import aiohttp  # type: ignore
    except ModuleNotFoundError:
        return None
    return aiohttp


class TelegramPolling:
    def __init__(self, config: ConnectorConfig, router: CommandRouter):
        self.config = config
        self.router = router
        self.state_store = ConnectorState(path=self.config.state_path)
        self.token = config.telegram_bot_token
        self.base_url = f"https://api.telegram.org/bot{self.token}"

        # Remediation: Load offset from persistent state
        self.offset = self.state_store.get_offset("telegram")
        self.session = None

    async def start(self):
        aiohttp = _import_aiohttp()
        if aiohttp is None:
            logger.warning("aiohttp not installed. Skipping Telegram adapter.")
            return

        if not self.token:
            logger.warning("Telegram token not configured. Skipping.")
            return

        logger.info(f"Starting Telegram Polling (offset={self.offset})...")
        async with aiohttp.ClientSession() as self.session:
            while True:
                try:
                    await self._poll_once()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Telegram poll error: {e}")
                    await asyncio.sleep(5)

    async def _poll_once(self):
        url = f"{self.base_url}/getUpdates"
        params = {"offset": self.offset, "timeout": 30}

        async with self.session.get(url, params=params) as resp:
            if resp.status != 200:
                # IMPORTANT (debuggability): Telegram frequently returns actionable details in the body
                # for non-200 responses (e.g. 409 conflict: "terminated by other getUpdates request",
                # or "webhook is active"). Log the response text in debug mode to speed up diagnosis.
                try:
                    body = await resp.text()
                except Exception:
                    body = ""
                if body:
                    logger.error(f"Telegram API Error {resp.status}: {body}")
                else:
                    logger.error(f"Telegram API Error {resp.status}")
                await asyncio.sleep(5)
                return

            data = await resp.json()
            if not data.get("ok"):
                # Telegram sometimes returns `ok=false` with a useful description even on 200.
                # Keep logs concise, but include enough context to fix config issues quickly.
                desc = (
                    data.get("description") or data.get("error_code") or "unknown_error"
                )
                logger.warning(f"Telegram API returned ok=false: {desc}")
                return

            updates = data.get("result", [])
            if self.config.debug and not updates:
                logger.debug("Telegram poll OK (no updates). offset=%s", self.offset)
            for update in updates:
                next_offset = update["update_id"] + 1
                if next_offset > self.offset:
                    self.offset = next_offset
                    # Remediation: Persist offset
                    self.state_store.set_offset("telegram", self.offset)

                await self._process_update(update)

    async def _process_update(self, update: dict):
        # Telegram update shapes vary by chat type and sender mode.
        # - Normal groups/DMs: `message`
        # - Edited messages: `edited_message`
        # - Channels: `channel_post` / `edited_channel_post`
        #
        # IMPORTANT (recurring support issue):
        # If users say "DM works but group/channel does nothing" AND connector logs show no
        # `DEBUG raw message`, it's often because updates are arriving under `channel_post`
        # (or `sender_chat` anonymous posts) which older code ignored.
        message = (
            update.get("message")
            or update.get("edited_message")
            or update.get("channel_post")
            or update.get("edited_channel_post")
        )
        if not message or "text" not in message:
            return

        chat_id = message["chat"]["id"]
        # `from` may be missing for channel posts; `sender_chat` is used for anonymous admins.
        from_obj = message.get("from") or {}
        sender_chat = message.get("sender_chat") or {}
        user_id = from_obj.get("id")
        username = from_obj.get("username") or sender_chat.get("username") or "unknown"
        text = message["text"]

        # Security Check
        is_allowed = False
        if isinstance(user_id, int) and user_id in self.config.telegram_allowed_users:
            is_allowed = True
        if chat_id in self.config.telegram_allowed_chats:
            is_allowed = True

        if not is_allowed and self.config.debug:
            logger.debug(
                f"Untrusted Telegram message user={user_id} chat={chat_id} (will require approval)"
            )

        # Build Request
        req = CommandRequest(
            platform="telegram",
            # If `user_id` is missing (channel posts), fall back to chat_id so allowlisting by
            # `OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_CHATS` still works deterministically.
            sender_id=str(user_id) if user_id is not None else str(chat_id),
            channel_id=str(chat_id),
            username=username,
            message_id=str(message["message_id"]),
            text=text,
            timestamp=time.time(),
        )

        try:
            resp = await self.router.handle(req)
            await self._send_response(chat_id, resp)
        except Exception as e:
            logger.exception(f"Error handling command: {e}")
            await self._send_response(
                chat_id, CommandResponse(text="[Error] Internal processing error.")
            )

    async def _send_response(self, chat_id: int, resp: CommandResponse):
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": chat_id,
            # Remediation: Plain text only, no parse_mode
            "text": resp.text,
        }
        try:
            async with self.session.post(url, json=payload) as r:
                if r.status != 200:
                    logger.error(
                        f"Failed to send Telegram response: {r.status} {await r.text()}"
                    )
        except Exception as e:
            logger.error(f"Telegram send exception: {e}")

    async def send_image(
        self,
        channel_id: str,
        image_data: bytes,
        filename: str = "image.png",
        caption: Optional[str] = None,
        delivery_context: Optional[dict] = None,
    ):
        """Send photo via Telegram sendPhoto."""
        if not self.session:
            return

        import aiohttp  # Lazy import safe here as we have session

        url = f"{self.base_url}/sendPhoto"
        data = aiohttp.FormData()
        data.add_field("chat_id", channel_id)
        if caption:
            data.add_field("caption", caption)

        data.add_field("photo", image_data, filename=filename, content_type="image/png")

        try:
            async with self.session.post(url, data=data) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    logger.error(f"Telegram send_image failed: {resp.status} {err}")
        except Exception as e:
            logger.error(f"Telegram send_image error: {e}")

    async def send_message(
        self,
        channel_id: str,
        text: str,
        delivery_context: Optional[dict] = None,
    ):
        """Send text message."""
        if not self.session:
            return

        # Reuse internal logic logic but public
        # Using simplified direct call
        url = f"{self.base_url}/sendMessage"
        payload = {"chat_id": channel_id, "text": text}
        try:
            async with self.session.post(url, json=payload) as r:
                if r.status != 200:
                    err = await r.text()
                    logger.error(f"Telegram send_message failed: {r.status} {err}")
        except Exception as e:
            logger.error(f"Telegram send_message error: {e}")
