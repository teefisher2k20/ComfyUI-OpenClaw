#!/usr/bin/env bash
set -euo pipefail
set -o errtrace

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

trap 'echo "[tests] ERROR at line ${LINENO}: ${BASH_COMMAND}" >&2' ERR

echo "[tests] repo: $ROOT_DIR"

# Cache isolation for pre-commit + black
export PRE_COMMIT_HOME="${PRE_COMMIT_HOME:-$ROOT_DIR/.tmp/pre-commit}"
export BLACK_CACHE_DIR="${BLACK_CACHE_DIR:-$ROOT_DIR/.tmp/black-cache}"
mkdir -p "$PRE_COMMIT_HOME" "$BLACK_CACHE_DIR"

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "[tests] ERROR: missing command: $cmd" >&2
    exit 1
  fi
}

is_wsl() {
  grep -qiE "(microsoft|wsl)" /proc/version 2>/dev/null
}

select_venv_dir() {
  # Explicit override for advanced/local setups.
  if [ -n "${OPENCLAW_TEST_VENV:-}" ]; then
    echo "$OPENCLAW_TEST_VENV"
    return 0
  fi
  # IMPORTANT:
  # In WSL, prefer dedicated Linux venv to avoid clashing with Windows .venv.
  if is_wsl; then
    echo "$ROOT_DIR/.venv-wsl"
  else
    echo "$ROOT_DIR/.venv"
  fi
}

pip_install_or_fail() {
  local why="$1"
  shift
  if "$VENV_PY" -m pip install "$@"; then
    return 0
  fi
  echo "[tests] ERROR: failed to install dependency ($why): $*" >&2
  echo "[tests] HINT: check internet/proxy, then retry the script." >&2
  echo "[tests] HINT: if offline, pre-install into venv manually: $VENV_PY -m pip install $*" >&2
  exit 1
}

require_cmd node
require_cmd npm

# Always use project-local venv to avoid global interpreter / tool drift.
VENV_DIR="$(select_venv_dir)"
VENV_PY="$VENV_DIR/bin/python"
if [ ! -x "$VENV_PY" ]; then
  echo "[tests] Creating project venv at $VENV_DIR ..."
  if command -v python3 >/dev/null 2>&1; then
    python3 -m venv "$VENV_DIR"
  elif command -v python >/dev/null 2>&1; then
    python -m venv "$VENV_DIR"
  else
    echo "[tests] ERROR: no bootstrap Python found (need python3 or python)" >&2
    exit 1
  fi
fi

if ! "$VENV_PY" -m pre_commit --version >/dev/null 2>&1; then
  echo "[tests] Installing pre-commit into project venv ($VENV_DIR) ..."
  pip_install_or_fail "required for detect-secrets and hook validation" -U pip pre-commit
fi

if ! "$VENV_PY" -c "import aiohttp" >/dev/null 2>&1; then
  echo "[tests] Installing aiohttp into project venv ($VENV_DIR) ..."
  pip_install_or_fail "required by import paths used in unit tests" aiohttp
fi

if ! "$VENV_PY" -c "import cryptography" >/dev/null 2>&1; then
  echo "[tests] Installing cryptography into project venv ($VENV_DIR) ..."
  pip_install_or_fail "required for S57 secrets-at-rest encryption tests" cryptography
fi

NODE_MAJOR="$(node -p "process.versions.node.split('.')[0]")"
if [ "$NODE_MAJOR" -lt 18 ]; then
  # Best-effort: try to use nvm if available
  if [ -n "${NVM_DIR:-}" ] && [ -s "${NVM_DIR}/nvm.sh" ]; then
    # shellcheck disable=SC1090
    . "${NVM_DIR}/nvm.sh"
  elif [ -s "${HOME}/.nvm/nvm.sh" ]; then
    # shellcheck disable=SC1091
    . "${HOME}/.nvm/nvm.sh"
  fi
  if command -v nvm >/dev/null 2>&1; then
    nvm use 18 >/dev/null 2>&1 || true
  fi
  NODE_MAJOR="$(node -p "process.versions.node.split('.')[0]")"
fi

if [ "$NODE_MAJOR" -lt 18 ]; then
  echo "[tests] ERROR: Node >=18 required, current=$(node -v)" >&2
  echo "[tests] Hint: source ~/.nvm/nvm.sh && nvm use 18" >&2
  exit 1
fi

can_bind_local_port() {
  local port="$1"
  node -e "const net=require('net'); const port=Number(process.argv[1]); const server=net.createServer(); server.unref(); server.once('error', ()=>process.exit(1)); server.listen({host:'127.0.0.1', port, exclusive:true}, ()=>server.close(()=>process.exit(0)));" "$port" >/dev/null 2>&1
}

resolve_e2e_port() {
  if [ -n "${OPENCLAW_E2E_PORT:-}" ]; then
    if ! can_bind_local_port "$OPENCLAW_E2E_PORT"; then
      echo "[tests] ERROR: OPENCLAW_E2E_PORT=$OPENCLAW_E2E_PORT is not bindable on 127.0.0.1." >&2
      echo "[tests] Hint: unset OPENCLAW_E2E_PORT or set another port (example: 3300)." >&2
      exit 1
    fi
    echo "$OPENCLAW_E2E_PORT"
    return 0
  fi

  local candidate
  for candidate in 3000 3300 3400 3500 3600; do
    if can_bind_local_port "$candidate"; then
      echo "$candidate"
      return 0
    fi
  done

  echo "[tests] ERROR: no bindable local port found for Playwright webServer (tried 3000,3300,3400,3500,3600)." >&2
  exit 1
}

E2E_PORT="$(resolve_e2e_port)"
export OPENCLAW_E2E_PORT="$E2E_PORT"
if [ "$E2E_PORT" != "3000" ]; then
  echo "[tests] WARN: port 3000 unavailable; using OPENCLAW_E2E_PORT=$E2E_PORT for Playwright."
else
  echo "[tests] INFO: using OPENCLAW_E2E_PORT=$E2E_PORT for Playwright."
fi

echo "[tests] Node version: $(node -v)"

echo "[tests] 0/8 R120 dependency preflight"
"$VENV_PY" scripts/preflight_check.py --strict

echo "[tests] 1/8 detect-secrets"
"$VENV_PY" -m pre_commit run detect-secrets --all-files

echo "[tests] 2/8 pre-commit all hooks (pass 1: autofix)"
if "$VENV_PY" -m pre_commit run --all-files --show-diff-on-failure; then
  :
else
  echo "[tests] INFO: pre-commit reported changes/issues; running pass 2 verification..."
  "$VENV_PY" -m pre_commit run --all-files --show-diff-on-failure
fi

echo "[tests] 3/8 backend unit tests"
MOLTBOT_STATE_DIR="$ROOT_DIR/moltbot_state/_local_unit" "$VENV_PY" scripts/run_unittests.py --start-dir tests --pattern "test_*.py" --enforce-skip-policy tests/skip_policy.json

if [ -n "${OPENCLAW_IMPL_RECORD_PATH:-}" ]; then
  echo "[tests] 3.5/8 implementation record lint (strict)"
  # IMPORTANT: strict mode is opt-in via OPENCLAW_IMPL_RECORD_PATH to avoid retroactive legacy record failures.
  "$VENV_PY" scripts/lint_implementation_record.py --path "$OPENCLAW_IMPL_RECORD_PATH" --strict
fi

echo "[tests] 4/8 backend real E2E lanes (R122/R123)"
MOLTBOT_STATE_DIR="$ROOT_DIR/moltbot_state/_local_backend_e2e_real" \
  "$VENV_PY" scripts/run_unittests.py --module tests.test_r122_real_backend_lane --enforce-skip-policy tests/skip_policy.json --max-skipped 0
MOLTBOT_STATE_DIR="$ROOT_DIR/moltbot_state/_local_backend_e2e_real" \
  "$VENV_PY" scripts/run_unittests.py --module tests.test_r123_real_backend_model_list_lane --enforce-skip-policy tests/skip_policy.json --max-skipped 0

echo "[tests] 5/8 R121 retry partition contract"
"$VENV_PY" scripts/run_unittests.py --module tests.test_r121_retry_partition_contract --enforce-skip-policy tests/skip_policy.json --max-skipped 0

echo "[tests] 6/8 Slack integration gates (R124/R125/R117/F57)"
"$VENV_PY" scripts/run_unittests.py --module tests.test_r124_slack_ingress_contract --enforce-skip-policy tests/skip_policy.json --max-skipped 0
"$VENV_PY" scripts/run_unittests.py --module tests.test_r125_slack_real_backend_lane --enforce-skip-policy tests/skip_policy.json --max-skipped 0
"$VENV_PY" scripts/run_unittests.py --module tests.test_r117_observability_redaction_e2e --enforce-skip-policy tests/skip_policy.json --max-skipped 0
"$VENV_PY" scripts/run_unittests.py --module tests.test_r117_observability_redaction_endpoints --enforce-skip-policy tests/skip_policy.json --max-skipped 0
"$VENV_PY" scripts/run_unittests.py --module tests.test_f57_slack_transport_parity --enforce-skip-policy tests/skip_policy.json --max-skipped 0
"$VENV_PY" scripts/run_unittests.py --module tests.test_f57_slack_socket_mode_startup --enforce-skip-policy tests/skip_policy.json --max-skipped 0

echo "[tests] 7/8 R118 adversarial gate (adaptive: smoke/extended)"
MOLTBOT_STATE_DIR="$ROOT_DIR/moltbot_state/_local_adversarial" \
  "$VENV_PY" scripts/run_adversarial_gate.py --profile auto --seed 42 --artifact-dir .tmp/adversarial

echo "[tests] 8/8 frontend E2E"
npm test

echo "[tests] PASS"
