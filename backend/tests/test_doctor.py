"""Operator-facing readiness checker behavior."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

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
    assert "--json" in result.stdout
    assert "directly invoke one audited, read-only CRM call" in result.stdout


@pytest.mark.parametrize(
    ("system", "machine", "release", "expected"),
    [
        ("Darwin", "arm64", "25.0.0", "macOS arm64"),
        ("Linux", "x86_64", "6.8.0-generic", "Linux x86_64"),
        (
            "Linux",
            "x86_64",
            "6.6.87.2-microsoft-standard-WSL2",
            "Windows WSL2 x86_64",
        ),
        (
            "Windows",
            "AMD64",
            "11",
            "native Windows AMD64 (unsupported; use WSL2)",
        ),
    ],
)
def test_platform_label(system, machine, release, expected):
    assert doctor._platform_label(system, machine, release) == expected


def test_linux_memory_parser_returns_bytes():
    assert doctor._linux_memory_bytes("MemTotal:       16777216 kB\n") == 16 * 1024**3


@pytest.mark.parametrize(
    ("total", "level"),
    [
        (16 * 1024**3, "PASS"),
        (15 * 1024**3, "WARN"),
        (None, "WARN"),
    ],
)
def test_memory_check_uses_16_gib_baseline(total, level):
    assert doctor._memory_check(total).level == level


def test_json_report_is_structured_and_omits_local_paths():
    checks = [
        doctor.Check("PASS", "Platform", "Windows WSL2 x86_64"),
        doctor.Check("PASS", "OpenClaw CLI", "2026.8.1-beta.2"),
        doctor.Check("PASS", "CRM capability", "crm_verified"),
    ]

    rendered = doctor.render_report(checks, as_json=True)

    assert json.loads(rendered) == {
        "schema_version": 1,
        "checks": [
            {
                "level": "PASS",
                "name": "Platform",
                "detail": "Windows WSL2 x86_64",
            },
            {
                "level": "PASS",
                "name": "OpenClaw CLI",
                "detail": "2026.8.1-beta.2",
            },
            {
                "level": "PASS",
                "name": "CRM capability",
                "detail": "crm_verified",
            },
        ],
    }
    assert str(Path.home()) not in rendered


def test_system_checks_report_versions_without_executable_paths(monkeypatch):
    versions = {
        ("git", "rev-parse", "--short", "HEAD"): "abc1234",
        ("node", "--version"): "v24.1.0",
        ("npm", "--version"): "11.0.0",
        ("openclaw", "--version"): "OpenClaw 2026.8.1-beta.2",
        ("ollama", "--version"): "ollama version 0.32.15",
    }
    monkeypatch.setattr(doctor.platform, "system", lambda: "Linux")
    monkeypatch.setattr(doctor.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        doctor.platform,
        "release",
        lambda: "6.6.87.2-microsoft-standard-WSL2",
    )
    monkeypatch.setattr(doctor, "_total_memory_bytes", lambda: 32 * 1024**3)
    monkeypatch.setattr(
        doctor,
        "_command_version",
        lambda argv, **_kwargs: versions.get(tuple(argv)),
    )

    checks = doctor.system_checks()
    by_name = {check.name: check for check in checks}

    assert by_name["Product revision"].detail == "abc1234"
    assert by_name["Platform"].detail == "Windows WSL2 x86_64"
    assert by_name["Memory"].level == "PASS"
    assert by_name["Node.js"].detail == "v24.1.0"
    assert by_name["npm"].detail == "11.0.0"
    assert by_name["OpenClaw CLI"].detail == "OpenClaw 2026.8.1-beta.2"
    assert by_name["Ollama (optional)"].detail == "ollama version 0.32.15"
    assert str(Path.home()) not in doctor.render_report(checks, as_json=True)


def test_command_version_redacts_home_directory(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda _command: "/redacted/tool")
    monkeypatch.setattr(
        doctor.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["tool", "--version"],
            returncode=0,
            stdout=f"tool installed at {Path.home()}/bin\n",
            stderr="",
        ),
    )

    assert doctor._command_version(["tool", "--version"]) == (
        "tool installed at <home>/bin"
    )


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
