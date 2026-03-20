# R69 UI Framework Migration Feasibility Decision (2026-03-08)

## Scope

- Item: `R69` from roadmap (`UI framework migration feasibility + decision record`)
- Goal: determine whether migrating OpenClaw frontend from current modular vanilla JS architecture to a framework improves long-term maintainability enough to justify migration risk.
- Constraint from roadmap: no migration by default unless clear ROI; any POC must stay `DEV-only`.

## Current frontend baseline

- Entry/shell/tabs architecture:
  - `web/openclaw.js`
  - `web/openclaw_ui.js`
  - `web/openclaw_actions.js`
  - `web/openclaw_queue_monitor.js`
  - `web/openclaw_notification_center.js`
  - `web/openclaw_banner_manager.js`
  - `web/openclaw_tabs.js`
  - `web/admin_console_app.js`
  - `web/admin_console_api.js`
- Runtime model:
  - Loaded as a ComfyUI extension in the host page (no standalone SPA bootstrap).
  - Uses direct ES module loading from `custom_nodes` path.
  - Must preserve legacy compatibility paths (`openclaw` + `moltbot` API/class aliases).
- Verification baseline:
  - Frontend unit lane (`vitest`, `jsdom`)
  - Frontend E2E lane (`playwright`, harness-based)

## Decision criteria

Scored 1-5 (higher is better), weighted by current risk profile:

- `Maintainability` (weight 30%)
- `ComfyUI host integration risk` (weight 25%)
- `Security/control-surface regression risk` (weight 20%)
- `Migration effort and delivery impact` (weight 15%)
- `Test/CI transition complexity` (weight 10%)

## Option matrix

### Option A: Keep modular vanilla JS (status quo + guardrails)

- Maintainability: 3.5
- Integration risk: 5.0
- Security risk: 4.5
- Migration effort: 5.0
- Test transition complexity: 5.0
- Weighted result: **4.45**

### Option B: React + build pipeline (Vite/webpack)

- Maintainability: 4.5
- Integration risk: 2.5
- Security risk: 3.0
- Migration effort: 2.0
- Test transition complexity: 2.5
- Weighted result: **3.25**

### Option C: Vue/Svelte + build pipeline

- Maintainability: 4.0
- Integration risk: 2.5
- Security risk: 3.0
- Migration effort: 2.0
- Test transition complexity: 2.5
- Weighted result: **3.10**

## Key findings

1. OpenClaw frontend is host-coupled to ComfyUI extension lifecycle and remount behavior; framework migration introduces significant integration and lifecycle risk with limited near-term operator value.
2. Current architecture already has critical stability controls (`ErrorBoundary`, tab remount safety, capability-gated registration, compatibility aliases, Vitest + Playwright lanes).
   Recent decomposition work further reduced shell/admin/runtime hotspot size without introducing a framework dependency.
3. Most remaining roadmap priorities are functionality/security features (`F53/F54/F58/F59`), not frontend rendering abstraction gaps; migration now would consume high-risk bandwidth with weak ROI.

## Decision

- **No framework migration now** (`NO-GO` for immediate rewrite).
- Keep current modular vanilla architecture and continue targeted quality hardening.

## Approved follow-up guardrails

1. Keep canonical class/API ownership and avoid new legacy alias sprawl.
2. Continue expanding unit tests around shared UI helpers and tab state transitions before adding complexity.
3. If future migration is reconsidered, require a `DEV-only` POC with explicit rollback plan and CI parity evidence before roadmap promotion.

## Re-evaluation triggers

Re-open migration decision only if one of the following becomes true:

1. >30% of frontend PR churn is dominated by repetitive state/render boilerplate that cannot be reduced with current modular patterns.
2. A required roadmap feature demonstrably cannot be implemented safely without virtual-DOM/state tooling.
3. ComfyUI extension host contract changes in a way that materially favors framework runtime encapsulation.
