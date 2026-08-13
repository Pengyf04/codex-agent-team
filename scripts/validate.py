#!/usr/bin/env python3
"""Validate the Codex Agent Team repository using only the Python standard library."""

from __future__ import annotations

import json
import re

# Invokes this repository's Python script without a shell.
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "codex-agent-team"
SKILL = PLUGIN / "skills" / "agent-team"
ERRORS: list[str] = []
IGNORED_SCAN_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
}


def check(condition: bool, message: str) -> None:
    symbol = "[OK]" if condition else "[FAIL]"
    print(f"  {symbol} {message}")
    if not condition:
        ERRORS.append(message)


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            data: Any = json.load(handle)
        if not isinstance(data, dict):
            raise TypeError("JSON root must be an object")
        return data
    except Exception as exc:  # noqa: BLE001
        ERRORS.append(f"{path.relative_to(ROOT)} is invalid JSON: {exc}")
        return {}


def skill_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not match:
        return {}
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    return values


print("== Plugin manifest ==")
manifest = load_json(PLUGIN / ".codex-plugin" / "plugin.json")
check(manifest.get("name") == "codex-agent-team", "plugin name is codex-agent-team")
check(
    bool(
        re.fullmatch(
            r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", str(manifest.get("version", ""))
        )
    ),
    "plugin version is release semver without build metadata",
)
check(bool(manifest.get("description")), "plugin description is present")
author = manifest.get("author") or {}
check(
    author.get("name") == "Pengyf04", "plugin author uses the approved public identity"
)
check("email" not in author, "plugin manifest does not publish an email address")
check(
    manifest.get("repository") == "https://github.com/Pengyf04/codex-agent-team",
    "plugin repository points to the public release target",
)
check(manifest.get("skills") == "./skills/", "plugin skills path is ./skills/")
check((PLUGIN / "skills").is_dir(), "plugin skills directory exists")
interface = manifest.get("interface") or {}
check(
    interface.get("privacyPolicyURL")
    == "https://github.com/Pengyf04/codex-agent-team/blob/main/docs/security-and-privacy.md",
    "plugin links to the public privacy documentation",
)

print("== Marketplace ==")
marketplace = load_json(ROOT / ".agents" / "plugins" / "marketplace.json")
entries = marketplace.get("plugins") or []
entry = entries[0] if entries else {}
source = entry.get("source") or {}
check(
    marketplace.get("name") == "codex-agent-team",
    "marketplace name is codex-agent-team",
)
check(entry.get("name") == manifest.get("name"), "marketplace and plugin names match")
check(source.get("source") == "local", "marketplace source type is local")
check(
    source.get("path") == "./plugins/codex-agent-team",
    "marketplace source path is canonical",
)
check(
    bool(source.get("path") and (ROOT / source["path"]).is_dir()),
    "marketplace source resolves to plugin",
)
policy = entry.get("policy") or {}
check(policy.get("installation") == "AVAILABLE", "installation policy is AVAILABLE")
check(
    policy.get("authentication") == "ON_INSTALL", "authentication policy is ON_INSTALL"
)
check(bool(entry.get("category")), "marketplace category is present")

print("== GitHub workflows ==")
workflow_files = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
check(bool(workflow_files), "GitHub workflow files are present")
action_references: list[str] = []
for workflow_path in workflow_files:
    for line in workflow_path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*-?\s*uses:\s*([^\s#]+)", line)
        if match:
            action_references.append(match.group(1))
unpinned_actions = [
    reference
    for reference in action_references
    if not re.fullmatch(r"[^@]+@[0-9a-f]{40}", reference)
]
check(bool(action_references), "GitHub workflows use reviewed Actions")
check(
    not unpinned_actions,
    f"GitHub Actions are pinned to commit SHAs "
    f"({', '.join(unpinned_actions) or 'all pinned'})",
)

print("== Skill ==")
skill_md = SKILL / "SKILL.md"
frontmatter = skill_frontmatter(skill_md)
check(frontmatter.get("name") == "agent-team", "skill name is agent-team")
check(bool(frontmatter.get("description")), "skill description is present")
check(
    set(frontmatter) == {"name", "description"},
    "skill frontmatter has only name and description",
)
skill_text = skill_md.read_text(encoding="utf-8")
check("scripts/state.py" in skill_text, "skill delegates state changes to state.py")
check(
    "Spawn a real subagent" in skill_text, "skill explicitly authorizes real subagents"
)
check("five unresolved review rounds" in skill_text, "skill documents planning limit")
check(
    "eight complete Verifier rounds" in skill_text, "skill documents verification limit"
)
check(
    "JSON-file `request` interface" in skill_text,
    "skill requires safe JSON state requests",
)
check(
    "expected.artifact_token" in skill_text, "skill requires evidence-gated artifacts"
)
check("request_id" in skill_text, "skill requires idempotent request identifiers")
check(
    "V1 rejects `user_path`, `worktree`" in skill_text,
    "skill limits V1 delivery modes",
)
check(
    "one unfinished Agent Team run per workspace" in skill_text,
    "skill documents the single-active-run boundary",
)
check(
    "expected.subject" in skill_text,
    "skill preserves checkpoint subject snapshots",
)
check(
    "NEEDS_TEST_PERSISTENCE" in skill_text,
    "skill requires critical ad hoc tests to be persisted",
)
check("risk_update" in skill_text, "skill documents risk lifecycle updates")
check("completion_scope" in skill_text, "skill requires explicit DONE scope")
check("tracked by Git" in skill_text, "skill refuses tracked control data")

openai_yaml = (SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8")
check('display_name: "Agent Team"' in openai_yaml, "skill UI display name is present")
check("$agent-team" in openai_yaml, "skill UI prompt invokes $agent-team")
check(
    "allow_implicit_invocation: false" in openai_yaml, "implicit invocation is disabled"
)

print("== Role references ==")
role_contracts = {
    "planner.md": (
        "PLAN_READY",
        "FINAL_ACCEPT",
        "FINAL_REJECT",
        "## Goal and scope",
        "## Write scope",
        "## Implementation steps",
        "## Risks and recovery",
        "## Acceptance criteria",
        "## Final review",
        "## Residual risks",
    ),
    "plan-reviewer.md": (
        "APPROVED",
        "NEEDS_REVISION",
        "## Planning cycle",
        "### Findings",
    ),
    "executor.md": (
        "EXECUTION_DONE",
        "BLOCKED",
        "## Files changed",
        "## Execution iterations",
        "## Validation",
        "## Residual risks",
    ),
    "verifier.md": (
        "PASS",
        "FAIL",
        "NEEDS_TEST_PERSISTENCE",
        "## Verification cycle",
        "| # | Criterion |",
        "### Test persistence",
        "### Defects and unmet criteria",
    ),
}
for filename, contract_terms in role_contracts.items():
    path = SKILL / "references" / filename
    check(path.is_file(), f"{filename} exists")
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    for term in contract_terms:
        check(term in text, f"{filename} declares {term}")
check((SKILL / "references" / "protocol.md").is_file(), "protocol.md exists")
check(
    (SKILL / "references" / "state-requests.md").is_file(),
    "state-requests.md exists",
)

print("== State script ==")
state_script = SKILL / "scripts" / "state.py"
check(state_script.is_file(), "state.py exists")
# Fixed interpreter/script argv; no shell.
result = subprocess.run(  # nosec B603
    [sys.executable, str(state_script), "--help"],
    capture_output=True,
    text=True,
    check=False,
)
check(result.returncode == 0, "state.py CLI loads successfully")
check(
    "guard" in result.stdout and "request" in result.stdout,
    "state.py exposes only guarded JSON request commands",
)
state_text = state_script.read_text(encoding="utf-8")
check("REQUEST_LOCK_FILE" in state_text, "state.py serializes JSON requests")
check(
    "ROLE_REQUIRED_SECTIONS" in state_text,
    "state.py enforces structured role evidence",
)
check(
    "pending_test_persistence" in state_text,
    "state.py persists unresolved critical test evidence",
)
check("RISK_TRANSITIONS" in state_text, "state.py enforces risk lifecycle transitions")
check(
    "executor_internal_repair_count" in state_text,
    "state.py tracks Executor internal repairs",
)
check("completion_scope" in state_text, "state.py persists explicit DONE scope")
check(
    "PRIVATE_DIRECTORY_MODE = 0o700" in state_text
    and "PRIVATE_FILE_MODE = 0o600" in state_text,
    "state.py defines explicit private control-data modes",
)
check(
    "reject_tracked_control_dir" in state_text,
    "state.py rejects Git-tracked control data",
)

print("== Public documentation ==")
for relative in (
    "README.md",
    "README.zh-CN.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "requirements-dev.txt",
    "scripts/smoke_local_install.py",
    "docs/architecture.md",
    "docs/security-and-privacy.md",
    "docs/development-and-release.md",
    "docs/explicit-invocation-guard.md",
    "docs/pre-release-report.md",
):
    check((ROOT / relative).is_file(), f"{relative} exists")
requirements = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8").splitlines()
check(
    requirements == ["bandit==1.9.4", "mypy==2.3.0", "PyYAML==6.0.3", "ruff==0.16.2"],
    "development dependencies are explicitly pinned",
)

markdown_files = [
    path
    for path in ROOT.rglob("*.md")
    if not IGNORED_SCAN_PARTS.intersection(path.parts)
]
broken_links: list[str] = []
for markdown_path in markdown_files:
    content = markdown_path.read_text(encoding="utf-8")
    for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", content):
        if target.startswith(("https://", "http://", "mailto:", "#")):
            continue
        relative_target = target.split("#", 1)[0]
        if relative_target and not (markdown_path.parent / relative_target).exists():
            broken_links.append(f"{markdown_path.relative_to(ROOT)} -> {target}")
check(
    not broken_links,
    f"relative Markdown links resolve ({', '.join(broken_links) or 'all resolved'})",
)

print("== Placeholder and secret scan ==")
text_files = [
    path
    for path in ROOT.rglob("*")
    if path.is_file()
    and not IGNORED_SCAN_PARTS.intersection(path.parts)
    and path != Path(__file__).resolve()
]
todo_hits: list[str] = []
secret_hits: list[str] = []
secret_patterns = [
    re.compile(r"gh[oprsu]_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"/Users/[A-Za-z0-9._-]+/"),
    re.compile(r"/home/[A-Za-z0-9._-]+/"),
    re.compile(r"[A-Za-z]:\\\\Users\\\\[^\\\\\s]+\\\\"),
]
email_pattern = re.compile(
    r"\b[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)
allowed_example_domains = {"example.com", "example.org", "example.net"}
email_hits: list[str] = []
for path in text_files:
    try:
        content = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    if "[TODO:" in content:
        todo_hits.append(str(path.relative_to(ROOT)))
    for pattern in secret_patterns:
        if pattern.search(content):
            secret_hits.append(str(path.relative_to(ROOT)))
            break
    for email in email_pattern.findall(content):
        domain = email.rsplit("@", 1)[1].lower()
        if domain not in allowed_example_domains:
            email_hits.append(str(path.relative_to(ROOT)))
            break
dangerous_names = {".env", "credentials.json", "id_rsa", "id_ed25519"}
dangerous_suffixes = {".key", ".p12", ".pfx", ".pem"}
sensitive_file_hits = [
    str(path.relative_to(ROOT))
    for path in ROOT.rglob("*")
    if path.is_file()
    and not IGNORED_SCAN_PARTS.intersection(path.parts)
    and (path.name in dangerous_names or path.suffix.lower() in dangerous_suffixes)
]
symlink_hits = [
    str(path.relative_to(ROOT)) for path in PLUGIN.rglob("*") if path.is_symlink()
]
check(
    not todo_hits, f"no scaffold TODO placeholders ({', '.join(todo_hits) or 'none'})"
)
check(
    not secret_hits,
    f"no secrets, PII, or host paths ({', '.join(secret_hits) or 'none'})",
)
check(
    not email_hits,
    f"no public personal email addresses ({', '.join(email_hits) or 'none'})",
)
check(
    not sensitive_file_hits,
    f"no sensitive credential files ({', '.join(sensitive_file_hits) or 'none'})",
)
check(
    not symlink_hits,
    f"plugin package contains no symlinks ({', '.join(symlink_hits) or 'none'})",
)

print()
if ERRORS:
    print(f"FAILED: {len(ERRORS)} validation problem(s).")
    raise SystemExit(1)
print(f"All checks passed across {len(text_files)} files.")
