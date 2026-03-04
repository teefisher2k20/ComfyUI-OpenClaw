# Security Policy

## Quick Links

- Deployment profiles and checklists: [Security Deployment Guide](docs/security_deployment_guide.md)
- Runtime startup hardening behavior: [Runtime Hardening and Startup](docs/runtime_hardening_and_startup.md)
- Pre-exposure checklist: [Security Checklist](docs/security_checklist.md)
- Deployment self-check command:
  - `python scripts/check_deployment_profile.py --profile local|lan|public`

## Supported Versions

Only the latest version of ComfyUI-OpenClaw is supported for security updates.

| Version | Supported          |
| ------- | ------------------ |
| Latest  | :white_check_mark: |
| < 0.2.0 | :x:                |

## Reporting a Vulnerability

Please report security vulnerabilities by creating a **private** issue on GitHub if possible, or contact the maintainers directly. Do not open public issues for sensitive security flaws.

---

# Safe Deployment Guide

OpenClaw is a powerful extension that interacts with LLMs and the filesystem (via ComfyUI). **By default, it is designed for local (localhost) use.** Exposing it to the public internet requires careful configuration.

## ⚠️ Warning

**Do NOT expose your ComfyUI instance directly to the public internet** (for example via direct port-forwarding) without a secure reverse proxy or VPN.

## Shared Listener Boundary (Critical)

OpenClaw and ComfyUI share the same HTTP listener/port.

This means:

1. Protecting `/openclaw/*` routes does not automatically protect ComfyUI-native routes.
2. Public reverse-proxy policy must enforce path-level allow/deny and network ACL boundaries.
3. Public posture requires explicit operator acknowledgement that these boundaries are in place.

High-risk ComfyUI-native routes to deny on public edges unless intentionally required:

- `/prompt`, `/history*`, `/view*`, `/upload*`, `/ws`
- `/api/prompt`, `/api/history*`, `/api/view*`, `/api/upload*`, `/api/ws`

## Recommended Deployment

1. **Localhost (Default)**: Use on your own machine. No extra config needed.
2. **VPN / Tailscale**: Best for private remote access.
3. **SSH Tunnel**: `ssh -L 8188:localhost:8188 user@remote`

## Reverse Proxy Setup (Advanced)

If you must expose OpenClaw via a reverse proxy (Nginx, Caddy, Cloudflare Tunnel), you MUST configure the following:

### 1. Token Boundaries

Logs (`/openclaw/logs/tail`) and Config (`/openclaw/config`) are restricted to loopback clients by default. (Legacy `/moltbot/*` endpoints are also supported.) To allow remote access via proxy, set a secure token:

```bash
export OPENCLAW_OBSERVABILITY_TOKEN="your-secure-random-token-here"
export OPENCLAW_ADMIN_TOKEN="your-secure-random-admin-token-here"
# Legacy compatibility (optional):
# export MOLTBOT_OBSERVABILITY_TOKEN="your-secure-random-token-here"
# export MOLTBOT_ADMIN_TOKEN="your-secure-random-admin-token-here"
```

Then configure your proxy or client to send the header `X-OpenClaw-Obs-Token: your-secure-random-token-here` (legacy: `X-Moltbot-Obs-Token`).

### 2. Trusted Proxy Attribution

If using a reverse proxy, OpenClaw needs to know the *real* client IP for rate limiting enforcement.

Configure your proxy to send `X-Forwarded-For`, then configure trusted proxy ranges:

```bash
export OPENCLAW_TRUST_X_FORWARDED_FOR=1
export OPENCLAW_TRUSTED_PROXIES="127.0.0.1,10.0.0.0/8"
# Legacy compatibility (optional):
# export MOLTBOT_TRUST_X_FORWARDED_FOR=1
# export MOLTBOT_TRUSTED_PROXIES="127.0.0.1,10.0.0.0/8"
```

### 3. Public Profile Boundary Acknowledgement (S69)

For public profile deployments, you must explicitly acknowledge that reverse-proxy path controls and network ACL boundaries are already enforced:

```bash
export OPENCLAW_DEPLOYMENT_PROFILE=public
export OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK=1
# Legacy compatibility (optional):
# export MOLTBOT_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK=1
```

If this acknowledgement is missing in public profile, deployment profile checks fail with `DP-PUBLIC-008`.

### 4. Connector Allowlist Fail-Closed (Public/Hardened)

Connector ingress posture is fail-closed in strict profiles:

- if connector platform ingress is active (Telegram/Discord/LINE/WhatsApp/WeChat/Kakao/Slack)
- and matching allowlist variables are missing
- startup/deployment checks fail closed (`DP-PUBLIC-009` for public profile)

Operational requirement:

- never enable connector platform tokens/enable flags in public or hardened posture without platform allowlist coverage.

### 4.1 Interactive Callback Contract Baseline (Connector)

For interactive connector callbacks (actions/modals/workflow style payloads), the shared callback contract is fail-closed by default:

- signed envelope is required (`signature`, `timestamp`, `request_id`, `workspace_id`, `action_type`, `payload_hash`)
- stale timestamp, replay/duplicate request ID, payload-hash mismatch, or unknown action type are rejected
- workspace-to-installation resolution is fail-closed on missing/ambiguous/inactive/stale-token-ref binding
- policy mapping is explicit (`public`/`run`/`admin`) and untrusted `run` callbacks degrade to approval instead of direct privileged execution

Operational note:

- treat callback decision codes/audit trails as security evidence and investigate repeated reject patterns before enabling higher-risk interactive flows.

### 5. Startup Gate Behavior (R136 + S56)

Startup security gates are fail-closed. Fatal startup gate/bootstrap failures abort route/worker registration and do not continue in a partial state.

Recommended preflight:

```bash
python scripts/check_deployment_profile.py --profile public --strict-warnings
```

### 6. SSRF Protection

OpenClaw validates custom LLM `base_url` settings to prevent Server-Side Request Forgery (SSRF).

* **Default**: known providers and localhost-safe paths are allowed.
* **Pinned connect contract**: on supported CPython versions (current baseline: 3.10+), `safe_io` dials resolved IPs directly for HTTP/HTTPS and keeps TLS `server_hostname` on the original host; the no-skip `tests.test_s70_ssrf_pinning_regression` lane is intended to fail loudly if stdlib connect behavior drifts.
* **Custom base URL**:
  - requires explicit opt-in:

    ```bash
    export OPENCLAW_ALLOW_CUSTOM_BASE_URL=1
    # Legacy compatibility (optional):
    # export MOLTBOT_ALLOW_CUSTOM_BASE_URL=1
    ```
  - use strict allowlist:
    ```bash
    export OPENCLAW_LLM_ALLOWED_HOSTS="api.example.com,llm.example.com"
    ```
  - avoid broad bypass flags in production (`OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST`, `OPENCLAW_ALLOW_INSECURE_BASE_URL`).

### 7. Rate Limiting

OpenClaw enforces internal rate limits:

* Webhooks: 30/min
* Logs: 60/min
* Admin: 20/min

### 8. Sidecar Bridge

OpenClaw supports a "Sidecar Bridge" (F10) for safe interaction with external bots (Discord/Slack).

* **Default**: **DISABLED**.
* **Enable**: Set `OPENCLAW_BRIDGE_ENABLED=1` (legacy `MOLTBOT_BRIDGE_ENABLED=1`).
* **Authentication**: Requires `OPENCLAW_BRIDGE_DEVICE_TOKEN` (legacy `MOLTBOT_BRIDGE_DEVICE_TOKEN`) (shared secret).
* **Network**: Bridge endpoints (`/bridge/*`) are sensitive. **Do not expose to public internet.** Use a private network (Tailscale) or restrict access via reverse proxy.
* **SSRF**: Callback delivery blocks internal IPs. To allow specific external callback hosts, set `OPENCLAW_BRIDGE_CALLBACK_HOST_ALLOWLIST` (legacy: `MOLTBOT_BRIDGE_CALLBACK_HOST_ALLOWLIST`).

## Security Checklist

* [ ] **HTTPS + Edge Auth**: reverse proxy enforces TLS and an additional auth boundary (SSO/Basic/IP ACL).
* [ ] **No direct public bind**: never expose raw ComfyUI/OpenClaw listener directly.
* [ ] **Token boundaries**: set `OPENCLAW_ADMIN_TOKEN` and `OPENCLAW_OBSERVABILITY_TOKEN` (legacy aliases acceptable).
* [ ] **Trusted proxy config**: set `OPENCLAW_TRUST_X_FORWARDED_FOR=1` and exact `OPENCLAW_TRUSTED_PROXIES`.
* [ ] **Public shared-surface ack**: for `OPENCLAW_DEPLOYMENT_PROFILE=public`, set `OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK=1` only after proxy path allowlist + ACL are verified.
* [ ] **Public path deny rules**: block ComfyUI-native high-risk routes and `/api/*` equivalents unless explicitly required.
* [ ] **Connector strict-posture allowlists**: if connector ingress is active in `public` or `hardened`, ensure platform allowlists are set before startup (`DP-PUBLIC-009` for public profile).
* [ ] **Startup gate preflight**: run `python scripts/check_deployment_profile.py --profile public --strict-warnings`.
* [ ] **Runtime diagnostics**: review `GET /openclaw/security/doctor` before exposure.
* [ ] **Least privilege host posture**: do not run as root/Administrator.
