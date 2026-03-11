"""
R66 OpenAPI spec generation from the release API contract markdown.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class ContractRoute:
    method: str
    path: str
    description: str
    auth: str = "Unknown"
    legacy_path: Optional[str] = None
    section: str = ""


BASE_PATH_RE = re.compile(r"^\*\*Base Path\*\*:\s*`([^`]+)`")
SECTION_RE = re.compile(r"^###\s+(.+)$")
VERSION_RE = re.compile(r"^>\s+\*\*Version\*\*:\s*([0-9]+(?:\.[0-9]+)*)\s*$")

REASONING_REVEAL_ROUTE_DESCRIPTIONS = {
    ("GET", "/trace/{prompt_id}"): (
        "Trace payloads redact provider reasoning/thinking fields by default. "
        "Privileged debug reveal is local-only and opt-in."
    ),
    ("GET", "/events"): (
        "Event payloads redact provider reasoning/thinking fields by default. "
        "Privileged debug reveal is local-only and opt-in."
    ),
    ("GET", "/events/stream"): (
        "SSE event payloads redact provider reasoning/thinking fields by default. "
        "Privileged debug reveal is local-only and opt-in."
    ),
    ("POST", "/assist/planner"): (
        "Structured assist payloads preserve final answer fields while redacting "
        "provider reasoning/thinking fields by default. Privileged debug reveal "
        "is local-only and opt-in."
    ),
    ("POST", "/assist/refiner"): (
        "Structured assist payloads preserve final answer fields while redacting "
        "provider reasoning/thinking fields by default. Privileged debug reveal "
        "is local-only and opt-in."
    ),
    ("POST", "/assist/planner/stream"): (
        "Streaming assist final payloads redact provider reasoning/thinking "
        "fields by default. Privileged debug reveal is local-only and opt-in."
    ),
    ("POST", "/assist/refiner/stream"): (
        "Streaming assist final payloads redact provider reasoning/thinking "
        "fields by default. Privileged debug reveal is local-only and opt-in."
    ),
}

REASONING_REVEAL_PARAMETER_REFS = [
    {"$ref": "#/components/parameters/OpenClawReasoningRevealHeader"},
    {"$ref": "#/components/parameters/OpenClawReasoningRevealQuery"},
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _parse_contract_version(path: Optional[str | Path] = None) -> str:
    contract_path = (
        Path(path) if path else (_repo_root() / "docs" / "release" / "api_contract.md")
    )
    for line in contract_path.read_text(encoding="utf-8").splitlines():
        match = VERSION_RE.match(line.strip())
        if match:
            return match.group(1)
    return "1.0.0"


def _normalize_header_row(cells: List[str]) -> List[str]:
    return [c.strip().lower() for c in cells]


def _split_md_row(line: str) -> List[str]:
    text = line.strip().strip("|")
    return [c.strip() for c in text.split("|")]


def _is_separator_row(line: str) -> bool:
    cells = _split_md_row(line)
    return bool(cells) and all(set(c.replace(":", "").strip()) <= {"-"} for c in cells)


def _join_base(base_path: str, path: str) -> str:
    if (
        path.startswith("/openclaw/")
        or path.startswith("/moltbot/")
        or path.startswith("/bridge/")
    ):
        return path
    if not path.startswith("/"):
        path = "/" + path
    return (base_path.rstrip("/") + "/" + path.lstrip("/")).rstrip()


def parse_api_contract_markdown(
    path: Optional[str | Path] = None,
) -> List[ContractRoute]:
    path = (
        Path(path) if path else (_repo_root() / "docs" / "release" / "api_contract.md")
    )
    lines = path.read_text(encoding="utf-8").splitlines()

    routes: List[ContractRoute] = []
    current_section = ""
    current_base_path = ""
    idx = 0

    while idx < len(lines):
        line = lines[idx]
        m_section = SECTION_RE.match(line)
        if m_section:
            current_section = m_section.group(1).strip()
        m_base = BASE_PATH_RE.match(line.strip())
        if m_base:
            current_base_path = m_base.group(1).strip()

        if line.strip().startswith("|"):
            header_cells = _split_md_row(line)
            if (
                idx + 1 < len(lines)
                and lines[idx + 1].strip().startswith("|")
                and _is_separator_row(lines[idx + 1])
            ):
                headers = _normalize_header_row(header_cells)
                idx += 2
                while idx < len(lines) and lines[idx].strip().startswith("|"):
                    row = lines[idx]
                    if _is_separator_row(row):
                        idx += 1
                        continue
                    values = _split_md_row(row)
                    if len(values) != len(headers):
                        idx += 1
                        continue
                    row_map = {headers[i]: values[i] for i in range(len(headers))}
                    method = row_map.get("method", "").strip("` ").upper()
                    raw_path = row_map.get("path", "").strip("` ")
                    if not method or not raw_path:
                        idx += 1
                        continue
                    if "..." in raw_path:
                        idx += 1
                        continue
                    if current_base_path and current_section in {
                        "1.5 Schedules & Approvals",
                        "1.6 Bridge (Sidecar)",
                    }:
                        full_path = _join_base(current_base_path, raw_path)
                    else:
                        full_path = (
                            raw_path
                            if raw_path.startswith("/")
                            else _join_base(current_base_path or "/openclaw/", raw_path)
                        )
                    legacy_path = row_map.get("legacy path")
                    if legacy_path:
                        legacy_path = legacy_path.strip("` ")
                    auth = row_map.get("auth", "Unknown").strip()
                    desc = row_map.get("description", "").strip()
                    routes.append(
                        ContractRoute(
                            method=method,
                            path=full_path,
                            description=desc,
                            auth=auth or "Unknown",
                            legacy_path=legacy_path or None,
                            section=current_section,
                        )
                    )
                    idx += 1
                continue
        idx += 1
    return routes


def _operation_id(method: str, path: str) -> str:
    tokens = [t for t in path.strip("/").split("/") if t]
    cleaned = []
    for token in tokens:
        token = token.replace("{", "").replace("}", "")
        token = re.sub(r"[^A-Za-z0-9_]+", "_", token)
        cleaned.append(token)
    return f"{method.lower()}_" + "_".join(cleaned or ["root"])


def _security_from_auth(auth_label: str) -> tuple[list[dict[str, list]], Optional[str]]:
    auth = (auth_label or "").lower()
    if auth in {"none", ""}:
        return [], "none"
    if "observability" in auth:
        return [{"OpenClawObservabilityToken": []}], "observability"
    if "webhook" in auth:
        return [{"OpenClawWebhookAuth": []}], "webhook"
    if "bridge auth" in auth:
        return [{"OpenClawBridgeAuth": []}], "bridge"
    if "admin" in auth:
        return [{"OpenClawAdminToken": []}], "admin"
    return [], None


def build_openapi_document(
    routes: Iterable[ContractRoute], *, info_version: str = "1.0.0"
) -> Dict[str, Any]:
    paths: Dict[str, Dict[str, Any]] = {}
    for route in routes:
        method_key = route.method.lower()
        security, normalized_auth = _security_from_auth(route.auth)
        operation: Dict[str, Any] = {
            "operationId": _operation_id(route.method, route.path),
            "summary": route.description or route.path,
            "responses": {"200": {"description": "OK"}},
            "x-openclaw-auth": route.auth,
            "x-openclaw-section": route.section,
        }
        reasoning_reveal_description = REASONING_REVEAL_ROUTE_DESCRIPTIONS.get(
            (route.method, route.path)
        )
        if reasoning_reveal_description:
            operation["description"] = reasoning_reveal_description
        if route.legacy_path:
            operation["x-openclaw-legacy-path"] = route.legacy_path
        if normalized_auth:
            operation["x-openclaw-auth-tier"] = normalized_auth
        if security:
            operation["security"] = security
        if (
            route.path.endswith("/stream")
            or route.path.endswith("/planner/stream")
            or route.path.endswith("/refiner/stream")
        ):
            operation["x-openclaw-streaming"] = True
        if "{" in route.path and "}" in route.path:
            params = []
            for p in re.findall(r"{([^}]+)}", route.path):
                params.append(
                    {
                        "name": p,
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                )
            operation["parameters"] = params
        # IMPORTANT: keep generator-owned reveal params here; hand-editing docs/openapi.yaml
        # causes drift against R66 parity tests and breaks pre-push verification.
        if reasoning_reveal_description:
            operation.setdefault("parameters", []).extend(
                REASONING_REVEAL_PARAMETER_REFS
            )
        paths.setdefault(route.path, {})[method_key] = operation

    return {
        "openapi": "3.0.3",
        "info": {
            "title": "ComfyUI-OpenClaw API",
            "version": info_version,
            "description": "Generated from docs/release/api_contract.md (R66 baseline).",
        },
        "servers": [
            {"url": "/openclaw", "description": "Direct OpenClaw prefix"},
            {"url": "/api/openclaw", "description": "ComfyUI /api shim"},
        ],
        "paths": paths,
        "components": {
            "parameters": {
                "OpenClawReasoningRevealHeader": {
                    "name": "X-OpenClaw-Debug-Reveal-Reasoning",
                    "in": "header",
                    "required": False,
                    "description": (
                        "Debug-only opt-in for privileged reasoning reveal. "
                        "Effective only when server-side enablement, admin "
                        "authorization, loopback source, and permissive local "
                        "posture all pass."
                    ),
                    "schema": {"type": "string", "enum": ["1"]},
                },
                "OpenClawReasoningRevealQuery": {
                    "name": "debug_reasoning",
                    "in": "query",
                    "required": False,
                    "description": (
                        "Debug-only query opt-in for privileged reasoning reveal. "
                        "Effective only when server-side enablement, admin "
                        "authorization, loopback source, and permissive local "
                        "posture all pass."
                    ),
                    "schema": {"type": "string", "enum": ["1"]},
                },
            },
            "securitySchemes": {
                "OpenClawAdminToken": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-OpenClaw-Admin-Token",
                },
                "OpenClawObservabilityToken": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-OpenClaw-Obs-Token",
                },
                "OpenClawWebhookAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "Webhook bearer or HMAC-based auth (see release API contract).",
                },
                "OpenClawBridgeAuth": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-OpenClaw-Bridge-Token",
                },
            }
        },
    }


def _yaml_scalar(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def to_yaml(data: Any, *, indent: int = 0) -> str:
    space = " " * indent
    if isinstance(data, dict):
        lines: List[str] = []
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{space}{key}:")
                lines.append(to_yaml(value, indent=indent + 2))
            else:
                lines.append(f"{space}{key}: {_yaml_scalar(value)}")
        return "\n".join(lines) if lines else f"{space}{{}}"
    if isinstance(data, list):
        lines = []
        for item in data:
            if isinstance(item, (dict, list)):
                block = to_yaml(item, indent=indent + 2).splitlines()
                if not block:
                    lines.append(f"{space}-")
                else:
                    lines.append(f"{space}- {block[0].lstrip()}")
                    lines.extend(block[1:])
            else:
                lines.append(f"{space}- {_yaml_scalar(item)}")
        return "\n".join(lines) if lines else f"{space}[]"
    return f"{space}{_yaml_scalar(data)}"


def generate_openapi_yaml(path: Optional[str | Path] = None) -> str:
    routes = parse_api_contract_markdown(path)
    doc = build_openapi_document(routes, info_version=_parse_contract_version(path))
    return to_yaml(doc) + "\n"


def write_openapi_yaml(
    output_path: str | Path, *, contract_path: Optional[str | Path] = None
) -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(generate_openapi_yaml(contract_path), encoding="utf-8")
    return out
