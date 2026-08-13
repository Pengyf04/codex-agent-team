from __future__ import annotations

import importlib.util
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = (
    ROOT
    / "plugins"
    / "codex-agent-team"
    / "skills"
    / "agent-team"
    / "scripts"
    / "state.py"
)
SPEC = importlib.util.spec_from_file_location(
    "agent_team_state_permissions", STATE_PATH
)
assert SPEC and SPEC.loader
state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(state)


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class AgentTeamPermissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name) / "workspace"
        self.workspace.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @unittest.skipIf(os.name == "nt", "POSIX permission bits do not model Windows ACLs")
    def test_guard_enforces_private_control_and_request_modes(self) -> None:
        control = self.workspace / ".agent-team"
        runs = control / "runs"
        runs.mkdir(parents=True)
        control.chmod(0o777)
        runs.chmod(0o777)

        request_path = state.guarded_request_path(self.workspace, create=True)

        self.assertEqual(mode(control), 0o700)
        self.assertEqual(mode(runs), 0o700)
        self.assertEqual(mode(request_path), 0o600)

        request_path.chmod(0o644)
        state.guarded_request_path(self.workspace, create=True)
        self.assertEqual(mode(request_path), 0o600)

    @unittest.skipIf(os.name == "nt", "POSIX permission bits do not model Windows ACLs")
    def test_run_artifacts_and_locks_remain_private(self) -> None:
        run_dir = cast(
            Path,
            state.initialize_run(
                workspace=self.workspace,
                slug="permission-check",
                task="Verify private control data.",
                acceptance_criteria=["Control data is private."],
                delivery_mode="staged",
                timestamp="20260813-120000",
            ),
        )
        state.load_state(run_dir)

        self.assertEqual(mode(run_dir), 0o700)
        self.assertEqual(mode(run_dir / "deliverable"), 0o700)
        for filename in (
            "state.json",
            "task.md",
            "plan.md",
            "review-log.md",
            "verify-report.md",
            "final-review.md",
            "deliverables.md",
            "risk-log.md",
            ".state.lock",
        ):
            self.assertEqual(mode(run_dir / filename), 0o600, filename)

        (run_dir / "state.json").chmod(0o644)
        state.load_state(run_dir)
        self.assertEqual(mode(run_dir / "state.json"), 0o600)

    @unittest.skipUnless(shutil.which("git"), "Git is required for tracked-path test")
    def test_tracked_control_directory_is_rejected(self) -> None:
        subprocess.run(["git", "-C", str(self.workspace), "init", "-q"], check=True)
        control = self.workspace / ".agent-team"
        control.mkdir()
        (control / "tracked.txt").write_text(
            "must not contain task data\n", encoding="utf-8"
        )
        subprocess.run(
            ["git", "-C", str(self.workspace), "add", ".agent-team/tracked.txt"],
            check=True,
        )

        with self.assertRaisesRegex(state.StateError, "tracked by Git"):
            state.guarded_request_path(self.workspace, create=True)


if __name__ == "__main__":
    unittest.main()
