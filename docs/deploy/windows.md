# Windows Deployment Notes

Running ComfyUI + OpenClaw on Windows, especially with the Portable version.

## Environment Variables

### Portable Version (`run_nvidia_gpu.bat`)

To set OpenClaw security tokens in the portable version, edit your `run_nvidia_gpu.bat` (or create a wrapper `run_openclaw.bat`):

```bat
@echo off
:: Security Tokens
set OPENCLAW_ADMIN_TOKEN=my-secret-token
set OPENCLAW_ALLOW_REMOTE_ADMIN=1
set OPENCLAW_OBSERVABILITY_TOKEN=observability-token
set OPENCLAW_LOG_TRUNCATE_ON_START=1

:: Run ComfyUI
.\python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build
pause
```

Important:

- `set KEY=value` must appear before launching `python_embeded\\python.exe`.
- Do not write `set KEY = value`; CMD treats the spaces as part of the variable name.
- Editing Windows Environment Variables or using `setx` does not change an already-running portable ComfyUI process. Restart the launcher after changes.

### PowerShell

```powershell
$env:OPENCLAW_ADMIN_TOKEN="my-secret-token"
$env:OPENCLAW_ALLOW_REMOTE_ADMIN="1"
$env:OPENCLAW_LOG_TRUNCATE_ON_START="1"
./python_embeded/python.exe -s ComfyUI/main.py
```

## LAN / Mobile Remote Admin Startup

If you want to open the standalone remote admin page from another device in your LAN:

```powershell
$env:OPENCLAW_ADMIN_TOKEN="my-secret-token"
$env:OPENCLAW_ALLOW_REMOTE_ADMIN="1"
$env:OPENCLAW_LOG_TRUNCATE_ON_START="1"
./python_embeded/python.exe -s ComfyUI/main.py --listen 0.0.0.0 --port 8188
```

Then open from phone/tablet browser:

```text
http://<WINDOWS_LAN_IP>:8188/openclaw/admin
```

Find your LAN IP:

```powershell
ipconfig
```

## Custom LLM Base URL on Another LAN Machine

If Remote Admin is running on this Windows host, but your custom/OpenAI-compatible LLM is on another LAN machine such as `192.168.x.x`, the current SSRF policy is stricter than the remote-admin policy:

- `OPENCLAW_LLM_ALLOWED_HOSTS` only allows additional exact public hosts.
- `OPENCLAW_ALLOW_ANY_PUBLIC_LLM_HOST=1` still applies only to public hosts.
- Private/reserved LAN IPs still require `OPENCLAW_ALLOW_INSECURE_BASE_URL=1`.
- `OPENCLAW_LLM_ALLOWED_HOSTS=*` is not supported.
- On current builds, that same override is honored by both Remote Admin validation and `/openclaw/llm/models` refresh requests after a full restart.

Recommended verification in the same embedded runtime:

```bat
.\python_embeded\python.exe -c "import os; print(repr(os.environ.get('OPENCLAW_LLM_ALLOWED_HOSTS')))"
```

If you intentionally accept the risk and enable `OPENCLAW_ALLOW_INSECURE_BASE_URL=1`, restart ComfyUI fully after changing the env vars.

## Service Mode (NSSM)

If you want to run ComfyUI as a background service, use **NSSM** (Non-Sucking Service Manager).

1. Download NSSM.
2. `nssm install ComfyUI`
3. **Application**: Path to python.exe (or bat file).
4. **Environment**: Add tokens here in the Environment tab (Input: `KEY=VALUE` per line).
5. **I/O**: Redirect stdout/stderr to logs so you can debug startup issues.

## Caveats

- **Permissions**: Services run as `SYSTEM` by default. It is safer to create a dedicated user and set the service to Log On as that user.
- **GPU Access**: Ensure the user running the service has access to the GPU driver context (usually fine for logged-in users, tricky for headless services).

## Common Startup Failure: WinError 10013

Symptom:

```text
PermissionError: [WinError 10013] ... bind on address ('0.0.0.0', 8188)
```

Typical causes:

- Port is already occupied by another process.
- Firewall/security policy blocks this bind.
- Reserved/excluded port range on Windows.

Deterministic remediation:

1. Retry with another port (for example `--port 8200`).
2. Ensure no duplicate ComfyUI instance is already listening.
3. Verify inbound firewall rule allows the selected port for `LocalSubnet`.
4. If still failing, check excluded ranges and avoid those ports:

```powershell
netsh int ipv4 show excludedportrange protocol=tcp
```
