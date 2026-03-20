import json
import logging
import os
from typing import Any, Callable, Dict, Optional, Tuple

from .llm_client import LLMClient
from .llm_output import extract_json_object, filter_allowed_keys, sanitize_string
from .reasoning_redaction import get_redacted_reasoning_debug

try:
    from ..models.schemas import GenerationParams
except ImportError:
    from models.schemas import GenerationParams

from .metrics import metrics

logger = logging.getLogger("ComfyUI-OpenClaw.services.refiner")

# F25: Tool calling support (optional)
try:
    from .tool_calling import (
        REFINER_TOOL_SCHEMA,
        extract_tool_call_by_name,
        validate_refiner_output,
    )

    TOOL_CALLING_AVAILABLE = True
except ImportError:
    TOOL_CALLING_AVAILABLE = False

ALLOWED_PATCH_KEYS = {
    "steps",
    "cfg",
    "width",
    "height",
    "sampler_name",
    "scheduler",
    "seed",
}


class RefinerService:
    """Core logic for Prompt Refiner (F21)."""

    def __init__(self):
        self.llm_client = LLMClient()
        self._last_reasoning_debug: Any = None

    def _get_request_llm_client(self):
        # CRITICAL: refresh the default LLMClient per request.
        # Refiner shares the same long-lived assist handler lifecycle as Planner; keeping
        # the startup client causes stale provider/key state after UI Save.
        # Preserve injected fakes by only rotating real LLMClient instances.
        # IMPORTANT: do not mutate the stored long-lived service client when resolving
        # a fresh default request client; that write is unnecessary shared-state churn.
        if isinstance(self.llm_client, LLMClient):
            return LLMClient()
        return self.llm_client

    def consume_last_reasoning_debug(self) -> Any:
        debug_payload = self._last_reasoning_debug
        self._last_reasoning_debug = None
        return debug_payload

    def refine_prompt(
        self,
        image_b64: str,
        orig_positive: str,
        orig_negative: str,
        issue: str,
        params_json: str = "{}",
        goal: str = "Fix the issues",
        on_text_delta: Optional[Callable[[str], None]] = None,
    ) -> Tuple[str, str, Dict[str, Any], str]:
        """
        Refine prompt based on image + issue.

        Args:
            image_b64: Base64 dictionary or string (if header). Client handles encoding.
            orig_positive: Original positive prompt.
            orig_negative: Original negative prompt.
            issue: Description of the issue (e.g. "hands bad").
            params_json: JSON string of current params.
            goal: Goal of refinement.

        Returns:
            (refined_positive, refined_negative, param_patch_dict, rationale)
        """
        metrics.increment("refiner_calls")
        self._last_reasoning_debug = None

        # 2. Parse Baseline Params
        try:
            base_params = json.loads(params_json) if params_json.strip() else {}
        except json.JSONDecodeError:
            base_params = {}

        # 3. Construct System Prompt
        system_prompt = f"""
You are an expert stable diffusion technician.
Your task is to CRITIQUE the provided image against the original prompts and identified issue: "{issue}".
Then REFINE the prompt and optionally suggest parameter tweaks (CFG, steps, size) to fix it.

Goal: {goal}

Output JSON only:
{{
  "refined_positive": "string",
  "refined_negative": "string",
  "param_patch": {{
    "steps": int, "cfg": float, "width": int, "height": int, ...
  }},
  "rationale": "Explanation of changes"
}}

Allowed patch keys: steps, cfg, width, height, sampler_name, scheduler, seed.
Ignore others.
"""

        # 4. Construct User Message
        user_message = f"""
Original Positive: {orig_positive}
Original Negative: {orig_negative}
Current Params: {json.dumps(base_params)}
Issue: {issue}
"""

        try:
            # IMPORTANT: resolve client at request time so UI-saved provider/key changes
            # apply without restarting ComfyUI.
            llm_client = self._get_request_llm_client()
            # F25: Optional tool calling (OpenAI-compat only; fallback to JSON parsing)
            use_tool_calling = (
                TOOL_CALLING_AVAILABLE
                and os.getenv("OPENCLAW_ENABLE_TOOL_CALLING", "0") == "1"
            )

            logger.info(f"Refining prompt for issue: {issue}")

            if use_tool_calling:
                logger.info("F25: Using tool calling for refiner")
                try:
                    from .schema_sanitizer import sanitize_tools

                    tools = sanitize_tools([REFINER_TOOL_SCHEMA])
                except ImportError:
                    tools = [REFINER_TOOL_SCHEMA]

                response = llm_client.complete(
                    system=system_prompt,
                    user_message=user_message,
                    image_base64=image_b64,
                    tools=tools,
                    tool_choice="auto",
                )
                self._last_reasoning_debug = get_redacted_reasoning_debug(
                    response.get("raw", {})
                )

                tool_args, tool_error = extract_tool_call_by_name(
                    response.get("raw", {}),
                    "openclaw_refiner_output",
                )
                if not tool_error:
                    validated, validation_error = validate_refiner_output(tool_args)
                    if not validation_error:
                        metrics.increment("refiner_tool_calls_success")
                        refined_pos = sanitize_string(
                            validated.get("refined_positive"), default=orig_positive
                        )
                        refined_neg = sanitize_string(
                            validated.get("refined_negative"), default=orig_negative
                        )
                        raw_patch = validated.get("param_patch", {})
                        rationale = sanitize_string(
                            validated.get("rationale"), default="No rationale provided."
                        )

                        if not isinstance(raw_patch, dict):
                            raw_patch = {}

                        filtered_patch = filter_allowed_keys(
                            raw_patch, ALLOWED_PATCH_KEYS
                        )

                        merged = base_params.copy()
                        merged.update(filtered_patch)
                        validated_full = GenerationParams.from_dict(merged)
                        full_dict = validated_full.dict()
                        final_patch = {
                            k: full_dict[k]
                            for k in filtered_patch.keys()
                            if k in full_dict
                        }

                        return refined_pos, refined_neg, final_patch, rationale

                # Tool calling failed; fall back to JSON parsing
                content = response.get("text", "")
                data = extract_json_object(content)
            else:
                # 5. Call Vision LLM (traditional JSON)
                response = llm_client.complete(
                    system=system_prompt,
                    user_message=user_message,
                    image_base64=image_b64,
                    streaming=on_text_delta is not None,
                    on_text_delta=on_text_delta,
                )
                self._last_reasoning_debug = get_redacted_reasoning_debug(
                    response.get("raw", {})
                )

                content = response.get("text", "")
                data = extract_json_object(content)

            if data is None:
                logger.warning("Failed to extract JSON from LLM response")
                metrics.increment("errors")
                return (
                    orig_positive,
                    orig_negative,
                    {},
                    "Error: Failed to parse LLM response",
                )

            # Extract & Sanitize
            refined_pos = sanitize_string(
                data.get("refined_positive"), default=orig_positive
            )
            refined_neg = sanitize_string(
                data.get("refined_negative"), default=orig_negative
            )
            raw_patch = data.get("param_patch", {})
            rationale = sanitize_string(
                data.get("rationale"), default="No rationale provided."
            )

            # 6. Apply & Validate Patch
            if not isinstance(raw_patch, dict):
                raw_patch = {}

            filtered_patch = filter_allowed_keys(raw_patch, ALLOWED_PATCH_KEYS)

            # Merge with base and validate
            merged = base_params.copy()
            merged.update(filtered_patch)

            validated_full = GenerationParams.from_dict(merged)
            full_dict = validated_full.dict()

            # Output only the keys that were in the patch (clamped)
            final_patch = {
                k: full_dict[k] for k in filtered_patch.keys() if k in full_dict
            }

            return refined_pos, refined_neg, final_patch, rationale

        except Exception:
            metrics.increment("errors")
            logger.error("Refiner failed", exc_info=True)
            raise
