from __future__ import annotations

import importlib.util
import json
import re
import runpy
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "codex-agent-team"
MANIFEST_PATH = PLUGIN / ".codex-plugin" / "plugin.json"
MARKETPLACE_PATH = ROOT / ".agents" / "plugins" / "marketplace.json"
STATE_PATH = PLUGIN / "skills" / "agent-team" / "scripts" / "state.py"
SMOKE_PATH = ROOT / "scripts" / "smoke_local_install.py"

EXPECTED_PLUGIN_FILES = {
    ".codex-plugin/plugin.json",
    "skills/agent-team/SKILL.md",
    "skills/agent-team/agents/openai.yaml",
    "skills/agent-team/references/executor.md",
    "skills/agent-team/references/plan-reviewer.md",
    "skills/agent-team/references/planner.md",
    "skills/agent-team/references/protocol.md",
    "skills/agent-team/references/state-requests.md",
    "skills/agent-team/references/verifier.md",
    "skills/agent-team/scripts/state.py",
}


def load_json(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


class AgentTeamPackageTests(unittest.TestCase):
    def test_release_identity_and_marketplace_are_consistent(self) -> None:
        manifest = load_json(MANIFEST_PATH)
        marketplace = load_json(MARKETPLACE_PATH)
        entries = cast(list[dict[str, Any]], marketplace["plugins"])

        self.assertEqual(manifest["name"], "codex-agent-team")
        self.assertEqual(manifest["version"], "0.1.0")
        self.assertEqual(manifest["author"]["name"], "Pengyf04")
        self.assertNotIn("email", manifest["author"])
        self.assertEqual(
            manifest["repository"], "https://github.com/Pengyf04/codex-agent-team"
        )
        self.assertEqual(marketplace["name"], "codex-agent-team")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], manifest["name"])
        self.assertEqual(entries[0]["source"]["path"], "./plugins/codex-agent-team")

    def test_plugin_package_has_only_reviewed_files_and_no_symlinks(self) -> None:
        actual = {
            path.relative_to(PLUGIN).as_posix()
            for path in PLUGIN.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        }
        symlinks = [
            path.relative_to(PLUGIN).as_posix()
            for path in PLUGIN.rglob("*")
            if path.is_symlink()
        ]

        self.assertEqual(actual, EXPECTED_PLUGIN_FILES)
        self.assertEqual(symlinks, [])

    def test_state_cli_loads_without_site_packages(self) -> None:
        completed = subprocess.run(  # nosec B603
            [sys.executable, "-I", "-S", str(STATE_PATH), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("guard", completed.stdout)
        self.assertIn("request", completed.stdout)

    def test_install_digest_ignores_transient_python_bytecode(self) -> None:
        namespace = runpy.run_path(str(SMOKE_PATH))
        package_digest = cast(
            Callable[[Path], dict[str, str]], namespace["package_digest"]
        )

        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            (package / "state.py").write_text("VALUE = 1\n", encoding="utf-8")
            before = package_digest(package)

            cache = package / "__pycache__"
            cache.mkdir()
            (cache / "state.cpython-314.pyc").write_bytes(b"transient")
            (package / "state.pyc").write_bytes(b"transient")

            self.assertEqual(package_digest(package), before)

    def test_smoke_covers_installed_state_update_before_reinstall(self) -> None:
        spec = importlib.util.spec_from_file_location("smoke_local_install", SMOKE_PATH)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader if spec is not None else None)
        if spec is None or spec.loader is None:
            self.fail("could not load smoke_local_install.py")
        module = cast(Any, importlib.util.module_from_spec(spec))
        spec.loader.exec_module(module)

        calls: list[tuple[str, ...]] = []
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary)
            installed_path = codex_home / "plugins" / "codex-agent-team"
            installed_path.mkdir(parents=True)
            (installed_path / "manifest.txt").write_text("release\n", encoding="utf-8")
            source_digest = module.package_digest(installed_path)

            def fake_run_json(
                _codex: Path, _env: dict[str, str], *args: str
            ) -> dict[str, Any]:
                calls.append(args)
                if args == ("plugin", "add", module.PLUGIN_ID, "--json"):
                    return {
                        "pluginId": module.PLUGIN_ID,
                        "version": module.VERSION,
                        "installedPath": str(installed_path),
                    }
                if args == ("plugin", "list", "--json"):
                    return {
                        "installed": [
                            {
                                "pluginId": module.PLUGIN_ID,
                                "installed": True,
                                "version": module.VERSION,
                                "enabled": True,
                            }
                        ]
                    }
                if args == ("plugin", "remove", module.PLUGIN_ID, "--json"):
                    return {"pluginId": module.PLUGIN_ID}
                self.fail(f"unexpected smoke command: {args}")

            original_run_json = module.run_json
            module.run_json = fake_run_json
            try:
                module.exercise_plugin_lifecycle(
                    Path("/fake/codex"), {}, codex_home, source_digest
                )
            finally:
                module.run_json = original_run_json

        add = ("plugin", "add", module.PLUGIN_ID, "--json")
        remove = ("plugin", "remove", module.PLUGIN_ID, "--json")
        self.assertEqual(calls.count(add), 3)
        self.assertEqual(calls.count(remove), 2)
        self.assertLess(calls.index(add, calls.index(add) + 1), calls.index(remove))

    def test_public_docs_and_ignore_rules_cover_install_lifecycle(self) -> None:
        for relative in (
            "README.md",
            "README.zh-CN.md",
            "SECURITY.md",
            "CONTRIBUTING.md",
            "CHANGELOG.md",
            "LICENSE",
            "requirements-dev.txt",
            "docs/architecture.md",
            "docs/security-and-privacy.md",
            "docs/development-and-release.md",
            "docs/explicit-invocation-guard.md",
            "docs/pre-release-report.md",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)

        english = (ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        for command in (
            "codex plugin marketplace add Pengyf04/codex-agent-team",
            "codex plugin add codex-agent-team@codex-agent-team",
            "codex plugin marketplace upgrade codex-agent-team",
            "codex plugin remove codex-agent-team@codex-agent-team",
        ):
            self.assertIn(command, english)
            self.assertIn(command, chinese)
        self.assertIn("Windows | Experimental", english)
        self.assertIn("Windows | 实验性", chinese)

        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        for pattern in (".agent-team/", ".mypy_cache/", ".ruff_cache/", ".env"):
            self.assertIn(pattern, ignored)

    def test_workflow_actions_are_commit_pinned(self) -> None:
        workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
        references: list[str] = []
        content = ""
        for workflow in workflows:
            workflow_text = workflow.read_text(encoding="utf-8")
            content += workflow_text
            for line in workflow_text.splitlines():
                match = re.match(r"^\s*-?\s*uses:\s*([^\s#]+)", line)
                if match:
                    references.append(match.group(1))

        self.assertGreater(len(references), 0)
        self.assertTrue(
            all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", item) for item in references)
        )
        self.assertIn("github/codeql-action/init@", content)
        self.assertIn("actions/dependency-review-action@", content)


if __name__ == "__main__":
    unittest.main()
