# Operator UX Acceleration Bundle Contracts

**Version**: 1.0.0 (Baseline)
**Date**: 260216
**Status**: DRAFT -> FROZEN

This document defines the interface contracts for the Operator UX Acceleration Bundle (F49, F51, F52, F50).

## 1. Banner Status (F49)

Used by `QueueMonitor` and other UI components to display transient status or recovery guidance.

### Schema (TypeScript)

```typescript
type BannerSeverity = 'info' | 'success' | 'warning' | 'error';

interface BannerStatus {
  /** Unique identifier for deduplication (e.g., 'backpressure_123') */
  id: string;

  /** Visual severity level */
  severity: BannerSeverity;

  /** Display message */
  message: string;

  /** Source of the banner (e.g., 'system', 'queue', 'connectivity') */
  source: string;

  /** Time-to-live in milliseconds. If missing, persists until dismissed or replaced. */
  ttl_ms?: number;

  /** Whether the user can manually dismiss the banner */
  dismissible?: boolean;

  /** Optional clickable action */
  action?: {
    label: string;
    /** Target type: 'url' | 'tab' | 'action' */
    type: string;
    /** Target value (URL, tab ID, or action name) */
    payload: string;
  };
}
```

### F49 Baseline (Current Behavior)

- **Monitoring**: Polls `/health` every 10s.
- **Triggers**: Checks `stats.observability.total_dropped > 0`.
- **Display**: Simple DOM injection of `.moltbot-banner`.
- **Limitations**: No connectivity state handling, no 'info'/'success' states, simplistic dedupe.

## 1.1 Notification Center (F66)

Persistent operator notifications are the durable counterpart to transient banners and toasts.

### Schema (TypeScript)

```typescript
interface NotificationEntry {
  id: string;
  severity: BannerSeverity;
  message: string;
  source: string;
  created_at: string;
  updated_at: string;
  count: number;
  acknowledged_at?: string | null;
  dismissed_at?: string | null;
  action?: {
    label: string;
    type: 'url' | 'tab' | 'action';
    payload: string;
  };
  metadata?: Record<string, unknown>;
}
```

### F66 Baseline

- Warning/error banners and selected operator toasts are mirrored into the in-app notification center.
- Entries are deduplicated by source-specific keys and persisted in local storage across reloads.
- `Dismiss` hides an entry from the active list without deleting the historical record from storage.
- `Acknowledge` clears unread state while keeping the entry visible.
- Sources with jump targets should attach a tab/action deep link so operators can navigate directly to the affected surface.

## 2. Context Actions (F51)

Defines quick actions available in the node context menu (via ComfyUI extension hooks).

### Schema (TypeScript)

```typescript
interface ContextAction {
  /** Unique action ID */
  id: string;

  /** Display label */
  label: string;

  /** Optional icon class or emoji */
  icon?: string;

  /** Primary target category */
  target: 'explorer' | 'jobs' | 'settings' | 'doctor' | 'url';

  /** Context data required for the action */
  payload?: {
    node_type?: string;
    node_id?: string;
    widget_name?: string;
    [key: string]: any;
  };

  /** Filter function to determine availability (frontend-side) */
  condition?: (node: any) => boolean;
}
```

## 3. Parameter Lab (F52)

Contracts for bounded parameter sweeps and experiment orchestration.

### Sweep Request Schema (JSON)

```json
{
  "workflow_json": "...",
  "params": [
    {
      "node_id": "10",
      "widget_name": "cfg",
      "values": [6.0, 7.0, 8.0]
    },
    {
      "node_id": "3",
      "widget_name": "seed",
      "strategy": "random",
      "count": 3
    }
  ],
  "max_runs": 20,
  "batch_size": 1
}
```

### Experiment Result Schema (JSON)

```json
{
  "experiment_id": "exp_abc123",
  "run_id": "run_xyz789",
  "timestamp": 1234567890,
  "params": {
    "10.cfg": 7.0,
    "3.seed": 42
  },
  "status": "completed",
  "outputs": {
    "9.image": ["filename_1.png"]
  },
  "error": null
}
```

## 4. Model Compare (F50)

Contracts for multi-model side-by-side comparison.

### Compare Request Schema (JSON)

```json
{
  "prompt": "User input text...",
  "candidates": [
    { "provider": "openai", "model": "gpt-4o" },
    { "provider": "anthropic", "model": "claude-3-5-sonnet" }
  ],
  "config": {
    "temperature": 0.7,
    "max_tokens": 1000
  },
  "timeout_ms": 30000
}
```

### Compare Result Schema (JSON)

```json
{
  "run_id": "cmp_def456",
  "candidates": [
    {
      "provider": "openai",
      "model": "gpt-4o",
      "output": "Result A...",
      "latency_ms": 1200,
      "cost_usd": 0.001,
      "error": null
    },
    {
      "provider": "anthropic",
      "model": "claude-3-5-sonnet",
      "output": "Result B...",
      "latency_ms": 1400,
      "cost_usd": 0.003,
      "error": null
    }
  ]
}
```
