import json
import logging
from typing import Any, Callable, Dict, List, Optional

try:
    from ..provider_errors import ProviderHTTPError
    from ..retry_after import parse_retry_after_body, parse_retry_after_header
    from ..safe_io import (
        STANDARD_OUTBOUND_POLICY,
        SafeIOHTTPError,
        SSRFError,
        safe_request_json,
        safe_request_text_stream,
    )
except ImportError:
    from services.provider_errors import ProviderHTTPError
    from services.retry_after import parse_retry_after_body, parse_retry_after_header
    from services.safe_io import (
        STANDARD_OUTBOUND_POLICY,
        SafeIOHTTPError,
        SSRFError,
        safe_request_json,
        safe_request_text_stream,
    )

logger = logging.getLogger("ComfyUI-OpenClaw.services.providers.openai_compat")


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
    temperature: float = 0.7,
    max_tokens: int = 4096,
    tools: Optional[List[Dict[str, Any]]] = None,  # R39: Optional tools
    tool_choice: Optional[str] = None,  # R39: Optional tool_choice
) -> Dict[str, Any]:
    """Build request payload for OpenAI-compatible chat completions."""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    # R39: Sanitize and include tools if provided
    if tools:
        try:
            # CRITICAL: package-relative import must be tried first for ComfyUI
            # custom_nodes package loading; fallback absolute import is for local tools.
            try:
                from ..schema_sanitizer import get_sanitization_summary, sanitize_tools
            except ImportError:
                from services.schema_sanitizer import (
                    get_sanitization_summary,
                    sanitize_tools,
                )

            sanitized = sanitize_tools(tools, profile="openai_compat")
            if sanitized:
                payload["tools"] = sanitized
                # Log summary (never log full schemas)
                summary = get_sanitization_summary(sanitized)
                logger.debug(
                    f"R39: Sanitized {summary['count']} tools ({summary['size_bytes']} bytes): "
                    f"{summary['function_names']}"
                )
            if tool_choice:
                payload["tool_choice"] = tool_choice
        except ImportError:
            logger.warning(
                "R39: schema_sanitizer not available, passing tools unsanitized"
            )
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice

    return payload


def make_request(
    base_url: str,
    api_key: Optional[str],
    messages: List[Dict[str, Any]],
    model: str,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    timeout: float = 120.0,
    tools: Optional[List[Dict[str, Any]]] = None,  # R39: Optional tools
    tool_choice: Optional[str] = None,  # R39: Optional tool_choice
    allow_hosts: Optional[set[str]] = None,
    allow_any_public_host: bool = False,
    allow_loopback_hosts: Optional[set[str]] = None,
    allow_insecure_base_url: bool = False,
) -> Dict[str, Any]:
    """
    Make a request to an OpenAI-compatible /chat/completions endpoint.

    Returns: {"text": str, "raw": dict}
    """
    # Build endpoint URL (S65: safe_io handles normalization)
    endpoint = f"{base_url.rstrip('/')}/chat/completions"

    # Build request payload
    payload = build_chat_request(
        messages, model, temperature, max_tokens, tools, tool_choice
    )

    # Build headers
    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

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
        if "choices" in raw and len(raw["choices"]) > 0:
            choice = raw["choices"][0]
            if "message" in choice and "content" in choice["message"]:
                text = choice["message"]["content"]

        return {"text": text, "raw": raw}

    except SafeIOHTTPError as e:
        error_body = _parse_error_body_dict(e.body)
        retry_after = parse_retry_after_header(e.headers)
        if retry_after is None:
            retry_after = parse_retry_after_body(error_body)

        logger.error(f"OpenAI-compat API error: {e}")
        raise ProviderHTTPError(
            status_code=e.status_code,
            message=str(e),
            provider="openai_compat",
            model=model,
            retry_after=retry_after,
            headers=e.headers,
            body=error_body if error_body is not None else e.body,
        )

    except RuntimeError as e:
        # S65/R14: Attempt to reconstruct ProviderHTTPError from safe_io exception

        # Try to parse status code
        params = str(e)
        status_code = 500
        import re

        m = re.search(r"HTTP error (\d+)", params)
        if m:
            status_code = int(m.group(1))

        logger.error(f"OpenAI-compat API error: {e}")

        raise ProviderHTTPError(
            status_code=status_code,
            message=str(e),
            provider="openai_compat",
            model=model,
            retry_after=None,
        )

    except SSRFError as e:
        logger.error(f"OpenAI-compat SSRF blocked: {e}")
        raise RuntimeError(f"Security policy blocked request: {e}")

    except Exception as e:
        logger.error(f"OpenAI-compat unexpected error: {e}")
        raise RuntimeError(f"API request failed: {e}")


def make_request_stream(
    base_url: str,
    api_key: Optional[str],
    messages: List[Dict[str, Any]],
    model: str,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    timeout: float = 120.0,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[str] = None,
    allow_hosts: Optional[set[str]] = None,
    allow_any_public_host: bool = False,
    allow_loopback_hosts: Optional[set[str]] = None,
    allow_insecure_base_url: bool = False,
    on_text_delta: Optional[Callable[[str], None]] = None,
    max_preview_chars: int = 16000,
) -> Dict[str, Any]:
    """
    Best-effort streaming request to OpenAI-compatible /chat/completions endpoint.

    Parses SSE `data:` lines and emits incremental text deltas when present.
    Falls back to final accumulated text result shape `{"text": str, "raw": dict}`.
    """
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    payload = build_chat_request(
        messages, model, temperature, max_tokens, tools, tool_choice
    )
    payload["stream"] = True

    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    full_text_parts: List[str] = []
    chunk_count = 0
    saw_done = False

    def _emit_delta(delta: str) -> None:
        if not delta:
            return
        nonlocal full_text_parts
        current_len = sum(len(p) for p in full_text_parts)
        if current_len >= max_preview_chars:
            return
        clipped = delta[: max_preview_chars - current_len]
        if not clipped:
            return
        full_text_parts.append(clipped)
        if on_text_delta:
            try:
                on_text_delta(clipped)
            except Exception:
                # Callback errors must not break provider parsing.
                logger.debug("Ignoring on_text_delta callback error", exc_info=True)

    try:
        for line in safe_request_text_stream(
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
        ):
            line = line.rstrip("\r\n")
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue

            data_str = line[5:].strip()
            if not data_str:
                continue
            if data_str == "[DONE]":
                saw_done = True
                break

            try:
                payload_obj = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            chunk_count += 1
            choices = payload_obj.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            content = delta.get("content")

            if isinstance(content, str):
                _emit_delta(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text = part.get("text")
                        if isinstance(text, str):
                            _emit_delta(text)

        return {
            "text": "".join(full_text_parts),
            "raw": {
                "stream": True,
                "provider": "openai_compat",
                "chunks": chunk_count,
                "saw_done": saw_done,
            },
        }

    except SafeIOHTTPError as e:
        error_body = _parse_error_body_dict(e.body)
        retry_after = parse_retry_after_header(e.headers)
        if retry_after is None:
            retry_after = parse_retry_after_body(error_body)

        logger.error(f"OpenAI-compat streaming API error: {e}")
        raise ProviderHTTPError(
            status_code=e.status_code,
            message=str(e),
            provider="openai_compat",
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

        logger.error(f"OpenAI-compat streaming API error: {e}")
        raise ProviderHTTPError(
            status_code=status_code,
            message=str(e),
            provider="openai_compat",
            model=model,
            retry_after=None,
        )
    except SSRFError as e:
        logger.error(f"OpenAI-compat streaming SSRF blocked: {e}")
        raise RuntimeError(f"Security policy blocked request: {e}")
    except Exception as e:
        logger.error(f"OpenAI-compat streaming unexpected error: {e}")
        raise RuntimeError(f"API request failed: {e}")


def build_vision_message(
    text_prompt: str,
    image_base64: str,
    image_media_type: str = "image/png",
) -> Dict[str, Any]:
    """Build a message with vision content for OpenAI-compatible APIs."""
    return {
        "role": "user",
        "content": [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{image_media_type};base64,{image_base64}",
                },
            },
            {
                "type": "text",
                "text": text_prompt,
            },
        ],
    }
