import { expect, test } from '@playwright/test';
import { clickTab, mockComfyUiCore, waitForOpenClawReady } from '../utils/helpers.js';

const presets = [
  {
    id: 'prompt-1',
    name: 'Portrait Prompt',
    category: 'prompt',
    content: { positive: 'portrait lighting', negative: 'blurry' },
  },
  {
    id: 'params-1',
    name: 'Landscape Params',
    category: 'params',
    content: { params: { width: 1280, height: 720, seed: 99 } },
  },
];

test.describe('Library Tab', () => {
  test.beforeEach(async ({ page }) => {
    await mockComfyUiCore(page);
    await page.addInitScript(() => {
      window.confirm = () => true;
      window.alert = () => {};
    });

    await page.route('**/openclaw/presets**', async (route) => {
      const request = route.request();
      const url = new URL(request.url());

      if (request.method() === 'GET' && /\/presets\/[^/]+$/.test(url.pathname)) {
        const id = decodeURIComponent(url.pathname.split('/').pop());
        const preset = presets.find((item) => item.id === id);
        await route.fulfill({
          status: preset ? 200 : 404,
          contentType: 'application/json',
          body: JSON.stringify(preset || { error: 'not_found' }),
        });
        return;
      }

      if (request.method() === 'GET') {
        const category = url.searchParams.get('category');
        const items = category ? presets.filter((item) => item.category === category) : presets;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(items),
        });
        return;
      }

      if (request.method() === 'POST') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ ok: true, id: 'new-preset' }),
        });
        return;
      }

      await route.fulfill({ status: 204, body: '' });
    });

    await page.route('**/openclaw/packs**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ packs: [] }),
      });
    });

    await page.goto('test-harness.html');
    await waitForOpenClawReady(page);
  });

  test('loads presets and applies prompt presets into Planner', async ({ page }) => {
    await clickTab(page, 'Library');

    await expect(page.locator('#lib-list .openclaw-list-item')).toHaveCount(2);
    await expect(page.locator('#lib-list')).toContainText('Portrait Prompt');

    await page.locator('#lib-list button[data-action="apply"]').first().click();

    await expect(page.locator('#openclaw-tab-planner')).toHaveClass(/active/);
    await expect(page.locator('#planner-out-pos')).toHaveValue('portrait lighting');
    await expect(page.locator('#planner-out-neg')).toHaveValue('blurry');
  });

  test('filters presets and routes params presets into Variants', async ({ page }) => {
    await clickTab(page, 'Library');

    await page.locator('#lib-search').fill('landscape');
    await expect(page.locator('#lib-list .openclaw-list-item')).toHaveCount(1);
    await expect(page.locator('#lib-list')).toContainText('Landscape Params');

    await page.locator('#lib-list button[data-action="apply"]').click();

    await expect(page.locator('#openclaw-tab-variants')).toHaveClass(/active/);
    await expect(page.locator('#var-base-params')).toHaveValue(/1280/);
    await expect(page.locator('#var-base-params')).toHaveValue(/99/);
  });

  test('shows a deterministic error state when presets fail to load', async ({ page }) => {
    await page.unroute('**/openclaw/presets**');
    await page.route('**/openclaw/presets**', async (route) => {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'preset_list_failed' }),
      });
    });

    await page.evaluate(async () => {
      const { STORAGE_KEYS } = await import('/web/openclaw_compat.js');
      const { tabManager } = await import('/web/openclaw_tabs.js');

      localStorage.removeItem(STORAGE_KEYS.local.activeTab.primary);
      if (STORAGE_KEYS.local.activeTab.legacy) {
        localStorage.removeItem(STORAGE_KEYS.local.activeTab.legacy);
      }

      const libraryTab = tabManager.tabs.find((tab) => tab.id === 'library');
      if (libraryTab) {
        libraryTab.loaded = false;
      }

      const libraryPane = document.querySelector('#openclaw-tab-library');
      if (libraryPane) {
        libraryPane.innerHTML = '';
      }
    });

    await clickTab(page, 'Library');
    const libraryPane = page.locator('#openclaw-tab-library');
    const libraryError = page.locator('#openclaw-tab-library .openclaw-error-box');

    await expect(libraryPane).toHaveClass(/active/);
    await expect(libraryError).toBeVisible({ timeout: 15000 });
    await expect(libraryError).toContainText('preset_list_failed');
  });
});
