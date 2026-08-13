#!/usr/bin/env python3
"""Deterministic, evidence-gated state management for Agent Team."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil

# Git is invoked with a fixed argv and shell=False.
import subprocess  # nosec B404
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

try:  # pragma: no cover - platform-specific import
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]
    import msvcrt


SCHEMA_VERSION = 5
PLANNING_LIMIT = 5
VERIFICATION_LIMIT = 8
CONTROL_DIR = ".agent-team"
RUNS_DIR = "runs"
REQUEST_FILE = "request.json"
REQUEST_LOCK_FILE = ".request.lock"
LOCK_FILE = ".state.lock"
INIT_TEMP_PREFIX = ".init-"

INITIALIZED = "INITIALIZED"
PLANNING = "PLANNING"
AWAITING_PLAN_APPROVAL = "AWAITING_PLAN_APPROVAL"
EXECUTING = "EXECUTING"
VERIFYING = "VERIFYING"
FINAL_REVIEW = "FINAL_REVIEW"
AWAITING_FINAL_APPROVAL = "AWAITING_FINAL_APPROVAL"
DONE = "DONE"
BLOCKED = "BLOCKED"

ACTIVE_STATES = {
    INITIALIZED,
    PLANNING,
    AWAITING_PLAN_APPROVAL,
    EXECUTING,
    VERIFYING,
    FINAL_REVIEW,
    AWAITING_FINAL_APPROVAL,
}
ALL_STATES = ACTIVE_STATES | {DONE, BLOCKED}
DELIVERY_MODES = {"workspace", "staged"}
RISK_STATUSES = {"open", "mitigated", "closed"}
RISK_TRANSITIONS = {
    "open": {"mitigated"},
    "mitigated": {"open", "closed"},
    "closed": {"open"},
}
RISK_CLOSERS = {"verifier", "planner_final"}
EXECUTOR_MODES = {"initial", "repair"}
TEST_EVIDENCE_KINDS = {"formal", "ad_hoc"}
DEPLOYMENT_STATUSES = {"not_applicable", "local_only", "not_deployed", "deployed"}

ROLE_MARKERS = {
    "planner": ("PLAN_READY", "BLOCKED"),
    "plan_reviewer": ("APPROVED", "NEEDS_REVISION", "BLOCKED"),
    "executor": ("EXECUTION_DONE", "BLOCKED"),
    "verifier": ("PASS", "FAIL", "NEEDS_TEST_PERSISTENCE", "BLOCKED"),
    "planner_final": ("FINAL_ACCEPT", "FINAL_REJECT", "BLOCKED"),
}
ROLE_ARTIFACTS = {
    "planner": "plan.md",
    "plan_reviewer": "review-log.md",
    "executor": "deliverables.md",
    "verifier": "verify-report.md",
    "planner_final": "final-review.md",
}
APPEND_ONLY_ROLES = {"plan_reviewer", "verifier", "planner_final"}
ROLE_MIN_EVIDENCE_BYTES = {
    "planner": 300,
    "plan_reviewer": 180,
    "executor": 250,
    "verifier": 300,
    "planner_final": 250,
}
ROLE_REQUIRED_SECTIONS = {
    "planner": (
        "## goal and scope",
        "## write scope",
        "## implementation steps",
        "## risks and recovery",
        "## acceptance criteria",
    ),
    "plan_reviewer": ("## planning cycle", "### findings"),
    "executor": (
        "## files changed",
        "## execution iterations",
        "## validation",
        "## residual risks",
    ),
    "verifier": (
        "## verification cycle",
        "| # | criterion |",
        "### test persistence",
        "### defects and unmet criteria",
    ),
    "planner_final": ("## final review", "| # | criterion |", "## residual risks"),
}
MARKERS = {marker for markers in ROLE_MARKERS.values() for marker in markers}
RUN_ID_PATTERN = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9][a-z0-9-]*(?:-[0-9]+)?$")
REQUEST_ID_PATTERN = re.compile(r"^req-[0-9a-f]{32}$")
RISK_ID_PATTERN = re.compile(r"^R-[0-9]{4,}$")
TEST_EVIDENCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


class StateError(RuntimeError):
    """Raised when a requested state operation violates an invariant."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or "task")[:48].rstrip("-")


def lexical_absolute(path: str | Path) -> Path:
    expanded = Path(path).expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    return Path(os.path.abspath(os.fspath(expanded)))


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def workspace_paths(workspace: str | Path) -> tuple[Path, Path]:
    lexical = lexical_absolute(workspace)
    try:
        real = lexical.resolve(strict=True)
    except FileNotFoundError as exc:
        raise StateError(f"workspace does not exist: {lexical}") from exc
    if not real.is_dir():
        raise StateError(f"workspace is not a directory: {lexical}")
    return lexical, real


def invocation_workspace() -> Path:
    real_cwd = Path.cwd().resolve(strict=True)
    pwd = os.environ.get("PWD")
    if pwd:
        lexical = lexical_absolute(pwd)
        try:
            if lexical.resolve(strict=True) == real_cwd:
                return lexical
        except FileNotFoundError:
            pass
    return real_cwd


def secure_permissions(path: Path, mode: int) -> None:
    """Apply explicit private POSIX permissions to Agent Team control data."""
    if os.name != "nt":
        os.chmod(path, mode)


def secure_file_descriptor(fd: int) -> None:
    if os.name != "nt":
        os.fchmod(fd, PRIVATE_FILE_MODE)


def reject_tracked_control_dir(workspace: Path) -> None:
    """Refuse to persist task data inside Git's tracked file set."""
    probe = run_git(workspace, "rev-parse", "--is-inside-work-tree")
    if probe is None or probe.returncode != 0 or probe.stdout.strip() != "true":
        return
    tracked = run_git(workspace, "ls-files", "-z", "--", CONTROL_DIR)
    if tracked is not None and tracked.returncode == 0 and tracked.stdout:
        raise StateError(
            f"{CONTROL_DIR}/ is tracked by Git; remove it from the index before "
            "running Agent Team"
        )


def guard_control_root(
    workspace: str | Path, *, create: bool
) -> tuple[Path, Path, Path, Path]:
    workspace_lexical, workspace_real = workspace_paths(workspace)
    control = workspace_lexical / CONTROL_DIR
    runs = control / RUNS_DIR

    for candidate, label in (
        (control, CONTROL_DIR),
        (runs, f"{CONTROL_DIR}/{RUNS_DIR}"),
    ):
        if candidate.is_symlink():
            raise StateError(f"refusing symlinked control directory: {label}")
        if candidate.exists() and not candidate.is_dir():
            raise StateError(f"control path is not a directory: {candidate}")

    if create:
        control.mkdir(mode=PRIVATE_DIRECTORY_MODE, exist_ok=True)
        runs.mkdir(mode=PRIVATE_DIRECTORY_MODE, exist_ok=True)
    elif not runs.is_dir():
        raise StateError(f"Agent Team runs directory does not exist: {runs}")

    control_real = control.resolve(strict=True)
    runs_real = runs.resolve(strict=True)
    if not is_within(control_real, workspace_real) or not is_within(
        runs_real, workspace_real
    ):
        raise StateError("Agent Team control directory escapes the workspace")
    secure_permissions(control, PRIVATE_DIRECTORY_MODE)
    secure_permissions(runs, PRIVATE_DIRECTORY_MODE)
    reject_tracked_control_dir(workspace_lexical)
    return workspace_lexical, workspace_real, control, runs


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        secure_file_descriptor(fd)
    except Exception:
        os.close(fd)
        os.unlink(temporary)
        raise
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        secure_permissions(path, PRIVATE_FILE_MODE)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        secure_file_descriptor(fd)
    except Exception:
        os.close(fd)
        os.unlink(temporary)
        raise
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        secure_permissions(path, PRIVATE_FILE_MODE)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_text_if_changed(path: Path, content: str) -> None:
    if path.is_symlink():
        raise StateError(f"derived run artifact must not be a symlink: {path.name}")
    try:
        if path.read_text(encoding="utf-8") == content:
            secure_permissions(path, PRIVATE_FILE_MODE)
            return
    except FileNotFoundError:
        pass
    atomic_write_text(path, content)


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256_bytes(encoded)


def artifact_snapshot(path: Path) -> tuple[str, int]:
    if path.is_symlink():
        raise StateError(f"run artifact must not be a symlink: {path.name}")
    try:
        content = path.read_bytes()
    except FileNotFoundError as exc:
        raise StateError(f"required run artifact is missing: {path.name}") from exc
    if not path.is_file():
        raise StateError(f"run artifact is not a regular file: {path.name}")
    return sha256_bytes(content), len(content)


def canonical_output_path(path: Path) -> Path:
    return path.resolve(strict=False)


def delivery_target(state: dict[str, Any]) -> Path:
    lexical = Path(state["delivery"]["output_path"])
    actual_real = canonical_output_path(lexical)
    stored_real = state["delivery"].get("output_path_real")
    if not isinstance(stored_real, str) or str(actual_real) != stored_real:
        raise StateError("delivery canonical target changed after approval")
    return Path(stored_real)


def hash_delivery_entry(digest: Any, root: Path, path: Path, kind: str) -> None:
    relative = "." if path == root else path.relative_to(root).as_posix()
    mode = path.lstat().st_mode & 0o777
    digest.update(f"{kind}\0{relative}\0{mode:o}\0".encode())
    if kind == "L":
        digest.update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
    elif kind == "F":
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    digest.update(b"\0")


def delivery_content_digest(state: dict[str, Any]) -> str:
    root = delivery_target(state)
    if root.is_symlink():
        raise StateError("delivery canonical target must not be a symlink")
    if not root.exists():
        raise StateError(f"delivery output does not exist: {root}")

    digest = hashlib.sha256()
    if root.is_file():
        hash_delivery_entry(digest, root, root, "F")
        return digest.hexdigest()
    if not root.is_dir():
        raise StateError(f"delivery output is not a regular file or directory: {root}")

    hash_delivery_entry(digest, root, root, "D")
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        directory_names.sort()
        file_names.sort()
        retained_directories: list[str] = []
        for name in directory_names:
            candidate = current_path / name
            relative = candidate.relative_to(root)
            if (
                state["delivery"]["mode"] == "workspace"
                and len(relative.parts) == 1
                and name in {".git", CONTROL_DIR}
            ):
                continue
            if candidate.is_symlink():
                if state["delivery"]["mode"] == "staged":
                    raise StateError("staged delivery must not contain symlinks")
                hash_delivery_entry(digest, root, candidate, "L")
            else:
                hash_delivery_entry(digest, root, candidate, "D")
                retained_directories.append(name)
        directory_names[:] = retained_directories
        for name in file_names:
            candidate = current_path / name
            if candidate.is_symlink():
                if state["delivery"]["mode"] == "staged":
                    raise StateError("staged delivery must not contain symlinks")
                hash_delivery_entry(digest, root, candidate, "L")
            elif candidate.is_file():
                hash_delivery_entry(digest, root, candidate, "F")
            else:
                raise StateError(
                    f"delivery contains unsupported file type: {candidate}"
                )
    return digest.hexdigest()


def plan_subject(state: dict[str, Any], run_dir: Path) -> dict[str, str]:
    plan_digest, _ = artifact_snapshot(run_dir / "plan.md")
    return {
        "kind": "plan",
        "sha256": sha256_json(
            {
                "task": state["task"],
                "acceptance_criteria": state["acceptance_criteria"],
                "delivery": state["delivery"],
                "plan_sha256": plan_digest,
            }
        ),
    }


def delivery_subject(state: dict[str, Any]) -> dict[str, str]:
    return {
        "kind": "delivery",
        "sha256": sha256_json(
            {
                "delivery": state["delivery"],
                "content_sha256": delivery_content_digest(state),
            }
        ),
    }


def verification_subject(state: dict[str, Any], run_dir: Path) -> dict[str, str]:
    return {
        "kind": "verification",
        "sha256": sha256_json(
            {
                "approved_plan": plan_subject(state, run_dir),
                "delivery": delivery_subject(state),
                "deliverables_sha256": artifact_snapshot(run_dir / "deliverables.md")[
                    0
                ],
                "verify_report_sha256": artifact_snapshot(run_dir / "verify-report.md")[
                    0
                ],
            }
        ),
    }


def final_subject(state: dict[str, Any], run_dir: Path) -> dict[str, str]:
    artifact_digests = {
        name: artifact_snapshot(run_dir / name)[0]
        for name in (
            "final-review.md",
            "risk-log.md",
        )
    }
    return {
        "kind": "final",
        "sha256": sha256_json(
            {
                "verification": verification_subject(state, run_dir),
                "artifacts": artifact_digests,
            }
        ),
    }


def run_git(workspace: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    git_executable = shutil.which("git")
    if git_executable is None:
        return None
    try:
        # No shell is used, so argv items remain data.
        return subprocess.run(  # nosec B603
            [git_executable, "-C", str(workspace), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired as exc:
        raise StateError(
            f"git command timed out in {workspace}: {' '.join(args)}"
        ) from exc


def git_baseline(workspace: Path) -> dict[str, Any]:
    probe = run_git(workspace, "rev-parse", "--is-inside-work-tree")
    if probe is None:
        return {
            "available": False,
            "is_git_repo": False,
            "head": None,
            "status": None,
        }
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        return {
            "available": True,
            "is_git_repo": False,
            "head": None,
            "status": None,
        }

    head = run_git(workspace, "rev-parse", "HEAD")
    status = run_git(
        workspace,
        "status",
        "--short",
        "--",
        ".",
        f":(exclude){CONTROL_DIR}",
        f":(exclude){CONTROL_DIR}/**",
    )
    if head is None or status is None:
        return {
            "available": False,
            "is_git_repo": False,
            "head": None,
            "status": None,
        }
    return {
        "available": True,
        "is_git_repo": True,
        "head": head.stdout.strip() if head.returncode == 0 else None,
        "status": status.stdout.rstrip() if status.returncode == 0 else None,
    }


def resolve_output_path(
    workspace: Path,
    run_dir: Path,
    mode: str,
    output_path: str | None,
) -> Path:
    if mode not in DELIVERY_MODES:
        raise StateError(f"unsupported delivery mode: {mode}")
    if mode == "workspace":
        return workspace
    if mode == "staged":
        staged = run_dir / "deliverable"
        if staged.is_symlink():
            raise StateError("staged deliverable directory must not be a symlink")
        if staged.exists() and not staged.is_dir():
            raise StateError("staged deliverable path must be a directory")
        run_real = run_dir.resolve(strict=False)
        staged_real = staged.resolve(strict=False)
        if staged_real.parent != run_real:
            raise StateError("staged deliverable directory escapes the run")
        return staged
    raise StateError(f"unsupported delivery mode: {mode}")


def unique_run_path(workspace: Path, slug: str, timestamp: str | None = None) -> Path:
    workspace_lexical, _, _, runs = guard_control_root(workspace, create=True)
    stamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}", stamp):
        raise StateError(f"invalid run timestamp: {stamp}")
    base = runs / f"{stamp}-{normalize_slug(slug)}"
    candidate = base
    suffix = 2
    while candidate.exists() or candidate.is_symlink():
        candidate = Path(f"{base}-{suffix}")
        suffix += 1
    if not is_within(
        candidate.resolve(strict=False), workspace_lexical.resolve(strict=True)
    ):
        raise StateError("new run directory escapes the workspace")
    return candidate


def run_candidates(workspace: Path) -> list[Path]:
    _, _, _, runs = guard_control_root(workspace, create=False)
    return sorted(
        candidate
        for candidate in runs.iterdir()
        if candidate.is_dir()
        and RUN_ID_PATTERN.fullmatch(candidate.name)
        and (candidate / "state.json").is_file()
    )


def load_usable_run(candidate: Path) -> dict[str, Any] | None:
    try:
        return load_state(candidate)
    except (OSError, StateError):
        return None


def reject_existing_active_run(workspace: Path) -> None:
    for candidate in run_candidates(workspace):
        current = load_usable_run(candidate)
        if current is not None and current["status"] != DONE:
            raise StateError(
                f"workspace already has an active Agent Team run: {candidate.name}"
            )


def cleanup_incomplete_runs(runs: Path) -> None:
    for candidate in runs.iterdir():
        if not candidate.name.startswith(INIT_TEMP_PREFIX) or candidate.is_symlink():
            continue
        if candidate.is_dir():
            shutil.rmtree(candidate)


def render_task_markdown(state: dict[str, Any]) -> str:
    criteria_markdown = "\n".join(
        f"{index}. {item}"
        for index, item in enumerate(state["acceptance_criteria"], start=1)
    )
    baseline = state["git_baseline"]
    baseline_status = baseline.get("status")
    baseline_text = baseline_status if baseline_status else "clean or not applicable"
    return (
        "# Task\n\n"
        f"{state['task']}\n\n"
        "## Acceptance criteria\n\n"
        f"{criteria_markdown}\n\n"
        "## Delivery\n\n"
        f"- Mode: `{state['delivery']['mode']}`\n"
        f"- Requested output path: `{state['delivery']['output_path']}`\n"
        f"- Canonical output path: `{state['delivery']['output_path_real']}`\n\n"
        "## Git baseline\n\n"
        f"- Git available: `{baseline['available']}`\n"
        f"- Repository: `{baseline['is_git_repo']}`\n"
        f"- HEAD: `{baseline.get('head') or 'n/a'}`\n"
        f"- Existing status:\n\n```text\n{baseline_text}\n```\n"
    )


def render_risk_log(state: dict[str, Any]) -> str:
    sections = ["# Risk log\n"]
    for item in state["risks"]:
        sections.append(
            f"\n## {item['id']} — {item['status'].upper()}\n\n"
            f"- Risk: {item['text']}\n"
            f"- Created: {item['created_at']} by {item['created_by']}\n\n"
            "### History\n"
        )
        for event in item["events"]:
            previous = event["from"] or "new"
            line = (
                f"\n- {event['at']} — {event['source']}: "
                f"`{previous} → {event['to']}` — {event['detail']}"
            )
            if event["evidence"]:
                line += f" Evidence: {event['evidence']}"
            sections.append(line + "\n")
    return "".join(sections)


def sync_derived_artifacts(run_dir: Path, state: dict[str, Any]) -> None:
    write_text_if_changed(run_dir / "task.md", render_task_markdown(state))
    write_text_if_changed(run_dir / "risk-log.md", render_risk_log(state))


def state_path(run_dir: Path) -> Path:
    path = run_dir / "state.json"
    if path.is_symlink():
        raise StateError("state.json must not be a symlink")
    if path.exists():
        if not path.is_file():
            raise StateError("state.json must be a regular file")
        secure_permissions(path, PRIVATE_FILE_MODE)
    return path


def validate_run_path(run_dir: str | Path, *, require_state: bool) -> tuple[Path, Path]:
    lexical = lexical_absolute(run_dir)
    if lexical.is_symlink():
        raise StateError("run directory must not be a symlink")
    try:
        real = lexical.resolve(strict=True)
    except FileNotFoundError as exc:
        raise StateError(f"run directory does not exist: {lexical}") from exc
    if not real.is_dir():
        raise StateError(f"run path is not a directory: {lexical}")
    secure_permissions(lexical, PRIVATE_DIRECTORY_MODE)
    if lexical.parent.name != RUNS_DIR or lexical.parent.parent.name != CONTROL_DIR:
        raise StateError(f"run directory is outside {CONTROL_DIR}/{RUNS_DIR}")
    workspace = lexical.parent.parent.parent
    _, workspace_real, _, runs = guard_control_root(workspace, create=False)
    if real.parent != runs.resolve(strict=True) or not is_within(real, workspace_real):
        raise StateError("run directory escapes the workspace control root")
    if require_state:
        path = state_path(lexical)
        if not path.is_file():
            raise StateError(f"state.json is missing from run: {lexical}")
    return lexical, real


@contextmanager
def exclusive_file_lock(lock_path: Path, label: str) -> Iterator[None]:
    if lock_path.is_symlink():
        raise StateError(f"{label} must not be a symlink")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(lock_path, flags, 0o600)
    try:
        secure_file_descriptor(fd)
    except Exception:
        os.close(fd)
        raise
    with os.fdopen(fd, "a+b", buffering=0) as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        else:  # pragma: no cover - Windows fallback
            if handle.tell() == 0:
                handle.write(b"0")
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            else:  # pragma: no cover - Windows fallback
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def state_lock(run_dir: Path) -> Iterator[None]:
    with exclusive_file_lock(run_dir / LOCK_FILE, "state lock"):
        yield


def require_exact_type(value: Any, expected: type, label: str) -> None:
    if type(value) is not expected:
        raise StateError(f"invalid {label} in state.json")


def validate_subject_shape(subject: Any, kind: str, label: str) -> None:
    if (
        not isinstance(subject, dict)
        or set(subject) != {"kind", "sha256"}
        or subject.get("kind") != kind
        or not isinstance(subject.get("sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", subject["sha256"])
    ):
        raise StateError(f"{label} subject is invalid")


def validate_test_evidence_items(
    items: Any, state: dict[str, Any], *, label: str
) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        raise StateError(f"{label} must be a list")
    known_risks = {item["id"] for item in state["risks"]}
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    required = {
        "id",
        "description",
        "kind",
        "critical",
        "persisted",
        "target",
        "risk_ids",
    }
    for index, item in enumerate(items):
        if not isinstance(item, dict) or set(item) != required:
            raise StateError(f"invalid {label} item at index {index}")
        evidence_id = item["id"]
        if (
            not isinstance(evidence_id, str)
            or not TEST_EVIDENCE_ID_PATTERN.fullmatch(evidence_id)
            or evidence_id in seen_ids
        ):
            raise StateError(f"invalid or duplicate {label} id at index {index}")
        if (
            not isinstance(item["description"], str)
            or not item["description"].strip()
            or not isinstance(item["kind"], str)
            or item["kind"] not in TEST_EVIDENCE_KINDS
            or type(item["critical"]) is not bool
            or type(item["persisted"]) is not bool
            or not isinstance(item["target"], str)
            or not isinstance(item["risk_ids"], list)
            or any(
                not isinstance(risk_id, str) or risk_id not in known_risks
                for risk_id in item["risk_ids"]
            )
        ):
            raise StateError(f"invalid {label} item at index {index}")
        if item["kind"] == "formal" and (
            not item["persisted"] or not item["target"].strip()
        ):
            raise StateError("formal test evidence must be persisted with a target")
        if item["kind"] == "ad_hoc" and item["persisted"]:
            raise StateError("ad hoc test evidence cannot claim persistence")
        seen_ids.add(evidence_id)
        normalized.append(
            {
                "id": evidence_id,
                "description": item["description"].strip(),
                "kind": item["kind"],
                "critical": item["critical"],
                "persisted": item["persisted"],
                "target": item["target"].strip(),
                "risk_ids": list(item["risk_ids"]),
            }
        )
    return normalized


def validate_execution_iteration_items(items: Any) -> list[dict[str, str]]:
    if not isinstance(items, list):
        raise StateError("execution_iterations must be a list")
    required = {"trigger", "failed_check", "fix", "validation"}
    normalized: list[dict[str, str]] = []
    for index, item in enumerate(items):
        if (
            not isinstance(item, dict)
            or set(item) != required
            or any(
                not isinstance(item[key], str) or not item[key].strip()
                for key in required
            )
        ):
            raise StateError(f"invalid execution iteration at index {index}")
        normalized.append({key: item[key].strip() for key in required})
    return normalized


def validate_completion_input(value: Any) -> dict[str, Any]:
    required = {"summary", "completed", "excluded", "deployment_status"}
    if not isinstance(value, dict) or set(value) != required:
        raise StateError("final approval requires a complete completion_scope")
    if not isinstance(value["summary"], str) or not value["summary"].strip():
        raise StateError("completion_scope summary must not be empty")
    for key in ("completed", "excluded"):
        if (
            not isinstance(value[key], list)
            or not value[key]
            or any(not isinstance(item, str) or not item.strip() for item in value[key])
        ):
            raise StateError(f"completion_scope {key} must be a non-empty string list")
    if (
        not isinstance(value["deployment_status"], str)
        or value["deployment_status"] not in DEPLOYMENT_STATUSES
    ):
        raise StateError("completion_scope deployment_status is invalid")
    return {
        "summary": value["summary"].strip(),
        "completed": [item.strip() for item in value["completed"]],
        "excluded": [item.strip() for item in value["excluded"]],
        "deployment_status": value["deployment_status"],
    }


def validate_stored_completion_scope(state: dict[str, Any], value: Any) -> None:
    required = {
        "completed_at",
        "summary",
        "completed",
        "excluded",
        "deployment_status",
        "delivery_mode",
        "output_path",
        "open_risk_ids",
        "approved_plan_sha256",
        "verified_subject_sha256",
        "final_subject_sha256",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise StateError("DONE state has an invalid completion scope")
    normalized = validate_completion_input(
        {
            "summary": value["summary"],
            "completed": value["completed"],
            "excluded": value["excluded"],
            "deployment_status": value["deployment_status"],
        }
    )
    if (
        not isinstance(value["completed_at"], str)
        or not value["completed_at"]
        or value["delivery_mode"] != state["delivery"]["mode"]
        or value["output_path"] != state["delivery"]["output_path_real"]
        or value["summary"] != normalized["summary"]
        or value["completed"] != normalized["completed"]
        or value["excluded"] != normalized["excluded"]
    ):
        raise StateError(
            "DONE completion scope contradicts delivery or normalized input"
        )
    open_risk_ids = [
        item["id"] for item in state["risks"] if item["status"] != "closed"
    ]
    if value["open_risk_ids"] != open_risk_ids:
        raise StateError("DONE completion scope contradicts open risks")
    approved = state.get("approved_plan_subject") or {}
    verified = state.get("verified_subject") or {}
    if (
        value["approved_plan_sha256"] != approved.get("sha256")
        or value["verified_subject_sha256"] != verified.get("sha256")
        or not isinstance(value["final_subject_sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", value["final_subject_sha256"])
    ):
        raise StateError("DONE completion scope has invalid evidence hashes")


def validate_state_shape(state: dict[str, Any]) -> None:
    required_types = {
        "schema_version": int,
        "run_id": str,
        "workspace": str,
        "workspace_real": str,
        "run_dir": str,
        "run_dir_real": str,
        "status": str,
        "task": str,
        "acceptance_criteria": list,
        "delivery": dict,
        "planning_round": int,
        "planning_cycle": int,
        "verification_round": int,
        "verification_cycle": int,
        "executor_dispatch_count": int,
        "executor_repair_dispatch_count": int,
        "executor_internal_repair_count": int,
        "execution_iterations": list,
        "pending_test_persistence": list,
        "risks": list,
        "git_baseline": dict,
        "history": list,
        "processed_requests": list,
    }
    for key, expected_type in required_types.items():
        if key not in state:
            raise StateError(f"state.json is missing required field: {key}")
        require_exact_type(state[key], expected_type, key)
    for key in (
        "pending_checkpoint",
        "last_marker",
        "expected",
        "blocked",
        "approved_plan_subject",
        "verified_subject",
        "executor_mode",
        "completion_scope",
        "created_at",
        "updated_at",
    ):
        if key not in state:
            raise StateError(f"state.json is missing required field: {key}")
    if not isinstance(state["created_at"], str) or not isinstance(
        state["updated_at"], str
    ):
        raise StateError("state timestamps are invalid")
    if state["approved_plan_subject"] is not None:
        validate_subject_shape(state["approved_plan_subject"], "plan", "approved plan")
    if state["verified_subject"] is not None:
        validate_subject_shape(state["verified_subject"], "verification", "verified")
    if state["schema_version"] != SCHEMA_VERSION:
        raise StateError(f"unsupported state schema: {state['schema_version']!r}")
    if state["status"] not in ALL_STATES:
        raise StateError(f"unknown status in state.json: {state['status']!r}")
    if not state["task"].strip():
        raise StateError("state task must not be empty")
    if not state["acceptance_criteria"] or any(
        not isinstance(item, str) or not item.strip()
        for item in state["acceptance_criteria"]
    ):
        raise StateError("state acceptance criteria are invalid")
    if (
        state["delivery"].get("mode") not in DELIVERY_MODES
        or not isinstance(state["delivery"].get("output_path"), str)
        or not isinstance(state["delivery"].get("output_path_real"), str)
    ):
        raise StateError("state delivery is invalid")
    for key in (
        "planning_round",
        "planning_cycle",
        "verification_round",
        "verification_cycle",
        "executor_dispatch_count",
        "executor_repair_dispatch_count",
        "executor_internal_repair_count",
    ):
        if state[key] < 0:
            raise StateError(f"state counter must not be negative: {key}")
    if state["planning_cycle"] < 1 or state["verification_cycle"] < 1:
        raise StateError("state cycle counters must start at one")
    if state["planning_round"] > PLANNING_LIMIT:
        raise StateError("state planning round exceeds its hard limit")
    if state["verification_round"] > VERIFICATION_LIMIT:
        raise StateError("state verification round exceeds its hard limit")
    if state["executor_repair_dispatch_count"] > state["executor_dispatch_count"]:
        raise StateError("executor repair dispatch count exceeds total dispatches")
    if state["executor_mode"] is not None and (
        not isinstance(state["executor_mode"], str)
        or state["executor_mode"] not in EXECUTOR_MODES
    ):
        raise StateError("state executor mode is invalid")
    if state["status"] == EXECUTING and state["executor_mode"] is None:
        raise StateError("executing state is missing executor mode")
    if (
        state["status"] not in {EXECUTING, BLOCKED}
        and state["executor_mode"] is not None
    ):
        raise StateError("non-executing state contains executor mode")
    if not isinstance(
        state.get("git_baseline", {}).get("available"), bool
    ) or not isinstance(state.get("git_baseline", {}).get("is_git_repo"), bool):
        raise StateError("state Git baseline is invalid")
    if any(
        value is not None and not isinstance(value, str)
        for value in (
            state["git_baseline"].get("head"),
            state["git_baseline"].get("status"),
        )
    ):
        raise StateError("state Git baseline details are invalid")
    if state.get("pending_checkpoint") not in {None, "plan", "final"}:
        raise StateError("state pending checkpoint is invalid")
    if state.get("last_marker") is not None and state["last_marker"] not in MARKERS:
        raise StateError("state last marker is invalid")
    expected = state.get("expected")
    if expected is not None:
        validate_expectation_shape(state, expected, exact_token=True)
    expected_roles: dict[str, set[str | None]] = {
        INITIALIZED: {None},
        PLANNING: {"planner", "plan_reviewer"},
        AWAITING_PLAN_APPROVAL: {"human_plan"},
        EXECUTING: {"executor"},
        VERIFYING: {"verifier"},
        FINAL_REVIEW: {"planner_final"},
        AWAITING_FINAL_APPROVAL: {"human_final"},
        DONE: {None},
        BLOCKED: {None},
    }
    expected_role = None if expected is None else expected.get("role")
    if expected_role not in expected_roles[state["status"]]:
        raise StateError(
            f"state expectation {expected_role!r} contradicts status {state['status']}"
        )
    required_checkpoint = {
        AWAITING_PLAN_APPROVAL: "plan",
        AWAITING_FINAL_APPROVAL: "final",
    }.get(state["status"])
    if state["pending_checkpoint"] != required_checkpoint:
        raise StateError("pending checkpoint contradicts the current status")
    if state["status"] == BLOCKED:
        if not isinstance(state.get("blocked"), dict):
            raise StateError("blocked state is missing recovery metadata")
        blocked = state["blocked"]
        if blocked.get("from") not in ACTIVE_STATES:
            raise StateError("blocked state has an invalid origin")
        if not isinstance(blocked.get("reason"), str) or not blocked["reason"].strip():
            raise StateError("blocked state has no reason")
        if not isinstance(blocked.get("at"), str) or not isinstance(
            blocked.get("event"), str
        ):
            raise StateError("blocked state has invalid recovery metadata")
        if blocked.get("expected") is not None:
            validate_expectation_shape(
                state,
                blocked["expected"],
                exact_token=blocked["event"]
                not in {"PLANNING_LIMIT_REACHED", "VERIFICATION_LIMIT_REACHED"},
            )
    elif state.get("blocked") is not None:
        raise StateError("non-blocked state contains blocked metadata")
    seen_risk_ids: set[str] = set()
    for index, item in enumerate(state["risks"]):
        if not isinstance(item, dict) or set(item) != {
            "id",
            "text",
            "created_at",
            "created_by",
            "status",
            "events",
        }:
            raise StateError(f"invalid risk item at index {index}")
        if (
            not isinstance(item["id"], str)
            or not RISK_ID_PATTERN.fullmatch(item["id"])
            or item["id"] in seen_risk_ids
            or not isinstance(item["text"], str)
            or not item["text"].strip()
            or not isinstance(item["created_at"], str)
            or not item["created_at"]
            or not isinstance(item["created_by"], str)
            or not item["created_by"].strip()
            or not isinstance(item["status"], str)
            or item["status"] not in RISK_STATUSES
            or not isinstance(item["events"], list)
            or not item["events"]
        ):
            raise StateError(f"invalid risk item at index {index}")
        previous: str | None = None
        for event_index, event in enumerate(item["events"]):
            if not isinstance(event, dict) or set(event) != {
                "at",
                "source",
                "from",
                "to",
                "detail",
                "evidence",
            }:
                raise StateError(f"invalid risk event {event_index} for {item['id']}")
            if (
                not isinstance(event["at"], str)
                or not event["at"]
                or not isinstance(event["source"], str)
                or not event["source"].strip()
                or event["from"] != previous
                or not isinstance(event["to"], str)
                or event["to"] not in RISK_STATUSES
                or not isinstance(event["detail"], str)
                or not event["detail"].strip()
                or not isinstance(event["evidence"], str)
            ):
                raise StateError(f"invalid risk event {event_index} for {item['id']}")
            if event_index == 0:
                if event["from"] is not None or event["to"] != "open":
                    raise StateError(f"risk {item['id']} must start open")
                if (
                    event["at"] != item["created_at"]
                    or event["source"] != item["created_by"]
                ):
                    raise StateError(
                        f"risk {item['id']} creation event is inconsistent"
                    )
            elif event["to"] not in RISK_TRANSITIONS[cast(str, previous)]:
                raise StateError(
                    f"invalid risk history transition for {item['id']}: "
                    f"{previous} -> {event['to']}"
                )
            previous = event["to"]
        if previous != item["status"]:
            raise StateError(f"risk history does not match status for {item['id']}")
        seen_risk_ids.add(item["id"])
    pending = validate_test_evidence_items(
        state["pending_test_persistence"], state, label="pending_test_persistence"
    )
    if any(item["kind"] != "ad_hoc" or not item["critical"] for item in pending):
        raise StateError("pending test persistence must contain critical ad hoc tests")
    recorded_internal_repairs = 0
    recorded_dispatches: set[int] = set()
    for index, record in enumerate(state["execution_iterations"]):
        if not isinstance(record, dict) or set(record) != {
            "at",
            "dispatch",
            "mode",
            "items",
        }:
            raise StateError(f"invalid execution iteration record at index {index}")
        if (
            not isinstance(record["at"], str)
            or not record["at"]
            or type(record["dispatch"]) is not int
            or record["dispatch"] < 1
            or record["dispatch"] > state["executor_dispatch_count"]
            or not isinstance(record["mode"], str)
            or record["mode"] not in EXECUTOR_MODES
            or record["dispatch"] in recorded_dispatches
        ):
            raise StateError(f"invalid execution iteration record at index {index}")
        recorded_dispatches.add(record["dispatch"])
        recorded_internal_repairs += len(
            validate_execution_iteration_items(record["items"])
        )
    if recorded_internal_repairs != state["executor_internal_repair_count"]:
        raise StateError("executor internal repair count contradicts iteration records")
    completion = state["completion_scope"]
    if state["status"] == DONE:
        validate_stored_completion_scope(state, completion)
    elif completion is not None:
        raise StateError("completion scope is allowed only for DONE")
    seen_request_ids: set[str] = set()
    for index, item in enumerate(state["processed_requests"]):
        if not isinstance(item, dict) or set(item) != {
            "id",
            "command",
            "digest",
            "at",
        }:
            raise StateError(f"invalid processed request at index {index}")
        if (
            not isinstance(item["id"], str)
            or not REQUEST_ID_PATTERN.fullmatch(item["id"])
            or not isinstance(item["command"], str)
            or not item["command"]
            or not isinstance(item["digest"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", item["digest"])
            or not isinstance(item["at"], str)
            or not item["at"]
        ):
            raise StateError(f"invalid processed request at index {index}")
        if item["id"] in seen_request_ids:
            raise StateError("state contains a duplicate processed request id")
        seen_request_ids.add(item["id"])
    if not state["history"]:
        raise StateError("state history must not be empty")
    for index, item in enumerate(state["history"]):
        if not isinstance(item, dict):
            raise StateError(f"state history item {index} is not an object")
        if not all(
            isinstance(item.get(key), str) and item[key].strip()
            for key in ("at", "event", "to")
        ) or not isinstance(item.get("detail"), str):
            raise StateError(f"state history item {index} is invalid")
        if item["to"] not in ALL_STATES or item.get("from") not in ALL_STATES | {None}:
            raise StateError(f"state history item {index} has an invalid state")
    if state["history"][-1]["to"] != state["status"]:
        raise StateError("state history does not end at the current status")


def validate_expectation_shape(
    state: dict[str, Any], expected: dict[str, Any], *, exact_token: bool
) -> None:
    if not isinstance(expected, dict) or not isinstance(expected.get("role"), str):
        raise StateError("state expectation is invalid")
    role = expected["role"]
    if role in {"human_plan", "human_final"}:
        if expected.get("markers") != [] or any(
            expected.get(key) is not None
            for key in (
                "artifact",
                "artifact_sha256",
                "artifact_size",
                "artifact_token",
            )
        ):
            raise StateError("human expectation must not contain role artifact data")
        if expected.get("append_only") is not False:
            raise StateError("human expectation append-only flag is invalid")
    else:
        if role not in ROLE_MARKERS:
            raise StateError(f"unknown role in state expectation: {role}")
        if expected.get("markers") != list(ROLE_MARKERS[role]):
            raise StateError("state expectation markers do not match the role")
        if expected.get("artifact") != ROLE_ARTIFACTS[role]:
            raise StateError("state expectation artifact does not match the role")
        digest = expected.get("artifact_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise StateError("state expectation artifact hash is invalid")
        if (
            not isinstance(expected.get("artifact_size"), int)
            or expected["artifact_size"] < 0
        ):
            raise StateError("state expectation artifact size is invalid")
        if expected.get("append_only") is not (role in APPEND_ONLY_ROLES):
            raise StateError("state expectation append-only flag is invalid")
        token = expected.get("artifact_token")
        if not isinstance(token, str):
            raise StateError("state expectation artifact token is invalid")
        if exact_token and token != artifact_token_template(state, role):
            raise StateError(
                "state expectation artifact token contradicts the counters"
            )

    expected_subject_kind = {
        "plan_reviewer": "plan",
        "human_plan": "plan",
        "planner_final": "verification",
        "human_final": "final",
    }.get(role)
    subject = expected.get("subject")
    if expected_subject_kind is None:
        if subject is not None:
            raise StateError("state expectation has an unexpected subject")
    else:
        validate_subject_shape(subject, expected_subject_kind, "state expectation")


def _load_state_unlocked(run_dir: Path, run_real: Path) -> dict[str, Any]:
    path = state_path(run_dir)
    try:
        with path.open(encoding="utf-8") as handle:
            state = json.load(handle)
    except json.JSONDecodeError as exc:
        raise StateError(f"state.json is invalid JSON: {exc}") from exc
    if not isinstance(state, dict):
        raise StateError("state.json must contain an object")
    validate_state_shape(state)
    if state["run_id"] != run_dir.name:
        raise StateError("state run_id does not match the actual run directory")
    try:
        stored_run_real = Path(state["run_dir_real"]).resolve(strict=True)
        stored_workspace_real = Path(state["workspace_real"]).resolve(strict=True)
    except FileNotFoundError as exc:
        raise StateError("stored workspace or run directory no longer exists") from exc
    if stored_run_real != run_real:
        raise StateError("state run_dir does not match the actual run directory")
    actual_workspace_real = run_real.parent.parent.parent
    if stored_workspace_real != actual_workspace_real:
        raise StateError("state workspace does not match the actual control root")
    if Path(state["workspace"]).resolve(strict=True) != stored_workspace_real:
        raise StateError("state lexical workspace resolves to a different directory")
    if Path(state["run_dir"]).resolve(strict=True) != run_real:
        raise StateError("state lexical run_dir resolves to a different directory")
    expected_output = resolve_output_path(
        Path(state["workspace"]),
        run_dir,
        state["delivery"]["mode"],
        (
            None
            if state["delivery"]["mode"] in {"workspace", "staged"}
            else state["delivery"]["output_path"]
        ),
    )
    if expected_output.resolve(strict=False) != Path(
        state["delivery"]["output_path"]
    ).resolve(strict=False):
        raise StateError("state delivery output path contradicts its mode")
    if (
        str(canonical_output_path(expected_output))
        != state["delivery"]["output_path_real"]
    ):
        raise StateError("delivery canonical target changed after approval")
    return state


def load_state(run_dir: str | Path) -> dict[str, Any]:
    run_path, run_real = validate_run_path(run_dir, require_state=True)
    with state_lock(run_path):
        state = _load_state_unlocked(run_path, run_real)
        if reconcile_active_run(run_path, state):
            save_state_unlocked(run_path, state)
        else:
            sync_derived_artifacts(run_path, state)
        return state


def show_run(run_dir: str | Path, request_id: str, request_hash: str) -> dict[str, Any]:
    run_path, run_real = validate_run_path(run_dir, require_state=True)
    with state_lock(run_path):
        state = _load_state_unlocked(run_path, run_real)
        reconcile_active_run(run_path, state)
        record_processed_request(state, request_id, "show", request_hash)
        save_state_unlocked(run_path, state)
        return state


def save_state_unlocked(run_dir: Path, state: dict[str, Any]) -> None:
    validate_state_shape(state)
    state["updated_at"] = utc_now()
    sync_derived_artifacts(run_dir, state)
    atomic_write_json(state_path(run_dir), state)


def validate_request_id(request_id: str) -> None:
    if not isinstance(request_id, str) or not REQUEST_ID_PATTERN.fullmatch(request_id):
        raise StateError(
            "request_id must match req- followed by 32 lowercase hex digits"
        )


def request_digest(request: dict[str, Any]) -> str:
    return sha256_json(request)


def record_processed_request(
    state: dict[str, Any],
    request_id: str | None,
    command: str,
    digest: str | None,
) -> None:
    if request_id is None:
        return
    validate_request_id(request_id)
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise StateError("request digest is required for an idempotent state change")
    for item in state["processed_requests"]:
        if item["id"] == request_id:
            if item["command"] != command or item["digest"] != digest:
                raise StateError("request_id was already used for a different payload")
            return
    state["processed_requests"].append(
        {"id": request_id, "command": command, "digest": digest, "at": utc_now()}
    )


def record_history(
    state: dict[str, Any],
    event: str,
    previous: str,
    target: str,
    detail: str = "",
) -> None:
    state["history"].append(
        {
            "at": utc_now(),
            "event": event,
            "from": previous,
            "to": target,
            "detail": detail,
        }
    )


def set_status(
    state: dict[str, Any], target: str, event: str, detail: str = ""
) -> None:
    previous = state["status"]
    state["status"] = target
    state["pending_checkpoint"] = (
        "plan"
        if target == AWAITING_PLAN_APPROVAL
        else "final"
        if target == AWAITING_FINAL_APPROVAL
        else None
    )
    record_history(state, event, previous, target, detail)


def artifact_token_template(state: dict[str, Any], role: str) -> str:
    if role in {"planner", "plan_reviewer"}:
        cycle = state["planning_cycle"]
        round_number = state["planning_round"] + 1
    elif role in {"executor", "verifier"}:
        cycle = state["verification_cycle"]
        round_number = state["verification_round"] + 1
    elif role == "planner_final":
        cycle = state["verification_cycle"]
        round_number = state["verification_round"]
    else:
        raise StateError(f"role does not own an artifact: {role}")
    kind = {
        "planner": "plan",
        "plan_reviewer": "review",
        "executor": "execution",
        "verifier": "verification",
        "planner_final": "final-review",
    }[role]
    return (
        f"<!-- agent-team:{kind} cycle={cycle} round={round_number} marker=<MARKER> -->"
    )


def expected_subject(
    state: dict[str, Any], run_dir: Path, role: str
) -> dict[str, str] | None:
    if role in {"plan_reviewer", "human_plan"}:
        return plan_subject(state, run_dir)
    if role == "planner_final":
        return verification_subject(state, run_dir)
    if role == "human_final":
        return final_subject(state, run_dir)
    return None


def make_expectation(state: dict[str, Any], run_dir: Path, role: str) -> dict[str, Any]:
    if role in {"human_plan", "human_final"}:
        empty_evidence_value: str | None = None
        return {
            "role": role,
            "markers": [],
            "artifact": None,
            "artifact_sha256": None,
            "artifact_size": None,
            "artifact_token": empty_evidence_value,
            "append_only": False,
            "subject": expected_subject(state, run_dir, role),
        }
    if role not in ROLE_MARKERS:
        raise StateError(f"unknown expected role: {role}")
    artifact = ROLE_ARTIFACTS[role]
    digest, size = artifact_snapshot(run_dir / artifact)
    return {
        "role": role,
        "markers": list(ROLE_MARKERS[role]),
        "artifact": artifact,
        "artifact_sha256": digest,
        "artifact_size": size,
        "artifact_token": artifact_token_template(state, role),
        "append_only": role in APPEND_ONLY_ROLES,
        "subject": expected_subject(state, run_dir, role),
    }


def set_expectation(state: dict[str, Any], run_dir: Path, role: str | None) -> None:
    state["expected"] = None if role is None else make_expectation(state, run_dir, role)


def schedule_executor(state: dict[str, Any], run_dir: Path, mode: str) -> None:
    if mode not in EXECUTOR_MODES:
        raise StateError(f"invalid executor mode: {mode}")
    state["executor_dispatch_count"] += 1
    if mode == "repair":
        state["executor_repair_dispatch_count"] += 1
    state["executor_mode"] = mode
    set_expectation(state, run_dir, "executor")


def subject_is_current(
    run_dir: Path, state: dict[str, Any], role: str, subject: Any
) -> bool:
    try:
        return bool(expected_subject(state, run_dir, role) == subject)
    except StateError:
        return False


def approved_plan_is_current(run_dir: Path, state: dict[str, Any]) -> bool:
    approved = state.get("approved_plan_subject")
    if approved is None:
        return True
    try:
        return bool(plan_subject(state, run_dir) == approved)
    except StateError:
        return False


def restore_missing_artifact(path: Path, default_content: str) -> None:
    if path.is_symlink():
        raise StateError(f"run artifact must not be a symlink: {path.name}")
    if path.exists():
        if not path.is_file():
            raise StateError(f"run artifact is not a regular file: {path.name}")
        return
    atomic_write_text(path, default_content)


def restore_missing_role_artifacts(run_dir: Path) -> set[str]:
    default_content = {
        "planner": "# Plan\n\nPending.\n",
        "plan_reviewer": "# Plan review log\n",
        "executor": "# Deliverables\n\nPending.\n",
        "verifier": "# Verification report\n",
        "planner_final": "# Final review log\n",
    }
    restored: set[str] = set()
    for role, artifact in ROLE_ARTIFACTS.items():
        path = run_dir / artifact
        missing = not path.exists()
        restore_missing_artifact(path, default_content[role])
        if missing:
            restored.add(role)
    return restored


def restart_planning(
    state: dict[str, Any], run_dir: Path, event: str, detail: str
) -> None:
    restore_missing_artifact(run_dir / "plan.md", "# Plan\n\nPending.\n")
    restore_missing_artifact(run_dir / "review-log.md", "# Plan review log\n")
    state["planning_cycle"] += 1
    state["planning_round"] = 0
    state["verification_round"] = 0
    state["approved_plan_subject"] = None
    state["verified_subject"] = None
    state["pending_test_persistence"] = []
    state["executor_mode"] = None
    set_status(state, PLANNING, event, detail)
    set_expectation(state, run_dir, "planner")


def restart_verification(
    state: dict[str, Any], run_dir: Path, event: str, detail: str
) -> None:
    restore_missing_artifact(
        run_dir / "deliverables.md", "# Deliverables\n\nPending.\n"
    )
    restore_missing_artifact(run_dir / "verify-report.md", "# Verification report\n")
    restore_missing_artifact(run_dir / "final-review.md", "# Final review log\n")
    state["verification_cycle"] += 1
    state["verification_round"] = 0
    state["verified_subject"] = None
    set_status(state, EXECUTING, event, detail)
    schedule_executor(state, run_dir, "repair")


def reconcile_recorded_subjects(run_dir: Path, state: dict[str, Any]) -> bool:
    if state["status"] not in ACTIVE_STATES:
        return False
    if not approved_plan_is_current(run_dir, state):
        restart_planning(
            state,
            run_dir,
            "APPROVED_PLAN_CHANGED",
            "approved plan changed before the next role dispatch",
        )
        return True

    expected = state.get("expected") or {}
    role = expected.get("role")
    if role in {"plan_reviewer", "human_plan"} and not subject_is_current(
        run_dir, state, role, expected.get("subject")
    ):
        restart_planning(
            state,
            run_dir,
            "REVIEWED_PLAN_CHANGED",
            "reviewed plan changed before the next dispatch or approval",
        )
        return True
    if role == "planner_final" and not subject_is_current(
        run_dir, state, role, expected.get("subject")
    ):
        restart_verification(
            state,
            run_dir,
            "VERIFIED_EVIDENCE_CHANGED",
            "delivery or verification evidence changed after PASS",
        )
        return True
    if role == "human_final" and not subject_is_current(
        run_dir, state, role, expected.get("subject")
    ):
        restart_verification(
            state,
            run_dir,
            "FINAL_EVIDENCE_CHANGED",
            "delivery or final evidence changed before checkpoint B",
        )
        return True
    return False


def reconcile_active_run(run_dir: Path, state: dict[str, Any]) -> bool:
    if state["status"] not in ACTIVE_STATES:
        return False
    sync_derived_artifacts(run_dir, state)
    restored_roles = restore_missing_role_artifacts(run_dir)
    if reconcile_recorded_subjects(run_dir, state):
        return True
    expected = state.get("expected") or {}
    role = expected.get("role")
    history_dependencies: dict[tuple[str, str], tuple[str, str, str]] = {
        ("planner", "NEEDS_REVISION"): (
            "plan_reviewer",
            "review-log.md",
            "plan-review feedback",
        ),
        ("executor", "FAIL"): (
            "verifier",
            "verify-report.md",
            "verification failure evidence",
        ),
        ("executor", "NEEDS_TEST_PERSISTENCE"): (
            "verifier",
            "verify-report.md",
            "test-persistence requirement evidence",
        ),
        ("executor", "FINAL_REJECT"): (
            "planner_final",
            "final-review.md",
            "final-rejection evidence",
        ),
    }
    last_marker = state.get("last_marker")
    missing_history = (
        history_dependencies.get((role, last_marker))
        if isinstance(role, str) and isinstance(last_marker, str)
        else None
    )
    if missing_history is not None and missing_history[0] in restored_roles:
        _, artifact, evidence = missing_history
        block_state(
            state,
            f"{artifact} was missing after the prior decision; "
            f"confirm how to recover the lost {evidence}",
            event="REQUIRED_HISTORY_MISSING",
        )
        return True
    if role in restored_roles:
        set_expectation(state, run_dir, role)
    return bool(restored_roles)


def validate_expected_subject(
    run_dir: Path, state: dict[str, Any], expected: dict[str, Any]
) -> None:
    subject = expected.get("subject")
    if subject is None:
        return
    role = expected["role"]
    current = expected_subject(state, run_dir, role)
    if current == subject:
        return
    message = {
        "plan_reviewer": "plan changed after Planner handoff",
        "human_plan": "approved plan changed since review",
        "planner_final": "delivery or verification evidence changed after independent verification",
        "human_final": "approved delivery changed since final review",
    }[role]
    raise StateError(message)


def validate_role_and_artifact(
    run_dir: Path,
    state: dict[str, Any],
    role: str | None,
    marker: str,
) -> None:
    expected = state.get("expected")
    if not isinstance(expected, dict):
        raise StateError("no role result is expected in the current state")
    expected_role = expected.get("role")
    if not isinstance(expected_role, str):
        raise StateError("expected role is invalid")
    if role != expected_role:
        raise StateError(f"expected role {expected_role}, got {role or 'none'}")
    if marker not in expected["markers"]:
        raise StateError(
            f"marker {marker} is not allowed for expected role {expected_role}"
        )
    if marker == "BLOCKED":
        return

    validate_expected_subject(run_dir, state, expected)

    artifact_name = expected["artifact"]
    if not isinstance(artifact_name, str):
        raise StateError("expected role has no evidence artifact")
    artifact_path = run_dir / artifact_name
    if artifact_path.is_symlink() or artifact_path.resolve(
        strict=True
    ).parent != run_dir.resolve(strict=True):
        raise StateError(f"invalid evidence artifact path: {artifact_name}")
    content = artifact_path.read_bytes()
    current_digest = sha256_bytes(content)
    baseline_digest = expected["artifact_sha256"]
    baseline_size = expected["artifact_size"]
    if current_digest == baseline_digest:
        raise StateError(f"{role} did not update required artifact: {artifact_name}")
    if not isinstance(baseline_size, int):
        raise StateError("artifact expectation is missing its baseline size")
    if expected["append_only"]:
        if (
            len(content) <= baseline_size
            or sha256_bytes(content[:baseline_size]) != baseline_digest
        ):
            raise StateError(f"append-only artifact was overwritten: {artifact_name}")
        evidence = content[baseline_size:].decode("utf-8")
    else:
        evidence = content.decode("utf-8")

    minimum_size = ROLE_MIN_EVIDENCE_BYTES[expected_role]
    if len(evidence.encode("utf-8")) < minimum_size:
        raise StateError(
            f"{artifact_name} evidence is too short for {expected_role}: "
            f"minimum {minimum_size} bytes"
        )
    normalized_evidence = evidence.lower()
    missing_sections = [
        section
        for section in ROLE_REQUIRED_SECTIONS[expected_role]
        if section not in normalized_evidence
    ]
    if missing_sections:
        raise StateError(
            f"{artifact_name} is missing required evidence section(s): "
            + ", ".join(missing_sections)
        )

    token_template = expected["artifact_token"]
    if not isinstance(token_template, str):
        raise StateError("artifact expectation is missing its token")
    token = token_template.replace("<MARKER>", marker)
    lines = [line.strip() for line in evidence.splitlines() if line.strip()]
    if not lines or lines[-1] != token:
        raise StateError(
            f"{artifact_name} is missing the expected final evidence token"
        )
    decisions = [line for line in lines if line.startswith("Decision:")]
    expected_decision = f"Decision: {marker}"
    if decisions != [expected_decision]:
        raise StateError(f"{artifact_name} must contain exactly one Decision: {marker}")
    if len(lines) < 2 or lines[-2] != expected_decision:
        raise StateError(
            f"{artifact_name} must place Decision: {marker} immediately before its token"
        )


def initialize_run(
    workspace: str | Path,
    slug: str,
    task: str,
    acceptance_criteria: list[str],
    delivery_mode: str,
    output_path: str | None = None,
    timestamp: str | None = None,
    request_id: str | None = None,
    request_hash: str | None = None,
) -> Path:
    if not isinstance(task, str) or not task.strip():
        raise StateError("task must not be empty")
    if not isinstance(slug, str) or not slug.strip():
        raise StateError("slug must not be empty")
    if not isinstance(acceptance_criteria, list):
        raise StateError("acceptance_criteria must be a list")
    if not isinstance(delivery_mode, str) or delivery_mode not in DELIVERY_MODES:
        raise StateError(f"unsupported delivery mode: {delivery_mode!r}")
    if output_path is not None and not isinstance(output_path, str):
        raise StateError("output_path must be a string when provided")
    if output_path is not None and output_path.strip():
        raise StateError("output_path is not supported by V1 delivery modes")
    criteria = [
        item.strip()
        for item in acceptance_criteria
        if isinstance(item, str) and item.strip()
    ]
    if not criteria or len(criteria) != len(acceptance_criteria):
        raise StateError("at least one non-empty acceptance criterion is required")

    workspace_lexical, workspace_real, _, runs = guard_control_root(
        workspace, create=True
    )
    cleanup_incomplete_runs(runs)
    reject_existing_active_run(workspace_lexical)
    run_dir = unique_run_path(workspace_lexical, slug, timestamp)
    resolved_output = resolve_output_path(
        workspace_lexical, run_dir, delivery_mode, output_path
    )
    baseline = git_baseline(workspace_lexical)
    now = utc_now()
    state: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_dir.name,
        "workspace": str(workspace_lexical),
        "workspace_real": str(workspace_real),
        "run_dir": str(run_dir),
        "run_dir_real": str(run_dir.resolve(strict=False)),
        "status": INITIALIZED,
        "task": task.strip(),
        "acceptance_criteria": criteria,
        "delivery": {
            "mode": delivery_mode,
            "output_path": str(resolved_output),
            "output_path_real": str(canonical_output_path(resolved_output)),
        },
        "planning_round": 0,
        "planning_cycle": 1,
        "verification_round": 0,
        "verification_cycle": 1,
        "executor_dispatch_count": 0,
        "executor_repair_dispatch_count": 0,
        "executor_internal_repair_count": 0,
        "execution_iterations": [],
        "executor_mode": None,
        "pending_test_persistence": [],
        "pending_checkpoint": None,
        "last_marker": None,
        "expected": None,
        "approved_plan_subject": None,
        "verified_subject": None,
        "completion_scope": None,
        "risks": [],
        "blocked": None,
        "git_baseline": baseline,
        "created_at": now,
        "updated_at": now,
        "history": [
            {
                "at": now,
                "event": "INIT",
                "from": None,
                "to": INITIALIZED,
                "detail": "run initialized",
            }
        ],
        "processed_requests": [],
    }
    record_processed_request(state, request_id, "init", request_hash)
    validate_state_shape(state)

    temporary_run = Path(tempfile.mkdtemp(prefix=INIT_TEMP_PREFIX, dir=runs))
    secure_permissions(temporary_run, PRIVATE_DIRECTORY_MODE)
    try:
        if delivery_mode == "staged":
            (temporary_run / "deliverable").mkdir(mode=PRIVATE_DIRECTORY_MODE)
        atomic_write_text(temporary_run / "task.md", render_task_markdown(state))
        atomic_write_text(temporary_run / "plan.md", "# Plan\n\nPending.\n")
        atomic_write_text(temporary_run / "review-log.md", "# Plan review log\n")
        atomic_write_text(temporary_run / "verify-report.md", "# Verification report\n")
        atomic_write_text(temporary_run / "final-review.md", "# Final review log\n")
        atomic_write_text(
            temporary_run / "deliverables.md", "# Deliverables\n\nPending.\n"
        )
        atomic_write_text(temporary_run / "risk-log.md", render_risk_log(state))
        atomic_write_json(temporary_run / "state.json", state)
        os.replace(temporary_run, run_dir)
        secure_permissions(run_dir, PRIVATE_DIRECTORY_MODE)
    except Exception:
        shutil.rmtree(temporary_run, ignore_errors=True)
        raise
    return run_dir


def start_planning(
    run_dir: str | Path,
    request_id: str | None = None,
    request_hash: str | None = None,
) -> dict[str, Any]:
    run_path, run_real = validate_run_path(run_dir, require_state=True)
    with state_lock(run_path):
        state = _load_state_unlocked(run_path, run_real)
        if state["status"] != INITIALIZED:
            raise StateError(f"start requires {INITIALIZED}, got {state['status']}")
        set_status(state, PLANNING, "START_PLANNING")
        set_expectation(state, run_path, "planner")
        record_processed_request(state, request_id, "start", request_hash)
        save_state_unlocked(run_path, state)
        return state


def block_state(
    state: dict[str, Any],
    reason: str,
    *,
    event: str,
    resume_expected: dict[str, Any] | None = None,
) -> None:
    if state["status"] not in ACTIVE_STATES:
        raise StateError(f"cannot block from {state['status']}")
    if not reason.strip():
        raise StateError("block reason must not be empty")
    previous = state["status"]
    state["blocked"] = {
        "from": previous,
        "reason": reason.strip(),
        "at": utc_now(),
        "event": event,
        "expected": resume_expected
        if resume_expected is not None
        else state.get("expected"),
    }
    set_status(state, BLOCKED, event, reason.strip())
    state["expected"] = None


def apply_verifier_test_evidence(
    state: dict[str, Any], marker: str, test_evidence: Any
) -> None:
    evidence = validate_test_evidence_items(test_evidence, state, label="test_evidence")
    critical_ad_hoc = [
        item for item in evidence if item["critical"] and item["kind"] == "ad_hoc"
    ]
    if marker == "PASS" and critical_ad_hoc:
        raise StateError(
            "PASS cannot rely on critical ad hoc tests; return NEEDS_TEST_PERSISTENCE"
        )
    if marker == "NEEDS_TEST_PERSISTENCE" and not critical_ad_hoc:
        raise StateError(
            "NEEDS_TEST_PERSISTENCE requires critical ad hoc test evidence"
        )

    pending_by_id = {item["id"]: item for item in state["pending_test_persistence"]}
    evidence_by_id = {item["id"]: item for item in evidence}
    unresolved = []
    for evidence_id, pending in pending_by_id.items():
        current = evidence_by_id.get(evidence_id)
        if (
            current is None
            or current["kind"] != "formal"
            or not current["persisted"]
            or not current["target"]
        ):
            unresolved.append(pending)
    if marker == "PASS" and unresolved:
        missing = ", ".join(item["id"] for item in unresolved)
        raise StateError(
            f"PASS requires persisted formal regression evidence for: {missing}"
        )

    combined = {item["id"]: item for item in unresolved}
    if marker in {"FAIL", "NEEDS_TEST_PERSISTENCE"}:
        combined.update({item["id"]: item for item in critical_ad_hoc})
    state["pending_test_persistence"] = list(combined.values())
    if marker == "PASS":
        state["pending_test_persistence"] = []


def record_executor_iterations(state: dict[str, Any], items: Any) -> None:
    iterations = validate_execution_iteration_items(items)
    mode = state.get("executor_mode")
    if mode not in EXECUTOR_MODES or state["executor_dispatch_count"] < 1:
        raise StateError("executor completion has no active dispatch")
    state["execution_iterations"].append(
        {
            "at": utc_now(),
            "dispatch": state["executor_dispatch_count"],
            "mode": mode,
            "items": iterations,
        }
    )
    state["executor_internal_repair_count"] += len(iterations)


def apply_marker(
    run_dir: str | Path,
    marker: str,
    detail: str = "",
    *,
    role: str | None = None,
    test_evidence: Any = None,
    execution_iterations: Any = None,
    request_id: str | None = None,
    request_hash: str | None = None,
) -> dict[str, Any]:
    if not isinstance(marker, str):
        raise StateError("marker must be a string")
    if role is not None and not isinstance(role, str):
        raise StateError("role must be a string when provided")
    if not isinstance(detail, str):
        raise StateError("marker detail must be a string")
    marker = marker.strip().upper()
    if marker not in MARKERS:
        raise StateError(f"unknown marker: {marker}")
    run_path, run_real = validate_run_path(run_dir, require_state=True)
    with state_lock(run_path):
        state = _load_state_unlocked(run_path, run_real)
        if not approved_plan_is_current(run_path, state):
            restart_planning(
                state,
                run_path,
                "APPROVED_PLAN_CHANGED",
                "approved plan changed after checkpoint A",
            )
            record_processed_request(state, request_id, "marker", request_hash)
            save_state_unlocked(run_path, state)
            return state
        status = state["status"]
        expected = state.get("expected") or {}
        if (
            status == FINAL_REVIEW
            and expected.get("role") == "planner_final"
            and not subject_is_current(
                run_path, state, "planner_final", expected.get("subject")
            )
        ):
            restart_verification(
                state,
                run_path,
                "VERIFIED_EVIDENCE_CHANGED",
                "delivery or verification evidence changed after PASS",
            )
            record_processed_request(state, request_id, "marker", request_hash)
            save_state_unlocked(run_path, state)
            return state
        validate_role_and_artifact(run_path, state, role, marker)
        if marker == "BLOCKED" and not detail.strip():
            raise StateError("BLOCKED marker requires a specific detail")
        if role == "verifier" and marker != "BLOCKED":
            if test_evidence is None:
                raise StateError("verifier marker requires test_evidence")
            if execution_iterations is not None:
                raise StateError("verifier marker cannot include execution_iterations")
            apply_verifier_test_evidence(state, marker, test_evidence)
        elif role == "executor" and marker == "EXECUTION_DONE":
            if execution_iterations is None:
                raise StateError("EXECUTION_DONE requires execution_iterations")
            if test_evidence is not None:
                raise StateError("executor marker cannot include test_evidence")
            record_executor_iterations(state, execution_iterations)
        elif test_evidence is not None or execution_iterations is not None:
            raise StateError("marker includes evidence fields not owned by this role")
        # BLOCKED is already durable in status/history. Preserve the prior role
        # decision so resume can still detect any lost causal feedback.
        if marker != "BLOCKED":
            state["last_marker"] = marker

        if marker == "PLAN_READY":
            record_history(state, marker, status, status, f"{role}: {detail}".strip())
            set_expectation(state, run_path, "plan_reviewer")
        elif marker in {"APPROVED", "NEEDS_REVISION"}:
            state["planning_round"] += 1
            if marker == "APPROVED":
                set_status(state, AWAITING_PLAN_APPROVAL, marker, detail)
                set_expectation(state, run_path, "human_plan")
            elif state["planning_round"] >= PLANNING_LIMIT:
                resume_expected = make_expectation(state, run_path, "planner")
                block_state(
                    state,
                    f"planning remained NEEDS_REVISION after {PLANNING_LIMIT} rounds"
                    + (f": {detail}" if detail else ""),
                    event="PLANNING_LIMIT_REACHED",
                    resume_expected=resume_expected,
                )
            else:
                record_history(state, marker, status, status, detail)
                set_expectation(state, run_path, "planner")
        elif marker == "EXECUTION_DONE":
            state["verified_subject"] = None
            state["executor_mode"] = None
            set_status(state, VERIFYING, marker, detail)
            set_expectation(state, run_path, "verifier")
        elif marker == "BLOCKED":
            block_state(state, detail, event="SUBAGENT_BLOCKED")
        elif marker in {"PASS", "FAIL", "NEEDS_TEST_PERSISTENCE"}:
            state["verification_round"] += 1
            if marker == "PASS":
                state["verified_subject"] = verification_subject(state, run_path)
                set_status(state, FINAL_REVIEW, marker, detail)
                set_expectation(state, run_path, "planner_final")
            elif state["verification_round"] >= VERIFICATION_LIMIT:
                state["verified_subject"] = None
                resume_expected = make_expectation(state, run_path, "executor")
                block_state(
                    state,
                    f"verification did not pass within {VERIFICATION_LIMIT} rounds"
                    + (f": {detail}" if detail else ""),
                    event="VERIFICATION_LIMIT_REACHED",
                    resume_expected=resume_expected,
                )
            else:
                state["verified_subject"] = None
                set_status(state, EXECUTING, marker, detail)
                schedule_executor(state, run_path, "repair")
        elif marker == "FINAL_ACCEPT":
            set_status(state, AWAITING_FINAL_APPROVAL, marker, detail)
            set_expectation(state, run_path, "human_final")
        elif marker == "FINAL_REJECT":
            state["verified_subject"] = None
            if state["verification_round"] >= VERIFICATION_LIMIT:
                resume_expected = make_expectation(state, run_path, "executor")
                block_state(
                    state,
                    f"final review rejected the result after {VERIFICATION_LIMIT} verification rounds"
                    + (f": {detail}" if detail else ""),
                    event="VERIFICATION_LIMIT_REACHED",
                    resume_expected=resume_expected,
                )
            else:
                set_status(state, EXECUTING, marker, detail)
                schedule_executor(state, run_path, "repair")

        record_processed_request(state, request_id, "marker", request_hash)
        save_state_unlocked(run_path, state)
        return state


def confirm_checkpoint(
    run_dir: str | Path,
    checkpoint: str,
    decision: str,
    detail: str = "",
    completion_scope: Any = None,
    request_id: str | None = None,
    request_hash: str | None = None,
) -> dict[str, Any]:
    if not all(isinstance(item, str) for item in (checkpoint, decision, detail)):
        raise StateError("checkpoint, decision, and detail must be strings")
    checkpoint = checkpoint.lower()
    decision = decision.lower()
    if decision == "revise" and not detail.strip():
        raise StateError("revision decisions require specific user feedback")
    if completion_scope is not None and not (
        checkpoint == "final" and decision == "approve"
    ):
        raise StateError("completion_scope is allowed only for final approval")
    run_path, run_real = validate_run_path(run_dir, require_state=True)
    with state_lock(run_path):
        state = _load_state_unlocked(run_path, run_real)
        expected = state.get("expected") or {}

        if checkpoint == "plan":
            if (
                state["status"] != AWAITING_PLAN_APPROVAL
                or expected.get("role") != "human_plan"
            ):
                raise StateError(
                    f"plan confirmation requires {AWAITING_PLAN_APPROVAL} and human_plan"
                )
            if decision == "approve":
                if not subject_is_current(
                    run_path, state, "human_plan", expected.get("subject")
                ):
                    restart_planning(
                        state,
                        run_path,
                        "PLAN_CHANGED_BEFORE_APPROVAL",
                        "reviewed plan changed before checkpoint A approval",
                    )
                else:
                    state["approved_plan_subject"] = expected["subject"]
                    state["verified_subject"] = None
                    set_status(state, EXECUTING, "USER_APPROVED_PLAN", detail)
                    schedule_executor(state, run_path, "initial")
            elif decision == "revise":
                restart_planning(state, run_path, "USER_REVISE_PLAN", detail)
            else:
                raise StateError("plan decision must be approve or revise")
        elif checkpoint == "final":
            if (
                state["status"] != AWAITING_FINAL_APPROVAL
                or expected.get("role") != "human_final"
            ):
                raise StateError(
                    f"final confirmation requires {AWAITING_FINAL_APPROVAL} and human_final"
                )
            if decision not in {"approve", "revise"}:
                raise StateError("final decision must be approve or revise")
            if not approved_plan_is_current(run_path, state):
                restart_planning(
                    state,
                    run_path,
                    "APPROVED_PLAN_CHANGED",
                    "approved plan changed before checkpoint B",
                )
            elif decision == "approve":
                if not subject_is_current(
                    run_path, state, "human_final", expected.get("subject")
                ):
                    restart_verification(
                        state,
                        run_path,
                        "FINAL_EVIDENCE_CHANGED",
                        "delivery or final evidence changed before checkpoint B",
                    )
                else:
                    completion = validate_completion_input(completion_scope)
                    approved = state["approved_plan_subject"] or {}
                    verified = state["verified_subject"] or {}
                    final = expected.get("subject") or {}
                    state["completion_scope"] = {
                        "completed_at": utc_now(),
                        **completion,
                        "delivery_mode": state["delivery"]["mode"],
                        "output_path": state["delivery"]["output_path_real"],
                        "open_risk_ids": [
                            item["id"]
                            for item in state["risks"]
                            if item["status"] != "closed"
                        ],
                        "approved_plan_sha256": approved.get("sha256"),
                        "verified_subject_sha256": verified.get("sha256"),
                        "final_subject_sha256": final.get("sha256"),
                    }
                    set_status(state, DONE, "USER_APPROVED_FINAL", detail)
                    set_expectation(state, run_path, None)
            else:
                restart_verification(state, run_path, "USER_REJECTED_FINAL", detail)
        else:
            raise StateError("checkpoint must be plan or final")

        record_processed_request(state, request_id, "confirm", request_hash)
        save_state_unlocked(run_path, state)
        return state


def update_delivery(
    run_dir: str | Path,
    mode: str,
    output_path: str | None,
    request_id: str | None = None,
    request_hash: str | None = None,
) -> dict[str, Any]:
    if not isinstance(mode, str):
        raise StateError("delivery mode must be a string")
    if output_path is not None and not isinstance(output_path, str):
        raise StateError("output_path must be a string when provided")
    if output_path is not None and output_path.strip():
        raise StateError("output_path is not supported by V1 delivery modes")
    run_path, run_real = validate_run_path(run_dir, require_state=True)
    with state_lock(run_path):
        state = _load_state_unlocked(run_path, run_real)
        if state["status"] not in {INITIALIZED, PLANNING, AWAITING_PLAN_APPROVAL}:
            raise StateError(f"delivery cannot change from {state['status']}")
        workspace = Path(state["workspace"])
        resolved = resolve_output_path(workspace, run_path, mode, output_path)
        new_delivery = {
            "mode": mode,
            "output_path": str(resolved),
            "output_path_real": str(canonical_output_path(resolved)),
        }
        if new_delivery == state["delivery"]:
            record_processed_request(state, request_id, "set_delivery", request_hash)
            if request_id is not None:
                save_state_unlocked(run_path, state)
            return state
        if mode == "staged":
            if resolved.is_symlink():
                raise StateError("staged deliverable directory must not be a symlink")
            resolved.mkdir(exist_ok=True)
        previous_status = state["status"]
        state["delivery"] = new_delivery
        state["git_baseline"] = git_baseline(workspace)
        state["approved_plan_subject"] = None
        state["verified_subject"] = None
        state["pending_test_persistence"] = []
        state["executor_mode"] = None
        if previous_status in {PLANNING, AWAITING_PLAN_APPROVAL}:
            state["planning_cycle"] += 1
            state["planning_round"] = 0
            set_status(
                state,
                PLANNING,
                "UPDATE_DELIVERY_REQUIRES_REPLAN",
                f"{mode}: {resolved}",
            )
            set_expectation(state, run_path, "planner")
        else:
            record_history(
                state,
                "UPDATE_DELIVERY",
                previous_status,
                previous_status,
                f"{mode}: {resolved}",
            )
        record_processed_request(state, request_id, "set_delivery", request_hash)
        save_state_unlocked(run_path, state)
        return state


def add_risk(
    run_dir: str | Path,
    source: str,
    text: str,
    request_id: str | None = None,
    request_hash: str | None = None,
) -> dict[str, Any]:
    if not isinstance(source, str) or not isinstance(text, str) or not text.strip():
        raise StateError("risk text must not be empty")
    run_path, run_real = validate_run_path(run_dir, require_state=True)
    with state_lock(run_path):
        state = _load_state_unlocked(run_path, run_real)
        if state["status"] == DONE:
            raise StateError("cannot add risks to a completed run")
        now = utc_now()
        risk_id = f"R-{len(state['risks']) + 1:04d}"
        creator = source.strip() or "unknown"
        item = {
            "id": risk_id,
            "text": text.strip(),
            "created_at": now,
            "created_by": creator,
            "status": "open",
            "events": [
                {
                    "at": now,
                    "source": creator,
                    "from": None,
                    "to": "open",
                    "detail": "risk recorded",
                    "evidence": "",
                }
            ],
        }
        state["risks"].append(item)
        record_history(
            state,
            "ADD_RISK",
            state["status"],
            state["status"],
            f"{risk_id} {creator}: {item['text']}",
        )
        record_processed_request(state, request_id, "risk", request_hash)
        save_state_unlocked(run_path, state)
        return state


def update_risk(
    run_dir: str | Path,
    risk_id: str,
    source: str,
    status: str,
    detail: str,
    evidence: str = "",
    test_evidence: Any = None,
    request_id: str | None = None,
    request_hash: str | None = None,
) -> dict[str, Any]:
    if not all(
        isinstance(item, str) for item in (risk_id, source, status, detail, evidence)
    ):
        raise StateError("risk update fields must be strings")
    risk_id = risk_id.strip()
    source = source.strip()
    target = status.strip().lower()
    if not RISK_ID_PATTERN.fullmatch(risk_id):
        raise StateError("invalid risk_id")
    if not source or not detail.strip():
        raise StateError("risk update source and detail must not be empty")
    if target not in RISK_STATUSES:
        raise StateError("invalid risk status")
    run_path, run_real = validate_run_path(run_dir, require_state=True)
    with state_lock(run_path):
        state = _load_state_unlocked(run_path, run_real)
        if state["status"] == DONE:
            raise StateError("cannot update risks in a completed run")
        risk = next((item for item in state["risks"] if item["id"] == risk_id), None)
        if risk is None:
            raise StateError(f"unknown risk: {risk_id}")
        previous = risk["status"]
        if target not in RISK_TRANSITIONS[previous]:
            raise StateError(f"invalid risk transition: {previous} -> {target}")
        if target == "closed":
            if source not in RISK_CLOSERS:
                raise StateError("only verifier or planner_final may close a risk")
            expected_role = (state.get("expected") or {}).get("role")
            if expected_role != source:
                raise StateError("risk closer must be the currently expected role")
            if not evidence.strip():
                raise StateError("closing a risk requires concrete evidence")
            declared_tests = validate_test_evidence_items(
                test_evidence, state, label="test_evidence"
            )
            critical_ad_hoc = [
                item
                for item in declared_tests
                if item["critical"]
                and item["kind"] == "ad_hoc"
                and risk_id in item["risk_ids"]
            ]
            if critical_ad_hoc:
                raise StateError(
                    "a risk cannot be closed with critical ad hoc test evidence; "
                    "return NEEDS_TEST_PERSISTENCE"
                )
        elif test_evidence is not None:
            raise StateError("test_evidence is accepted only when closing a risk")
        event = {
            "at": utc_now(),
            "source": source,
            "from": previous,
            "to": target,
            "detail": detail.strip(),
            "evidence": evidence.strip(),
        }
        risk["events"].append(event)
        risk["status"] = target
        action = "REOPEN_RISK" if previous == "closed" else "UPDATE_RISK"
        record_history(
            state,
            action,
            state["status"],
            state["status"],
            f"{risk_id}: {previous} -> {target}; {detail.strip()}",
        )
        record_processed_request(state, request_id, "risk_update", request_hash)
        save_state_unlocked(run_path, state)
        return state


def block_run(
    run_dir: str | Path,
    reason: str,
    request_id: str | None = None,
    request_hash: str | None = None,
) -> dict[str, Any]:
    if not isinstance(reason, str):
        raise StateError("block reason must be a string")
    run_path, run_real = validate_run_path(run_dir, require_state=True)
    with state_lock(run_path):
        state = _load_state_unlocked(run_path, run_real)
        block_state(state, reason, event="BLOCK")
        record_processed_request(state, request_id, "block", request_hash)
        save_state_unlocked(run_path, state)
        return state


def resume_run(
    run_dir: str | Path,
    target: str | None,
    reason: str,
    request_id: str | None = None,
    request_hash: str | None = None,
) -> dict[str, Any]:
    if target is not None and not isinstance(target, str):
        raise StateError("resume target must be a string when provided")
    if not isinstance(reason, str) or not reason.strip():
        raise StateError("resume reason must not be empty")
    run_path, run_real = validate_run_path(run_dir, require_state=True)
    with state_lock(run_path):
        state = _load_state_unlocked(run_path, run_real)
        if state["status"] != BLOCKED:
            raise StateError(f"resume requires {BLOCKED}, got {state['status']}")
        blocked = state["blocked"]
        blocked_from = blocked["from"]
        if target is not None and target.upper() != blocked_from:
            raise StateError(
                f"blocked run can resume only to {blocked_from}, not {target.upper()}"
            )
        event = blocked.get("event")
        state["blocked"] = None
        if event == "PLANNING_LIMIT_REACHED":
            restart_planning(state, run_path, "USER_AUTHORIZED_RESUME", reason.strip())
        elif event == "VERIFICATION_LIMIT_REACHED":
            restart_verification(
                state, run_path, "USER_AUTHORIZED_RESUME", reason.strip()
            )
        else:
            set_status(state, blocked_from, "USER_AUTHORIZED_RESUME", reason.strip())
            state["expected"] = blocked.get("expected")
            if state["expected"] is None and blocked_from != INITIALIZED:
                raise StateError("blocked run has no safe role expectation to restore")
        reconcile_active_run(run_path, state)
        record_processed_request(state, request_id, "resume", request_hash)
        save_state_unlocked(run_path, state)
        return state


def state_summary(state: dict[str, Any]) -> dict[str, Any]:
    expected = state.get("expected")
    risk_counts = {
        status: sum(1 for item in state["risks"] if item["status"] == status)
        for status in sorted(RISK_STATUSES)
    }
    return {
        "run_id": state["run_id"],
        "run_dir": state["run_dir"],
        "status": state["status"],
        "delivery": state["delivery"],
        "planning_round": state["planning_round"],
        "planning_cycle": state["planning_cycle"],
        "verification_round": state["verification_round"],
        "verification_cycle": state["verification_cycle"],
        "executor_dispatch_count": state["executor_dispatch_count"],
        "executor_repair_dispatch_count": state["executor_repair_dispatch_count"],
        "executor_internal_repair_count": state["executor_internal_repair_count"],
        "executor_mode": state["executor_mode"],
        "pending_test_persistence": state["pending_test_persistence"],
        "pending_checkpoint": state["pending_checkpoint"],
        "last_marker": state["last_marker"],
        "approved_plan_subject": state["approved_plan_subject"],
        "verified_subject": state["verified_subject"],
        "expected": (
            None
            if expected is None
            else {
                "role": expected["role"],
                "markers": expected["markers"],
                "artifact": expected["artifact"],
                "artifact_token": expected["artifact_token"],
                "subject": expected["subject"],
            }
        ),
        "risk_count": len(state["risks"]),
        "risk_counts": risk_counts,
        "risks": [
            {"id": item["id"], "status": item["status"], "text": item["text"]}
            for item in state["risks"]
        ],
        "completion_scope": state["completion_scope"],
        "blocked": state["blocked"],
    }


def require_request_fields(
    request: dict[str, Any], required: set[str], optional: set[str] | None = None
) -> None:
    optional = optional or set()
    keys = set(request)
    missing = required - keys
    unknown = keys - required - optional
    if missing:
        raise StateError(f"request is missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise StateError(
            f"request contains unknown fields: {', '.join(sorted(unknown))}"
        )


def run_dir_from_id(workspace: Path, run_id: str) -> Path:
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        raise StateError(f"invalid run_id: {run_id!r}")
    _, _, _, runs = guard_control_root(workspace, create=False)
    candidate = runs / run_id
    validate_run_path(candidate, require_state=True)
    return candidate


def processed_request_entry(
    state: dict[str, Any], request_id: str
) -> dict[str, Any] | None:
    for item in state["processed_requests"]:
        if item["id"] == request_id:
            return cast(dict[str, Any], item)
    return None


def find_processed_request(
    workspace: Path, request: dict[str, Any]
) -> dict[str, Any] | None:
    request_id = request.get("request_id")
    command = request.get("command")
    if not isinstance(request_id, str) or not isinstance(command, str):
        return None
    validate_request_id(request_id)
    digest = request_digest(request)

    candidates: list[Path]
    if command == "init":
        candidates = run_candidates(workspace)
    else:
        run_id = request.get("run_id")
        if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
            return None
        candidates = [run_dir_from_id(workspace, run_id)]

    for candidate in candidates:
        state = (
            load_usable_run(candidate) if command == "init" else load_state(candidate)
        )
        if state is None:
            continue
        processed = processed_request_entry(state, request_id)
        if processed is None:
            continue
        if processed["command"] != command or processed["digest"] != digest:
            raise StateError("request_id was already used for a different payload")
        return state_summary(state)
    return None


def process_request(workspace: Path, request: dict[str, Any]) -> dict[str, Any]:
    command = request.get("command")
    if not isinstance(command, str):
        raise StateError("request command must be a string")
    request_id = request.get("request_id")
    if not isinstance(request_id, str):
        raise StateError("request is missing fields: request_id")
    validate_request_id(request_id)
    request_hash = request_digest(request)

    already_processed = find_processed_request(workspace, request)
    if already_processed is not None:
        return already_processed

    if command == "init":
        require_request_fields(
            request,
            {
                "request_id",
                "command",
                "slug",
                "task",
                "acceptance_criteria",
                "delivery_mode",
            },
            {"output_path"},
        )
        run_dir = initialize_run(
            workspace=workspace,
            slug=request["slug"],
            task=request["task"],
            acceptance_criteria=request["acceptance_criteria"],
            delivery_mode=request["delivery_mode"],
            output_path=request.get("output_path"),
            request_id=request_id,
            request_hash=request_hash,
        )
        return state_summary(load_state(run_dir))

    if command not in {
        "show",
        "start",
        "marker",
        "confirm",
        "set_delivery",
        "risk",
        "risk_update",
        "block",
        "resume",
    }:
        raise StateError(f"unknown request command: {command}")
    if "run_id" not in request:
        raise StateError("request is missing fields: run_id")
    run_dir = run_dir_from_id(workspace, request["run_id"])
    if command == "show":
        require_request_fields(request, {"request_id", "command", "run_id"})
        state = show_run(run_dir, request_id, request_hash)
    elif command == "start":
        require_request_fields(request, {"request_id", "command", "run_id"})
        state = start_planning(
            run_dir, request_id=request_id, request_hash=request_hash
        )
    elif command == "marker":
        require_request_fields(
            request,
            {"request_id", "command", "run_id", "role", "marker"},
            {"detail", "test_evidence", "execution_iterations"},
        )
        state = apply_marker(
            run_dir,
            request["marker"],
            request.get("detail", ""),
            role=request["role"],
            test_evidence=request.get("test_evidence"),
            execution_iterations=request.get("execution_iterations"),
            request_id=request_id,
            request_hash=request_hash,
        )
    elif command == "confirm":
        require_request_fields(
            request,
            {"request_id", "command", "run_id", "checkpoint", "decision"},
            {"detail", "completion_scope"},
        )
        state = confirm_checkpoint(
            run_dir,
            request["checkpoint"],
            request["decision"],
            request.get("detail", ""),
            completion_scope=request.get("completion_scope"),
            request_id=request_id,
            request_hash=request_hash,
        )
    elif command == "set_delivery":
        require_request_fields(
            request,
            {"request_id", "command", "run_id", "mode"},
            {"output_path"},
        )
        state = update_delivery(
            run_dir,
            request["mode"],
            request.get("output_path"),
            request_id=request_id,
            request_hash=request_hash,
        )
    elif command == "risk":
        require_request_fields(
            request,
            {"request_id", "command", "run_id", "source", "text"},
        )
        state = add_risk(
            run_dir,
            request["source"],
            request["text"],
            request_id=request_id,
            request_hash=request_hash,
        )
    elif command == "risk_update":
        require_request_fields(
            request,
            {
                "request_id",
                "command",
                "run_id",
                "risk_id",
                "source",
                "status",
                "detail",
            },
            {"evidence", "test_evidence"},
        )
        state = update_risk(
            run_dir,
            request["risk_id"],
            request["source"],
            request["status"],
            request["detail"],
            request.get("evidence", ""),
            request.get("test_evidence"),
            request_id=request_id,
            request_hash=request_hash,
        )
    elif command == "block":
        require_request_fields(request, {"request_id", "command", "run_id", "reason"})
        state = block_run(
            run_dir,
            request["reason"],
            request_id=request_id,
            request_hash=request_hash,
        )
    elif command == "resume":
        require_request_fields(request, {"request_id", "command", "run_id", "reason"})
        state = resume_run(
            run_dir,
            None,
            request["reason"],
            request_id=request_id,
            request_hash=request_hash,
        )
    return state_summary(state)


def guarded_request_path(workspace: Path, *, create: bool) -> Path:
    _, _, control, _ = guard_control_root(workspace, create=create)
    request_path = control / REQUEST_FILE
    if request_path.is_symlink():
        raise StateError("request.json must not be a symlink")
    if request_path.exists() and not request_path.is_file():
        raise StateError("request.json must be a regular file")
    if create and not request_path.exists():
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(request_path, flags, PRIVATE_FILE_MODE)
        try:
            secure_file_descriptor(fd)
        finally:
            os.close(fd)
    if request_path.exists():
        secure_permissions(request_path, PRIVATE_FILE_MODE)
    return request_path


def execute_request_file(workspace: Path) -> dict[str, Any]:
    request_path = guarded_request_path(workspace, create=False)
    request_lock = request_path.parent / REQUEST_LOCK_FILE
    with exclusive_file_lock(request_lock, "request lock"):
        try:
            secure_permissions(request_path, PRIVATE_FILE_MODE)
            with request_path.open(encoding="utf-8") as handle:
                request = json.load(handle)
        except FileNotFoundError as exc:
            raise StateError(
                f"write the next request with a file-edit tool at {request_path}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise StateError(f"request.json is invalid JSON: {exc}") from exc
        if not isinstance(request, dict):
            raise StateError("request.json must contain an object")
        result = process_request(workspace, request)
        request_path.unlink()
        return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "guard", help=f"validate the workspace and prepare {CONTROL_DIR}/{REQUEST_FILE}"
    )
    subparsers.add_parser(
        "request", help=f"consume one JSON operation from {CONTROL_DIR}/{REQUEST_FILE}"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        workspace = invocation_workspace()
        if args.command == "guard":
            request_path = guarded_request_path(workspace, create=True)
            result: dict[str, Any] = {
                "workspace": str(workspace),
                "request_file": str(request_path),
                "request_id": f"req-{secrets.token_hex(16)}",
                "ready": True,
            }
        elif args.command == "request":
            result = execute_request_file(workspace)
        else:  # pragma: no cover
            raise StateError(f"unhandled command: {args.command}")
    except (StateError, OSError, TypeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
