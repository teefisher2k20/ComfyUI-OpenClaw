import { test, expect } from "@playwright/test";
import { clickTab, mockComfyUiCore, waitForOpenClawReady } from "../utils/helpers.js";

function normalizeApiPath(pathname) {
  const stripped = pathname.startsWith("/api/") ? pathname.slice(4) : pathname;
  return stripped.replace(/\/+$/, "");
}

function isPath(pathname, suffix) {
  const normalizedSuffix = String(suffix || "").replace(/\/+$/, "");
  const path = normalizeApiPath(pathname);
  return path === `/openclaw${normalizedSuffix}` || path === `/moltbot${normalizedSuffix}`;
}

test.describe("Notification Center", () => {
  test("persists model-manager failures across reload until dismissed", async ({ page }) => {
    await mockComfyUiCore(page);

    await page.route("**/models/search**", async (route) => {
      const url = new URL(route.request().url());
      if (!isPath(url.pathname, "/models/search")) {
        await route.fallback();
        return;
      }
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ ok: false, error: "search_failed" }),
      });
    });

    const okCollection = JSON.stringify({
      ok: true,
      tasks: [],
      installations: [],
      pagination: { limit: 100, offset: 0, total: 0 },
      filters: {},
    });

    await page.route("**/models/downloads**", async (route) => {
      const url = new URL(route.request().url());
      if (!isPath(url.pathname, "/models/downloads")) {
        await route.fallback();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: okCollection,
      });
    });

    await page.route("**/models/installations**", async (route) => {
      const url = new URL(route.request().url());
      if (!isPath(url.pathname, "/models/installations")) {
        await route.fallback();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: okCollection,
      });
    });

    await page.goto("test-harness.html");
    await waitForOpenClawReady(page);
    await clickTab(page, "Model Manager");

    const toggle = page.locator("#openclaw-notification-toggle");
    await expect(toggle.locator(".openclaw-notification-badge")).toHaveText("1");

    await toggle.click();
    await expect(page.locator("#openclaw-notification-panel")).toContainText("search: search_failed");
    await expect(page.locator("#openclaw-notification-panel")).toContainText("Open Model Manager");

    await page.reload();
    await waitForOpenClawReady(page);
    await page.locator("#openclaw-notification-toggle").click();
    await expect(page.locator("#openclaw-notification-panel")).toContainText("search: search_failed");

    await page.getByRole("button", { name: "Dismiss" }).first().click();
    await expect(page.locator("#openclaw-notification-panel")).not.toContainText("search: search_failed");
  });
});
