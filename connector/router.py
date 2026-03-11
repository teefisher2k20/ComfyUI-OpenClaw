"""
Connector Router (F29 Remediation).
Dispatches parsed commands to handlers with AST argument parsing.
"""

import logging
import shlex
from typing import Any, Dict, List, Optional

from .config import CommandClass, ConnectorConfig
from .contract import CommandRequest, CommandResponse
from .openclaw_client import OpenClawClient
from .state import ConnectorState

if False:  # Type hinting only
    from .results_poller import ResultsPoller

from .command_firewall import CommandFirewall
from .llm_client import LLMClient
from .prompts import CHAT_STATUS_PROMPT, CHAT_SYSTEM_PROMPT
from .rate_limiter import RateLimiter
from .semantic_guard import GuardAction, SemanticGuard

try:
    from services.reasoning_redaction import sanitize_operator_payload
except Exception:  # pragma: no cover - connector tests may stub import graph
    sanitize_operator_payload = lambda value, **_: value  # type: ignore

logger = logging.getLogger(__name__)


class CommandRouter:
    def __init__(
        self,
        config: ConnectorConfig,
        client: OpenClawClient,
        poller: "ResultsPoller" = None,
    ):
        self.config = config
        self.client = client
        self.poller = poller
        self.state = ConnectorState(path=self.config.state_path)
        self._template_meta_cache: Dict[str, Dict[str, Any]] = {}
        # F32 WP2: Rate limiter
        self._rate_limiter = RateLimiter(
            user_rpm=self.config.rate_limit_user_rpm,
            channel_rpm=self.config.rate_limit_channel_rpm,
        )
        # S44/R97: Semantic Guards
        self.semantic_guard = SemanticGuard()
        self.command_firewall = CommandFirewall()

    async def handle(self, req: CommandRequest) -> CommandResponse:
        """Main dispatch loop."""
        text = req.text.strip()
        # NOTE: Debug-only raw message logging for troubleshooting parsing issues.
        # Enable with OPENCLAW_CONNECTOR_DEBUG=1. May include sensitive user content.
        if self.config.debug:
            logger.info(
                "DEBUG raw message: platform=%s user=%s chat=%s text=%r",
                req.platform,
                req.sender_id,
                req.channel_id,
                text,
            )

        # F32 WP2: Rate limiting
        if not self._rate_limiter.is_allowed(str(req.sender_id), str(req.channel_id)):
            return CommandResponse(
                text="[Rate Limited] Too many requests. Please wait a moment."
            )

        # F32 WP5: Command length limit
        if len(text) > self.config.max_command_length:
            return CommandResponse(
                text=f"[Error] Command too long ({len(text)} chars). Max: {self.config.max_command_length}."
            )

        try:
            # IMPORTANT (recurring usability bug):
            # Do not use `shlex.split()` directly for ChatOps commands that may include natural
            # language. In POSIX mode, `shlex` treats apostrophes (`'`) as quote delimiters, so
            # common contractions like "She's" trigger "unbalanced quotes" failures.
            #
            # We therefore only treat *double quotes* (`"`) as quoting characters, so users can
            # still do: positive_prompt="a prompt with spaces" while apostrophes remain safe.
            lexer = shlex.shlex(text, posix=True)
            lexer.whitespace_split = True
            lexer.commenters = ""
            lexer.quotes = '"'
            parts = list(lexer)
        except ValueError:
            return CommandResponse(
                text="[Error] Parsing command arguments failed (unbalanced quotes?)."
            )

        if not parts:
            return CommandResponse(text="Empty command.")

        cmd = parts[0].lower()
        args = parts[1:]

        # Telegram group commands often include the bot username suffix, e.g. `/help@mybot`.
        # If we don't strip it, the command won't match our dispatch table and appears "dead"
        # even though polling is working.
        if (
            (req.platform or "").lower() == "telegram"
            and cmd.startswith("/")
            and "@" in cmd
        ):
            cmd = cmd.split("@", 1)[0]

        # Some users type `@bot /help` in group chats. Treat that as a command too.
        if cmd.startswith("@") and args and args[0].startswith("/"):
            cmd = args[0].lower()
            args = args[1:]

        # Dispatch Table
        handlers = {
            ("/status", "status"): (self._handle_status, CommandClass.PUBLIC),
            ("/help", "help", "/start"): (self._handle_help, CommandClass.PUBLIC),
            ("/run", "run"): (self._handle_run, CommandClass.RUN),
            ("/interrupt", "interrupt", "/cancel", "cancel", "/stop"): (
                self._handle_interrupt,
                CommandClass.ADMIN,
            ),  # Global interrupt => admin-only.
            ("/approvals", "approvals"): (
                self._handle_approvals_list,
                CommandClass.ADMIN,
            ),
            ("/approve", "approve"): (self._handle_approve, CommandClass.ADMIN),
            ("/reject", "reject"): (self._handle_reject, CommandClass.ADMIN),
            ("/schedules", "schedules"): (
                self._handle_schedules_list,
                CommandClass.ADMIN,
            ),
            ("/schedule", "schedule"): (
                self._handle_schedule_subcommand,
                CommandClass.ADMIN,
            ),
            # Phase 3 Introspection
            ("/history", "history"): (self._handle_history, CommandClass.PUBLIC),
            ("/trace", "trace"): (self._handle_trace, CommandClass.ADMIN),  # Admin only
            ("/jobs", "jobs", "queue"): (self._handle_jobs, CommandClass.PUBLIC),
            # F30: Chat Assistant
            ("/chat", "chat"): (self._handle_chat, CommandClass.PUBLIC),
        }

        # Find Handler
        handler = None
        requires_admin = False

        canonical_cmd = cmd  # Fallback
        for aliases, (func, cmd_class) in handlers.items():
            if cmd in aliases:
                handler = func
                default_class = cmd_class
                # R80 Remediation: Use canonical command (first alias) for policy checks
                # This prevents "run" vs "/run" bypass issues.
                if isinstance(aliases, tuple):
                    # Convention: first alias is canonical (e.g. "/run")
                    canonical_cmd = aliases[0]
                else:
                    canonical_cmd = aliases
                break

        if not handler:
            return CommandResponse(
                text=f"Unknown command: {cmd}. Type /help for options."
            )

        # R80: Centralized Authorization Gate
        # Pass canonical_cmd to ensure policy matches aliases correctly
        if auth_err := self._check_command_authz(canonical_cmd, req, default_class):
            return auth_err

        # Execute
        try:
            return await handler(req, args)
        except Exception as e:
            logger.exception(f"Command execution error {cmd}: {e}")
            return CommandResponse(text=f"[Internal Error] {str(e)}")

    def _is_admin(self, user_id: str) -> bool:
        return str(user_id) in self.config.admin_users

    def _check_command_authz(
        self, cmd: str, req: CommandRequest, default_class: CommandClass
    ) -> Optional[CommandResponse]:
        """
        R80: Verify command authorization policy.
        Returns None if allowed, or CommandResponse(text=error) if denied.
        """
        policy = self.config.command_policy

        # 1. Resolve Effective Class (Handle per-command overrides)
        # Note: 'cmd' here is the canonical parsed command string (lowercase), e.g., "/run" or "run"
        # The overrides dict might use "/run" or "run", we should check both or normalize.
        # Currently, the router logic normalized `cmd` from input (lines 90-101).
        # We'll check exact match against the override key.
        eff_class = policy.command_overrides.get(cmd, default_class)

        # 2. Check AllowFrom List (Explicit User Allow)
        # If an explicit AllowFrom list exists for this class, the user MUST be in it.
        # This takes precedence over role logic.
        allowed_users = policy.allow_from.get(eff_class)
        if allowed_users is not None and len(allowed_users) > 0:
            if str(req.sender_id) not in allowed_users:
                # If explicit allow-list is active, even admins must be in it?
                # Decision: YES, for strict compliance. If you want admins, add them to the list.
                # However, for usability, usually admins are implied.
                # Let's stick to "Explicit List Wins" for R80 strict mode.
                return CommandResponse(
                    text="[Access Denied] You are not in the allow-list for this command."
                )
            # If in list, proceed (bypass default role checks? No, usually allows)
            return None

        # 3. Default Role Logic
        if eff_class == CommandClass.ADMIN:
            if not self._is_admin(req.sender_id):
                return CommandResponse(
                    text="[Access Denied] This command requires Admin privileges."
                )

        # PUBLIC and RUN are allowed by default (RUN checks trust internally)
        return None

    def _is_trusted(self, req: CommandRequest) -> bool:
        """
        Trusted users can execute /run immediately.
        Untrusted users are routed to approval flow.
        """
        if self._is_admin(req.sender_id):
            return True

        platform = (req.platform or "").lower()
        sender_id = str(req.sender_id)
        channel_id = str(req.channel_id)

        if platform == "telegram":
            try:
                uid = int(sender_id)
            except Exception:
                uid = None
            try:
                cid = int(channel_id)
            except Exception:
                cid = None
            if uid is not None and uid in self.config.telegram_allowed_users:
                return True
            if cid is not None and cid in self.config.telegram_allowed_chats:
                return True
            return False

        if platform == "discord":
            if sender_id in self.config.discord_allowed_users:
                return True
            if channel_id in self.config.discord_allowed_channels:
                return True
            return False

        if platform == "line":
            if sender_id in self.config.line_allowed_users:
                return True
            if channel_id in self.config.line_allowed_groups:
                return True
            return False

        if platform == "whatsapp":
            if sender_id in self.config.whatsapp_allowed_users:
                return True
            return False

        if platform == "wechat":
            if sender_id in self.config.wechat_allowed_users:
                return True
            return False

        if platform == "kakao":
            if sender_id in self.config.kakao_allowed_users:
                return True
            return False

        if platform == "slack":
            if sender_id in self.config.slack_allowed_users:
                return True
            if channel_id in self.config.slack_allowed_channels:
                return True
            return False

        # Unknown platform: trust only admins
        return False

    # --- Handlers ---

    async def _handle_status(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        health = await self.client.get_health()
        queue = await self.client.get_prompt_queue()

        # New standardized response handling
        health_ok = health.get("ok")

        status_icon = "Online" if health_ok else "Offline"
        details = []

        if health_ok:
            data = health.get("data", {})
            stats = data.get("stats", {})
            details.append(f"Logs: {stats.get('logs_processed', 0)}")
            details.append(f"Errors: {stats.get('errors_captured', 0)}")

            q_res = queue.get("data", {})
            q_rem = q_res.get("exec_info", {}).get("queue_remaining", 0)
            details.append(f"Queue: {q_rem}")
        else:
            details.append(f"Error: {health.get('error')}")

        return CommandResponse(
            text=f"[{status_icon}] System Status\n"
            + "\n".join(f"- {d}" for d in details)
        )

    def _require_admin_token_configured(self) -> Optional[CommandResponse]:
        """
        F32 WP3: Check if admin token is configured before running admin commands.
        Fail-fast with clear error message instead of 403/500 later.

        IMPORTANT (recurring CI failure mode):
        - Admin-only commands are gated by BOTH:
          (1) sender is an admin user, AND
          (2) the connector admin token is configured (OPENCLAW_CONNECTOR_ADMIN_TOKEN).
        - Unit tests that exercise admin command handlers MUST set `config.admin_token`,
          otherwise they will correctly receive the config error response.
        """
        if not self.config.admin_token:
            return CommandResponse(
                text="[Error] Admin token not configured. Set OPENCLAW_CONNECTOR_ADMIN_TOKEN and restart connector."
            )
        return None

    async def _handle_run(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        if not args:
            return CommandResponse(
                text="Usage: /run <template_id> [prompt text] [key=value ...] [--approval]"
            )

        # Parse flags
        explicit_approval = False
        clean_args = []
        for arg in args:
            if arg in ("--require-approval", "--approval", "-a"):
                explicit_approval = True
            else:
                clean_args.append(arg)

        if not clean_args:
            return CommandResponse(text="Usage: /run <template_id> ...")

        template_id = clean_args[0]
        inputs: Dict[str, str] = {}
        free_text_parts: List[str] = []
        for arg in clean_args[1:]:
            if "=" in arg:
                k, v = arg.split("=", 1)
                inputs[k.strip()] = v.strip()
            else:
                free_text_parts.append(arg)

        # If user provided free text without key=value, treat it as the prompt.
        # We map it to a best-effort prompt key (prefers template metadata if available).
        if free_text_parts:
            prompt_key = await self._resolve_prompt_key(template_id)
            if prompt_key not in inputs:
                inputs[prompt_key] = " ".join(free_text_parts).strip()
            elif self.config.debug:
                logger.info(
                    "DEBUG /run free-text ignored (prompt key already set): %s",
                    prompt_key,
                )

        # NOTE: Debug-only payload logging for troubleshooting prompt mismatches.
        # Enable with OPENCLAW_CONNECTOR_DEBUG=1 to log template_id + inputs.
        if self.config.debug:
            logger.info(
                "DEBUG /run payload: template=%s inputs=%s approval_flag=%s trusted=%s",
                template_id,
                inputs,
                explicit_approval,
                self._is_trusted(req),
            )

        trusted = self._is_trusted(req)
        require_approval = explicit_approval or (not trusted)

        res = await self.client.submit_job(
            template_id, inputs, require_approval=require_approval
        )
        if res.get("ok"):
            data = res.get("data", {})
            trace_id = data.get("trace_id", "unknown")

            if data.get("pending"):
                approval_id = data.get("approval_id", "unknown")
                msg = f"[Approval Requested]\nID: {approval_id}\nTrace: {trace_id}"
                if "expires_at" in data:
                    msg += f"\nExpires: {data['expires_at']}"
                if self.poller:
                    # IMPORTANT:
                    # For untrusted users, approvals are done in the OpenClaw UI.
                    # We must start tracking the approval_id so we can map
                    # approval_id -> executed_prompt_id later and auto-deliver images.
                    self.poller.track_approval(
                        approval_id, req.platform, req.channel_id, req.sender_id
                    )
                return CommandResponse(text=msg)
            else:
                prompt_id = data.get("prompt_id", "unknown")
                if self.poller:
                    self.poller.track_job(
                        prompt_id, req.platform, req.channel_id, req.sender_id
                    )

                return CommandResponse(
                    text=f"[Job Submitted]\nID: {prompt_id}\nTemplate: {template_id}\nTrace: {trace_id}"
                )
        else:
            err = res.get("error", "Unknown error")
            return CommandResponse(text=f"[Submission Failed] Reason: {err}")

    async def _resolve_prompt_key(self, template_id: str) -> str:
        """
        Best-effort prompt key resolution.
        Prefer template metadata (allowed_inputs), then fall back to common names.
        """
        meta = await self._get_template_meta(template_id)
        allowed = meta.get("allowed_inputs") or []

        # If template explicitly declares a single input, use it.
        if isinstance(allowed, list) and len(allowed) == 1:
            return str(allowed[0])

        preferred = ("positive_prompt", "prompt", "text", "positive", "caption")
        if isinstance(allowed, list):
            for key in preferred:
                if key in allowed:
                    return key

        # Default fallback
        return "positive_prompt"

    async def _get_template_meta(self, template_id: str) -> Dict[str, Any]:
        if template_id in self._template_meta_cache:
            return self._template_meta_cache[template_id]
        try:
            res = await self.client.get_templates()
            if res.get("ok"):
                for item in res.get("templates", []) or []:
                    if item.get("id") == template_id:
                        self._template_meta_cache[template_id] = item
                        return item
        except Exception as e:
            if self.config.debug:
                logger.info(f"DEBUG template meta fetch failed: {e}")
        return {}

    async def _handle_interrupt(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        # F32 WP3: Guard
        if err := self._require_admin_token_configured():
            return err

        # Remediation: Global Interrupt
        res = await self.client.interrupt_output()
        if res.get("ok"):
            return CommandResponse(text="[Stop] Global Interrupt sent to ComfyUI.")
        else:
            return CommandResponse(text=f"[Stop Failed] {res.get('error')}")

    async def _handle_approvals_list(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        # F32 WP3: Guard
        if err := self._require_admin_token_configured():
            return err

        res = await self.client.get_approvals()
        if not res.get("ok"):
            return CommandResponse(
                text=f"[Error] Failed to list approvals: {res.get('error')}"
            )

        items = res.get("items", [])
        if not items:
            return CommandResponse(text="No pending approvals.")

        pending_count = res.get("pending_count")
        lines = []
        for i in items:
            # IMPORTANT (stability): the backend approval schema uses:
            # `approval_id`, `template_id`, `status`, `requested_by`, `source`.
            # Do not “simplify” these keys to `id/description/requester` unless you also
            # update the backend API + all tests. This mismatch previously caused silent
            # bad output and brittle regressions.
            approval_id = i.get("approval_id") or i.get("id") or "unknown"
            template_id = i.get("template_id") or "unknown"
            status = i.get("status") or "unknown"
            requested_by = i.get("requested_by") or "unknown"
            source = i.get("source") or "unknown"

            lines.append(
                f"- {approval_id} [{status}] template={template_id} by={requested_by} source={source}"
            )

        header = "Pending Approvals"
        if isinstance(pending_count, int):
            header += f" ({pending_count})"
        return CommandResponse(text=header + ":\n" + "\n".join(lines))

    async def _handle_approve(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        if not args:
            return CommandResponse(text="Usage: /approve <id>")

        # F32 WP3: Guard
        if err := self._require_admin_token_configured():
            return err

        # Assuming auto_execute=True by default for chat logic
        res = await self.client.approve_request(args[0], auto_execute=True)
        if not res.get("ok"):
            return CommandResponse(text=f"[Failed] {res.get('error')}")

        data = res.get("data", {})
        msg = f"[Approved] {args[0]}"

        # Phase 4: Show execution result
        if "prompt_id" in data:
            pid = data["prompt_id"]
            msg += f"\nExecuted: {pid}"
            if self.poller:
                # Approval request might have come from different flow, but usually user invoking /approve
                # wants the result. Using current req context is safest assumption for "ChatOps".
                self.poller.track_job(pid, req.platform, req.channel_id, req.sender_id)
        elif data.get("executed") is False:
            msg += "\n(Not Executed)"
            if err := data.get("execution_error"):
                msg += f"\nError: {err}"

        return CommandResponse(text=msg)

    async def _handle_reject(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        if not args:
            return CommandResponse(text="Usage: /reject <id> [reason]")

        # F32 WP3: Guard
        if err := self._require_admin_token_configured():
            return err

        reason = " ".join(args[1:]) if len(args) > 1 else "Rejected via chat"
        res = await self.client.reject_request(args[0], reason)
        if not res.get("ok"):
            return CommandResponse(text=f"[Failed] {res.get('error')}")

        return CommandResponse(text=f"[Rejected] {args[0]}")

    async def _handle_schedules_list(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        # F32 WP3: Guard
        if err := self._require_admin_token_configured():
            return err

        res = await self.client.get_schedules()
        if not res.get("ok"):
            return CommandResponse(text=f"[Error] {res.get('error')}")

        scheds = res.get("schedules", [])
        if not scheds:
            return CommandResponse(text="No schedules found.")

        lines = []
        for s in scheds:
            status = "+" if s.get("enabled") else "-"
            lines.append(
                f"[{status}] {s.get('id')}: {s.get('cron')} - {s.get('template_id')}"
            )

        return CommandResponse(text="Schedules:\n" + "\n".join(lines))

    async def _handle_schedule_subcommand(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        if len(args) < 2:
            return CommandResponse(text="Usage: /schedule <run|toggle> <id>")

        # F32 WP3: Guard
        if err := self._require_admin_token_configured():
            return err

        sub = args[0].lower()
        sid = args[1]

        if sub == "run":
            res = await self.client.run_schedule(sid)
            if not res.get("ok"):
                return CommandResponse(text=f"[Error] {res.get('error')}")
            return CommandResponse(text=f"[Success] Schedule {sid} triggered manually.")
        else:
            return CommandResponse(text="Not implemented yet.")

    async def _handle_help(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        return CommandResponse(
            text=(
                "OpenClaw Connector\n"
                "/status - Check system health and queue\n"
                "/run <template> [prompt] [k=v] - Run a generation (trusted users auto-exec; others require approval)\n"
                "/stop - Global Interrupt (Admin)\n"
                "/history <id> - Job details\n"
                "/jobs - Queue summary\n"
                "Admin Only:\n"
                "/approvals - List pending approvals\n"
                "/approve <id>, /reject <id>\n"
                "/schedules, /schedule run <id>\n"
                "/trace <id> - Execution trace"
            )
        )

    async def _handle_history(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        if not args:
            return CommandResponse(text="Usage: /history <prompt_id>")
        res = await self.client.get_history(args[0])
        if not res.get("ok"):
            return CommandResponse(text=f"[Error] {res.get('error')}")

        # Simple format
        data = res.get("data", {})
        status = data.get("status", {}).get("status_str", "unknown")
        # Assuming backend returns a structure we can summarise
        return CommandResponse(
            text=f"Job {args[0]}: {status}\nFull details: not implemented in connector view yet."
        )

    async def _handle_trace(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        if not args:
            return CommandResponse(text="Usage: /trace <prompt_id>")

        # F32 WP3: Guard
        if err := self._require_admin_token_configured():
            return err

        res = await self.client.get_trace(args[0])
        if not res.get("ok"):
            return CommandResponse(text=f"[Error] {res.get('error')}")

        # Dump trace
        sanitized = sanitize_operator_payload(res.get("data"))
        return CommandResponse(text=f"Trace {args[0]}: {str(sanitized)[:1000]}...")

    async def _handle_jobs(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        # Try native /openclaw/jobs first
        res = await self.client.get_jobs()
        if res.get("ok"):
            # Format nice summary
            return CommandResponse(
                text=f"Default Jobs View: {sanitize_operator_payload(res.get('data'))}"
            )

        # Fallback: Queue
        q = await self.client.get_prompt_queue()
        if q.get("ok"):
            rem = q.get("data", {}).get("exec_info", {}).get("queue_remaining", "?")
            return CommandResponse(text=f"[Fallback] Queue Remaining: {rem}")

        return CommandResponse(text="[Error] Could not fetch jobs or queue.")

    # -------------------------------------------------------------------------
    # F30: Chat LLM Assistant
    # -------------------------------------------------------------------------

    async def _handle_chat(
        self, req: CommandRequest, args: List[str]
    ) -> CommandResponse:
        """
        /chat [subcommand] <message>
        Subcommands: run, template, status
        Default: general chat

        Security: Never auto-executes commands. Only suggests command text.
        """
        llm = LLMClient(self.client)

        if not await llm.is_configured():
            return CommandResponse(
                text="[Chat Error] LLM not configured. Configure in OpenClaw Settings."
            )

        # Parse subcommand
        if not args:
            return CommandResponse(
                text="Usage: /chat <message> or /chat run|template|status <request>"
            )

        subcommand = args[0].lower()
        message = " ".join(args[1:]) if len(args) > 1 else ""

        trust_level = "TRUSTED" if self._is_trusted(req) else "UNTRUSTED"

        if subcommand == "run":
            return await self._chat_run(llm, message, trust_level)
        elif subcommand == "template":
            return await self._chat_template(llm, message)
        elif subcommand == "status":
            return await self._chat_status(llm)
        else:
            # General chat: first word is part of message
            full_message = " ".join(args)
            return await self._chat_general(llm, full_message, trust_level)

    async def _chat_general(
        self, llm: LLMClient, message: str, trust_level: str
    ) -> CommandResponse:
        """General chat with assistant."""
        # S44: Semantic Guard Evaluation
        decision = self.semantic_guard.evaluate_request(message, {"trust": trust_level})

        if decision.action == GuardAction.DENY:
            return CommandResponse(
                text=(
                    "[Blocked] Request denied by semantic policy "
                    f"({decision.reason}). {self._policy_kv(decision.to_contract())}"
                )
            )

        system_prompt = CHAT_SYSTEM_PROMPT.format(trust_level=trust_level)
        response = await llm.chat(system_prompt, message)

        # S44: Output Validation + SAFE_REPLY sanitization.
        try:
            response = self.semantic_guard.validate_output(
                response, "general", decision.action
            )
        except ValueError as e:
            return CommandResponse(
                text=(
                    "[Validation Error] Assistant output invalid: "
                    f"{e}. {self._policy_kv({'code': 'semantic_output_invalid', 'severity': 'medium', 'action': 'deny', 'reason': str(e)})}"
                )
            )

        if decision.action == GuardAction.SAFE_REPLY:
            safe_response = (
                response
                or "I can help with general guidance, but commands are restricted for this request."
            )
            return CommandResponse(
                text=(
                    f"[Safe Mode] {safe_response}\n\n"
                    f"(Policy: {self._policy_kv(decision.to_contract())})"
                )
            )

        return CommandResponse(text=response)

    async def _chat_run(
        self, llm: LLMClient, request: str, trust_level: str
    ) -> CommandResponse:
        """Suggest a /run command based on user request."""
        if not request:
            return CommandResponse(
                text="Usage: /chat run <description of what you want>"
            )

        # S44: Semantic Guard Evaluation
        decision = self.semantic_guard.evaluate_request(request, {"trust": trust_level})

        if decision.action == GuardAction.DENY:
            return CommandResponse(
                text=(
                    "[Blocked] Request denied by semantic policy "
                    f"({decision.reason}). {self._policy_kv(decision.to_contract())}"
                )
            )

        # Force Approval Override based on Risk
        force_approval_policy = decision.action == GuardAction.FORCE_APPROVAL

        # Get available templates (simplified - could fetch from API)
        templates = "txt2img, img2img, upscale (examples)"

        system_prompt = CHAT_SYSTEM_PROMPT.format(trust_level=trust_level)
        user_prompt = f"""User wants to run a generation. Suggest a `/run` command.

Request: {request}
Available templates: {templates}
Trust level: {trust_level}

Remember: {"add --approval flag" if trust_level == "UNTRUSTED" else "no --approval needed"}.
Output only the command in a code block."""

        response = await llm.chat(system_prompt, user_prompt)

        # S44: Output Structure Validation
        try:
            response = self.semantic_guard.validate_output(
                response, "run", decision.action
            )
        except ValueError as e:
            return CommandResponse(
                text=(
                    "[Validation Error] Assistant output invalid: "
                    f"{e}. {self._policy_kv({'code': 'semantic_output_invalid', 'severity': 'high', 'action': 'deny', 'reason': str(e)})}"
                )
            )

        # R97: Command Firewall - Extract and Validate
        import re

        cmd_match = re.search(r"```(?:bash)?\s*(.*?)\s*```", response, re.DOTALL)
        raw_cmd = cmd_match.group(1).strip() if cmd_match else response.strip()

        # Validate through Firewall
        normalized = self.command_firewall.validate_suggestion(raw_cmd)

        if not normalized.is_safe:
            return CommandResponse(
                text=(
                    "[Safety Block] Assistant suggested unsafe command: "
                    f"{normalized.safety_reason}. {self._policy_kv(normalized.to_contract())}"
                )
            )

        # R97: Strict /run enforcement (Remediation for Medium Severity)
        # CRITICAL: keep this check. /chat run must never emit non-/run commands.
        if normalized.command != "/run":
            return CommandResponse(
                text=(
                    "[Policy Block] Only /run commands are allowed in this mode. "
                    f"Got: {normalized.command}. "
                    f"{self._policy_kv({'code': 'firewall_non_run_command', 'severity': 'high', 'action': 'deny', 'reason': 'non_run_command_in_run_mode'})}"
                )
            )

        # R97/S44: Apply Policy Overrides
        # If risk was elevated, ensure --approval is present
        if (
            force_approval_policy
            and "--approval" not in normalized.args
            and "approval" not in normalized.flags
        ):
            normalized.args.append("--approval")

        final_cmd = normalized.to_string()

        # Return as code block for easy copy-paste (or auto-execution UI cues)
        if force_approval_policy:
            return CommandResponse(
                text=(
                    f"```\n{final_cmd}\n```\n"
                    f"(Policy: {self._policy_kv(decision.to_contract())})"
                )
            )
        return CommandResponse(text=f"```\n{final_cmd}\n```")

    @staticmethod
    def _policy_kv(contract: Dict[str, Any]) -> str:
        ordered = ("code", "severity", "action", "reason")
        parts = []
        for key in ordered:
            value = contract.get(key)
            if value is not None:
                parts.append(f"{key}={value}")
        return "[" + ", ".join(parts) + "]"

    async def _chat_template(self, llm: LLMClient, request: str) -> CommandResponse:
        """Generate a template JSON suggestion."""
        if not request:
            return CommandResponse(text="Usage: /chat template <description>")

        system_prompt = CHAT_SYSTEM_PROMPT.format(trust_level="N/A")
        user_prompt = f"""Generate a workflow template JSON for this request:

Request: {request}

Output:
1. Suggested filename
2. Template JSON in a code block

Keep it minimal."""

        response = await llm.chat(system_prompt, user_prompt)
        return CommandResponse(text=response)

    async def _chat_status(self, llm: LLMClient) -> CommandResponse:
        """Summarize system status using LLM."""
        # Fetch status data
        health = await self.client.get_health()
        jobs = await self.client.get_jobs()
        queue = await self.client.get_prompt_queue()

        status_data = {
            "health": health.get("data", {}) if health.get("ok") else "unavailable",
            "jobs": jobs.get("data", {}) if jobs.get("ok") else "unavailable",
            "queue": queue.get("data", {}) if queue.get("ok") else "unavailable",
        }

        system_prompt = CHAT_SYSTEM_PROMPT.format(trust_level="N/A")
        user_prompt = CHAT_STATUS_PROMPT.format(status_data=status_data)

        response = await llm.chat(system_prompt, user_prompt)
        return CommandResponse(text=response)
