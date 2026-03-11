"""
F25 Phase B: Automation Payload Composer Service.

Generates safe, validated payload drafts for:
- /openclaw/triggers/fire
- /openclaw/webhook/submit

This service is generate-only and never executes workflows.
"""

import copy
import json
import logging
import os
from typing import Any, Dict, List, Optional

from .llm_client import LLMClient
from .reasoning_redaction import get_redacted_reasoning_debug
from .templates import is_template_allowed

try:
    from .tool_calling import (
        TOOL_CALLING_AVAILABLE,
        TRIGGER_TOOL_SCHEMA,
        WEBHOOK_TOOL_SCHEMA,
        extract_tool_call_by_name,
        validate_trigger_request,
        validate_webhook_request,
    )
except Exception:
    TOOL_CALLING_AVAILABLE = False

logger = logging.getLogger("ComfyUI-OpenClaw.services.automation_composer")

MAX_INTENT_LEN = 4000


class AutomationComposerService:
    """Compose validated automation payload drafts without side effects."""

    def __init__(self):
        self.llm_client = LLMClient()
        self._last_reasoning_debug: Any = None

    def _get_request_llm_client(self):
        # CRITICAL: Assist compose service is held by long-lived route handlers.
        # Refresh the default LLMClient per request so UI-saved provider/key updates
        # take effect without backend restart. Keep injected fakes intact for tests.
        if isinstance(self.llm_client, LLMClient):
            self.llm_client = LLMClient()
        return self.llm_client

    def consume_last_reasoning_debug(self) -> Any:
        debug_payload = self._last_reasoning_debug
        self._last_reasoning_debug = None
        return debug_payload

    def compose_payload(
        self,
        *,
        kind: str,
        template_id: str,
        intent: str,
        inputs_hint: Optional[Dict[str, Any]] = None,
        profile_id: Optional[str] = None,
        require_approval: Optional[bool] = None,
        trace_id: Optional[str] = None,
        callback: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized_kind = str(kind or "").strip().lower()
        self._last_reasoning_debug = None
        if normalized_kind not in {"trigger", "webhook"}:
            raise ValueError("kind must be 'trigger' or 'webhook'")

        if not isinstance(template_id, str) or not template_id.strip():
            raise ValueError("template_id is required")
        template_id = template_id.strip()
        if len(template_id) > 64:
            raise ValueError("template_id exceeds max length (64)")
        if not is_template_allowed(template_id):
            raise ValueError(f"template_id '{template_id}' not found")

        if not isinstance(intent, str) or not intent.strip():
            raise ValueError("intent is required")
        if len(intent) > MAX_INTENT_LEN:
            raise ValueError(f"intent exceeds {MAX_INTENT_LEN} chars")

        if inputs_hint is None:
            inputs_hint = {}
        if not isinstance(inputs_hint, dict):
            raise ValueError("inputs_hint must be an object")

        fallback_payload = self._build_fallback_payload(
            kind=normalized_kind,
            template_id=template_id,
            inputs_hint=inputs_hint,
            profile_id=profile_id,
            require_approval=require_approval,
            trace_id=trace_id,
            callback=callback,
        )

        warnings: List[str] = []
        used_tool_calling = False

        if (
            TOOL_CALLING_AVAILABLE
            and os.getenv("OPENCLAW_ENABLE_TOOL_CALLING", "0") == "1"
        ):
            candidate = self._try_tool_call_compose(
                kind=normalized_kind,
                template_id=template_id,
                profile_id=profile_id,
                intent=intent,
                inputs_hint=inputs_hint,
                fallback_payload=fallback_payload,
            )
            if candidate.get("payload") is not None:
                used_tool_calling = True
                warnings.extend(candidate.get("warnings", []))
                return {
                    "kind": normalized_kind,
                    "payload": candidate["payload"],
                    "warnings": warnings,
                    "used_tool_calling": used_tool_calling,
                }
            warnings.extend(candidate.get("warnings", []))

        validated = self._validate_payload(normalized_kind, fallback_payload)

        return {
            "kind": normalized_kind,
            "payload": validated,
            "warnings": warnings,
            "used_tool_calling": used_tool_calling,
        }

    def _build_fallback_payload(
        self,
        *,
        kind: str,
        template_id: str,
        inputs_hint: Dict[str, Any],
        profile_id: Optional[str],
        require_approval: Optional[bool],
        trace_id: Optional[str],
        callback: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        payload: Dict[str, Any]
        if kind == "trigger":
            payload = {
                "template_id": template_id,
                "inputs": inputs_hint,
                "require_approval": bool(require_approval),
            }
            if trace_id is not None:
                payload["trace_id"] = trace_id
            if callback is not None:
                payload["callback"] = callback
            return payload

        payload = {
            "version": 1,
            "template_id": template_id,
            "profile_id": profile_id or "default",
            "inputs": inputs_hint,
        }
        if trace_id is not None:
            payload["trace_id"] = trace_id
        if callback is not None:
            payload["callback"] = callback
        return payload

    def _try_tool_call_compose(
        self,
        *,
        kind: str,
        template_id: str,
        profile_id: Optional[str],
        intent: str,
        inputs_hint: Dict[str, Any],
        fallback_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        if kind == "trigger":
            schema = TRIGGER_TOOL_SCHEMA
            tool_name = "openclaw_trigger_request"
        else:
            schema = WEBHOOK_TOOL_SCHEMA
            tool_name = "openclaw_webhook_request"

        warnings: List[str] = []

        try:
            try:
                from .schema_sanitizer import sanitize_tools

                tools = sanitize_tools([schema])
            except Exception:
                tools = [schema]

            system = self._build_system_prompt(kind, template_id, profile_id)
            user = self._build_user_prompt(intent, inputs_hint)
            # IMPORTANT: resolve client at request time to avoid stale provider/key.
            llm_client = self._get_request_llm_client()

            response = llm_client.complete(
                system=system,
                user_message=user,
                tools=tools,
                tool_choice="auto",
            )
            self._last_reasoning_debug = get_redacted_reasoning_debug(
                response.get("raw", {})
            )

            tool_args, tool_error = extract_tool_call_by_name(
                response.get("raw", {}), tool_name
            )
            if tool_error:
                warnings.append(f"tool_call_fallback: {tool_error}")
                return {"payload": None, "warnings": warnings}

            candidate = self._merge_tool_args(
                kind=kind,
                fallback_payload=fallback_payload,
                tool_args=tool_args or {},
            )
            validated = self._validate_payload(kind, candidate)
            return {"payload": validated, "warnings": warnings}

        except Exception as e:
            logger.warning(f"F25 compose tool-call fallback: {e}")
            warnings.append(f"tool_call_fallback: {e}")
            return {"payload": None, "warnings": warnings}

    def _merge_tool_args(
        self,
        *,
        kind: str,
        fallback_payload: Dict[str, Any],
        tool_args: Dict[str, Any],
    ) -> Dict[str, Any]:
        merged = copy.deepcopy(fallback_payload)
        if not isinstance(tool_args, dict):
            return merged

        if isinstance(tool_args.get("inputs"), dict):
            merged["inputs"] = tool_args.get("inputs")

        if kind == "trigger":
            if isinstance(tool_args.get("require_approval"), bool):
                merged["require_approval"] = tool_args.get("require_approval")
            if "trace_id" in tool_args:
                merged["trace_id"] = tool_args.get("trace_id")
            if isinstance(tool_args.get("callback"), dict):
                merged["callback"] = tool_args.get("callback")
            return merged

        if "version" in tool_args:
            merged["version"] = tool_args.get("version")
        if "job_id" in tool_args:
            merged["job_id"] = tool_args.get("job_id")
        if "trace_id" in tool_args:
            merged["trace_id"] = tool_args.get("trace_id")
        if isinstance(tool_args.get("callback"), dict):
            merged["callback"] = tool_args.get("callback")
        return merged

    def _validate_payload(self, kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if kind == "trigger":
            validated, error = validate_trigger_request(payload)
        else:
            validated, error = validate_webhook_request(payload)

        if error or validated is None:
            raise ValueError(error or "invalid compose payload")

        return validated

    @staticmethod
    def _build_system_prompt(
        kind: str, template_id: str, profile_id: Optional[str]
    ) -> str:
        if kind == "trigger":
            return (
                "You are composing a SAFE draft payload for /openclaw/triggers/fire. "
                "Return only tool arguments. Never include secrets. Keep template_id unchanged. "
                f"Target template_id: {template_id}."
            )

        return (
            "You are composing a SAFE draft payload for /openclaw/webhook/submit. "
            "Return only tool arguments. Never include secrets. Keep template_id/profile_id unchanged unless missing. "
            f"Target template_id: {template_id}. Target profile_id: {profile_id or 'default'}."
        )

    @staticmethod
    def _build_user_prompt(intent: str, inputs_hint: Dict[str, Any]) -> str:
        return (
            "Compose a payload draft from this intent and hints.\\n"
            f"Intent: {intent}\\n"
            f"Inputs hint: {json.dumps(inputs_hint, ensure_ascii=False)}"
        )
