"""Operator-facing readiness checker behavior."""

import subprocess
import sys
from pathlib import Path

import scripts.doctor as doctor


REPO = Path(__file__).resolve().parents[2]


def test_doctor_help_lists_live_agent_check():
    # sys.executable, not a hardcoded .venv/bin/python: CI installs deps into
    # the runner's system Python directly (no venv), so a hardcoded venv path
    # 404s there even though the same interpreter running this test is
    # perfectly able to run the script.
    result = subprocess.run(
        [sys.executable, "scripts/doctor.py", "--help"],
        cwd=REPO,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--live-agent" in result.stdout
    assert "--live-crm" in result.stdout
    assert "--live-timeout" in result.stdout
    assert "--base-url" in result.stdout


def test_doctor_reports_chat_and_crm_capability_separately(monkeypatch):
    calls = []

    def fake_request(url, method="GET", timeout=5):
        calls.append((url, method, timeout))
        if url.endswith("/health"):
            return {
                "agent_status": {
                    "status": "endpoint_enabled",
                    "gateway_reachable": True,
                    "endpoint_enabled": True,
                    "last_chat_ok": None,
                    "crm_verified": False,
                    "agent_id": "openhouse-crm",
                    "fallbacks": {},
                    "detail": None,
                }
            }
        if url.endswith("/health/agent-check"):
            return {"status": "chat_verified"}
        if url.endswith("/health/crm-check"):
            return {"status": "crm_verified"}
        raise AssertionError(url)

    monkeypatch.setattr(doctor, "_request_json", fake_request)

    checks = doctor.run_checks(
        "http://127.0.0.1:8080/api",
        live_agent=True,
        live_crm=True,
        live_timeout=37,
    )

    by_name = {check.name: check for check in checks}
    assert by_name["Live chat completion"].level == "WARN"
    assert by_name["Live chat completion"].detail == "chat_verified"
    assert by_name["CRM capability"].level == "PASS"
    assert by_name["CRM capability"].detail == "crm_verified"
    assert calls == [
        ("http://127.0.0.1:8080/api/health", "GET", 5),
        ("http://127.0.0.1:8080/api/health/agent-check", "POST", 37),
        ("http://127.0.0.1:8080/api/health/crm-check", "POST", 37),
    ]


def test_doctor_live_timeout_defaults_above_backend_agent_timeout(monkeypatch):
    calls = []
    monkeypatch.setenv("AGENT_TIMEOUT_SECONDS", "120")

    def fake_request(url, method="GET", timeout=5):
        calls.append((url, timeout))
        if url.endswith("/health"):
            return {"agent_status": {"status": "endpoint_enabled"}}
        return {"status": "chat_verified"}

    monkeypatch.setattr(doctor, "_request_json", fake_request)

    doctor.run_checks(
        "http://127.0.0.1:8080/api",
        live_agent=True,
        live_crm=True,
    )

    live_timeouts = [timeout for url, timeout in calls if not url.endswith("/health")]
    assert live_timeouts == [125, 125]
