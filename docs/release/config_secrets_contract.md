# OpenClaw Config & Secrets Contract (v1)

> **Status**: normative
> **Version**: 1.0.5
> **Date**: 2026-03-07

This document defines the authoritative configuration contract for OpenClaw. It enumerates all supported environment variables, their precedence rules, and security classifications.

---

## 1. Configuration Principles

1. **Environment First**: Environment variables (`OPENCLAW_*`) always take precedence over runtime/file-based config layers.
2. **Secure by Default**: Missing optional secrets result in disabled features (fail-closed), not insecure open access.
3. **No Plaintext Storage**: Secrets MUST NOT be stored in plaintext config files committed to version control. They should be injected via environment variables or a secure secrets manager.
4. **Legacy Compatibility**: `MOLTBOT_*` keys are supported for backward compatibility but are deprecated. `OPENCLAW_*` keys are preferred.

---

## 2. Key Catalog

### 2.1 Backend LLM & AI Service

Controls the core LLM client used by nodes (Planner, Refiner, etc.).

| Variable | Required | Default | Description |
| :--- | :--- | :--- | :--- |
| `OPENCLAW_LLM_PROVIDER` | No | `openai` | Logic provider ID (e.g., `openai`, `anthropic`, `ollama`). |
| `OPENCLAW_LLM_MODEL` | No | Provider default | Specific model ID (e.g., `gpt-4o`, `claude-3-5-sonnet`). |
| `OPENCLAW_LLM_API_KEY` | **Yes*** | - | API Key for the configured provider. <br>*(Required unless using local provider or provider-specific key)* |
| `OPENCLAW_LLM_BASE_URL` | No | Provider default | Override base URL (crucial for local/compatible providers). |
| `OPENCLAW_LLM_TIMEOUT`| No | `120` | Request timeout in seconds. |

Optional local secret-manager path (S11, disabled by default):

| Variable | Required | Default | Description |
| :--- | :--- | :--- | :--- |
| `OPENCLAW_1PASSWORD_ENABLED` | No | `0` | Enables optional 1Password CLI provider for API key resolution. |
| `OPENCLAW_1PASSWORD_ALLOWED_COMMANDS` | **Yes (when enabled)** | - | Comma-separated executable allowlist. Empty allowlist with enabled provider is fail-closed. |
| `OPENCLAW_1PASSWORD_CMD` | No | `op` | 1Password CLI executable name/path. Must match allowlist entry. |
| `OPENCLAW_1PASSWORD_VAULT` | **Yes (when enabled)** | - | Vault name used to resolve secret references. |
| `OPENCLAW_1PASSWORD_ITEM_TEMPLATE` | No | `openclaw/{provider}` | Item path template. In multi-tenant mode it must include both `{tenant}` and `{provider}` (default becomes `openclaw/{tenant}/{provider}`). |
| `OPENCLAW_1PASSWORD_FIELD` | No | `api_key` | Item field name containing the key value. |
| `OPENCLAW_1PASSWORD_TIMEOUT_SEC` | No | `5` | CLI lookup timeout (bounded, fail-closed on timeout). |

Lookup precedence for provider keys:
1. Provider-specific env key (`OPENCLAW_*`, legacy `MOLTBOT_*`)
2. Generic env key (`OPENCLAW_LLM_API_KEY`, legacy aliases)
3. Optional 1Password provider (if enabled and allowlist-valid)
4. Encrypted server-side secret store (`secrets.enc.json`)

Multi-tenant note:
- when `OPENCLAW_MULTI_TENANT_ENABLED=1`, 1Password lookup references become tenant-scoped and the item template must include `{tenant}` or lookup fails closed.

**SSRF Protection:**

| Variable | Default | Description |
| :--- | :--- | :--- |
| `OPENCLAW_LLM_ALLOWED_HOSTS` | - | Comma-separated list of additional exact public hosts for custom base URLs. |
| `OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST` | `0` | Set `1` to bypass host allowlist and allow any public IP. |
| `OPENCLAW_ALLOW_INSECURE_BASE_URL` | `0` | Set `1` to allow HTTP or private IP targets (Dangerous!). |

Notes:
- Local providers (`ollama`, `lmstudio`) are loopback-only by design and should use `localhost` / `127.0.0.1` / `::1`.
- Local loopback provider targets do not require enabling insecure SSRF flags.
- `OPENCLAW_LLM_ALLOWED_HOSTS` does not allow private/reserved IPs; those still require `OPENCLAW_ALLOW_INSECURE_BASE_URL=1`.
- The same insecure override applies to config-save validation, `/openclaw/llm/models`, and outbound provider requests.
- Wildcard entries such as `*` are not supported in `OPENCLAW_LLM_ALLOWED_HOSTS`.

### 2.2 Security & Authentication

Controls access to APIs and administrative features.

| Variable | Sensitivity | Description |
| :--- | :--- | :--- |
| `OPENCLAW_ADMIN_TOKEN` | **Critical** | Bearer token for Admin Write actions (Config, Presets, Schedules). <br>*If unset, admin writes are loopback-only with strict checks.* |
| `OPENCLAW_OBSERVABILITY_TOKEN` | **High** | Token for Read-Only observability (Logs, Traces, Health). <br>*If unset, Remote observability is denied.* |
| `OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK` | **High** | Public-profile explicit ack that reverse-proxy path allowlist + network ACL controls are in place for shared ComfyUI/OpenClaw surface. Required by deployment-profile gate in `public` mode. |
| `OPENCLAW_WEBHOOK_AUTH_MODE` | **High** | Webhook auth mode (`bearer`, `hmac`, `bearer_or_hmac`). |
| `OPENCLAW_WEBHOOK_BEARER_TOKEN` | **High** | Bearer secret for inbound webhook auth when bearer mode is enabled. |
| `OPENCLAW_WEBHOOK_HMAC_SECRET` | **High** | HMAC secret for inbound webhook auth when hmac mode is enabled. |
| `OPENCLAW_WEBHOOK_REQUIRE_REPLAY_PROTECTION` | **High** | Set `1` to enforce replay protection for webhook requests. |
| `OPENCLAW_REQUIRE_APPROVAL_FOR_TRIGGERS` | Low | Set `1` to require admin approval for all external triggers (default: `0`). |
| `OPENCLAW_PRESETS_PUBLIC_READ` | Low | Set `0` to require Admin Token for listing presets (default: `1`). |
| `OPENCLAW_STRICT_LOCALHOST_AUTH` | Low | Legacy compatibility toggle used by preset read paths; prefer explicit `OPENCLAW_PRESETS_PUBLIC_READ` + `OPENCLAW_ADMIN_TOKEN`. |

### 2.3 Multi-tenant Boundary Contract

Controls tenant-boundary behavior for multi-tenant deployments.

| Variable | Default | Description |
| :--- | :--- | :--- |
| `OPENCLAW_MULTI_TENANT_ENABLED` | `0` | Enables fail-closed multi-tenant mode across config/secrets/connector resolution/API contexts. |
| `OPENCLAW_TENANT_HEADER` | `X-OpenClaw-Tenant-Id` | Request header used for tenant context extraction (legacy header alias still accepted). |
| `OPENCLAW_MULTI_TENANT_ALLOW_DEFAULT_FALLBACK` | `0` | If `1`, allows default tenant fallback when tenant context is missing (reduces strictness). |
| `OPENCLAW_MULTI_TENANT_ALLOW_CONFIG_FALLBACK` | `0` | If `1`, tenant config may fall back to global `llm` branch when `tenants.<id>.llm` is absent. |
| `OPENCLAW_MULTI_TENANT_ALLOW_LEGACY_SECRET_FALLBACK` | `0` | If `1`, tenant secret lookup may fall back to legacy unscoped secret keys. |

Boundary behavior:
- tenant mismatch between token context and tenant header is rejected (`tenant_mismatch`).
- connector installation resolution rejects cross-tenant matches fail-closed (`tenant_mismatch` diagnostics path).
- current admin/API handlers preserve compatibility by defaulting missing tenant context to `default`; stricter caller paths can enforce explicit-tenant requirements.

### 2.4 Connector & Delivery (Chat Apps)

Controls the `connector` sidecar process and outbound delivery.

| Variable | Platform | Description |
| :--- | :--- | :--- |
| `OPENCLAW_CONNECTOR_URL` | Core | URL of the OpenClaw backend (default: `http://127.0.0.1:8188`). |
| `OPENCLAW_CONNECTOR_ADMIN_TOKEN` | Core | Token to authenticate Connector calls to Backend. |
| `OPENCLAW_CONNECTOR_TELEGRAM_TOKEN` | Telegram | Bot API Token. |
| `OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_USERS` | Telegram | Comma-separated trusted user IDs. |
| `OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_CHATS`| Telegram | Comma-separated allowlist of logic IDs (User IDs or Chat IDs). |
| `OPENCLAW_CONNECTOR_DISCORD_TOKEN` | Discord | Bot User Token. |
| `OPENCLAW_CONNECTOR_DISCORD_ALLOWED_USERS` | Discord | Comma-separated trusted user IDs. |
| `OPENCLAW_CONNECTOR_DISCORD_ALLOWED_CHANNELS`| Discord | Comma-separated list of Channel IDs. |
| `OPENCLAW_CONNECTOR_LINE_CHANNEL_SECRET` | LINE | Channel Secret. |
| `OPENCLAW_CONNECTOR_LINE_CHANNEL_ACCESS_TOKEN`| LINE | Channel Access Token. |
| `OPENCLAW_CONNECTOR_LINE_ALLOWED_USERS` | LINE | Comma-separated trusted user IDs. |
| `OPENCLAW_CONNECTOR_LINE_ALLOWED_GROUPS` | LINE | Comma-separated trusted group IDs. |
| `OPENCLAW_CONNECTOR_WHATSAPP_ACCESS_TOKEN` | WhatsApp | Cloud API access token. |
| `OPENCLAW_CONNECTOR_WHATSAPP_APP_SECRET` | WhatsApp | App secret for webhook signature validation. |
| `OPENCLAW_CONNECTOR_WHATSAPP_ALLOWED_USERS` | WhatsApp | Comma-separated trusted `wa_id` values. |
| `OPENCLAW_CONNECTOR_WECHAT_TOKEN` | WeChat | Webhook verification token. |
| `OPENCLAW_CONNECTOR_WECHAT_APP_ID` | WeChat | Official Account AppID. |
| `OPENCLAW_CONNECTOR_WECHAT_APP_SECRET` | WeChat | Official Account AppSecret. |
| `OPENCLAW_CONNECTOR_WECHAT_ALLOWED_USERS` | WeChat | Comma-separated trusted OpenID values. |
| `OPENCLAW_CONNECTOR_KAKAO_ENABLED` | KakaoTalk | Enables Kakao adapter when truthy. |
| `OPENCLAW_CONNECTOR_KAKAO_ALLOWED_USERS` | KakaoTalk | Comma-separated trusted user IDs. |
| `OPENCLAW_CONNECTOR_SLACK_BOT_TOKEN` | Slack | Bot OAuth token (`xoxb-*`). |
| `OPENCLAW_CONNECTOR_SLACK_SIGNING_SECRET` | Slack | Ingress signature secret. |
| `OPENCLAW_CONNECTOR_SLACK_APP_TOKEN` | Slack | Optional Socket Mode app token (`xapp-*`). |
| `OPENCLAW_CONNECTOR_SLACK_CLIENT_ID` | Slack | OAuth client ID for multi-workspace installation flow. |
| `OPENCLAW_CONNECTOR_SLACK_CLIENT_SECRET` | Slack | OAuth client secret for multi-workspace installation flow. |
| `OPENCLAW_CONNECTOR_SLACK_OAUTH_REDIRECT_URI` | Slack | Explicit OAuth callback URL. Falls back to `OPENCLAW_CONNECTOR_PUBLIC_BASE_URL + OPENCLAW_CONNECTOR_SLACK_OAUTH_CALLBACK_PATH` when omitted. |
| `OPENCLAW_CONNECTOR_SLACK_OAUTH_INSTALL_PATH` | Slack | Local install route path (default `/slack/install`). |
| `OPENCLAW_CONNECTOR_SLACK_OAUTH_CALLBACK_PATH` | Slack | Local OAuth callback route path (default `/slack/oauth/callback`). |
| `OPENCLAW_CONNECTOR_SLACK_OAUTH_SCOPES` | Slack | Comma-separated bot scopes for install URL generation. |
| `OPENCLAW_CONNECTOR_SLACK_OAUTH_STATE_TTL_SEC` | Slack | TTL for single-use OAuth state tokens (default `600`). |
| `OPENCLAW_CONNECTOR_SLACK_ALLOWED_USERS` | Slack | Comma-separated trusted user IDs. |
| `OPENCLAW_CONNECTOR_SLACK_ALLOWED_CHANNELS` | Slack | Comma-separated trusted channel IDs. |

Connector posture rules:
- In strict posture (`OPENCLAW_DEPLOYMENT_PROFILE=public` or `OPENCLAW_RUNTIME_PROFILE=hardened`), active connector platforms without allowlist coverage are fail-closed.
- Public deployment profile check surfaces this as `DP-PUBLIC-009`.
- Slack multi-workspace installs persist only encrypted token refs in `connector_installations.json`; raw bot/app tokens remain in encrypted secret storage and must not appear in diagnostics or exported config surfaces.

**Delivery & Media:**

| Variable | Description |
| :--- | :--- |
| `OPENCLAW_CONNECTOR_DELIVERY_TIMEOUT_SEC` | Timeout (sec) for delivering results to chat (default: `600`). |
| `OPENCLAW_CONNECTOR_PUBLIC_BASE_URL` | Public base URL for serving images to LINE/Webhooks. |
| `OPENCLAW_CONNECTOR_MEDIA_PATH` | Local directory for staging media files. |

### 2.5 Execution Budgets & Limits

Contractual limits to prevent resource exhaustion.

| Variable | Default | Description |
| :--- | :--- | :--- |
| `OPENCLAW_MAX_INFLIGHT_SUBMITS_TOTAL` | `2` | Max concurrent jobs submitted to ComfyUI. |
| `OPENCLAW_MAX_INFLIGHT_SUBMITS_WEBHOOK`| `1` | Max concurrent jobs from Webhooks. |
| `OPENCLAW_MAX_INFLIGHT_SUBMITS_TRIGGER`| `1` | Max concurrent jobs from trigger/manual fire paths. |
| `OPENCLAW_MAX_INFLIGHT_SUBMITS_SCHEDULER`| `1` | Max concurrent jobs from scheduler paths. |
| `OPENCLAW_MAX_INFLIGHT_SUBMITS_BRIDGE` | `1` | Max concurrent jobs from Bridge/Sidecar. |
| `OPENCLAW_MAX_INFLIGHT_SUBMITS_PER_TENANT` | `1` | Per-tenant concurrent submit cap (applies when multi-tenant mode is enabled). |
| `OPENCLAW_MAX_RENDERED_WORKFLOW_BYTES` | `524288` | Max size (bytes) of a rendered workflow JSON (512KB). |

### 2.6 Runtime & Diagnostics

| Variable | Description |
| :--- | :--- |
| `OPENCLAW_STATE_DIR` | Directory for persistent state (DBs, history, logs). Default: `ComfyUI/user/default/openclaw` |
| `OPENCLAW_LOG_TRUNCATE_ON_START` | Set `1` to truncate active log file (`openclaw.log`) once at process startup before new handlers write records. |
| `OPENCLAW_DIAGNOSTICS` | Comma-separated list of subsystems to enable debug logging for (e.g. `webhook.*,templates`). Safe-redacted. |
| `OPENCLAW_CONNECTOR_DEBUG` | Set `1` to enable verbose debug logging in Connector. |

Runtime guardrails contract (ENV-driven, runtime-only):
- `GET /openclaw/config` may include a `runtime_guardrails` diagnostics object describing effective runtime caps, sources, and degraded status.
- Runtime guardrails are evaluated at runtime (deployment/runtime profile aware) and are not part of the persisted user config contract.
- `PUT /openclaw/config` rejects attempts to persist `runtime_guardrails` / legacy guardrail payloads; callers must change the underlying environment variables instead.

---

## 3. Secret Rotation & Migration

### 3.1 Rotation Procedure

To rotate a secret (e.g., `OPENCLAW_ADMIN_TOKEN` or `OPENCLAW_LLM_API_KEY`):

1. **Update Environment**: Change the value in your `.env` file or environment configuration.
2. **Restart**: Restart ComfyUI (and the Connector process if running).
3. **Verify**: Check `/openclaw/health` to ensure services initialized correctly.

*Note: There is no zero-downtime rotation support in v1. Restart is required.*

### 3.2 Key Precedence

If multiple layers are configured for the same purpose, the following order applies:

1. Environment variable layer (`OPENCLAW_*` preferred, `MOLTBOT_*` fallback only when preferred key is absent)
2. Runtime override (process-local, non-persisted)
3. File-based config (`OPENCLAW_STATE_DIR/config.json`)
4. Defaults (Lowest priority)

### 3.3 Persistence

Non-secret configuration (such as enabled/disabled flags, feature toggles) may be persisted in the `OPENCLAW_STATE_DIR/config.json` via the Settings API. Runtime overrides (if enabled by internal callers) are process-local and non-persisted. **Environment variables always override runtime and persisted settings**.

Multi-tenant persistence note:
- global LLM settings are stored under `llm`
- tenant-specific settings are stored under `tenants.<tenant_id>.llm`
- tenant fallback to global config is disabled by default and requires `OPENCLAW_MULTI_TENANT_ALLOW_CONFIG_FALLBACK=1`

Persistence guardrails:
- Runtime-only guardrail fields (for example `runtime_guardrails` and legacy guardrail aliases) are stripped/ignored when loading persisted config and rejected on `/config` write requests.
- This prevents runtime safety caps (timeouts/retries/provider safety clamps) from being silently converted into mutable persisted settings.
