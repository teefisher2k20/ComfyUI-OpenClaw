import asyncio
import contextlib
import json
import logging
from typing import Any, Dict, Optional

from aiohttp import web

try:
    from ..services.access_control import require_admin_token
    from ..services.async_utils import run_in_thread
    from ..services.automation_composer import AutomationComposerService
    from ..services.planner import PlannerService
    from ..services.planner_registry import get_planner_registry
    from ..services.rate_limit import check_rate_limit
    from ..services.reasoning_redaction import (
        audit_reasoning_reveal,
        resolve_reasoning_reveal,
        sanitize_operator_payload,
    )
    from ..services.refiner import RefinerService
except ImportError:
    # Fallback for ComfyUI's non-package loader or ad-hoc imports.
    from services.access_control import require_admin_token
    from services.async_utils import run_in_thread
    from services.automation_composer import AutomationComposerService
    from services.planner import PlannerService
    from services.planner_registry import get_planner_registry
    from services.rate_limit import check_rate_limit
    from services.reasoning_redaction import (
        audit_reasoning_reveal,
        resolve_reasoning_reveal,
        sanitize_operator_payload,
    )
    from services.refiner import RefinerService

# R98: Endpoint Metadata
if __package__ and "." in __package__:
    from ..services.endpoint_manifest import (
        AuthTier,
        RiskTier,
        RoutePlane,
        endpoint_metadata,
    )
else:
    from services.endpoint_manifest import (
        AuthTier,
        RiskTier,
        RoutePlane,
        endpoint_metadata,
    )

logger = logging.getLogger("ComfyUI-OpenClaw.api.assist")

# Payload size limits (character count for strings, base64 length for images)
MAX_REQUIREMENTS_LEN = 8000
MAX_STYLE_LEN = 2000
MAX_IMAGE_B64_LEN = 5 * 1024 * 1024  # ~5MB base64 string length
MAX_STREAM_DELTA_CHARS = 256
MAX_STREAM_PREVIEW_CHARS = 16_000
STREAM_KEEPALIVE_SEC = 1.0


def _planner_profiles_payload() -> Dict[str, Any]:
    registry = get_planner_registry()
    return {
        "profiles": [
            {
                "id": profile.id,
                "label": profile.label,
                "description": profile.description,
                "version": profile.version,
            }
            for profile in registry.list_profiles()
        ],
        "default_profile": registry.get_default_profile_id(),
    }


class AssistHandlers:
    def __init__(self):
        self.planner = PlannerService()
        self.refiner = RefinerService()
        self.composer = AutomationComposerService()

    async def _require_admin_and_rate_limit(
        self, request: web.Request
    ) -> Optional[web.Response]:
        authorized, _err_msg = require_admin_token(request)
        if not authorized:
            return web.json_response({"error": "Unauthorized"}, status=401)
        if not check_rate_limit(request, "admin"):
            return web.json_response({"error": "Rate limit exceeded"}, status=429)
        return None

    async def _parse_json_body(
        self, request: web.Request
    ) -> tuple[Optional[dict], Optional[web.Response]]:
        try:
            data = await request.json()
        except Exception:
            return None, web.json_response({"error": "Invalid JSON"}, status=400)
        if not isinstance(data, dict):
            return None, web.json_response(
                {"error": "JSON object required"}, status=400
            )
        return data, None

    def _validate_planner_payload(
        self, data: dict
    ) -> tuple[Optional[dict], Optional[web.Response]]:
        registry = get_planner_registry()
        profile = data.get("profile", registry.get_default_profile_id())
        requirements = data.get("requirements", "")
        style = data.get("style_directives", "")
        seed = data.get("seed", 0)

        if not isinstance(profile, str):
            return None, web.json_response(
                {"error": "profile must be string"}, status=400
            )
        if not registry.get_profile(profile):
            return None, web.json_response(
                {"error": f"Unknown profile: {profile}"}, status=400
            )
        if not isinstance(requirements, str):
            return None, web.json_response(
                {"error": "requirements must be string"}, status=400
            )
        if not isinstance(style, str):
            return None, web.json_response(
                {"error": "style_directives must be string"}, status=400
            )
        if len(requirements) > MAX_REQUIREMENTS_LEN:
            return None, web.json_response(
                {"error": f"requirements exceeds {MAX_REQUIREMENTS_LEN} chars"},
                status=400,
            )
        if len(style) > MAX_STYLE_LEN:
            return None, web.json_response(
                {"error": f"style_directives exceeds {MAX_STYLE_LEN} chars"}, status=400
            )
        try:
            seed = int(seed)
        except Exception:
            seed = 0
        return {
            "profile": profile,
            "requirements": requirements,
            "style_directives": style,
            "seed": seed,
        }, None

    def _validate_refiner_payload(
        self, data: dict
    ) -> tuple[Optional[dict], Optional[web.Response]]:
        image_b64 = data.get("image_b64", "")
        orig_pos = data.get("orig_positive", "")
        orig_neg = data.get("orig_negative", "")
        issue = data.get("issue", "Fix issues")
        params_json = data.get("params_json", "{}")
        goal = data.get("goal", "Fix issues")

        if not isinstance(image_b64, str) or not image_b64:
            return None, web.json_response({"error": "image_b64 required"}, status=400)
        if len(image_b64) > MAX_IMAGE_B64_LEN:
            return None, web.json_response(
                {"error": f"image_b64 exceeds {MAX_IMAGE_B64_LEN // 1024 // 1024}MB"},
                status=400,
            )
        for key, value in (
            ("orig_positive", orig_pos),
            ("orig_negative", orig_neg),
            ("issue", issue),
            ("params_json", params_json),
            ("goal", goal),
        ):
            if not isinstance(value, str):
                return None, web.json_response(
                    {"error": f"{key} must be string"}, status=400
                )
        if len(orig_pos) > MAX_REQUIREMENTS_LEN or len(orig_neg) > MAX_REQUIREMENTS_LEN:
            return None, web.json_response({"error": "Prompt too long"}, status=400)

        return {
            "image_b64": image_b64,
            "orig_positive": orig_pos,
            "orig_negative": orig_neg,
            "issue": issue,
            "params_json": params_json,
            "goal": goal,
        }, None

    def _finalize_operator_payload(
        self,
        request: web.Request,
        *,
        target: str,
        payload: Dict[str, Any],
        service: Any,
    ) -> Dict[str, Any]:
        admin_allowed, _ = require_admin_token(request)
        reveal = resolve_reasoning_reveal(request, admin_authorized=admin_allowed)
        audit_reasoning_reveal(request, target=target, decision=reveal)

        final_payload = sanitize_operator_payload(payload)
        consume_debug = getattr(service, "consume_last_reasoning_debug", None)
        reasoning_debug = consume_debug() if callable(consume_debug) else None
        if reveal["allowed"] and reasoning_debug not in (None, {}, []):
            final_payload = dict(final_payload)
            final_payload["debug"] = {"reasoning": reasoning_debug}
        return final_payload

    @staticmethod
    def _sse_frame(event: str, payload: Dict[str, Any]) -> bytes:
        return (
            f"event: {event}\n"
            f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
        ).encode("utf-8")

    async def _write_sse_event(
        self, response: web.StreamResponse, event: str, payload: Dict[str, Any]
    ) -> bool:
        try:
            await response.write(self._sse_frame(event, payload))
            return True
        except (ConnectionError, RuntimeError):
            return False

    async def _assist_stream_session(
        self,
        request: web.Request,
        *,
        kind: str,
        worker_fn,
        worker_kwargs: Dict[str, Any],
    ) -> web.StreamResponse:
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
        await response.prepare(request)

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        preview_chars = 0

        def emit(event: str, payload: Dict[str, Any]) -> None:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, (event, payload))
            except RuntimeError:
                pass

        def on_text_delta(delta: str) -> None:
            nonlocal preview_chars
            if not isinstance(delta, str) or not delta:
                return
            remaining = MAX_STREAM_PREVIEW_CHARS - preview_chars
            if remaining <= 0:
                return
            clipped = delta[: min(remaining, MAX_STREAM_DELTA_CHARS)]
            if not clipped:
                return
            preview_chars += len(clipped)
            emit("delta", {"text": clipped, "preview_chars": preview_chars})

        async def runner() -> None:
            emit("ready", {"ok": True, "kind": kind, "mode": "sse"})
            emit(
                "stage", {"phase": "dispatch", "message": "Dispatching assist request"}
            )
            try:
                call_kwargs = dict(worker_kwargs)
                call_kwargs["on_text_delta"] = on_text_delta
                result = await run_in_thread(worker_fn, **call_kwargs)
                emit(
                    "stage",
                    {"phase": "finalize", "message": "Parsing and validating output"},
                )
                if kind == "planner":
                    pos, neg, params = result
                    final_payload = {
                        "positive": pos,
                        "negative": neg,
                        "params": params,
                    }
                elif kind == "refiner":
                    new_pos, new_neg, patch, rationale = result
                    final_payload = {
                        "refined_positive": new_pos,
                        "refined_negative": new_neg,
                        "param_patch": patch,
                        "rationale": rationale,
                    }
                else:
                    final_payload = {"result": result}
                service = (
                    self.planner
                    if kind == "planner"
                    else self.refiner if kind == "refiner" else self.composer
                )
                final_payload = self._finalize_operator_payload(
                    request,
                    target=f"assist.{kind}.stream",
                    payload=final_payload,
                    service=service,
                )
                emit(
                    "final",
                    {
                        "ok": True,
                        "kind": kind,
                        "result": final_payload,
                        "streaming": {
                            "preview_chars": preview_chars,
                            "preview_truncated": preview_chars
                            >= MAX_STREAM_PREVIEW_CHARS,
                        },
                    },
                )
            except Exception as e:
                logger.exception("Assist streaming API failed (%s)", kind)
                emit(
                    "error",
                    {"ok": False, "kind": kind, "error": "Internal server error"},
                )
            finally:
                emit("__done__", {})

        runner_task = asyncio.create_task(runner())

        try:
            while True:
                try:
                    event, payload = await asyncio.wait_for(
                        queue.get(), timeout=STREAM_KEEPALIVE_SEC
                    )
                except asyncio.TimeoutError:
                    if runner_task.done():
                        break
                    if not await self._write_sse_event(
                        response, "keepalive", {"ok": True}
                    ):
                        break
                    continue

                if event == "__done__":
                    break
                if not await self._write_sse_event(response, event, payload):
                    break
        finally:
            if not runner_task.done():
                runner_task.cancel()
                with contextlib.suppress(BaseException):
                    await runner_task
        return response

    @endpoint_metadata(
        auth=AuthTier.ADMIN,
        risk=RiskTier.LOW,
        summary="List planner profiles",
        description="Returns Prompt Planner profiles from the active registry.",
        audit="assist.planner_profiles",
        plane=RoutePlane.ADMIN,
    )
    async def planner_profiles_handler(self, request):
        auth_resp = await self._require_admin_and_rate_limit(request)
        if auth_resp:
            return auth_resp
        return web.json_response(_planner_profiles_payload())

    @endpoint_metadata(
        auth=AuthTier.ADMIN,
        risk=RiskTier.MEDIUM,
        summary="Run planner",
        description="Generate prompts from requirements via LLM.",
        audit="assist.planner",
        plane=RoutePlane.ADMIN,
    )
    async def planner_handler(self, request):
        """
        POST /openclaw/assist/planner (legacy: /moltbot/assist/planner)
        JSON: { profile, requirements, style_directives, seed }
        """
        # Security: Admin Token required
        auth_resp = await self._require_admin_and_rate_limit(request)
        if auth_resp:
            return auth_resp
        data, error_resp = await self._parse_json_body(request)
        if error_resp:
            return error_resp
        assert data is not None
        payload, payload_err = self._validate_planner_payload(data)
        if payload_err:
            return payload_err
        assert payload is not None

        try:
            # Run sync LLM call in thread pool to avoid blocking event loop
            pos, neg, params = await run_in_thread(
                self.planner.plan_generation,
                payload["profile"],
                payload["requirements"],
                payload["style_directives"],
                payload["seed"],
            )

            return web.json_response(
                self._finalize_operator_payload(
                    request,
                    target="assist.planner",
                    payload={"positive": pos, "negative": neg, "params": params},
                    service=self.planner,
                )
            )

        except Exception as e:
            logger.exception("Planner API failed")
            return web.json_response({"error": "Internal server error"}, status=500)

    @endpoint_metadata(
        auth=AuthTier.ADMIN,
        risk=RiskTier.MEDIUM,
        summary="Run refiner",
        description="Refine prompt/parameters based on feedback.",
        audit="assist.refiner",
        plane=RoutePlane.ADMIN,
    )
    async def refiner_handler(self, request):
        """
        POST /openclaw/assist/refiner (legacy: /moltbot/assist/refiner)
        JSON: { image_b64, orig_positive, orig_negative, issue, params_json, goal }
        """
        # Security checks
        auth_resp = await self._require_admin_and_rate_limit(request)
        if auth_resp:
            return auth_resp
        data, error_resp = await self._parse_json_body(request)
        if error_resp:
            return error_resp
        assert data is not None
        payload, payload_err = self._validate_refiner_payload(data)
        if payload_err:
            return payload_err
        assert payload is not None

        try:
            # Run sync LLM call in thread pool
            new_pos, new_neg, patch, rationale = await run_in_thread(
                self.refiner.refine_prompt,
                **payload,
            )

            return web.json_response(
                self._finalize_operator_payload(
                    request,
                    target="assist.refiner",
                    payload={
                        "refined_positive": new_pos,
                        "refined_negative": new_neg,
                        "param_patch": patch,
                        "rationale": rationale,
                    },
                    service=self.refiner,
                )
            )
        except Exception as e:
            logger.exception("Refiner API failed")
            return web.json_response({"error": "Internal server error"}, status=500)

    @endpoint_metadata(
        auth=AuthTier.ADMIN,
        risk=RiskTier.MEDIUM,
        summary="Run planner (streaming)",
        description="Generate prompts from requirements via LLM with SSE-style incremental updates.",
        audit="assist.planner.stream",
        plane=RoutePlane.ADMIN,
    )
    async def planner_stream_handler(self, request):
        auth_resp = await self._require_admin_and_rate_limit(request)
        if auth_resp:
            return auth_resp
        data, error_resp = await self._parse_json_body(request)
        if error_resp:
            return error_resp
        assert data is not None
        payload, payload_err = self._validate_planner_payload(data)
        if payload_err:
            return payload_err
        assert payload is not None

        return await self._assist_stream_session(
            request,
            kind="planner",
            worker_fn=self.planner.plan_generation,
            worker_kwargs=payload,
        )

    @endpoint_metadata(
        auth=AuthTier.ADMIN,
        risk=RiskTier.MEDIUM,
        summary="Run refiner (streaming)",
        description="Refine prompt/parameters with SSE-style incremental updates.",
        audit="assist.refiner.stream",
        plane=RoutePlane.ADMIN,
    )
    async def refiner_stream_handler(self, request):
        auth_resp = await self._require_admin_and_rate_limit(request)
        if auth_resp:
            return auth_resp
        data, error_resp = await self._parse_json_body(request)
        if error_resp:
            return error_resp
        assert data is not None
        payload, payload_err = self._validate_refiner_payload(data)
        if payload_err:
            return payload_err
        assert payload is not None

        return await self._assist_stream_session(
            request,
            kind="refiner",
            worker_fn=self.refiner.refine_prompt,
            worker_kwargs=payload,
        )

    @endpoint_metadata(
        auth=AuthTier.ADMIN,
        risk=RiskTier.MEDIUM,
        summary="Compose automation payload",
        description="Generate-only automation payload draft for trigger/webhook endpoints.",
        audit="assist.compose",
        plane=RoutePlane.ADMIN,
    )
    async def compose_handler(self, request):
        """
        POST /openclaw/assist/automation/compose (legacy: /moltbot/assist/automation/compose)
        JSON:
        {
          kind: "trigger" | "webhook",
          template_id: str,
          intent: str,
          inputs_hint?: object,
          profile_id?: str,
          require_approval?: bool,
          trace_id?: str,
          callback?: object
        }
        """
        authorized, err_msg = require_admin_token(request)
        if not authorized:
            return web.json_response({"error": "Unauthorized"}, status=401)

        if not check_rate_limit(request, "admin"):
            return web.json_response({"error": "Rate limit exceeded"}, status=429)

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        kind = data.get("kind")
        template_id = data.get("template_id")
        intent = data.get("intent")
        inputs_hint = data.get("inputs_hint", {})
        profile_id = data.get("profile_id")
        require_approval = data.get("require_approval")
        trace_id = data.get("trace_id")
        callback = data.get("callback")

        if not isinstance(kind, str) or kind.strip().lower() not in {
            "trigger",
            "webhook",
        }:
            return web.json_response(
                {"error": "kind must be 'trigger' or 'webhook'"}, status=400
            )
        if not isinstance(template_id, str) or not template_id.strip():
            return web.json_response({"error": "template_id is required"}, status=400)
        if not isinstance(intent, str) or not intent.strip():
            return web.json_response({"error": "intent is required"}, status=400)
        if len(intent) > MAX_REQUIREMENTS_LEN:
            return web.json_response(
                {"error": f"intent exceeds {MAX_REQUIREMENTS_LEN} chars"}, status=400
            )
        if not isinstance(inputs_hint, dict):
            return web.json_response(
                {"error": "inputs_hint must be object"}, status=400
            )
        if profile_id is not None and not isinstance(profile_id, str):
            return web.json_response({"error": "profile_id must be string"}, status=400)
        if require_approval is not None and not isinstance(require_approval, bool):
            return web.json_response(
                {"error": "require_approval must be boolean"}, status=400
            )
        if trace_id is not None and not isinstance(trace_id, str):
            return web.json_response({"error": "trace_id must be string"}, status=400)
        if callback is not None and not isinstance(callback, dict):
            return web.json_response({"error": "callback must be object"}, status=400)

        try:
            result = await run_in_thread(
                self.composer.compose_payload,
                kind=kind,
                template_id=template_id,
                intent=intent,
                inputs_hint=inputs_hint,
                profile_id=profile_id,
                require_approval=require_approval,
                trace_id=trace_id,
                callback=callback,
            )
            return web.json_response(
                self._finalize_operator_payload(
                    request,
                    target="assist.compose",
                    payload={"ok": True, **result},
                    service=self.composer,
                )
            )
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        except Exception:
            logger.exception("Automation compose API failed")
            return web.json_response({"error": "Internal server error"}, status=500)
