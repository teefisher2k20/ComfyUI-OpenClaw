import json
import unittest
from unittest.mock import patch


class _PlannerDynamicFakeLLMClient:
    _next_id = 0

    def __init__(self):
        type(self)._next_id += 1
        self.instance_id = type(self)._next_id
        self.calls = []

    def complete(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {
            "text": json.dumps(
                {
                    "positive_prompt": f"p-{self.instance_id}",
                    "negative_prompt": "",
                    "params": {
                        "width": 1024,
                        "height": 1024,
                        "steps": 20,
                        "cfg": 7.0,
                        "sampler_name": "euler",
                        "scheduler": "normal",
                    },
                }
            ),
            "raw": {},
        }


class _RefinerDynamicFakeLLMClient:
    _next_id = 0

    def __init__(self):
        type(self)._next_id += 1
        self.instance_id = type(self)._next_id
        self.calls = []

    def complete(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {
            "text": json.dumps(
                {
                    "refined_positive": f"rp-{self.instance_id}",
                    "refined_negative": "",
                    "param_patch": {"steps": 25},
                    "rationale": "ok",
                }
            ),
            "raw": {},
        }


class _InjectedFakeLLMClient:
    def __init__(self):
        self.calls = []

    def complete(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {
            "text": json.dumps(
                {
                    "positive_prompt": "custom",
                    "negative_prompt": "",
                    "params": {"width": 1024, "height": 1024},
                }
            ),
            "raw": {},
        }


class TestAssistLLMClientHotReload(unittest.TestCase):
    def test_planner_refreshes_default_llm_client_per_request(self):
        import services.planner as planner_mod

        _PlannerDynamicFakeLLMClient._next_id = 0
        with patch.object(planner_mod, "LLMClient", _PlannerDynamicFakeLLMClient):
            planner = planner_mod.PlannerService()
            init_client = planner.llm_client

            pos1, _, _ = planner.plan_generation("SDXL-v1", "req", "style", seed=1)
            pos2, _, _ = planner.plan_generation("SDXL-v1", "req", "style", seed=2)

        self.assertNotEqual(pos1, pos2)
        self.assertIs(planner.llm_client, init_client)
        self.assertEqual(init_client.instance_id, 1)

    def test_refiner_refreshes_default_llm_client_per_request(self):
        import services.refiner as refiner_mod

        _RefinerDynamicFakeLLMClient._next_id = 0
        with patch.object(refiner_mod, "LLMClient", _RefinerDynamicFakeLLMClient):
            refiner = refiner_mod.RefinerService()
            init_client = refiner.llm_client

            rp1, _, _, _ = refiner.refine_prompt(
                image_b64="dummy",
                orig_positive="op",
                orig_negative="on",
                issue="fix",
                params_json=json.dumps({"width": 1024, "height": 1024}),
            )
            rp2, _, _, _ = refiner.refine_prompt(
                image_b64="dummy",
                orig_positive="op",
                orig_negative="on",
                issue="fix",
                params_json=json.dumps({"width": 1024, "height": 1024}),
            )

        self.assertNotEqual(rp1, rp2)
        self.assertIs(refiner.llm_client, init_client)
        self.assertEqual(init_client.instance_id, 1)

    def test_planner_keeps_injected_custom_llm_client(self):
        from services.planner import PlannerService

        planner = PlannerService()
        fake = _InjectedFakeLLMClient()
        planner.llm_client = fake

        pos, _, _ = planner.plan_generation("SDXL-v1", "req", "style", seed=1)

        self.assertEqual(pos, "custom")
        self.assertIs(planner.llm_client, fake)
        self.assertEqual(len(fake.calls), 1)


if __name__ == "__main__":
    unittest.main()
