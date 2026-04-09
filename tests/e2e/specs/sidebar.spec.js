import { test, expect } from '@playwright/test';
import { mockComfyUiCore, waitForOpenClawReady, clickTab } from '../utils/helpers.js';

test.describe('OpenClaw Sidebar', () => {
  test.beforeEach(async ({ page }) => {
    await mockComfyUiCore(page);
    await page.goto('test-harness.html');
    await waitForOpenClawReady(page);
  });

  test('renders header + tabs', async ({ page }) => {
    await expect(page.locator('.openclaw-title')).toHaveText('OpenClaw');
    await expect(page.locator('.openclaw-repo-link')).toContainText('View on GitHub');
  });

  test('switching tabs does not lose content', async ({ page }) => {
    // Click a few tabs and verify active pane is non-empty
    for (const t of ['Settings', 'Jobs', 'Planner', 'Variants', 'Refiner', 'Library', 'Approvals', 'Explorer', 'Packs', 'Model Manager', 'PNG Info']) {
      await clickTab(page, t);
      const active = page.locator('.openclaw-tab-pane.active');
      await expect(active).toBeVisible();
      await expect(active).not.toBeEmpty();
    }
  });

  test('default harness bootstrap provides stable Settings and Model Manager baselines', async ({ page }) => {
    await clickTab(page, 'Settings');
    await expect(page.locator('.openclaw-log-viewer')).not.toContainText('Failed to load logs');
    await expect(page.locator('details')).toContainText('ComfyUI: test');

    await clickTab(page, 'Model Manager');
    await expect(page.locator('#mm-search-results')).toContainText('No matching models.');
    await expect(page.locator('#mm-tasks')).toContainText('No download tasks.');
    await expect(page.locator('#mm-installations')).toContainText('No managed installations.');

    await clickTab(page, 'PNG Info');
    await expect(page.locator('#pnginfo-dropzone')).toContainText('Drop an image here');
    await expect(page.locator('#pnginfo-empty-state')).toContainText('Load an image to inspect');
  });

  test('harness recovers from one transient openclaw entry fetch failure', async ({ page }) => {
    let failedOnce = false;

    await page.route('**/web/openclaw.js?openclaw_harness_attempt=*', async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname !== '/web/openclaw.js') {
        await route.fallback();
        return;
      }

      if (!failedOnce) {
        failedOnce = true;
        await route.abort('failed');
        return;
      }

      await route.fallback();
    });

    await page.reload();
    await waitForOpenClawReady(page);
    await expect(page.locator('.openclaw-title')).toHaveText('OpenClaw');
    await expect
      .poll(() => page.evaluate(() => window.__openclawTestLoadAttempts))
      .toBe(2);
  });

  test('harness recovers from two transient openclaw entry fetch failures', async ({ page }) => {
    let remainingFailures = 2;

    await page.route('**/web/openclaw.js?openclaw_harness_attempt=*', async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname !== '/web/openclaw.js') {
        await route.fallback();
        return;
      }

      if (remainingFailures > 0) {
        remainingFailures -= 1;
        await route.abort('failed');
        return;
      }

      await route.fallback();
    });

    await page.reload();
    await waitForOpenClawReady(page);
    await expect(page.locator('.openclaw-title')).toHaveText('OpenClaw');
    await expect
      .poll(() => page.evaluate(() => window.__openclawTestLoadAttempts))
      .toBe(3);
  });
});
