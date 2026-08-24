"""Behavior tests for the shared launcher environment loader."""

import importlib.util
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]


GUIDE_SETUP_REGIONS = {
    REPO / "README.md": (
        "## Set up real local AI",
        "## What the status means",
        "cd open-intelligence-crm",
        "start in the directory where you cloned the project",
    ),
    REPO / "docs/LOCAL-AI.md": (
        "## Basic setup",
        "## Configuration details",
        "cd open-intelligence-crm",
        "start in the directory where you cloned the project",
    ),
    REPO / "docs/MAC-MINI-SETUP.md": (
        "## 2. Enable OpenClaw chat access",
        "## 4. Check the visible behavior",
        "cd ~/Documents/open-intelligence-crm",
        "Open a second Terminal and run:",
    ),
    REPO / "docs/GB10-SETUP.md": (
        "## Before you start",
        "## Optional Discord",
        "cd open-intelligence-crm",
        "start in the directory where you cloned the project",
    ),
}


def _section_between(text: str, start: str, end: str) -> str:
    start_at = text.index(start)
    end_at = text.index(end, start_at + len(start))
    return text[start_at:end_at]


def _bash_blocks(text: str) -> list[str]:
    return re.findall(r"```bash\n(.*?)```", text, flags=re.DOTALL)


def test_primary_setup_regions_have_a_safe_runnable_sequence():
    for path, (start, end, doctor_cd, _) in GUIDE_SETUP_REGIONS.items():
        primary = _section_between(path.read_text(), start, end)
        blocks = _bash_blocks(primary)

        endpoint = next(
            index
            for index, block in enumerate(blocks)
            if "gateway.http.endpoints.chatCompletions.enabled true" in block
        )
        setup = next(
            index
            for index, block in enumerate(blocks)
            if "python3 scripts/setup_openclaw.py" in block
            and "--bind-discord" not in block
        )
        serve = next(
            index for index, block in enumerate(blocks) if "bash scripts/serve.sh" in block
        )
        doctor = next(
            index
            for index, block in enumerate(blocks)
            if "python3 scripts/doctor.py --live-agent --live-crm" in block
        )

        assert endpoint < setup <= serve < doctor, path
        assert primary.index("AGENT_MODE=openclaw") < primary.index("bash scripts/serve.sh"), path
        assert primary.index("python3 scripts/setup_openclaw.py") < primary.index("bash scripts/serve.sh"), path
        assert doctor_cd in blocks[doctor], path
        assert "cp -R skills/" not in primary, path


def test_each_guide_independently_explains_the_agent_and_trust_boundaries():
    for path in GUIDE_SETUP_REGIONS:
        text = path.read_text()

        assert "AGENT_ID=openhouse-crm" in text, path
        assert "crm-db-operations" in text, path
        assert "openhouse_crm" in text, path
        assert "openhouse-crm skill" not in text.lower(), path
        assert "CRM wrapper and daily-brief runner" not in text, path
        assert "crm_verified" in text or "CRM verified" in text, path
        assert "Pending approvals" in text, path
        assert "deterministic fallback" in text, path
        assert "publication date" in text and "geographic area" in text, path
        assert "--bind-discord ACCOUNT" in text, path


def test_readme_uses_setup_helper_and_real_capability_check():
    text = (REPO / "README.md").read_text()

    assert "python3 scripts/setup_openclaw.py" in text
    assert "python3 scripts/doctor.py --live-agent --live-crm" in text
    assert "16 GB" in text


def test_setup_docs_do_not_make_manual_skill_copy_the_primary_path():
    for path, (start, end, _, _) in GUIDE_SETUP_REGIONS.items():
        primary = _section_between(path.read_text(), start, end)
        assert "cp -R skills/" not in primary, path


def test_all_doctor_commands_are_copy_pasteable_from_a_second_terminal():
    for path, (start, end, doctor_cd, starting_assumption) in GUIDE_SETUP_REGIONS.items():
        primary = _section_between(path.read_text(), start, end)
        doctor_block = next(
            block
            for block in _bash_blocks(primary)
            if "python3 scripts/doctor.py --live-agent --live-crm" in block
        )
        doctor_at = primary.index("python3 scripts/doctor.py --live-agent --live-crm")
        assert starting_assumption in " ".join(primary[:doctor_at].split()), path
        assert doctor_cd in doctor_block, path


def test_acceptance_records_remain_unchecked_for_all_target_hosts():
    for path in [
        REPO / "docs/LOCAL-AI.md",
        REPO / "docs/MAC-MINI-SETUP.md",
        REPO / "docs/GB10-SETUP.md",
    ]:
        text = path.read_text()
        for label in (
            "OpenClaw version:",
            "Model/provider:",
            "Memory:",
            "Date and operator:",
            "--live-agent --live-crm",
            "Dashboard chat proposes a reviewed CRM write",
            "Voice note reaches the review screen",
            "Optional Discord binding",
        ):
            assert any(
                line.startswith("- [ ]") and label in line
                for line in text.splitlines()
            ), (path, label)


def test_example_environment_selects_the_dedicated_agent_once():
    text = (REPO / ".env.example").read_text()

    assert text.count("AGENT_ID=openhouse-crm") == 1


def test_load_env_exports_values_without_overwriting_explicit_environment(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("PORT=9123\nAGENT_MODE=openclaw\n")
    script = (
        "export AGENT_MODE=mock; "
        "source scripts/load-env.sh; "
        f"load_repo_env {shlex.quote(str(env_file))}; "
        "printf '%s|%s' \"$PORT\" \"$AGENT_MODE\""
    )

    result = subprocess.run(
        ["bash", "-c", script],
        cwd=REPO,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "9123|mock"


def test_load_env_accepts_comments_blank_lines_and_quoted_values(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# local operator settings\n"
        "\n"
        "AGENT_DISPLAY_NAME=\"Annie Example\"\n"
    )
    script = (
        "source scripts/load-env.sh; "
        f"load_repo_env {shlex.quote(str(env_file))}; "
        "printf '%s' \"$AGENT_DISPLAY_NAME\""
    )

    result = subprocess.run(
        ["bash", "-c", script],
        cwd=REPO,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "Annie Example"


def test_setup_client_probe_uses_only_one_request_scoped_dummy_function(monkeypatch):
    setup_path = REPO / "scripts" / "setup_openclaw.py"
    spec = importlib.util.spec_from_file_location("setup_openclaw_launcher_test", setup_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    captured = {}
    nonce = "bounded-nonce"

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, *_args):
            return json.dumps(
                {
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "tool_calls": [
                                    {
                                        "id": "probe-call",
                                        "type": "function",
                                        "function": {
                                            "name": "openhouse_setup_capability_probe",
                                            "arguments": json.dumps({"nonce": nonce}),
                                        },
                                    }
                                ]
                            },
                        }
                    ]
                }
            ).encode()

    def fake_urlopen(request, *, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setenv("AGENT_GATEWAY_URL", "http://127.0.0.1:18789/")
    monkeypatch.setenv("AGENT_CHAT_PATH", "/v1/chat/completions")
    monkeypatch.setenv("AGENT_GATEWAY_TOKEN", "gateway-secret")
    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    result = module.OpenClawCLI().probe_client_tools(
        agent_id="openhouse-crm", nonce=nonce
    )

    assert result.returncode == 200
    assert captured["url"] == "http://127.0.0.1:18789/v1/chat/completions"
    assert captured["timeout"] <= 30
    assert captured["headers"]["Authorization"] == "Bearer gateway-secret"
    assert (
        captured["headers"]["X-openclaw-message-channel"]
        == "openhouse-setup-capability"
    )
    payload = captured["payload"]
    assert payload["model"] == "openclaw/openhouse-crm"
    assert payload["tool_choice"] == "required"
    assert [tool["function"]["name"] for tool in payload["tools"]] == [
        "openhouse_setup_capability_probe"
    ]
    assert "openhouse_crm" not in json.dumps(payload)


def test_setup_dashboard_fallback_is_loopback_only_and_uses_no_real_operation(
    monkeypatch,
):
    setup_path = REPO / "scripts" / "setup_openclaw.py"
    spec = importlib.util.spec_from_file_location(
        "setup_openclaw_dashboard_probe_test", setup_path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    captured = {"calls": 0}

    def fake_post(_self, path, payload, *, channel):
        captured["calls"] += 1
        captured.update(path=path, payload=payload, channel=channel)
        return module.CommandResult(
            403,
            "",
            "Dashboard CRM calls must use the verified tool invocation path.",
        )

    monkeypatch.setenv("AGENT_GATEWAY_URL", "http://localhost:18789")
    monkeypatch.setattr(module.OpenClawCLI, "_post_gateway_json", fake_post)

    result = module.OpenClawCLI().probe_dashboard_tool_block(
        agent_id="openhouse-crm", nonce="bounded-nonce"
    )

    assert result.returncode == 403
    assert captured["path"] == "/tools/invoke"
    assert captured["channel"] == "openhouse-dashboard"
    assert captured["payload"]["tool"] == "openhouse_crm"
    assert captured["payload"]["args"] == {
        "operation": "__openhouse_setup_probe__",
        "arguments": {},
    }
    assert "__openhouse_setup_probe__" not in (
        REPO / "skills" / "crm-db-operations" / "contract.json"
    ).read_text()
    assert captured["calls"] == 1

    monkeypatch.setenv("AGENT_GATEWAY_URL", "https://gateway.example.test")
    rejected = module.OpenClawCLI().probe_dashboard_tool_block(
        agent_id="openhouse-crm", nonce="bounded-nonce"
    )
    assert rejected.returncode == 503
    assert "loopback" in rejected.stderr
    assert captured["calls"] == 1


@pytest.mark.parametrize(
    "gateway_url",
    [
        "https://gateway.example.test",
        "http://localhost.evil:18789",
        "http://user:password@localhost:18789",
        "ftp://localhost:18789",
        "http://localhost:18789/unexpected/path",
        "http://localhost:18789?target=remote",
        "http://2130706433:18789",
        "http://0177.0.0.1:18789",
    ],
)
def test_setup_probes_reject_remote_credentialed_or_ambiguous_gateway_urls(
    monkeypatch, gateway_url
):
    setup_path = REPO / "scripts" / "setup_openclaw.py"
    spec = importlib.util.spec_from_file_location(
        "setup_openclaw_loopback_rejection_test", setup_path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    network_calls = []

    def forbidden_urlopen(*args, **kwargs):
        network_calls.append((args, kwargs))
        raise AssertionError("unsafe network call")

    monkeypatch.setenv("AGENT_GATEWAY_URL", gateway_url)
    monkeypatch.setenv("AGENT_GATEWAY_TOKEN", "must-not-be-attached")
    monkeypatch.setattr(module.urllib.request, "urlopen", forbidden_urlopen)

    client = module.OpenClawCLI().probe_client_tools(
        agent_id="openhouse-setup-probe-safe", nonce="bounded-nonce"
    )
    dashboard = module.OpenClawCLI().probe_dashboard_tool_block(
        agent_id="openhouse-crm", nonce="bounded-nonce"
    )

    assert client.returncode == 503
    assert dashboard.returncode == 503
    assert network_calls == []
    assert "must-not-be-attached" not in client.stderr + dashboard.stderr


@pytest.mark.parametrize(
    "gateway_url",
    [
        "http://localhost:18789",
        "http://127.0.0.1:18789",
        "http://[::1]:18789",
    ],
)
def test_setup_client_probe_accepts_exact_loopback_hosts(monkeypatch, gateway_url):
    setup_path = REPO / "scripts" / "setup_openclaw.py"
    spec = importlib.util.spec_from_file_location(
        "setup_openclaw_loopback_acceptance_test", setup_path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    calls = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, *_args):
            return b"{}"

    def fake_urlopen(request, *, timeout):
        calls.append((request.full_url, timeout))
        return Response()

    monkeypatch.setenv("AGENT_GATEWAY_URL", gateway_url)
    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    result = module.OpenClawCLI().probe_client_tools(
        agent_id="openhouse-setup-probe-safe", nonce="bounded-nonce"
    )

    assert result.returncode == 200
    assert len(calls) == 1
