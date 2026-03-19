"""
R147 bootstrap contract helpers.

Keeps route bootstrap imports declarative and validates symbol shape before
runtime registration starts mutating the ComfyUI server/router state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .import_fallback import import_attrs_dual


@dataclass(frozen=True)
class BootstrapSymbolSpec:
    key: str
    relative_module: str
    absolute_module: str
    attr: str
    kind: str


ROUTE_BOOTSTRAP_SPECS = (
    BootstrapSymbolSpec(
        key="register_approval_routes",
        relative_module="..api.approvals",
        absolute_module="api.approvals",
        attr="register_approval_routes",
        kind="callable",
    ),
    BootstrapSymbolSpec(
        key="BridgeHandlers",
        relative_module="..api.bridge",
        absolute_module="api.bridge",
        attr="BridgeHandlers",
        kind="class",
    ),
    BootstrapSymbolSpec(
        key="register_preset_routes",
        relative_module="..api.presets",
        absolute_module="api.presets",
        attr="register_preset_routes",
        kind="callable",
    ),
    BootstrapSymbolSpec(
        key="register_routes",
        relative_module="..api.routes",
        absolute_module="api.routes",
        attr="register_routes",
        kind="callable",
    ),
    BootstrapSymbolSpec(
        key="register_schedule_routes",
        relative_module="..api.schedules",
        absolute_module="api.schedules",
        attr="register_schedule_routes",
        kind="callable",
    ),
    BootstrapSymbolSpec(
        key="register_trigger_routes",
        relative_module="..api.triggers",
        absolute_module="api.triggers",
        attr="register_trigger_routes",
        kind="callable",
    ),
)


def _validate_symbol(spec: BootstrapSymbolSpec, value: Any) -> None:
    if spec.kind == "callable" and not callable(value):
        raise RuntimeError(
            f"Bootstrap contract violation: {spec.key} from {spec.absolute_module} "
            f"must be callable, got {type(value).__name__}"
        )
    if spec.kind == "class" and not isinstance(value, type):
        raise RuntimeError(
            f"Bootstrap contract violation: {spec.key} from {spec.absolute_module} "
            f"must be a class, got {type(value).__name__}"
        )


def load_route_bootstrap_contract(package_name: str | None) -> dict[str, Any]:
    contract: dict[str, Any] = {}
    for spec in ROUTE_BOOTSTRAP_SPECS:
        (value,) = import_attrs_dual(
            package_name,
            spec.relative_module,
            spec.absolute_module,
            (spec.attr,),
        )
        _validate_symbol(spec, value)
        contract[spec.key] = value
    return contract
