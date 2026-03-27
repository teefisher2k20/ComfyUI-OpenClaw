# OpenClaw API Contract (v1)

> **Status**: normative
> **Version**: 1.0.5
> **Date**: 2026-03-12

This document defines the public API contract for OpenClaw. It serves as the authoritative baseline for client compatibility and breaking change policies.

## 0. Tenant Boundary Context (S49)

Default behavior remains single-tenant compatible (`tenant_id=default`).

When `OPENCLAW_MULTI_TENANT_ENABLED=1`:

- tenant context may be supplied by token context and/or request header (`X-OpenClaw-Tenant-Id`, configurable)
- token/header mismatch is rejected with `403` (`tenant_mismatch`)
- connector installation resolution is tenant-scoped and rejects cross-tenant matches fail-closed (diagnostic conflict path)
- current admin/API handlers keep compatibility behavior by defaulting missing tenant context to `default` tenant

## 1. Route Inventory

All new integrations should use the `/openclaw/` prefix. Use of `/moltbot/` is deprecated.

### 1.0 UI Entry Points

**Base Path**: `/openclaw/`

| Method | Path | Legacy Path | Auth | Description |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/admin` | `/moltbot/admin` | None* | Standalone remote admin console HTML shell (mobile-friendly). |

`*` The page shell can be loaded directly, but all backend write operations from the console still enforce Admin token and remote-admin policy.

### 1.1 Core Observability & System

**Base Path**: `/openclaw/`

| Method | Path | Legacy Path | Auth | Description |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/health` | `/moltbot/health` | None | System status, uptime, and dependencies. |
| `GET` | `/capabilities` | `/moltbot/capabilities` | None | Feature flags and supported extensions (includes optional UX/runtime features such as assist streaming support). |
| `GET` | `/logs/tail` | `/moltbot/logs/tail` | Observability | Tail recent log lines (rate-limited). |
| `GET` | `/trace/{prompt_id}` | `/moltbot/trace/{id}` | Observability | Get execution trace by prompt ID. |
| `GET` | `/events` | `/moltbot/events` | Observability | List recent job lifecycle events (JSON polling fallback; includes pagination/scan diagnostics). |
| `GET` | `/events/stream` | `/moltbot/events/stream` | Observability | SSE stream of job lifecycle events with resume support. |
| `GET` | `/config` | `/moltbot/config` | Observability | Read-only view of sanitized provider config. |
| `PUT` | `/config` | `/moltbot/config` | Admin | Update system configuration. |
| `GET` | `/jobs` | `/moltbot/jobs` | Observability | List recent jobs (Stub/Not Implemented). |

Reasoning-content redaction contract:

- operator-visible trace and events payloads strip provider reasoning / thinking-like fields by default
- privileged reveal is opt-in only and requires:
  - request header `X-OpenClaw-Debug-Reveal-Reasoning: 1` or query `debug_reasoning=1`
  - server-side enablement via `OPENCLAW_DEBUG_REASONING_REVEAL=1`
  - admin authorization
  - loopback source
  - non-hardened runtime profile
  - deployment profile `local` or `lan`
- clients MUST treat reveal behavior as debug-only and MUST NOT depend on reasoning payload presence in normal operation

### 1.2 Webhooks & Triggers

**Auth**: Requires configured webhook secret or Admin Token.

| Method | Path | Legacy Path | Auth | Description |
| :--- | :--- | :--- | :--- | :--- |
| `POST` | `/webhook` | `/moltbot/webhook` | Webhook Secret | Receive external alerts (schema validation only). |
| `POST` | `/webhook/submit` | `/moltbot/webhook/submit` | Webhook Secret | Validate and submit job from webhook payload. |
| `POST` | `/webhook/validate` | `/moltbot/webhook/validate` | Webhook Secret | Dry-run validation of webhook payload. |
| `POST` | `/triggers/fire` | `/moltbot/triggers/fire` | Admin | Fire an ad-hoc workflow trigger from external system. |

### 1.3 Assist, LLM & Chat

**Assist Base Path**: `/openclaw/assist/`

| Method | Path | Legacy Path | Auth | Description |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/assist/planner/profiles` | `/moltbot/assist/planner/profiles` | Admin/Local | List active planner profiles from registry for UI/node alignment. |
| `POST` | `/assist/planner` | `/moltbot/assist/planner` | Admin/Local | Planner structured prompt generation. |
| `POST` | `/assist/refiner` | `/moltbot/assist/refiner` | Admin/Local | Prompt refinement with optional image context. |
| `POST` | `/assist/planner/stream` | `/moltbot/assist/planner/stream` | Admin/Local | Optional SSE-style planner streaming response (`text/event-stream`) with staged progress + final payload. |
| `POST` | `/assist/refiner/stream` | `/moltbot/assist/refiner/stream` | Admin/Local | Optional SSE-style refiner streaming response (`text/event-stream`) with staged progress + final payload. |

Assist payload redaction contract:

- structured assist responses preserve final operator-visible answer fields but strip provider reasoning / chain-of-thought style fields by default
- when the privileged reveal gate is allowed, debug reasoning is exposed only in a separate debug payload and not merged back into the normal structured answer fields

### 1.3B Connector Installation Diagnostics

**Base Path**: `/openclaw/connector/`
**Auth**: Admin Token Required

| Method | Path | Legacy Path | Auth | Description |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/connector/installations` | `/moltbot/connector/installations` | Admin | List redacted connector installations with lifecycle diagnostics. |
| `GET` | `/connector/installations/{installation_id}` | `/moltbot/connector/installations/{installation_id}` | Admin | Get one redacted connector installation record. |
| `GET` | `/connector/installations/resolve` | `/moltbot/connector/installations/resolve` | Admin | Run fail-closed workspace resolution diagnostics (`platform`, `workspace_id`). |
| `GET` | `/connector/installations/audit` | `/moltbot/connector/installations/audit` | Admin | List installation lifecycle audit evidence (redacted). |

Connector diagnostics contract notes:
- installation records may expose operator-safe health metadata under `installation.metadata.health` (for example `ok`, `invalid_token`, `revoked`, `degraded`) without exposing token material
- `/connector/installations` diagnostics may include aggregate `health_counts` in addition to lifecycle `status_counts`
- `/connector/installations/resolve` may expose a stable `health_code` alongside the legacy `reject_reason` so clients can distinguish `workspace_unbound` vs token-health failures without parsing status text

### 1.3C Model Management & Installations

**Base Path**: `/openclaw/`
**Auth**: Admin Token Required

| Method | Path | Legacy Path | Auth | Description |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/models/search` | `/moltbot/models/search` | Admin | Search normalized model entries across managed installs and catalog sources. |
| `POST` | `/models/downloads` | `/moltbot/models/downloads` | Admin | Create a managed model download task with progress/cancel lifecycle. |
| `GET` | `/models/downloads` | `/moltbot/models/downloads` | Admin | List model download tasks with snapshot or delta cursor semantics (`since_seq`). |
| `GET` | `/models/downloads/{task_id}` | `/moltbot/models/downloads/{task_id}` | Admin | Get one model download task by id. |
| `POST` | `/models/downloads/{task_id}/cancel` | `/moltbot/models/downloads/{task_id}/cancel` | Admin | Cancel a queued or running model download task. |
| `POST` | `/models/import` | `/moltbot/models/import` | Admin | Import a completed managed download into the bounded install root after provenance and hash verification. |
| `GET` | `/models/installations` | `/moltbot/models/installations` | Admin | List managed model installations. |

Model-manager contract notes:
- `/models/downloads` supports `since_seq` cursor polling and may return deterministic delta metadata (`requested_since_seq`, `effective_since_seq`, `next_since_seq`, truncation/reset hints) alongside the task list
- download creation requires structured provenance metadata (`publisher`, `license`, `source_url`) and a 64-char `expected_sha256`
- import keeps fail-closed destination/filename validation and re-checks the staged file hash before activation

### 1.3D LLM Management & Chat

**LLM Base Path**: `/openclaw/llm/`

| Method | Path | Legacy Path | Auth | Description |
| :--- | :--- | :--- | :--- | :--- |
| `POST` | `/chat` | `/moltbot/llm/chat` | Admin/Local | Unified chat interface for assistant interactions. |
| `POST` | `/test` | `/moltbot/llm/test` | Admin | Test LLM connectivity and configuration. |
| `GET` | `/models` | `/moltbot/llm/models` | Admin | List available models from configured provider. Request-time fetch uses the same SSRF contract as saved `base_url` validation, including the explicit insecure override for private-IP/HTTP targets. |

### 1.4 Templates & Assets

**Base Path**: `/openclaw/`

| Method | Path | Legacy Path | Auth | Description |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/templates` | `/moltbot/templates` | Observability | List discovered template IDs and metadata. |
| `GET` | `/presets` | `/moltbot/presets` | Public/Admin* | List local presets (*depends on `OPENCLAW_PRESETS_PUBLIC_READ`). |
| `POST` | `/presets` | `/moltbot/presets` | Admin | Create a new preset. |
| `PUT` | `/presets/{id}` | `/moltbot/presets/{id}` | Admin | Update an existing preset. |
| `DELETE` | `/presets/{id}` | `/moltbot/presets/{id}` | Admin | Delete a preset. |
| `GET` | `/checkpoints` | `/moltbot/checkpoints` | Observability | List model checkpoints. |
| `POST` | `/checkpoints` | `/moltbot/checkpoints` | Admin | Create/copy a checkpoint. |
| `GET` | `/packs` | `/moltbot/packs` | Admin | List installed asset packs. |
| `POST` | `/packs/import` | `/moltbot/packs/import` | Admin | Import an asset pack (.zip). |
| `GET` | `/packs/export/...` | `/moltbot/packs/export...` | Admin | Download an asset pack. |

### 1.5 Schedules & Approvals

**Base Path**: `/openclaw/`
**Auth**: Admin Token Required

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/schedules` | List all schedules. |
| `POST` | `/schedules` | Create a new schedule. |
| `GET` | `/schedules/{id}` | Get schedule details. |
| `PUT` | `/schedules/{id}` | Update a schedule. |
| `DELETE` | `/schedules/{id}` | Delete a schedule. |
| `POST` | `/schedules/{id}/run` | Manually trigger a schedule. |
| `GET` | `/schedules/{id}/runs` | Get run history for a schedule. |
| `GET` | `/approvals` | List pending approvals (includes pagination/scan diagnostics; bounded serialization scan on malformed records). |
| `POST` | `/approvals/{id}/approve` | Approve a pending request. |
| `POST` | `/approvals/{id}/reject` | Reject a pending request. |

### 1.6 Bridge (Sidecar)

**Base Path**: `/bridge/`
**Auth**: Bridge Auth (Device Check)

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Bridge status and connectivity check. |
| `POST` | `/submit` | Submit job from sidecar to core. |
| `POST` | `/deliver` | Outbound delivery from core to sidecar (via callback). |

---

## 2. Status & Error Semantics

API responses MUST adhere to the following status codes and envelope format.

### 2.1 Standard Envelope

All JSON responses (success or error) share a common structure:

```json
{
  "ok": boolean,
  "error": "string (optional)",
  "detail": "string (optional)",
  "trace_id": "string (optional)",
  "data": { ... } // Success payload
}
```

### 2.2 Status Codes

| Code | Meaning | Usage |
| :--- | :--- | :--- |
| `200` | OK | Successful synchronous request. |
| `201` | Created | Resource created (schedules, presets). |
| `202` | Accepted | Async job submitted (pending execution or approval). |
| `400` | Bad Request | Schema validation failure, missing required fields. |
| `401` | Unauthorized | Missing or invalid authentication token. |
| `403` | Forbidden | Authenticated but permission denied (e.g., admin-only). |
| `404` | Not Found | Resource or route does not exist. |
| `409` | Conflict | Idempotency collision or state conflict. |
| `413` | Payload Too Large | Input size exceeds `OPENCLAW_MAX_RENDERED_WORKFLOW_BYTES` or similar limits. |
| `429` | Too Many Requests | Rate limit or Execution Budget exceeded. |
| `500` | Internal Error | Unhandled server exception. |
| `503` | Unavailable | Feature disabled or service not wired. |

Tenant-boundary error notes:
- tenant boundary violations use `403` with explicit codes such as `tenant_mismatch` and `tenant_invalid`.

### 2.3 SSE Endpoint Notes (Contractual Behavior)

- SSE endpoints return `Content-Type: text/event-stream`.
- Current SSE surfaces include:
  - `/openclaw/events/stream` (job lifecycle events)
  - optional `/openclaw/assist/planner/stream` and `/openclaw/assist/refiner/stream` (assist incremental preview path)
- Assist streaming emits event types from the following set:
  - `ready`
  - `stage`
  - `delta`
  - `final`
  - `error`
  - `keepalive`
- Clients MUST treat `final` as the source of truth for structured assist results. `delta` preview text is best-effort and may be truncated or differ from the final parsed payload.
- Event-stream and polling payloads redact provider reasoning / thinking traces by default; reveal is debug-only and gated by the same privileged local-debug contract used by trace/assist surfaces.
- Clients SHOULD gracefully fall back to non-streaming assist endpoints when streaming capability is absent or streaming transport fails.

### 2.4 Pagination & Scan Diagnostics (Management Query Contract)

- `GET /openclaw/events` and `GET /openclaw/approvals` include deterministic pagination normalization.
- Responses may include `pagination` and `scan` diagnostic objects so clients/operators can detect:
  - normalized limit/offset/cursor values
  - stale/future cursor resets
  - bounded scan truncation or malformed-record skips
- Backend/runtime errors outside pagination normalization are still surfaced explicitly (not silently swallowed).

---

## 3. Limits & Budgets

These limits are contractual and strictly enforced. Clients MUST handle `413` and `429` responses.

| Limit | Metric | Default | Configuration Key |
| :--- | :--- | :--- | :--- |
| **Concurrency (Global)** | In-flight jobs | 2 | `OPENCLAW_MAX_INFLIGHT_SUBMITS_TOTAL` |
| **Concurrency (Webhook)** | In-flight jobs | 1 | `OPENCLAW_MAX_INFLIGHT_SUBMITS_WEBHOOK` |
| **Concurrency (Bridge)** | In-flight jobs | 1 | `OPENCLAW_MAX_INFLIGHT_SUBMITS_BRIDGE` |
| **Concurrency (Per-Tenant)** | In-flight jobs per tenant | 1 | `OPENCLAW_MAX_INFLIGHT_SUBMITS_PER_TENANT` |
| **Payload Size** | Rendered workflow | 512KB | `OPENCLAW_MAX_RENDERED_WORKFLOW_BYTES` |
| **Webhook Body** | Raw JSON body | 10MB | `MAX_BODY_SIZE` (internal constant) |
| **Trigger Inputs** | Input variables | 32KB | Hardcoded in `api/triggers.py` |
| **Log Tail** | Max lines | 500 | Hardcoded in `api/routes.py` |

---

## 4. Deprecation Policy

### 4.1 Legacy Routes (`/moltbot/`)

- **Status**: Deprecated.
- **Policy**: Maintained for backward compatibility in v1.x.
- **Removal**: Scheduled for removal in v2.0.
- **Action**: Clients should migrate to `/openclaw/` prefixes immediately.

### 4.2 Legacy Config Keys

- **Status**: Deprecated.
- **Policy**: Read-only fallback. `OPENCLAW_*` keys take precedence.
- **Action**: Operators should rename `MOLTBOT_*` keys to `OPENCLAW_*`.
