from __future__ import annotations

import errno
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast
from unittest import mock

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
    "agent_team_state_adversarial", STATE_PATH
)
assert SPEC and SPEC.loader
state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(state)


class AgentTeamAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.request_counter = 0

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request_payload(self, **values: Any) -> dict[str, Any]:
        self.request_counter += 1
        return {
            "request_id": f"req-{self.request_counter:032x}",
            **values,
        }

    def initialize(
        self,
        workspace: Path | None = None,
        mode: str = "workspace",
        output_path: str | None = None,
    ) -> Path:
        target = workspace or self.root / "workspace"
        target.mkdir(parents=True, exist_ok=True)
        return cast(
            Path,
            state.initialize_run(
                workspace=target,
                slug="adversarial",
                task="Exercise state-machine safety.",
                acceptance_criteria=["All safety invariants remain true."],
                delivery_mode=mode,
                output_path=output_path,
                timestamp="20260811-120000",
            ),
        )

    def write_expected_artifact(self, run_dir: Path, marker: str) -> str:
        current = state.load_state(run_dir)
        expected = current["expected"]
        role = expected["role"]
        if marker != "BLOCKED":
            artifact = run_dir / expected["artifact"]
            token = expected["artifact_token"].replace("<MARKER>", marker)
            evidence = f"\nDecision: {marker}\n\n{token}\n"
            if role == "planner":
                artifact.write_text(
                    "# Plan\n\n## Goal and scope\n\nExercise only the requested "
                    "state-machine path.\n\n## Write scope\n\nLimit writes to the target "
                    "workspace and owned run artifact.\n\n## Implementation steps\n\n1. "
                    "Inspect state.\n2. Apply the expected transition.\n3. Verify all "
                    "invariants and negative cases.\n\n## Risks and recovery\n\nRestore "
                    "the approved files and block on any ambiguous state.\n\n"
                    "## Acceptance criteria\n\n1. Stay "
                    "safe.\n2. Preserve unrelated files.\n3. Reject every invalid transition.\n"
                    + evidence,
                    encoding="utf-8",
                )
            elif role == "plan_reviewer":
                with artifact.open("a", encoding="utf-8") as handle:
                    handle.write(
                        "\n## Planning cycle 1, review round 1\n\n### Findings\n\n"
                        "The plan is correct, bounded, independently testable, and explicit "
                        "about the write scope and recovery behavior. No blocking gap remains.\n"
                        + evidence
                    )
            elif role == "executor":
                artifact.write_text(
                    "# Deliverables\n\n## Files changed\n\nOnly approved files were "
                    "changed and unrelated baseline content was preserved.\n\n"
                    "## Execution iterations\n\nNo internal repair iteration was needed "
                    "in this dispatch.\n\n## Validation\n\nRan the approved positive, "
                    "negative, and boundary checks; all observed "
                    "results match the plan.\n\n## Residual risks\n\nNone known. No commit, "
                    "push, or destructive operation occurred.\n" + evidence,
                    encoding="utf-8",
                )
            elif role == "verifier":
                with artifact.open("a", encoding="utf-8") as handle:
                    handle.write(
                        "\n## Verification cycle 1, round 1\n\n"
                        "| # | Criterion | Method | Expected | Actual evidence | Result |\n"
                        "|---|---|---|---|---|---|\n"
                        "| 1 | Safety invariants | Independent test | Pass | All positive, "
                        "negative, and boundary checks passed | PASS |\n\n"
                        "### Test persistence\n\nAll acceptance-critical test evidence is "
                        "persisted and reached by the standard validation command.\n\n"
                        "### Defects and unmet criteria\n\nNone; write scope also matched.\n"
                        + evidence
                    )
            elif role == "planner_final":
                with artifact.open("a", encoding="utf-8") as handle:
                    handle.write(
                        "\n## Final review\n\n"
                        "| # | Criterion | Delivery evidence | Result |\n"
                        "|---|---|---|---|\n"
                        "| 1 | Safety invariants | State and verifier evidence agree | PASS |\n"
                        "| 2 | Scope | Actual changes remain approved | PASS |\n\n"
                        "## Residual risks\n\nNone known after final comparison.\n"
                        + evidence
                    )
        return cast(str, role)

    def apply_expected_marker(
        self,
        run_dir: Path,
        marker: str,
        detail: str = "",
        *,
        test_evidence: list[dict[str, Any]] | None = None,
        execution_iterations: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        role = self.write_expected_artifact(run_dir, marker)
        marker_fields: dict[str, Any] = {}
        if role == "executor" and marker == "EXECUTION_DONE":
            marker_fields["execution_iterations"] = execution_iterations or []
        if role == "verifier" and marker != "BLOCKED":
            marker_fields["test_evidence"] = test_evidence or []
        return cast(
            dict[str, Any],
            state.apply_marker(run_dir, marker, detail, role=role, **marker_fields),
        )

    def completion_scope(self) -> dict[str, Any]:
        return {
            "summary": "Approved adversarial-test delivery.",
            "completed": ["Safety invariants", "Independent verification"],
            "excluded": ["Production deployment"],
            "deployment_status": "not_deployed",
        }

    def evidence_record(
        self, evidence_id: str, *, kind: str, risk_ids: list[str] | None = None
    ) -> dict[str, Any]:
        persisted = kind == "formal"
        return {
            "id": evidence_id,
            "description": "Acceptance-critical adversarial boundary check.",
            "kind": kind,
            "critical": True,
            "persisted": persisted,
            "target": "tests/test_boundary.py::test_case" if persisted else "",
            "risk_ids": risk_ids or [],
        }

    def advance_to_execution(self, run_dir: Path) -> None:
        state.start_planning(run_dir)
        self.apply_expected_marker(run_dir, "PLAN_READY")
        self.apply_expected_marker(run_dir, "APPROVED")
        state.confirm_checkpoint(run_dir, "plan", "approve")

    def test_role_marker_requires_fresh_artifact_evidence(self) -> None:
        run_dir = self.initialize()
        state.start_planning(run_dir)

        with self.assertRaises(state.StateError):
            state.apply_marker(run_dir, "PLAN_READY", role="planner")

        current = state.load_state(run_dir)
        self.assertEqual(current["status"], state.PLANNING)
        self.assertIsNone(current["last_marker"])

    def test_padded_footer_is_not_substantive_evidence(self) -> None:
        run_dir = self.initialize()
        state.start_planning(run_dir)
        current = state.load_state(run_dir)
        token = current["expected"]["artifact_token"].replace("<MARKER>", "PLAN_READY")
        (run_dir / "plan.md").write_text(
            "# Plan\n\n" + ("padding without required structure " * 20) + "\n\n"
            "Decision: PLAN_READY\n\n" + token + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(state.StateError, "missing required evidence"):
            state.apply_marker(run_dir, "PLAN_READY", role="planner")

    def test_planner_must_include_every_v1_protocol_section(self) -> None:
        run_dir = self.initialize()
        state.start_planning(run_dir)
        current = state.load_state(run_dir)
        token = current["expected"]["artifact_token"].replace("<MARKER>", "PLAN_READY")
        (run_dir / "plan.md").write_text(
            "# Plan\n\n## Write scope\n\nOnly approved files.\n\n"
            "## Implementation steps\n\n1. Inspect.\n2. Change.\n3. Verify.\n\n"
            "## Acceptance criteria\n\n1. Tests pass.\n\n"
            + ("Substantive implementation and validation detail. " * 12)
            + f"\n\nDecision: PLAN_READY\n\n{token}\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            state.StateError, "## goal and scope.*## risks and recovery"
        ):
            state.apply_marker(run_dir, "PLAN_READY", role="planner")

    def test_blocked_resume_cannot_jump_to_another_phase(self) -> None:
        run_dir = self.initialize()
        state.start_planning(run_dir)
        state.block_run(run_dir, "simulated blocker")

        with self.assertRaises(state.StateError):
            state.resume_run(run_dir, state.FINAL_REVIEW, "unsafe jump")

    def test_delivery_change_after_review_forces_replanning(self) -> None:
        run_dir = self.initialize(mode="staged")
        state.start_planning(run_dir)
        self.apply_expected_marker(run_dir, "PLAN_READY")
        self.apply_expected_marker(run_dir, "APPROVED")

        changed = state.update_delivery(run_dir, "workspace", None)

        self.assertEqual(changed["status"], state.PLANNING)
        self.assertEqual(changed["planning_cycle"], 2)
        self.assertEqual(changed["expected"]["role"], "planner")

    def test_plan_cannot_change_after_review_before_human_approval(self) -> None:
        run_dir = self.initialize()
        state.start_planning(run_dir)
        self.apply_expected_marker(run_dir, "PLAN_READY")
        self.apply_expected_marker(run_dir, "APPROVED")
        (run_dir / "plan.md").write_text(
            "# Plan\n\nSilently replaced after review.\n", encoding="utf-8"
        )

        recovered = state.confirm_checkpoint(run_dir, "plan", "approve")

        self.assertEqual(recovered["status"], state.PLANNING)
        self.assertEqual(recovered["expected"]["role"], "planner")
        self.assertEqual(recovered["planning_cycle"], 2)

    def test_plan_cannot_change_between_planner_and_reviewer(self) -> None:
        run_dir = self.initialize()
        state.start_planning(run_dir)
        self.apply_expected_marker(run_dir, "PLAN_READY")
        (run_dir / "plan.md").write_text(
            "# Plan\n\nReplaced before review result.\n", encoding="utf-8"
        )

        recovered = state.load_state(run_dir)

        self.assertEqual(recovered["status"], state.PLANNING)
        self.assertEqual(recovered["expected"]["role"], "planner")
        self.assertEqual(recovered["planning_cycle"], 2)

    def test_delivery_cannot_change_after_final_review_before_approval(self) -> None:
        run_dir = self.initialize(mode="staged")
        self.advance_to_execution(run_dir)
        deliverable = run_dir / "deliverable" / "result.txt"
        deliverable.write_text("verified\n", encoding="utf-8")
        self.apply_expected_marker(run_dir, "EXECUTION_DONE")
        self.apply_expected_marker(run_dir, "PASS")
        self.apply_expected_marker(run_dir, "FINAL_ACCEPT")
        deliverable.write_text("replaced after final review\n", encoding="utf-8")

        recovered = state.confirm_checkpoint(
            run_dir, "final", "approve", completion_scope=self.completion_scope()
        )

        self.assertEqual(recovered["status"], state.EXECUTING)
        self.assertEqual(recovered["expected"]["role"], "executor")
        self.assertEqual(recovered["verification_cycle"], 2)

    def test_plan_change_after_approval_forces_replanning(self) -> None:
        run_dir = self.initialize(mode="staged")
        self.advance_to_execution(run_dir)
        (run_dir / "plan.md").write_text(
            "# Replaced after approval\n\nThis is not the approved plan.\n",
            encoding="utf-8",
        )

        recovered = state.load_state(run_dir)

        self.assertEqual(recovered["status"], state.PLANNING)
        self.assertEqual(recovered["expected"]["role"], "planner")
        self.assertIsNone(recovered["approved_plan_subject"])

    def test_verify_report_cannot_change_after_pass(self) -> None:
        run_dir = self.initialize(mode="staged")
        self.advance_to_execution(run_dir)
        (run_dir / "deliverable" / "result.txt").write_text(
            "verified\n", encoding="utf-8"
        )
        self.apply_expected_marker(run_dir, "EXECUTION_DONE")
        self.apply_expected_marker(run_dir, "PASS")
        with (run_dir / "verify-report.md").open("a", encoding="utf-8") as handle:
            handle.write("\nForged after PASS.\n")

        recovered = state.load_state(run_dir)

        self.assertEqual(recovered["status"], state.EXECUTING)
        self.assertEqual(recovered["expected"]["role"], "executor")
        self.assertEqual(recovered["verification_cycle"], 2)

    def test_staged_control_named_directory_is_part_of_delivery_snapshot(self) -> None:
        run_dir = self.initialize(mode="staged")
        self.advance_to_execution(run_dir)
        hidden = run_dir / "deliverable" / ".agent-team" / "hidden.txt"
        hidden.parent.mkdir(parents=True)
        hidden.write_text("verified\n", encoding="utf-8")
        self.apply_expected_marker(run_dir, "EXECUTION_DONE")
        self.apply_expected_marker(run_dir, "PASS")
        self.apply_expected_marker(run_dir, "FINAL_ACCEPT")
        hidden.write_text("changed after review\n", encoding="utf-8")

        recovered = state.confirm_checkpoint(
            run_dir, "final", "approve", completion_scope=self.completion_scope()
        )

        self.assertEqual(recovered["status"], state.EXECUTING)
        self.assertEqual(recovered["expected"]["role"], "executor")

    def test_delivery_cannot_change_between_verifier_and_final_review(self) -> None:
        run_dir = self.initialize(mode="staged")
        self.advance_to_execution(run_dir)
        deliverable = run_dir / "deliverable" / "result.txt"
        deliverable.write_text("verified\n", encoding="utf-8")
        self.apply_expected_marker(run_dir, "EXECUTION_DONE")
        self.apply_expected_marker(run_dir, "PASS")
        deliverable.write_text("changed before final review\n", encoding="utf-8")

        recovered = state.load_state(run_dir)

        self.assertEqual(recovered["status"], state.EXECUTING)
        self.assertEqual(recovered["expected"]["role"], "executor")

    def test_missing_pass_evidence_is_restored_for_a_new_verification_cycle(
        self,
    ) -> None:
        for missing_name in ("deliverables.md", "verify-report.md"):
            with self.subTest(missing_name=missing_name):
                workspace = self.root / f"missing-{missing_name}"
                run_dir = self.initialize(workspace=workspace, mode="staged")
                self.advance_to_execution(run_dir)
                (run_dir / "deliverable" / "result.txt").write_text(
                    "verified\n", encoding="utf-8"
                )
                self.apply_expected_marker(run_dir, "EXECUTION_DONE")
                self.apply_expected_marker(run_dir, "PASS")
                (run_dir / missing_name).unlink()

                recovered = state.load_state(run_dir)

                self.assertEqual(recovered["status"], state.EXECUTING)
                self.assertEqual(recovered["expected"]["role"], "executor")
                self.assertTrue((run_dir / missing_name).is_file())
                advanced = self.apply_expected_marker(run_dir, "EXECUTION_DONE")
                self.assertEqual(advanced["status"], state.VERIFYING)

    def test_final_revision_checks_the_approved_plan_before_execution(self) -> None:
        run_dir = self.initialize(mode="staged")
        self.advance_to_execution(run_dir)
        (run_dir / "deliverable" / "result.txt").write_text(
            "verified\n", encoding="utf-8"
        )
        self.apply_expected_marker(run_dir, "EXECUTION_DONE")
        self.apply_expected_marker(run_dir, "PASS")
        self.apply_expected_marker(run_dir, "FINAL_ACCEPT")
        (run_dir / "plan.md").write_text(
            "# Changed after checkpoint A\n", encoding="utf-8"
        )

        recovered = state.confirm_checkpoint(
            run_dir, "final", "revise", "adjust the result"
        )

        self.assertEqual(recovered["status"], state.PLANNING)
        self.assertEqual(recovered["expected"]["role"], "planner")
        self.assertIsNone(recovered["approved_plan_subject"])

    def test_removed_delivery_modes_are_rejected_before_path_handling(self) -> None:
        workspace = self.root / "v1-workspace"
        workspace.mkdir()

        for mode in ("user_path", "worktree"):
            with (
                self.subTest(mode=mode),
                self.assertRaisesRegex(state.StateError, "unsupported delivery mode"),
            ):
                self.initialize(
                    workspace,
                    mode=mode,
                    output_path="../must-not-be-resolved",
                )

    def test_control_root_symlink_cannot_escape_workspace(self) -> None:
        workspace = self.root / "workspace"
        outside = self.root / "outside"
        workspace.mkdir()
        outside.mkdir()
        (workspace / ".agent-team").symlink_to(outside, target_is_directory=True)

        with self.assertRaises(state.StateError):
            self.initialize(workspace)

        self.assertFalse((outside / "runs").exists())

    def test_staged_directory_cannot_be_replaced_with_symlink(self) -> None:
        run_dir = self.initialize(mode="staged")
        outside = self.root / "outside-deliverable"
        outside.mkdir()
        deliverable = run_dir / "deliverable"
        deliverable.rmdir()
        deliverable.symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(state.StateError, "must not be a symlink"):
            state.load_state(run_dir)

    def test_staged_delivery_rejects_nested_symlinks(self) -> None:
        run_dir = self.initialize(mode="staged")
        self.advance_to_execution(run_dir)
        outside = self.root / "external.txt"
        outside.write_text("external content\n", encoding="utf-8")
        (run_dir / "deliverable" / "link.txt").symlink_to(outside)
        self.apply_expected_marker(run_dir, "EXECUTION_DONE")

        with self.assertRaisesRegex(
            state.StateError, "staged delivery must not contain symlinks"
        ):
            self.apply_expected_marker(run_dir, "PASS")

        self.assertEqual(state.load_state(run_dir)["status"], state.VERIFYING)

    def test_forged_state_run_dir_cannot_redirect_control_writes(self) -> None:
        run_dir = self.initialize()
        victim = self.root / "victim"
        victim.mkdir()
        victim_task = victim / "task.md"
        victim_task.write_text("SENTINEL\n", encoding="utf-8")
        state_path = run_dir / "state.json"
        forged = json.loads(state_path.read_text(encoding="utf-8"))
        forged["run_dir"] = str(victim)
        state_path.write_text(json.dumps(forged), encoding="utf-8")

        with self.assertRaises(state.StateError):
            state.update_delivery(run_dir, "staged", None)

        self.assertEqual(victim_task.read_text(encoding="utf-8"), "SENTINEL\n")

    def test_missing_git_binary_is_treated_as_git_unavailable(self) -> None:
        with mock.patch.object(
            state.subprocess, "run", side_effect=FileNotFoundError("git")
        ):
            run_dir = self.initialize()

        current = state.load_state(run_dir)
        self.assertFalse(current["git_baseline"]["available"])
        self.assertFalse(current["git_baseline"]["is_git_repo"])

    def test_reviewer_cannot_approve_before_planner_finishes(self) -> None:
        run_dir = self.initialize()
        state.start_planning(run_dir)

        with self.assertRaises(state.StateError):
            state.apply_marker(run_dir, "APPROVED", role="plan_reviewer")

        self.assertEqual(state.load_state(run_dir)["expected"]["role"], "planner")

    def test_append_only_role_artifact_cannot_be_overwritten(self) -> None:
        run_dir = self.initialize()
        state.start_planning(run_dir)
        self.apply_expected_marker(run_dir, "PLAN_READY")
        current = state.load_state(run_dir)
        token = current["expected"]["artifact_token"].replace("<MARKER>", "APPROVED")
        (run_dir / "review-log.md").write_text(
            f"Decision: APPROVED\n\n{token}\n", encoding="utf-8"
        )

        with self.assertRaises(state.StateError):
            state.apply_marker(run_dir, "APPROVED", role="plan_reviewer")

    def test_role_evidence_rejects_contradictory_decisions(self) -> None:
        run_dir = self.initialize()
        state.start_planning(run_dir)
        self.apply_expected_marker(run_dir, "PLAN_READY")
        current = state.load_state(run_dir)
        token = current["expected"]["artifact_token"].replace("<MARKER>", "APPROVED")
        with (run_dir / "review-log.md").open("a", encoding="utf-8") as handle:
            handle.write(
                "\n## Planning cycle 1, review round 1\n\n"
                "### Findings\n\nThe plan is bounded, testable, reversible, and "
                "preserves unrelated work. All scope and validation details are explicit.\n\n"
                "Decision: APPROVED\n\nDecision: NEEDS_REVISION\n\n"
                f"{token}\n"
            )

        with self.assertRaisesRegex(state.StateError, "exactly one Decision"):
            state.apply_marker(run_dir, "APPROVED", role="plan_reviewer")

    def test_final_rejections_consume_the_eight_round_budget(self) -> None:
        run_dir = self.initialize()
        self.advance_to_execution(run_dir)

        for round_number in range(1, state.VERIFICATION_LIMIT + 1):
            self.apply_expected_marker(run_dir, "EXECUTION_DONE")
            self.apply_expected_marker(run_dir, "PASS")
            current = self.apply_expected_marker(
                run_dir, "FINAL_REJECT", f"round {round_number} rejected"
            )

        self.assertEqual(current["status"], state.BLOCKED)
        self.assertEqual(current["verification_round"], state.VERIFICATION_LIMIT)

    def test_concurrent_risk_updates_do_not_lose_state(self) -> None:
        run_dir = self.initialize()

        with ThreadPoolExecutor(max_workers=12) as pool:
            list(
                pool.map(
                    lambda index: state.add_risk(
                        run_dir, f"worker-{index}", f"risk-{index}"
                    ),
                    range(40),
                )
            )

        current = state.load_state(run_dir)
        risk_log = (run_dir / "risk-log.md").read_text(encoding="utf-8")
        self.assertEqual(len(current["risks"]), 40)
        self.assertEqual(risk_log.count("\n## "), 40)

    def test_windows_file_lock_retries_deadlock_errors(self) -> None:
        class FakeMsvcrt:
            LK_NBLCK = 1
            LK_UNLCK = 2
            lock_attempts = 0
            unlock_attempts = 0

            @classmethod
            def locking(cls, _fd: int, mode: int, _length: int) -> None:
                if mode == cls.LK_UNLCK:
                    cls.unlock_attempts += 1
                    return
                cls.lock_attempts += 1
                if cls.lock_attempts < 3:
                    raise OSError(errno.EDEADLK, "lock is busy")

        lock_path = self.root / "windows.lock"
        with (
            mock.patch.object(state, "fcntl", None),
            mock.patch.object(state, "msvcrt", FakeMsvcrt, create=True),
            state.exclusive_file_lock(lock_path, "test lock"),
        ):
            self.assertTrue(lock_path.is_file())

        self.assertEqual(FakeMsvcrt.lock_attempts, 3)
        self.assertEqual(FakeMsvcrt.unlock_attempts, 1)

    def test_critical_ad_hoc_test_cannot_close_a_risk(self) -> None:
        run_dir = self.initialize()
        self.advance_to_execution(run_dir)
        added = state.add_risk(run_dir, "executor", "Boundary risk.")
        risk_id = added["risks"][0]["id"]
        state.update_risk(
            run_dir,
            risk_id,
            "executor",
            "mitigated",
            "Candidate mitigation implemented.",
        )
        self.apply_expected_marker(run_dir, "EXECUTION_DONE")

        with self.assertRaisesRegex(state.StateError, "NEEDS_TEST_PERSISTENCE"):
            state.update_risk(
                run_dir,
                risk_id,
                "verifier",
                "closed",
                "One-off probe passed.",
                "Temporary browser script exited zero.",
                [
                    self.evidence_record(
                        "risk-boundary", kind="ad_hoc", risk_ids=[risk_id]
                    )
                ],
            )

        self.assertEqual(state.load_state(run_dir)["risks"][0]["status"], "mitigated")

    def test_json_request_keeps_shell_syntax_literal(self) -> None:
        workspace = self.root / "request-workspace"
        workspace.mkdir()
        request_path = state.guarded_request_path(workspace, create=True)
        marker = workspace / "must-not-exist"
        task = f"Keep $(touch {marker}) and `uname` literal."
        request_path.write_text(
            json.dumps(
                self.request_payload(
                    command="init",
                    slug="literal-input",
                    task=task,
                    acceptance_criteria=["Quotes ' and $() remain literal."],
                    delivery_mode="staged",
                )
            ),
            encoding="utf-8",
        )

        result = state.execute_request_file(workspace)
        run_dir = workspace / ".agent-team" / "runs" / result["run_id"]
        current = state.load_state(run_dir)

        self.assertEqual(current["task"], task)
        self.assertFalse(marker.exists())
        self.assertFalse(request_path.exists())

    def test_cli_guard_and_request_round_trip(self) -> None:
        workspace = self.root / "cli-workspace"
        workspace.mkdir()
        guard = subprocess.run(
            [sys.executable, str(STATE_PATH), "guard"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(guard.returncode, 0, guard.stderr)
        guarded = json.loads(guard.stdout)
        self.assertRegex(guarded["request_id"], state.REQUEST_ID_PATTERN)
        request_path = Path(guarded["request_file"])
        request_path.write_text(
            json.dumps(
                self.request_payload(
                    command="init",
                    slug="cli-round-trip",
                    task="Keep $(printf unsafe) literal.",
                    acceptance_criteria=["The request is consumed exactly once."],
                    delivery_mode="staged",
                )
            ),
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, str(STATE_PATH), "request"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["status"], state.INITIALIZED)
        self.assertFalse(request_path.exists())
        run_dir = workspace / ".agent-team" / "runs" / summary["run_id"]
        self.assertEqual(
            state.load_state(run_dir)["task"], "Keep $(printf unsafe) literal."
        )

    def test_guard_returns_a_fresh_request_id_for_every_operation(self) -> None:
        workspace = self.root / "guard-id-workspace"
        workspace.mkdir()
        command = [sys.executable, str(STATE_PATH), "guard"]

        first = subprocess.run(
            command, cwd=workspace, capture_output=True, text=True, check=True
        )
        second = subprocess.run(
            command, cwd=workspace, capture_output=True, text=True, check=True
        )
        first_id = json.loads(first.stdout)["request_id"]
        second_id = json.loads(second.stdout)["request_id"]

        self.assertRegex(first_id, state.REQUEST_ID_PATTERN)
        self.assertRegex(second_id, state.REQUEST_ID_PATTERN)
        self.assertNotEqual(first_id, second_id)

    def test_cli_happy_path_reaches_done_through_both_checkpoints(self) -> None:
        workspace = self.root / "cli-e2e-workspace"
        workspace.mkdir()
        guard = subprocess.run(
            [sys.executable, str(STATE_PATH), "guard"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(guard.returncode, 0, guard.stderr)
        request_path = Path(json.loads(guard.stdout)["request_file"])

        def request(payload: dict[str, Any]) -> dict[str, Any]:
            payload = self.request_payload(**payload)
            request_path.write_text(json.dumps(payload), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(STATE_PATH), "request"],
                cwd=workspace,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return cast(dict[str, Any], json.loads(completed.stdout))

        summary = request(
            {
                "command": "init",
                "slug": "full-cli-e2e",
                "task": "Exercise the complete CLI protocol.",
                "acceptance_criteria": ["The run reaches DONE without a bypass."],
                "delivery_mode": "staged",
            }
        )
        run_id = summary["run_id"]
        run_dir = workspace / ".agent-team" / "runs" / run_id
        request({"command": "start", "run_id": run_id})

        for marker in ("PLAN_READY", "APPROVED"):
            role = self.write_expected_artifact(run_dir, marker)
            request(
                {
                    "command": "marker",
                    "run_id": run_id,
                    "role": role,
                    "marker": marker,
                }
            )
        request(
            {
                "command": "confirm",
                "run_id": run_id,
                "checkpoint": "plan",
                "decision": "approve",
            }
        )
        for marker in ("EXECUTION_DONE", "PASS", "FINAL_ACCEPT"):
            role = self.write_expected_artifact(run_dir, marker)
            payload: dict[str, Any] = {
                "command": "marker",
                "run_id": run_id,
                "role": role,
                "marker": marker,
            }
            if role == "executor":
                payload["execution_iterations"] = []
            if role == "verifier":
                payload["test_evidence"] = []
            request(payload)
        final = request(
            {
                "command": "confirm",
                "run_id": run_id,
                "checkpoint": "final",
                "decision": "approve",
                "completion_scope": self.completion_scope(),
            }
        )

        self.assertEqual(final["status"], state.DONE)
        self.assertIsNone(final["expected"])
        self.assertEqual(final["verification_round"], 1)

    def test_failed_request_is_preserved_for_correction(self) -> None:
        workspace = self.root / "failed-request-workspace"
        workspace.mkdir()
        request_path = state.guarded_request_path(workspace, create=True)
        request_path.write_text(
            json.dumps(self.request_payload(command="unknown-operation")),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(state.StateError, "unknown request command"):
            state.execute_request_file(workspace)

        self.assertTrue(request_path.is_file())
        self.assertEqual(
            json.loads(request_path.read_text(encoding="utf-8"))["command"],
            "unknown-operation",
        )

    def test_concurrent_cli_callers_execute_request_once(self) -> None:
        workspace = self.root / "concurrent-request-workspace"
        workspace.mkdir()
        request_path = state.guarded_request_path(workspace, create=True)
        request_path.write_text(
            json.dumps(
                self.request_payload(
                    command="init",
                    slug="single-consumer",
                    task="Execute this request only once.",
                    acceptance_criteria=["Exactly one run is created."],
                    delivery_mode="staged",
                )
            ),
            encoding="utf-8",
        )
        command = [sys.executable, str(STATE_PATH), "request"]
        processes = [
            subprocess.Popen(
                command,
                cwd=workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        results = [process.communicate(timeout=10) for process in processes]

        self.assertEqual(sorted(process.returncode for process in processes), [0, 2])
        self.assertEqual(
            len(list((workspace / ".agent-team" / "runs").iterdir())), 1, results
        )

    def test_committed_request_is_idempotent_when_request_delete_fails(self) -> None:
        workspace = self.root / "request-retry-workspace"
        workspace.mkdir()
        run_dir = self.initialize(workspace)
        request_path = state.guarded_request_path(workspace, create=True)
        payload = self.request_payload(
            command="risk",
            run_id=run_dir.name,
            source="test",
            text="Record this risk once.",
        )
        request_path.write_text(json.dumps(payload), encoding="utf-8")

        with (
            mock.patch.object(Path, "unlink", side_effect=OSError("simulated")),
            self.assertRaises(OSError),
        ):
            state.execute_request_file(workspace)

        self.assertEqual(len(state.load_state(run_dir)["risks"]), 1)
        retried = state.execute_request_file(workspace)
        self.assertEqual(retried["risk_count"], 1)
        self.assertFalse(request_path.exists())

    def test_executor_iteration_request_retry_does_not_double_count(self) -> None:
        workspace = self.root / "iteration-idempotency"
        run_dir = self.initialize(workspace)
        self.advance_to_execution(run_dir)
        role = self.write_expected_artifact(run_dir, "EXECUTION_DONE")
        payload = self.request_payload(
            command="marker",
            run_id=run_dir.name,
            role=role,
            marker="EXECUTION_DONE",
            execution_iterations=[
                {
                    "trigger": "self-check",
                    "failed_check": "boundary failed",
                    "fix": "fixed boundary",
                    "validation": "standard suite passed",
                }
            ],
        )

        first = state.process_request(workspace, payload)
        retried = state.process_request(workspace, payload)

        self.assertEqual(first["executor_internal_repair_count"], 1)
        self.assertEqual(retried["executor_internal_repair_count"], 1)
        self.assertEqual(len(state.load_state(run_dir)["execution_iterations"]), 1)

    def test_request_id_is_bound_to_the_complete_payload(self) -> None:
        workspace = self.root / "payload-workspace"
        workspace.mkdir()
        run_dir = self.initialize(workspace)
        request_id = "req-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        first = {
            "request_id": request_id,
            "command": "risk",
            "run_id": run_dir.name,
            "source": "test",
            "text": "first risk",
        }
        second = {**first, "text": "different risk"}
        state.process_request(workspace, first)

        with self.assertRaisesRegex(state.StateError, "different payload"):
            state.process_request(workspace, second)

        self.assertEqual(
            [item["text"] for item in state.load_state(run_dir)["risks"]],
            ["first risk"],
        )

    def test_show_request_id_is_bound_to_its_first_payload(self) -> None:
        workspace = self.root / "show-payload-workspace"
        workspace.mkdir()
        run_dir = self.initialize(workspace)
        request_id = "req-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        show_request = {
            "request_id": request_id,
            "command": "show",
            "run_id": run_dir.name,
        }

        first = state.process_request(workspace, show_request)

        with self.assertRaisesRegex(state.StateError, "different payload"):
            state.process_request(
                workspace,
                {
                    "request_id": request_id,
                    "command": "start",
                    "run_id": run_dir.name,
                },
            )

        replayed = state.process_request(workspace, show_request)
        self.assertEqual(first["status"], state.INITIALIZED)
        self.assertEqual(replayed["status"], state.INITIALIZED)

    def test_committed_init_is_idempotent_when_request_delete_fails(self) -> None:
        workspace = self.root / "init-retry-workspace"
        workspace.mkdir()
        request_path = state.guarded_request_path(workspace, create=True)
        payload = self.request_payload(
            command="init",
            slug="retry-init",
            task="Create only one durable run.",
            acceptance_criteria=["Exactly one run exists."],
            delivery_mode="staged",
        )
        request_path.write_text(json.dumps(payload), encoding="utf-8")

        with (
            mock.patch.object(Path, "unlink", side_effect=OSError("simulated")),
            self.assertRaises(OSError),
        ):
            state.execute_request_file(workspace)

        retried = state.execute_request_file(workspace)
        runs = list((workspace / ".agent-team" / "runs").iterdir())
        self.assertEqual([path.name for path in runs], [retried["run_id"]])
        self.assertFalse(request_path.exists())

    def test_corrupt_unrelated_run_does_not_block_new_init(self) -> None:
        workspace = self.root / "corrupt-run-workspace"
        workspace.mkdir()
        corrupt_run = self.initialize(workspace)
        (corrupt_run / "state.json").write_text("{not-json", encoding="utf-8")
        request = self.request_payload(
            command="init",
            slug="new-run",
            task="Initialize independently of an unusable old run.",
            acceptance_criteria=["A new V1 run is created."],
            delivery_mode="staged",
        )

        created = state.process_request(workspace, request)

        self.assertEqual(created["status"], state.INITIALIZED)
        self.assertNotEqual(created["run_id"], corrupt_run.name)

    def test_second_active_run_in_the_same_workspace_is_rejected(self) -> None:
        workspace = self.root / "single-active-workspace"
        workspace.mkdir()
        first = self.initialize(workspace)

        with self.assertRaisesRegex(state.StateError, "active Agent Team run"):
            state.initialize_run(
                workspace=workspace,
                slug="second",
                task="This run must wait.",
                acceptance_criteria=["Only one run is active."],
                delivery_mode="staged",
                timestamp="20260811-120001",
            )

        self.assertTrue((first / "state.json").is_file())

    def test_failed_initialization_leaves_no_official_run(self) -> None:
        workspace = self.root / "atomic-init-workspace"
        workspace.mkdir()

        with (
            mock.patch.object(
                state,
                "atomic_write_json",
                side_effect=OSError("simulated init failure"),
            ),
            self.assertRaises(OSError),
        ):
            self.initialize(workspace, mode="staged")

        runs = workspace / ".agent-team" / "runs"
        self.assertEqual(list(runs.iterdir()), [])

    def test_git_baseline_excludes_agent_team_control_files(self) -> None:
        workspace = self.root / "baseline-workspace"
        workspace.mkdir()
        subprocess.run(["git", "-C", str(workspace), "init", "-q"], check=True)
        subprocess.run(
            ["git", "-C", str(workspace), "config", "user.name", "Test"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(workspace), "config", "user.email", "test@localhost"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(workspace),
                "commit",
                "--allow-empty",
                "-q",
                "-m",
                "base",
            ],
            check=True,
        )
        request_path = state.guarded_request_path(workspace, create=True)
        request_path.write_text(
            json.dumps(
                self.request_payload(
                    command="init",
                    slug="baseline",
                    task="Capture only pre-existing product changes.",
                    acceptance_criteria=["Control files are excluded."],
                    delivery_mode="staged",
                )
            ),
            encoding="utf-8",
        )

        result = state.execute_request_file(workspace)
        run_dir = workspace / ".agent-team" / "runs" / result["run_id"]

        self.assertEqual(state.load_state(run_dir)["git_baseline"]["status"], "")

    def test_failed_derived_artifact_write_does_not_commit_state(self) -> None:
        run_dir = self.initialize()
        task_path = run_dir / "task.md"
        task_path.unlink()
        task_path.mkdir()

        with self.assertRaises(OSError):
            state.start_planning(run_dir)

        persisted = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted["status"], state.INITIALIZED)

    def test_initialized_block_can_resume_safely(self) -> None:
        run_dir = self.initialize()
        state.block_run(run_dir, "waiting for user input")

        resumed = state.resume_run(run_dir, state.INITIALIZED, "input supplied")

        self.assertEqual(resumed["status"], state.INITIALIZED)
        self.assertIsNone(resumed["expected"])

    def test_done_run_is_not_reactivated_by_later_artifact_drift(self) -> None:
        workspace = self.root / "completed-workspace"
        run_dir = self.initialize(workspace, mode="staged")
        self.advance_to_execution(run_dir)
        (run_dir / "deliverable" / "result.txt").write_text(
            "verified\n", encoding="utf-8"
        )
        self.apply_expected_marker(run_dir, "EXECUTION_DONE")
        self.apply_expected_marker(run_dir, "PASS")
        self.apply_expected_marker(run_dir, "FINAL_ACCEPT")
        completed = state.confirm_checkpoint(
            run_dir, "final", "approve", completion_scope=self.completion_scope()
        )
        self.assertEqual(completed["status"], state.DONE)

        (run_dir / "plan.md").write_text(
            "# Changed after completion\n", encoding="utf-8"
        )

        loaded = state.load_state(run_dir)
        self.assertEqual(loaded["status"], state.DONE)
        second = state.initialize_run(
            workspace=workspace,
            slug="second",
            task="Start after the prior run completed.",
            acceptance_criteria=["Completed runs do not block a new run."],
            delivery_mode="staged",
            timestamp="20260811-120001",
        )
        self.assertTrue((second / "state.json").is_file())

    def test_blocked_run_stays_blocked_until_resume_reconciles_plan(self) -> None:
        run_dir = self.initialize(mode="staged")
        self.advance_to_execution(run_dir)
        state.block_run(run_dir, "pause before execution")
        (run_dir / "plan.md").write_text("# Changed while blocked\n", encoding="utf-8")

        shown = state.load_state(run_dir)
        self.assertEqual(shown["status"], state.BLOCKED)

        resumed = state.resume_run(run_dir, state.EXECUTING, "continue safely")
        self.assertEqual(resumed["status"], state.PLANNING)
        self.assertEqual(resumed["expected"]["role"], "planner")
        self.assertEqual(resumed["planning_cycle"], 2)
        self.assertIsNone(resumed["blocked"])

    def test_blocked_final_review_reconciles_evidence_on_resume(self) -> None:
        run_dir = self.initialize(mode="staged")
        self.advance_to_execution(run_dir)
        deliverable = run_dir / "deliverable" / "result.txt"
        deliverable.write_text("verified\n", encoding="utf-8")
        self.apply_expected_marker(run_dir, "EXECUTION_DONE")
        self.apply_expected_marker(run_dir, "PASS")
        state.block_run(run_dir, "pause before final review")
        deliverable.write_text("changed while blocked\n", encoding="utf-8")

        shown = state.load_state(run_dir)
        self.assertEqual(shown["status"], state.BLOCKED)

        resumed = state.resume_run(run_dir, state.FINAL_REVIEW, "continue safely")
        self.assertEqual(resumed["status"], state.EXECUTING)
        self.assertEqual(resumed["expected"]["role"], "executor")
        self.assertEqual(resumed["verification_cycle"], 2)
        self.assertIsNone(resumed["blocked"])

    def test_blocked_verifier_resume_restores_missing_report(self) -> None:
        run_dir = self.initialize(mode="staged")
        self.advance_to_execution(run_dir)
        (run_dir / "deliverable" / "result.txt").write_text(
            "candidate\n", encoding="utf-8"
        )
        self.apply_expected_marker(run_dir, "EXECUTION_DONE")
        state.block_run(run_dir, "pause during verification")
        (run_dir / "verify-report.md").unlink()

        resumed = state.resume_run(run_dir, state.VERIFYING, "continue safely")
        self.assertEqual(resumed["status"], state.VERIFYING)
        self.assertEqual(resumed["expected"]["role"], "verifier")
        self.assertTrue((run_dir / "verify-report.md").is_file())

        passed = self.apply_expected_marker(run_dir, "PASS")
        self.assertEqual(passed["status"], state.FINAL_REVIEW)

    def test_active_show_restores_missing_append_only_artifacts(self) -> None:
        review_run = self.initialize(self.root / "missing-review", mode="staged")
        state.start_planning(review_run)
        self.apply_expected_marker(review_run, "PLAN_READY")
        (review_run / "review-log.md").unlink()
        review_state = state.load_state(review_run)
        self.assertEqual(review_state["expected"]["role"], "plan_reviewer")
        self.apply_expected_marker(review_run, "APPROVED")

        verify_run = self.initialize(self.root / "missing-verify", mode="staged")
        self.advance_to_execution(verify_run)
        (verify_run / "deliverable" / "result.txt").write_text(
            "candidate\n", encoding="utf-8"
        )
        self.apply_expected_marker(verify_run, "EXECUTION_DONE")
        (verify_run / "verify-report.md").unlink()
        verify_state = state.load_state(verify_run)
        self.assertEqual(verify_state["expected"]["role"], "verifier")
        self.apply_expected_marker(verify_run, "PASS")

        final_run = self.initialize(self.root / "missing-final", mode="staged")
        self.advance_to_execution(final_run)
        (final_run / "deliverable" / "result.txt").write_text(
            "candidate\n", encoding="utf-8"
        )
        self.apply_expected_marker(final_run, "EXECUTION_DONE")
        self.apply_expected_marker(final_run, "PASS")
        (final_run / "final-review.md").unlink()
        final_state = state.load_state(final_run)
        self.assertEqual(final_state["expected"]["role"], "planner_final")
        accepted = self.apply_expected_marker(final_run, "FINAL_ACCEPT")
        self.assertEqual(accepted["status"], state.AWAITING_FINAL_APPROVAL)

    def test_missing_plan_review_feedback_blocks_before_planner_dispatch(self) -> None:
        run_dir = self.initialize(mode="staged")
        state.start_planning(run_dir)
        self.apply_expected_marker(run_dir, "PLAN_READY")
        self.apply_expected_marker(
            run_dir, "NEEDS_REVISION", "add the missing negative case"
        )
        (run_dir / "review-log.md").unlink()

        blocked = state.load_state(run_dir)
        self.assertEqual(blocked["status"], state.BLOCKED)
        self.assertEqual(blocked["blocked"]["event"], "REQUIRED_HISTORY_MISSING")
        self.assertIn("review-log.md", blocked["blocked"]["reason"])

        resumed = state.resume_run(
            run_dir, state.PLANNING, "continue with the recorded summary"
        )
        self.assertEqual(resumed["status"], state.PLANNING)
        self.assertEqual(resumed["expected"]["role"], "planner")

    def test_missing_verification_failure_blocks_before_executor_dispatch(self) -> None:
        run_dir = self.initialize(mode="staged")
        self.advance_to_execution(run_dir)
        (run_dir / "deliverable" / "result.txt").write_text(
            "candidate\n", encoding="utf-8"
        )
        self.apply_expected_marker(run_dir, "EXECUTION_DONE")
        self.apply_expected_marker(run_dir, "FAIL", "boundary case still fails")
        (run_dir / "verify-report.md").unlink()

        blocked = state.load_state(run_dir)
        self.assertEqual(blocked["status"], state.BLOCKED)
        self.assertEqual(blocked["blocked"]["event"], "REQUIRED_HISTORY_MISSING")
        self.assertIn("verify-report.md", blocked["blocked"]["reason"])

        resumed = state.resume_run(
            run_dir, state.EXECUTING, "continue with the recorded summary"
        )
        self.assertEqual(resumed["status"], state.EXECUTING)
        self.assertEqual(resumed["expected"]["role"], "executor")

    def test_missing_test_persistence_feedback_blocks_executor_dispatch(self) -> None:
        run_dir = self.initialize(mode="staged")
        self.advance_to_execution(run_dir)
        (run_dir / "deliverable" / "result.txt").write_text(
            "candidate\n", encoding="utf-8"
        )
        self.apply_expected_marker(run_dir, "EXECUTION_DONE")
        returned = self.apply_expected_marker(
            run_dir,
            "NEEDS_TEST_PERSISTENCE",
            "persist the boundary check",
            test_evidence=[self.evidence_record("boundary-guard", kind="ad_hoc")],
        )
        self.assertEqual(
            returned["pending_test_persistence"][0]["id"], "boundary-guard"
        )
        (run_dir / "verify-report.md").unlink()

        blocked = state.load_state(run_dir)

        self.assertEqual(blocked["status"], state.BLOCKED)
        self.assertEqual(blocked["blocked"]["event"], "REQUIRED_HISTORY_MISSING")
        self.assertIn("test-persistence", blocked["blocked"]["reason"])

    def test_missing_final_rejection_blocks_before_executor_dispatch(self) -> None:
        run_dir = self.initialize(mode="staged")
        self.advance_to_execution(run_dir)
        (run_dir / "deliverable" / "result.txt").write_text(
            "candidate\n", encoding="utf-8"
        )
        self.apply_expected_marker(run_dir, "EXECUTION_DONE")
        self.apply_expected_marker(run_dir, "PASS")
        self.apply_expected_marker(run_dir, "FINAL_REJECT", "criterion two is unmet")
        (run_dir / "final-review.md").unlink()

        blocked = state.load_state(run_dir)
        self.assertEqual(blocked["status"], state.BLOCKED)
        self.assertEqual(blocked["blocked"]["event"], "REQUIRED_HISTORY_MISSING")
        self.assertIn("final-review.md", blocked["blocked"]["reason"])

        resumed = state.resume_run(
            run_dir, state.EXECUTING, "continue with the recorded summary"
        )
        self.assertEqual(resumed["status"], state.EXECUTING)
        self.assertEqual(resumed["expected"]["role"], "executor")

    def test_nested_block_preserves_plan_review_recovery_gate(self) -> None:
        run_dir = self.initialize(mode="staged")
        state.start_planning(run_dir)
        self.apply_expected_marker(run_dir, "PLAN_READY")
        self.apply_expected_marker(
            run_dir, "NEEDS_REVISION", "add the missing negative case"
        )
        blocked = self.apply_expected_marker(
            run_dir, "BLOCKED", "waiting for product input"
        )
        self.assertEqual(blocked["last_marker"], "NEEDS_REVISION")
        (run_dir / "review-log.md").unlink()

        missing_history = state.resume_run(
            run_dir, None, "the product input is now available"
        )
        self.assertEqual(missing_history["status"], state.BLOCKED)
        self.assertEqual(
            missing_history["blocked"]["event"], "REQUIRED_HISTORY_MISSING"
        )

        resumed = state.resume_run(
            run_dir, None, "continue with the recorded review summary"
        )
        self.assertEqual(resumed["status"], state.PLANNING)
        self.assertEqual(resumed["expected"]["role"], "planner")

    def test_nested_block_preserves_verification_failure_recovery_gate(self) -> None:
        run_dir = self.initialize(mode="staged")
        self.advance_to_execution(run_dir)
        (run_dir / "deliverable" / "result.txt").write_text(
            "candidate\n", encoding="utf-8"
        )
        self.apply_expected_marker(run_dir, "EXECUTION_DONE")
        self.apply_expected_marker(run_dir, "FAIL", "boundary case still fails")
        blocked = self.apply_expected_marker(
            run_dir, "BLOCKED", "waiting for a dependency"
        )
        self.assertEqual(blocked["last_marker"], "FAIL")
        (run_dir / "verify-report.md").unlink()

        missing_history = state.resume_run(
            run_dir, None, "the dependency is now available"
        )
        self.assertEqual(missing_history["status"], state.BLOCKED)
        self.assertEqual(
            missing_history["blocked"]["event"], "REQUIRED_HISTORY_MISSING"
        )

        resumed = state.resume_run(
            run_dir, None, "continue with the recorded verification summary"
        )
        self.assertEqual(resumed["status"], state.EXECUTING)
        self.assertEqual(resumed["expected"]["role"], "executor")

    def test_nested_block_preserves_final_rejection_recovery_gate(self) -> None:
        run_dir = self.initialize(mode="staged")
        self.advance_to_execution(run_dir)
        (run_dir / "deliverable" / "result.txt").write_text(
            "candidate\n", encoding="utf-8"
        )
        self.apply_expected_marker(run_dir, "EXECUTION_DONE")
        self.apply_expected_marker(run_dir, "PASS", "verified")
        self.apply_expected_marker(run_dir, "FINAL_REJECT", "criterion two is unmet")
        blocked = self.apply_expected_marker(
            run_dir, "BLOCKED", "waiting for credentials"
        )
        self.assertEqual(blocked["last_marker"], "FINAL_REJECT")
        (run_dir / "final-review.md").unlink()

        missing_history = state.resume_run(
            run_dir, None, "the credentials are now available"
        )
        self.assertEqual(missing_history["status"], state.BLOCKED)
        self.assertEqual(
            missing_history["blocked"]["event"], "REQUIRED_HISTORY_MISSING"
        )

        resumed = state.resume_run(
            run_dir, None, "continue with the recorded final-review summary"
        )
        self.assertEqual(resumed["status"], state.EXECUTING)
        self.assertEqual(resumed["expected"]["role"], "executor")

    def test_blocked_plan_review_recovers_multiple_missing_artifacts(self) -> None:
        run_dir = self.initialize(mode="staged")
        state.start_planning(run_dir)
        self.apply_expected_marker(run_dir, "PLAN_READY")
        state.block_run(run_dir, "pause during plan review")
        (run_dir / "plan.md").unlink()
        (run_dir / "review-log.md").unlink()

        resumed = state.resume_run(run_dir, state.PLANNING, "continue safely")
        self.assertEqual(resumed["status"], state.PLANNING)
        self.assertEqual(resumed["expected"]["role"], "planner")
        self.assertEqual(resumed["planning_cycle"], 2)

    def test_blocked_final_review_recovers_multiple_missing_artifacts(self) -> None:
        run_dir = self.initialize(mode="staged")
        self.advance_to_execution(run_dir)
        (run_dir / "deliverable" / "result.txt").write_text(
            "candidate\n", encoding="utf-8"
        )
        self.apply_expected_marker(run_dir, "EXECUTION_DONE")
        self.apply_expected_marker(run_dir, "PASS")
        state.block_run(run_dir, "pause during final review")
        for artifact in ("deliverables.md", "verify-report.md", "final-review.md"):
            (run_dir / artifact).unlink()

        resumed = state.resume_run(run_dir, state.FINAL_REVIEW, "continue safely")
        self.assertEqual(resumed["status"], state.EXECUTING)
        self.assertEqual(resumed["expected"]["role"], "executor")
        self.assertEqual(resumed["verification_cycle"], 2)

    def test_planning_limit_resume_restores_missing_artifacts(self) -> None:
        run_dir = self.initialize(mode="staged")
        state.start_planning(run_dir)
        for round_number in range(1, state.PLANNING_LIMIT + 1):
            self.apply_expected_marker(run_dir, "PLAN_READY")
            blocked = self.apply_expected_marker(
                run_dir, "NEEDS_REVISION", f"round {round_number}"
            )
        self.assertEqual(blocked["status"], state.BLOCKED)
        (run_dir / "plan.md").unlink()
        (run_dir / "review-log.md").unlink()

        resumed = state.resume_run(run_dir, state.PLANNING, "authorize a new cycle")
        self.assertEqual(resumed["status"], state.PLANNING)
        self.assertEqual(resumed["planning_cycle"], 2)
        self.assertEqual(resumed["expected"]["role"], "planner")
        self.assertTrue((run_dir / "plan.md").is_file())
        self.assertTrue((run_dir / "review-log.md").is_file())

    def test_verification_limit_resume_restores_missing_artifacts(self) -> None:
        run_dir = self.initialize(mode="staged")
        self.advance_to_execution(run_dir)
        for round_number in range(1, state.VERIFICATION_LIMIT + 1):
            self.apply_expected_marker(run_dir, "EXECUTION_DONE")
            blocked = self.apply_expected_marker(
                run_dir, "FAIL", f"round {round_number}"
            )
        self.assertEqual(blocked["status"], state.BLOCKED)
        for artifact in ("deliverables.md", "verify-report.md", "final-review.md"):
            (run_dir / artifact).unlink()

        resumed = state.resume_run(run_dir, None, "authorize a new verification cycle")
        self.assertEqual(resumed["status"], state.EXECUTING)
        self.assertEqual(resumed["verification_cycle"], 2)
        self.assertEqual(resumed["expected"]["role"], "executor")
        for artifact in ("deliverables.md", "verify-report.md", "final-review.md"):
            self.assertTrue((run_dir / artifact).is_file())

    def test_pending_test_persistence_survives_limit_resume(self) -> None:
        run_dir = self.initialize(mode="staged")
        self.advance_to_execution(run_dir)
        self.apply_expected_marker(run_dir, "EXECUTION_DONE")
        current = self.apply_expected_marker(
            run_dir,
            "NEEDS_TEST_PERSISTENCE",
            "persist the acceptance-critical probe",
            test_evidence=[self.evidence_record("durable-boundary", kind="ad_hoc")],
        )
        for round_number in range(2, state.VERIFICATION_LIMIT + 1):
            self.apply_expected_marker(run_dir, "EXECUTION_DONE")
            current = self.apply_expected_marker(
                run_dir, "FAIL", f"round {round_number} still fails"
            )
        self.assertEqual(current["status"], state.BLOCKED)

        resumed = state.resume_run(run_dir, None, "authorize another repair cycle")

        self.assertEqual(resumed["status"], state.EXECUTING)
        self.assertEqual(
            resumed["pending_test_persistence"][0]["id"], "durable-boundary"
        )

    def test_missing_required_state_field_is_rejected_cleanly(self) -> None:
        run_dir = self.initialize()
        state_path = run_dir / "state.json"
        corrupted = json.loads(state_path.read_text(encoding="utf-8"))
        del corrupted["expected"]
        state_path.write_text(json.dumps(corrupted), encoding="utf-8")

        with self.assertRaisesRegex(
            state.StateError, "missing required field: expected"
        ):
            state.load_state(run_dir)

    def test_forged_round_counter_above_limit_is_rejected(self) -> None:
        run_dir = self.initialize()
        state_path = run_dir / "state.json"
        forged = json.loads(state_path.read_text(encoding="utf-8"))
        forged["verification_round"] = state.VERIFICATION_LIMIT + 1
        state_path.write_text(json.dumps(forged), encoding="utf-8")

        with self.assertRaisesRegex(state.StateError, "exceeds its hard limit"):
            state.load_state(run_dir)

    def test_forged_history_tail_is_rejected(self) -> None:
        run_dir = self.initialize()
        state_path = run_dir / "state.json"
        forged = json.loads(state_path.read_text(encoding="utf-8"))
        forged["history"][-1]["to"] = state.DONE
        state_path.write_text(json.dumps(forged), encoding="utf-8")

        with self.assertRaisesRegex(state.StateError, "history does not end"):
            state.load_state(run_dir)


if __name__ == "__main__":
    unittest.main()
