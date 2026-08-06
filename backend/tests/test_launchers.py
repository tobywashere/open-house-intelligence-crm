"""Behavior tests for the shared launcher environment loader."""

import shlex
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


def test_beginner_docs_use_one_agent_and_skill_name():
    paths = [
        REPO / "README.md",
        REPO / "docs/LOCAL-AI.md",
        REPO / "docs/MAC-MINI-SETUP.md",
        REPO / "docs/GB10-SETUP.md",
    ]
    text = "\n".join(path.read_text() for path in paths)

    assert "AGENT_ID=openhouse-crm" in text
    assert "crm-db-operations" in text
    assert "openhouse-crm skill" not in text.lower()


def test_readme_uses_setup_helper_and_real_capability_check():
    text = (REPO / "README.md").read_text()

    assert "python3 scripts/setup_openclaw.py" in text
    assert "python3 scripts/doctor.py --live-agent --live-crm" in text
    assert "16 GB" in text


def test_setup_docs_do_not_make_manual_skill_copy_the_primary_path():
    paths = [
        REPO / "README.md",
        REPO / "docs/LOCAL-AI.md",
        REPO / "docs/MAC-MINI-SETUP.md",
        REPO / "docs/GB10-SETUP.md",
    ]
    text = "\n".join(path.read_text() for path in paths)

    assert text.count("python3 scripts/setup_openclaw.py") >= len(paths)
    assert "cp -R skills/" not in text


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
