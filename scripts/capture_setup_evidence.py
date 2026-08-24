#!/usr/bin/env python3
"""Run OpenClaw setup twice explicitly and save sanitized revision-tied evidence."""

from __future__ import annotations

import sys as _bootstrap_sys

if __name__ == "__main__" and not _bootstrap_sys.flags.isolated:
    _bootstrap_sys.stderr.write(
        "Safe startup requires isolated Python mode. Run exactly:\n"
        "  python3 -I scripts/capture_setup_evidence.py\n"
        "Add any options you need after the script name.\n"
    )
    raise SystemExit(2)


_BOOTSTRAP_ORIGINAL_PATH = _bootstrap_sys.path[:]
_BOOTSTRAP_VERSION = (
    f"python{_bootstrap_sys.version_info.major}.{_bootstrap_sys.version_info.minor}"
)
_BOOTSTRAP_ZIP = (
    f"python{_bootstrap_sys.version_info.major}{_bootstrap_sys.version_info.minor}.zip"
)
_BOOTSTRAP_NORMALIZED = [
    item.replace("\\", "/").rstrip("/")
    for item in _bootstrap_sys.path
    if isinstance(item, str) and item
]
_BOOTSTRAP_STDLIB = ""
for _bootstrap_index, _bootstrap_item in enumerate(_BOOTSTRAP_NORMALIZED):
    if not _bootstrap_item.endswith("/" + _BOOTSTRAP_VERSION):
        continue
    _bootstrap_parent = _bootstrap_item[: -len(_BOOTSTRAP_VERSION)].rstrip("/")
    if (
        _bootstrap_parent + "/" + _BOOTSTRAP_ZIP
        in _BOOTSTRAP_NORMALIZED[:_bootstrap_index]
    ):
        _BOOTSTRAP_STDLIB = _bootstrap_item
if not _BOOTSTRAP_STDLIB:
    raise RuntimeError("could not establish an isolated Python standard library")
_BOOTSTRAP_STDLIB_PARENT = _BOOTSTRAP_STDLIB.rsplit("/", 1)[0]
_BOOTSTRAP_ALLOWED = {
    _BOOTSTRAP_STDLIB_PARENT + "/" + _BOOTSTRAP_ZIP,
    _BOOTSTRAP_STDLIB,
    _BOOTSTRAP_STDLIB + "/lib-dynload",
}
_bootstrap_sys.path[:] = [
    item
    for item in _BOOTSTRAP_ORIGINAL_PATH
    if isinstance(item, str)
    and item
    and item.replace("\\", "/").rstrip("/") in _BOOTSTRAP_ALLOWED
]
_bootstrap_sys.dont_write_bytecode = True
_bootstrap_sys.pycache_prefix = _BOOTSTRAP_STDLIB + "/.openhouse-disabled-pycache"

import argparse
import hashlib
import json
import math
import os
import stat
import subprocess
import sys
import tempfile
import time
import types
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

_VERIFIED_HEAD_ENTRYPOINT = __name__ == "__main__" or __name__.startswith(
    "_openhouse_validated_"
)

if not _VERIFIED_HEAD_ENTRYPOINT:
    sys.path[:] = _BOOTSTRAP_ORIGINAL_PATH

_SOURCE_ONLY_PYCACHE = tempfile.TemporaryDirectory(prefix="openhouse-source-only-")
sys.dont_write_bytecode = True
sys.pycache_prefix = _SOURCE_ONLY_PYCACHE.name
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["PYTHONPYCACHEPREFIX"] = _SOURCE_ONLY_PYCACHE.name


def _load_verified_head_module(
    repo: Path, relative: str, module_name: str
) -> types.ModuleType:
    """Execute only bytes proven to be the named regular file in HEAD."""
    source = repo / relative
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise RuntimeError("repository source validation failed")
    try:
        path_node = os.lstat(source)
    except OSError as exc:
        raise RuntimeError("repository source validation failed") from exc
    if stat.S_ISLNK(path_node.st_mode) or not stat.S_ISREG(path_node.st_mode):
        raise RuntimeError("repository source validation failed")
    tree_result = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "HEAD", "--", relative],
        capture_output=True,
        check=False,
        timeout=10,
    )
    try:
        metadata, tracked_path = tree_result.stdout.rstrip(b"\n").split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split(" ")
        tracked_relative = tracked_path.decode("utf-8", "strict")
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError("repository source validation failed") from exc
    blob_result = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "blob", object_id],
        capture_output=True,
        check=False,
        timeout=10,
    )
    descriptor = -1
    try:
        descriptor = os.open(source, os.O_RDONLY | nofollow)
        before = os.fstat(descriptor)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise RuntimeError("repository source validation failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    actual_mode = "100755" if before.st_mode & 0o111 else "100644"
    contents = b"".join(chunks)
    if (
        tree_result.returncode != 0
        or blob_result.returncode != 0
        or tracked_relative != relative
        or kind != "blob"
        or mode != actual_mode
        or not stat.S_ISREG(before.st_mode)
        or (before.st_dev, before.st_ino)
        != (path_node.st_dev, path_node.st_ino)
        or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        or len(contents) != before.st_size
        or contents != blob_result.stdout
    ):
        raise RuntimeError("repository source validation failed")
    module = types.ModuleType(module_name)
    module.__file__ = str(source)
    module.__package__ = ""
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        exec(compile(contents, str(source), "exec"), module.__dict__)
    except Exception:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous
        raise
    return module


def _validated_material_repo() -> tuple[Path, types.ModuleType]:
    """Load the HEAD setup scanner without exposing the repository on sys.path."""
    script = Path(__file__)
    if script.is_symlink():
        raise RuntimeError("repository source validation failed")
    repo = script.absolute().parent.parent.resolve()
    root_result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if (
        root_result.returncode != 0
        or Path(root_result.stdout.strip()).resolve() != repo
    ):
        raise RuntimeError("repository source validation failed")
    try:
        module = _load_verified_head_module(
            repo,
            "scripts/setup_openclaw.py",
            "_openhouse_validated_capture_setup",
        )
        module._material_head_state(repo)
    except Exception as exc:
        raise RuntimeError("repository source validation failed") from exc
    return repo, module


if _VERIFIED_HEAD_ENTRYPOINT:
    try:
        REPO, _setup_module = _validated_material_repo()
        _acceptance_module = _load_verified_head_module(
            REPO,
            "scripts/acceptance_openclaw.py",
            "_openhouse_validated_capture_acceptance",
        )
    except RuntimeError:
        print("repository source validation failed", file=sys.stderr)
        raise SystemExit(1) from None
    REVISION_RE = _acceptance_module.REVISION_RE
    _sanitize_text = _acceptance_module._sanitize_text
    OpenClawCLI = _setup_module.OpenClawCLI
    SetupConflict = _setup_module.SetupConflict
    SETUP_DEADLINE_SECONDS = _setup_module.SETUP_DEADLINE_SECONDS
    ROLLBACK_DEADLINE_SECONDS = _setup_module.ROLLBACK_DEADLINE_SECONDS
    MAX_SETUP_STATE_BYTES = _setup_module.MAX_SETUP_STATE_BYTES
    SETUP_STATE_FD_ENV = _setup_module.SETUP_STATE_FD_ENV
    _parse_args = _setup_module._parse_args
    _redact_api_token = _setup_module._redact_api_token
    _material_head_state = _setup_module._material_head_state
    canonical_installed_state_digest = _setup_module.canonical_installed_state_digest
    capture_installed_state = _setup_module.capture_installed_state
    validate_installed_state_snapshot = _setup_module.validate_installed_state_snapshot
else:
    REPO = Path(__file__).resolve().parent.parent
    from scripts.acceptance_openclaw import REVISION_RE, _sanitize_text
    from scripts.setup_openclaw import (
        OpenClawCLI,
        MAX_SETUP_STATE_BYTES,
        ROLLBACK_DEADLINE_SECONDS,
        SETUP_DEADLINE_SECONDS,
        SETUP_STATE_FD_ENV,
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
EVIDENCE_DEADLINE_SECONDS = 2 * (
    SETUP_DEADLINE_SECONDS + ROLLBACK_DEADLINE_SECONDS
) + 60


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _remaining_time(
    deadline: float, clock: Callable[[], float], *, cap: float | None = None
) -> float:
    remaining = deadline - clock()
    if remaining <= 0:
        raise RuntimeError("setup evidence time limit expired")
    return remaining if cap is None else min(remaining, cap)


def _capture_revision(
    *, deadline: float | None = None, clock: Callable[[], float] = time.monotonic
) -> str:
    timeout = 10.0 if deadline is None else _remaining_time(deadline, clock, cap=10.0)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    revision = result.stdout.strip().lower()
    if result.returncode != 0 or REVISION_RE.fullmatch(revision) is None:
        raise RuntimeError("could not capture the tested git revision")
    return revision


def _capture_repository_state(
    *, deadline: float | None = None, clock: Callable[[], float] = time.monotonic
) -> tuple[str, bool, str]:
    revision = _capture_revision(deadline=deadline, clock=clock)
    timeout = 10.0 if deadline is None else _remaining_time(deadline, clock, cap=10.0)
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError("could not verify that the tested worktree is clean")
    material = _material_head_state(
        REPO,
        deadline_check=(
            None
            if deadline is None
            else lambda: _remaining_time(deadline, clock)
        ),
    )
    return revision, not bool(result.stdout), material["material_tree_sha256"]


def _combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return f"{result.stdout}\n{result.stderr}".strip()


def _sanitize_output(value: str) -> str:
    return _sanitize_text(_redact_api_token(value), limit=MAX_CAPTURE_TEXT)


def _read_setup_state_handoff(descriptor: int) -> tuple[int, dict | None]:
    os.lseek(descriptor, 0, os.SEEK_SET)
    raw = os.read(descriptor, MAX_SETUP_STATE_BYTES + 1)
    if not raw or len(raw) > MAX_SETUP_STATE_BYTES:
        return 1, None
    try:
        envelope = json.loads(raw)
    except (UnicodeError, ValueError):
        return 1, None
    if (
        not isinstance(envelope, dict)
        or set(envelope) != {
            "schema_version",
            "state_capture_exit_code",
            "state",
        }
        or envelope.get("schema_version") != 1
        or not isinstance(envelope.get("state_capture_exit_code"), int)
        or isinstance(envelope.get("state_capture_exit_code"), bool)
        or envelope["state_capture_exit_code"] not in {0, 1}
    ):
        return 1, None
    if envelope["state_capture_exit_code"] != 0:
        return 1, None
    try:
        state = validate_installed_state_snapshot(envelope.get("state"))
    except SetupConflict:
        return 1, None
    return 0, state


def _run_setup(
    _sequence: int,
    *,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[int, str, int, dict | None]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPYCACHEPREFIX"] = str(sys.pycache_prefix)
    timeout = (
        EVIDENCE_DEADLINE_SECONDS
        if deadline is None
        else _remaining_time(deadline, clock)
    )
    with tempfile.TemporaryFile(prefix="openhouse-setup-state-") as handoff:
        descriptor = handoff.fileno()
        os.fchmod(descriptor, 0o600)
        environment[SETUP_STATE_FD_ENV] = str(descriptor)
        try:
            result = subprocess.run(
                [sys.executable, "-I", "scripts/setup_openclaw.py"],
                cwd=REPO,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
                pass_fds=(descriptor,),
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return (
                124,
                "OpenClaw setup exceeded the bounded evidence time limit; "
                "review the retained configuration before retrying.",
                -1,
                None,
            )
        if result.returncode != 0:
            return result.returncode, _combined_output(result), -1, None
        state_capture_exit_code, state = _read_setup_state_handoff(descriptor)
    return (
        result.returncode,
        _combined_output(result),
        state_capture_exit_code,
        state,
    )


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
    descriptor: int, expected: bytes, identity: tuple[int, int]
) -> None:
    before = os.fstat(descriptor)
    if (before.st_dev, before.st_ino) != identity:
        raise OSError("evidence verification found a changed file identity")
    if not stat.S_ISREG(before.st_mode) or stat.S_IMODE(before.st_mode) != 0o600:
        raise OSError("evidence verification found an unsupported file")
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        digest.update(chunk)
    after = os.fstat(descriptor)
    if (
        (after.st_dev, after.st_ino) != identity
        or after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
        or size != len(expected)
        or digest.digest() != hashlib.sha256(expected).digest()
    ):
        raise OSError("evidence verification did not match the complete content")


def _verify_private_leaf(
    parent_fd: int, leaf: str, expected: bytes, identity: tuple[int, int]
) -> None:
    descriptor = os.open(
        leaf,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_fd,
    )
    try:
        _verify_private_file(descriptor, expected, identity)
    finally:
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


def _scrub_private_descriptor(descriptor: int, parent_fd: int) -> None:
    os.fchmod(descriptor, 0o600)
    os.lseek(descriptor, 0, os.SEEK_SET)
    os.ftruncate(descriptor, 0)
    os.fsync(descriptor)
    os.fsync(parent_fd)


@dataclass
class _PrivateArtifact:
    descriptor: int
    parent_fd: int
    identity: tuple[int, int]

    def invalidate(self) -> None:
        try:
            _scrub_private_descriptor(self.descriptor, self.parent_fd)
        finally:
            self.close()

    def close(self) -> None:
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1
        if self.parent_fd >= 0:
            os.close(self.parent_fd)
            self.parent_fd = -1


def _write_private_verified(
    path: Path,
    content: bytes,
    *,
    retain: bool = False,
    deadline_check: Callable[[], float] | None = None,
) -> tuple[int, int] | _PrivateArtifact:
    if deadline_check is not None:
        deadline_check()
    absolute, parent_fd = _private_parent(path)
    descriptor = -1
    identity: tuple[int, int] | None = None
    try:
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(absolute.name, flags, 0o600, dir_fd=parent_fd)
        if deadline_check is not None:
            deadline_check()
        node = os.fstat(descriptor)
        if not stat.S_ISREG(node.st_mode):
            raise OSError("evidence destination was not a regular file")
        identity = (node.st_dev, node.st_ino)
        view = memoryview(content)
        while view:
            if deadline_check is not None:
                deadline_check()
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("evidence write did not make progress")
            view = view[written:]
        if deadline_check is not None:
            deadline_check()
        os.fsync(descriptor)
        if deadline_check is not None:
            deadline_check()
        os.fsync(parent_fd)
        if deadline_check is not None:
            deadline_check()
        _verify_private_file(descriptor, content, identity)
        if deadline_check is not None:
            deadline_check()
        _verify_private_leaf(parent_fd, absolute.name, content, identity)
        if deadline_check is not None:
            deadline_check()
        _parent_identity_matches_path(absolute, parent_fd)
        if deadline_check is not None:
            deadline_check()
        if retain:
            artifact = _PrivateArtifact(descriptor, parent_fd, identity)
            descriptor = -1
            parent_fd = -1
            return artifact
        return identity
    except Exception as exc:
        if identity is not None:
            try:
                _scrub_private_descriptor(descriptor, parent_fd)
            except OSError as scrub_error:
                raise OSError(
                    "evidence write failed and its private artifact could not be scrubbed"
                ) from scrub_error
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_fd >= 0:
            os.close(parent_fd)


def capture_setup_evidence(
    output: Path,
    *,
    revision: str | None = None,
    runner: Runner | None = None,
    repository_state: RepositoryState | None = None,
    clock: Callable[[], float] = time.monotonic,
    deadline_seconds: float = EVIDENCE_DEADLINE_SECONDS,
) -> dict:
    """Capture two sequential setup outcomes without hiding either setup run."""
    if (
        not isinstance(deadline_seconds, (int, float))
        or isinstance(deadline_seconds, bool)
        or not math.isfinite(deadline_seconds)
        or deadline_seconds <= 0
    ):
        raise ValueError("evidence time limit must be positive seconds")
    deadline = clock() + float(deadline_seconds)
    tested_revision = (
        revision or _capture_revision(deadline=deadline, clock=clock)
    ).lower()
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
    run_setup = runner or (
        lambda sequence: _run_setup(sequence, deadline=deadline, clock=clock)
    )
    inspect_repository = repository_state or (
        lambda: _capture_repository_state(deadline=deadline, clock=clock)
    )
    records: list[dict] = []
    logs: list[tuple[Path, str]] = []
    repository_checks: list[dict] = []
    _remaining_time(deadline, clock)
    initial_revision, initial_clean, initial_material = inspect_repository()
    _remaining_time(deadline, clock)
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
        _remaining_time(deadline, clock)
        started_at = _now()
        exit_code, raw_log, state_capture_exit_code, state = run_setup(sequence)
        _remaining_time(deadline, clock)
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
        _remaining_time(deadline, clock)
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
        "setup_command": ["python3", "-I", "scripts/setup_openclaw.py"],
        "repository_checks": repository_checks,
        "runs": records,
    }
    payload_sha256 = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    artifact_envelope = {
        "artifact_schema_version": 1,
        "payload": manifest,
        "payload_sha256": payload_sha256,
    }
    written: list[_PrivateArtifact] = []
    try:
        for log_path, content in logs:
            artifact = _write_private_verified(
                log_path,
                content.encode("utf-8"),
                retain=True,
                deadline_check=lambda: _remaining_time(deadline, clock),
            )
            if not isinstance(artifact, _PrivateArtifact):
                raise OSError("evidence writer did not retain its verified descriptor")
            written.append(artifact)
        manifest_bytes = (
            json.dumps(artifact_envelope, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        artifact = _write_private_verified(
            output,
            manifest_bytes,
            retain=True,
            deadline_check=lambda: _remaining_time(deadline, clock),
        )
        if not isinstance(artifact, _PrivateArtifact):
            raise OSError("evidence writer did not retain its verified descriptor")
        written.append(artifact)
    except Exception as exc:
        invalidation_failed = False
        for artifact in reversed(written):
            try:
                artifact.invalidate()
            except OSError:
                invalidation_failed = True
        if invalidation_failed:
            raise OSError(
                "evidence capture failed and a private artifact could not be scrubbed"
            ) from exc
        raise
    for artifact in written:
        artifact.close()
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
