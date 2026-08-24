#!/usr/bin/env python3
"""Run OpenClaw setup twice explicitly and save sanitized revision-tied evidence."""

from __future__ import annotations

import sys as _bootstrap_sys


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
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

if __name__ != "__main__":
    sys.path[:] = _BOOTSTRAP_ORIGINAL_PATH

_SOURCE_ONLY_PYCACHE = tempfile.TemporaryDirectory(prefix="openhouse-source-only-")
sys.dont_write_bytecode = True
sys.pycache_prefix = _SOURCE_ONLY_PYCACHE.name
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["PYTHONPYCACHEPREFIX"] = _SOURCE_ONLY_PYCACHE.name


def _validated_material_repo() -> Path:
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
    relative = "scripts/setup_openclaw.py"
    source = repo / relative
    try:
        node = os.lstat(source)
    except OSError as exc:
        raise RuntimeError("repository source validation failed") from exc
    if stat.S_ISLNK(node.st_mode) or not stat.S_ISREG(node.st_mode):
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
    actual_mode = "100755" if node.st_mode & 0o111 else "100644"
    if (
        tree_result.returncode != 0
        or tracked_relative != relative
        or kind != "blob"
        or mode != actual_mode
    ):
        raise RuntimeError("repository source validation failed")
    blob_result = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "blob", object_id],
        capture_output=True,
        check=False,
        timeout=10,
    )
    try:
        contents = source.read_bytes()
    except OSError as exc:
        raise RuntimeError("repository source validation failed") from exc
    if blob_result.returncode != 0 or contents != blob_result.stdout:
        raise RuntimeError("repository source validation failed")
    spec = importlib.util.spec_from_file_location(
        "_openhouse_validated_setup", source
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("repository source validation failed")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        module._material_head_state(repo)
    except Exception as exc:
        raise RuntimeError("repository source validation failed") from exc
    finally:
        if previous is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous
    return repo


if __name__ == "__main__":
    try:
        REPO = _validated_material_repo()
    except RuntimeError:
        print("repository source validation failed", file=sys.stderr)
        raise SystemExit(1) from None
else:
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
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPYCACHEPREFIX"] = str(sys.pycache_prefix)
    result = subprocess.run(
        [sys.executable, "scripts/setup_openclaw.py"],
        cwd=REPO,
        env=environment,
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
    path: Path, content: bytes, *, retain: bool = False
) -> tuple[int, int] | _PrivateArtifact:
    absolute, parent_fd = _private_parent(path)
    descriptor = -1
    identity: tuple[int, int] | None = None
    try:
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
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
        os.fsync(parent_fd)
        _verify_private_file(descriptor, content, identity)
        _verify_private_leaf(parent_fd, absolute.name, content, identity)
        _parent_identity_matches_path(absolute, parent_fd)
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
                log_path, content.encode("utf-8"), retain=True
            )
            if not isinstance(artifact, _PrivateArtifact):
                raise OSError("evidence writer did not retain its verified descriptor")
            written.append(artifact)
        manifest_bytes = (
            json.dumps(artifact_envelope, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        artifact = _write_private_verified(output, manifest_bytes, retain=True)
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
