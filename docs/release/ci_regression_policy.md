# CI Regression Policy

All pull requests must pass the repository SOP gate before merge.

## Mandatory Checks

| Check | Command | Purpose |
| --- | --- | --- |
| Secret detection | `pre-commit run detect-secrets --all-files` | Prevent secret leakage |
| Pre-commit hooks | `pre-commit run --all-files --show-diff-on-failure` | Enforce formatting and static checks |
| Frontend dependency audit | `npm audit --production` | Fail on production dependency vulnerabilities in the shipped Node dependency surface |
| Backend dependency audit | `pip-audit -r requirements.txt` | Audit declared Python project dependencies without scanning unrelated CI runner/toolchain packages |
| GitHub CodeQL analysis | `.github/workflows/codeql.yml` | Run repository-native static security analysis for Python, JavaScript/TypeScript, and GitHub Actions on push, pull request, and weekly schedule |
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
- Dependency-audit governance is part of CI parity:
  - Node audit should continue to target production dependencies only.
  - Python audit must stay scoped to `requirements.txt`; env-wide bare `pip-audit` is out of contract because it can fail on tool-only transient packages that are not part of the repo dependency surface.
- GitHub Actions workflow files are part of the security boundary:
  - workflows using `GITHUB_TOKEN` must declare explicit least-privilege `permissions:` instead of relying on repository defaults
  - missing or broadened workflow token scope should be treated as CI-policy drift, not an acceptable implementation shortcut
  - CodeQL analysis must stay versioned in `.github/workflows/codeql.yml`; do not rely on UI-only default-setup drift for the repository baseline
  - CodeQL rollout remains visibility-first until the active backlog is burned down; treat new workflow findings as triage input, not an automatic merge blocker, unless the gating policy is explicitly tightened in roadmap/docs
- `pyproject.toml` must keep:
  - `fail_under >= 35.0`
  - `show_missing = true`
  - `skip_covered = true`
- Mutation governance remains adaptive:
  - smoke profile threshold: `20.0%`
  - extended profile threshold: `80.0%`
- Known equivalent mutation survivors must stay explicitly allowlisted in `tests/mutation_survivor_allowlist.json`; drift is a gate failure, not a warning.
