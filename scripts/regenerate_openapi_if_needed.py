from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_sync_guard():
    module_path = _repo_root() / "scripts" / "check_openapi_sync.py"
    spec = importlib.util.spec_from_file_location("openapi_sync_guard_mod", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load check_openapi_sync.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def regenerate_openapi_if_needed(
    *,
    openapi_path: str | Path | None = None,
    contract_path: str | Path | None = None,
    guard_module=None,
    write_openapi_yaml=None,
) -> tuple[bool, str]:
    guard = guard_module or _load_sync_guard()
    root = _repo_root()
    openapi_file = (
        Path(openapi_path) if openapi_path else root / "docs" / "openapi.yaml"
    )
    contract_file = (
        Path(contract_path)
        if contract_path
        else root / "docs" / "release" / "api_contract.md"
    )
    ok, _ = guard.validate_openapi_sync(
        openapi_path=openapi_file,
        contract_path=contract_file,
    )
    if ok:
        return False, ""

    writer = write_openapi_yaml
    if writer is None:
        _ensure_repo_on_path()
        from services.openapi_generation import write_openapi_yaml as writer

    output = writer(openapi_file, contract_path=contract_file)
    return True, f"[OpenClaw] Regenerated generated spec: {output}"


def _ensure_repo_on_path() -> None:
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate docs/openapi.yaml when generator inputs changed."
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="Only regenerate when staged changes touch OpenAPI generator/spec sources.",
    )
    args = parser.parse_args(argv)

    guard = _load_sync_guard()
    if args.staged and not guard.should_validate_openapi(guard._get_staged_paths()):
        return 0

    changed, message = regenerate_openapi_if_needed()
    if changed and message:
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
