import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(os.getcwd())

from connector.config import ConnectorConfig
from connector.contract import CommandRequest
from connector.router import CommandRouter
from services.job_events import JobEvent
from services.reasoning_redaction import (
    REASONING_REVEAL_ENV,
    REASONING_REVEAL_HEADER,
    extract_reasoning_payload,
    resolve_reasoning_reveal,
    sanitize_operator_payload,
)

try:
    import api.assist
    import api.routes

    AIOHTTP_AVAILABLE = True
except Exception:
    AIOHTTP_AVAILABLE = False


class TestS72ReasoningHelper(unittest.TestCase):
    def test_sanitize_operator_payload_drops_reasoning_fields_and_blocks(self):
        payload = {
            "text": "final answer",
            "reasoning": "private chain",
            "meta": {
                "thinking": "secret",
                "content": [
                    {"type": "reasoning", "text": "step 1"},
                    {"type": "text", "text": "visible"},
                ],
            },
        }

        cleaned = sanitize_operator_payload(payload)

        self.assertEqual(cleaned["text"], "final answer")
        self.assertNotIn("reasoning", cleaned)
        self.assertNotIn("thinking", cleaned["meta"])
        self.assertEqual(
            cleaned["meta"]["content"], [{"type": "text", "text": "visible"}]
        )

    def test_extract_reasoning_payload_returns_reasoning_only(self):
        payload = {
            "text": "final answer",
            "meta": {
                "thinking": "secret",
                "content": [
                    {"type": "reasoning", "text": "step 1"},
                    {"type": "text", "text": "visible"},
                ],
            },
        }

        reasoning = extract_reasoning_payload(payload)

        self.assertEqual(reasoning["meta"]["thinking"], "secret")
        self.assertEqual(
            reasoning["meta"]["content"], [{"type": "reasoning", "text": "step 1"}]
        )

    def test_resolve_reasoning_reveal_denies_non_local_posture(self):
        request = SimpleNamespace(headers={REASONING_REVEAL_HEADER: "1"}, query={})
        with (
            patch.dict(
                os.environ,
                {REASONING_REVEAL_ENV: "1", "OPENCLAW_DEPLOYMENT_PROFILE": "public"},
                clear=False,
            ),
            patch(
                "services.reasoning_redaction.get_client_ip", return_value="127.0.0.1"
            ),
        ):
            decision = resolve_reasoning_reveal(request, admin_authorized=True)

        self.assertTrue(decision["requested"])
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "deployment_profile_public")


@unittest.skipUnless(AIOHTTP_AVAILABLE, "aiohttp not available")
class TestS72AssistHandlers(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.handler = api.assist.AssistHandlers()
        self.handler.planner = MagicMock()
        self.handler.refiner = MagicMock()
        self.handler.composer = MagicMock()

    async def test_planner_default_redacts_reasoning(self):
        request = AsyncMock()
        request.query = {}
        request.headers = {}
        request.json = AsyncMock(
            return_value={
                "profile": "SDXL-v1",
                "requirements": "cat",
                "style_directives": "cinematic",
                "seed": 7,
            }
        )
        self.handler.planner.consume_last_reasoning_debug.return_value = {
            "reasoning": "hidden"
        }

        with (
            patch("api.assist.require_admin_token", return_value=(True, None)),
            patch("api.assist.check_rate_limit", return_value=True),
            patch(
                "services.reasoning_redaction.get_client_ip", return_value="127.0.0.1"
            ),
            patch(
                "api.assist.run_in_thread",
                return_value=("pos", "neg", {"steps": 20, "reasoning": "private"}),
            ),
        ):
            resp = await self.handler.planner_handler(request)

        body = json.loads(resp.body)
        self.assertEqual(resp.status, 200)
        self.assertNotIn("reasoning", body["params"])
        self.assertNotIn("debug", body)

    async def test_planner_reveal_allowed_only_with_local_debug_gate(self):
        request = AsyncMock()
        request.query = {}
        request.headers = {REASONING_REVEAL_HEADER: "1"}
        request.json = AsyncMock(
            return_value={
                "profile": "SDXL-v1",
                "requirements": "cat",
                "style_directives": "cinematic",
                "seed": 7,
            }
        )
        self.handler.planner.consume_last_reasoning_debug.return_value = {
            "reasoning": "hidden"
        }

        with (
            patch.dict(os.environ, {REASONING_REVEAL_ENV: "1"}, clear=False),
            patch("api.assist.require_admin_token", return_value=(True, None)),
            patch("api.assist.check_rate_limit", return_value=True),
            patch(
                "services.reasoning_redaction.get_client_ip", return_value="127.0.0.1"
            ),
            patch(
                "api.assist.run_in_thread",
                return_value=("pos", "neg", {"steps": 20, "reasoning": "private"}),
            ),
        ):
            resp = await self.handler.planner_handler(request)

        body = json.loads(resp.body)
        self.assertEqual(resp.status, 200)
        self.assertEqual(body["debug"]["reasoning"]["reasoning"], "hidden")
        self.assertNotIn("reasoning", body["params"])

    async def test_planner_reveal_denied_for_public_profile(self):
        request = AsyncMock()
        request.query = {}
        request.headers = {REASONING_REVEAL_HEADER: "1"}
        request.json = AsyncMock(
            return_value={
                "profile": "SDXL-v1",
                "requirements": "cat",
                "style_directives": "cinematic",
                "seed": 7,
            }
        )
        self.handler.planner.consume_last_reasoning_debug.return_value = {
            "reasoning": "hidden"
        }

        with (
            patch.dict(
                os.environ,
                {REASONING_REVEAL_ENV: "1", "OPENCLAW_DEPLOYMENT_PROFILE": "public"},
                clear=False,
            ),
            patch("api.assist.require_admin_token", return_value=(True, None)),
            patch("api.assist.check_rate_limit", return_value=True),
            patch(
                "services.reasoning_redaction.get_client_ip", return_value="127.0.0.1"
            ),
            patch(
                "api.assist.run_in_thread", return_value=("pos", "neg", {"steps": 20})
            ),
        ):
            resp = await self.handler.planner_handler(request)

        body = json.loads(resp.body)
        self.assertEqual(resp.status, 200)
        self.assertNotIn("debug", body)


class TestS72EventsAndTrace(unittest.IsolatedAsyncioTestCase):
    def test_job_event_default_redacts_reasoning(self):
        evt = JobEvent(
            seq=1,
            event_type="completed",
            prompt_id="p1",
            trace_id="t1",
            data={"status": "ok", "reasoning": "hidden"},
        )

        self.assertNotIn("reasoning", evt.to_dict()["data"])
        self.assertIn('"status":"ok"', evt.to_sse())
        self.assertNotIn("hidden", evt.to_sse())
        self.assertEqual(
            evt.to_dict(include_reasoning=True)["data"]["reasoning"], "hidden"
        )

    @unittest.skipUnless(AIOHTTP_AVAILABLE, "aiohttp not available")
    async def test_trace_handler_redacts_reasoning_by_default(self):
        mock_request = MagicMock()
        mock_request.match_info = {"prompt_id": "p1"}
        mock_request.headers = {}
        mock_request.query = {}

        mock_web = MagicMock()
        mock_record = MagicMock()
        mock_record.to_dict.return_value = {
            "prompt_id": "p1",
            "events": [{"meta": {"reasoning": "hidden", "status": "ok"}}],
        }

        with (
            patch.object(api.routes, "web", mock_web),
            patch.object(
                api.routes,
                "_ensure_observability_deps_ready",
                return_value=(True, None),
            ),
            patch.object(api.routes, "require_admin_token", return_value=(True, None)),
            patch.object(api.routes, "trace_store") as mock_trace_store,
            patch(
                "services.reasoning_redaction.get_client_ip", return_value="127.0.0.1"
            ),
        ):
            mock_trace_store.get.return_value = mock_record
            await api.routes.trace_handler(mock_request)

        args, _ = mock_web.json_response.call_args
        trace = args[0]["trace"]
        self.assertNotIn("reasoning", trace["events"][0]["meta"])


class TestS72CallbackDelivery(unittest.IsolatedAsyncioTestCase):
    async def test_watch_and_deliver_sanitizes_callback_payload(self):
        import services.callback_delivery as callback_delivery

        sent_payloads = []

        async def fake_run_io(func, *args, **kwargs):
            if func is callback_delivery.fetch_history:
                return {"prompt_id": "p1"}
            if func is callback_delivery.safe_request_json:
                sent_payloads.append(args[2])
                return {"ok": True}
            raise AssertionError(f"unexpected func: {func}")

        with (
            patch.object(
                callback_delivery, "run_io_in_thread", side_effect=fake_run_io
            ),
            patch.object(
                callback_delivery.asyncio, "sleep", AsyncMock(return_value=None)
            ),
            patch.object(
                callback_delivery,
                "get_callback_allow_hosts",
                return_value={"example.com"},
            ),
            patch.object(callback_delivery, "get_job_status", return_value="completed"),
            patch.object(
                callback_delivery,
                "extract_images",
                return_value=[
                    {"url": "https://example.com/x.png", "reasoning": "hidden"}
                ],
            ),
            patch.object(
                callback_delivery, "get_job_event_store", return_value=MagicMock()
            ),
            patch.object(callback_delivery.trace_store, "add_event", return_value=None),
        ):
            await callback_delivery._watch_and_deliver(
                "p1",
                {"url": "https://example.com/hook"},
                trace_id="trace-1",
            )

        self.assertEqual(len(sent_payloads), 1)
        self.assertNotIn("reasoning", sent_payloads[0]["outputs"][0])


class TestS72Connector(unittest.IsolatedAsyncioTestCase):
    async def test_trace_command_redacts_reasoning_fields(self):
        client = MagicMock()
        client.get_trace = AsyncMock(
            return_value={
                "ok": True,
                "data": {"events": [{"meta": {"reasoning": "hidden", "status": "ok"}}]},
            }
        )
        config = ConnectorConfig()
        config.admin_users = ["admin1"]
        config.admin_token = "token"
        router = CommandRouter(config, client)

        req = CommandRequest(
            platform="telegram",
            channel_id="c1",
            sender_id="admin1",
            username="tester",
            message_id="m1",
            text="/trace p1",
            timestamp=1.0,
        )

        resp = await router.handle(req)

        self.assertIn("status", resp.text)
        self.assertNotIn("hidden", resp.text)


if __name__ == "__main__":
    unittest.main()
