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
    await page.addInitScript(() => {
      try {
        if (!window.name.includes("__openclaw_notifications_storage_reset__")) {
          window.localStorage.clear();
          window.sessionStorage.clear();
          window.name = `${window.name}__openclaw_notifications_storage_reset__`;
        }
      } catch {
        // ignore storage reset failures in restrictive browser contexts
      }
    });

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

    const okJson = JSON.stringify({ ok: true, entries: [], config: {}, stats: {} });
    await page.route("**/events/stream**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: "",
      });
    });
    await page.route("**/logs/tail**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: okJson,
      });
    });
    await page.route("**/config**", async (route) => {
      const url = new URL(route.request().url());
      if (isPath(url.pathname, "/config")) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: okJson,
        });
        return;
      }
      await route.fallback();
    });
    await page.route("**/system_stats**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: okJson,
      });
    });
    await page.route("**/system_info**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: okJson,
      });
    });
    await page.route("**/version**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: okJson,
      });
    });

    await page.goto("test-harness.html");
    await waitForOpenClawReady(page);
    await clickTab(page, "Model Manager");
    await page.locator("#mm-refresh-btn").click();

    const toggle = page.locator("#openclaw-notification-toggle");
    await toggle.dispatchEvent("click");
    const targetNotification = page
      .locator("#openclaw-notification-panel .openclaw-notification-item")
      .filter({ hasText: "search: search_failed" })
      .first();
    await expect(targetNotification).toContainText("search: search_failed", { timeout: 15000 });
    await expect(targetNotification).toContainText("Open Model Manager");

    await page.reload();
    await waitForOpenClawReady(page);
    await page.locator("#openclaw-notification-toggle").dispatchEvent("click");
    const reloadedNotification = page
      .locator("#openclaw-notification-panel .openclaw-notification-item")
      .filter({ hasText: "search: search_failed" })
      .first();
    await expect(reloadedNotification).toContainText("search: search_failed", { timeout: 15000 });

    await reloadedNotification
      .getByRole("button", { name: "Dismiss notification: search: search_failed" })
      .click();
    await expect(page.locator("#openclaw-notification-panel")).not.toContainText("search: search_failed");
  });
});
