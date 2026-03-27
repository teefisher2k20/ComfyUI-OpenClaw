# Troubleshooting

This guide keeps the longer operational troubleshooting steps out of the README.

## UI shows Backend Not Loaded / endpoints return 404

This usually means ComfyUI did not load the Python side of the pack, or route registration failed during startup.

Steps:

1. Check ComfyUI startup logs for import errors while loading the custom node pack. Search for `openclaw`, `Route registration failed`, or `ModuleNotFoundError`.
2. Confirm the pack folder is directly under `custom_nodes/` and contains `__init__.py`.
3. Run the smoke import check inside the same Python environment ComfyUI uses:

```bash
python scripts/openclaw_smoke_import.py
# or
python scripts/openclaw_smoke_import.py --verbose
```

4. Manually verify the endpoints used by the Settings tab:
   - `GET /api/openclaw/health`
   - `GET /api/openclaw/config`
   - `GET /api/openclaw/logs/tail?n=50`

Notes:

- If your pack folder name is not `comfyui-openclaw`, the smoke script may need `OPENCLAW_PACK_IMPORT_NAME=your-folder-name`.
- If imports fail with a `services.*` module error, check for name collisions with other custom nodes and prefer package-relative imports.

## Operator Doctor

Run the built-in diagnostic tool to verify environment readiness (libraries, permissions, contract files):

```bash
python scripts/operator_doctor.py
# Or check JSON output:
python scripts/operator_doctor.py --json
```

Explorer / inventory note:

- `/openclaw/preflight/inventory` is snapshot-first on current builds.
- A response showing `scan_state=refreshing` or `stale=true` does not necessarily mean the inventory path is broken; it can mean the cached snapshot was returned quickly while a deeper model scan continues in the background.
- Treat `last_error` as the primary signal that the background scan actually failed.

## Webhooks return `403 auth_not_configured`

Set webhook auth environment variables as described in the README quick-start section, then restart ComfyUI.

## LLM model list shows `HTTP 403 ... Private/reserved IP blocked: 127.0.0.1`

This usually means your OpenClaw build is older than the local-loopback SSRF fix. For local providers, `127.0.0.1` and `localhost` are valid targets and should not require insecure SSRF flags.

Checklist:

1. Update OpenClaw to the latest release.
2. For Ollama:
   - run `ollama serve`
   - verify `http://127.0.0.1:11434/api/tags` is reachable on the same machine
3. In OpenClaw Settings:
   - Provider: `Ollama (Local)` or `LM Studio (Local)`
   - Base URL: leave empty to use the provider default, or set a loopback URL explicitly
4. Keep these flags disabled:
   - `OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST=0`
   - `OPENCLAW_ALLOW_INSECURE_BASE_URL=0`

## Remote Admin can open, but custom LLM on `192.168.x.x` is still blocked

This is expected under the current SSRF policy.

- `OPENCLAW_ALLOW_REMOTE_ADMIN=1` only allows remote admin access; it does not relax outbound LLM egress rules.
- `OPENCLAW_LLM_ALLOWED_HOSTS` only extends the exact-host allowlist for custom public hosts.
- Private/reserved IP targets such as `192.168.x.x`, `10.x.x.x`, and `172.16.x.x` remain blocked unless `OPENCLAW_ALLOW_INSECURE_BASE_URL=1` is also set.
- `OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST=1` does not allow private/reserved IPs.
- `OPENCLAW_LLM_ALLOWED_HOSTS=*` is not a wildcard and will not bypass the policy.

Correct setup flow:

1. If you are using a built-in local provider (`ollama`, `lmstudio`), keep it on loopback only and use the provider default or `localhost` / `127.0.0.1` / `::1`.
2. If you need a custom public LLM host, set:
   - `OPENCLAW_ALLOW_CUSTOM_BASE_URL=1`
   - `OPENCLAW_LLM_ALLOWED_HOSTS=<exact-host>` or `OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST=1`
3. If you intentionally need a LAN/private-IP target, set `OPENCLAW_ALLOW_INSECURE_BASE_URL=1`, accept the SSRF risk, and fully restart ComfyUI.
4. On Windows portable, set environment variables in the same launcher that starts `python_embeded\\python.exe`, or restart after `setx` / System Properties changes.
5. Verify the effective value in the same embedded Python runtime:

```bat
python_embeded\python.exe -c "import os; print(repr(os.environ.get('OPENCLAW_LLM_ALLOWED_HOSTS')))"
```

Safer alternative:

- keep the LLM behind a reviewed public HTTPS reverse proxy and allowlist that public host, instead of enabling `OPENCLAW_ALLOW_INSECURE_BASE_URL`
- on current builds, once that override is intentionally enabled and the process is restarted, both Remote Admin validation and `/openclaw/llm/models` should follow the same decision

## Admin Token: server-side vs UI

`OPENCLAW_ADMIN_TOKEN` is a server-side environment variable.

- The Settings UI can use an Admin Token for authenticated requests.
- The UI cannot set or persist the server token itself.

For full setup steps, see the main README quick-start section and [`tests/TEST_SOP.md`](../tests/TEST_SOP.md).
