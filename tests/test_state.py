from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

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
SPEC = importlib.util.spec_from_file_location("agent_team_state", STATE_PATH)
assert SPEC and SPEC.loader
state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(state)


class AgentTeamStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def initialize(
        self, mode: str = "workspace", output_path: str | None = None
    ) -> Path:
        return cast(
            Path,
            state.initialize_run(
                workspace=self.workspace,
                slug="sample-task",
                task="Implement a sample change.",
                acceptance_criteria=[
                    "The requested behavior is present.",
                    "Tests pass.",
                ],
                delivery_mode=mode,
                output_path=output_path,
                timestamp="20260810-120000",
            ),
        )

    def advance_to_execution(self, run_dir: Path) -> None:
        state.start_planning(run_dir)
        self.apply_marker(run_dir, "PLAN_READY")
        self.apply_marker(run_dir, "APPROVED")
        state.confirm_checkpoint(run_dir, "plan", "approve")

    def apply_marker(
        self,
        run_dir: Path,
        marker: str,
        detail: str = "",
        *,
        test_evidence: list[dict[str, Any]] | None = None,
        execution_iterations: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        current = state.load_state(run_dir)
        expected = current["expected"]
        role = expected["role"]
        if marker != "BLOCKED":
            artifact = run_dir / expected["artifact"]
            token = expected["artifact_token"].replace("<MARKER>", marker)
            evidence = f"\n## Test evidence\n\nDecision: {marker}\n\n{token}\n"
            if role == "planner":
                artifact.write_text(
                    "# Plan\n\n## Goal and scope\n\nImplement only the approved behavior "
                    "and preserve unrelated work.\n\n## Write scope\n\nModify only the "
                    "approved target files.\n\n## Implementation steps\n\n1. Inspect the "
                    "baseline.\n2. Make the smallest change.\n3. Run negative and "
                    "positive checks.\n\n## Risks and recovery\n\nRestore the approved "
                    "files if validation fails; no destructive operations are allowed.\n\n"
                    "## Acceptance criteria\n\n1. The requested "
                    "behavior is present.\n2. Tests pass.\n3. Unrelated files remain "
                    "unchanged.\n" + evidence,
                    encoding="utf-8",
                )
            elif role == "plan_reviewer":
                with artifact.open("a", encoding="utf-8") as handle:
                    handle.write(
                        "\n## Planning cycle 1, review round 1\n\n### Findings\n\n"
                        "Correctness, scope, testability, and rollback are explicit. "
                        "No material gap remains; the plan is executable without guessing.\n"
                        + evidence
                    )
            elif role == "executor":
                artifact.write_text(
                    "# Deliverables\n\n## Files changed\n\nOnly approved target files; "
                    "unrelated baseline changes were preserved.\n\n"
                    "## Execution iterations\n\nNo internal repair iteration was "
                    "needed in this dispatch.\n\n## Validation\n\nRan "
                    "the planned positive, negative, and boundary checks successfully and "
                    "inspected the resulting diff.\n\n## Residual risks\n\nNone known. No "
                    "commit or push occurred.\n" + evidence,
                    encoding="utf-8",
                )
            elif role == "verifier":
                with artifact.open("a", encoding="utf-8") as handle:
                    handle.write(
                        "\n## Verification cycle 1, round 1\n\n"
                        "| # | Criterion | Method | Expected | Actual evidence | Result |\n"
                        "|---|---|---|---|---|---|\n"
                        "| 1 | Requested behavior | Independent test | Pass | Command "
                        "returned zero and diff stayed in scope | PASS |\n\n"
                        "### Test persistence\n\nAll acceptance-critical tests are "
                        "formal regression tests reached by the standard test command.\n\n"
                        "### Defects and unmet criteria\n\nNone. Negative and boundary "
                        "checks also passed.\n" + evidence
                    )
            elif role == "planner_final":
                with artifact.open("a", encoding="utf-8") as handle:
                    handle.write(
                        "\n## Final review\n\n"
                        "| # | Criterion | Delivery evidence | Result |\n"
                        "|---|---|---|---|\n"
                        "| 1 | Requested behavior | Verified output and report agree | PASS |\n"
                        "| 2 | Scope preserved | Diff contains only approved files | PASS |\n\n"
                        "## Residual risks\n\nNone known after independent verification.\n"
                        + evidence
                    )
            else:  # pragma: no cover - test helper guard
                self.fail(f"unexpected artifact-owning role: {role}")
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
            "summary": "Approved sample delivery and regression evidence.",
            "completed": ["Requested behavior", "Regression validation"],
            "excluded": ["Production deployment"],
            "deployment_status": "not_deployed",
        }

    def prepare_and_confirm_final(self, run_dir: Path) -> dict[str, Any]:
        state.prepare_final_scope(run_dir, self.completion_scope())
        return cast(
            dict[str, Any], state.confirm_checkpoint(run_dir, "final", "approve")
        )

    def evidence_record(
        self,
        evidence_id: str,
        *,
        kind: str,
        critical: bool = True,
        risk_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        persisted = kind == "formal"
        return {
            "id": evidence_id,
            "description": "Boundary regression that closes an acceptance risk.",
            "kind": kind,
            "critical": critical,
            "persisted": persisted,
            "target": "tests/test_regression.py::test_boundary" if persisted else "",
            "risk_ids": risk_ids or [],
        }

    def test_initialize_creates_durable_artifacts(self) -> None:
        run_dir = self.initialize()
        current = state.load_state(run_dir)

        self.assertEqual(current["schema_version"], 6)
        self.assertEqual(current["status"], state.INITIALIZED)
        self.assertEqual(current["delivery"]["mode"], "workspace")
        self.assertEqual(
            current["delivery"]["output_path"], str(self.workspace.absolute())
        )
        for filename in (
            "state.json",
            "task.md",
            "plan.md",
            "review-log.md",
            "verify-report.md",
            "final-review.md",
            "deliverables.md",
            "risk-log.md",
        ):
            self.assertTrue((run_dir / filename).is_file(), filename)
        self.assertFalse((run_dir / "deliverable").exists())

    def test_staged_mode_creates_deliverable_directory(self) -> None:
        run_dir = self.initialize(mode="staged")
        current = state.load_state(run_dir)

        self.assertEqual(
            current["delivery"]["output_path"], str(run_dir / "deliverable")
        )
        self.assertTrue((run_dir / "deliverable").is_dir())

    def test_v1_rejects_user_path_and_worktree_modes(self) -> None:
        for mode in ("user_path", "worktree"):
            with (
                self.subTest(mode=mode),
                self.assertRaisesRegex(state.StateError, "unsupported delivery mode"),
            ):
                self.initialize(mode=mode, output_path="outputs/report.md")

    def test_happy_path_with_one_repair_round(self) -> None:
        run_dir = self.initialize()
        self.advance_to_execution(run_dir)

        self.apply_marker(run_dir, "EXECUTION_DONE")
        self.apply_marker(run_dir, "FAIL", "one defect")
        self.apply_marker(run_dir, "EXECUTION_DONE")
        self.apply_marker(run_dir, "PASS")
        self.apply_marker(run_dir, "FINAL_ACCEPT")
        final = self.prepare_and_confirm_final(run_dir)

        self.assertEqual(final["status"], state.DONE)
        self.assertEqual(final["planning_round"], 1)
        self.assertEqual(final["verification_round"], 2)
        self.assertIsNone(final["pending_checkpoint"])

    def test_critical_ad_hoc_test_must_be_persisted_before_pass(self) -> None:
        rejected_run = self.initialize()
        self.advance_to_execution(rejected_run)
        self.apply_marker(rejected_run, "EXECUTION_DONE")

        with self.assertRaisesRegex(
            state.StateError, "PASS cannot rely on critical ad hoc tests"
        ):
            self.apply_marker(
                rejected_run,
                "PASS",
                test_evidence=[self.evidence_record("boundary-1", kind="ad_hoc")],
            )
        self.assertEqual(state.load_state(rejected_run)["status"], state.VERIFYING)

        self.workspace = Path(self.temporary.name) / "persistence-workspace"
        self.workspace.mkdir()
        run_dir = self.initialize()
        self.advance_to_execution(run_dir)
        self.apply_marker(run_dir, "EXECUTION_DONE")
        needs_persistence = self.apply_marker(
            run_dir,
            "NEEDS_TEST_PERSISTENCE",
            "Persist boundary regression.",
            test_evidence=[self.evidence_record("boundary-1", kind="ad_hoc")],
        )

        self.assertEqual(needs_persistence["status"], state.EXECUTING)
        self.assertEqual(
            needs_persistence["pending_test_persistence"][0]["id"], "boundary-1"
        )
        self.apply_marker(run_dir, "EXECUTION_DONE")
        passed = self.apply_marker(
            run_dir,
            "PASS",
            test_evidence=[self.evidence_record("boundary-1", kind="formal")],
        )
        self.assertEqual(passed["status"], state.FINAL_REVIEW)
        self.assertEqual(passed["pending_test_persistence"], [])
        self.assertEqual(passed["executor_dispatch_count"], 2)
        self.assertEqual(passed["executor_repair_dispatch_count"], 1)

    def test_executor_internal_repairs_are_counted_and_audited(self) -> None:
        run_dir = self.initialize()
        self.advance_to_execution(run_dir)
        iterations = [
            {
                "trigger": "self-check",
                "failed_check": "boundary test failed",
                "fix": "corrected the boundary condition",
                "validation": "formal regression and standard suite passed",
            },
            {
                "trigger": "self-check",
                "failed_check": "lint reported an unused import",
                "fix": "removed the import introduced by this change",
                "validation": "lint and unit tests passed",
            },
        ]

        current = self.apply_marker(
            run_dir, "EXECUTION_DONE", execution_iterations=iterations
        )

        self.assertEqual(current["executor_dispatch_count"], 1)
        self.assertEqual(current["executor_repair_dispatch_count"], 0)
        self.assertEqual(current["executor_internal_repair_count"], 2)
        persisted = state.load_state(run_dir)["execution_iterations"]
        self.assertEqual(persisted[0]["items"], iterations)

    def test_risk_lifecycle_preserves_history_and_controls_closure(self) -> None:
        run_dir = self.initialize()
        self.advance_to_execution(run_dir)
        added = state.add_risk(run_dir, "executor", "Boundary behavior was uncertain.")
        risk_id = added["risks"][0]["id"]
        mitigated = state.update_risk(
            run_dir,
            risk_id,
            "executor",
            "mitigated",
            "Implemented a boundary fix and regression candidate.",
        )
        self.assertEqual(mitigated["risks"][0]["status"], "mitigated")
        self.apply_marker(run_dir, "EXECUTION_DONE")

        with self.assertRaisesRegex(state.StateError, "only verifier"):
            state.update_risk(
                run_dir,
                risk_id,
                "executor",
                "closed",
                "Claimed closure.",
                "A test passed.",
                [],
            )
        closed = state.update_risk(
            run_dir,
            risk_id,
            "verifier",
            "closed",
            "Independent formal regression passed.",
            "python3 -m unittest tests.test_regression",
            [self.evidence_record("boundary-risk", kind="formal", risk_ids=[risk_id])],
        )
        self.assertEqual(closed["risks"][0]["status"], "closed")
        reopened = state.update_risk(
            run_dir,
            risk_id,
            "verifier",
            "open",
            "New contradictory evidence requires re-evaluation.",
        )
        risk = state.load_state(run_dir)["risks"][0]
        self.assertEqual(reopened["risks"][0]["status"], "open")
        self.assertEqual(risk["status"], "open")
        self.assertEqual(len(risk["events"]), 4)

    def test_done_requires_and_persists_explicit_completion_scope(self) -> None:
        run_dir = self.initialize()
        self.advance_to_execution(run_dir)
        self.apply_marker(run_dir, "EXECUTION_DONE")
        self.apply_marker(run_dir, "PASS")
        self.apply_marker(run_dir, "FINAL_ACCEPT")

        with self.assertRaisesRegex(state.StateError, "prepared completion scope"):
            state.confirm_checkpoint(run_dir, "final", "approve")
        self.assertEqual(
            state.load_state(run_dir)["status"], state.AWAITING_FINAL_APPROVAL
        )
        prepared = state.prepare_final_scope(run_dir, self.completion_scope())
        self.assertEqual(
            prepared["prepared_completion_scope"]["scope"], self.completion_scope()
        )
        completed = state.confirm_checkpoint(run_dir, "final", "approve")

        self.assertEqual(completed["status"], state.DONE)
        self.assertIsNone(completed["prepared_completion_scope"])
        self.assertEqual(completed["completion_scope"]["delivery_mode"], "workspace")
        self.assertEqual(
            completed["completion_scope"]["excluded"], ["Production deployment"]
        )

    def test_final_scope_is_preflighted_before_human_approval(self) -> None:
        run_dir = self.initialize(mode="staged")
        self.advance_to_execution(run_dir)
        self.apply_marker(run_dir, "EXECUTION_DONE")
        self.apply_marker(run_dir, "PASS")
        self.apply_marker(run_dir, "FINAL_ACCEPT")
        invalid = self.completion_scope()
        invalid["exclusions"] = invalid.pop("excluded")

        with self.assertRaisesRegex(state.StateError, "complete completion_scope"):
            state.prepare_final_scope(run_dir, invalid)
        unchanged = state.load_state(run_dir)
        self.assertEqual(unchanged["status"], state.AWAITING_FINAL_APPROVAL)
        self.assertIsNone(unchanged["prepared_completion_scope"])

        valid = self.completion_scope()
        valid["summary"] = f"  {valid['summary']}  "
        prepared = state.prepare_final_scope(run_dir, valid)
        summary = state.state_summary(prepared)
        preview = summary["prepared_completion_scope"]
        self.assertEqual(preview["scope"], self.completion_scope())
        self.assertEqual(
            summary["canonical_output_path"],
            str((run_dir / "deliverable").resolve()),
        )
        self.assertIsNotNone(summary["evidence_hashes"]["approved_plan_sha256"])
        self.assertIsNotNone(
            summary["evidence_hashes"]["verified_delivery_and_reports_sha256"]
        )
        self.assertEqual(
            summary["evidence_hashes"]["final_review_and_risk_log_sha256"],
            preview["final_subject_sha256"],
        )

    def test_final_scope_can_be_reprepared_before_approval(self) -> None:
        run_dir = self.initialize()
        self.advance_to_execution(run_dir)
        self.apply_marker(run_dir, "EXECUTION_DONE")
        self.apply_marker(run_dir, "PASS")
        self.apply_marker(run_dir, "FINAL_ACCEPT")
        first = state.prepare_final_scope(run_dir, self.completion_scope())
        first_digest = first["prepared_completion_scope"]["scope_sha256"]
        revised_scope = self.completion_scope()
        revised_scope["summary"] = "User-visible completion wording was revised."

        revised = state.prepare_final_scope(run_dir, revised_scope)
        self.assertNotEqual(
            revised["prepared_completion_scope"]["scope_sha256"], first_digest
        )
        completed = state.confirm_checkpoint(run_dir, "final", "approve")

        self.assertEqual(completed["status"], state.DONE)
        self.assertEqual(
            completed["completion_scope"]["summary"], revised_scope["summary"]
        )
        self.assertEqual(
            [
                item["event"]
                for item in completed["history"]
                if item["event"] == "PREPARE_FINAL_SCOPE"
            ],
            ["PREPARE_FINAL_SCOPE", "PREPARE_FINAL_SCOPE"],
        )

    def test_planning_limit_blocks_fifth_unresolved_review(self) -> None:
        run_dir = self.initialize()
        state.start_planning(run_dir)

        for round_number in range(1, state.PLANNING_LIMIT + 1):
            self.apply_marker(run_dir, "PLAN_READY")
            current = self.apply_marker(
                run_dir, "NEEDS_REVISION", f"round {round_number}"
            )

        self.assertEqual(current["status"], state.BLOCKED)
        self.assertEqual(current["planning_round"], state.PLANNING_LIMIT)
        self.assertIn("planning remained NEEDS_REVISION", current["blocked"]["reason"])

    def test_verification_limit_blocks_eighth_failure(self) -> None:
        run_dir = self.initialize()
        self.advance_to_execution(run_dir)

        for round_number in range(1, state.VERIFICATION_LIMIT + 1):
            self.apply_marker(run_dir, "EXECUTION_DONE")
            current = self.apply_marker(run_dir, "FAIL", f"round {round_number}")

        self.assertEqual(current["status"], state.BLOCKED)
        self.assertEqual(current["verification_round"], state.VERIFICATION_LIMIT)
        self.assertIn("verification did not pass", current["blocked"]["reason"])

    def test_checkpoint_cannot_be_skipped(self) -> None:
        run_dir = self.initialize()
        state.start_planning(run_dir)
        self.apply_marker(run_dir, "PLAN_READY")
        self.apply_marker(run_dir, "APPROVED")

        with self.assertRaises(state.StateError):
            state.apply_marker(run_dir, "EXECUTION_DONE", role="executor")

        current = state.load_state(run_dir)
        self.assertEqual(current["status"], state.AWAITING_PLAN_APPROVAL)
        self.assertEqual(current["pending_checkpoint"], "plan")

    def test_human_revision_starts_new_cycle(self) -> None:
        run_dir = self.initialize()
        state.start_planning(run_dir)
        self.apply_marker(run_dir, "PLAN_READY")
        self.apply_marker(run_dir, "APPROVED")
        current = state.confirm_checkpoint(run_dir, "plan", "revise", "change scope")

        self.assertEqual(current["status"], state.PLANNING)
        self.assertEqual(current["planning_cycle"], 2)
        self.assertEqual(current["planning_round"], 0)

    def test_delivery_can_change_only_before_execution(self) -> None:
        run_dir = self.initialize()
        changed = state.update_delivery(run_dir, "staged", None)
        self.assertEqual(changed["delivery"]["mode"], "staged")
        task_text = (run_dir / "task.md").read_text(encoding="utf-8")
        self.assertIn("Mode: `staged`", task_text)
        self.assertIn(str(run_dir / "deliverable"), task_text)
        self.advance_to_execution(run_dir)

        with self.assertRaises(state.StateError):
            state.update_delivery(run_dir, "workspace", None)

    def test_risk_is_persisted_in_state_and_log(self) -> None:
        run_dir = self.initialize()
        current = state.add_risk(run_dir, "verifier", "Browser validation unavailable.")

        self.assertEqual(len(current["risks"]), 1)
        self.assertIn(
            "Browser validation unavailable", (run_dir / "risk-log.md").read_text()
        )

    def test_prepared_scope_survives_explicit_block_and_resume(self) -> None:
        run_dir = self.initialize()
        self.advance_to_execution(run_dir)
        self.apply_marker(run_dir, "EXECUTION_DONE")
        self.apply_marker(run_dir, "PASS")
        self.apply_marker(run_dir, "FINAL_ACCEPT")
        prepared = state.prepare_final_scope(run_dir, self.completion_scope())
        expected_hash = prepared["prepared_completion_scope"]["final_subject_sha256"]

        blocked = state.block_run(run_dir, "waiting for explicit user approval")
        blocked_summary = state.state_summary(blocked)
        self.assertEqual(
            blocked_summary["evidence_hashes"]["final_review_and_risk_log_sha256"],
            expected_hash,
        )
        resumed = state.resume_run(
            run_dir, state.AWAITING_FINAL_APPROVAL, "user returned to checkpoint B"
        )
        self.assertIsNotNone(resumed["prepared_completion_scope"])
        completed = state.confirm_checkpoint(run_dir, "final", "approve")
        self.assertEqual(completed["status"], state.DONE)

    def test_blocked_run_requires_explicit_resume_reason(self) -> None:
        run_dir = self.initialize()
        state.start_planning(run_dir)
        state.block_run(run_dir, "missing dependency")

        with self.assertRaises(state.StateError):
            state.resume_run(run_dir, state.PLANNING, "")

        resumed = state.resume_run(
            run_dir, state.PLANNING, "dependency supplied by user"
        )
        self.assertEqual(resumed["status"], state.PLANNING)
        self.assertIsNone(resumed["blocked"])

    def test_invalid_marker_does_not_change_persisted_state(self) -> None:
        run_dir = self.initialize()

        with self.assertRaises(state.StateError):
            state.apply_marker(run_dir, "PASS")

        current = state.load_state(run_dir)
        self.assertEqual(current["status"], state.INITIALIZED)
        self.assertIsNone(current["last_marker"])


if __name__ == "__main__":
    unittest.main()
