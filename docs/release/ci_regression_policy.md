# CI Regression Policy

All pull requests must pass the repository SOP gate before merge.

## Mandatory Checks

| Check | Command | Purpose |
| --- | --- | --- |
| Secret detection | `pre-commit run detect-secrets --all-files` | Prevent secret leakage |
| Pre-commit hooks | `pre-commit run --all-files --show-diff-on-failure` | Enforce formatting and static checks |
| Coverage governance | `python scripts/verify_quality_governance.py` | Fail closed on coverage-policy, mutation-threshold, SOP-guidance, and survivor-allowlist drift |
| Backend unit tests | `python scripts/run_unittests.py --start-dir tests --pattern "test_*.py" --enforce-skip-policy tests/skip_policy.json` | Validate backend behavior and skip governance |
| Adversarial gate | `python scripts/run_adversarial_gate.py --profile auto --seed 42` | Enforce adaptive fuzz/mutation verification with smoke=>extended escalation on high-risk diffs |
| Frontend E2E | `npm test` | Validate UI and frontend/backend integration |

## Public MAE Hard-Guarantee Suites

These suites are explicit no-skip CI gates to prevent route classification drift:

- `tests.test_s60_mae_route_segmentation`
- `tests.test_s60_routes_startup_gate`
- `tests.security.test_endpoint_drift`

If any of these fail or are skipped, CI must fail.

## Change Management Rule

If a change intentionally modifies contract behavior:

1. Update affected tests and docs in the same PR.
2. Record the behavior change and migration impact in release notes.
3. Keep security-path tests on triple-assert semantics (status + machine code + audit signal).

## Governance Baseline

- Coverage governance is part of the standard gate, not an optional reporting step.
- `pyproject.toml` must keep:
  - `fail_under >= 35.0`
  - `show_missing = true`
  - `skip_covered = true`
- Mutation governance remains adaptive:
  - smoke profile threshold: `20.0%`
  - extended profile threshold: `80.0%`
- Known equivalent mutation survivors must stay explicitly allowlisted in `tests/mutation_survivor_allowlist.json`; drift is a gate failure, not a warning.
