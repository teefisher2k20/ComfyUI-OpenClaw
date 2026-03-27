# Runtime Hardening and Startup

This guide explains the startup security model and bridge compatibility behavior.

## What this covers

- Runtime profile selection
- Hardened startup enforcement behavior
- Module startup boundaries
- Bridge protocol handshake compatibility

## Runtime profile

Use `OPENCLAW_RUNTIME_PROFILE` to select startup posture:

- `minimal` (default): compatibility-first
- `hardened`: strict fail-closed startup checks

If the value is unknown, startup falls back to `minimal` with a warning.

You can verify the active profile through:

- `GET /openclaw/capabilities`
- `GET /moltbot/capabilities`

The response includes `runtime_profile`.

## Hardened startup enforcement

When `OPENCLAW_RUNTIME_PROFILE=hardened`, startup enforces mandatory controls and aborts on failure.

Current mandatory checks:

- Authentication is configured for privileged actions
- Unsafe egress bypass is not enabled:
  - `OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST` must not bypass policy
  - `OPENCLAW_ALLOW_INSECURE_BASE_URL` must not bypass policy
- If webhook module is active, webhook auth mode must be configured
- Redaction service must be available
- Connector ingress allowlist coverage is enforced for strict posture:
  - `OPENCLAW_RUNTIME_PROFILE=hardened`: startup fails closed if an active connector platform has no allowlist
  - `OPENCLAW_DEPLOYMENT_PROFILE=public`: deployment/startup checks fail closed for the same condition (`DP-PUBLIC-009`)

In `minimal` mode, these checks are warning-first for local/LAN posture, but `public` deployment profile still enforces fail-closed policy checks.

### Bootstrap fail-closed propagation

Startup bootstrap no longer swallows fatal security-gate errors.
If a critical startup gate fails, initialization aborts deterministically instead of continuing with partial route registration.

## Public deployment shared-surface acknowledgement

When running deployment profile checks for public posture (`OPENCLAW_DEPLOYMENT_PROFILE=public`),
you must explicitly acknowledge that upstream boundary controls are in place:

- `OPENCLAW_PUBLIC_SHARED_SURFACE_BOUNDARY_ACK=1`

Why this exists:

- OpenClaw shares ComfyUI's listener/port.
- Route auth on `/openclaw/*` does not automatically protect ComfyUI-native high-risk routes.
- Public posture requires reverse-proxy path allowlist + network ACL boundaries.

Gate behavior:

- missing ack in public profile: deployment profile check fails (`DP-PUBLIC-008`)
- ack present: this boundary contract check passes

## Connector allowlist posture in strict profiles

Connector token/enable markers activate ingress posture checks for:

- Telegram, Discord, LINE, WhatsApp, WeChat, KakaoTalk, Slack, Feishu/Lark

When a platform is active, at least one platform-specific allowlist variable must be configured.

- `public` deployment profile: fail-closed (`DP-PUBLIC-009`)
- `hardened` runtime profile: fail-closed at startup gate
- non-strict local/LAN posture: warning posture in Security Doctor (`s32_allowlist_coverage`)

## Localhost no-origin override posture

`OPENCLAW_LOCALHOST_ALLOW_NO_ORIGIN` controls a localhost convenience escape hatch for clients
that do not send `Origin` / `Sec-Fetch-Site` headers.

- Default/unset: strict behavior remains active (no-origin requests are denied in convenience mode).
- When set to `true`: no-origin localhost requests are allowed in convenience mode.

Operational visibility:

- Startup emits an explicit warning when the override is enabled.
- Security Doctor reports this as an explicit posture check (`csrf_no_origin_override`, code `SEC-CSRF-001`).
- Startup audit includes a dedicated event so operators can trace when this override is active.

Security note:

- Keep this override disabled for shared/LAN/public deployments.
- Enable only for local CLI/tooling compatibility, and only as long as required.

## Module startup boundaries

Module enablement is decided during startup and then locked.

Current boundary behavior:

- Core, security, observability, scheduler, webhook, and connector modules are initialized at startup
- Bridge module initialization is conditional on `OPENCLAW_BRIDGE_ENABLED`
- If bridge is disabled, bridge route registration is skipped

## Bridge protocol handshake

Sidecar startup performs protocol compatibility negotiation with:

- `POST /bridge/handshake`

Request body:

```json
{ "version": 1 }
```

Response behavior:

- `200` when compatible
- `409` when incompatible (too old or too new)
- Includes compatibility metadata such as server version and minimum supported version

The sidecar bridge client executes this handshake during startup before worker polling.

## Recommended startup baseline

Use this as a starting point for hardened deployments:

```bash
OPENCLAW_RUNTIME_PROFILE=hardened
OPENCLAW_ADMIN_TOKEN=replace-with-strong-token
OPENCLAW_WEBHOOK_AUTH_MODE=hmac
OPENCLAW_WEBHOOK_HMAC_SECRET=replace-with-strong-secret
OPENCLAW_BRIDGE_ENABLED=1
OPENCLAW_BRIDGE_DEVICE_TOKEN=replace-with-bridge-device-token
# Optional: clear stale history in startup log views
OPENCLAW_LOG_TRUNCATE_ON_START=1
```

Then validate:

1. Restart ComfyUI and check startup logs for security gate result.
2. Call `GET /openclaw/capabilities` and confirm `runtime_profile`.
3. If sidecar is used, verify handshake succeeds before worker polling begins.
