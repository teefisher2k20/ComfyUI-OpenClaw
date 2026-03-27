"""
R144 shared JSON-serializable request/schema fixtures.

These fixtures are consumed by runtime validators and contract tests so route
behavior, schema limits, and public docs do not drift independently.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

SCHEMA_VERSION = "260127"

MAX_JOB_ID_LENGTH = 64
MAX_TEMPLATE_ID_LENGTH = 64
MAX_PROFILE_ID_LENGTH = 64
MAX_INPUT_STRING_LENGTH = 2048
MAX_BODY_SIZE = 65536
MAX_TRACE_ID_LENGTH = 64

WEBHOOK_JOB_REQUEST_CONTRACT: Dict[str, Any] = {
    "schema_id": "webhook_job_request_v1",
    "version": 1,
    "required_top_level": ["version", "template_id", "profile_id"],
    "allowed_top_level": [
        "version",
        "template_id",
        "profile_id",
        "inputs",
        "job_id",
        "trace_id",
        "callback",
    ],
    "allowed_input_keys": [
        "requirements",
        "goal",
        "seed",
        "positive_prompt",
        "negative_prompt",
    ],
    "limits": {
        "job_id_max_length": MAX_JOB_ID_LENGTH,
        "template_id_max_length": MAX_TEMPLATE_ID_LENGTH,
        "profile_id_max_length": MAX_PROFILE_ID_LENGTH,
        "input_string_max_length": MAX_INPUT_STRING_LENGTH,
        "body_max_bytes": MAX_BODY_SIZE,
        "trace_id_max_length": MAX_TRACE_ID_LENGTH,
    },
    "trace_id_pattern": "^[a-zA-Z0-9_-]+$",
}

MODEL_MANAGER_PROVENANCE_CONTRACT: Dict[str, Any] = {
    "schema_id": "model_manager_provenance_v1",
    "required_fields": ["publisher", "license", "source_url"],
    "optional_fields": ["note"],
    "note_max_chars": 500,
}

MODEL_MANAGER_IMPORT_CONTRACT: Dict[str, Any] = {
    "schema_id": "model_manager_import_v1",
    "required_fields": ["task_id"],
    "optional_fields": ["destination_subdir", "filename", "tags"],
    "tags": {
        "max_items": 24,
        "normalize": "strip_lower_dedupe",
    },
}

R144_ROUTE_FIXTURES: Dict[str, Any] = {
    "webhook": [
        {
            "method": "POST",
            "path": "/webhook",
            "legacy_path": "/moltbot/webhook",
            "auth": "Webhook Secret",
        },
        {
            "method": "POST",
            "path": "/webhook/submit",
            "legacy_path": "/moltbot/webhook/submit",
            "auth": "Webhook Secret",
        },
        {
            "method": "POST",
            "path": "/webhook/validate",
            "legacy_path": "/moltbot/webhook/validate",
            "auth": "Webhook Secret",
        },
    ],
    "model_manager": [
        {
            "method": "GET",
            "path": "/models/search",
            "legacy_path": "/moltbot/models/search",
            "auth": "Admin",
        },
        {
            "method": "POST",
            "path": "/models/downloads",
            "legacy_path": "/moltbot/models/downloads",
            "auth": "Admin",
        },
        {
            "method": "GET",
            "path": "/models/downloads",
            "legacy_path": "/moltbot/models/downloads",
            "auth": "Admin",
        },
        {
            "method": "GET",
            "path": "/models/downloads/{task_id}",
            "legacy_path": "/moltbot/models/downloads/{task_id}",
            "auth": "Admin",
        },
        {
            "method": "POST",
            "path": "/models/downloads/{task_id}/cancel",
            "legacy_path": "/moltbot/models/downloads/{task_id}/cancel",
            "auth": "Admin",
        },
        {
            "method": "POST",
            "path": "/models/import",
            "legacy_path": "/moltbot/models/import",
            "auth": "Admin",
        },
        {
            "method": "GET",
            "path": "/models/installations",
            "legacy_path": "/moltbot/models/installations",
            "auth": "Admin",
        },
    ],
}

R144_IO_BOUNDARY_MATRIX: Dict[str, Any] = {
    "webhook_validate": [
        {
            "case_id": "body_gt_limit_rejected",
            "limit_bytes": MAX_BODY_SIZE,
            "expected_status": 413,
            "expected_error": "payload_too_large",
        },
        {
            "case_id": "body_eq_limit_not_rejected_by_size_gate",
            "limit_bytes": MAX_BODY_SIZE,
            "expected_status": 400,
            "expected_error": "validation_error",
        },
        {
            "case_id": "malformed_json_rejected",
            "expected_status": 400,
            "expected_error": "invalid_json",
        },
    ],
    "model_manager_download_create": [
        {
            "case_id": "provenance_must_be_object",
            "expected_status": 400,
            "expected_error": "invalid_provenance",
        }
    ],
    "model_manager_import": [
        {
            "case_id": "invalid_filename_extension_rejected",
            "expected_status": 400,
            "expected_error": "invalid_filename",
        },
        {
            "case_id": "invalid_destination_rejected",
            "expected_status": 400,
            "expected_error": "invalid_destination",
        },
    ],
}


def get_serializable_contract_bundle() -> Dict[str, Any]:
    return deepcopy(
        {
            "webhook_job_request": WEBHOOK_JOB_REQUEST_CONTRACT,
            "model_manager_provenance": MODEL_MANAGER_PROVENANCE_CONTRACT,
            "model_manager_import": MODEL_MANAGER_IMPORT_CONTRACT,
            "route_fixtures": R144_ROUTE_FIXTURES,
            "io_boundary_matrix": R144_IO_BOUNDARY_MATRIX,
        }
    )
