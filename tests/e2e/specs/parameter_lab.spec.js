import { test, expect } from '@playwright/test';
import { mockComfyUiCore, waitForOpenClawReady, clickTab } from '../utils/helpers.js';

test.describe('Parameter Lab - Dynamic Dimensions', () => {
    test.beforeEach(async ({ page }) => {
        // 1. Setup mock environment
        await mockComfyUiCore(page);
        await page.goto('test-harness.html');
        await waitForOpenClawReady(page);

        // 2. Inject mock graph with nodes and widgets
        await page.evaluate(() => {
            window.app.graph = {
                _nodes: [
                    {
                        id: 10,
                        type: "KSampler",
                        title: "My Sampler",
                        widgets: [
                            { name: "seed", type: "number", value: 1234, options: {} },
                            { name: "steps", type: "number", value: 20, options: { values: [20, 30, 40] } },
                            { name: "sampler_name", type: "combo", value: "euler", options: { values: ["euler", "ddim", "uni_pc"] } }
                        ]
                    },
                    {
                        id: 20,
                        type: "CheckpointLoader",
                        title: "Load Model",
                        widgets: [
                            { name: "ckpt_name", type: "combo", value: "base.ckpt", options: { values: ["base.ckpt", "v2.ckpt", "xl.ckpt"] } }
                        ]
                    }
                ],
                getNodeById(id) { return this._nodes.find(n => n.id === id); },
                serialize() { return { "test_graph": true }; }
            };
        });

        // 3. Open Parameter Lab
        await clickTab(page, 'Parameter Lab');
    });

    test('can select node, widget, and add values via dropdown', async ({ page }) => {
        // Add Dimension
        await page.click('#lab-add-dim');
        await expect(page.locator('.openclaw-lab-dim-row.dynamic')).toBeVisible();

        // Select Node (KSampler id=10)
        await page.selectOption('.dim-node-select', { value: '10' });

        // Select Widget (sampler_name)
        await page.selectOption('.dim-widget-select', { value: 'sampler_name' });

        // Verify candidates are populated
        const candidates = page.locator('.dim-candidate-select option');
        await expect(candidates).toHaveCount(4); // "Add option..." + 3 values

        // Select a candidate "ddim"
        await page.selectOption('.dim-candidate-select', { value: 'ddim' });

        // Verify chip added
        await expect(page.locator('.openclaw-chip >> text=ddim')).toBeVisible();

        // Select another "uni_pc"
        await page.selectOption('.dim-candidate-select', { value: 'uni_pc' });
        await expect(page.locator('.openclaw-chip >> text=uni_pc')).toBeVisible();

        // Verify remove chip
        await page.click('.openclaw-chip:has-text("ddim") .chip-rm');
        await expect(page.locator('.openclaw-chip >> text=ddim')).not.toBeVisible();
    });

    test('can add custom manual values', async ({ page }) => {
        await page.click('#lab-add-dim');

        // Select Node (KSampler id=10)
        await page.selectOption('.dim-node-select', { value: '10' });

        // Select Widget (seed)
        await page.selectOption('.dim-widget-select', { value: 'seed' });

        // Type custom value
        await page.fill('.dim-manual-input', '9999');
        await page.press('.dim-manual-input', 'Enter');

        // Verify chip
        await expect(page.locator('.openclaw-chip >> text=9999')).toBeVisible();
    });

    test('generates correct plan payload', async ({ page }) => {
        await page.evaluate(async () => {
            const mod = await import('/web/openclaw_api.js');
            window.__labSweepPayload = null;

            const originalFetch = mod.openclawApi.fetch.bind(mod.openclawApi);
            mod.openclawApi.fetch = async (url, options = {}) => {
                const normalizedPath = String(url || '').replace(/^\/moltbot/, '/openclaw');
                if (normalizedPath.endsWith('/lab/sweep')) {
                    window.__labSweepPayload = JSON.parse(options?.body || '{}');
                    return {
                        ok: true,
                        status: 200,
                        data: {
                            plan: {
                                runs: [],
                                experiment_id: 'exp123'
                            }
                        }
                    };
                }
                return originalFetch(url, options);
            };
        });

        // Configure dimension
        await page.click('#lab-add-dim');
        await page.selectOption('.dim-node-select', { value: '20' }); // CheckpointLoader
        await page.selectOption('.dim-widget-select', { value: 'ckpt_name' });

        // Add value "v2.ckpt" via candidate
        await page.selectOption('.dim-candidate-select', { value: 'v2.ckpt' });

        // Add value "xl.ckpt" via candidate
        await page.selectOption('.dim-candidate-select', { value: 'xl.ckpt' });

        // Click Generate
        await page.click('#lab-generate');
        await expect
            .poll(() => page.evaluate(() => (window.__labSweepPayload ? 'ready' : 'pending')))
            .toBe('ready');

        // Verify payload
        const payload = await page.evaluate(() => window.__labSweepPayload);
        expect(payload).toBeTruthy();
        expect(payload.params).toHaveLength(1);
        expect(payload.params[0]).toEqual({
            node_id: 20,
            widget_name: 'ckpt_name',
            values: ['v2.ckpt', 'xl.ckpt'],
            strategy: 'grid'
        });
    });
});
