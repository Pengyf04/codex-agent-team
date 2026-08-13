#!/usr/bin/env python3
"""Exercise local Marketplace install, removal, and reinstall in an isolated Codex home."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil

# Invokes the resolved local Codex executable without a shell.
import subprocess  # nosec B404
import tempfile
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PLUGIN = ROOT / "plugins" / "codex-agent-team"
PLUGIN_ID = "codex-agent-team@codex-agent-team"
VERSION = "0.1.0"


class SmokeError(RuntimeError):
    """Raised when an isolated install invariant is not satisfied."""


def run_json(codex: Path, env: dict[str, str], *args: str) -> dict[str, Any]:
    completed = subprocess.run(  # nosec B603
        [str(codex), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if completed.returncode != 0:
        raise SmokeError(
            f"codex {' '.join(args)} failed with {completed.returncode}: "
            f"{completed.stderr.strip()}"
        )
    try:
        value: Any = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SmokeError(f"codex {' '.join(args)} returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise SmokeError(f"codex {' '.join(args)} did not return a JSON object")
    return cast(dict[str, Any], value)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeError(message)


def package_digest(root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise SmokeError(f"plugin package contains a symlink: {relative}")
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_file():
            files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def find_plugin(items: Any, *, installed: bool) -> dict[str, Any]:
    if not isinstance(items, list):
        raise SmokeError("plugin list JSON has an invalid collection")
    matches = [
        item
        for item in items
        if isinstance(item, dict) and item.get("pluginId") == PLUGIN_ID
    ]
    require(len(matches) == 1, f"expected exactly one {PLUGIN_ID} listing")
    item = cast(dict[str, Any], matches[0])
    require(item.get("installed") is installed, "plugin installed flag is incorrect")
    require(item.get("version") == VERSION, "plugin version is incorrect")
    return item


def validate_installed_plugin(
    codex: Path,
    env: dict[str, str],
    codex_home: Path,
    source_digest: dict[str, str],
    attempt: str,
) -> None:
    installed = run_json(codex, env, "plugin", "add", PLUGIN_ID, "--json")
    require(installed.get("pluginId") == PLUGIN_ID, f"{attempt} ID mismatch")
    require(installed.get("version") == VERSION, f"{attempt} version mismatch")
    installed_path_value = installed.get("installedPath")
    require(isinstance(installed_path_value, str), f"{attempt} path is missing")
    installed_path = Path(cast(str, installed_path_value)).resolve(strict=True)
    require(
        installed_path.is_relative_to(codex_home.resolve(strict=True)),
        f"{attempt} escaped the isolated Codex home",
    )
    require(
        package_digest(installed_path) == source_digest,
        f"{attempt} cache differs from the source Plugin",
    )

    listing = run_json(codex, env, "plugin", "list", "--json")
    item = find_plugin(listing.get("installed"), installed=True)
    require(item.get("enabled") is True, f"{attempt} is not enabled")


def exercise_plugin_lifecycle(
    codex: Path,
    env: dict[str, str],
    codex_home: Path,
    source_digest: dict[str, str],
) -> None:
    validate_installed_plugin(codex, env, codex_home, source_digest, "install")
    validate_installed_plugin(
        codex, env, codex_home, source_digest, "installed-state update"
    )

    removed = run_json(codex, env, "plugin", "remove", PLUGIN_ID, "--json")
    require(removed.get("pluginId") == PLUGIN_ID, "update-cycle removal failed")

    validate_installed_plugin(codex, env, codex_home, source_digest, "reinstall")
    removed = run_json(codex, env, "plugin", "remove", PLUGIN_ID, "--json")
    require(removed.get("pluginId") == PLUGIN_ID, "reinstall removal failed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--codex",
        default="codex",
        help="Codex executable name or path (default: codex)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    codex_value = str(args.codex)
    codex_path = shutil.which(codex_value)
    if codex_path is None:
        raise SmokeError(f"Codex executable not found: {codex_value}")
    codex = Path(codex_path).resolve(strict=True)
    require(SOURCE_PLUGIN.is_dir(), f"source Plugin is missing: {SOURCE_PLUGIN}")
    source_digest = package_digest(SOURCE_PLUGIN)

    with tempfile.TemporaryDirectory(prefix="codex-agent-team-smoke-") as temporary:
        isolated_home = Path(temporary)
        codex_home = isolated_home / ".codex"
        codex_home.mkdir(mode=0o700)
        env = os.environ.copy()
        env["HOME"] = str(isolated_home)
        env["CODEX_HOME"] = str(codex_home)

        initial = run_json(codex, env, "plugin", "marketplace", "list", "--json")
        require(initial.get("marketplaces") == [], "isolated Codex home is not empty")

        added_marketplace = run_json(
            codex, env, "plugin", "marketplace", "add", str(ROOT), "--json"
        )
        require(
            added_marketplace.get("marketplaceName") == "codex-agent-team",
            "Marketplace name does not match",
        )

        available = run_json(codex, env, "plugin", "list", "--available", "--json")
        find_plugin(available.get("available"), installed=False)

        exercise_plugin_lifecycle(codex, env, codex_home, source_digest)

        removed_marketplace = run_json(
            codex,
            env,
            "plugin",
            "marketplace",
            "remove",
            "codex-agent-team",
            "--json",
        )
        require(
            removed_marketplace.get("marketplaceName") == "codex-agent-team",
            "Marketplace removal failed",
        )
        final = run_json(codex, env, "plugin", "marketplace", "list", "--json")
        require(final.get("marketplaces") == [], "Marketplace remained after removal")

    print(
        "PASS: isolated Marketplace discovery, install, installed-state update, "
        "cache parity, removal, reinstall, and cleanup"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
