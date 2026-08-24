#!/usr/bin/env python3
"""Run OpenClaw setup twice explicitly and save sanitized revision-tied evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
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
from scripts.setup_openclaw import (
    OpenClawCLI,
    SetupConflict,
    _parse_args,
    _redact_api_token,
    _material_head_state,
    canonical_installed_state_digest,
    capture_installed_state,
    validate_installed_state_snapshot,
)


Runner = Callable[[int], tuple[int, str, int, dict | None]]
RepositoryState = Callable[[], tuple[str, bool, str]]
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


def _capture_repository_state() -> tuple[str, bool, str]:
    revision = _capture_revision()
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError("could not verify that the tested worktree is clean")
    material = _material_head_state(REPO)
    return revision, not bool(result.stdout), material["material_tree_sha256"]


def _combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return f"{result.stdout}\n{result.stderr}".strip()


def _sanitize_output(value: str) -> str:
    return _sanitize_text(_redact_api_token(value), limit=MAX_CAPTURE_TEXT)


def _run_setup(_sequence: int) -> tuple[int, str, int, dict | None]:
    result = subprocess.run(
        [sys.executable, "scripts/setup_openclaw.py"],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return result.returncode, _combined_output(result), -1, None
    try:
        options = _parse_args([], repo=REPO)
        state = capture_installed_state(options, OpenClawCLI())
    except (OSError, SetupConflict, SystemExit) as exc:
        detail = _sanitize_output(exc.__class__.__name__)
        return result.returncode, f"{_combined_output(result)}\n{detail}", 1, None
    return result.returncode, _combined_output(result), 0, state


def _private_parent(path: Path) -> tuple[Path, int]:
    absolute = Path(os.path.abspath(path))
    if absolute.name in {"", ".", ".."}:
        raise OSError("evidence destination must have a safe filename")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    if not nofollow or not directory:
        raise OSError("this platform cannot safely create setup evidence")
    flags = os.O_RDONLY | directory | nofollow
    descriptor = os.open(os.path.sep, flags)
    try:
        for component in absolute.parent.parts[1:]:
            if component in {"", ".", ".."}:
                raise OSError("evidence parent contained an unsafe component")
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            node = os.fstat(next_descriptor)
            if not stat.S_ISDIR(node.st_mode):
                os.close(next_descriptor)
                raise OSError("evidence parent must contain only real directories")
            os.close(descriptor)
            descriptor = next_descriptor
        return absolute, descriptor
    except Exception:
        os.close(descriptor)
        raise


def _verify_private_file(
    parent_fd: int,
    leaf: str,
    expected: bytes,
    identity: tuple[int, int],
) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            leaf,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        node = os.fstat(descriptor)
        if (node.st_dev, node.st_ino) != identity:
            raise OSError("evidence verification found a changed file identity")
        if not stat.S_ISREG(node.st_mode) or stat.S_IMODE(node.st_mode) != 0o600:
            raise OSError("evidence verification found an unsupported file")
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
        if size != len(expected) or digest.digest() != hashlib.sha256(expected).digest():
            raise OSError("evidence verification did not match the complete content")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _parent_identity_matches_path(path: Path, parent_fd: int) -> None:
    expected = os.fstat(parent_fd)
    _, current_fd = _private_parent(path)
    try:
        current = os.fstat(current_fd)
        if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
            raise OSError("evidence parent identity changed during the write")
    finally:
        os.close(current_fd)


def _remove_owned_private_file_at(
    parent_fd: int, leaf: str, identity: tuple[int, int]
) -> None:
    try:
        node = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return
    if stat.S_ISREG(node.st_mode) and (node.st_dev, node.st_ino) == identity:
        os.unlink(leaf, dir_fd=parent_fd)
        os.fsync(parent_fd)


def _remove_owned_private_file(path: Path, identity: tuple[int, int]) -> None:
    try:
        absolute, parent_fd = _private_parent(path)
    except OSError:
        return
    try:
        _remove_owned_private_file_at(parent_fd, absolute.name, identity)
    finally:
        os.close(parent_fd)


def _write_private_verified(path: Path, content: bytes) -> tuple[int, int]:
    absolute, parent_fd = _private_parent(path)
    descriptor = -1
    identity: tuple[int, int] | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(absolute.name, flags, 0o600, dir_fd=parent_fd)
        node = os.fstat(descriptor)
        if not stat.S_ISREG(node.st_mode):
            raise OSError("evidence destination was not a regular file")
        identity = (node.st_dev, node.st_ino)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("evidence write did not make progress")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.fsync(parent_fd)
        _verify_private_file(parent_fd, absolute.name, content, identity)
        _parent_identity_matches_path(absolute, parent_fd)
        return identity
    except Exception:
        if identity is not None:
            _remove_owned_private_file_at(parent_fd, absolute.name, identity)
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)


def capture_setup_evidence(
    output: Path,
    *,
    revision: str | None = None,
    runner: Runner | None = None,
    repository_state: RepositoryState | None = None,
) -> dict:
    """Capture two sequential setup outcomes without hiding either setup run."""
    tested_revision = (revision or _capture_revision()).lower()
    if REVISION_RE.fullmatch(tested_revision) is None or len(tested_revision) != 40:
        raise ValueError("revision must be the complete 40 character lowercase git hash")
    if output.exists() or output.is_symlink():
        raise FileExistsError("setup evidence output already exists")
    _, parent_fd = _private_parent(output)
    os.close(parent_fd)
    log_paths = [
        output.with_name(f"openhouse-setup-run-{sequence}.log")
        for sequence in (1, 2)
    ]
    if any(path.exists() or path.is_symlink() for path in log_paths):
        raise FileExistsError("a setup evidence log already exists")
    run_setup = runner or _run_setup
    inspect_repository = repository_state or _capture_repository_state
    records: list[dict] = []
    logs: list[tuple[Path, str]] = []
    repository_checks: list[dict] = []
    initial_revision, initial_clean, initial_material = inspect_repository()
    repository_checks.append(
        {
            "phase": "before_run_1",
            "revision": initial_revision,
            "clean": initial_clean,
            "material_tree_sha256": initial_material,
        }
    )
    if initial_revision != tested_revision:
        raise RuntimeError("tested repository revision changed before setup")
    if not initial_clean:
        raise RuntimeError("tested worktree must be clean before setup evidence capture")
    for sequence in (1, 2):
        started_at = _now()
        exit_code, raw_log, state_capture_exit_code, state = run_setup(sequence)
        finished_at = _now()
        sanitized_log = _sanitize_output(raw_log)
        log_path = log_paths[sequence - 1]
        logs.append((log_path, sanitized_log + "\n"))
        state_sha256 = None
        if state_capture_exit_code == 0 and state is not None:
            state_sha256 = canonical_installed_state_digest(state)
        records.append(
            {
                "sequence": sequence,
                "run_id": str(uuid.uuid4()),
                "exit_code": exit_code,
                "started_at": started_at,
                "finished_at": finished_at,
                "state_capture_exit_code": state_capture_exit_code,
                "state": state,
                "state_sha256": state_sha256,
            }
        )
        checked_revision, checked_clean, checked_material = inspect_repository()
        repository_checks.append(
            {
                "phase": f"after_run_{sequence}",
                "revision": checked_revision,
                "clean": checked_clean,
                "material_tree_sha256": checked_material,
            }
        )
        if checked_revision != tested_revision:
            raise RuntimeError("tested repository revision changed during setup")
        if not checked_clean:
            raise RuntimeError("tested worktree became unclean during setup")
        if checked_material != initial_material:
            raise RuntimeError("tested material tree changed during setup")
        if exit_code != 0 or state_capture_exit_code != 0:
            break
    manifest = {
        "schema_version": 2,
        "revision": tested_revision,
        "setup_command": ["python3", "scripts/setup_openclaw.py"],
        "repository_checks": repository_checks,
        "runs": records,
    }
    written: list[tuple[Path, tuple[int, int]]] = []
    try:
        for log_path, content in logs:
            identity = _write_private_verified(log_path, content.encode("utf-8"))
            written.append((log_path, identity))
        manifest_bytes = (
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        identity = _write_private_verified(output, manifest_bytes)
        written.append((output, identity))
    except Exception:
        for path, identity in reversed(written):
            _remove_owned_private_file(path, identity)
        raise
    return manifest


def _evidence_succeeded(manifest: dict) -> bool:
    try:
        runs = manifest["runs"]
        checks = manifest["repository_checks"]
        if (
            manifest.get("schema_version") != 2
            or not isinstance(runs, list)
            or len(runs) != 2
            or not isinstance(checks, list)
            or [item.get("phase") for item in checks]
            != ["before_run_1", "after_run_1", "after_run_2"]
            or any(
                item.get("revision") != manifest.get("revision")
                or item.get("clean") is not True
                or item.get("material_tree_sha256")
                != checks[0].get("material_tree_sha256")
                for item in checks
            )
        ):
            return False
        states = []
        for run in runs:
            if run.get("exit_code") != 0 or run.get("state_capture_exit_code") != 0:
                return False
            state = validate_installed_state_snapshot(run.get("state"))
            if canonical_installed_state_digest(state) != run.get("state_sha256"):
                return False
            if state["sources"]["material_tree_sha256"] != checks[0]["material_tree_sha256"]:
                return False
            states.append(state)
        return states[0] == states[1]
    except (KeyError, SetupConflict, TypeError):
        return False


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
    except (OSError, RuntimeError, SetupConflict, ValueError) as exc:
        print(exc.__class__.__name__, file=sys.stderr)
        return 1
    succeeded = _evidence_succeeded(manifest)
    print(
        "Saved revision-tied setup evidence. Sanitized setup logs are manual "
        "diagnostics only; inspect them before sharing."
    )
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
