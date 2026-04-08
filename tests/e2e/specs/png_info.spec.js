import { test, expect } from '@playwright/test';
import { clickTab, mockComfyUiCore, waitForOpenClawReady } from '../utils/helpers.js';

const PNG_BUFFER = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j0kQAAAAASUVORK5CYII=',
  'base64'
);

function normalizeApiPath(pathname) {
  const stripped = pathname.startsWith('/api/') ? pathname.slice(4) : pathname;
  return stripped.replace(/\/+$/, '');
}

function isPngInfoPath(pathname) {
  const normalized = normalizeApiPath(pathname);
  return normalized === '/openclaw/pnginfo' || normalized === '/moltbot/pnginfo';
}

async function installClipboardSpy(page) {
  await page.addInitScript(() => {
    window.__openclawClipboardWrites = [];
    const clipboard = window.navigator.clipboard || {};
    clipboard.writeText = async (value) => {
      window.__openclawClipboardWrites.push(String(value));
    };
    Object.defineProperty(window.navigator, 'clipboard', {
      configurable: true,
      value: clipboard,
    });
  });
}

test.describe('PNG Info Tab', () => {
  test('loads metadata from drag-and-drop and supports prompt copy', async ({ page }) => {
    await installClipboardSpy(page);
    await mockComfyUiCore(page);

    let pngInfoRequests = 0;
    await page.route('**/pnginfo', async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      if (req.method() !== 'POST' || !isPngInfoPath(url.pathname)) {
        await route.fallback();
        return;
      }

      pngInfoRequests += 1;
      const body = req.postDataJSON();
      expect(body.image_b64).toContain('data:image/png;base64,');

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ok: true,
          source: 'a1111',
          info: 'A1111 metadata detected.',
          parameters: {
            positive_prompt: 'studio portrait, dramatic light',
            negative_prompt: 'blur, lowres',
            Steps: '24',
            Sampler: 'Euler a',
            'CFG scale': '7',
            Seed: '42',
            Size: '768x512',
            Model: 'demoModel',
            'Model hash': 'abc123',
          },
          items: {
            prompt: {
              1: {
                class_type: 'KSampler',
              },
            },
            workflow: {
              nodes: [{ id: 1, type: 'KSampler' }],
            },
          },
        }),
      });
    });

    await page.goto('test-harness.html');
    await waitForOpenClawReady(page);
    await clickTab(page, 'PNG Info');

    await page.evaluate((pngBytes) => {
      const bytes = Uint8Array.from(pngBytes);
      const file = new File([bytes], 'meta.png', { type: 'image/png' });
      const transfer = new DataTransfer();
      transfer.items.add(file);
      const event = new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: transfer });
      document.querySelector('#pnginfo-dropzone').dispatchEvent(event);
    }, [...PNG_BUFFER]);

    await expect(page.locator('#pnginfo-status')).toHaveText('Metadata ready');
    await expect(page.locator('#pnginfo-summary-card')).toContainText('A1111');
    await expect(page.locator('#pnginfo-summary-card')).toContainText('demoModel');
    await expect(page.locator('#pnginfo-positive')).toContainText('studio portrait, dramatic light');
    await expect(page.locator('#pnginfo-negative')).toContainText('blur, lowres');
    await expect(page.locator('#pnginfo-raw')).toContainText('"class_type": "KSampler"');
    await expect(page.locator('#pnginfo-preview-image')).toBeVisible();
    expect(pngInfoRequests).toBe(1);

    await page.locator('[data-action="copy-positive"]').click();
    await expect.poll(() => page.evaluate(() => window.__openclawClipboardWrites.at(-1))).toBe('studio portrait, dramatic light');

    await page.locator('[data-action="copy-negative"]').click();
    await expect.poll(() => page.evaluate(() => window.__openclawClipboardWrites.at(-1))).toBe('blur, lowres');
  });

  test('keeps paste scoped to the tab surface and accepts focused dropzone paste', async ({ page }) => {
    await mockComfyUiCore(page);

    let pngInfoRequests = 0;
    await page.route('**/pnginfo', async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      if (req.method() !== 'POST' || !isPngInfoPath(url.pathname)) {
        await route.fallback();
        return;
      }

      pngInfoRequests += 1;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ok: true,
          source: 'comfyui',
          info: 'ComfyUI metadata detected.',
          parameters: {},
          items: {
            workflow: {
              nodes: [{ id: 1, type: 'SaveImage' }],
            },
          },
        }),
      });
    });

    await page.goto('test-harness.html');
    await waitForOpenClawReady(page);
    await clickTab(page, 'PNG Info');

    await page.evaluate((pngBytes) => {
      const bytes = Uint8Array.from(pngBytes);
      const buildPasteEvent = () => {
        const file = new File([bytes], 'paste.png', { type: 'image/png' });
        const transfer = new DataTransfer();
        transfer.items.add(file);
        const event = new Event('paste', { bubbles: true, cancelable: true });
        Object.defineProperty(event, 'clipboardData', { value: transfer });
        return event;
      };

      document.dispatchEvent(buildPasteEvent());
    }, [...PNG_BUFFER]);

    await page.waitForTimeout(150);
    expect(pngInfoRequests).toBe(0);

    await page.locator('#pnginfo-dropzone').focus();
    await page.evaluate((pngBytes) => {
      const bytes = Uint8Array.from(pngBytes);
      const file = new File([bytes], 'paste.png', { type: 'image/png' });
      const transfer = new DataTransfer();
      transfer.items.add(file);
      const event = new Event('paste', { bubbles: true, cancelable: true });
      Object.defineProperty(event, 'clipboardData', { value: transfer });
      document.querySelector('#pnginfo-dropzone').dispatchEvent(event);
    }, [...PNG_BUFFER]);

    await expect(page.locator('#pnginfo-status')).toHaveText('Metadata ready');
    await expect(page.locator('#pnginfo-summary-card')).toContainText('COMFYUI');
    expect(pngInfoRequests).toBe(1);
  });
});
