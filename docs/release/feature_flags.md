# Feature Flag Policy

This document lists the feature flags that control **risky capabilities** in ComfyUI-OpenClaw.
Users should audit these flags before deploying to a public or untrusted network.

> [!WARNING]
> Enabling these flags increases the attack surface. Ensure you have read the [Security Policy](../SECURITY.md) and use appropriate network controls (e.g., Tailscale, Reverse Proxy with Auth).

## Risk Levels

- **Low**: Safe for most deployments.
- **Medium**: Exposure risk if misconfigured; requires token auth.
- **High**: Significant risk; enables remote execution or bypasses safety checks.

---

## Runtime Flags

| Flag | Default | Risk | Description |
| :--- | :--- | :--- | :--- |
| `OPENCLAW_CONNECTOR_ADMIN_TOKEN` | *None* | **Medium** | Required for admin commands (stop/approve/trace) if server auth is enabled. If missing, admin commands fail safe. |
| `OPENCLAW_ALLOW_REMOTE_ADMIN` | `0` | **High** | Be careful! Allows admin actions from non-loopback IPs if token is present (including writes from `/openclaw/admin` remote console). Default is loopback-only for admin. |
| `OPENCLAW_BRIDGE_ENABLED` | `0` | **High** | Enables the sidecar bridge for remote orchestration. Requires `OPENCLAW_BRIDGE_DEVICE_TOKEN` (and in public posture also mTLS + device allowlist controls). |
| `OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST` | `0` | **High** | Bypasses the known-host allowlist for LLM `base_url`. Allows SSRF to public IPs. |
| `OPENCLAW_ALLOW_INSECURE_BASE_URL` | `0` | **Critical** | Allows HTTP (non-HTTPS) or private IP `base_url` for LLM. Risk of internal network scanning (SSRF). |
| `OPENCLAW_1PASSWORD_ENABLED` | `0` | **Medium** | Enables optional local 1Password CLI key lookup. Requires explicit command allowlist (`OPENCLAW_1PASSWORD_ALLOWED_COMMANDS`) and vault config; fail-closed when misconfigured. |
| `OPENCLAW_LOG_TRUNCATE_ON_START` | `0` | **Low** | Operational log hygiene toggle. If `1`, truncates active `openclaw.log` at startup (once per process). |
| `OPENCLAW_CONNECTOR_DISCORD_TOKEN` | *None* | **Medium** | Presence enables Discord Bot gateway. |
| `OPENCLAW_CONNECTOR_LINE_CHANNEL_SECRET` | *None* | **Medium** | Presence enables LINE webhook listener. Requires a public HTTPS endpoint. |
| `OPENCLAW_CONNECTOR_TELEGRAM_TOKEN` | *None* | **Low** | Presence enables Telegram long-polling. Outbound only. |
| `OPENCLAW_OBSERVABILITY_TOKEN` | *None* | **Low** | Protects `/openclaw/logs/tail` and `/openclaw/config`. Recommended for all remote deployments. |

---

## Enabling Policy

1. **Documentation**: Any PR adding a new risky flag MUST update this table.
2. **Default**: All high-risk flags MUST default to `0` (Disabled).
3. **Validation**: CI tests should run with default flags (secure) and verify risky features are unreachable.
