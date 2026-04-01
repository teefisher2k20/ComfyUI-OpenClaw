import { expect } from '@playwright/test';

const HOST_SURFACES = Object.freeze({
  standaloneFrontend: 'standalone_frontend',
  desktop: 'desktop',
});

function resolveUiTimeoutMs() {
  const raw = process.env.OPENCLAW_E2E_READY_TIMEOUT_MS;
  if (raw) {
    const parsed = Number.parseInt(raw, 10);
    if (Number.isInteger(parsed) && parsed > 0) {
      return parsed;
    }
  }

  // IMPORTANT: WSL on /mnt/* can load the module-heavy harness much slower than
  // native filesystems, especially after several sequential page reloads in the
  // same worker; give readiness checks extra budget to avoid false reds.
  if (process.platform === 'linux' && process.env.WSL_DISTRO_NAME && process.cwd().startsWith('/mnt/')) {
    return 120_000;
  }

  return 30_000;
}

function normalizeApiPath(pathname) {
  if (typeof pathname !== 'string') return '';
  const stripped = pathname.startsWith('/api/') ? pathname.slice(4) : pathname;
  return stripped.replace(/\/+$/, '');
}

function isCompatApiPath(pathname, suffix) {
  const normalizedSuffix = String(suffix || '').replace(/\/+$/, '');
  const normalizedPath = normalizeApiPath(pathname);
  return normalizedPath === `/openclaw${normalizedSuffix}` || normalizedPath === `/moltbot${normalizedSuffix}`;
}

function isNativeApiPath(pathname, suffix) {
  const normalizedSuffix = String(suffix || '').replace(/\/+$/, '');
  return normalizeApiPath(pathname) === normalizedSuffix;
}

function jsonRoute(body, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  };
}

function normalizeHostSurface(hostSurface) {
  if (hostSurface === HOST_SURFACES.desktop || hostSurface === 'desktop') {
    return HOST_SURFACES.desktop;
  }
  return HOST_SURFACES.standaloneFrontend;
}

export async function installHostRuntime(page, { hostSurface = HOST_SURFACES.standaloneFrontend } = {}) {
  const resolvedHostSurface = normalizeHostSurface(hostSurface);

  await page.addInitScript((options) => {
    const currentHostSurface = options?.hostSurface === 'desktop'
      ? 'desktop'
      : 'standalone_frontend';

    window.__openclawTestHostSurface = currentHostSurface;

    if (currentHostSurface === 'desktop') {
      window.__DISTRIBUTION__ = 'desktop';
      window.electronAPI = window.electronAPI || {
        getPlatform: () => 'win32',
        platform: 'win32',
        versions: { electron: 'test' },
      };
      try {
        delete window.__OPENCLAW_HOST_SURFACE__;
      } catch (error) {
        window.__OPENCLAW_HOST_SURFACE__ = undefined;
      }
    } else {
      window.__OPENCLAW_HOST_SURFACE__ = 'standalone_frontend';
      window.__DISTRIBUTION__ = 'standalone_frontend';
      try {
        delete window.electronAPI;
      } catch (error) {
        window.electronAPI = undefined;
      }
    }

    // CRITICAL: the harness must provide the host globals that OpenClaw touches
    // during startup. Missing host shims can surface as Windows-only false-red
    // module-load failures when the static harness server is already under load.
    window.comfyui_version = window.comfyui_version || 'test';

    class OpenClawTestEventSource {
      constructor(url) {
        this.url = url;
        this.readyState = 1;
        this.onmessage = null;
        this.onerror = null;
      }

      addEventListener() { }
      removeEventListener() { }

      close() {
        this.readyState = 2;
      }
    }

    window.EventSource = OpenClawTestEventSource;
    window.__openclawMockEventSourceInstalled = true;

    if (typeof window.LGraphCanvas !== 'function') {
      window.LGraphCanvas = function LGraphCanvas() { };
    }
    if (typeof window.LGraphCanvas.prototype.getNodeMenuOptions !== 'function') {
      window.LGraphCanvas.prototype.getNodeMenuOptions = function getNodeMenuOptions() {
        return [];
      };
    }
  }, { hostSurface: resolvedHostSurface });
}

export async function mockComfyUiCore(page, options = {}) {
  await installHostRuntime(page, options);

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

  await page.route('**/config**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== 'GET' || !isCompatApiPath(url.pathname, '/config')) {
      await route.fallback();
      return;
    }

    await route.fulfill(
      jsonRoute({
        ok: true,
        config: {
          provider: 'openai',
          model: 'test-model',
          base_url: '',
          timeout_sec: 30,
          max_retries: 1,
        },
        sources: {
          provider: 'default',
          model: 'default',
        },
        providers: [
          { id: 'openai', label: 'OpenAI' },
        ],
        apply: {},
        schema: {},
      })
    );
  });

  await page.route('**/logs/tail**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== 'GET' || !isCompatApiPath(url.pathname, '/logs/tail')) {
      await route.fallback();
      return;
    }

    await route.fulfill(jsonRoute({ ok: true, content: [] }));
  });

  await page.route('**/system_stats**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== 'GET' || !isNativeApiPath(url.pathname, '/system_stats')) {
      await route.fallback();
      return;
    }

    await route.fulfill(jsonRoute({ comfyui_version: 'test' }));
  });

  await page.route('**/system_info**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== 'GET' || !isNativeApiPath(url.pathname, '/system_info')) {
      await route.fallback();
      return;
    }

    await route.fulfill(jsonRoute({ name: 'ComfyUI', version: 'test' }));
  });

  await page.route('**/version**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== 'GET' || !isNativeApiPath(url.pathname, '/version')) {
      await route.fallback();
      return;
    }

    await route.fulfill(jsonRoute({ name: 'ComfyUI', version: 'test' }));
  });

  await page.route('**/models/search**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== 'GET' || !isCompatApiPath(url.pathname, '/models/search')) {
      await route.fallback();
      return;
    }

    await route.fulfill(
      jsonRoute({
        ok: true,
        items: [],
        pagination: { limit: 100, offset: 0, total: 0 },
        filters: {},
      })
    );
  });

  await page.route('**/models/downloads**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== 'GET' || !isCompatApiPath(url.pathname, '/models/downloads')) {
      await route.fallback();
      return;
    }

    await route.fulfill(
      jsonRoute({
        ok: true,
        tasks: [],
        pagination: { limit: 100, offset: 0, total: 0 },
        filters: {},
        delta: { next_since_seq: 0 },
      })
    );
  });

  await page.route('**/models/installations**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== 'GET' || !isCompatApiPath(url.pathname, '/models/installations')) {
      await route.fallback();
      return;
    }

    await route.fulfill(
      jsonRoute({
        ok: true,
        installations: [],
        pagination: { limit: 100, offset: 0, total: 0 },
        filters: {},
      })
    );
  });

  await page.route('**/assist/planner/profiles**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== 'GET' || !isCompatApiPath(url.pathname, '/assist/planner/profiles')) {
      await route.fallback();
      return;
    }

    await route.fulfill(
      jsonRoute({
        ok: true,
        profiles: [],
        default_profile: 'SDXL-v1',
      })
    );
  });

  await page.route('**/presets**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== 'GET' || !isCompatApiPath(url.pathname, '/presets')) {
      await route.fallback();
      return;
    }

    await route.fulfill(jsonRoute([]));
  });

  await page.route('**/approvals**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== 'GET' || !isCompatApiPath(url.pathname, '/approvals')) {
      await route.fallback();
      return;
    }

    await route.fulfill(
      jsonRoute({
        ok: true,
        approvals: [],
        pagination: { limit: 100, offset: 0, total: 0 },
      })
    );
  });

  await page.route('**/preflight/inventory**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== 'GET' || !isCompatApiPath(url.pathname, '/preflight/inventory')) {
      await route.fallback();
      return;
    }

    await route.fulfill(
      jsonRoute({
        ok: true,
        nodes: ['CheckpointLoaderSimple'],
        models: {
          checkpoints: ['base.ckpt'],
        },
        snapshot_ts: 0,
        scan_state: 'idle',
        stale: false,
        last_error: null,
      })
    );
  });

  await page.route('**/packs**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== 'GET' || !isCompatApiPath(url.pathname, '/packs')) {
      await route.fallback();
      return;
    }

    await route.fulfill(
      jsonRoute({
        ok: true,
        packs: [],
      })
    );
  });
}

export async function mockCompatApprovalsList(page, approvals = []) {
  await page.route('**/approvals**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== 'GET' || !isCompatApiPath(url.pathname, '/approvals')) {
      await route.fallback();
      return;
    }

    const statusFilter = String(url.searchParams.get('status') || '').trim().toLowerCase();
    const filtered = statusFilter
      ? approvals.filter((item) => String(item.status || '').toLowerCase() === statusFilter)
      : approvals;

    await route.fulfill(
      jsonRoute({
        ok: true,
        approvals: filtered,
        pagination: { limit: 100, offset: 0, total: filtered.length },
      })
    );
  });
}

export async function mockRemoteAdminBaseline(
  page,
  {
    hostSurface = HOST_SURFACES.standaloneFrontend,
    approvals = [],
  } = {}
) {
  await installHostRuntime(page, { hostSurface });

  const sharedConfig = {
    provider: 'openai',
    model: 'test-model',
    base_url: '',
    timeout_sec: 30,
    max_retries: 1,
    llm_key_configured: false,
  };

  await page.route('**/health**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== 'GET' || !isCompatApiPath(url.pathname, '/health')) {
      await route.fallback();
      return;
    }

    await route.fulfill(
      jsonRoute({
        ok: true,
        pack: { name: 'ComfyUI-OpenClaw', version: 'test' },
        config: sharedConfig,
        stats: {
          approvals_pending: approvals.filter((item) => String(item.status || '').toLowerCase() === 'pending').length,
          queue_depth: 0,
          observability: { total_dropped: 0 },
        },
        control_plane: { mode: 'test' },
        deployment_profile: 'desktop-host-harness',
        uptime_sec: 42,
      })
    );
  });

  await page.route('**/logs/tail**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== 'GET' || !isCompatApiPath(url.pathname, '/logs/tail')) {
      await route.fallback();
      return;
    }

    await route.fulfill(
      jsonRoute({
        ok: true,
        tail: '',
      })
    );
  });

  await page.route('**/runs**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== 'GET' || !isCompatApiPath(url.pathname, '/runs')) {
      await route.fallback();
      return;
    }

    await route.fulfill(
      jsonRoute({
        ok: true,
        runs: [],
      })
    );
  });

  await page.route('**/schedules**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== 'GET' || !isCompatApiPath(url.pathname, '/schedules')) {
      await route.fallback();
      return;
    }

    await route.fulfill(
      jsonRoute({
        ok: true,
        schedules: [],
      })
    );
  });

  await page.route('**/config**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (!isCompatApiPath(url.pathname, '/config')) {
      await route.fallback();
      return;
    }

    if (request.method() === 'GET') {
      await route.fulfill(
        jsonRoute({
          ok: true,
          config: sharedConfig,
        })
      );
      return;
    }

    if (request.method() === 'PUT') {
      await route.fulfill(
        jsonRoute({
          ok: true,
          config: sharedConfig,
        })
      );
      return;
    }

    await route.fallback();
  });

  await page.route('**/security/doctor**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== 'GET' || !isCompatApiPath(url.pathname, '/security/doctor')) {
      await route.fallback();
      return;
    }

    await route.fulfill(
      jsonRoute({
        ok: true,
        checks: [],
      })
    );
  });

  await page.route('**/preflight/inventory**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== 'GET' || !isCompatApiPath(url.pathname, '/preflight/inventory')) {
      await route.fallback();
      return;
    }

    await route.fulfill(
      jsonRoute({
        ok: true,
        nodes: ['CheckpointLoaderSimple'],
        models: {
          checkpoints: ['base.ckpt'],
        },
        snapshot_ts: 0,
        scan_state: 'idle',
        stale: false,
        last_error: null,
      })
    );
  });

  await mockCompatApprovalsList(page, approvals);
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
