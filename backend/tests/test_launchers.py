"""Behavior tests for the shared launcher environment loader."""

import shlex
import subprocess
import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


GUIDE_SETUP_REGIONS = {
    REPO / "README.md": (
        "## Set up real local AI",
        "## What the status means",
        "cd open-intelligence-crm",
    ),
    REPO / "docs/LOCAL-AI.md": (
        "## Basic setup",
        "## Configuration details",
        "cd open-intelligence-crm",
    ),
    REPO / "docs/MAC-MINI-SETUP.md": (
        "## 2. Enable OpenClaw chat access",
        "## 4. Check the visible behavior",
        "cd ~/Documents/open-intelligence-crm",
    ),
    REPO / "docs/GB10-SETUP.md": (
        "## Before you start",
        "## Optional Discord",
        "cd open-intelligence-crm",
    ),
}


def _section_between(text: str, start: str, end: str) -> str:
    start_at = text.index(start)
    end_at = text.index(end, start_at + len(start))
    return text[start_at:end_at]


def _bash_blocks(text: str) -> list[str]:
    return re.findall(r"```bash\n(.*?)```", text, flags=re.DOTALL)


def test_primary_setup_regions_have_a_safe_runnable_sequence():
    for path, (start, end, doctor_cd) in GUIDE_SETUP_REGIONS.items():
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
        assert "openhouse-crm skill" not in text.lower(), path
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
    for path, (start, end, _) in GUIDE_SETUP_REGIONS.items():
        primary = _section_between(path.read_text(), start, end)
        assert "cp -R skills/" not in primary, path


def test_all_doctor_commands_are_copy_pasteable_from_a_second_terminal():
    for path, (start, end, doctor_cd) in GUIDE_SETUP_REGIONS.items():
        primary = _section_between(path.read_text(), start, end)
        doctor_block = next(
            block
            for block in _bash_blocks(primary)
            if "python3 scripts/doctor.py --live-agent --live-crm" in block
        )
        assert "second Terminal" in primary, path
        assert primary.index("second Terminal") < primary.index(
            "python3 scripts/doctor.py --live-agent --live-crm"
        ), path
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
