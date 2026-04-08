"""
R151 route-family registrar helpers.

Keep route composition data-driven without importing api.routes back into this
module, which would reintroduce circular bootstrap fragility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class RouteSpec:
    method: str
    path: str
    handler: Any


def register_route_family(
    server, register_route_fn, specs: Iterable[RouteSpec]
) -> None:
    for spec in specs:
        register_route_fn(server, spec.method, spec.path, spec.handler)


def build_core_route_specs(
    prefix: str, handlers: dict[str, Any]
) -> tuple[RouteSpec, ...]:
    return (
        RouteSpec("GET", f"{prefix}/admin", handlers["remote_admin_page_handler"]),
        RouteSpec("GET", f"{prefix}/health", handlers["health_handler"]),
        RouteSpec("GET", f"{prefix}/logs/tail", handlers["logs_tail_handler"]),
        RouteSpec("GET", f"{prefix}/jobs", handlers["jobs_handler"]),
        RouteSpec("GET", f"{prefix}/trace/{{prompt_id}}", handlers["trace_handler"]),
        RouteSpec("POST", f"{prefix}/webhook", handlers["webhook_handler"]),
        RouteSpec(
            "POST",
            f"{prefix}/webhook/submit",
            handlers["webhook_submit_handler"],
        ),
        RouteSpec(
            "POST",
            f"{prefix}/webhook/validate",
            handlers["webhook_validate_handler"],
        ),
        RouteSpec("GET", f"{prefix}/capabilities", handlers["capabilities_handler"]),
        RouteSpec("GET", f"{prefix}/config", handlers["config_get_handler"]),
        RouteSpec("PUT", f"{prefix}/config", handlers["config_put_handler"]),
        RouteSpec("POST", f"{prefix}/llm/test", handlers["llm_test_handler"]),
        RouteSpec("POST", f"{prefix}/llm/chat", handlers["llm_chat_handler"]),
        RouteSpec("GET", f"{prefix}/llm/models", handlers["llm_models_handler"]),
        RouteSpec("GET", f"{prefix}/templates", handlers["templates_list_handler"]),
        RouteSpec("POST", f"{prefix}/preflight", handlers["preflight_handler"]),
        RouteSpec(
            "GET",
            f"{prefix}/preflight/inventory",
            handlers["inventory_handler"],
        ),
        RouteSpec("POST", f"{prefix}/pnginfo", handlers["pnginfo_handler"]),
        RouteSpec("GET", f"{prefix}/checkpoints", handlers["list_checkpoints_handler"]),
        RouteSpec(
            "POST",
            f"{prefix}/checkpoints",
            handlers["create_checkpoint_handler"],
        ),
        RouteSpec(
            "GET",
            f"{prefix}/checkpoints/{{id}}",
            handlers["get_checkpoint_handler"],
        ),
        RouteSpec(
            "DELETE",
            f"{prefix}/checkpoints/{{id}}",
            handlers["delete_checkpoint_handler"],
        ),
        RouteSpec(
            "GET",
            f"{prefix}/rewrite/recipes",
            handlers["rewrite_recipes_list_handler"],
        ),
        RouteSpec(
            "POST",
            f"{prefix}/rewrite/recipes",
            handlers["rewrite_recipe_create_handler"],
        ),
        RouteSpec(
            "GET",
            f"{prefix}/rewrite/recipes/{{recipe_id}}",
            handlers["rewrite_recipe_get_handler"],
        ),
        RouteSpec(
            "PUT",
            f"{prefix}/rewrite/recipes/{{recipe_id}}",
            handlers["rewrite_recipe_update_handler"],
        ),
        RouteSpec(
            "DELETE",
            f"{prefix}/rewrite/recipes/{{recipe_id}}",
            handlers["rewrite_recipe_delete_handler"],
        ),
        RouteSpec(
            "POST",
            f"{prefix}/rewrite/recipes/{{recipe_id}}/dry-run",
            handlers["rewrite_recipe_dry_run_handler"],
        ),
        RouteSpec(
            "POST",
            f"{prefix}/rewrite/recipes/{{recipe_id}}/apply",
            handlers["rewrite_recipe_apply_handler"],
        ),
        RouteSpec("GET", f"{prefix}/models/search", handlers["model_search_handler"]),
        RouteSpec(
            "POST",
            f"{prefix}/models/downloads",
            handlers["model_download_create_handler"],
        ),
        RouteSpec(
            "GET",
            f"{prefix}/models/downloads",
            handlers["model_download_list_handler"],
        ),
        RouteSpec(
            "GET",
            f"{prefix}/models/downloads/{{task_id}}",
            handlers["model_download_get_handler"],
        ),
        RouteSpec(
            "POST",
            f"{prefix}/models/downloads/{{task_id}}/cancel",
            handlers["model_download_cancel_handler"],
        ),
        RouteSpec("POST", f"{prefix}/models/import", handlers["model_import_handler"]),
        RouteSpec(
            "GET",
            f"{prefix}/models/installations",
            handlers["model_installations_list_handler"],
        ),
        RouteSpec(
            "GET",
            f"{prefix}/secrets/status",
            handlers["secrets_status_handler"],
        ),
        RouteSpec("PUT", f"{prefix}/secrets", handlers["secrets_put_handler"]),
        RouteSpec(
            "GET",
            f"{prefix}/events/stream",
            handlers["events_stream_handler"],
        ),
        RouteSpec("GET", f"{prefix}/events", handlers["events_poll_handler"]),
        RouteSpec(
            "DELETE",
            f"{prefix}/secrets/{{provider}}",
            handlers["secrets_delete_handler"],
        ),
        RouteSpec(
            "GET",
            f"{prefix}/security/doctor",
            handlers["security_doctor_handler"],
        ),
        RouteSpec("GET", f"{prefix}/tools", handlers["tools_list_handler"]),
        RouteSpec(
            "POST",
            f"{prefix}/tools/{{name}}/run",
            handlers["tools_run_handler"],
        ),
        RouteSpec("POST", f"{prefix}/lab/sweep", handlers["create_sweep_handler"]),
        RouteSpec("POST", f"{prefix}/lab/compare", handlers["create_compare_handler"]),
        RouteSpec(
            "GET",
            f"{prefix}/lab/experiments",
            handlers["list_experiments_handler"],
        ),
        RouteSpec(
            "GET",
            f"{prefix}/lab/experiments/{{exp_id}}",
            handlers["get_experiment_handler"],
        ),
        RouteSpec(
            "POST",
            f"{prefix}/lab/experiments/{{exp_id}}/runs/{{run_id}}",
            handlers["update_experiment_handler"],
        ),
        RouteSpec(
            "POST",
            f"{prefix}/lab/experiments/{{exp_id}}/winner",
            handlers["select_apply_winner_handler"],
        ),
    )


def build_assist_route_specs(prefix: str, assist) -> tuple[RouteSpec, ...]:
    return (
        RouteSpec(
            "GET",
            f"{prefix}/assist/planner/profiles",
            assist.planner_profiles_handler,
        ),
        RouteSpec("POST", f"{prefix}/assist/planner", assist.planner_handler),
        RouteSpec(
            "POST",
            f"{prefix}/assist/planner/stream",
            assist.planner_stream_handler,
        ),
        RouteSpec("POST", f"{prefix}/assist/refiner", assist.refiner_handler),
        RouteSpec(
            "POST",
            f"{prefix}/assist/refiner/stream",
            assist.refiner_stream_handler,
        ),
        RouteSpec(
            "POST",
            f"{prefix}/assist/automation/compose",
            assist.compose_handler,
        ),
    )


def build_connector_installation_route_specs(
    prefix: str, handlers: dict[str, Any]
) -> tuple[RouteSpec, ...]:
    return (
        RouteSpec(
            "GET",
            f"{prefix}/connector/installations",
            handlers["connector_installations_list_handler"],
        ),
        RouteSpec(
            "GET",
            f"{prefix}/connector/installations/resolve",
            handlers["connector_installation_resolve_handler"],
        ),
        RouteSpec(
            "GET",
            f"{prefix}/connector/installations/audit",
            handlers["connector_installation_audit_handler"],
        ),
        RouteSpec(
            "GET",
            f"{prefix}/connector/installations/{{installation_id}}",
            handlers["connector_installation_get_handler"],
        ),
    )


def build_pack_route_specs(prefix: str, packs) -> tuple[RouteSpec, ...]:
    return (
        RouteSpec("GET", f"{prefix}/packs", packs.list_packs_handler),
        RouteSpec("POST", f"{prefix}/packs/import", packs.import_pack_handler),
        RouteSpec(
            "GET",
            f"{prefix}/packs/export/{{name}}/{{version}}",
            packs.export_pack_handler,
        ),
        RouteSpec(
            "DELETE",
            f"{prefix}/packs/{{name}}/{{version}}",
            packs.delete_pack_handler,
        ),
    )
