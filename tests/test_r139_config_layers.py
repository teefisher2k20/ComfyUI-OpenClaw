import unittest

from services.config_layers import (
    SOURCE_DEFAULT,
    SOURCE_ENV,
    SOURCE_PERSISTED,
    SOURCE_RUNTIME_OVERRIDE,
    clear_runtime_overrides,
    get_preferred_env_value,
    get_runtime_overrides,
    resolve_layered_config,
    set_runtime_overrides,
)


class TestR139ConfigLayers(unittest.TestCase):
    def setUp(self):
        clear_runtime_overrides("llm")

    def tearDown(self):
        clear_runtime_overrides("llm")

    def test_get_preferred_env_value_prefers_primary(self):
        value, used_legacy = get_preferred_env_value(
            "OPENCLAW_LLM_PROVIDER",
            "MOLTBOT_LLM_PROVIDER",
            env={
                "OPENCLAW_LLM_PROVIDER": "openai",
                "MOLTBOT_LLM_PROVIDER": "anthropic",
            },
        )
        self.assertEqual(value, "openai")
        self.assertFalse(used_legacy)

    def test_get_preferred_env_value_uses_legacy_when_primary_missing(self):
        value, used_legacy = get_preferred_env_value(
            "OPENCLAW_LLM_PROVIDER",
            "MOLTBOT_LLM_PROVIDER",
            env={"MOLTBOT_LLM_PROVIDER": "anthropic"},
        )
        self.assertEqual(value, "anthropic")
        self.assertTrue(used_legacy)

    def test_resolve_layered_config_precedence(self):
        # defaults < persisted < runtime_override < env
        effective, sources = resolve_layered_config(
            ordered_keys=["provider"],
            defaults={"provider": "openai"},
            persisted={"provider": "gemini"},
            runtime_overrides={"provider": "openrouter"},
            env_getter=lambda _k: "anthropic",
        )
        self.assertEqual(effective["provider"], "anthropic")
        self.assertEqual(sources["provider"], SOURCE_ENV)

    def test_resolve_layered_config_without_env(self):
        effective, sources = resolve_layered_config(
            ordered_keys=["provider"],
            defaults={"provider": "openai"},
            persisted={"provider": "gemini"},
            runtime_overrides={"provider": "openrouter"},
            env_getter=lambda _k: None,
        )
        self.assertEqual(effective["provider"], "openrouter")
        self.assertEqual(sources["provider"], SOURCE_RUNTIME_OVERRIDE)

        effective, sources = resolve_layered_config(
            ordered_keys=["provider"],
            defaults={"provider": "openai"},
            persisted={"provider": "gemini"},
            runtime_overrides={},
            env_getter=lambda _k: None,
        )
        self.assertEqual(effective["provider"], "gemini")
        self.assertEqual(sources["provider"], SOURCE_PERSISTED)

        effective, sources = resolve_layered_config(
            ordered_keys=["provider"],
            defaults={"provider": "openai"},
            persisted={},
            runtime_overrides={},
            env_getter=lambda _k: None,
        )
        self.assertEqual(effective["provider"], "openai")
        self.assertEqual(sources["provider"], SOURCE_DEFAULT)

    def test_runtime_override_registry_merge_and_clear(self):
        current = set_runtime_overrides("llm", {"provider": "openai", "model": "x"})
        self.assertEqual(current["provider"], "openai")
        self.assertEqual(current["model"], "x")

        current = set_runtime_overrides("llm", {"model": None, "timeout_sec": 120})
        self.assertEqual(current["provider"], "openai")
        self.assertNotIn("model", current)
        self.assertEqual(current["timeout_sec"], 120)

        snapshot = get_runtime_overrides("llm")
        self.assertEqual(snapshot["provider"], "openai")
        self.assertEqual(snapshot["timeout_sec"], 120)

        clear_runtime_overrides("llm", keys=["provider"])
        snapshot = get_runtime_overrides("llm")
        self.assertNotIn("provider", snapshot)
        self.assertIn("timeout_sec", snapshot)

        clear_runtime_overrides("llm")
        self.assertEqual(get_runtime_overrides("llm"), {})


if __name__ == "__main__":
    unittest.main()
