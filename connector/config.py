"""
Connector Configuration (F29).
Loads environment variables and validates allowlists.
"""

import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set


class CommandClass(str, Enum):
    PUBLIC = "public"  # status, help, tools
    RUN = "run"  # run (subject to approval flow)
    ADMIN = "admin"  # sensitive ops


@dataclass
class CommandPolicy:
    """
    R80: Authorization Matrix.
    Defines who can run what.
    """

    # Default AllowFrom lists (User IDs)
    # If empty for a class, it falls back to role checks (e.g. is_admin, is_trusted)
    allow_from: Dict[CommandClass, Set[str]] = field(default_factory=dict)

    # Command -> Class overrides
    # e.g. {"/custom": CommandClass.ADMIN}
    command_overrides: Dict[str, CommandClass] = field(default_factory=dict)


@dataclass
class ConnectorConfig:
    # OpenClaw Connection
    openclaw_url: str = "http://127.0.0.1:8188"
    admin_token: Optional[str] = None  # To call admin endpoints

    # Results Delivery
    delivery_enabled: bool = True
    delivery_max_images: int = 4
    delivery_max_bytes: int = 10 * 1024 * 1024  # 10MB
    delivery_timeout_sec: int = 600

    # Telegram
    telegram_bot_token: Optional[str] = None
    telegram_allowed_users: List[int] = field(default_factory=list)
    telegram_allowed_chats: List[int] = field(default_factory=list)

    # Discord
    discord_bot_token: Optional[str] = None
    discord_allowed_users: List[str] = field(default_factory=list)
    discord_allowed_channels: List[str] = field(default_factory=list)

    # LINE
    line_channel_secret: Optional[str] = None
    line_channel_access_token: Optional[str] = None
    line_allowed_users: List[str] = field(default_factory=list)
    line_allowed_groups: List[str] = field(default_factory=list)
    line_bind_host: str = "127.0.0.1"
    line_bind_port: int = 8099
    line_webhook_path: str = "/line/webhook"

    # WhatsApp
    whatsapp_access_token: Optional[str] = None
    whatsapp_verify_token: Optional[str] = None
    whatsapp_app_secret: Optional[str] = None  # For signature verification
    whatsapp_phone_number_id: Optional[str] = None
    whatsapp_allowed_users: List[str] = field(default_factory=list)
    whatsapp_bind_host: str = "127.0.0.1"
    whatsapp_bind_port: int = 8098
    whatsapp_webhook_path: str = "/whatsapp/webhook"

    # WeChat Official Account (R74/S31/F43)
    wechat_token: Optional[str] = None
    wechat_app_id: Optional[str] = None
    wechat_app_secret: Optional[str] = None
    wechat_encoding_aes_key: Optional[str] = None  # R82: AES encrypted mode
    wechat_allowed_users: List[str] = field(default_factory=list)
    wechat_bind_host: str = "127.0.0.1"
    wechat_bind_port: int = 8097
    wechat_webhook_path: str = "/wechat/webhook"

    # KakaoTalk (F44 Phase A)
    kakao_enabled: bool = False
    kakao_bind_host: str = "127.0.0.1"
    kakao_bind_port: int = 8096
    kakao_webhook_path: str = "/kakao/webhook"
    kakao_allowed_users: List[str] = field(default_factory=list)

    # Slack (F56 / S67)
    slack_bot_token: Optional[str] = None
    slack_signing_secret: Optional[str] = None
    slack_allowed_users: List[str] = field(default_factory=list)
    slack_allowed_channels: List[str] = field(default_factory=list)
    slack_bind_host: str = "127.0.0.1"
    slack_bind_port: int = 8095
    slack_webhook_path: str = "/slack/events"
    slack_require_mention: bool = True
    slack_reply_in_thread: bool = True
    slack_mode: str = "events"  # F57: events | socket
    slack_app_token: Optional[str] = None  # F57: required in socket mode (xapp-...)
    slack_client_id: Optional[str] = None
    slack_client_secret: Optional[str] = None
    slack_oauth_redirect_uri: Optional[str] = None
    slack_oauth_install_path: str = "/slack/install"
    slack_oauth_callback_path: str = "/slack/oauth/callback"
    slack_oauth_scopes: List[str] = field(
        default_factory=lambda: [
            "app_mentions:read",
            "channels:history",
            "chat:write",
            "files:write",
            "groups:history",
            "im:history",
            "mpim:history",
        ]
    )
    slack_oauth_state_ttl_sec: int = 600

    # Feishu / Lark (F67)
    feishu_app_id: Optional[str] = None
    feishu_app_secret: Optional[str] = None
    feishu_verification_token: Optional[str] = None
    feishu_encrypt_key: Optional[str] = None
    feishu_account_id: Optional[str] = None
    feishu_default_account_id: Optional[str] = None
    feishu_workspace_id: Optional[str] = None
    feishu_workspace_name: Optional[str] = None
    feishu_bindings_json: Optional[str] = None
    feishu_allowed_users: List[str] = field(default_factory=list)
    feishu_allowed_chats: List[str] = field(default_factory=list)
    feishu_bind_host: str = "127.0.0.1"
    feishu_bind_port: int = 8094
    feishu_webhook_path: str = "/feishu/events"
    feishu_callback_path: str = "/feishu/callback"
    feishu_domain: str = "feishu"  # feishu | lark
    feishu_mode: str = "websocket"  # websocket | webhook
    feishu_require_mention: bool = True
    feishu_reply_in_thread: bool = True

    # Privileged Access (ID match across platforms; Telegram Int vs Discord Str handled by router)
    admin_users: List[str] = field(default_factory=list)

    # Media Host (F33)
    public_base_url: Optional[str] = None
    media_path: str = "/media"
    media_ttl_sec: int = 300
    media_max_mb: int = 8

    # Security (F32)
    rate_limit_user_rpm: int = 10  # Requests per minute per user
    rate_limit_channel_rpm: int = 30  # Requests per minute per channel
    max_command_length: int = 4096  # Max characters in a single command
    llm_max_tokens_per_request: int = 1024  # LLM token budget

    # R80: Command Auth Policy
    command_policy: CommandPolicy = field(default_factory=CommandPolicy)

    # Global
    debug: bool = False
    state_path: Optional[str] = None

    def __repr__(self):
        """R117: redact secret/token/key fields in logs and debug output."""
        d = self.__dict__.copy()
        for k in d:
            if "token" in k or "secret" in k or "key" in k:
                if d[k]:
                    d[k] = "***REDACTED***"
        fields = ", ".join(f"{k}={v!r}" for k, v in d.items())
        return f"{self.__class__.__name__}({fields})"


def load_config() -> ConnectorConfig:
    """Load configuration from environment variables."""
    cfg = ConnectorConfig()

    cfg.openclaw_url = os.environ.get(
        "OPENCLAW_CONNECTOR_URL", "http://127.0.0.1:8188"
    ).rstrip("/")
    cfg.admin_token = os.environ.get("OPENCLAW_CONNECTOR_ADMIN_TOKEN")
    cfg.debug = os.environ.get("OPENCLAW_CONNECTOR_DEBUG", "0") == "1"
    cfg.state_path = os.environ.get("OPENCLAW_CONNECTOR_STATE_PATH")

    # Delivery
    cfg.delivery_max_images = int(
        os.environ.get("OPENCLAW_CONNECTOR_DELIVERY_MAX_IMAGES", "4")
    )
    cfg.delivery_max_bytes = int(
        os.environ.get("OPENCLAW_CONNECTOR_DELIVERY_MAX_BYTES", str(10 * 1024 * 1024))
    )
    cfg.delivery_timeout_sec = int(
        os.environ.get("OPENCLAW_CONNECTOR_DELIVERY_TIMEOUT_SEC", "600")
    )

    # Telegram
    cfg.telegram_bot_token = os.environ.get("OPENCLAW_CONNECTOR_TELEGRAM_TOKEN")
    if t_users := os.environ.get("OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_USERS"):
        cfg.telegram_allowed_users = [
            int(u.strip()) for u in t_users.split(",") if u.strip().isdigit()
        ]
    if t_chats := os.environ.get("OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_CHATS"):
        cfg.telegram_allowed_chats = [
            int(u.strip())
            for u in t_chats.split(",")
            if u.strip().lstrip("-").isdigit()
        ]

    # Discord
    cfg.discord_bot_token = os.environ.get("OPENCLAW_CONNECTOR_DISCORD_TOKEN")
    if d_users := os.environ.get("OPENCLAW_CONNECTOR_DISCORD_ALLOWED_USERS"):
        cfg.discord_allowed_users = [u.strip() for u in d_users.split(",") if u.strip()]
    if d_chans := os.environ.get("OPENCLAW_CONNECTOR_DISCORD_ALLOWED_CHANNELS"):
        cfg.discord_allowed_channels = [
            u.strip() for u in d_chans.split(",") if u.strip()
        ]

    # LINE
    cfg.line_channel_secret = os.environ.get("OPENCLAW_CONNECTOR_LINE_CHANNEL_SECRET")
    cfg.line_channel_access_token = os.environ.get(
        "OPENCLAW_CONNECTOR_LINE_CHANNEL_ACCESS_TOKEN"
    )
    if l_users := os.environ.get("OPENCLAW_CONNECTOR_LINE_ALLOWED_USERS"):
        cfg.line_allowed_users = [u.strip() for u in l_users.split(",") if u.strip()]
    if l_groups := os.environ.get("OPENCLAW_CONNECTOR_LINE_ALLOWED_GROUPS"):
        cfg.line_allowed_groups = [u.strip() for u in l_groups.split(",") if u.strip()]

    cfg.line_bind_host = os.environ.get("OPENCLAW_CONNECTOR_LINE_BIND", "127.0.0.1")
    if l_port := os.environ.get("OPENCLAW_CONNECTOR_LINE_PORT"):
        if l_port.isdigit():
            cfg.line_bind_port = int(l_port)
    cfg.line_webhook_path = os.environ.get(
        "OPENCLAW_CONNECTOR_LINE_PATH", "/line/webhook"
    )

    # WhatsApp
    cfg.whatsapp_access_token = os.environ.get(
        "OPENCLAW_CONNECTOR_WHATSAPP_ACCESS_TOKEN"
    )
    cfg.whatsapp_verify_token = os.environ.get(
        "OPENCLAW_CONNECTOR_WHATSAPP_VERIFY_TOKEN"
    )
    cfg.whatsapp_app_secret = os.environ.get("OPENCLAW_CONNECTOR_WHATSAPP_APP_SECRET")
    cfg.whatsapp_phone_number_id = os.environ.get(
        "OPENCLAW_CONNECTOR_WHATSAPP_PHONE_NUMBER_ID"
    )
    if wa_users := os.environ.get("OPENCLAW_CONNECTOR_WHATSAPP_ALLOWED_USERS"):
        cfg.whatsapp_allowed_users = [
            u.strip() for u in wa_users.split(",") if u.strip()
        ]
    cfg.whatsapp_bind_host = os.environ.get(
        "OPENCLAW_CONNECTOR_WHATSAPP_BIND", "127.0.0.1"
    )
    if wa_port := os.environ.get("OPENCLAW_CONNECTOR_WHATSAPP_PORT"):
        if wa_port.isdigit():
            cfg.whatsapp_bind_port = int(wa_port)
    cfg.whatsapp_webhook_path = os.environ.get(
        "OPENCLAW_CONNECTOR_WHATSAPP_PATH", "/whatsapp/webhook"
    )

    # WeChat Official Account (R74/S31/F43)
    cfg.wechat_token = os.environ.get("OPENCLAW_CONNECTOR_WECHAT_TOKEN")
    cfg.wechat_app_id = os.environ.get("OPENCLAW_CONNECTOR_WECHAT_APP_ID")
    cfg.wechat_app_secret = os.environ.get("OPENCLAW_CONNECTOR_WECHAT_APP_SECRET")
    cfg.wechat_encoding_aes_key = os.environ.get(
        "OPENCLAW_CONNECTOR_WECHAT_ENCODING_AES_KEY"
    )
    if wc_users := os.environ.get("OPENCLAW_CONNECTOR_WECHAT_ALLOWED_USERS"):
        cfg.wechat_allowed_users = [u.strip() for u in wc_users.split(",") if u.strip()]
    cfg.wechat_bind_host = os.environ.get("OPENCLAW_CONNECTOR_WECHAT_BIND", "127.0.0.1")
    if wc_port := os.environ.get("OPENCLAW_CONNECTOR_WECHAT_PORT"):
        if wc_port.isdigit():
            cfg.wechat_bind_port = int(wc_port)
    cfg.wechat_webhook_path = os.environ.get(
        "OPENCLAW_CONNECTOR_WECHAT_PATH", "/wechat/webhook"
    )

    # KakaoTalk (F44)
    if os.environ.get("OPENCLAW_CONNECTOR_KAKAO_ENABLED", "").lower() == "true":
        cfg.kakao_enabled = True

    cfg.kakao_bind_host = os.environ.get("OPENCLAW_CONNECTOR_KAKAO_BIND", "127.0.0.1")
    if kp := os.environ.get("OPENCLAW_CONNECTOR_KAKAO_PORT"):
        if kp.isdigit():
            cfg.kakao_bind_port = int(kp)
    cfg.kakao_webhook_path = os.environ.get(
        "OPENCLAW_CONNECTOR_KAKAO_PATH", "/kakao/webhook"
    )
    if ku := os.environ.get("OPENCLAW_CONNECTOR_KAKAO_ALLOWED_USERS"):
        cfg.kakao_allowed_users = [u.strip() for u in ku.split(",") if u.strip()]

    # Slack (F56 / S67)
    cfg.slack_bot_token = os.environ.get("OPENCLAW_CONNECTOR_SLACK_BOT_TOKEN")
    cfg.slack_signing_secret = os.environ.get("OPENCLAW_CONNECTOR_SLACK_SIGNING_SECRET")
    if su := os.environ.get("OPENCLAW_CONNECTOR_SLACK_ALLOWED_USERS"):
        cfg.slack_allowed_users = [u.strip() for u in su.split(",") if u.strip()]
    if sc := os.environ.get("OPENCLAW_CONNECTOR_SLACK_ALLOWED_CHANNELS"):
        cfg.slack_allowed_channels = [u.strip() for u in sc.split(",") if u.strip()]
    cfg.slack_bind_host = os.environ.get("OPENCLAW_CONNECTOR_SLACK_BIND", "127.0.0.1")
    if sp := os.environ.get("OPENCLAW_CONNECTOR_SLACK_PORT"):
        if sp.isdigit():
            cfg.slack_bind_port = int(sp)
    cfg.slack_webhook_path = os.environ.get(
        "OPENCLAW_CONNECTOR_SLACK_PATH", "/slack/events"
    )
    if (
        os.environ.get("OPENCLAW_CONNECTOR_SLACK_REQUIRE_MENTION", "").lower()
        == "false"
    ):
        cfg.slack_require_mention = False
    if (
        os.environ.get("OPENCLAW_CONNECTOR_SLACK_REPLY_IN_THREAD", "").lower()
        == "false"
    ):
        cfg.slack_reply_in_thread = False
    cfg.slack_mode = os.environ.get("OPENCLAW_CONNECTOR_SLACK_MODE", "events").lower()
    cfg.slack_app_token = os.environ.get("OPENCLAW_CONNECTOR_SLACK_APP_TOKEN")
    cfg.slack_client_id = os.environ.get("OPENCLAW_CONNECTOR_SLACK_CLIENT_ID")
    cfg.slack_client_secret = os.environ.get("OPENCLAW_CONNECTOR_SLACK_CLIENT_SECRET")
    cfg.slack_oauth_redirect_uri = os.environ.get(
        "OPENCLAW_CONNECTOR_SLACK_OAUTH_REDIRECT_URI"
    )
    cfg.slack_oauth_install_path = os.environ.get(
        "OPENCLAW_CONNECTOR_SLACK_OAUTH_INSTALL_PATH", "/slack/install"
    )
    cfg.slack_oauth_callback_path = os.environ.get(
        "OPENCLAW_CONNECTOR_SLACK_OAUTH_CALLBACK_PATH", "/slack/oauth/callback"
    )
    if slack_scopes := os.environ.get("OPENCLAW_CONNECTOR_SLACK_OAUTH_SCOPES"):
        parsed_scopes = [
            scope.strip() for scope in slack_scopes.split(",") if scope.strip()
        ]
        if parsed_scopes:
            cfg.slack_oauth_scopes = parsed_scopes
    if slack_oauth_ttl := os.environ.get(
        "OPENCLAW_CONNECTOR_SLACK_OAUTH_STATE_TTL_SEC"
    ):
        if slack_oauth_ttl.isdigit():
            cfg.slack_oauth_state_ttl_sec = max(60, int(slack_oauth_ttl))

    # Feishu / Lark (F67)
    cfg.feishu_app_id = os.environ.get("OPENCLAW_CONNECTOR_FEISHU_APP_ID")
    cfg.feishu_app_secret = os.environ.get("OPENCLAW_CONNECTOR_FEISHU_APP_SECRET")
    cfg.feishu_verification_token = os.environ.get(
        "OPENCLAW_CONNECTOR_FEISHU_VERIFICATION_TOKEN"
    )
    cfg.feishu_encrypt_key = os.environ.get("OPENCLAW_CONNECTOR_FEISHU_ENCRYPT_KEY")
    cfg.feishu_account_id = os.environ.get("OPENCLAW_CONNECTOR_FEISHU_ACCOUNT_ID")
    cfg.feishu_default_account_id = os.environ.get(
        "OPENCLAW_CONNECTOR_FEISHU_DEFAULT_ACCOUNT_ID"
    )
    cfg.feishu_workspace_id = os.environ.get("OPENCLAW_CONNECTOR_FEISHU_WORKSPACE_ID")
    cfg.feishu_workspace_name = os.environ.get(
        "OPENCLAW_CONNECTOR_FEISHU_WORKSPACE_NAME"
    )
    cfg.feishu_bindings_json = os.environ.get("OPENCLAW_CONNECTOR_FEISHU_BINDINGS_JSON")
    if fu := os.environ.get("OPENCLAW_CONNECTOR_FEISHU_ALLOWED_USERS"):
        cfg.feishu_allowed_users = [u.strip() for u in fu.split(",") if u.strip()]
    if fc := os.environ.get("OPENCLAW_CONNECTOR_FEISHU_ALLOWED_CHATS"):
        cfg.feishu_allowed_chats = [u.strip() for u in fc.split(",") if u.strip()]
    cfg.feishu_bind_host = os.environ.get("OPENCLAW_CONNECTOR_FEISHU_BIND", "127.0.0.1")
    if fp := os.environ.get("OPENCLAW_CONNECTOR_FEISHU_PORT"):
        if fp.isdigit():
            cfg.feishu_bind_port = int(fp)
    cfg.feishu_webhook_path = os.environ.get(
        "OPENCLAW_CONNECTOR_FEISHU_PATH", "/feishu/events"
    )
    cfg.feishu_callback_path = os.environ.get(
        "OPENCLAW_CONNECTOR_FEISHU_CALLBACK_PATH", "/feishu/callback"
    )
    cfg.feishu_domain = (
        os.environ.get("OPENCLAW_CONNECTOR_FEISHU_DOMAIN", "feishu").strip() or "feishu"
    )
    cfg.feishu_mode = os.environ.get(
        "OPENCLAW_CONNECTOR_FEISHU_MODE", "websocket"
    ).lower()
    if (
        os.environ.get("OPENCLAW_CONNECTOR_FEISHU_REQUIRE_MENTION", "").lower()
        == "false"
    ):
        cfg.feishu_require_mention = False
    if (
        os.environ.get("OPENCLAW_CONNECTOR_FEISHU_REPLY_IN_THREAD", "").lower()
        == "false"
    ):
        cfg.feishu_reply_in_thread = False

    # Admin
    if admins := os.environ.get("OPENCLAW_CONNECTOR_ADMIN_USERS"):
        cfg.admin_users = [u.strip() for u in admins.split(",") if u.strip()]

    # Security (F32)
    if rpm := os.environ.get("OPENCLAW_CONNECTOR_RATE_LIMIT_USER_RPM"):
        if rpm.isdigit():
            cfg.rate_limit_user_rpm = int(rpm)
    if rpm := os.environ.get("OPENCLAW_CONNECTOR_RATE_LIMIT_CHANNEL_RPM"):
        if rpm.isdigit():
            cfg.rate_limit_channel_rpm = int(rpm)
    if max_len := os.environ.get("OPENCLAW_CONNECTOR_MAX_COMMAND_LENGTH"):
        if max_len.isdigit():
            cfg.max_command_length = int(max_len)

    # Media Host (F33)
    cfg.public_base_url = os.environ.get("OPENCLAW_CONNECTOR_PUBLIC_BASE_URL")
    cfg.media_path = os.environ.get("OPENCLAW_CONNECTOR_MEDIA_PATH", "/media")
    if ttl := os.environ.get("OPENCLAW_CONNECTOR_MEDIA_TTL_SEC"):
        if ttl.isdigit():
            cfg.media_ttl_sec = int(ttl)
    if mb := os.environ.get("OPENCLAW_CONNECTOR_MEDIA_MAX_MB"):
        if mb.isdigit():
            cfg.media_max_mb = int(mb)

    # R80: Command Auth Policy
    import json

    # 1. Overrides (JSON dict)
    if overrides_json := os.environ.get("OPENCLAW_COMMAND_OVERRIDES"):
        try:
            overrides = json.loads(overrides_json)
            if isinstance(overrides, dict):
                for k, v in overrides.items():
                    try:
                        # Normalize command key (lowercase, ensure leading slash)
                        k = k.strip().lower()
                        if not k.startswith("/"):
                            k = "/" + k

                        # Map string value to enum
                        if isinstance(v, str):
                            v = CommandClass(v.lower())
                        cfg.command_policy.command_overrides[k] = v
                    except ValueError:
                        pass  # Invalid enum value, ignore
        except json.JSONDecodeError:
            pass  # Invalid JSON, ignore

    # 2. AllowFrom Lists (start with empty sets)
    # Env vars: OPENCLAW_COMMAND_ALLOW_FROM_ADMIN=user1,user2
    #           OPENCLAW_COMMAND_ALLOW_FROM_RUN=user3
    #           OPENCLAW_COMMAND_ALLOW_FROM_PUBLIC=...
    for cmd_class in CommandClass:
        env_key = f"OPENCLAW_COMMAND_ALLOW_FROM_{cmd_class.value.upper()}"
        if val := os.environ.get(env_key):
            users = {u.strip() for u in val.split(",") if u.strip()}
            if users:
                if cmd_class not in cfg.command_policy.allow_from:
                    cfg.command_policy.allow_from[cmd_class] = set()
                cfg.command_policy.allow_from[cmd_class].update(users)

    return cfg
