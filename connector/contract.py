"""
Connector Contract (F29).
Shared data models for request/response.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class CommandRequest:
    platform: str  # "telegram" | "discord"
    sender_id: str
    channel_id: str
    username: str
    message_id: str
    text: str
    timestamp: float
    workspace_id: str = ""
    thread_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CommandResponse:
    text: str
    files: List[str] = field(default_factory=list)  # Local paths to upload
    buttons: List[dict] = field(default_factory=list)  # Simple quick replies


class Platform:
    """Abstract base class for chat platforms."""

    async def start(self):
        """Start the platform connection/polling."""
        pass

    async def stop(self):
        """Stop/cleanup."""
        pass

    async def send_image(
        self,
        channel_id: str,
        image_data: bytes,
        filename: str = "image.png",
        caption: Optional[str] = None,
        delivery_context: Optional[Dict[str, Any]] = None,
    ):
        """Send an image to the channel."""
        pass

    async def send_message(
        self,
        channel_id: str,
        text: str,
        delivery_context: Optional[Dict[str, Any]] = None,
    ):
        """Send a text message to the channel."""
        pass
