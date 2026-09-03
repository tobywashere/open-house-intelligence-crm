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
    REPO / "docs/WINDOWS-WSL-SETUP.md": (
        "## 4. Download the CRM",
        "## 9. Check the visible behavior",
        "cd ~/open-intelligence-crm",
        "Open a second WSL terminal",
    ),
}


BEGINNER_GUIDES = [
    REPO / "README.md",
    REPO / "docs/LOCAL-AI.md",
    REPO / "docs/MAC-MINI-SETUP.md",
    REPO / "docs/WINDOWS-WSL-SETUP.md",
    REPO / "docs/GB10-SETUP.md",
]


def _section_between(text: str, start: str, end: str) -> str:
    start_at = text.index(start)
    end_at = text.index(end, start_at + len(start))
    return text[start_at:end_at]


def _bash_blocks(text: str) -> list[str]:
    return re.findall(r"```bash\n(.*?)```", text, flags=re.DOTALL)


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _manual_openclaw_policy_repairs(text: str) -> list[str]:
    safe_command_warnings = re.compile(
        r"(?:do not|don't|never)\s+(?:run|use|execute)\s+"
        r"(?:`openclaw\s+config\s+(?:set|patch|unset|delete)\s+[^`]+`|"
        r"openclaw\s+config\s+(?:set|patch|unset|delete)\s+[^\n;]+)",
        flags=re.IGNORECASE,
    )
    positive_text = safe_command_warnings.sub(" ", text)
    commands = re.findall(
        r"openclaw\s+(?:config\s+(?:set|patch|unset|delete)\b|plugins?\s+"
        r"(?:install|enable|disable|remove))[^\n`]*",
        positive_text,
        flags=re.IGNORECASE,
    )
    commands = [
        command
        for command in commands
        if command
        != "openclaw config set gateway.http.endpoints.chatCompletions.enabled true --strict-json"
    ]

    normalized = _normalized(positive_text.lower())
    safe_negations = re.compile(
        r"(?:do not|never|without|rather than|no need to|does not|is not) "
        r"(?:manually )?\b(?:edit|change|patch|set|configure|install|enable|"
        r"unset|delete)\b "
        r"[^.!]+[.!]"
    )
    positive_only = safe_negations.sub(" ", normalized)
    manual_repair = re.compile(
        r"\b(?:edit|change|patch|set|configure|install|enable|unset|delete)\b "
        r"(?:the )?(?:agents\.list|openclaw\.json|plugin (?:file|files|manifest|settings)|"
        r"agent(?:'s)? (?:profile|tool|tools|exec|plugin)|global (?:tool profile|"
        r"tool profiles|profile|tools\.exec)|exec (?:host|mode|policy|security))"
    )
    prose = manual_repair.findall(positive_only)
    return commands + prose


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
            if "python3 -I scripts/setup_openclaw.py" in block
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
        assert primary.index("python3 -I scripts/setup_openclaw.py") < primary.index("bash scripts/serve.sh"), path
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

    assert "python3 -I scripts/setup_openclaw.py" in text
    assert "python3 scripts/doctor.py --live-agent --live-crm" in text
    assert "16 GB" in text


def test_beginner_guides_share_setup_readiness_and_acceptance_commands():
    normal_setup = "python3 -I scripts/setup_openclaw.py"
    serve = "bash scripts/serve.sh"
    readiness = "python3 scripts/doctor.py --live-agent --live-crm"
    setup_evidence = (
        "python3 -I scripts/capture_setup_evidence.py "
        "--output openhouse-setup-evidence.json"
    )
    acceptance = (
        "python3 -I scripts/acceptance_openclaw.py --json --allow-test-write "
        "--setup-evidence openhouse-setup-evidence.json"
    )

    for path in BEGINNER_GUIDES:
        text = path.read_text()
        assert normal_setup in text, path
        assert serve in text, path
        assert readiness in text, path
        assert setup_evidence in text, path
        assert acceptance in text, path
        assert text.index(normal_setup) < text.index(serve) < text.index(readiness), path


def test_beginner_guides_explain_required_isolated_python_flag():
    explanation = (
        "Keep the `-I` in these commands. It prevents personal Python startup "
        "customizations from running before the repository safety checks."
    )

    for path in BEGINNER_GUIDES:
        assert explanation in _normalized(path.read_text()), path


def test_beginner_guides_explain_reviewed_writes_in_plain_language():
    for path in BEGINNER_GUIDES:
        normalized = " ".join(path.read_text().lower().split())
        assert "writes wait for review" in normalized, path
        assert "pending approvals" in normalized, path


def test_beginner_guides_do_not_prescribe_manual_openclaw_policy_repairs():
    for path in BEGINNER_GUIDES:
        text = path.read_text()
        assert _manual_openclaw_policy_repairs(text) == [], path


@pytest.mark.parametrize(
    "instruction",
    [
        "Run `openclaw config unset agents.list.1.tools` and try again.",
        "Use `openclaw config delete tools.profile` to repair the agent.",
        "Unset the agent's profile before setup.",
        "Delete the plugin settings, then restart OpenClaw.",
    ],
)
def test_manual_policy_guard_rejects_unset_and_delete_repairs(instruction):
    assert _manual_openclaw_policy_repairs(instruction), instruction


@pytest.mark.parametrize(
    "warning",
    [
        "Do not run `openclaw config unset agents.list.1.tools`; rerun setup.",
        "Do not run openclaw config delete tools.profile; rerun setup.",
        "Never delete the plugin settings by hand.",
    ],
)
def test_manual_policy_guard_allows_negative_safety_warnings(warning):
    assert _manual_openclaw_policy_repairs(warning) == [], warning


def test_beginner_guides_state_supported_hardware_and_optional_features():
    readme = (REPO / "README.md").read_text()
    local = (REPO / "docs/LOCAL-AI.md").read_text()
    mac = (REPO / "docs/MAC-MINI-SETUP.md").read_text()
    wsl = (REPO / "docs/WINDOWS-WSL-SETUP.md").read_text()

    assert "Apple-silicon" in readme and "16 GB" in readme
    assert "Linux x86_64 or ARM64" in readme
    assert "Windows 11" in readme and "WSL2" in readme
    assert "Native Windows" in readme and "not" in readme
    assert "16 GB" in mac
    assert "16 GB" in wsl
    normalized_local = " ".join(local.lower().split())
    assert "optional transcription provider" in normalized_local
    assert "after dashboard acceptance" in normalized_local


def test_every_beginner_guide_explicitly_rejects_native_windows_setup():
    expected = "Native Windows is unsupported; use Windows 11 with WSL2."
    for path in BEGINNER_GUIDES:
        assert expected in _normalized(path.read_text()), path


def test_acceptance_scope_is_accurate_in_every_beginner_guide():
    required = (
        "automated CRM chat acceptance",
        "audited CRM read",
        "exact lead count",
        "invalid-write safety",
        "truthful briefing",
        "disposable create-lead proposal",
        "natural-language booking proposal",
        "Neither proposal is approved",
        "denied and cleaned up",
        "session cleanup",
        "does not automate voice or Discord delivery",
    )
    misleading = re.compile(r"\b(?:full|complete) acceptance\b|\bsupported full test\b")

    for path in BEGINNER_GUIDES:
        normalized = _normalized(path.read_text())
        for phrase in required:
            assert phrase in normalized, (path, phrase)
        assert misleading.search(normalized.lower()) is None, path


def test_readme_describes_both_disposable_proposals_in_cleanup_sentence():
    readme = _normalized((REPO / "README.md").read_text())

    assert "never approves the test proposals" in readme
    assert "never approves the test proposal." not in readme


def test_wsl_write_acceptance_saves_json_without_masking_failure():
    text = (REPO / "docs/WINDOWS-WSL-SETUP.md").read_text()
    block = next(
        block
        for block in _bash_blocks(text)
        if "scripts/acceptance_openclaw.py" in block and "--allow-test-write" in block
    )

    assert "set -o pipefail" in block
    assert "| tee openhouse-acceptance.json" in block


def test_voice_is_conditional_and_not_a_release_blocker_in_every_beginner_guide():
    expected = (
        "If no transcription provider is configured, record voice as "
        "SKIP (not configured); voice is optional and is not a release blocker."
    )
    for path in BEGINNER_GUIDES:
        assert expected in _normalized(path.read_text()), path

    for path in (
        REPO / "docs/LOCAL-AI.md",
        REPO / "docs/MAC-MINI-SETUP.md",
        REPO / "docs/GB10-SETUP.md",
    ):
        assert any(
            line.startswith("- [ ]") and "Voice (optional):" in line
            for line in path.read_text().splitlines()
        ), path


def test_discord_is_optional_and_follows_dashboard_acceptance_everywhere():
    expected = "Discord is optional and is tested only after dashboard acceptance."
    for path in BEGINNER_GUIDES:
        assert expected in _normalized(path.read_text()), path


def test_discord_delivery_is_a_manual_hardware_gate_not_an_automated_pass():
    required = (
        "Discord delivery is a manual hardware test",
        "binding alone is not a pass",
        "lists the real CRM lead count",
        "disposable write appears in Pending approvals",
        "merge waits for this manual evidence when Discord is in scope",
    )
    for path in BEGINNER_GUIDES:
        text = _normalized(path.read_text()).casefold()
        for phrase in required:
            assert phrase.casefold() in text, (path, phrase)


def test_setup_evidence_is_explicit_and_tied_to_the_tested_revision_everywhere():
    required = (
        "runs setup twice",
        "tested revision",
        "openhouse-setup-evidence.json",
        "Setup twice",
    )
    for path in BEGINNER_GUIDES:
        text = _normalized(path.read_text())
        for phrase in required:
            assert phrase in text, (path, phrase)


def test_setup_evidence_uses_exact_head_material_and_logs_are_only_diagnostics():
    required = (
        "tracked HEAD files",
        "unexpected extra files",
        "executable modes",
        "Python caches are isolated",
        "manual diagnostics only",
    )
    for path in BEGINNER_GUIDES:
        text = _normalized(path.read_text())
        for phrase in required:
            assert phrase in text, (path, phrase)


def test_generated_hardware_acceptance_artifacts_are_not_tracked_by_default():
    ignored = (REPO / ".gitignore").read_text().splitlines()

    assert "openhouse-setup-evidence.json" in ignored
    assert "openhouse-setup-run-*.log" in ignored
    assert "openhouse-acceptance.json" in ignored


def test_approved_design_matches_automated_and_manual_hardware_gates():
    design = _normalized(
        (REPO / "docs/superpowers/specs/2026-08-22-verified-dashboard-crm-chat-design.md")
        .read_text()
    ).casefold()

    for phrase in (
        "two explicit setup runs",
        "machine-verifiable setup evidence",
        "natural-language booking proposal",
        "never approves",
        "Discord delivery remains a manual hardware test",
        "binding alone is not proof",
        "merge waits for the manual Discord evidence when Discord is in scope",
    ):
        assert phrase.casefold() in design, phrase


def test_local_ai_acceptance_checklist_has_one_numbered_sequence():
    text = (REPO / "docs/LOCAL-AI.md").read_text()
    section = text[text.index("## Target hardware and live acceptance record") : text.index(
        "Optional feature checks after the ordered acceptance run"
    )]
    numbers = [
        int(value)
        for value in re.findall(r"^- \[ \] (\d+)\.", section, flags=re.MULTILINE)
    ]
    assert numbers == list(range(1, 12))
    assert "Steps 6 and 8 through 11 are conditional" in _normalized(section)


def test_local_ai_explains_evidence_statuses_without_equating_chat_and_crm():
    text = " ".join((REPO / "docs/LOCAL-AI.md").read_text().split())

    assert "`chat_verified`" in text
    assert "CRM access has not been proven" in text
    assert "`crm_verified`" in text
    assert "native CRM tool completed" in text
    assert "matching audit" in text
    assert "`degraded`" in text
    assert "previously verified" in text
    assert "latest chat completion failed" in text
    assert "`failed`" in text
    assert "required live check did not complete" in text


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
            "Voice (optional):",
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


def test_setup_client_probe_uses_full_production_schemas_and_dashboard_channel(monkeypatch):
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
                                            "name": "finish_crm_response",
                                            "arguments": json.dumps(
                                                {
                                                    "classification": "needs_clarification",
                                                    "message": nonce,
                                                    "evidence_call_ids": [],
                                                }
                                            ),
                                        },
                                    }
                                ]
                            },
                        }
                    ]
                }
            ).encode()

    class FakeOpener:
        def open(self, request, *, timeout):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["payload"] = json.loads(request.data)
            captured["timeout"] = timeout
            return Response()

    monkeypatch.setenv("AGENT_GATEWAY_URL", "http://127.0.0.1:18789/")
    monkeypatch.setenv("AGENT_CHAT_PATH", "/v1/chat/completions")
    monkeypatch.setenv("AGENT_GATEWAY_TOKEN", "gateway-secret")
    monkeypatch.setattr(
        module.urllib.request, "build_opener", lambda *_handlers: FakeOpener()
    )

    contract = module._capture_canonical_contract(REPO)
    tools = module._capture_dashboard_client_tools(REPO, contract).tools
    result = module.OpenClawCLI().probe_client_tools(
        agent_id="openhouse-crm",
        nonce=nonce,
        tools=tools,
        session_key="agent:openhouse-crm:dashboard:setup-test",
    )

    assert result.returncode == 200
    assert captured["url"] == "http://127.0.0.1:18789/v1/chat/completions"
    assert captured["timeout"] <= 30
    assert captured["headers"]["Authorization"] == "Bearer gateway-secret"
    assert (
        captured["headers"]["X-openclaw-session-key"]
        == "agent:openhouse-crm:dashboard:setup-test"
    )
    assert (
        captured["headers"]["X-openclaw-message-channel"]
        == "openhouse-dashboard"
    )
    payload = captured["payload"]
    assert payload["model"] == "openclaw/openhouse-crm"
    assert payload["tool_choice"] == "required"
    assert [tool["function"]["name"] for tool in payload["tools"]] == [
        "openhouse_crm_request",
        "finish_crm_response",
    ]
    assert payload["tools"] == tools
    assert "finish_crm_response" in payload["messages"][0]["content"]
    assert "openhouse_setup_marker_probe" not in payload["messages"][0]["content"]


def test_setup_channel_attempt_invokes_native_marker_on_the_trusted_channel(monkeypatch):
    setup_path = REPO / "scripts" / "setup_openclaw.py"
    spec = importlib.util.spec_from_file_location(
        "setup_openclaw_channel_attempt_test", setup_path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    captured = {}

    def fake_post(_self, path, payload, *, channel, session_key=None):
        captured.update(
            path=path,
            payload=payload,
            channel=channel,
            session_key=session_key,
        )
        return module.CommandResult(502, '{"error":{"type":"api_error"}}', "")

    monkeypatch.setenv("AGENT_GATEWAY_URL", "http://localhost:18789")
    monkeypatch.setenv("AGENT_CHAT_PATH", "/v1/chat/completions")
    monkeypatch.setattr(module.OpenClawCLI, "_post_gateway_json", fake_post)

    result = module.OpenClawCLI().probe_channel_marker_attempt(
        agent_id="openhouse-setup-probe-safe",
        nonce="0123456789abcdef0123456789abcdef",
        channel="openhouse-dashboard",
        session_key="agent:openhouse-setup-probe-safe:dashboard:setup-test",
    )

    assert result.returncode == 502
    assert captured["path"] == "/tools/invoke"
    assert captured["channel"] == "openhouse-dashboard"
    assert captured["session_key"] is None
    assert captured["payload"] == {
        "tool": "openhouse_setup_marker_probe",
            "args": {
                "action": "attempt",
                "channel": "openhouse-dashboard",
                "nonce": "0123456789abcdef0123456789abcdef",
                "session_key": (
                    "agent:openhouse-setup-probe-safe:dashboard:setup-test"
                ),
            },
        "agentId": "openhouse-setup-probe-safe",
        "sessionKey": "agent:openhouse-setup-probe-safe:dashboard:setup-test",
        "idempotencyKey": (
            "setup-marker-attempt:0123456789abcdef0123456789abcdef:"
            "openhouse-dashboard"
        ),
    }


def test_setup_analysis_probe_matches_the_production_no_tools_path(monkeypatch):
    setup_path = REPO / "scripts" / "setup_openclaw.py"
    spec = importlib.util.spec_from_file_location(
        "setup_openclaw_analysis_probe_test", setup_path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    captured = {}

    def fake_post(_self, path, payload, *, channel, session_key=None):
        captured.update(
            path=path,
            payload=payload,
            channel=channel,
            session_key=session_key,
        )
        return module.CommandResult(200, '{"choices":[]}', "")

    monkeypatch.setenv("AGENT_GATEWAY_URL", "http://localhost:18789")
    monkeypatch.setenv("AGENT_CHAT_PATH", "/v1/chat/completions")
    monkeypatch.setattr(module.OpenClawCLI, "_post_gateway_json", fake_post)

    result = module.OpenClawCLI().probe_analysis_tool_block(
        agent_id="openhouse-setup-probe-safe",
        nonce="bounded-nonce",
        session_key="agent:openhouse-setup-probe-safe:dashboard:setup-test",
    )

    assert result.returncode == 200
    assert captured["path"] == "/v1/chat/completions"
    assert captured["channel"] == "openhouse-analysis"
    assert (
        captured["session_key"]
        == "agent:openhouse-setup-probe-safe:dashboard:setup-test"
    )
    assert captured["payload"]["tools"] == []
    assert captured["payload"]["tool_choice"] == "none"
    assert captured["payload"]["model"] == "openclaw/openhouse-setup-probe-safe"
    assert "without calling tools" in captured["payload"]["messages"][0]["content"]
    assert "openhouse_setup_marker_probe" not in captured["payload"]["messages"][0]["content"]


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

    contract = module._capture_canonical_contract(REPO)
    tools = module._capture_dashboard_client_tools(REPO, contract).tools
    client = module.OpenClawCLI().probe_client_tools(
        agent_id="openhouse-setup-probe-safe",
        nonce="bounded-nonce",
        tools=tools,
    )
    analysis = module.OpenClawCLI().probe_analysis_tool_block(
        agent_id="openhouse-setup-probe-safe", nonce="bounded-nonce"
    )
    channel_attempt = module.OpenClawCLI().probe_channel_marker_attempt(
        agent_id="openhouse-setup-probe-safe",
        nonce="0" * 32,
        channel="openhouse-dashboard",
        session_key="agent:openhouse-setup-probe-safe:dashboard:setup-test",
    )

    assert client.returncode == 503
    assert analysis.returncode == 503
    assert channel_attempt.returncode == 503
    assert network_calls == []
    assert "must-not-be-attached" not in (
        client.stderr + analysis.stderr + channel_attempt.stderr
    )


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

    class FakeOpener:
        def open(self, request, *, timeout):
            calls.append((request.full_url, timeout))
            return Response()

    monkeypatch.setenv("AGENT_GATEWAY_URL", gateway_url)
    monkeypatch.setattr(
        module.urllib.request, "build_opener", lambda *_handlers: FakeOpener()
    )

    contract = module._capture_canonical_contract(REPO)
    tools = module._capture_dashboard_client_tools(REPO, contract).tools
    result = module.OpenClawCLI().probe_client_tools(
        agent_id="openhouse-setup-probe-safe",
        nonce="bounded-nonce",
        tools=tools,
    )

    assert result.returncode == 200
    assert len(calls) == 1
