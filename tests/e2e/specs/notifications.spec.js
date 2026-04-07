import { test, expect } from "@playwright/test";
import { clickTab, mockComfyUiCore, waitForOpenClawReady } from "../utils/helpers.js";

const NOTIFICATION_STORAGE_KEY = "openclaw_notifications";

function normalizeApiPath(pathname) {
  const stripped = pathname.startsWith("/api/") ? pathname.slice(4) : pathname;
  return stripped.replace(/\/+$/, "");
}

function isPath(pathname, suffix) {
  const normalizedSuffix = String(suffix || "").replace(/\/+$/, "");
  const path = normalizeApiPath(pathname);
  return path === `/openclaw${normalizedSuffix}` || path === `/moltbot${normalizedSuffix}`;
}

function isApiCandidatePath(pathname, suffix) {
  const normalized = normalizeApiPath(pathname);
  return isPath(normalized, suffix);
}

function modelManagerRoutePattern(suffix) {
  const normalizedSuffix = String(suffix || "").replace(/\/+$/, "");
  return new RegExp(`/(?:api/)?(?:openclaw|moltbot)${normalizedSuffix.replace(/\//g, "\\/")}(?:\\?.*)?$`);
}

async function readNotificationEntries(page) {
  return page.evaluate((storageKey) => {
    try {
      const raw = window.localStorage.getItem(storageKey);
      return raw ? JSON.parse(raw) : [];
    } catch {
      return [];
    }
  }, NOTIFICATION_STORAGE_KEY);
}

async function getMatchingNotification(page, options = {}) {
  const { dedupeKey, message, includeDismissed = true } = options;
  const entries = await readNotificationEntries(page);
  return entries.find((entry) => {
    if (!entry || typeof entry !== "object") return false;
    if (dedupeKey && entry.dedupe_key !== dedupeKey) return false;
    if (message && entry.message !== message) return false;
    if (!includeDismissed && entry.dismissed_at) return false;
    return true;
  }) || null;
}

async function installNotificationStorageSeed(page, entries = null) {
  await page.addInitScript(([storageKey, seedEntries]) => {
    try {
      if (!window.name.includes("__openclaw_notifications_storage_reset__")) {
        window.localStorage.clear();
        window.sessionStorage.clear();
        window.name = `${window.name}__openclaw_notifications_storage_reset__`;
      }
      if (Array.isArray(seedEntries)) {
        window.localStorage.setItem(storageKey, JSON.stringify(seedEntries));
      }
    } catch {
      // ignore storage reset failures in restrictive browser contexts
    }
  }, [NOTIFICATION_STORAGE_KEY, entries]);
}

test.describe("Notification Center", () => {
  test("persists model-manager failures across reload until dismissed", async ({ page }) => {
    await installNotificationStorageSeed(page);

    await mockComfyUiCore(page);

    const unexpectedSearchResponses = [];
    page.on("response", async (response) => {
      try {
        const url = new URL(response.url());
        if (!isApiCandidatePath(url.pathname, "/models/search")) return;
        if (response.status() >= 400 && response.status() !== 503) {
          unexpectedSearchResponses.push(`${response.status()} ${url.pathname}${url.search}`);
        }
      } catch {
        // Ignore malformed URLs emitted by the browser tooling layer.
      }
    });

    await page.route(modelManagerRoutePattern("/models/search"), async (route) => {
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

    await page.route(modelManagerRoutePattern("/models/downloads"), async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: okCollection,
      });
    });

    await page.route(modelManagerRoutePattern("/models/installations"), async (route) => {
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
    await toggle.click();
    const targetNotification = page
      .locator("#openclaw-notification-panel .openclaw-notification-item")
      .filter({ hasText: "search: search_failed" })
      .first();
    await expect(targetNotification).toContainText("search: search_failed", { timeout: 15000 });
    await expect(targetNotification).toContainText("Open Model Manager");

    await page.reload();
    await waitForOpenClawReady(page);
    await page.locator("#openclaw-notification-toggle").click();
    const reloadedNotification = page
      .locator("#openclaw-notification-panel .openclaw-notification-item")
      .filter({ hasText: "search: search_failed" })
      .first();
    await expect(reloadedNotification).toContainText("search: search_failed", { timeout: 15000 });

    await reloadedNotification
      .getByRole("button", { name: "Dismiss notification: search: search_failed" })
      .click();

    await expect
      .poll(async () => {
        const entry = await getMatchingNotification(page, {
          dedupeKey: "model-manager:refresh",
          message: "search: search_failed",
        });
        return entry?.dismissed_at ? "dismissed" : "active";
      }, { timeout: 15000 })
      .toBe("dismissed");

    const visibleNotifications = page
      .locator("#openclaw-notification-panel .openclaw-notification-item")
      .filter({ hasText: "search: search_failed" });
    await expect(visibleNotifications).toHaveCount(0);

    await page.locator("#mm-refresh-btn").click();
    await expect(visibleNotifications).toHaveCount(0);

    await expect
      .poll(async () => {
        const entry = await getMatchingNotification(page, {
          dedupeKey: "model-manager:refresh",
          message: "search: search_failed",
        });
        return entry?.dismissed_at ? "dismissed" : "active";
      }, { timeout: 15000 })
      .toBe("dismissed");

    expect(unexpectedSearchResponses).toEqual([]);
  });

  test("renders notification payloads as escaped text instead of markup", async ({ page }) => {
    const maliciousMessage = '<img src=x onerror="boom">';
    await installNotificationStorageSeed(page, [
      {
        id: "ntf-escape",
        source: "<source>",
        severity: "warning",
        message: maliciousMessage,
        updated_at: "2026-03-20T00:00:00Z",
        count: 1,
        acknowledged_at: null,
        dismissed_at: null,
        action: null,
      },
    ]);

    await mockComfyUiCore(page);
    await page.goto("test-harness.html");
    await waitForOpenClawReady(page);
    await page.locator("#openclaw-notification-toggle").click();

    // CRITICAL: assert against the rendered DOM so test-only fixtures do not
    // regress back into HTML interpolation at the production notification sink.
    const messageNode = page.locator(".openclaw-notification-message").first();
    await expect(messageNode).toContainText(maliciousMessage);
    expect(await messageNode.innerHTML()).not.toContain("<img");
  });
});
