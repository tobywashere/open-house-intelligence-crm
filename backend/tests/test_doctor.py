"""Operator-facing readiness checker behavior."""

import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


def test_doctor_help_lists_live_agent_check():
    result = subprocess.run(
        [str(REPO / ".venv/bin/python"), "scripts/doctor.py", "--help"],
        cwd=REPO,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--live-agent" in result.stdout
    assert "--base-url" in result.stdout
