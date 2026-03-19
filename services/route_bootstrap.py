"""
R130 route/bootstrap orchestration extracted from package entrypoint.

Keeps __init__.py thin while preserving startup behavior and fallback handling.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time

_routes_registered = False


def _register_plugins_and_shutdown_hooks() -> None:
    # R67: Best-effort process shutdown hook for scheduler/failover flush.
    try:
        from .plugins.builtin import register_all
        from .runtime_lifecycle import register_shutdown_hooks

        register_shutdown_hooks()
        register_all()
    except Exception as e:
        logging.getLogger("ComfyUI-OpenClaw").error(f"Failed to register plugins: {e}")


def _initialize_registries_and_security_gate() -> None:
    # R63/R84: Initialize Service & Module Registries.
    try:
        from .modules import ModuleCapability, ModuleRegistry, enable_module
        from .registry import SVC_RUNTIME_CONFIG, ServiceRegistry
        from .runtime_config import get_config

        config = get_config()
        ServiceRegistry.register(SVC_RUNTIME_CONFIG, config)

        # Always-on modules
        enable_module(ModuleCapability.CORE)
        enable_module(ModuleCapability.SECURITY)
        enable_module(ModuleCapability.OBSERVABILITY)

        # S50: initialize durable idempotency storage early.
        from .idempotency_store import IdempotencyStore
        from .state_dir import get_state_dir

        db_path = os.path.join(get_state_dir(), "idempotency.db")
        # CRITICAL: pass db_path as keyword (first positional arg is backend object).
        IdempotencyStore().configure_durable(db_path=db_path, strict_mode=True)
        logging.getLogger("ComfyUI-OpenClaw").info(
            "IdempotencyStore durable backend configured at: %s (strict_mode=True)",
            db_path,
        )

        if config.bridge_enabled:
            enable_module(ModuleCapability.BRIDGE)

        # Core runtime modules stay enabled; runners decide active behavior.
        enable_module(ModuleCapability.SCHEDULER)
        enable_module(ModuleCapability.WEBHOOK)
        enable_module(ModuleCapability.CONNECTOR)

        ModuleRegistry.lock()
        logging.getLogger("ComfyUI-OpenClaw").info(
            "Initialized modules: %s", ModuleRegistry.get_enabled_list()
        )

        from .security_gate import enforce_startup_gate

        enforce_startup_gate()
    except Exception as e:
        logging.getLogger("ComfyUI-OpenClaw").error(
            f"Failed to initialize registries: {e}"
        )
        # CRITICAL: keep bootstrap fail-closed; swallowing startup gate errors
        # silently degrades security posture and can expose partial registration.
        raise


def _do_full_registration(server) -> None:
    """Register all OpenClaw routes including bridge/scheduler bindings."""
    from .access_control import require_admin_token
    from .plugins.async_bridge import run_async_in_sync_context
    from .queue_submit import submit_prompt
    from .route_bootstrap_contract import load_route_bootstrap_contract
    from .scheduler.runner import get_scheduler_runner, start_scheduler
    from .templates import get_template_service

    contract = load_route_bootstrap_contract(__package__)
    register_approval_routes = contract["register_approval_routes"]
    BridgeHandlers = contract["BridgeHandlers"]
    register_preset_routes = contract["register_preset_routes"]
    register_routes = contract["register_routes"]
    register_schedule_routes = contract["register_schedule_routes"]
    register_trigger_routes = contract["register_trigger_routes"]

    register_routes(server)
    register_preset_routes(server.app)
    register_schedule_routes(server.app, require_admin_token_fn=require_admin_token)

    class QueueSubmitService:
        def submit(self, job_req):
            tmpl_svc = get_template_service()
            workflow = tmpl_svc.render_template(job_req.template_id, job_req.inputs)

            async def _do_submit():
                return await submit_prompt(
                    workflow,
                    client_id=job_req.session_id or "bridge",
                    extra_data={
                        "openclaw": {"trace_id": job_req.trace_id},
                        # Legacy key kept for existing tooling that expects this blob.
                        "moltbot": {"trace_id": job_req.trace_id},
                    },
                    source="bridge",
                    trace_id=job_req.trace_id,
                )

            return run_async_in_sync_context(_do_submit())

    bridge_handlers = BridgeHandlers(submit_service=QueueSubmitService())
    _register_bridge_routes(server.app.router, bridge_handlers)

    async def unified_submit_fn(
        template_id,
        inputs,
        trace_id,
        idempotency_key,
        delivery=None,
        source="unknown",
    ):
        """Submit function for scheduler and trigger-triggered runs."""
        # NOTE: Use IdempotencyStore API (check_and_record/update_prompt_id).
        # Avoid legacy get_store/get/set usage; wrong API here breaks route registration at runtime.
        from .idempotency_store import IdempotencyStore
        from .queue_submit import submit_prompt as _submit_prompt
        from .templates import get_template_service as _get_template_service

        store = IdempotencyStore()
        is_dup, existing_prompt_id = store.check_and_record(idempotency_key)
        if is_dup:
            return {"prompt_id": existing_prompt_id, "deduped": True}

        tmpl_svc = _get_template_service()
        workflow = tmpl_svc.render_template(template_id, inputs)

        result = await _submit_prompt(
            workflow,
            extra_data={
                "openclaw": {"trace_id": trace_id, "source": "automation"},
                "moltbot": {"trace_id": trace_id, "source": "automation"},
            },
            source=source,
            trace_id=trace_id,
        )

        if result.get("prompt_id"):
            store.update_prompt_id(idempotency_key, result["prompt_id"])
        return result

    runner = get_scheduler_runner()
    runner._submit_fn = unified_submit_fn
    start_scheduler()

    register_trigger_routes(
        server.app,
        require_admin_token_fn=require_admin_token,
        submit_fn=unified_submit_fn,
    )
    register_approval_routes(
        server.app,
        require_admin_token_fn=require_admin_token,
        submit_fn=unified_submit_fn,
    )


_BRIDGE_ROUTE_SPECS = (
    ("add_post", "/moltbot/bridge/submit", "submit_handler"),
    ("add_post", "/moltbot/bridge/deliver", "deliver_handler"),
    ("add_get", "/moltbot/bridge/health", "health_handler"),
    ("add_post", "/openclaw/bridge/submit", "submit_handler"),
    ("add_post", "/openclaw/bridge/deliver", "deliver_handler"),
    ("add_get", "/openclaw/bridge/health", "health_handler"),
    ("add_post", "/api/moltbot/bridge/submit", "submit_handler"),
    ("add_post", "/api/moltbot/bridge/deliver", "deliver_handler"),
    ("add_get", "/api/moltbot/bridge/health", "health_handler"),
    ("add_post", "/api/openclaw/bridge/submit", "submit_handler"),
    ("add_post", "/api/openclaw/bridge/deliver", "deliver_handler"),
    ("add_get", "/api/openclaw/bridge/health", "health_handler"),
)


def _register_bridge_routes(router, bridge_handlers) -> None:
    # IMPORTANT: keep bridge route registration table-driven.
    # Missing one alias path here silently breaks one control-plane surface while
    # leaving the rest apparently healthy, which is hard to diagnose during startup.
    for method_name, path, handler_name in _BRIDGE_ROUTE_SPECS:
        registrar = getattr(router, method_name, None)
        if registrar is None:
            continue
        try:
            registrar(path, getattr(bridge_handlers, handler_name))
        except RuntimeError:
            if path.startswith("/api/"):
                continue
            raise


def _start_registration_retry_loop() -> None:
    """R25: Retry route registration while PromptServer is warming up."""

    def _retry_worker():
        global _routes_registered
        attempts = 0
        max_attempts = 10
        delay = 2.0
        logger = logging.getLogger("ComfyUI-OpenClaw")

        while not _routes_registered and attempts < max_attempts:
            try:
                ps_mod = sys.modules.get("server")
                PromptServer = getattr(ps_mod, "PromptServer", None) if ps_mod else None
                if PromptServer and getattr(PromptServer, "instance", None) is not None:
                    _do_full_registration(PromptServer.instance)
                    _routes_registered = True
                    logger.info(
                        "Routes registered successfully on attempt %s", attempts + 1
                    )
                    return
                logger.debug(
                    "PromptServer.instance not ready (attempt %s)", attempts + 1
                )
            except Exception:
                logger.exception("Error registering routes (attempt %s)", attempts + 1)

            time.sleep(delay)
            delay = min(delay * 1.5, 30)
            attempts += 1

        if not _routes_registered:
            logger.error(
                "Failed to register routes after %s attempts. API endpoints unavailable.",
                max_attempts,
            )

    t = threading.Thread(target=_retry_worker, name="openclaw-route-retry", daemon=True)
    t.start()


def register_routes_once() -> None:
    global _routes_registered
    if _routes_registered:
        return

    _register_plugins_and_shutdown_hooks()
    _initialize_registries_and_security_gate()

    try:
        ps_mod = sys.modules.get("server")
        PromptServer = getattr(ps_mod, "PromptServer", None) if ps_mod else None

        if PromptServer and getattr(PromptServer, "instance", None) is not None:
            _do_full_registration(PromptServer.instance)
            _routes_registered = True
            logging.getLogger("ComfyUI-OpenClaw").info(
                "Routes registered successfully on initial attempt."
            )
        else:
            logging.getLogger("ComfyUI-OpenClaw").info(
                "PromptServer not ready, starting background registration retry loop..."
            )
            _start_registration_retry_loop()
    except Exception:
        logging.getLogger("ComfyUI-OpenClaw").exception("Route registration failed")
