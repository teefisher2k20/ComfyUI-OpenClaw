import { test, expect } from "@playwright/test";
import { mockComfyUiCore, waitForOpenClawReady, clickTab } from "../utils/helpers.js";

function normalizeApiPath(pathname) {
  return pathname.startsWith("/api/") ? pathname.slice(4) : pathname;
}

test.describe("Model Manager Tab", () => {
  test("queues and imports a managed model task", async ({ page }) => {
    await mockComfyUiCore(page);

    const model = {
      id: "flux-test",
      name: "Flux Test Model",
      model_type: "checkpoint",
      source: "catalog",
      source_label: "Catalog",
      installed: false,
      download_url: "https://example.com/flux-test.safetensors",
      sha256: "a".repeat(64),
      provenance: {
        publisher: "OpenClaw",
        license: "OpenRAIL",
        source_url: "https://example.com/flux-test",
      },
      tags: ["flux", "test"],
    };

    let task = null;
    const installations = [];

    await page.route("**/models/search**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ok: true,
          items: [model],
          pagination: { limit: 100, offset: 0, total: 1 },
          filters: {},
        }),
      });
    });

    await page.route("**/models/downloads**", async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      const path = normalizeApiPath(url.pathname);
      const method = req.method();

      if ((path === "/openclaw/models/downloads" || path === "/moltbot/models/downloads") && method === "POST") {
        task = {
          task_id: "task-1",
          model_id: model.id,
          name: model.name,
          state: "completed",
          progress: 1,
          bytes_downloaded: 1024,
          total_bytes: 1024,
          imported: false,
        };
        await route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify({ ok: true, task }),
        });
        return;
      }

      if ((path === "/openclaw/models/downloads" || path === "/moltbot/models/downloads") && method === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
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

    await page.route("**/models/import", async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      const path = normalizeApiPath(url.pathname);
      if ((path === "/openclaw/models/import" || path === "/moltbot/models/import") && req.method() === "POST") {
        task = { ...task, imported: true };
        const installation = {
          id: "inst-1",
          model_id: model.id,
          name: model.name,
          model_type: model.model_type,
          installation_path: "checkpoints/flux-test.safetensors",
        };
        installations.push(installation);
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ ok: true, installation }),
        });
        return;
      }
      await route.fallback();
    });

    await page.route("**/models/installations**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ok: true,
          installations,
          pagination: { limit: 100, offset: 0, total: installations.length },
          filters: {},
        }),
      });
    });

    await page.goto("test-harness.html");
    await waitForOpenClawReady(page);
    await clickTab(page, "Model Manager");

    await expect(page.locator("#mm-search-results")).toContainText("Flux Test Model");
    await page.getByRole("button", { name: "Queue Download" }).first().click();
    await expect(page.locator("#mm-tasks")).toContainText("task-1");
    await page.getByRole("button", { name: "Import" }).first().click();
    await expect(page.locator("#mm-installations")).toContainText("checkpoints/flux-test.safetensors");
  });
});
