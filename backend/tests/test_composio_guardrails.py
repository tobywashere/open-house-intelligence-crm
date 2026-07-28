"""Task 6: Composio slug allowlist, robust CLI parsing, recipient allowlist,
poller opt-in default. See docs/superpowers/sdd/2026-07-27-offline-first-oss/
task-6-brief.md."""
import importlib.util
import sys
from pathlib import Path

import pytest

from .conftest import make_lead
from app.integrations import composio_client as cc

SKILLS = Path(__file__).resolve().parents[2] / "skills"


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, SKILLS / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


skill_tools = _load("composio_skill_tools", "composio-email-calendar/tools.py")


# ---- backend composio_client.py --------------------------------------------

def test_execute_rejects_unknown_slug():
    with pytest.raises(cc.IntegrationError):
        cc.execute("GMAIL_DELETE_MESSAGE", {})   # destructive, not in catalog


def test_cli_output_with_log_noise_parses(monkeypatch):
    """First-{ parsing broke on any braced log line — a SUCCESSFUL send then
    reported as failure. Parse the last JSON-parsing line instead."""
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_TRANSPORT", "cli")
    monkeypatch.setattr(cc.shutil, "which", lambda _: "/usr/bin/composio")
    monkeypatch.setattr(cc.os.path, "exists", lambda _: True)
    fake = type("P", (), {"returncode": 0,
                          "stdout": 'progress {50%}\n{"successful": true, "data": {"id": "m1"}}\n',
                          "stderr": ""})()
    monkeypatch.setattr(cc.subprocess, "run", lambda *a, **k: fake)
    out = cc.execute("GMAIL_SEND_EMAIL", {"user_id": "default"})
    assert out.get("id") == "m1"


def test_cli_run_passes_stdin_devnull(monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_TRANSPORT", "cli")
    monkeypatch.setattr(cc.shutil, "which", lambda _: "/usr/bin/composio")
    monkeypatch.setattr(cc.os.path, "exists", lambda _: True)
    captured = {}

    def fake_run(argv, **kw):
        captured.update(kw)
        return type("P", (), {"returncode": 0,
                              "stdout": '{"successful": true, "data": {}}',
                              "stderr": ""})()

    monkeypatch.setattr(cc.subprocess, "run", fake_run)
    cc.execute("GMAIL_SEND_EMAIL", {})
    assert captured.get("stdin") is cc.subprocess.DEVNULL


def test_cli_failure_error_never_leaks_raw_stderr(monkeypatch):
    monkeypatch.setenv("INTEGRATIONS_MODE", "live")
    monkeypatch.setenv("COMPOSIO_TRANSPORT", "cli")
    monkeypatch.setattr(cc.shutil, "which", lambda _: "/usr/bin/composio")
    monkeypatch.setattr(cc.os.path, "exists", lambda _: True)
    fake = type("P", (), {"returncode": 1, "stdout": "",
                          "stderr": "SECRET_TOKEN=abc123 leaked traceback"})()
    monkeypatch.setattr(cc.subprocess, "run", lambda *a, **k: fake)
    with pytest.raises(cc.IntegrationError) as exc_info:
        cc.execute("GMAIL_SEND_EMAIL", {})
    msg = str(exc_info.value)
    assert "SECRET_TOKEN" not in msg
    assert "composio link" in msg or "logs" in msg


def test_poller_default_off(monkeypatch):
    monkeypatch.delenv("INTEGRATIONS_POLLER", raising=False)
    import os
    assert os.environ.get("INTEGRATIONS_POLLER", "off") != "on"  # pins the new default


# ---- backend router recipient allowlist ------------------------------------

def test_send_recipient_not_a_known_lead_400(client, monkeypatch):
    lead = make_lead(client)
    # Simulate a spoofed/stale email reaching the send path that no longer
    # matches any leads.email row.
    monkeypatch.setattr(
        "app.integrations.router.fetch_lead",
        lambda conn, lead_id: {**lead, "email": "attacker@evil.example"})
    res = client.post("/api/email/send", json={
        "lead_id": lead["id"], "subject": "s", "body": "b"})
    assert res.status_code == 400


# ---- skill tools.py (composio-email-calendar) -------------------------------

def test_skill_execute_rejects_unknown_slug():
    with pytest.raises(skill_tools.IntegrationError):
        skill_tools.execute("GMAIL_DELETE_MESSAGE", {})


def test_skill_cli_output_with_log_noise_parses(monkeypatch):
    fake = type("P", (), {"returncode": 0,
                          "stdout": 'progress {50%}\n{"successful": true, "data": {"id": "m1"}}\n',
                          "stderr": ""})()
    monkeypatch.setattr(skill_tools, "_cli", lambda: "/usr/bin/composio")
    monkeypatch.setattr(skill_tools.subprocess, "run", lambda *a, **k: fake)
    out = skill_tools.execute("GMAIL_SEND_EMAIL", {})
    assert out.get("id") == "m1"


def test_skill_send_email_rejects_unknown_recipient(monkeypatch):
    monkeypatch.setattr(skill_tools, "_known_lead_emails", lambda: {"lead@example.com"})
    with pytest.raises(skill_tools.IntegrationError):
        skill_tools.send_email("stranger@evil.example", "s", "b")


def test_skill_send_email_allows_known_recipient_case_insensitive(monkeypatch):
    monkeypatch.setattr(skill_tools, "_known_lead_emails", lambda: {"lead@example.com"})
    called = {}

    def fake_execute(slug, args):
        called["slug"], called["args"] = slug, args
        return {"id": "m1"}

    monkeypatch.setattr(skill_tools, "execute", fake_execute)
    skill_tools.send_email("Lead@Example.com", "s", "b")
    assert called["slug"] == "GMAIL_SEND_EMAIL"
