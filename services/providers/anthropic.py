import json
import logging
from typing import Any, Dict, List, Optional

try:
    from ..provider_errors import ProviderHTTPError
    from ..retry_after import parse_retry_after_body, parse_retry_after_header
    from ..safe_io import (
        STANDARD_OUTBOUND_POLICY,
        SafeIOHTTPError,
        SSRFError,
        safe_request_json,
    )
except ImportError:
    from services.provider_errors import ProviderHTTPError
    from services.retry_after import parse_retry_after_body, parse_retry_after_header
    from services.safe_io import (
        STANDARD_OUTBOUND_POLICY,
        SafeIOHTTPError,
        SSRFError,
        safe_request_json,
    )

logger = logging.getLogger("ComfyUI-OpenClaw.services.providers.anthropic")

# Default Anthropic API version
ANTHROPIC_API_VERSION = "2023-06-01"


def _parse_error_body_dict(body: Optional[str]) -> Optional[Dict[str, Any]]:
    if not body:
        return None
    try:
        parsed = json.loads(body)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def build_chat_request(
    messages: List[Dict[str, Any]],
    model: str,
    system: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> Dict[str, Any]:
    """Build request payload for Anthropic Messages API."""
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }

    # Temperature is optional for Anthropic
    if temperature is not None:
        payload["temperature"] = temperature

    # System prompt is a top-level field in Anthropic API
    if system:
        payload["system"] = system

    return payload


def make_request(
    base_url: str,
    api_key: str,
    messages: List[Dict[str, Any]],
    model: str,
    system: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    timeout: float = 120.0,
    allow_hosts: Optional[set[str]] = None,
    allow_any_public_host: bool = False,
    allow_loopback_hosts: Optional[set[str]] = None,
    allow_insecure_base_url: bool = False,
) -> Dict[str, Any]:
    """
    Make a request to Anthropic /v1/messages endpoint.

    Returns: {"text": str, "raw": dict}
    """
    # Build endpoint URL (S65: safe_io handles normalization)
    endpoint = f"{base_url.rstrip('/')}/v1/messages"

    # Build request payload
    payload = build_chat_request(messages, model, system, temperature, max_tokens)

    # Build headers (Anthropic uses x-api-key, not Bearer)
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_API_VERSION,
    }

    try:
        # S65: Enforce restricted outbound policy (HTTPS, standard ports)
        # safe_request_json handles SSRF checks, DNS pinning, and redirects.
        raw = safe_request_json(
            method="POST",
            url=endpoint,
            json_body=payload,
            headers=headers,
            timeout_sec=int(timeout),
            policy=STANDARD_OUTBOUND_POLICY,
            allow_hosts=allow_hosts,
            allow_any_public_host=allow_any_public_host,
            allow_loopback_hosts=allow_loopback_hosts,
            allow_insecure_base_url=allow_insecure_base_url,
        )

        # Extract text from response
        text = ""
        if "content" in raw and len(raw["content"]) > 0:
            for block in raw["content"]:
                if block.get("type") == "text":
                    text += block.get("text", "")

        return {"text": text, "raw": raw}

    except SafeIOHTTPError as e:
        error_body = _parse_error_body_dict(e.body)
        retry_after = parse_retry_after_header(e.headers)
        if retry_after is None:
            retry_after = parse_retry_after_body(error_body)

        logger.error(f"Anthropic API error: {e}")
        raise ProviderHTTPError(
            status_code=e.status_code,
            message=str(e),
            provider="anthropic",
            model=model,
            retry_after=retry_after,
            headers=e.headers,
            body=error_body if error_body is not None else e.body,
        )

    except RuntimeError as e:
        params = str(e)
        status_code = 500
        import re

        m = re.search(r"HTTP error (\d+)", params)
        if m:
            status_code = int(m.group(1))

        logger.error(f"Anthropic API error: {e}")
        raise ProviderHTTPError(
            status_code=status_code,
            message=str(e),
            provider="anthropic",
            model=model,
            retry_after=None,
        )

    except SSRFError as e:
        logger.error(f"Anthropic SSRF blocked: {e}")
        raise RuntimeError(f"Security policy blocked request: {e}")

    except Exception as e:
        logger.error(f"Anthropic unexpected error: {e}")
        raise RuntimeError(f"API request failed: {e}")


def build_vision_message(
    text_prompt: str,
    image_base64: str,
    image_media_type: str = "image/png",
) -> Dict[str, Any]:
    """Build a message with vision content for Anthropic API."""
    return {
        "role": "user",
        "content": [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image_media_type,
                    "data": image_base64,
                },
            },
            {
                "type": "text",
                "text": text_prompt,
            },
        ],
    }
