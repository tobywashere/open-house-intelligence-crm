"""Behavior tests for the shared launcher environment loader."""

import shlex
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


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
