import { expect } from '@playwright/test';

function resolveUiTimeoutMs() {
  const raw = process.env.OPENCLAW_E2E_READY_TIMEOUT_MS;
  if (raw) {
    const parsed = Number.parseInt(raw, 10);
    if (Number.isInteger(parsed) && parsed > 0) {
      return parsed;
    }
  }

  // IMPORTANT: WSL on /mnt/* can load the module-heavy harness much slower than
  // native filesystems; give readiness checks extra budget to avoid false reds.
  if (process.platform === 'linux' && process.env.WSL_DISTRO_NAME && process.cwd().startsWith('/mnt/')) {
    return 60_000;
  }

  return 30_000;
}

export async function mockComfyUiCore(page) {
  // CRITICAL: only fulfill root /scripts/app.js.
  // Do NOT accept /extensions/<pack>/scripts/app.js, otherwise bad relative imports are masked in E2E.
  await page.route('**/scripts/app.js', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname !== '/scripts/app.js') {
      await route.abort();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/javascript',
      body: 'export const app = window.app;',
    });
  });

  // CRITICAL: same rule for /scripts/api.js to avoid false-green import paths.
  await page.route('**/scripts/api.js', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname !== '/scripts/api.js') {
      await route.abort();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/javascript',
      body: `
        export const api = {
          fetchApi: async (route, options) => {
             // Prefix with /api if not already present (shim logic simulation)
             const url = "/api" + route;
             return fetch(url, options);
          },
          apiURL: (route) => "/api" + route,
          fileURL: (route) => route // Simplified for test
        };
      `,
    });
  });
}

export async function waitForOpenClawReady(page) {
  const timeoutMs = resolveUiTimeoutMs();
  await page.waitForFunction(
    () => window.__openclawTestReady === true || window.__openclawTestError,
    null,
    { timeout: timeoutMs }
  );

  const error = await page.evaluate(() => window.__openclawTestError);
  if (error) {
    throw new Error(`OpenClaw test harness failed to load: ${error?.message || error}`);
  }

  // Basic sanity: header + tab bar exists
  await expect(page.locator('.openclaw-header')).toBeVisible();
  await expect(page.locator('.openclaw-tabs')).toBeVisible();
}

export async function clickTab(page, title) {
  const tab = page.locator('.openclaw-tab', { hasText: title });
  const timeoutMs = resolveUiTimeoutMs();
  await expect(tab).toBeVisible({ timeout: timeoutMs });
  await tab.click({ timeout: timeoutMs });
}
