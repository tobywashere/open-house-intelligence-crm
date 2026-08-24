#!/usr/bin/env python3
"""Run OpenClaw setup twice explicitly and save sanitized revision-tied evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.acceptance_openclaw import REVISION_RE, _sanitize_text


Runner = Callable[[int], tuple[int, str, int, str]]
MAX_CAPTURE_TEXT = 1024 * 1024


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _capture_revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    revision = result.stdout.strip().lower()
    if result.returncode != 0 or REVISION_RE.fullmatch(revision) is None:
        raise RuntimeError("could not capture the tested git revision")
    return revision


def _combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return f"{result.stdout}\n{result.stderr}".strip()


def _sanitize_output(value: str) -> str:
    return _sanitize_text(value, limit=MAX_CAPTURE_TEXT)


def _run_setup(_sequence: int) -> tuple[int, str, int, str]:
    result = subprocess.run(
        [sys.executable, "scripts/setup_openclaw.py"],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return result.returncode, _combined_output(result), -1, ""
    state = subprocess.run(
        [sys.executable, "scripts/setup_openclaw.py", "--dry-run"],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    return (
        result.returncode,
        _combined_output(result),
        state.returncode,
        _combined_output(state),
    )


def _write_private(path: Path, content: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, content.encode("utf-8"))
    finally:
        os.close(descriptor)


def capture_setup_evidence(
    output: Path,
    *,
    revision: str | None = None,
    runner: Runner | None = None,
) -> dict:
    """Capture two sequential setup outcomes without hiding either setup run."""
    tested_revision = (revision or _capture_revision()).lower()
    if REVISION_RE.fullmatch(tested_revision) is None:
        raise ValueError("revision must be a 7 to 40 character lowercase git hash")
    if output.exists() or output.is_symlink():
        raise FileExistsError("setup evidence output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    log_paths = [
        output.with_name(f"openhouse-setup-run-{sequence}.log")
        for sequence in (1, 2)
    ]
    if any(path.exists() or path.is_symlink() for path in log_paths):
        raise FileExistsError("a setup evidence log already exists")
    run_setup = runner or _run_setup
    records: list[dict] = []
    logs: list[tuple[Path, str]] = []
    for sequence in (1, 2):
        started_at = _now()
        exit_code, raw_log, state_probe_exit_code, raw_state = run_setup(sequence)
        finished_at = _now()
        sanitized_log = _sanitize_output(raw_log)
        sanitized_state = _sanitize_output(raw_state)
        encoded = (sanitized_log + "\n").encode("utf-8")
        state_encoded = (sanitized_state + "\n").encode("utf-8")
        log_path = log_paths[sequence - 1]
        logs.append((log_path, sanitized_log + "\n"))
        records.append(
            {
                "sequence": sequence,
                "run_id": str(uuid.uuid4()),
                "exit_code": exit_code,
                "started_at": started_at,
                "finished_at": finished_at,
                "sanitized_log_sha256": hashlib.sha256(encoded).hexdigest(),
                "state_probe_exit_code": state_probe_exit_code,
                "sanitized_state_sha256": hashlib.sha256(
                    state_encoded
                ).hexdigest(),
            }
        )
        if exit_code != 0 or state_probe_exit_code != 0:
            break
    manifest = {
        "schema_version": 1,
        "revision": tested_revision,
        "setup_command": ["python3", "scripts/setup_openclaw.py"],
        "runs": records,
    }
    for log_path, content in logs:
        _write_private(log_path, content)
    _write_private(output, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def _evidence_succeeded(manifest: dict) -> bool:
    runs = manifest.get("runs")
    return (
        isinstance(runs, list)
        and len(runs) == 2
        and all(
            isinstance(run, dict)
            and run.get("exit_code") == 0
            and run.get("state_probe_exit_code") == 0
            for run in runs
        )
        and runs[0].get("sanitized_state_sha256")
        == runs[1].get("sanitized_state_sha256")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Explicitly run OpenClaw setup twice and save sanitized, revision-tied evidence."
        )
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("openhouse-setup-evidence.json"),
        help="new evidence file to create (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    try:
        manifest = capture_setup_evidence(args.output)
    except (OSError, RuntimeError, ValueError) as exc:
        print(exc.__class__.__name__, file=sys.stderr)
        return 1
    succeeded = _evidence_succeeded(manifest)
    print(
        "Saved revision-tied setup evidence and sanitized run logs. "
        "Inspect them before sharing."
    )
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
