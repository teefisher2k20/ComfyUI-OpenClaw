# ADR-0001: Configuration Surface Unification (R139)

- Status: Accepted
- Date: 2026-03-07
- Owners: OpenClaw maintainers
- Related roadmap item: `R139`

## Context

OpenClaw currently has distributed configuration logic across `config.py`, `services/runtime_config.py`, and selected call sites that still read env vars directly. This increases precedence drift risk and makes behavior harder to reason about.

`R139` requires a phased, backward-compatible unification, not a single destructive rewrite.

## Decision

Adopt one authoritative layered model for runtime LLM config resolution, exposed through a unified resolver and consumed by `services/runtime_config.py` compatibility APIs.

Layer precedence (highest to lowest):
1. `env` (`OPENCLAW_*` first, `MOLTBOT_*` fallback)
2. `runtime_override` (in-memory only; process-local)
3. `persisted` (`OPENCLAW_STATE_DIR/config.json`)
4. `default`

Key points:
- Keep env-first semantics for operational safety and backward compatibility.
- Preserve legacy key support with explicit warning behavior.
- Keep runtime overrides non-persisted and source-attributed.

## Consequences

Positive:
- Deterministic precedence and source attribution.
- Reduced duplicated merge logic in primary runtime paths.
- Safer phased migration with compatibility facade intact.

Trade-offs:
- Temporary coexistence of migrated and non-migrated call sites during phased rollout.
- Additional adapter code until follow-up phases complete.

## Rollout Plan

Phase 1 (`R139`):
- Introduce unified resolver + runtime override registry.
- Refactor `services/runtime_config.py` effective-read path to resolver-backed flow.
- Migrate core LLM call sites to stop duplicating env precedence.
- Add precedence/compatibility regression tests.

Phase 2+ (future follow-ups):
- Continue migrating remaining direct env readers where they overlap with runtime config contract.
- Remove obsolete adapter/shim code once migration reaches stable completion.

## Rejected Alternatives

1. Big-bang rewrite of all config readers:
   - Rejected due to blast radius and rollback difficulty.
2. Keep dual systems and patch ad hoc:
   - Rejected due to ongoing precedence drift and maintenance cost.
