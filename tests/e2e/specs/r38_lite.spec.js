import { test, expect } from '@playwright/test';
import { mockComfyUiCore, waitForOpenClawReady, clickTab } from '../utils/helpers.js';

function normalizeApiPath(pathname) {
    const stripped = pathname.startsWith('/api/') ? pathname.slice(4) : pathname;
    return stripped.replace(/\/+$/, '');
}

function isAssistPath(pathname, suffix) {
    const path = normalizeApiPath(pathname);
    return path === `/openclaw${suffix}` || path === `/moltbot${suffix}`;
}

function isConfigPath(pathname) {
    return isAssistPath(pathname, '/config');
}

function isLogsTailPath(pathname) {
    return isAssistPath(pathname, '/logs/tail');
}

function isHealthPath(pathname) {
    return isAssistPath(pathname, '/health');
}

function isPlannerRequest(urlString) {
    const url = new URL(urlString);
    return isAssistPath(url.pathname, '/assist/planner');
}

function isPlannerStreamRequest(urlString) {
    const url = new URL(urlString);
    return isAssistPath(url.pathname, '/assist/planner/stream');
}

function isRefinerRequest(urlString) {
    const url = new URL(urlString);
    return isAssistPath(url.pathname, '/assist/refiner');
}

test.describe('R38 Lite UX lifecycle', () => {
    test.beforeEach(async ({ page }) => {
        await mockComfyUiCore(page);

        await page.route('**/config**', async (route) => {
            const url = new URL(route.request().url());
            if (!isConfigPath(url.pathname)) {
                await route.fallback();
                return;
            }
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, config: {}, apply: {} }) });
        });
        await page.route('**/logs/tail**', async (route) => {
            const url = new URL(route.request().url());
            if (!isLogsTailPath(url.pathname)) {
                await route.fallback();
                return;
            }
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, content: [] }) });
        });
        await page.route('**/health**', async (route) => {
            const url = new URL(route.request().url());
            if (!isHealthPath(url.pathname)) {
                await route.fallback();
                return;
            }
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, pack: { version: 'test' } }) });
        });

        await page.goto('test-harness.html');
        await waitForOpenClawReady(page);
    });

    test('Planner shows staged loading + elapsed timer and then succeeds', async ({ page }) => {
        const pageErrors = [];
        page.on('pageerror', (e) => pageErrors.push(e.message));

        await page.route('**/assist/planner**', async (route) => {
            if (!isPlannerRequest(route.request().url())) {
                await route.fallback();
                return;
            }
            await new Promise((resolve) => setTimeout(resolve, 1700));
            try {
                await route.fulfill({
                    status: 200,
                    contentType: 'application/json',
                    body: JSON.stringify({
                        positive: 'A foggy mountain valley',
                        negative: 'lowres, blurry',
                        params: { width: 1024, height: 1024 },
                    }),
                });
            } catch {
                // Request may already be aborted by navigation/cancel in edge races.
            }
        });

        await clickTab(page, 'Planner');
        await page.locator('#planner-run-btn').click();

        await expect(page.locator('#planner-loading')).toBeVisible();
        await expect(page.locator('#planner-stage')).toContainText('Waiting for provider response...', { timeout: 2000 });
        await expect(page.locator('#planner-loading')).toBeHidden({ timeout: 10000 });

        await expect(page.locator('#planner-out-pos')).toHaveValue('A foggy mountain valley', { timeout: 10000 });
        await expect(page.locator('#planner-out-neg')).toHaveValue('lowres, blurry', { timeout: 10000 });
        await expect(page.locator('#planner-run-btn')).toBeVisible();

        expect(pageErrors).toEqual([]);
    });

    test('Refiner cancel keeps UI stable and retry succeeds', async ({ page }) => {
        const pageErrors = [];
        page.on('pageerror', (e) => pageErrors.push(e.message));

        let callCount = 0;
        await page.route('**/assist/refiner**', async (route) => {
            if (!isRefinerRequest(route.request().url())) {
                await route.fallback();
                return;
            }

            callCount += 1;

            if (callCount === 1) {
                await new Promise((resolve) => setTimeout(resolve, 2500));
                try {
                    await route.fulfill({
                        status: 200,
                        contentType: 'application/json',
                        body: JSON.stringify({
                            refined_positive: 'stale response should be ignored',
                            refined_negative: 'stale',
                            rationale: 'stale',
                        }),
                    });
                } catch {
                    // Cancel path may abort before fulfill.
                }
                return;
            }

            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    refined_positive: 'clean cinematic portrait lighting',
                    refined_negative: 'overexposed, noisy',
                    rationale: 'Adjusted lighting and constrained noise artifacts.',
                }),
            });
        });

        await clickTab(page, 'Refiner');
        await page.locator('#refiner-orig-pos').fill('portrait, natural light');
        await page.locator('#refiner-issue').fill('too noisy and inconsistent lighting');

        const firstRefinerRequestSeen = page.waitForRequest((req) => isRefinerRequest(req.url()) && req.method() === 'POST');
        await page.locator('#refiner-run-btn').click();
        await expect(page.locator('#refiner-loading')).toBeVisible();
        await expect(page.locator('#refiner-stage')).toContainText('Waiting for provider response...', { timeout: 2000 });
        await firstRefinerRequestSeen;

        await page.locator('#refiner-cancel-btn').click();
        await expect(page.locator('#refiner-loading')).toBeHidden();
        await expect(page.locator('#refiner-run-btn')).toBeVisible();
        await expect(page.locator('.openclaw-toast')).toContainText('Request cancelled by user');

        await page.locator('#refiner-run-btn').click();

        await expect(page.locator('#refiner-new-pos')).toHaveValue('clean cinematic portrait lighting');
        await expect(page.locator('#refiner-new-neg')).toHaveValue('overexposed, noisy');
        await expect(page.locator('#refiner-rationale')).toContainText('Adjusted lighting');

        expect(pageErrors).toEqual([]);
    });

    test('Planner streaming path renders live preview and final result', async ({ page }) => {
        const pageErrors = [];
        page.on('pageerror', (e) => pageErrors.push(e.message));

        await page.route('**/assist/planner/stream**', async (route) => {
            if (!isPlannerStreamRequest(route.request().url())) {
                await route.fallback();
                return;
            }
            await route.fulfill({
                status: 200,
                contentType: 'text/event-stream',
                body:
                    'event: ready\n' +
                    'data: {"ok":true,"kind":"planner","mode":"sse"}\n\n' +
                    'event: stage\n' +
                    'data: {"phase":"dispatch","message":"Dispatching assist request"}\n\n' +
                    'event: delta\n' +
                    'data: {"text":"{\\"positive_prompt\\":\\"foggy ","preview_chars":28}\n\n' +
                    'event: delta\n' +
                    'data: {"text":"mountain\\"}","preview_chars":38}\n\n' +
                    'event: final\n' +
                    'data: {"ok":true,"kind":"planner","result":{"positive":"A foggy mountain valley","negative":"lowres, blurry","params":{"width":1024,"height":1024}},"streaming":{"preview_chars":38,"preview_truncated":false}}\n\n',
            });
        });

        // Force streaming capability in the shared API instance cache for this page.
        await page.evaluate(async () => {
            const mod = await import('/web/openclaw_api.js');
            mod.openclawApi._capabilitiesCache = {
                ok: true,
                data: { features: { assist_streaming: true } },
            };
            mod.openclawApi._capabilitiesCacheTs = Date.now();
        });

        await clickTab(page, 'Planner');
        await page.locator('#planner-run-btn').click();

        await expect
            .poll(async () => {
                const loadingVisible = await page.locator('#planner-loading').isVisible();
                if (loadingVisible) return true;
                const finalPositive = await page.locator('#planner-out-pos').inputValue();
                return finalPositive === 'A foggy mountain valley';
            })
            .toBeTruthy();
        await expect(page.locator('#planner-stream-preview')).toHaveValue(/foggy/, { timeout: 2000 });
        await expect(page.locator('#planner-stage')).toHaveText(
            /Dispatching assist request|Parsing and validating output\.\.\./,
        );
        await expect(page.locator('#planner-out-pos')).toHaveValue('A foggy mountain valley');
        await expect(page.locator('#planner-out-neg')).toHaveValue('lowres, blurry');

        expect(pageErrors).toEqual([]);
    });
});
