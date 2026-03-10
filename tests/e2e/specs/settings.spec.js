import { test, expect } from '@playwright/test';
import { mockComfyUiCore, waitForOpenClawReady, clickTab } from '../utils/helpers.js';

function normalizeApiPath(pathname) {
    return pathname.startsWith('/api/') ? pathname.slice(4) : pathname;
}

function isConfigPath(pathname) {
    const path = normalizeApiPath(pathname);
    return path === '/openclaw/config' || path === '/moltbot/config';
}

function isLogsTailPath(pathname) {
    const path = normalizeApiPath(pathname);
    return path === '/openclaw/logs/tail' || path === '/moltbot/logs/tail';
}

function isHealthPath(pathname) {
    const path = normalizeApiPath(pathname);
    return path === '/openclaw/health' || path === '/moltbot/health';
}

test.describe('Settings Tab Stability', () => {
    test.beforeEach(async ({ page }) => {
        await mockComfyUiCore(page);

        // Mock Config GET & PUT
        await page.route('**/config**', async (route) => {
            const req = route.request();
            const url = new URL(req.url());
            if (!isConfigPath(url.pathname)) {
                await route.fallback();
                return;
            }

            if (req.method() === 'GET') {
                await route.fulfill({
                    status: 200,
                    contentType: 'application/json',
                    body: JSON.stringify({
                        ok: true,
                        config: {
                            provider: 'openai',
                            model: 'gpt-4o',
                            base_url: '',
                            timeout_sec: 120,
                            max_retries: 3
                        },
                        sources: { provider: 'default' },
                        providers: [
                            { id: 'openai', label: 'OpenAI' },
                            { id: 'anthropic', label: 'Anthropic' },
                            { id: 'custom', label: 'Custom' }
                        ],
                        apply: {}
                    }),
                });
                return;
            }

            if (req.method() === 'PUT') {
                // Mock Config PUT (R53 feedback)
                await route.fulfill({
                    status: 200,
                    contentType: 'application/json',
                    body: JSON.stringify({
                        ok: true,
                        apply: {
                            applied_now: ['provider', 'model'],
                            restart_required: []
                        }
                    }),
                });
                return;
            }

            await route.fallback();
        });

        // Mock Logs (Dependency)
        await page.route('**/logs/tail**', async (route) => {
            const url = new URL(route.request().url());
            if (!isLogsTailPath(url.pathname)) {
                await route.fallback();
                return;
            }
            await route.fulfill({ status: 200, body: JSON.stringify({ ok: true, content: [] }) });
        });

        // Mock Health (Dependency)
        await page.route('**/health**', async (route) => {
            const url = new URL(route.request().url());
            if (!isHealthPath(url.pathname)) {
                await route.fallback();
                return;
            }
            await route.fulfill({
                status: 200,
                body: JSON.stringify({ ok: true, config: { llm_key_configured: true }, pack: { version: 'test' } })
            });
        });

        await page.goto('test-harness.html');
        await waitForOpenClawReady(page);
    });

    test('loads settings without flicker and populates fields', async ({ page }) => {
        await clickTab(page, 'Settings');

        await expect(page.getByRole('heading', { name: 'LLM Settings' })).toBeVisible({ timeout: 10000 });

        const providerSelect = page.getByRole('combobox').first();
        await expect(providerSelect).toBeVisible({ timeout: 10000 });
        await expect(providerSelect).toHaveValue('openai');

        const modelSelect = page.getByRole('combobox').nth(1);
        if (await modelSelect.count()) {
            await expect(modelSelect).toHaveValue('gpt-4o');
        } else {
            await expect(page.locator('input[list="openclaw-model-list"]')).toHaveValue('gpt-4o');
        }

        await expect(page.locator('text=Backend 404')).not.toBeVisible();
    });

    test('save triggers hot-reload feedback (R53)', async ({ page }) => {
        await clickTab(page, 'Settings');

        // Click Save (exact match to avoid "Save Key")
        const savePromise = page.waitForResponse((resp) => {
            const url = new URL(resp.url());
            return resp.request().method() === 'PUT' && isConfigPath(url.pathname) && resp.status() === 200;
        });
        await page.getByRole('button', { name: 'Save', exact: true }).click();
        await savePromise;

        // Expect success message
        await expect(page.locator('.openclaw-status.ok')).toContainText('Saved!');
        await expect(page.locator('.openclaw-status.ok')).toContainText('Applied immediately');
    });
});
