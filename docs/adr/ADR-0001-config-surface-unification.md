# ADR-0001: Configuration Surface Unification

- Status: Accepted
- Date: 2026-03-07
- Owners: OpenClaw maintainers
- Related roadmap items: `R139`, phase-2 follow-up completed 2026-03-19

## Context

OpenClaw currently has distributed configuration logic across `config.py`, `services/runtime_config.py`, and selected call sites that still read env vars directly. This increases precedence drift risk and makes behavior harder to reason about.

The unification work required a phased, backward-compatible rollout rather than a single destructive rewrite.

## Decision

Adopt one authoritative layered model for runtime LLM config resolution, exposed through a unified resolver and then through a single effective-config facade consumed by compatibility APIs and downstream readers.

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
- Shared read seams make parity testing and future deprecation work narrower.

Trade-offs:
- Temporary coexistence of migrated and non-migrated call sites during phased rollout.
- Additional adapter code until follow-up phases complete.

## Rollout Plan

Phase 1 (`R139`):
- Introduce unified resolver + runtime override registry.
- Refactor `services/runtime_config.py` effective-read path to resolver-backed flow.
- Migrate core LLM call sites to stop duplicating env precedence.
- Add precedence/compatibility regression tests.

Phase 2 (completed 2026-03-19):
- Introduced `services/effective_config.py` as the supported effective-config read facade.
- Migrated remaining mixed-path readers touched by the phase onto the shared facade/compatibility shims.
- Added parity coverage so env/runtime/persisted/default precedence is asserted once at the shared seam instead of at each consumer.

Future follow-ups:
- Continue migrating any remaining direct env readers that still overlap with the runtime config contract.
- Remove obsolete adapter/shim code once migration reaches stable completion.

## Rejected Alternatives

1. Big-bang rewrite of all config readers:
   - Rejected due to blast radius and rollback difficulty.
2. Keep dual systems and patch ad hoc:
   - Rejected due to ongoing precedence drift and maintenance cost.
