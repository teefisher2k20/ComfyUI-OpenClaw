"""
Scheduler CRUD API (R4).
REST endpoints for managing persistent schedules.
"""

from __future__ import annotations

import logging
from typing import Optional

# Import discipline:
# - ComfyUI runtime: package-relative imports only (prevents collisions with other custom nodes).
# - Unit tests: allow top-level fallbacks.
#
# IMPORTANT: Avoid a broad `try/except ImportError` here. Falling back to `services.*` in ComfyUI
# can silently import another pack's module and break auth/approval semantics.
if __package__ and "." in __package__:
    from ..services.aiohttp_compat import import_aiohttp_web
    from ..services.scheduler.models import Schedule, TriggerType
    from ..services.scheduler.storage import get_schedule_store
    from ..services.templates import is_template_allowed
    from ..services.webhook_auth import AuthError
else:  # pragma: no cover (test-only import mode)
    from services.aiohttp_compat import import_aiohttp_web  # type: ignore
    from services.scheduler.models import Schedule, TriggerType  # type: ignore
    from services.scheduler.storage import get_schedule_store  # type: ignore
    from services.templates import is_template_allowed  # type: ignore
    from services.webhook_auth import AuthError  # type: ignore

logger = logging.getLogger("ComfyUI-OpenClaw.api.schedules")
web = import_aiohttp_web()


def _get_scheduler_runner():
    if __package__ and "." in __package__:
        from ..services.scheduler.runner import get_scheduler_runner
    else:  # pragma: no cover (test-only import mode)
        from services.scheduler.runner import get_scheduler_runner  # type: ignore
    return get_scheduler_runner()


def _get_run_history():
    if __package__ and "." in __package__:
        from ..services.scheduler.history import get_run_history
    else:  # pragma: no cover (test-only import mode)
        from services.scheduler.history import get_run_history  # type: ignore
    return get_run_history()


class ScheduleHandlers:
    """
    CRUD handlers for /moltbot/schedules endpoints.
    All endpoints require admin token authentication.
    """

    def __init__(self, require_admin_token_fn=None, template_checker=None):
        """
        Args:
            require_admin_token_fn: Function to validate admin token.
                Expected to return either:
                - (allowed: bool, error: Optional[str])  OR
                - an awaitable resolving to that tuple.
            template_checker: Function to check if template_id is allowed.
        """
        self._require_admin_token = require_admin_token_fn
        self._template_checker = template_checker or is_template_allowed
        self._store = get_schedule_store()

    async def _check_auth(self, request: web.Request) -> None:
        """Require admin token for all schedule operations."""
        if self._require_admin_token:
            import inspect

            result = self._require_admin_token(request)
            if inspect.isawaitable(result):
                result = await result

            if isinstance(result, tuple):
                allowed, error = result
                if not allowed:
                    raise AuthError(error or "Unauthorized")

    async def list_schedules(self, request: web.Request) -> web.Response:
        """GET /moltbot/schedules - List all schedules."""
        try:
            await self._check_auth(request)
        except AuthError as e:
            return web.json_response({"error": str(e)}, status=403)

        schedules = self._store.list_all()

        # Optional filter by enabled status
        enabled_filter = request.query.get("enabled")
        if enabled_filter is not None:
            enabled = enabled_filter.lower() == "true"
            schedules = [s for s in schedules if s.enabled == enabled]

        return web.json_response(
            {
                "schedules": [s.to_dict() for s in schedules],
                "count": len(schedules),
            }
        )

    async def get_schedule(self, request: web.Request) -> web.Response:
        """GET /moltbot/schedules/{schedule_id} - Get a single schedule."""
        try:
            await self._check_auth(request)
        except AuthError as e:
            return web.json_response({"error": str(e)}, status=403)

        schedule_id = request.match_info.get("schedule_id", "")
        schedule = self._store.get(schedule_id)

        if not schedule:
            return web.json_response({"error": "Schedule not found"}, status=404)

        return web.json_response({"schedule": schedule.to_dict()})

    async def create_schedule(self, request: web.Request) -> web.Response:
        """POST /moltbot/schedules - Create a new schedule."""
        try:
            await self._check_auth(request)
        except AuthError as e:
            return web.json_response({"error": str(e)}, status=403)

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        # Validate required fields
        if not data.get("name"):
            return web.json_response({"error": "name is required"}, status=400)
        if not data.get("template_id"):
            return web.json_response({"error": "template_id is required"}, status=400)

        # Check template allowlist
        template_id = data["template_id"]
        if not self._template_checker(template_id):
            return web.json_response(
                {"error": f"template_id '{template_id}' not found"},
                status=404,
            )

        # Build schedule
        try:
            schedule = Schedule(
                schedule_id=Schedule.generate_id(),
                name=data["name"],
                template_id=template_id,
                trigger_type=TriggerType(data.get("trigger_type", "interval")),
                cron_expr=data.get("cron_expr"),
                interval_sec=data.get("interval_sec"),
                inputs=data.get("inputs", {}),
                delivery=data.get("delivery"),
                timezone=data.get("timezone", "local"),
                enabled=data.get("enabled", True),
            )
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)

        # Store
        if not self._store.add(schedule):
            return web.json_response({"error": "Failed to create schedule"}, status=500)

        logger.info(f"Created schedule: {schedule.schedule_id}")
        return web.json_response(
            {"schedule": schedule.to_dict(), "created": True}, status=201
        )

    async def update_schedule(self, request: web.Request) -> web.Response:
        """PUT /moltbot/schedules/{schedule_id} - Update a schedule."""
        try:
            await self._check_auth(request)
        except AuthError as e:
            return web.json_response({"error": str(e)}, status=403)

        schedule_id = request.match_info.get("schedule_id", "")
        existing = self._store.get(schedule_id)

        if not existing:
            return web.json_response({"error": "Schedule not found"}, status=404)

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        # Validate template_id if changing
        if "template_id" in data:
            template_id = data["template_id"]
            if not self._template_checker(template_id):
                return web.json_response(
                    {"error": f"template_id '{template_id}' not found"},
                    status=404,
                )
            existing.template_id = template_id

        # Update allowed fields
        if "name" in data:
            existing.name = data["name"]
        if "trigger_type" in data:
            existing.trigger_type = TriggerType(data["trigger_type"])
        if "cron_expr" in data:
            existing.cron_expr = data["cron_expr"]
        if "interval_sec" in data:
            existing.interval_sec = data["interval_sec"]
        if "inputs" in data:
            existing.inputs = data["inputs"]
        if "delivery" in data:
            existing.delivery = data["delivery"]
        if "timezone" in data:
            existing.timezone = data["timezone"]
        if "enabled" in data:
            existing.enabled = bool(data["enabled"])

        # Re-validate
        try:
            existing.validate()
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)

        # Save
        from datetime import datetime
        from datetime import timezone as tz

        existing.updated_at = datetime.now(tz.utc).isoformat()

        if not self._store.update(existing):
            return web.json_response({"error": "Failed to update schedule"}, status=500)

        logger.info(f"Updated schedule: {schedule_id}")
        return web.json_response({"schedule": existing.to_dict(), "updated": True})

    async def delete_schedule(self, request: web.Request) -> web.Response:
        """DELETE /moltbot/schedules/{schedule_id} - Delete a schedule."""
        try:
            await self._check_auth(request)
        except AuthError as e:
            return web.json_response({"error": str(e)}, status=403)

        schedule_id = request.match_info.get("schedule_id", "")
        existing = self._store.get(schedule_id)

        if not existing:
            return web.json_response({"error": "Schedule not found"}, status=404)

        if not self._store.delete(schedule_id):
            return web.json_response({"error": "Failed to delete schedule"}, status=500)

        logger.info(f"Deleted schedule: {schedule_id}")
        return web.json_response({"deleted": True, "schedule_id": schedule_id})

    async def toggle_schedule(self, request: web.Request) -> web.Response:
        """POST /moltbot/schedules/{schedule_id}/toggle - Toggle enabled status."""
        try:
            await self._check_auth(request)
        except AuthError as e:
            return web.json_response({"error": str(e)}, status=403)

        schedule_id = request.match_info.get("schedule_id", "")
        existing = self._store.get(schedule_id)

        if not existing:
            return web.json_response({"error": "Schedule not found"}, status=404)

        existing.enabled = not existing.enabled

        from datetime import datetime
        from datetime import timezone as tz

        existing.updated_at = datetime.now(tz.utc).isoformat()

        if not self._store.update(existing):
            return web.json_response({"error": "Failed to toggle schedule"}, status=500)

        logger.info(f"Toggled schedule {schedule_id}: enabled={existing.enabled}")
        return web.json_response(
            {
                "schedule_id": schedule_id,
                "enabled": existing.enabled,
            }
        )

    async def run_now(self, request: web.Request) -> web.Response:
        """POST /moltbot/schedules/{schedule_id}/run - Manually trigger a schedule."""
        try:
            await self._check_auth(request)
        except AuthError as e:
            return web.json_response({"error": str(e)}, status=403)

        schedule_id = request.match_info.get("schedule_id", "")
        schedule = self._store.get(schedule_id)

        if not schedule:
            return web.json_response({"error": "Schedule not found"}, status=404)

        # Trigger immediate execution via scheduler runner
        import time

        runner = _get_scheduler_runner()
        if runner.is_execution_delegated():
            # IMPORTANT: in public+split mode, embedded scheduler execution must remain blocked.
            return web.json_response(
                {
                    "error": "Scheduler execution is delegated to external control plane",
                    "code": "scheduler_delegated",
                    "remediation": "Use external scheduler control plane in split mode.",
                },
                status=503,
            )

        try:
            runner._execute_schedule(schedule, time.time())
            return web.json_response(
                {
                    "triggered": True,
                    "schedule_id": schedule_id,
                    "template_id": schedule.template_id,
                }
            )
        except Exception as e:
            logger.error(f"Manual run failed: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def list_runs(self, request: web.Request) -> web.Response:
        """GET /moltbot/schedules/{schedule_id}/runs - List runs for a schedule."""
        try:
            await self._check_auth(request)
        except AuthError as e:
            return web.json_response({"error": str(e)}, status=403)
        except Exception:
            return web.json_response({"error": "Unauthorized"}, status=403)

        schedule_id = request.match_info.get("schedule_id", "")

        # Verify schedule exists
        if not self._store.get(schedule_id):
            return web.json_response({"error": "Schedule not found"}, status=404)

        history = _get_run_history()
        limit = int(request.query.get("limit", "100"))
        offset = int(request.query.get("offset", "0"))
        status = request.query.get("status")

        runs = history.list_runs(
            schedule_id=schedule_id,
            status=status,
            limit=min(limit, 500),
            offset=offset,
        )

        return web.json_response(
            {
                "runs": [r.to_dict() for r in runs],
                "count": len(runs),
                "total": history.count_runs(schedule_id),
            }
        )

    async def list_all_runs(self, request: web.Request) -> web.Response:
        """GET /moltbot/runs - List all runs across all schedules."""
        try:
            await self._check_auth(request)
        except AuthError as e:
            return web.json_response({"error": str(e)}, status=403)
        except Exception:
            return web.json_response({"error": "Unauthorized"}, status=403)

        history = _get_run_history()
        limit = int(request.query.get("limit", "100"))
        offset = int(request.query.get("offset", "0"))
        status = request.query.get("status")
        schedule_id = request.query.get("schedule_id")

        runs = history.list_runs(
            schedule_id=schedule_id,
            status=status,
            limit=min(limit, 500),
            offset=offset,
        )

        return web.json_response(
            {
                "runs": [r.to_dict() for r in runs],
                "count": len(runs),
                "total": history.count_runs(),
            }
        )


def register_schedule_routes(app: web.Application, require_admin_token_fn=None) -> None:
    """Register schedule CRUD routes on the aiohttp app."""
    handlers = ScheduleHandlers(require_admin_token_fn)

    prefixes = ["/openclaw", "/moltbot"]  # new, legacy
    for prefix in prefixes:
        routes = [
            ("GET", f"{prefix}/schedules", handlers.list_schedules),
            ("POST", f"{prefix}/schedules", handlers.create_schedule),
            ("GET", f"{prefix}/schedules/{{schedule_id}}", handlers.get_schedule),
            ("PUT", f"{prefix}/schedules/{{schedule_id}}", handlers.update_schedule),
            ("DELETE", f"{prefix}/schedules/{{schedule_id}}", handlers.delete_schedule),
            (
                "POST",
                f"{prefix}/schedules/{{schedule_id}}/toggle",
                handlers.toggle_schedule,
            ),
            ("POST", f"{prefix}/schedules/{{schedule_id}}/run", handlers.run_now),
            # R9: Run history endpoints
            ("GET", f"{prefix}/schedules/{{schedule_id}}/runs", handlers.list_runs),
            ("GET", f"{prefix}/runs", handlers.list_all_runs),
        ]

        for method, path, handler in routes:
            # 1. Legacy
            try:
                app.router.add_route(method, path, handler)
            except RuntimeError:
                pass

            # 2. /api Shim aligned
            try:
                app.router.add_route(method, "/api" + path, handler)
            except RuntimeError:
                pass

    logger.info("Registered schedule CRUD routes (dual)")
