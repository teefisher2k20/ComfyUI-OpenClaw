// @ts-check
const { defineConfig } = require('@playwright/test');

const e2ePort = process.env.OPENCLAW_E2E_PORT || '3000';
const e2eBase = `http://127.0.0.1:${e2ePort}`;

function resolveWorkers() {
  const raw = process.env.OPENCLAW_PLAYWRIGHT_WORKERS;
  if (raw) {
    const parsed = Number.parseInt(raw, 10);
    if (!Number.isInteger(parsed) || parsed < 1) {
      throw new Error(
        `OPENCLAW_PLAYWRIGHT_WORKERS must be a positive integer, got '${raw}'`,
      );
    }
    return parsed;
  }

  // CRITICAL: WSL on /mnt/* has repeatable E2E harness instability under
  // high parallelism; cap workers to keep the repo acceptance gate deterministic.
  if (process.platform === 'linux' && process.env.WSL_DISTRO_NAME && process.cwd().startsWith('/mnt/')) {
    return 1;
  }

  return undefined;
}

module.exports = defineConfig({
  testDir: 'tests/e2e/specs',
  timeout: 30_000,
  retries: 0,
  workers: resolveWorkers(),
  use: {
    baseURL: `${e2eBase}/tests/e2e/`,
    headless: true,
  },
  webServer: {
    // IMPORTANT: allow overriding port for environments where 3000 is blocked/reserved.
    command: `${process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3')} -m http.server ${e2ePort}`,
    url: `${e2eBase}/tests/e2e/test-harness.html`,
    reuseExistingServer: true,
    timeout: 30_000,
  },
});
