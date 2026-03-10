import { test, expect } from '@playwright/test';
import { mockComfyUiCore, waitForOpenClawReady, clickTab } from '../utils/helpers.js';

function normalizeApiPath(pathname) {
  const stripped = pathname.startsWith('/api/') ? pathname.slice(4) : pathname;
  return stripped.replace(/\/+$/, '');
}

function isModelManagerPath(pathname, suffix) {
  const normalizedSuffix = String(suffix || '').replace(/\/+$/, '');
  const path = normalizeApiPath(pathname);
  return path === `/openclaw${normalizedSuffix}` || path === `/moltbot${normalizedSuffix}`;
}

test.describe('Model Manager Tab', () => {
  test('queues and imports a managed model task', async ({ page }) => {
    test.setTimeout(60000);
    await mockComfyUiCore(page);

    const model = {
      id: 'flux-test',
      name: 'Flux Test Model',
      model_type: 'checkpoint',
      source: 'catalog',
      source_label: 'Catalog',
      installed: false,
      download_url: 'https://example.com/flux-test.safetensors',
      sha256: 'a'.repeat(64),
      provenance: {
        publisher: 'OpenClaw',
        license: 'OpenRAIL',
        source_url: 'https://example.com/flux-test',
      },
      tags: ['flux', 'test'],
    };

    let task = null;
    const installations = [];

    await page.route('**/models/search**', async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      if (!isModelManagerPath(url.pathname, '/models/search')) {
        await route.fallback();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ok: true,
          items: [model],
          pagination: { limit: 100, offset: 0, total: 1 },
          filters: {},
        }),
      });
    });

    await page.route('**/models/downloads**', async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      const method = req.method();

      if (isModelManagerPath(url.pathname, '/models/downloads') && method === 'POST') {
        task = {
          task_id: 'task-1',
          model_id: model.id,
          name: model.name,
          state: 'completed',
          progress: 1,
          bytes_downloaded: 1024,
          total_bytes: 1024,
          imported: false,
        };
        await route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({ ok: true, task }),
        });
        return;
      }

      if (isModelManagerPath(url.pathname, '/models/downloads') && method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            ok: true,
            tasks: task ? [task] : [],
            pagination: { limit: 100, offset: 0, total: task ? 1 : 0 },
            filters: {},
          }),
        });
        return;
      }

      await route.fallback();
    });

    await page.route('**/models/import**', async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      if (isModelManagerPath(url.pathname, '/models/import') && req.method() === 'POST') {
        task = { ...task, imported: true };
        const installation = {
          id: 'inst-1',
          model_id: model.id,
          name: model.name,
          model_type: model.model_type,
          installation_path: 'checkpoints/flux-test.safetensors',
        };
        if (!installations.some((item) => item.id === installation.id)) {
          installations.push(installation);
        }
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ ok: true, installation }),
        });
        return;
      }
      await route.fallback();
    });

    await page.route('**/models/installations**', async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      if (!isModelManagerPath(url.pathname, '/models/installations')) {
        await route.fallback();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ok: true,
          installations,
          pagination: { limit: 100, offset: 0, total: installations.length },
          filters: {},
        }),
      });
    });

    await page.goto('test-harness.html');
    await waitForOpenClawReady(page);
    await clickTab(page, 'Model Manager');

    await expect(page.locator('#mm-search-results')).toContainText('Flux Test Model');

    const queueButton = page.locator('#mm-search-results').getByRole('button', { name: 'Queue Download' }).first();
    await expect(queueButton).toBeVisible();
    await queueButton.click();
    await expect(page.locator('#mm-tasks')).toContainText('task-1');

    const importButton = page.locator('#mm-tasks').getByRole('button', { name: 'Import' }).first();
    await expect(importButton).toBeVisible({ timeout: 10000 });

    const importRequest = page.waitForRequest((req) => {
      const url = new URL(req.url());
      return req.method() === 'POST' && isModelManagerPath(url.pathname, '/models/import');
    });

    await importButton.click();
    await importRequest;

    await expect
      .poll(async () => {
        const tasksText = (await page.locator('#mm-tasks').innerText()).toLowerCase();
        const installationsText = (await page.locator('#mm-installations').innerText()).toLowerCase();
        if (installationsText.includes('checkpoints/flux-test.safetensors')) return 'installed';
        if (tasksText.includes('imported')) return 'imported';
        return 'pending';
      }, { timeout: 30000 })
      .not.toBe('pending');
  });
});
