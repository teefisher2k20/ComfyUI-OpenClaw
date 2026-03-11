import logging
import os
from typing import Any, Callable, Dict, Optional, Tuple

from .llm_client import LLMClient
from .llm_output import extract_json_object, sanitize_string
from .planner_registry import get_planner_registry
from .reasoning_redaction import get_redacted_reasoning_debug

try:
    from ..models.schemas import GenerationParams
except ImportError:
    from models.schemas import GenerationParams

from .metrics import metrics

# F25: Tool calling support
try:
    from .tool_calling import (
        PLANNER_TOOL_SCHEMA,
        extract_tool_call_by_name,
        validate_planner_output,
    )

    TOOL_CALLING_AVAILABLE = True
except ImportError:
    TOOL_CALLING_AVAILABLE = False

logger = logging.getLogger("ComfyUI-OpenClaw.services.planner")

# Allowed keys
ALLOWED_RESPONSE_KEYS = {"positive_prompt", "negative_prompt", "params"}
ALLOWED_PARAM_KEYS = {"width", "height", "steps", "cfg", "sampler_name", "scheduler"}


class PlannerService:
    """Core logic for Prompt Planner (F8)."""

    def __init__(self):
        self.llm_client = LLMClient()
        self._last_reasoning_debug: Any = None

    def _get_request_llm_client(self):
        # CRITICAL: refresh the default LLMClient per request.
        # Assist handlers are long-lived singletons, so caching the startup client here
        # causes stale provider/key state after UI Save (requires backend restart).
        # Keep custom test fakes/injected clients intact by only rotating real LLMClient.
        if isinstance(self.llm_client, LLMClient):
            self.llm_client = LLMClient()
        return self.llm_client

    def consume_last_reasoning_debug(self) -> Any:
        debug_payload = self._last_reasoning_debug
        self._last_reasoning_debug = None
        return debug_payload

    def plan_generation(
        self,
        profile_id: str,
        requirements: str,
        style_directives: str,
        seed: int = 0,
        on_text_delta: Optional[Callable[[str], None]] = None,
    ) -> Tuple[str, str, Dict[str, Any]]:
        """
        Plan prompt and params via LLM.

        Returns:
            (positive_prompt, negative_prompt, params_dict)
        """
        metrics.increment("planner_calls")
        self._last_reasoning_debug = None
        registry = get_planner_registry()

        # 1. Select Profile
        selected_profile = registry.get_profile(profile_id)
        if not selected_profile:
            # Fallback or error? Node raises ValueError.
            # We'll default to SDXL if unknown, or raise.
            # Raising is safer for explicit API usage.
            raise ValueError(f"Unknown profile: {profile_id}")

        # 2. Construct System Prompt
        system_prompt = registry.render_system_prompt(selected_profile.id)

        # 3. Construct User Message
        user_message = f"""
Requirements: {requirements}
Style: {style_directives}
"""

        try:
            # IMPORTANT: resolve client at request time (not service init time).
            # This keeps Planner aligned with the latest runtime config + server-side key store.
            llm_client = self._get_request_llm_client()
            # F25: Check if tool calling is enabled
            use_tool_calling = (
                TOOL_CALLING_AVAILABLE
                and os.getenv("OPENCLAW_ENABLE_TOOL_CALLING", "0") == "1"
            )

            if use_tool_calling:
                # F25: Tool calling mode
                logger.info(
                    f"F25: Using tool calling for planner (profile {profile_id})"
                )

                # Import sanitizer for schema sanitization
                try:
                    from .schema_sanitizer import sanitize_tools

                    tools = sanitize_tools([PLANNER_TOOL_SCHEMA])
                except ImportError:
                    tools = [PLANNER_TOOL_SCHEMA]

                # Call LLM with tool
                response = llm_client.complete(
                    system_prompt, user_message, tools=tools, tool_choice="auto"
                )
                self._last_reasoning_debug = get_redacted_reasoning_debug(
                    response.get("raw", {})
                )

                # Extract tool call
                tool_args, tool_error = extract_tool_call_by_name(
                    response.get("raw", {}), "openclaw_planner_output"
                )

                if tool_error:
                    logger.warning(
                        f"F25: Tool call extraction failed ({tool_error}), falling back to JSON parsing"
                    )
                    # Fallback to traditional JSON extraction
                    content = response.get("text", "")
                    data = extract_json_object(content)
                else:
                    # Validate tool arguments
                    validated_output, validation_error = validate_planner_output(
                        tool_args
                    )
                    if validation_error:
                        logger.warning(
                            f"F25: Tool validation failed ({validation_error}), falling back"
                        )
                        content = response.get("text", "")
                        data = extract_json_object(content)
                    else:
                        # Success: use validated tool output
                        metrics.increment("planner_tool_calls_success")
                        positive = validated_output["positive"]
                        negative = validated_output.get("negative", "")
                        params_dict = validated_output.get("params", {})
                        params_dict["seed"] = seed
                        return positive, negative, params_dict
            else:
                # Traditional mode: Call LLM normally
                logger.info(f"Sending request to LLM for profile {profile_id}...")
                response = llm_client.complete(
                    system_prompt,
                    user_message,
                    streaming=on_text_delta is not None,
                    on_text_delta=on_text_delta,
                )
                self._last_reasoning_debug = get_redacted_reasoning_debug(
                    response.get("raw", {})
                )

            # Traditional JSON extraction (fallback or default path)
            content = response.get("text", "")
            data = extract_json_object(content)

            if data is None:
                logger.warning(
                    "Failed to extract JSON from LLM response, using defaults"
                )
                metrics.increment("errors")
                return "", "", GenerationParams(seed=seed).dict()

            # 6. Validate & Clamp
            raw_params = data.get("params", {})
            if isinstance(raw_params, dict):
                raw_params = {
                    k: v for k, v in raw_params.items() if k in ALLOWED_PARAM_KEYS
                }
            else:
                raw_params = {}

            # Inject seed
            raw_params["seed"] = seed

            # Validate via schema
            validated_params = GenerationParams.from_dict(raw_params)

            # Extract prompts
            positive = sanitize_string(data.get("positive_prompt"), default="")
            negative = sanitize_string(data.get("negative_prompt"), default="")

            return positive, negative, validated_params.dict()

        except Exception as e:
            metrics.increment("errors")
            logger.error(f"Failed to plan generation: {e}")
            raise e
