"""Operator-facing readiness checker behavior."""

import subprocess
import sys
from pathlib import Path


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
    assert "--base-url" in result.stdout
