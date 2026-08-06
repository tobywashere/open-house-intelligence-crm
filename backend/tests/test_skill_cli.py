import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = REPO_ROOT / "skills" / "crm-db-operations" / "cli.py"
SKILL_DIR = CLI.parent
SPEC = importlib.util.spec_from_file_location("skill_cli", CLI)
assert SPEC and SPEC.loader
sys.path.insert(0, str(SKILL_DIR))
try:
    skill_cli = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(skill_cli)
finally:
    sys.path.remove(str(SKILL_DIR))


def test_dispatch_calls_only_named_tool(monkeypatch):
    calls = []
    monkeypatch.setitem(
        skill_cli.OPERATIONS,
        "list_leads",
        lambda **kw: calls.append(kw) or [{"id": 1}],
    )

    result = skill_cli.dispatch("list_leads", {"sort": "priority"})

    assert result == [{"id": 1}]
    assert calls == [{"sort": "priority"}]


def test_dispatch_exposes_add_note(monkeypatch):
    assert "add_note" in skill_cli.OPERATIONS
    calls = []
    monkeypatch.setitem(
        skill_cli.OPERATIONS,
        "add_note",
        lambda **kw: calls.append(kw) or {"pending": True},
    )

    result = skill_cli.dispatch("add_note", {"lead_id": 4, "content": "Called back"})

    assert result == {"pending": True}
    assert calls == [{"lead_id": 4, "content": "Called back"}]


def test_dispatch_passes_probe_nonce_only_to_dashboard_insights(monkeypatch):
    calls = []
    monkeypatch.setitem(
        skill_cli.OPERATIONS,
        "generate_dashboard_insights",
        lambda **kw: calls.append(kw) or {"active_leads": 0},
    )

    result = skill_cli.dispatch(
        "generate_dashboard_insights",
        {"probe_nonce": "d" * 32},
    )

    assert result == {"active_leads": 0}
    assert calls == [{"probe_nonce": "d" * 32}]


def test_dispatch_rejects_unknown_operation():
    with pytest.raises(ValueError, match="unknown CRM operation"):
        skill_cli.dispatch("shell", {"command": "whoami"})


def test_dispatch_rejects_non_object_arguments():
    with pytest.raises(ValueError, match="JSON object"):
        skill_cli.dispatch("list_leads", [])


def test_cli_requires_json_object_arguments():
    result = subprocess.run(
        [sys.executable, str(CLI), "list_leads", "--args", "[]"],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "JSON object" in result.stderr


def test_cli_rejects_shell_as_an_operation():
    result = subprocess.run(
        [sys.executable, str(CLI), "shell", "--args", '{"command":"whoami"}'],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "invalid choice" in result.stderr
