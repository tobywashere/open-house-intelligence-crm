import importlib.util
import json
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "skills" / "crm-db-operations"
CONTRACT = SKILL_DIR / "contract.json"
CONTRACT_MODULE = SKILL_DIR / "contract.py"
EXPECTED_OPERATIONS = {
    "create_lead", "update_lead", "add_note", "close_lead",
    "find_duplicate_leads", "merge_leads", "get_lead_context", "list_leads",
    "list_lead_directory", "score_lead", "draft_followup", "check_availability",
    "list_appointments", "book_appointment", "schedule_followup",
    "find_neglected_leads", "generate_dashboard_insights", "post_briefing",
    "get_research_settings", "get_insights", "get_summary", "delete_lead",
    "search_knowledge",
}


def _load_cli():
    original_path = list(sys.path)
    previous_tools = sys.modules.pop("tools", None)
    previous_contract = sys.modules.pop("contract", None)
    try:
        sys.path.insert(0, str(SKILL_DIR))
        spec = importlib.util.spec_from_file_location("crm_operations_cli", SKILL_DIR / "cli.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = original_path
        sys.modules.pop("tools", None)
        sys.modules.pop("contract", None)
        if previous_tools is not None:
            sys.modules["tools"] = previous_tools
        if previous_contract is not None:
            sys.modules["contract"] = previous_contract


def _load_contract():
    original_path = list(sys.path)
    previous_contract = sys.modules.pop("contract", None)
    try:
        sys.path.insert(0, str(SKILL_DIR))
        spec = importlib.util.spec_from_file_location("crm_operation_contract", SKILL_DIR / "contract.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = original_path
        sys.modules.pop("contract", None)
        if previous_contract is not None:
            sys.modules["contract"] = previous_contract


def _load_contract_payload(tmp_path, payload):
    module_path = tmp_path / "contract.py"
    module_path.write_text(CONTRACT_MODULE.read_text(encoding="utf-8"), encoding="utf-8")
    module_path.with_name("contract.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    spec = importlib.util.spec_from_file_location("malformed_crm_operation_contract", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _contract_payload():
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_contract_is_strict_and_drives_dispatch():
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))

    assert contract["version"] == 1
    assert set(contract["operations"]) == EXPECTED_OPERATIONS
    cli = _load_cli()
    for name, entry in contract["operations"].items():
        assert entry["effect"] in {"read", "proposal", "narrative", "validated_write"}
        assert entry["arguments"]["type"] == "object"
        assert entry["arguments"]["additionalProperties"] is False
        assert callable(cli.OPERATIONS[name])


def test_create_lead_rejects_model_invented_arguments():
    with pytest.raises(ValueError, match="Unsupported argument: source_note"):
        _load_contract().validate_arguments(
            "create_lead", {"name": "Jordan", "source_note": "open house"}
        )

    with pytest.raises(ValueError, match="Unsupported argument: status"):
        _load_contract().validate_arguments(
            "create_lead", {"name": "Jordan", "status": "new"}
        )


def test_update_lead_requires_a_writable_field():
    with pytest.raises(ValueError, match="Invalid CRM arguments: update_lead"):
        _load_contract().validate_arguments("update_lead", {"lead_id": 7})


def test_update_lead_rejects_closed_status():
    with pytest.raises(ValueError, match="Invalid argument: status"):
        _load_contract().validate_arguments(
            "update_lead", {"lead_id": 7, "status": "closed"}
        )


def test_booking_arguments_survive_validation_unchanged():
    arguments = {
        "lead_id": 7,
        "start_ts": "2026-08-22T13:00:00",
        "end_ts": "2026-08-22T13:30:00",
        "location": "Kirkland office",
    }

    validated = _load_contract().validate_arguments("book_appointment", arguments)

    assert validated == arguments
    assert validated is not arguments


def test_merge_leads_requires_distinct_ids():
    with pytest.raises(ValueError, match="Invalid CRM arguments: merge_leads"):
        _load_contract().validate_arguments("merge_leads", {"primary_id": 7, "duplicate_id": 7})


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (
            lambda tools: tools.CRMError(0, "http://private.example/token=secret"),
            {"code": "backend_unavailable", "message": "CRM backend is unavailable", "retryable": True},
        ),
        (
            lambda tools: tools.CRMError(404, "lead 7 at /private/path"),
            {"code": "not_found", "message": "CRM record was not found", "retryable": False},
        ),
        (
            lambda tools: tools.CRMError(409, "private scheduling detail"),
            {
                "code": "schedule_conflict",
                "message": "Requested schedule conflicts with an existing appointment",
                "retryable": False,
            },
        ),
        (
            lambda _tools: ValueError("Unsupported argument: source_note"),
            {"code": "invalid_arguments", "message": "Unsupported argument: source_note", "retryable": False},
        ),
        (
            lambda _tools: RuntimeError("secret /private/path token=abc"),
            {"code": "operation_failed", "message": "CRM operation failed", "retryable": False},
        ),
    ],
)
def test_cli_safe_errors_are_structured_and_do_not_expose_private_details(exception, expected):
    cli = _load_cli()

    assert cli._safe_error(exception(cli.tools)) == expected


@pytest.mark.parametrize("operation", ["create_lead", "find_neglected_leads"])
def test_cli_marks_mutation_transport_failures_as_unknown_and_not_retryable(operation):
    cli = _load_cli()

    assert cli._safe_error(
        cli.tools.CRMError(0, "connection dropped after dispatch token=secret"),
        operation=operation,
    ) == {
        "code": "outcome_unknown",
        "message": "CRM mutation outcome is unknown",
        "retryable": False,
    }


def test_cli_marks_mutation_server_error_as_unknown_after_dispatch():
    cli = _load_cli()

    assert cli._safe_error(
        cli.tools.CRMError(500, "backend crashed after commit"),
        operation="create_lead",
    ) == {
        "code": "outcome_unknown",
        "message": "CRM mutation outcome is unknown",
        "retryable": False,
    }


def test_cli_keeps_rejected_mutation_http_response_deterministic():
    cli = _load_cli()

    assert cli._safe_error(
        cli.tools.CRMError(403, "private authorization detail"),
        operation="create_lead",
    ) == {
        "code": "operation_failed",
        "message": "CRM operation failed",
        "retryable": False,
    }


def test_cli_keeps_read_transport_failure_retryable():
    cli = _load_cli()

    assert cli._safe_error(
        cli.tools.CRMError(0, "could not connect"), operation="list_leads"
    ) == {
        "code": "backend_unavailable",
        "message": "CRM backend is unavailable",
        "retryable": True,
    }


def test_cli_main_writes_only_the_structured_safe_error(monkeypatch, capsys):
    cli = _load_cli()

    def fail(_operation, _arguments):
        raise cli.tools.CRMError(404, "private /path token=abc")

    monkeypatch.setattr(cli, "dispatch", fail)
    monkeypatch.setattr(sys, "argv", ["cli.py", "list_leads"])

    assert cli.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "ok": False,
        "error": {"code": "not_found", "message": "CRM record was not found", "retryable": False},
    }


def test_cli_main_preserves_unknown_mutation_outcome(monkeypatch, capsys):
    cli = _load_cli()

    def fail(_operation, _arguments):
        raise cli.tools.CRMError(0, "socket closed after request body was sent")

    monkeypatch.setattr(cli, "dispatch", fail)
    monkeypatch.setattr(sys, "argv", ["cli.py", "create_lead"])

    assert cli.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "ok": False,
        "error": {
            "code": "outcome_unknown",
            "message": "CRM mutation outcome is unknown",
            "retryable": False,
        },
    }


def test_contract_rejects_unsupported_schema_keyword_at_import(tmp_path):
    payload = _contract_payload()
    payload["operations"]["list_leads"]["arguments"]["format"] = "uri"

    with pytest.raises(RuntimeError, match="invalid CRM operation contract"):
        _load_contract_payload(tmp_path, payload)


def test_contract_rejects_non_boolean_nested_additional_properties_at_import(tmp_path):
    payload = _contract_payload()
    payload["operations"]["post_briefing"]["arguments"]["properties"]["payload"][
        "additionalProperties"
    ] = "false"

    with pytest.raises(RuntimeError, match="invalid CRM operation contract"):
        _load_contract_payload(tmp_path, payload)


@pytest.mark.parametrize(
    ("operation", "schema_update"),
    [
        ("list_leads", {"minimum": 0}),
        ("list_leads", {"minLength": 1}),
    ],
)
def test_contract_rejects_inapplicable_or_invalid_schema_constraints_at_import(
    tmp_path, operation, schema_update
):
    payload = _contract_payload()
    payload["operations"][operation]["arguments"].update(schema_update)

    with pytest.raises(RuntimeError, match="invalid CRM operation contract"):
        _load_contract_payload(tmp_path, payload)


def test_contract_rejects_anyof_on_non_object_schema_at_import(tmp_path):
    payload = _contract_payload()
    payload["operations"]["create_lead"]["arguments"]["properties"]["name"]["anyOf"] = [
        {"required": ["name"]}
    ]

    with pytest.raises(RuntimeError, match="invalid CRM operation contract"):
        _load_contract_payload(tmp_path, payload)


def test_contract_rejects_anyof_branch_constraints_ignored_at_runtime(tmp_path):
    payload = _contract_payload()
    payload["operations"]["create_lead"]["arguments"]["anyOf"][0]["const"] = {}

    with pytest.raises(RuntimeError, match="invalid CRM operation contract"):
        _load_contract_payload(tmp_path, payload)
