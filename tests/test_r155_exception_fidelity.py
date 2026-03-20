import traceback
import unittest
from unittest.mock import MagicMock, patch


class _BoomLLMClient:
    def complete(self, *args, **kwargs):
        raise RuntimeError("boom")


class TestR155ExceptionFidelity(unittest.TestCase):
    def _assert_traceback_contains_frame(
        self, tb, filename_fragment, expected_lineno, expected_line_fragment
    ):
        frames = traceback.extract_tb(tb)
        self.assertTrue(
            any(
                filename_fragment in frame.filename.replace("\\", "/")
                and frame.lineno == expected_lineno
                and expected_line_fragment in (frame.line or "")
                for frame in frames
            ),
            msg="Traceback frames did not include the original failing call site:\n"
            + "\n".join(
                f"{frame.filename}:{frame.lineno}: {frame.line}" for frame in frames
            ),
        )

    def _capture_runtime_error(self, func):
        try:
            func()
        except RuntimeError as exc:
            return exc, exc.__traceback__
        self.fail("RuntimeError was not raised")

    def test_planner_preserves_original_traceback_line(self):
        from services.planner import PlannerService

        planner = PlannerService()
        planner.llm_client = _BoomLLMClient()

        _, tb = self._capture_runtime_error(
            lambda: planner.plan_generation("SDXL-v1", "req", "style", seed=1)
        )

        self._assert_traceback_contains_frame(
            tb, "services/planner.py", 160, "response = llm_client.complete("
        )

    def test_refiner_preserves_original_traceback_line(self):
        from services.refiner import RefinerService

        refiner = RefinerService()
        refiner.llm_client = _BoomLLMClient()

        _, tb = self._capture_runtime_error(
            lambda: refiner.refine_prompt(
                image_b64="dummy",
                orig_positive="op",
                orig_negative="on",
                issue="fix",
                params_json="{}",
            )
        )

        self._assert_traceback_contains_frame(
            tb, "services/refiner.py", 203, "response = llm_client.complete("
        )

    def test_image_to_prompt_preserves_original_traceback_line(self):
        from nodes.image_to_prompt import OpenClawImageToPrompt

        node = OpenClawImageToPrompt()
        node.llm_client = _BoomLLMClient()

        with patch.object(node, "_tensor_to_base64_png", return_value="ZmFrZQ=="):
            _, tb = self._capture_runtime_error(
                lambda: node.generate_prompt(
                    image=object(),
                    goal="goal",
                    detail_level="medium",
                    max_image_side=512,
                )
            )

        self._assert_traceback_contains_frame(
            tb, "nodes/image_to_prompt.py", 112, "response = llm_client.complete("
        )

    def test_api_config_runtime_error_preserves_original_traceback_line(self):
        from api.config import llm_models_handler

        request = MagicMock()
        request.query = {}
        request.remote = "127.0.0.1"

        with (
            patch("api.config.check_rate_limit", return_value=True),
            patch("api.config.require_admin_token", return_value=(True, None)),
            patch("api.config.get_effective_config", return_value=({"provider": "openai"}, {})),
            patch("services.providers.keys.get_api_key_for_provider", return_value="sk-test"),
            patch("api.config.fetch_remote_model_list", side_effect=RuntimeError("boom")),
        ):
            _, tb = self._capture_runtime_error(
                lambda: self._run_async(llm_models_handler(request))
            )

        self._assert_traceback_contains_frame(
            tb, "api/config.py", 490, "models = fetch_remote_model_list("
        )

    def _run_async(self, coro):
        import asyncio

        return asyncio.run(coro)
