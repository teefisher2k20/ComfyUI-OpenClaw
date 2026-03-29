import { test, expect } from '@playwright/test';
import { mockComfyUiCore, waitForOpenClawReady, clickTab } from '../utils/helpers.js';

const TEST_OUTPUT_PNG = Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIW2NkYGD4DwABBAEAe7YQDgAAAABJRU5ErkJggg==',
    'base64'
);

test.describe('R107 Live Backend Parity', () => {
    test.beforeEach(async ({ page }) => {
        await mockComfyUiCore(page);

        // Mock common endpoints
        await page.route('**/openclaw/config', async route => {
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, config: {}, apply: {} }) });
        });
        await page.route('**/openclaw/logs/tail*', async route => {
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, content: [] }) });
        });
        await page.route('**/openclaw/health', async route => {
            await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, pack: { version: 'test' } }) });
        });

        await page.goto('test-harness.html');
        await waitForOpenClawReady(page);
    });

    test('Planner (Submit) critical path - Success', async ({ page }) => {
        // Mock Planner API
        await page.route('**/openclaw/assist/planner', async route => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    positive: "A beautiful landscape",
                    negative: "ugly, blurry",
                    params: { width: 1024, height: 1024 }
                })
            });
        });

        await clickTab(page, 'Planner');

        // Check initial state
        await expect(page.locator('#planner-run-btn')).toBeVisible();

        // Run Plan
        await page.locator('#planner-run-btn').click();

        // Verify result population
        await expect(page.locator('#planner-out-pos')).toHaveValue("A beautiful landscape");
        await expect(page.locator('#planner-out-neg')).toHaveValue("ugly, blurry");
    });

    test('Job Monitor (Status/Results) critical path', async ({ page }) => {
        const jobId = "job-123-abc";

        // Mock History (Polling)
        await page.route(`**/history/${jobId}`, async route => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    [jobId]: {
                        status: { status_str: "success", completed: true },
                        outputs: {
                            "9": {
                                images: [{ filename: "test_img.png", type: "output" }]
                            }
                        }
                    }
                })
            });
        });

        // Mock Trace
        await page.route(`**/openclaw/trace/${jobId}`, async route => {
            await route.fulfill({
                status: 200,
                contentType: 'application/json',
                body: JSON.stringify({
                    trace: {
                        trace_id: "trace-xyz",
                        events: [{ event: "queued", ts: 1700000000 }, { event: "completed", ts: 1700000010 }]
                    }
                })
            });
        });
        await page.route('**/view**', async route => {
            const request = route.request();
            const url = new URL(request.url());
            if (
                request.method() !== 'GET'
                || url.searchParams.get('filename') !== 'test_img.png'
                || url.searchParams.get('type') !== 'output'
            ) {
                await route.fallback();
                return;
            }

            await route.fulfill({
                status: 200,
                contentType: 'image/png',
                body: TEST_OUTPUT_PNG,
            });
        });

        await clickTab(page, 'Jobs');

        // Add Job
        await page.locator('input[placeholder="prompt_id"]').fill(jobId);
        await page.getByText('Add').click();

        // Assert Job Row Appears
        const jobRow = page.locator('.openclaw-job-row').first();
        await expect(jobRow).toBeVisible();
        await expect(jobRow).toContainText(jobId.substring(0, 16));

        // Wait for status to become completed (polling)
        await expect(page.locator('.openclaw-kv-val.ok')).toHaveText('completed', { timeout: 10000 });

        // Assert Image Output
        await expect(page.locator('img[src*="test_img.png"]')).toBeVisible();
    });

    test('Degraded Adapter / Fail Handling', async ({ page }) => {
        // Mock Planner Failure (503 Service Unavailable)
        await page.route('**/openclaw/assist/planner', async route => {
            await route.fulfill({
                status: 503,
                contentType: 'application/json',
                body: JSON.stringify({
                    ok: false,
                    error: "service_unavailable",
                    detail: "Backend overload"
                })
            });
        });

        await clickTab(page, 'Planner');
        await page.locator('#planner-run-btn').click();

        // Assume error handling shows a text in the container or valid error box
        // Checking openclaw_utils.js showError implementation would be precise,
        // but typically it creates an element with error text.
        // Let's look for the error message text.
        await expect(page.getByText('service_unavailable')).toBeVisible();
    });
});
