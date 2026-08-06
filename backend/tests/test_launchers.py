"""Behavior tests for the shared launcher environment loader."""

import shlex
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


GUIDES = [
    REPO / "README.md",
    REPO / "docs/LOCAL-AI.md",
    REPO / "docs/MAC-MINI-SETUP.md",
    REPO / "docs/GB10-SETUP.md",
]


def test_each_setup_guide_has_the_same_safe_agent_contract():
    for path in GUIDES:
        text = path.read_text()

        assert "AGENT_ID=openhouse-crm" in text, path
        assert "crm-db-operations" in text, path
        assert "openhouse-crm skill" not in text.lower(), path
        assert "AGENT_MODE=openclaw" in text, path
        assert "gateway.http.endpoints.chatCompletions.enabled true" in text, path
        assert "python3 scripts/setup_openclaw.py" in text, path
        assert "bash scripts/serve.sh" in text, path
        assert "python3 scripts/doctor.py --live-agent --live-crm" in text, path
        assert "--bind-discord ACCOUNT" in text, path
        assert "Pending approvals" in text, path
        assert "deterministic fallback" in text, path
        assert "publication date" in text and "geographic area" in text, path
        assert text.index("python3 scripts/setup_openclaw.py") < text.index("bash scripts/serve.sh"), path
        assert text.index("bash scripts/serve.sh") < text.index("python3 scripts/doctor.py --live-agent --live-crm"), path


def test_readme_uses_setup_helper_and_real_capability_check():
    text = (REPO / "README.md").read_text()

    assert "python3 scripts/setup_openclaw.py" in text
    assert "python3 scripts/doctor.py --live-agent --live-crm" in text
    assert "16 GB" in text


def test_setup_docs_do_not_make_manual_skill_copy_the_primary_path():
    for path in GUIDES:
        text = path.read_text()
        assert "cp -R skills/" not in text, path


def test_readme_and_mac_doctor_commands_are_copy_pasteable_from_second_terminal():
    for path in [REPO / "README.md", REPO / "docs/MAC-MINI-SETUP.md"]:
        text = path.read_text()
        doctor_at = text.index("python3 scripts/doctor.py --live-agent --live-crm")
        before_doctor = text[:doctor_at]
        assert "second Terminal" in before_doctor, path
        assert before_doctor.rfind("cd ") > before_doctor.rfind("second Terminal"), path


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
