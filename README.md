# ComfyUI-OpenClaw

![OpenClaw /run command example](assets/run.png)

ComfyUI-OpenClaw is a **security-first orchestration layer** for ComfyUI that combines hardened automation APIs, embedded operator UX, and production deployment controls:

- **LLM-assisted nodes** (planner/refiner/vision/batch variants)
- **A built-in extension UI** (`OpenClaw` panel)
- **A standalone Remote Admin Console** (`/openclaw/admin`) for mobile/remote browser operations
- **A secure-by-default HTTP API** for automation (webhooks, triggers, schedules, approvals, presets, rewrite recipes, model manager)
- **Public-ready control-plane split architecture** (embedded UX + externalized high-risk control surfaces)
- **Verification-first hardening lanes** (coverage governance, route drift, real-backend E2E, adversarial fuzz/mutation gates)
- **Now supports 8 major messaging platforms, including Discord, Telegram, WhatsApp, LINE, WeChat, KakaoTalk, Slack, and Feishu/Lark.**
- **And more exciting features being added continuously**

---
<br>

<div align="center">
  <img src="assets/adminMobileConsole.png" width="70%" />
</div>

<br>
<br>

```
ComfyUI Process (single Python process + shared aiohttp app)
│
├── ComfyUI Core (owned by ComfyUI)
│   ├── Native routes: /prompt, /history, /view, /upload, /ws, ...
│   └── Execution engine + model runtime
│
└── OpenClaw package (loaded from custom_nodes/comfyui-openclaw)
    ├── Registers OpenClaw-managed routes into the same PromptServer app:
    │   ├── /openclaw/*
    │   ├── /api/openclaw/* (browser/API shim)
    │   └── Legacy aliases: /moltbot/* and /api/moltbot/*
    ├── Security/runtime modules (startup gate, RBAC, CSRF, HMAC, audit, SSRF controls)
    ├── Automation services (approvals, schedules, presets, webhook/assist flows)
    ├── State + secrets storage (openclaw_state/*)
    ├── Embedded frontend extension (OpenClaw sidebar tabs) + remote admin page (/openclaw/admin)
    └── ComfyUI nodes exported by this pack (planner/refiner/image-to-prompt/batch variants)

Optional companion process (outside the ComfyUI process):
└── Connector sidecar (Telegram/Discord/LINE/WhatsApp/WeChat/Kakao/Slack/Feishu) -> calls OpenClaw HTTP APIs
```

This project is designed to make **ComfyUI a reliable automation target** with an explicit admin boundary and hardened defaults.
<br>

<details><summary><h2>Security stance (how this project differs from convenience-first automation packs) - Click to expand</h2></summary>

- Public and hardened deployment postures are fail-closed by design: shared-surface acknowledgement, startup gates, route-plane governance, and control-plane split all aim to reduce accidental exposure.
- Admin writes, webhook ingress, and bridge worker paths are protected as explicit trust boundaries rather than convenience-only localhost helpers.
- Connector ingress keeps allowlist and policy checks as first-class controls, with degraded/public posture handled deliberately instead of silently widening access.
- Interactive connector actions are treated as a security boundary too: callback-capable platforms use signed envelopes, timestamp/replay guards, dedupe, and explicit policy mapping instead of trusting button actions as implicit admin intent.
- Outbound egress is constrained: callback delivery and custom LLM base URLs stay behind SSRF-safe validation, exact-host policy, and explicit insecure overrides.
- Secret handling stays server-side: browser storage is not used for secrets, local secret-manager integration is opt-in, and secrets-at-rest / token lifecycle controls are treated as operational boundaries.
- Multi-tenant mode is isolation-first: tenant mismatches fail closed across config, secret sources, connector installations, approvals, visibility, and execution budgets.
- Connector multi-workspace and multi-account bindings are secret-ref-only and fail-closed by design, so tenant/binding mismatches degrade to explicit rejection paths instead of silently reusing the wrong installation context.
- Operator-facing payloads default to redaction for provider reasoning-like content, while audit trails, diagnostics, and runtime guardrails remain explicit and tamper-evident.
- Verification is part of the security model: route drift checks, coverage governance, adversarial gates, and doctor/compatibility diagnostics are all wired into CI-parity workflows.

Deployment profiles and hardening references:
- [Security Deployment Guide](docs/security_deployment_guide.md)
- [Security Key/Token Lifecycle SOP](docs/security_key_lifecycle_sop.md)
- [Security Checklist](docs/security_checklist.md)
- [Runtime Hardening and Startup](docs/runtime_hardening_and_startup.md)
- [Threat Model](docs/release/threat_model.md)
- [R69 Frontend Migration Decision](docs/r69_ui_framework_migration_decision.md)

</details>



<details><summary><h2>Latest Updates - Click to expand</h2></summary>

<details>

<summary><strong>PNG Info sidebar workflow added with ComfyUI metadata extraction, better large-image handling, and lower-noise operator alerts</strong></summary>

- Added a new `PNG Info` sidebar tab with drag-and-drop, file picker, scoped paste, preview rendering, prompt copy actions, structured summary cards, and raw metadata inspection for saved generation images.
- Added backend metadata parsing for A1111 infotext and ComfyUI `prompt` / `workflow` metadata, including prompt/sampler/model/size extraction from standard ComfyUI graphs and a larger dedicated payload ceiling for original metadata-bearing images.
- Improved operator-facing UX by making large-image failures explain the metadata-preservation constraint more clearly, letting the PNG Info input area scroll with the rest of the content, and moving prompt copy surfaces to the top of the information area.
- Reduced noise in ComfyUI prompt extraction so generic custom `CLIPTextEncode*` nodes now prefer explicit prompt-bearing keys instead of surfacing parser/config strings as if they were prompt text.
- Tightened queue-monitor alert sensitivity so sidebar startup races no longer generate persistent disconnect noise unless the backend stays unavailable long enough to look like a real incident.

</details>

<details>

<summary><strong>Repo-native CodeQL baseline and residual GitHub Security verification chain completed</strong></summary>

- Added a versioned GitHub Actions `CodeQL` workflow that scans Python, JavaScript/TypeScript, and GitHub Actions on push, pull request, manual dispatch, and a weekly schedule, so static security analysis now has an explicit in-repo baseline instead of depending only on opaque UI configuration.
- Kept the rollout visibility-first: CodeQL is now a GitHub Actions security lane and documented CI boundary, but it is not treated as a new local mandatory full-SOP command; local acceptance stays seam-first while GitHub-hosted scanning owns the repository-wide static-analysis baseline.
- Closed the acceptance-gap that surfaced during the residual verification push by propagating `defusedxml` through `requirements.txt`, preflight checks, local acceptance bootstraps, and CI preflight installation, with a repo-local dependency-parity regression seam to prevent future drift.
- Re-ran the full existing pre-push acceptance gate successfully after the parity fix: detect-secrets, pre-commit, governance verification, backend full suites, real-backend lanes, adaptive adversarial gate, and Playwright E2E.

</details>

<details>

<summary><strong>Security hardening wave completed across CI permissions, path boundaries, redaction, connector ingress, notification rendering, and GitHub security closure</strong></summary>

- Verified the minimal Vite development-tooling hotfix path already merged cleanly, so the repo now resolves the patched `vite` version without broadening the frontend toolchain scope.
- Added explicit least-privilege GitHub Actions `permissions:` declarations and a repo-local regression seam so workflow token scope drift is now treated as a tracked security regression instead of an implicit repository default.
- Hardened checkpoint, integrity, and managed model-transfer path handling to fail closed on invalid IDs, traversal markers, and rebased install targets, with focused regression coverage on every flagged filesystem sink.
- Replaced raw security-sensitive identifiers in bridge, auth, audit, proxy, and safe-IO diagnostics with stable redacted tags, and upgraded sensitive hashing paths to keyed constructions instead of plain or hardcoded hash inputs.
- Tightened connector ingress failure handling so WeChat rejects unsafe XML declarations before parser entry, while Slack and Feishu return bounded external failure text/codes instead of echoing raw exception detail.
- Added a targeted Playwright seam proving notification payloads render as escaped text rather than live markup, locking the production notification sink against future HTML-interpolation regressions.
- Completed the GitHub-side closeout for the same wave by switching the repository from GitHub code-scanning default setup to the versioned advanced CodeQL workflow, dismissing the final residual CodeQL false positives with recorded rationale, resolving the historical docs-only secret-scanning false positive, and bringing GitHub `Code scanning` / `Secret scanning` back to `0` open findings as of `2026-04-08`.

</details>

<details>

<summary><strong>Desktop host parity lane, refreshed compatibility anchors, and live-backend mock parity completed</strong></summary>

- Added an executable desktop-host regression lane for the OpenClaw sidebar and Remote Admin Console, so desktop-specific runtime drift is now verified separately from standalone frontend assumptions instead of being left to unit-only host detection.
- Added shared Playwright host/runtime shims and remote-admin baseline mocks so desktop-host metadata, approvals refresh behavior, and host-sensitive UI evidence stay deterministic under the test harness.
- Refreshed the recorded compatibility anchors against the current reference ComfyUI, ComfyUI Frontend, and Desktop hosts, keeping desktop embedded-frontend lag explicit in the published compatibility matrix and governance checks.
- Updated the mocked live-backend parity lane so image-output surfaces now return deterministic mocked output artifacts, closing the remaining preview/result gap in the real-backend-style E2E contract.
- Re-validated the combined batch on WSL with the full SOP gate: detect-secrets, pre-commit, governance verification, backend full suites, strict implementation-record lint, real-backend lanes, adaptive adversarial gate, and Playwright E2E.

</details>

<details>

<summary><strong>Feishu connector chain completed with long-connection transport, tenant-aware bindings, and interactive approval callbacks</strong></summary>

- Added a Feishu/Lark connector baseline that supports both long-connection and webhook ingress modes, keeps transport behavior aligned through the shared connector authorization model, and makes host-domain differences explicit through `feishu` vs `lark` account binding metadata instead of ad hoc runtime branching.
- Added Feishu account/workspace installation bindings with fail-closed resolution, tenant-aware diagnostics, normalized installation records, and support for multi-account binding manifests so one connector runtime can host more than one Feishu workspace contract safely.
- Added Feishu interactive-card callback handling for approval and command actions, including signed callback envelopes, stale/replay rejection, duplicate-action dedupe, actor-context mapping, and explicit approval downgrade when untrusted users press run-affecting actions.
- Updated the connector runtime so websocket-mode Feishu deployments also host the callback ingress surface, keeping interactive-card approvals available even when message ingress is handled over long connection instead of pure webhook mode.
- Re-validated the full Feishu batch on WSL with the full SOP gate: detect-secrets, pre-commit, governance verification, backend full suites, strict implementation-record lint, real-backend lanes, adaptive adversarial gate, and Playwright E2E.

</details>

<details>

<summary><strong>Snapshot-first diagnostics, delta polling contracts, schema alignment, and optional-dependency import hardening completed</strong></summary>

- Moved Explorer inventory diagnostics onto a snapshot-first contract so `/openclaw/preflight/inventory` returns quickly with explicit `snapshot_ts`, `scan_state`, `stale`, and `last_error` metadata while deep refresh continues in the background.
- Hardened event and managed-download polling around deterministic cursor metadata, so operator surfaces can resume from `effective` and `next` sequence markers instead of relying on duplicate-prone full refresh loops.
- Unified webhook and managed-model request/documentation fixtures around one shared contract bundle, tightened model-import destination validation to reject traversal markers fail-closed, and kept the published API/OpenAPI surfaces aligned with the runtime validators.
- Removed the remaining import-time `aiohttp` traps from high-impact route/service modules by moving them onto one bounded compatibility seam, so minimal environments degrade deterministically at call time instead of crashing on module import.
- Re-validated the full batch on WSL with the full SOP gate: detect-secrets, pre-commit, governance verification, backend full suites, strict implementation-record lint, real-backend lanes, adaptive adversarial gate, and Playwright E2E.

</details>

<details>

<summary><strong>Frontend host compatibility, asset-backed output interop, and CI audit alignment completed</strong></summary>

- Hardened frontend host compatibility against current standalone frontend and desktop bundle drift by moving graph/widget compatibility logic onto shared host helpers, adding explicit sidebar host-surface stamping, and surfacing desktop embedded-frontend parity through compatibility diagnostics instead of implicit assumptions.
- Added a bounded asset-output interoperability seam so classic ComfyUI history refs and newer asset-backed refs both resolve through the existing `/view` contract, preserving current temp/output behavior while allowing hash-backed previews where upstream metadata provides them.
- Updated output/history-facing frontend and backend parsers together, so `Jobs` previews, callback payload image refs, and history extraction follow one canonical path rather than duplicating view-URL assembly logic in separate layers.
- Refreshed compatibility anchors against the current reference repos and fixed the CI Python dependency audit path so the enforced audit checks declared project requirements instead of scanning unrelated runner/toolchain packages.
- Re-validated the implementation on WSL with the full SOP gate: detect-secrets, pre-commit, backend full suites, strict implementation-record lint, adaptive adversarial gate, and Playwright E2E.

</details>

<details>

<summary><strong>Exception-fidelity cleanup and verification-governance baseline completed</strong></summary>

- Preserved original traceback origins on the remaining planner/refiner/vision/config failure paths and aligned request-time default `LLMClient` refresh so runtime config hot-reload no longer mutates long-lived service state just to get a fresh client.
- Added explicit coverage governance in `pyproject.toml`, including minimum `fail_under`, visible missing-line reporting, and skip-covered output, so baseline quality drift is no longer implicit.
- Added a stdlib-only governance verifier that fails closed when coverage config, adversarial mutation thresholds, SOP guidance, or mutation-survivor allowlist shape drift away from the enforced baseline.
- Wired the governance verifier into Linux/Windows full-test flows and the repo pre-push gate, keeping local CI-parity checks aligned with the enforced verification contract.
- Re-validated the full implementation on WSL with the full SOP gate: detect-secrets, pre-commit, governance verification, backend full suites, adaptive adversarial gate, and Playwright E2E.

</details>

<details>

<summary><strong>Maintainability wave completed across routes, model operations, admin shell, and compatibility cleanup</strong></summary>

- Split route registration into focused route-family registrars while keeping one startup composition root and preserving legacy `/moltbot/*` plus `/api/*` fallback behavior.
- Split Model Manager internals into dedicated catalog, task-lifecycle, and transfer/security service slices without changing the accepted managed-download, resume, import, and recovery contract.
- Extracted sidebar notification/banner runtime and standalone admin-console browser logic into dedicated modules so the shell stays a composition root instead of a growing page-level hotspot.
- Centralized runtime generation of legacy `moltbot-*` class aliases and removed residual duplicated node image-helper wrappers, so canonical `openclaw-*` markup and shared image encoding logic now have one maintained path.
- Re-validated the full batch on WSL with the full SOP gate: detect-secrets, pre-commit, backend full suites, adaptive adversarial gate, and Playwright E2E.

</details>

<details>

<summary><strong>Slack multi-workspace installation flow completed, with final egress and notification-center hardening</strong></summary>

- Added Slack multi-workspace OAuth install/callback handling with single-use state validation, workspace-scoped installation binding, encrypted token refs, and workspace-aware reply routing for inbound events and delayed result delivery.
- Expanded connector diagnostics so Slack installation health now surfaces stable fail-closed states such as `ok`, `invalid_token`, `revoked`, `workspace_unbound`, and `degraded` without exposing token material.
- Moved Slack OAuth token exchange onto the same SSRF-safe outbound layer used by other protected network paths, closing the late-stage egress policy regression found during the full acceptance sweep.
- Fixed a notification-center persistence regression so dismissed model-manager failure alerts stay hidden after reload instead of being immediately re-created by repeated background refresh failures, while historical storage remains intact.
- Re-validated the final implementation on WSL with the full SOP gate: detect-secrets, pre-commit, backend full suites, adaptive adversarial gate, and Playwright E2E.

</details>

<details>

<summary><strong>Planning, startup/config hardening, compatibility governance, and frontend hotspot reduction batch</strong></summary>

- Normalized the active planning surface onto `.planning/roadmap.md` and clarified the docs-only test-flow exemption in the project SOP guidance.
- Hardened route/bootstrap registration around a declarative manifest and centralized validation seam so startup wiring is less fragile under delayed readiness and import-order edge cases.
- Completed the next config-unification pass around one effective-config read facade, reducing precedence drift across backend and frontend-facing config consumers.
- Centralized legacy compatibility handling for backend headers and frontend API/storage fallbacks so deprecation behavior is explicit, shared, and regression-covered.
- Split the frontend shell hotspot and LLM model-list helper logic into smaller seams, then fixed the timer-binding regression uncovered during full-gate Playwright validation.

</details>

<details>

<summary><strong>Private-host LLM SSRF contract clarified across Remote Admin, docs, and deployment guidance</strong></summary>

- Clarified that `OPENCLAW_LLM_ALLOWED_HOSTS` only extends the exact public-host allowlist for custom LLM `base_url` values and does not permit private/reserved LAN targets by itself.
- Updated Remote Admin and model-refresh SSRF error messages so operators can distinguish public-host allowlisting from the explicit insecure override required for private-IP targets.
- Documented Windows portable env inheritance expectations, including the need to set variables before launching `python_embeded\python.exe`, restart after changes, and avoid unsupported wildcard entries such as `*`.
- Fixed request-time parity so Remote Admin validation, `/openclaw/llm/models`, and outbound provider requests now honor the same explicit insecure override for intentional private-host/HTTP LLM targets.
- Added a pre-commit autofix guard that regenerates `docs/openapi.yaml` when OpenAPI contract/generator inputs change, preventing generated-spec drift from surfacing only at push time.
- Added regression coverage for the clarified SSRF error contract and re-validated with the full SOP gate.

</details>

<details>

<summary><strong>Inventory indexing moved to snapshot-first refresh with background deep-scan</strong></summary>

- Changed `/openclaw/preflight/inventory` to return a fast snapshot first, then refresh inventory state in the background instead of blocking on full directory traversal.
- Added snapshot freshness/status metadata (`snapshot_ts`, `scan_state`, `stale`, `last_error`) so the API and explorer UI can surface refresh progress and degraded scan results explicitly.
- Added bounded traversal checkpoints and background refresh scheduling to reduce latency spikes on large model directories while keeping later reads convergent.
- Added backend regression coverage for snapshot, stale/error, and API-state transitions, then validated with the full SOP gate.

</details>

<details>

<summary><strong>Model Manager reliability upgrade: resumable downloads and restart-safe recovery</strong></summary>

- Added resumable managed download support using staged `.part` artifacts plus checkpoint metadata, so interrupted transfers can continue via HTTP Range when upstream contracts are compatible.
- Added deterministic fallback-to-full restart paths when resume preconditions fail (range unsupported, validator drift, content-range mismatch) without bypassing existing provenance/SHA256 import gates.
- Added persisted download task registry with startup recovery replay and bounded replay limit control (`OPENCLAW_MODEL_DOWNLOAD_RECOVERY_REPLAY_LIMIT`) to prevent unbounded restart churn.
- Added backend regression coverage for resume success, fallback behavior, and replay-limit overflow handling, then validated with the full SOP gate.

</details>

<details>

<summary><strong>Reasoning trace redaction hardening and privileged local-debug reveal gate</strong></summary>

- Added a shared reasoning-redaction boundary helper so reasoning/thinking-like fields are stripped by default from assist responses, event/SSE payloads, trace responses, callback payloads, and connector-facing trace formatting.
- Added an explicit privileged reveal path that now requires request opt-in, server-side debug enablement, admin authorization, loopback source, and permissive local posture, with audit visibility for reveal attempts.
- Kept final user-visible answers intact while preventing internal reasoning traces from leaking through default operator-facing serializers.
- Closed a serializer compatibility regression found during full-gate validation and hardened WSL `/mnt/*` Playwright stability with environment-aware worker and readiness-timeout guardrails.
- Validated with the full SOP gate on WSL (detect-secrets, pre-commit, backend full suite, real-backend lanes, adversarial gate, and frontend Playwright E2E).

</details>

<details>

<summary><strong>Embedded model operations UX update: new Model Manager tab and Parameter Lab icon fix</strong></summary>

- Added a dedicated `Model Manager` sidebar tab for model search, managed download task queueing, task lifecycle monitoring, and completed-task import into managed install paths.
- Added frontend regression coverage for the new tab flow (sidebar visibility/switching plus queue/import interaction path in Playwright E2E).
- Fixed the `Parameter Lab` tab icon contract by using a PrimeIcon class so the tab icon renders correctly in the sidebar.

</details>

<details>

<summary><strong>Multi-tenant isolation baseline, optional local secret sourcing, and layered config unification completed</strong></summary>

- Added a fail-closed tenant boundary model with tenant-scoped config/secret resolution, connector installation isolation, approvals/presets/templates visibility boundaries, and per-tenant execution concurrency caps.
- Added optional local 1Password CLI key sourcing with explicit enablement, command allowlist, template validation, and bounded fail-closed lookup behavior.
- Unified config precedence across runtime/config/provider call paths around a shared layered resolver (`env > runtime override > persisted > default`) with compatibility aliases preserved.
- Completed full verification gate pass on `dev` (detect-secrets, pre-commit, backend unit suites, and frontend Playwright E2E).

</details>

<details>

<summary><strong>Optional local secret-manager baseline for safer key sourcing</strong></summary>

- Added a pluggable backend secret-provider chain for API keys (`env -> optional 1Password CLI -> encrypted server store -> none`) so operators can keep runtime keys out of plaintext deployment config where needed.
- Added fail-closed 1Password guardrails requiring explicit enablement, executable allowlist, command path validation, and bounded lookup timeout behavior.
- Added regression coverage for precedence resolution, allowlist/failure fallback behavior, and no-secret-leak logging expectations.
- Completed full verification gate pass on `dev` (detect-secrets, pre-commit, backend unit suites, adversarial/retry/real-backend lanes, and frontend Playwright E2E).

</details>

<details>

<summary><strong>Today’s implementation roundup across frontend quality, planner contracts, and connector security baselines</strong></summary>

- Completed the frontend quality bundle by stabilizing canonical style ownership, adding baseline frontend unit coverage, and expanding regression coverage for Library/Approvals/admin-console parity.
- Completed SSRF pinning regression hardening with dedicated no-skip coverage for pinned connect paths, multi-IP failover ordering, and TLS wrap degradation branches.
- Completed planner profile/system-prompt externalization with validated file-backed registry loading, runtime-safe fallback/reload behavior, and synchronized profile sourcing across API, node, and Planner tab.
- Completed connector contract baseline with multi-workspace installation lifecycle registry, encrypted token references, fail-closed workspace resolution, and reusable interactive callback security decisions (signature/timestamp/hash/replay/idempotency/policy mapping) plus admin diagnostics APIs.
- Completed full verification gate pass on `dev` (detect-secrets, pre-commit, backend unit suites, adversarial/retry/real-backend lanes, and frontend Playwright E2E).

</details>

<details>

<summary><strong>Connector multi-workspace installation and interactive callback contract baseline</strong></summary>

- Added a persistent connector installation registry with normalized installation identity (`platform`, `workspace_id`, `installation_id`, `token_refs`, `status`, `updated_at`) and explicit lifecycle transitions (`created`, `active`, `rotating`, `revoked`, `deactivated`, `uninstalled`).
- Enforced fail-closed workspace resolution for connector ingress (`missing`, `ambiguous`, `inactive`, and `stale token ref` bindings are rejected deterministically).
- Added reusable interactive callback security contract primitives (signed envelope, timestamp window, payload hash verification, replay/idempotency enforcement, ack/deferred callback lifecycle, and policy mapping to `public` / `run` / `admin` with explicit force-approval handling).
- Added admin read/diagnostic APIs for connector installation state, resolution evidence, and lifecycle audit visibility, with redacted outputs only.
- Completed full verification gate pass on `dev` (detect-secrets, pre-commit, backend unit suites, adversarial/retry/real-backend lanes, and frontend Playwright E2E).

</details>

<details>

<summary><strong>Planner registry externalization with runtime-safe profile alignment</strong></summary>

- Moved planner profiles and the planner system prompt into validated file-backed defaults under `data/planner/`, with state-dir override precedence for operator-managed customization without source edits.
- Added a planner profile list API so the Assist planner route, Prompt Planner node, and Planner tab resolve profiles from one synchronized source-of-truth.
- Kept runtime behavior fail-closed with schema validation, prompt placeholder validation, embedded fallback defaults, and lazy reload on planner file changes.
- Completed full verification gate pass on `dev` (detect-secrets, pre-commit, backend unit suites, adversarial/retry/real-backend lanes, and frontend Playwright E2E).

</details>

<details>

<summary><strong>Frontend quality baseline for Library and Approvals surfaces</strong></summary>

- Canonicalized active frontend styling ownership around `openclaw-*`, including shell/tab-manager cleanup and a deterministic split of `web/openclaw.css` into core and legacy-alias modules.
- Added a frontend unit-test lane with Vitest + jsdom plus baseline coverage for shared UI helpers and extracted Library tab state logic.
- Expanded Playwright coverage for `Library` and `Approvals`, including success/degraded paths and approvals parity between the sidebar and the Remote Admin Console.
- Completed full verification gate pass (detect-secrets, pre-commit, backend unit suites, adversarial/retry/real-backend lanes, and frontend Playwright E2E).

</details>

<details>

<summary><strong>Audit event clarity and connector ingress fail-closed hardening</strong></summary>

- Normalized audit helper behavior so config/secret/LLM-test convenience wrappers now emit one canonical audit event per action, reducing duplicate noise while preserving legacy compatibility paths.
- Added shared connector allowlist posture evaluation and enforced fail-closed startup behavior for public/hardened deployments when connector ingress is active without allowlist coverage.
- Kept local/permissive posture as warning-only, with synchronized visibility across startup checks, deployment profile checks, and Security Doctor diagnostics.
- Added focused regression coverage and completed full verification gate pass (detect-secrets, pre-commit, backend unit suites, and frontend Playwright E2E).

</details>

<details>

<summary><strong>Startup fail-closed bootstrap hardening and public boundary guardrail</strong></summary>

- Enforced strict fail-closed startup propagation so bootstrap security-gate failures are no longer logged-and-continued; route/worker registration now aborts deterministically on fatal startup failures.
- Added an explicit public deployment boundary acknowledgement contract:
  - `OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK=1` (legacy alias supported)
  - public profile gate now fails deterministically when this acknowledgement is missing.
- Added a dedicated Security Doctor boundary posture check and machine-readable environment marker so shared ComfyUI/OpenClaw surface risk is visible to operators.
- Synchronized deployment/operator docs for public boundary controls (reverse proxy path allowlist + network ACL requirements).
- Completed full verification gate pass (detect-secrets, pre-commit, backend unit suites, and frontend Playwright E2E).

</details>

<details>

<summary><strong>Core runtime maintainability and contract hardening batch</strong></summary>

- Refactored startup/bootstrap responsibilities into clearer service slices to keep the entry path thin and easier to validate.
- Hardened provider adapter error contracts with safer HTTP error propagation and retry-after handling consistency.
- Replaced fragile JSON object extraction logic in LLM output parsing with stdlib decoder-based behavior for stronger edge-case resilience.
- Unified node/runtime consistency by converging shared image encoding helpers and internal node naming compatibility paths.
- Added and aligned regression coverage, then completed full verification gate pass (detect-secrets, pre-commit, backend unit suites, and frontend Playwright E2E).

</details>

<details>

<summary><strong>Security and reliability hotfix chain: startup gate cleanup, atomic audit writes, and clearer CSRF override posture</strong></summary>

- Cleaned up unreachable startup security-gate code after fatal raise paths, keeping fail-closed behavior explicit and reducing maintenance ambiguity.
- Hardened append-only audit integrity by making hash-chain write flow atomic under a process lock to avoid concurrent chain-fork risk.
- Added explicit startup warning when localhost no-origin override is enabled, plus a dedicated Security Doctor posture check/violation mapping for operator visibility.
- Added focused regression coverage for startup warning/doctor posture and audit lock path behavior.
- Completed full verification gate pass (detect-secrets, pre-commit, backend unit suites, and frontend Playwright E2E).

</details>

<details>

<summary><strong>Standalone remote admin mobile console for phone/desktop operations</strong></summary>

- Added an independent remote admin entry page at `/openclaw/admin` (legacy `/moltbot/admin`), separate from the ComfyUI side panel.
- Added a mobile-first admin console layout for operational flows:
  - dashboard (health, provider/key state, scheduler/runs summary, recent error lines)
  - jobs/events (recent runs + SSE connect/poll fallback)
  - approvals (approve/reject)
  - schedules/triggers (toggle/run/fire)
  - config (read + guarded write)
  - doctor/diagnostics and quick actions (retry/model refresh/drill via existing policy gates)
- Preserved backend security boundaries: remote write actions still require explicit admin-token and remote-admin policy conditions.
- Completed full verification gate pass (detect-secrets, pre-commit, backend unit suites, and frontend Playwright E2E).

</details>

<details>

<summary><strong>Executor lane split and callback I/O isolation for better saturation resilience</strong></summary>

- Added dedicated executor lanes for LLM vs I/O workloads with bounded worker controls.
- Migrated callback delivery and outbound HTTP callback paths to the I/O lane, reducing interference with LLM execution paths.
- Added queue/saturation diagnostics and executor metrics exposure in health/stat telemetry.
- Added targeted regression coverage for lane split behavior and callback I/O lane migration.
- Completed full verification gate pass (detect-secrets, pre-commit, backend unit suites, and frontend Playwright E2E).

</details>

<details>

<summary><strong>Runtime lifecycle consistency, structured logging opt-in, and generated OpenAPI spec</strong></summary>

- Completed a focused runtime operability and contract maturity batch with full SOP verification:
  - added graceful shutdown/reset consistency hooks so scheduler/failover runtime state flushes and resets are deterministic
  - added opt-in structured JSON logging for core execution paths (including queue submit and LLM client) with bounded metadata events
  - added machine-readable OpenAPI spec generation and committed `docs/openapi.yaml` for integrator/review tooling use
  - added regression coverage for runtime lifecycle state handling, structured logging behavior, and OpenAPI generation drift
  - completed full verification gate pass (detect-secrets, pre-commit, backend unit suites, and frontend Playwright E2E)

</details>

<details>

<summary><strong>Assist streaming UX and frontend fetch-wrapper safety hardening</strong></summary>

- Completed a focused assist UX + frontend transport reliability batch with full SOP verification:
  - added optional streaming assist paths for Planner/Refiner with incremental preview updates and staged progress events
  - added backend streaming endpoints for planner/refiner assist flows with capability-gated frontend enablement and safe fallback to the existing non-stream path
  - added frontend live preview rendering for Planner/Refiner while preserving cancel/stale-response safety behavior
  - added idempotent fetch-wrapper composition guards to prevent duplicate wrapper stacking during repeated frontend bootstrap/setup
  - added backend/parser/frontend regression coverage for streaming assist behavior and fetch-wrapper idempotence, plus full verification gate pass (detect-secrets, pre-commit, backend unit suites, and frontend Playwright E2E)

</details>

<details>

<summary><strong>Recent hardening and reliability improvements: runtime guardrails, crypto drills, compatibility governance, and safer management queries</strong></summary>

- Completed a focused reliability + operations hardening batch with full SOP verification:
  - consolidated shared frontend/backed helper paths to reduce duplicated cancellation, JSON parsing, and import-fallback logic
  - added runtime guardrails diagnostics/contract enforcement so runtime-only safety limits stay visible and cannot be persisted back into config
  - added cryptographic lifecycle drill automation with machine-readable evidence for rotation, revoke, key-loss recovery, and token-compromise scenarios
  - added compatibility matrix governance metadata plus a refresh workflow script and operator-doctor freshness/drift warnings
  - hardened management query pagination behavior with deterministic malformed-input handling, bounded scans, and clearer cursor diagnostics for admin/event list paths
  - completed full verification gate pass (detect-secrets, pre-commit, backend unit suites, and frontend Playwright E2E)

</details>

<details>

<summary><strong>Latest completion: automation composer endpoint, safer payload drafting, and full verification pass</strong></summary>

- Completed the automation payload composer flow for safe draft generation:
  - added a new admin-only compose endpoint for trigger/webhook payload drafts (generate-only, no execution side effects)
  - added strict server-side validation and normalization for trigger/webhook draft payloads
  - added tool-calling schema support for automation payload composition with deterministic fallback behavior
  - exposed composer capability flag for frontend/runtime feature probing
  - added and extended backend tests for API handler, composer service, schema/validator coverage, and capability contract
  - completed full validation gate pass (detect-secrets, pre-commit, backend test lanes, adversarial smoke gate, and frontend Playwright E2E)

</details>

<details>

<summary><strong>Slack app support closeout: secure Events API ingress, connector parity, and no-skip verification lanes</strong></summary>

- Completed Slack implementation hardening chain with full SOP validation:
  - added Slack Events API adapter with signed ingress checks, replay/dedupe handling, bot-loop suppression, allowlist enforcement, and thread-aware reply delivery
  - wired Slack runtime policy into existing connector authorization boundaries so command trust behavior stays consistent with other platforms
  - added dedicated Slack verification lanes for ingress contract coverage and real-backend flow parity, both enforced by skip-policy and full-test scripts
  - added optional Slack Socket Mode fallback transport with fail-closed startup checks and transport-parity behavior aligned to Events API safety controls
  - expanded observability redaction coverage for Slack token families and added endpoint-level drift tests for logs/trace/config safety
  - aligned local full-test scripts so Slack phase-2 suites run explicitly as part of the Slack integration gate step
  - synchronized verification evidence through detect-secrets, pre-commit, backend unit + real lanes, adversarial gate, and frontend E2E full pass

</details>

<details>

<summary><strong>Post-Wave E closeout: Hardening chain completed</strong></summary>

- Completed on 2026-02 with full SOP validation:
  - Bundle A: established security invariants registry and startup/CI invariant gates, plus route-plane explicit-classification governance to prevent unmanaged endpoint exposure drift
  - Bundle B: converged outbound egress to a single safe path and added CI/local dependency parity preflight to prevent local-pass/CI-fail runtime drift
  - Bundle C: added adversarial verification execution gates (bounded fuzz + mutation smoke with artifacts) and dual-lane retry partition hardening for deterministic degrade/audit behavior
  - end-to-end verification evidence was synchronized across CI, local full-test scripts, and implementation records

</details>

<details>

<summary><strong>Wave E closeout: deployment guardrails, contract parity, and verification hardening chain completed</strong></summary>

- Completed Wave E with full SOP validation:
  - Bundle A delivered startup deployment gate enforcement and deployment-profile matrix parity, then locked critical operator flow parity (including degraded-path behavior)
  - Bundle B closed security contract parity gaps across token/mapping/route/signature state matrices and threat-intel resilience paths
  - Bundle C completed signed policy posture control, bounded security anomaly telemetry, deterministic adversarial fuzz harness coverage, and mutation-baseline evidence generation
  - full detect-secrets + pre-commit + backend unit + frontend E2E gate passed and evidence is recorded in the Bundle C implementation record

</details>

<details>

<summary><strong>Wave D closeout: control-plane split, ingress and supply-chain hardening, and verification governance baseline</strong></summary>

- Completed Wave D closeout full SOP validation:
  - enforced split-mode control-plane boundaries for public deployments while preserving embedded daily UX flows
  - finalized external control-plane adapter reliability behavior and split-mode degraded/blocked-action guidance
  - completed secrets-at-rest hardening v2 with split-compatible secret-reference behavior
  - closed bridge token lifecycle, legacy webhook ingress clamp, and public MAE route-plane enforcement gaps
  - replaced registry signature placeholder posture with trust-root based cryptographic verification and signer governance
  - established verification governance baseline with skip-budget enforcement, reject/degrade triple-assert contracts, and defect-first record lint gating
</details>

<details>

<summary><strong>Wave A/B/C closeout: stability baseline, high-risk security gates, and operator UX completion</strong></summary>

- Completed baseline runtime/config/connector stability improvements:
  - runtime provenance and manager-aware environment freshness checks
  - safer config merge behavior for object arrays
  - connector session invalidation resilience for 401/410 revoke paths
  - durable replay/idempotency storage for webhook/bridge flows
  - stricter outbound egress policy controls for callback and LLM targets
- Completed high-risk security and supply-chain hardening:
  - stronger external tool path resolution and allowlist enforcement
  - bridge/device binding hardening with mTLS validation controls
  - pack archive canonicalization and full manifest coverage enforcement
  - global DoS governance (quota/priority/storage controls)
  - signed release provenance pipeline and SBOM-integrity validation
- Completed Wave C operator UX and functionality closeout:
  - Wave C functionality closeout accepted on 2026-02-18 with full SOP validation
  - deterministic operator guidance banners and deep-link recovery behavior
  - capability-aware in-canvas quick actions with guarded mutation flow
  - Parameter Lab schema lock and bounded sweep/compare orchestration
  - compare winner-selection safety contract and expanded Wave C regression coverage

</details>

<details>

<summary><strong>Audit trail and external tool sandbox hardening closeout</strong></summary>

- Added non-repudiation audit coverage for sensitive config/secrets/tools/approvals/bridge and startup-dangerous-override paths.
- Standardized audit envelopes and append-only hash-chain logging to improve forensic traceability.
- Added stricter external tool sandbox controls:
  - hardened-mode fail-closed when sandbox posture/runtime is unsafe
  - explicit network allowlist requirement when tooling enables egress
  - pre-exec filesystem path allowlist enforcement for tool arguments
- Expanded security regression coverage for audit contract paths and sandbox policy enforcement.

</details>

<details>
<summary><strong>Endpoint inventory hardening and route drift detection coverage</strong></summary>

- Added explicit endpoint security metadata across API handlers so auth/risk posture is machine-readable and auditable.
- Added route inventory manifest generation to inspect registered API surfaces consistently.
- Added drift regression tests that fail when any registered endpoint is missing security metadata.
- Extended drift coverage to include optional bridge and packs routes to prevent false-green route scans.

</details>

<details>
<summary><strong>Operator UX improvements: context toolbox, parameter lab history/replay, and compare workflow baseline</strong></summary>

- Added in-canvas OpenClaw quick actions on node context menus: Inspect, Doctor, Queue Status, Compare, and Settings.
- Improved operator recovery flow by wiring quick actions to capability-aware targets with deterministic fallback guidance when optional endpoints are unavailable.
- Added Parameter Lab history flow so operators can browse saved experiments, load details, and replay run parameters back into the current graph.
- Added compare workflow baseline in Parameter Lab, including a dedicated compare endpoint with bounded fan-out and stricter payload validation.
- Expanded auth and regression coverage so compare routes remain admin-protected and route-registration drift is caught earlier.

</details>

<details>
<summary><strong>Pack security hardening: path traversal defense and strict API validation</strong></summary>

- Added path traversal protection for pack uninstall and pack path resolution.
- Hardened pack install path construction by validating pack metadata segments (`name`, `version`) and enforcing root-bounded path resolution.
- Added stricter input validation on pack API route handlers for pack lifecycle operations.
- Expanded regression coverage for traversal attempts and invalid input handling in pack flows.

</details>

<details>
<summary><strong>Runtime profile hardening and bridge startup compatibility checks</strong></summary>

- Added explicit runtime profiles with centralized resolution so startup behavior is deterministic across environments.
- Added a hardened startup security gate that fails closed when mandatory controls are not correctly configured.
- Added module capability boundaries so routes/workers only boot when their owning module is enabled.
- Added a bridge protocol handshake path with version compatibility checks during sidecar startup.
- Expanded regression coverage for profile resolution, startup gating, module boundaries, and bridge handshake behavior.

</details>

<details>
<summary><strong>Connector platform parity and sidecar worker runtime improvements</strong></summary>

- Added stronger KakaoTalk response handling:
  - strict QuickReply cap with safe truncation
  - empty-response guard to avoid invalid platform payloads
  - more predictable output shaping and sanitization behavior
- Added WeChat Official Account encrypted webhook support:
  - AES encrypted ingress (`encrypt_type=aes`) with signature verification and fail-closed decrypt/app-id validation
  - expanded event normalization coverage (`subscribe`, `unsubscribe`, `CLICK`, `VIEW`, `SCAN`)
  - deterministic dedupe behavior for event payloads without `MsgId`
  - bounded ACK-first flow with deferred reply handling for slow paths
- Added sidecar worker bridge alignment end-to-end:
  - worker poll/result/heartbeat bridge endpoints
  - contract-driven sidecar client endpoint resolution and idempotency header behavior
  - dedicated E2E test coverage for worker route registration, auth, and round-trip behavior

</details>

<details>
<summary><strong>Security Hardening: Auth/Observability boundaries, connector command controls, registry trust policy, transform isolation, integrity checks, and safe tooling controls</strong></summary>

- Delivered observability tier hardening with explicit sensitivity split:
  - Public-safe: `/openclaw/health`
  - Observability token: `/openclaw/config`, `/openclaw/events`, `/openclaw/events/stream`
  - Admin-only: `/openclaw/logs/tail`, `/openclaw/trace/{prompt_id}`, `/openclaw/secrets/status`, `/openclaw/security/doctor`
- Delivered constrained transform isolation hardening:
  - process-boundary execution via `TransformProcessRunner`
  - timeout/output caps and network-deny worker posture
  - feature-gated default-off behavior for safer rollout
- Delivered approval/checkpoint integrity hardening:
  - canonical JSON + SHA-256 integrity envelopes
  - tamper detection and fail-closed handling on integrity violations
  - migration-safe loading behavior for legacy persistence files
- Delivered external tooling execution policy:
  - allowlist-driven tool definitions (`data/tools_allowlist.json`)
  - strict argument validation, bounded timeout/output, and redacted output handling
  - gated by `OPENCLAW_ENABLE_EXTERNAL_TOOLS` plus admin access policy
- Extended security doctor coverage with wave-2 checks:
  - validates transform isolation posture
  - reports external tooling posture
  - verifies integrity module availability
- Auth-coverage contract tests were updated to include new tool routes and prevent future route-auth drift regressions.
- Added connector command authorization hardening:
  - separates command visibility from command execution privileges
  - centralizes per-command access checks to reduce cross-platform auth drift
  - supports explicit allow-list policy controls for sensitive command classes
  - adds operator-configurable command policy controls via `OPENCLAW_COMMAND_OVERRIDES` and `OPENCLAW_COMMAND_ALLOW_FROM_{PUBLIC|RUN|ADMIN}`
- Added registry anti-abuse controls for remote distribution paths:
  - bounded request-rate controls and deduplication windows reduce abuse and accidental hot loops
  - stale anti-abuse state pruning keeps long-running deployments stable
- Added registry preflight and trust-policy hardening:
  - static package safety checks are enforced before activation paths
  - policy-driven signature/trust posture supports audit and strict enforcement modes
  - registry trust mode is operator-controlled via `OPENCLAW_REGISTRY_POLICY` and preflight verification enforces fail-closed file-path requirements

</details>

<details>
<summary><strong>Sprint A: closes out with five concrete reliability and security improvements</strong></summary>

- Configuration save/apply now returns explicit apply metadata, so callers can see what was actually applied, what requires restart, and which effective provider/model is active.
- The Settings update flow adds defensive guards against stale or partial state, reducing accidental overwrites.
- Provider/model precedence is now deterministic across save, test, and chat paths, and prevents model contamination when switching providers.
- In localhost convenience mode (no admin token configured), chat requests enforce same-origin CSRF protection: same-origin requests are allowed, cross-origin requests are denied.
- Model-list fetching now uses a bounded in-memory cache keyed by provider and base URL, with a 5-minute TTL and LRU eviction cap to improve responsiveness and stability.

</details>

<details>
<summary><strong>Sprint B: ships security doctor diagnostics, registry quarantine gates, and constrained transforms defaults</strong></summary>

- Added the Security Doctor surface (`GET /openclaw/security/doctor`) for operator-focused security posture checks across endpoint exposure, token boundaries, SSRF posture, state-dir permissions, redaction drift, runtime mode, feature flags, and API key posture.
- Added optional remote pack registry quarantine controls with explicit lifecycle states, SHA256 integrity verification, bounded local persistence, and per-entry audit trail; this path remains disabled by default and fail-closed.
- Added optional constrained transform execution with trusted-directory + integrity pinning, timeout and output-size caps, and bounded chain execution semantics; transforms remain disabled by default and mapping-only behavior remains intact unless explicitly enabled.

</details>

<details>
<summary><strong>Settings contract, frontend graceful degradation, and provider drift governance</strong></summary>

- Enforced a strict settings write contract with schema-coerced values and explicit unknown-key rejection, reducing save/apply regressions across ComfyUI variants.
- Hardened frontend behavior to degrade safely when optional routes or runtime capabilities are unavailable, with clearer recovery hints instead of brittle failures.
- Added provider alias/deprecation governance and normalization coverage to reduce preset drift as upstream model IDs and endpoint shapes evolve.

</details>

<details>
<summary><strong>Mapping v1, job event stream, and operator doctor</strong></summary>

- Added webhook mapping engine v1 with declarative field mapping + type coercion, enabling external payload normalization without custom adapter code paths.
- Added real-time job event stream support via SSE (`/openclaw/events/stream`) with bounded buffering and polling fallback (`/openclaw/events`) for compatibility.
- Added Operator Doctor diagnostics tooling for runtime/deployment checks (Python/Node environment, state-dir posture, and contract readiness signals).

</details>

<details>
<summary><strong> Security doctor, registry quarantine, and constrained transforms</strong></summary>

- Added Security Doctor diagnostics surface (`GET /openclaw/security/doctor`) for operator-focused security posture checks and guarded remediation flow.
- Added optional remote registry quarantine lifecycle controls with integrity verification, bounded local persistence, and explicit trust/audit gates.
- Added optional constrained transform execution with integrity pinning, timeout/output caps, and bounded chain semantics; default posture remains disabled/fail-closed.

</details>

</details>

## Table of Contents

- [Installation](#installation)
- [Quick Start (Minimal)](#quick-start-minimal)
  - [Configure an LLM key](#1-configure-an-llm-key-for-plannerrefinervision-helpers)
  - [Configure webhook auth](#2-configure-webhook-auth-required-for-webhook)
  - [Set an Admin Token](#3-optional-recommended-set-an-admin-token)
- [Remote Admin Console (Mobile UI)](#remote-admin-console-mobile-ui)
  - [Environment variables for remote admin](#environment-variables-for-remote-admin)
  - [Connection from phone or other devices](#connection-from-phone-or-other-devices)
  - [Basic operations](#basic-operations)
  - [Reverse proxy and exposure notes](#reverse-proxy-and-exposure-notes)
- [Nodes](#nodes)
- [Extension UI](#extension-ui)
  - [Sidebar Modules](#sidebar-modules)
- [Operator UX Features](#operator-ux-features)
  - [Notification Center](#notification-center)
- [API Overview](#api-overview)
- [Templates](#templates)
- [Execution Budgets](#execution-budgets)
- [LLM Failover](#llm-failover)
- [Advanced Security and Runtime Setup](#advanced-security-and-runtime-setup)
- [State Directory & Logs](#state-directory--logs)
- [Troubleshooting](#troubleshooting)
- [Tests](#tests)
- [Updating](#updating)
- [Remote Control (Connector)](#remote-control-connector)
- [Security](#security)
  - [Security Deployment Guide](#security-deployment-guide)
  - [Deployment Self-check Command](#deployment-self-check-command)

---

## Installation

- ComfyUI-Manager: install as a custom node (recommended for most users), then restart ComfyUI.
- Git (manual):
  - `git clone <repo> ComfyUI/custom_nodes/comfyui-openclaw`

Alternative install options:

1. Copy/clone this repository into your ComfyUI `custom_nodes` folder
2. Restart ComfyUI.

If the UI loads but endpoints return 404, ComfyUI likely did not load the Python part of the pack (see Troubleshooting).

## Quick Start (Minimal)

### 1 Configure an LLM key (for Planner/Refiner/vision helpers)

Set at least one of:

- `OPENCLAW_LLM_API_KEY` (generic)
- Provider-specific keys from the provider catalog (preferred; see `services/providers/catalog.py`)

Provider/model configuration can be set via env or `/openclaw/config` (admin boundary; localhost-only convenience if no Admin Token configured).

Notes:

- Recommended: set API keys via environment variables.
- Optional: for single-user localhost setups, you can store a provider API key from the Settings tab (UI Key Store (Advanced)).
  - This writes to the server-side secret store (`{STATE_DIR}/secrets.json`).
  - Environment variables always take priority over stored keys.

### 2 Configure webhook auth (required for `/webhook*`)

Webhooks are **deny-by-default** unless auth is configured:

- `OPENCLAW_WEBHOOK_AUTH_MODE=bearer` and `OPENCLAW_WEBHOOK_BEARER_TOKEN=...`
- or `OPENCLAW_WEBHOOK_AUTH_MODE=hmac` and `OPENCLAW_WEBHOOK_HMAC_SECRET=...`
- or `OPENCLAW_WEBHOOK_AUTH_MODE=bearer_or_hmac` to accept either
- optional replay protection: `OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION=1`

### 3 Optional (recommended): set an Admin Token

Admin/write actions (save config, `/llm/test`, key store) are protected by the **Admin Token**:

- If `OPENCLAW_ADMIN_TOKEN` (or legacy `MOLTBOT_ADMIN_TOKEN`) is set, clients must send it via `X-OpenClaw-Admin-Token`.
- If no admin token is configured, admin actions are allowed on **localhost only** (convenience mode). Do not use this mode on shared/public deployments.

Remote admin actions are denied by default. If you understand the risk and need remote administration, opt in explicitly:

- `OPENCLAW_ALLOW_REMOTE_ADMIN=1`

Public profile boundary acknowledgement (required when `OPENCLAW_DEPLOYMENT_PROFILE=public`):

- `OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK=1`
  - set this only after your reverse proxy path allowlist + network ACL explicitly block ComfyUI-native high-risk routes (`/prompt`, `/history*`, `/view*`, `/upload*`, `/ws`, and `/api/*` equivalents)

### Windows env var tips (PowerShell / CMD / portable .bat / Desktop)

- PowerShell (current session only):
  - `$env:OPENCLAW_LLM_API_KEY="<YOUR_API_KEY>"`
  - `$env:OPENCLAW_ADMIN_TOKEN="<YOUR_ADMIN_TOKEN>"`
  - `$env:OPENCLAW_LOG_TRUNCATE_ON_START="1"` (optional: clear previous `openclaw.log` at startup)
- PowerShell (persistent; takes effect in new shells):
  - `setx OPENCLAW_LLM_API_KEY "<YOUR_API_KEY>"`
  - `setx OPENCLAW_ADMIN_TOKEN "<YOUR_ADMIN_TOKEN>"`
  - `setx OPENCLAW_LOG_TRUNCATE_ON_START "1"` (optional)
- CMD (current session only): `set OPENCLAW_LLM_API_KEY=<YOUR_API_KEY>`
- Portable `.bat` launchers: add `set OPENCLAW_LLM_API_KEY=...` / `set OPENCLAW_ADMIN_TOKEN=...` (optionally `set OPENCLAW_LOG_TRUNCATE_ON_START=1`) before launching ComfyUI.
- Windows note: changing env vars in System Properties or with `setx` does not update an already-running portable ComfyUI process; fully restart the launcher so `python_embeded\\python.exe` inherits the new values.
- ComfyUI Desktop: if env vars are not passed through reliably, prefer the Settings UI key store for localhost-only convenience, or set system-wide env vars.

## Remote Admin Console (Mobile UI)

The project now includes a standalone admin UI endpoint for mobile/remote operations:

- primary: `/openclaw/admin`
- legacy alias: `/moltbot/admin`

This page is independent from the embedded ComfyUI side panel and is intended for phone/desktop browsers.

Implementation shape:

- static shell: `web/admin_console.html`
- runtime app module: `web/admin_console_app.js`
- runtime API client module: `web/admin_console_api.js`

### Environment variables for remote admin

Recommended baseline before enabling remote administration:

- `OPENCLAW_ADMIN_TOKEN=<strong-secret>`
  - required for authenticated write/admin operations from remote devices
- `OPENCLAW_ALLOW_REMOTE_ADMIN=1`
  - explicit opt-in for remote admin write paths
- `OPENCLAW_OBSERVABILITY_TOKEN=<strong-secret>` (recommended)
  - tokenized read access for observability routes in non-localhost scenarios

Optional but commonly used with planner/refiner workflows:

- `OPENCLAW_LLM_API_KEY=<provider-key>` (or provider-specific key vars)

### Connection from phone or other devices

1. Start ComfyUI with external listen enabled (example):
   - `python main.py --listen 0.0.0.0 --port 8200`
2. Use your host LAN IP (for example `192.168.x.x`) and open:
   - `http://<HOST_LAN_IP>:<PORT>/openclaw/admin`
3. Enter the admin token in the page input and click `Save`.
4. Click `Refresh All` to verify health and API reachability.

Notes:

- On Windows, if a port fails with bind errors (for example WinError 10013), choose a different port outside excluded ranges.
- If write actions are denied remotely, verify both `OPENCLAW_ADMIN_TOKEN` and `OPENCLAW_ALLOW_REMOTE_ADMIN=1`.
- Remote Admin being reachable from LAN does not imply LAN-hosted custom LLM targets are allowed. SSRF rules for `base_url` remain separate and stricter.

### Basic operations

After token save, typical flow is:

- `Dashboard`: confirm provider/model/key status and recent errors
- `Jobs / Events`: refresh runs, connect SSE stream, verify event updates
- `Approvals`: approve/reject pending items
- `Schedules / Triggers`: toggle schedules, run now, or fire manual trigger
- `Config`: reload and safely update provider/model/base URL/retry/timeout
- `Doctor / Diagnostics`: inspect security doctor + preflight inventory output
- `Quick Actions`: retry failed schedule, refresh model list, or run drill (subject to existing policy/tool availability)

### Reverse proxy and exposure notes

Do **not** expose ComfyUI/OpenClaw admin endpoints directly to the public internet without a hardened edge.

Minimum recommendations:

- terminate TLS at reverse proxy (HTTPS only)
- add authentication at edge (in addition to OpenClaw admin token)
- restrict source IP ranges when possible
- apply request-rate limits and connection limits
- keep server and node package on current patched versions
- if running `OPENCLAW_DEPLOYMENT_PROFILE=public`, set `OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK=1` only after enforcing reverse-proxy path allowlist + network ACL boundary controls

For internet-facing deployment templates and hardening checklist, follow:

- `docs/security_deployment_guide.md`

## Nodes

Nodes are exported as `Moltbot*` class names for compatibility, but appear as `openclaw:*` display names in ComfyUI:

- `openclaw: Prompt Planner`
- `openclaw: Prompt Refiner`
- `openclaw: Image to Prompt`
- `openclaw: Batch Variants`

See `web/docs/` for node usage notes.

## Extension UI

![OpenClaw /sidebar ui example](assets/sidebar.png)

The frontend lives in `web/` and is served by ComfyUI as an extension panel. It uses the backend routes below (preferring `/api/openclaw/*`).

Current sidebar composition keeps `web/openclaw_ui.js` as the shell root and routes specialized browser logic through focused modules:

- actions and submit/cancel wiring: `web/openclaw_actions.js`
- queue polling and transient banners: `web/openclaw_queue_monitor.js` and `web/openclaw_banner_manager.js`
- persistent operator notifications: `web/openclaw_notification_center.js`
- tab registration/remount behavior: `web/openclaw_tabs.js`
- shared error + compatibility helpers: `web/openclaw_utils.js`

Canonical DOM/class ownership is now centered on `openclaw-*`; legacy `moltbot-*` class compatibility is still supported through shared runtime aliasing instead of duplicated markup in each tab template.

The sidebar now also resolves and stamps its active host surface (`standalone_frontend` vs desktop-embedded host) at mount time so frontend-host drift is explicit and testable instead of inferred from runtime accidents.

### Sidebar Modules

![OpenClaw /sidebar ui example](assets/sidebar_modules.png)

The OpenClaw sidebar includes these built-in tabs. Some tabs are capability-gated and may be hidden when the related backend feature is disabled.

| Tab | What it does | Related docs |
| --- | --- | --- |
| `Settings` | Health/config/log visibility, provider/model setup, model connectivity checks, and optional localhost key storage. | [Quick Start](#quick-start-minimal), [LLM config](#llm-config-non-secret), [Troubleshooting](#troubleshooting) |
| `Jobs` | Tracks prompt IDs, consumes deterministic event/task cursor metadata for polling, and shows output previews for recent jobs across classic history refs and asset-backed output refs through the same `/view` contract. | [Observability](#observability-read-only), [Remote Control (Connector)](#remote-control-connector) |
| `Planner` | Uses assist endpoint to generate structured prompt plans (positive/negative/params). | [Configure an LLM key](#1-configure-an-llm-key-for-plannerrefinervision-helpers), [Nodes](#nodes) |
| `Refiner` | Refines existing prompts with optional image context and issue/goal input. | [Configure an LLM key](#1-configure-an-llm-key-for-plannerrefinervision-helpers), [Nodes](#nodes) |
| `Variants` | Local helper for generating batch variant parameter JSON (seed/range-style sweeps). | [Nodes](#nodes), [Operator UX Features](#operator-ux-features) |
| `Library` | Manages reusable prompt/params presets and provides pack-oriented library operations in one place. | [Presets](#presets-admin), [Packs](#packs-admin) |
| `Approvals` | Lists approval gates and supports approve/reject operations, including the same approval objects now surfaced through Slack and Feishu interactive connector actions. | [Triggers + approvals](#triggers--approvals-admin), [Remote Control (Connector)](#remote-control-connector) |
| `Explorer` | Inventory/preflight diagnostics and snapshot/checkpoint troubleshooting workflows, including snapshot-first inventory refresh state (`snapshot_ts`, `scan_state`, `stale`, `last_error`). | [Operator UX Features](#operator-ux-features), [Troubleshooting](#troubleshooting) |
| `Packs` | Dedicated pack lifecycle tab for import/export/delete under admin boundary. | [Packs](#packs-admin) |
| `PNG Info` | Inspects saved generation images through drag-and-drop, file picker, or scoped paste, parses A1111 infotext plus ComfyUI `prompt` / `workflow` metadata, shows extracted prompt and generation fields when recoverable, and keeps raw metadata visible for operator inspection. | [API Overview](#api-overview), [Troubleshooting](#troubleshooting) |
| `Model Manager` | Searches model catalog/install records, queues managed downloads, monitors task lifecycle, and imports completed tasks into the managed install root with the same trusted download/import contract used by the backend model manager APIs. | [Model manager](#model-manager-admin-f54), [API Overview](#api-overview) |
| `Parameter Lab` | Runs bounded sweep/compare experiments, stores history, and replays parameters back into the graph. | [Operator UX Features](#operator-ux-features) |

## Operator UX Features

### Notification Center

The sidebar includes a persistent `Notification Center` for operator-facing alerts that should survive reloads:

- warning/error banners and selected durable toasts are mirrored into a local notification store
- entries are deduplicated by source-specific keys and keep an unread count
- `Acknowledge` clears unread state without hiding the item
- `Dismiss` removes the item from the active panel while preserving historical storage
- notification message/source fields are rendered as escaped text, not trusted as HTML, so operator-facing payloads cannot turn stored notification content into live markup
- action-enabled entries can deep-link back to the affected surface, such as `Model Manager` or `Jobs`

Current examples include queue-monitor incidents and managed-model failures that need operator follow-up.

### In-canvas context toolbox

Right-click a node and open the `OpenClaw` menu to access:

- `Inspect`: jump to the Explorer troubleshooting path.
- `Doctor`: run diagnostics and show readiness feedback.
- `Queue Status`: jump directly to queue/job monitoring.
- `Compare`: open Parameter Lab in compare setup mode for the selected node.
- `Settings`: jump to OpenClaw settings.

These actions are capability-aware and degrade to safe guidance when optional backend capabilities are unavailable.

### Parameter Lab history and replay

Parameter Lab now supports experiment history and run replay:

- `History` lists saved experiments from local state.
- `Load` opens stored experiment details and run statuses.
- `Replay` applies a selected run's parameter values back into the active workflow graph.

This makes iterative tuning and backtracking faster without manually retyping prior parameter sets.

### Compare workflow baseline

Parameter Lab includes a baseline compare flow for model/widget A/B style checks:

- Use `Compare` from the node context toolbox, or `Compare Models` inside Parameter Lab.
- The compare planner generates bounded runs from one selected comparison dimension.
- Backend compare submission is validated and admin-protected.
- Compare experiments are persisted and visible in history alongside sweep experiments.

Current scope is focused on bounded compare orchestration and replay-ready records; richer side-by-side evaluation and winner handoff are still being expanded.

### Operator guidance and quick recovery

Operator actions are wired for faster recovery loops:

- queue/status routing prefers the dedicated monitor view when available
- doctor checks surface immediate readiness feedback
- compare and history flows are connected so experiments can be reviewed and replayed quickly

## API Overview

This README now keeps only the high-level API map. Detailed route shapes, auth contracts, examples, and release-facing behavior live in `docs/`.

Base path notes:

- primary prefix: `/openclaw/*`
- legacy prefix: `/moltbot/*`
- browser/extension callers should prefer `/api/openclaw/*`
- standalone admin UI entry: `GET /openclaw/admin`

Main API families:

- Observability: health, capabilities, logs, traces, event feeds
- Admin diagnostics: preflight inventory snapshot/status, doctor-facing readiness views
- Config + LLM: effective config, provider tests, model lists, assist planner/refiner
- Connector installation diagnostics: installation state, resolution, callback/tenant binding evidence, audit views
- Webhooks + events: validate, submit, callback delivery, SSE/polling status
- Admin operations: approvals, schedules, presets, rewrite recipes
- Model Manager + Packs: search, download/import lifecycle, pack import/export
- Bridge / sidecar: worker poll/result/heartbeat and bridge health/submit routes

Primary references:

- [API contract](docs/release/api_contract.md)
- [Config and secrets contract](docs/release/config_secrets_contract.md)
- [Connector guide](docs/connector.md)
- [Sidecar guide](docs/sidecar.md)
- [OpenAPI spec](docs/openapi.yaml)

Operational notes:

- Observability remains token-gated for remote access and redacts provider reasoning-like content by default.
- Event and managed-download polling now expose deterministic cursor metadata so reconnect/backfill behavior can stay incremental instead of falling back to full-list refreshes on every poll.
- Preflight inventory is snapshot-first: clients should treat `snapshot_ts`, `scan_state`, `stale`, and `last_error` as part of the normal operator-diagnostics contract.
- Config/assist/model-management paths inherit the unified config precedence contract and SSRF-safe outbound policy.
- Connector installation diagnostics expose redacted token references only, never raw token material.
- Webhook and rate-limit error paths expose machine-readable diagnostics; client integrations should consume codes and structured fields instead of free-form text.

## Advanced Security and Runtime Setup

Use this section as a pointer map rather than a second full deployment manual.

Primary references:

- [Runtime hardening and startup](docs/runtime_hardening_and_startup.md)
- [Security deployment guide](docs/security_deployment_guide.md)
- [Security checklist](docs/security_checklist.md)
- [Config surface ADR](docs/adr/ADR-0001-config-surface-unification.md)
- [Config and secrets contract](docs/release/config_secrets_contract.md)
- [Advanced registry and transforms](docs/advanced_registry_and_transforms.md)
- [Connector guide](docs/connector.md)

The most important knobs in this area are:

- runtime posture: `OPENCLAW_RUNTIME_PROFILE`
- multi-tenant boundary: `OPENCLAW_MULTI_TENANT_ENABLED`, `OPENCLAW_TENANT_HEADER`
- local secret-manager path: `OPENCLAW_1PASSWORD_ENABLED`, `OPENCLAW_1PASSWORD_ALLOWED_COMMANDS`, `OPENCLAW_1PASSWORD_VAULT`
- registry / transform controls: `OPENCLAW_ENABLE_REGISTRY_SYNC`, `OPENCLAW_REGISTRY_POLICY`, `OPENCLAW_ENABLE_TRANSFORMS`
- connector authorization: `OPENCLAW_COMMAND_OVERRIDES`, `OPENCLAW_COMMAND_ALLOW_FROM_PUBLIC`, `OPENCLAW_COMMAND_ALLOW_FROM_RUN`, `OPENCLAW_COMMAND_ALLOW_FROM_ADMIN`

Config precedence remains:

- `env > runtime override > persisted config > default`

Legacy `MOLTBOT_*` aliases still exist for compatibility, but `OPENCLAW_*` is the supported canonical surface.

## Templates

Templates live in `data/templates/`.

- Any `data/templates/<template_id>.json` file is runnable (template ID = filename stem).
- `data/templates/manifest.json` is optional metadata (e.g. defaults).
- Rendering performs **strict placeholder substitution**:
  - Only exact string values matching `{{key}}` are replaced
  - Partial substitutions (e.g. `"foo {{bar}}"`) are intentionally not supported

For the full step-by-step guide (where to put exported workflow JSON, how to author `manifest.json`, how to verify `/openclaw/templates`, and how to use `/run`), see `tests/TEST_SOP.md`.

### Basic `/run` usage (chat)

**Free-text prompt mode (no `key=value` needed):**

```
/run z "a cinematic portrait" seed=-1
```

The connector will map the free text into a prompt field using:

- `allowed_inputs` if a single key is declared in `manifest.json`, or
- fallback order: `positive_prompt` -> `prompt` -> `text` -> `positive` -> `caption`.

**Key=value mode (explicit mapping):**

```
/run z positive_prompt="a cat" seed=-1
```

Important:

- Ensure your workflow uses the same placeholder (e.g., `"text": "{{positive_prompt}}"`).
- `seed=-1` gives random seeds; a fixed seed reproduces outputs.

## Execution Budgets

Queue submissions are protected by concurrency caps and render size budgets (`services/execution_budgets.py`).

Environment variables:

- `OPENCLAW_MAX_INFLIGHT_SUBMITS_TOTAL` (default: 2)
- `OPENCLAW_MAX_INFLIGHT_SUBMITS_WEBHOOK` (default: 1)
- `OPENCLAW_MAX_INFLIGHT_SUBMITS_TRIGGER` (default: 1)
- `OPENCLAW_MAX_INFLIGHT_SUBMITS_SCHEDULER` (default: 1)
- `OPENCLAW_MAX_INFLIGHT_SUBMITS_BRIDGE` (default: 1)
- `OPENCLAW_MAX_INFLIGHT_SUBMITS_PER_TENANT` (default: 1, only when multi-tenant mode is enabled)
- `OPENCLAW_MAX_RENDERED_WORKFLOW_BYTES` (default: 524288)

If budgets are exceeded, callers should expect `429` (concurrency) or `413` (oversized render).

## LLM Failover

Failover is integrated into `services/llm_client.py` and controlled via runtime config:

- `OPENCLAW_FALLBACK_MODELS` (CSV)
- `OPENCLAW_FALLBACK_PROVIDERS` (CSV)
- `OPENCLAW_MAX_FAILOVER_CANDIDATES` (int, 1-)

## State Directory & Logs

By default, state is stored in a platform user-data directory:

- Windows: `%LOCALAPPDATA%\\comfyui-openclaw\\`
- macOS: `~/Library/Application Support/comfyui-openclaw/`
- Linux: `~/.local/share/comfyui-openclaw/`

Override:

- `OPENCLAW_STATE_DIR=/path/to/state`

Logs:

- `openclaw.log` (legacy `moltbot.log` is still supported)
- Optional startup truncation: set `OPENCLAW_LOG_TRUNCATE_ON_START=1` to clear the active log file once at process startup (useful to avoid stale-history noise in UI log views).
- Optional structured JSON logs for selected core paths:
  - set `OPENCLAW_LOG_FORMAT=json` (or `OPENCLAW_STRUCTURED_LOGS=1`) before startup
  - default behavior remains plain text logs (no structured log emission unless opt-in)

## Troubleshooting

Common operator issues now live in a dedicated troubleshooting guide:

- [Troubleshooting guide](docs/troubleshooting.md)

Quick jumps:

- backend not loaded / route 404 startup failures
- Operator Doctor usage
- webhook auth not configured
- loopback LLM SSRF validation errors
- Remote Admin vs private-LAN LLM target behavior
- server-side Admin Token vs UI token usage

## Tests

For the authoritative validation workflow, follow `tests/TEST_SOP.md`.

Fast backend-only check from the repo root:

```bash
python3 -m unittest discover -s tests -p "test_*.py"
```

Full local acceptance gate (recommended before push):

```bash
bash scripts/run_full_tests_linux.sh
```

This full gate includes detect-secrets, pre-commit, coverage governance verification, backend suites, adaptive adversarial verification, Playwright E2E, and CI-parity dependency audit expectations scoped to declared project requirements.
It also includes backend regressions that pin snapshot-first diagnostics, delta cursor semantics, schema/OpenAPI drift checks, and minimal-environment optional-dependency import behavior.

## Updating

- Git install: `git pull` inside `custom_nodes/comfyui-openclaw/`, then restart ComfyUI.
- ComfyUI-Manager install: update from Manager UI, then restart ComfyUI.

## Remote Control (Connector)

OpenClaw includes a standalone **Connector** process that allows you to control your local instance securely via **Telegram**, **Discord**, **LINE**, **WhatsApp**, **WeChat**, **KakaoTalk**, **Slack**, and **Feishu/Lark**.

- **Status & Queue**: Check job progress remotely.
- **Run Jobs**: Submit templates via chat commands.
- **Approvals**: Approve/Reject paused workflows from your phone.
- **Secure**: Outbound-only for Telegram/Discord. LINE/WhatsApp/WeChat/KakaoTalk/Slack require inbound HTTPS (webhook), while Slack can also use Socket Mode and Feishu can run in either webhook or long-connection mode with a dedicated callback ingress path.
- **WeChat encrypted mode**: Official Account encrypted webhook mode is supported when AES settings are configured.
- **KakaoTalk response safety**: QuickReply limits and safe fallback handling are enforced for reliable payload behavior.
- **Slack multi-workspace mode**: Workspace installs can be handled through connector-managed OAuth install/callback routes with per-workspace token binding and fail-closed health diagnostics.
- **Feishu/Lark multi-account mode**: Connector-managed account/workspace bindings support tenant-aware installation resolution, interactive approval cards, and signed callback handling without exposing raw app secrets or widening command trust implicitly.

- [See Setup Guide (`docs/connector.md`)](docs/connector.md)

## Security

Read [SECURITY.md](docs/SECURITY.md) before exposing any endpoint beyond localhost. The project is designed to be secure-by-default (deny-by-default auth, SSRF protections, redaction, bounded outputs), but unsafe deployment can still create risk.

### Security Deployment Guide

- [Security Deployment Guide](docs/security_deployment_guide.md)
- Includes three copy-paste deployment profiles (`local`, `lan`, `public`) and step-by-step checklists.

### Deployment Self-check Command

Validate current env against deployment profile:

```bash
python scripts/check_deployment_profile.py --profile local
python scripts/check_deployment_profile.py --profile lan
python scripts/check_deployment_profile.py --profile public
```

Fail on warnings too (recommended for hardened/public pipelines):

```bash
python scripts/check_deployment_profile.py --profile public --strict-warnings
```

---

## Disclaimer (Security & Liability)

This project is provided **as-is** without warranty of any kind. You are solely responsible for:

- **API keys / Admin tokens**: creation, storage, rotation, and revocation
- **Runtime configuration**: environment variables, config files, UI settings
- **Network exposure**: tunnels, reverse proxies, public endpoints
- **Data handling**: logs, prompts, outputs, and any content generated or transmitted

### Key Handling Guidance (all environments)

- **Prefer environment variables** for API keys and admin tokens.
- **UI key storage (if enabled)** is for local, single-user setups only.
- **Never commit secrets** or embed them in versioned files.
- **Rotate tokens** regularly and after any suspected exposure.

### Common Deployment Contexts (you must secure each)

- **Local / single-user**: treat keys as secrets; avoid long-term browser storage.
- **LAN / shared machines**: require admin tokens, restrict IPs, disable unsafe endpoints.
- **Public / tunneled / reverse-proxy**: enforce strict allowlists, HTTPS, least-privilege access.
- **Desktop / portable / scripts**: ensure secrets are not logged or persisted by launchers.

### No Liability

The maintainers and contributors **accept no responsibility** for:

- Unauthorized access or misuse of your instance
- Loss of data, keys, or generated content
- Any direct or indirect damages resulting from use of this software

By using this project, you acknowledge and accept these terms.
