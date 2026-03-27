# Frontend UX Walkthrough (ComfyUI-OpenClaw)

This document summarizes the current OpenClaw sidebar UI structure and how to verify it after changes.

## UI Structure

- Entry: `web/openclaw.js` registers the extension and sidebar tab.
- Shell: `web/openclaw_ui.js` now acts as the composition root for the sidebar shell and public singleton exports.
- Actions: `web/openclaw_actions.js` owns submit/cancel/retry wiring and guarded action routing for the shell.
- Queue monitor: `web/openclaw_queue_monitor.js` owns queue polling lifecycle and transient banner/status updates used by the shell.
- Event/task polling: admin-console and model/task views consume deterministic delta metadata (`effective_since_seq`, `next_since_seq`, reset/truncation hints) instead of assuming every refresh is a full snapshot.
- Notification center: `web/openclaw_notification_center.js` owns persistent in-app notification storage, dedupe, acknowledge, dismiss, and deep-link behavior.
- Banner runtime: `web/openclaw_banner_manager.js` owns transient banner state and shell-facing banner transitions.
- Tabs: `web/openclaw_tabs.js` manages tab registration, rendering, and remount safety.
- API: `web/openclaw_api.js` provides a normalized fetch wrapper and OpenClaw endpoints (legacy Moltbot endpoints still work).
- Host surface: `web/openclaw_host_surface.js` resolves the active frontend host surface and stamps explicit metadata so standalone frontend vs desktop-embedded behavior stays testable.
- Output refs: `web/openclaw_asset_refs.js` normalizes classic history refs and newer asset-backed output refs onto the same bounded `/view` preview contract.
- Styles: `web/openclaw.css` provides shared design tokens and component classes.
- Errors and compatibility helpers: `web/openclaw_utils.js` provides `showError()` / `clearError()` plus runtime legacy-class alias helpers used to keep canonical `openclaw-*` markup compatible with existing `moltbot-*` selectors.

Refactor note:
- `web/openclaw_ui.js` should stay focused on shell composition, shared singleton ownership, and exports.
- New shell behaviors should prefer the extracted action/queue modules unless they truly belong to top-level shell assembly.
- New tab markup should use canonical `openclaw-*` classes; legacy `moltbot-*` aliases are generated centrally at runtime instead of being duplicated in each template.
- Host-sensitive behaviors should consume the shared host-surface helper rather than inferring desktop vs standalone frontend from ad-hoc globals.
- Output preview flows should consume the shared asset-ref normalizer rather than assembling `/view` URLs independently in each tab.
- Explorer/preflight consumers should treat inventory diagnostics as snapshot-first and surface `snapshot_ts`, `scan_state`, `stale`, and `last_error` instead of blocking the UI on full rescans.

## Feature Gating (Capabilities)

- Backend exposes `GET /openclaw/capabilities` (legacy `/moltbot/capabilities` still works).
- Frontend fetches capabilities during setup and conditionally registers tabs:
  - `assist_planner` → Planner
  - `assist_refiner` → Refiner
  - `assist_streaming` → enable Planner/Refiner incremental live preview (fallback remains non-streaming)
  - `scheduler` → Variants (current gating)
  - `presets` → Library
  - `approvals` → Approvals

If capabilities are unavailable, the full tab set is registered to surface actionable errors (instead of “missing tabs”).
If `assist_streaming` is unavailable or the stream transport degrades, Planner/Refiner automatically fall back to the existing non-stream request path.

## Host-Surface Contract

- OpenClaw treats standalone `ComfyUI_frontend` and `desktop` as distinct frontend host surfaces.
- The sidebar stamps its resolved host surface at mount time so desktop bundle drift is explicit in diagnostics and regression tests.
- Graph/widget compatibility code should route through shared host helpers to keep nested-subgraph and promoted-widget behavior aligned with current upstream host semantics.

## Standalone Remote Admin Console

- Entry route: `GET /openclaw/admin` (legacy `GET /moltbot/admin` still works).
- HTML shell: `web/admin_console.html`
- Runtime app module: `web/admin_console_app.js`
- Runtime API module: `web/admin_console_api.js`
- Purpose: mobile-friendly standalone operations UI for non-sidebar workflows.
- Security model:
  - The page itself is a static shell and can render without authentication.
  - All write APIs still enforce backend admin policy (`X-OpenClaw-Admin-Token` and remote policy such as `OPENCLAW_ALLOW_REMOTE_ADMIN`).
- Runtime behaviors:
  - Dashboard summary + health/config snapshots
  - Jobs/Events polling + SSE stream connect/fallback
  - Delta-aware polling cursors for events and managed-task refresh loops
  - Approvals/Schedules/Triggers control actions
  - Config read/partial write and diagnostics access
  - Quick Actions (retry/refresh/drill) remain backend-authorized

## Remote Console Manual Checks

1. Open `http://<host>:<port>/openclaw/admin` from desktop and phone browsers.
2. Save an admin token via the console and verify protected actions succeed.
3. Clear token and verify write actions fail with explicit auth/policy errors.
4. Connect SSE, then trigger a run; verify event stream updates and fallback polling still works.
5. Confirm there is no blank/overflow breakage on narrow mobile widths.

## Quick Manual Checks

1. Open ComfyUI and confirm OpenClaw appears in the sidebar.
2. Switch between all visible tabs multiple times (and reopen the sidebar if possible) and ensure panes do not go blank.
3. Confirm the sidebar host-surface metadata resolves correctly for the current environment instead of defaulting silently.
4. Planner: click **Plan Generation** with minimal input and confirm either live preview/stage updates appear (when streaming is supported) or a readable fallback result/error appears.
5. Refiner: click **Refine Prompts** (with or without image) and confirm either live preview/stage updates appear (when streaming is supported) or a readable fallback result/error appears.
6. Jobs: verify output previews still resolve for both classic history refs and any asset-backed refs surfaced by callback/history payloads, and that repeated polls do not duplicate rows after reconnect/resume.
7. Explorer: verify preflight inventory can show `refreshing` / `stale` / `error` state without freezing the tab while deep scan work continues.
8. Library/Approvals: if backend endpoints are not enabled, confirm the UI shows a clear error state (no crashes).
9. If you simulate/fake a stream failure in dev tools, confirm Planner/Refiner retry through the classic non-stream path without duplicate submits or broken loading state.

## E2E (Playwright) Checks

- Run: `npm test`
- Tests live in: `tests/e2e/specs/`
- Harness: `tests/e2e/test-harness.html` (mocks ComfyUI core + basic OpenClaw API calls)
- Web helper/self-test harness: `web/tests/e2e-harness.html` (includes frontend helper and wrapper idempotence checks)
