import unittest
from unittest.mock import sentinel

from api.route_registrars import (
    build_assist_route_specs,
    build_connector_installation_route_specs,
    build_core_route_specs,
    build_pack_route_specs,
)


class _AssistStub:
    planner_profiles_handler = sentinel.planner_profiles_handler
    planner_handler = sentinel.planner_handler
    planner_stream_handler = sentinel.planner_stream_handler
    refiner_handler = sentinel.refiner_handler
    refiner_stream_handler = sentinel.refiner_stream_handler
    compose_handler = sentinel.compose_handler


class _PacksStub:
    list_packs_handler = sentinel.list_packs_handler
    import_pack_handler = sentinel.import_pack_handler
    export_pack_handler = sentinel.export_pack_handler
    delete_pack_handler = sentinel.delete_pack_handler


class TestR151RouteRegistrars(unittest.TestCase):
    def test_build_core_route_specs_preserves_expected_paths(self):
        handlers = {
            "remote_admin_page_handler": sentinel.remote_admin_page_handler,
            "health_handler": sentinel.health_handler,
            "logs_tail_handler": sentinel.logs_tail_handler,
            "jobs_handler": sentinel.jobs_handler,
            "trace_handler": sentinel.trace_handler,
            "webhook_handler": sentinel.webhook_handler,
            "webhook_submit_handler": sentinel.webhook_submit_handler,
            "webhook_validate_handler": sentinel.webhook_validate_handler,
            "capabilities_handler": sentinel.capabilities_handler,
            "config_get_handler": sentinel.config_get_handler,
            "config_put_handler": sentinel.config_put_handler,
            "llm_test_handler": sentinel.llm_test_handler,
            "llm_chat_handler": sentinel.llm_chat_handler,
            "llm_models_handler": sentinel.llm_models_handler,
            "templates_list_handler": sentinel.templates_list_handler,
            "preflight_handler": sentinel.preflight_handler,
            "inventory_handler": sentinel.inventory_handler,
            "pnginfo_handler": sentinel.pnginfo_handler,
            "list_checkpoints_handler": sentinel.list_checkpoints_handler,
            "create_checkpoint_handler": sentinel.create_checkpoint_handler,
            "get_checkpoint_handler": sentinel.get_checkpoint_handler,
            "delete_checkpoint_handler": sentinel.delete_checkpoint_handler,
            "rewrite_recipes_list_handler": sentinel.rewrite_recipes_list_handler,
            "rewrite_recipe_create_handler": sentinel.rewrite_recipe_create_handler,
            "rewrite_recipe_get_handler": sentinel.rewrite_recipe_get_handler,
            "rewrite_recipe_update_handler": sentinel.rewrite_recipe_update_handler,
            "rewrite_recipe_delete_handler": sentinel.rewrite_recipe_delete_handler,
            "rewrite_recipe_dry_run_handler": sentinel.rewrite_recipe_dry_run_handler,
            "rewrite_recipe_apply_handler": sentinel.rewrite_recipe_apply_handler,
            "model_search_handler": sentinel.model_search_handler,
            "model_download_create_handler": sentinel.model_download_create_handler,
            "model_download_list_handler": sentinel.model_download_list_handler,
            "model_download_get_handler": sentinel.model_download_get_handler,
            "model_download_cancel_handler": sentinel.model_download_cancel_handler,
            "model_import_handler": sentinel.model_import_handler,
            "model_installations_list_handler": sentinel.model_installations_list_handler,
            "secrets_status_handler": sentinel.secrets_status_handler,
            "secrets_put_handler": sentinel.secrets_put_handler,
            "events_stream_handler": sentinel.events_stream_handler,
            "events_poll_handler": sentinel.events_poll_handler,
            "secrets_delete_handler": sentinel.secrets_delete_handler,
            "security_doctor_handler": sentinel.security_doctor_handler,
            "tools_list_handler": sentinel.tools_list_handler,
            "tools_run_handler": sentinel.tools_run_handler,
            "create_sweep_handler": sentinel.create_sweep_handler,
            "create_compare_handler": sentinel.create_compare_handler,
            "list_experiments_handler": sentinel.list_experiments_handler,
            "get_experiment_handler": sentinel.get_experiment_handler,
            "update_experiment_handler": sentinel.update_experiment_handler,
            "select_apply_winner_handler": sentinel.select_apply_winner_handler,
        }

        specs = build_core_route_specs("/openclaw", handlers)
        keys = {(spec.method, spec.path) for spec in specs}

        self.assertIn(("GET", "/openclaw/health"), keys)
        self.assertIn(("POST", "/openclaw/webhook"), keys)
        self.assertIn(("GET", "/openclaw/llm/models"), keys)
        self.assertIn(("POST", "/openclaw/pnginfo"), keys)
        self.assertIn(("POST", "/openclaw/lab/experiments/{exp_id}/winner"), keys)
        self.assertEqual(50, len(specs))

    def test_build_assist_route_specs_preserves_expected_paths(self):
        specs = build_assist_route_specs("/moltbot", _AssistStub())
        keys = {(spec.method, spec.path) for spec in specs}
        self.assertEqual(6, len(specs))
        self.assertIn(("GET", "/moltbot/assist/planner/profiles"), keys)
        self.assertIn(("POST", "/moltbot/assist/automation/compose"), keys)

    def test_build_connector_installation_specs_preserves_expected_paths(self):
        specs = build_connector_installation_route_specs(
            "/openclaw",
            {
                "connector_installations_list_handler": sentinel.list_handler,
                "connector_installation_resolve_handler": sentinel.resolve_handler,
                "connector_installation_audit_handler": sentinel.audit_handler,
                "connector_installation_get_handler": sentinel.get_handler,
            },
        )
        keys = {(spec.method, spec.path) for spec in specs}
        self.assertEqual(4, len(specs))
        self.assertIn(("GET", "/openclaw/connector/installations/audit"), keys)
        self.assertIn(
            ("GET", "/openclaw/connector/installations/{installation_id}"), keys
        )

    def test_build_pack_route_specs_preserves_expected_paths(self):
        specs = build_pack_route_specs("/openclaw", _PacksStub())
        keys = {(spec.method, spec.path) for spec in specs}
        self.assertEqual(4, len(specs))
        self.assertIn(("GET", "/openclaw/packs"), keys)
        self.assertIn(("DELETE", "/openclaw/packs/{name}/{version}"), keys)


if __name__ == "__main__":
    unittest.main()
