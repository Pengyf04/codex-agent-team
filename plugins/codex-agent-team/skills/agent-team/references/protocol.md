# Agent Team orchestration protocol

## State and dispatch table

| Current state | Dispatch or action | Allowed conclusion | Next state |
|---|---|---|---|
| `INITIALIZED` | Start planning | — | `PLANNING` |
| `PLANNING` | Planner mode A | `PLAN_READY` | `PLANNING` |
| `PLANNING` | Plan Reviewer | `APPROVED` | `AWAITING_PLAN_APPROVAL` |
| `PLANNING` | Plan Reviewer | `NEEDS_REVISION` | `PLANNING`, or `BLOCKED` at round 5 |
| `AWAITING_PLAN_APPROVAL` | Human approves | — | `EXECUTING` |
| `AWAITING_PLAN_APPROVAL` | Human requests revision | — | new `PLANNING` cycle |
| `EXECUTING` | Executor mode A or B | `EXECUTION_DONE` | `VERIFYING` |
| Any active role | Role cannot continue | `BLOCKED` | `BLOCKED` |
| `VERIFYING` | Verifier | `PASS` | `FINAL_REVIEW` |
| `VERIFYING` | Verifier | `FAIL` | `EXECUTING`, or `BLOCKED` at total round 8 |
| `VERIFYING` | Verifier | `NEEDS_TEST_PERSISTENCE` | `EXECUTING`, or `BLOCKED` at total round 8 |
| `FINAL_REVIEW` | Planner mode B | `FINAL_ACCEPT` | `AWAITING_FINAL_APPROVAL` |
| `FINAL_REVIEW` | Planner mode B | `FINAL_REJECT` | `EXECUTING`, or `BLOCKED` when round 8 was consumed |
| `AWAITING_FINAL_APPROVAL` | Human approves | — | `DONE` |
| `AWAITING_FINAL_APPROVAL` | Human requests revision | — | new `EXECUTING` cycle |

Never bypass `scripts/state.py`. Follow [state-requests.md](state-requests.md): free-form values live only in `.agent-team/request.json`, created with a file-edit tool, and never in a shell command. Every request uses the fresh `request_id` returned by `guard`. After a non-zero exit, retry the retained request unchanged once so an already committed operation can return idempotently; if it still fails, issue a safe `show` request and reconcile instead of editing `state.json`.

## Run artifacts

Treat the run directory as the durable source of truth across subagents, context compaction, checkpoints, and resumed tasks:

- `state.json`: machine-controlled state; only `state.py` writes it.
- `task.md`: original task, acceptance criteria, delivery choice, and Git baseline.
- `plan.md`: current executable plan and final acceptance criteria; Planner owns it.
- `review-log.md`: append-only plan review history; Plan Reviewer owns it.
- `verify-report.md`: append-only verification history; Verifier owns it.
- `final-review.md`: append-only Planner final-acceptance history.
- `deliverables.md`: paths, change summary, structured internal repair iterations, self-checks, and residual issues; Executor owns it.
- `risk-log.md`: risk lifecycle and immutable event history derived from `state.py risk` and `risk_update`.
- `deliverable/`: final output only in `staged` mode.
- `.state.lock`: machine-owned run lock; no role edits or removes it.

`state.json` is the machine source of truth. `task.md` and `risk-log.md` are deterministically regenerated before the corresponding atomic state commit and repaired from state when loaded. Every successful operation, including `show`, records its `request_id` and complete-payload digest in `state.json`, making the exact committed request safe to retry. Reusing an ID with different JSON is rejected. Do not use the main conversation as the only handoff channel.

## Evidence-gated transitions

Every state summary names exactly one `expected.role`, its allowed markers, its owned artifact, and an exact artifact-token template. Dispatch only that role.

Before returning a non-`BLOCKED` marker, the role must update its artifact with substantive evidence, use the exact required section headings from its role definition, add exactly one `Decision: <MARKER>` immediately before the exact token, and make that token the final non-empty line. Replace `<MARKER>` in the returned template with the real marker. Plan review, verification, and final review artifacts are append-only.

The state script rejects stale, undersized, or structurally incomplete artifacts, overwritten append-only history, a mismatched role, out-of-order markers, ambiguous decisions, and incorrect tokens. Every Verifier conclusion also carries structured `test_evidence`. A critical ad hoc test cannot support `PASS` or risk closure: Verifier must return `NEEDS_TEST_PERSISTENCE` (or `FAIL` when the product also fails), Executor must persist it under the approved write scope, and a later Verifier round must identify the same evidence ID as a formal test with a durable target. The state retains unresolved IDs until that proof arrives.

Executor `EXECUTION_DONE` carries structured `execution_iterations`. State separately counts all Executor dispatches, repair dispatches caused by `FAIL`, `NEEDS_TEST_PERSISTENCE`, or `FINAL_REJECT`, and internal self-check/fix/revalidation iterations. It preserves the approved plan through execution and freezes the delivery, `deliverables.md`, and `verify-report.md` at `PASS`. Every `show/load_state` for an active run reconciles recorded subjects before returning the next role; a mismatch starts a new planning or verification cycle instead of dispatching against stale material. A `BLOCKED` run remains frozen until explicit `resume`, which restores its recorded phase and then reconciles before dispatch. `DONE` is terminal and is never reactivated by later artifact drift. Recovery recreates only empty templates for accidentally deleted run-owned plan or evidence files and never overwrites existing content. Deleting a recorded subject starts a new cycle; deleting only the current role's unfrozen artifact refreshes that role's expectation. If an empty replacement lost prior `NEEDS_REVISION`, `FAIL`, `NEEDS_TEST_PERSISTENCE`, or `FINAL_REJECT` feedback required by the next role, the run blocks until the user explicitly authorizes recovery. Structural checks establish fresh evidence and continuity, while independent roles remain responsible for judging whether claims are true.

## Required role prompt envelope

Every role prompt must provide:

1. Role name and mode.
2. Absolute target workspace path.
3. Absolute run directory.
4. Delivery mode and resolved output path from `state.json`.
5. Files the role must read before acting.
6. Exact files or paths the role may write.
7. Relevant user feedback or prior role findings.
8. Required final marker and the rule that it must be the last non-empty line.
9. A request to identify risks, workarounds, residual issues, and environmental dependencies.
10. The exact `expected.artifact` and `expected.artifact_token`, including the required `Decision: <MARKER>` line.

Do not tell a read-only role to “help fix” an issue. Do not send a role only a summarized plan when the durable file exists.

## Marker handling

Accept only the following marker token at the beginning of the last non-empty line:

- `PLAN_READY`
- `APPROVED`
- `NEEDS_REVISION`
- `EXECUTION_DONE`
- `BLOCKED`
- `PASS`
- `FAIL`
- `NEEDS_TEST_PERSISTENCE`
- `FINAL_ACCEPT`
- `FINAL_REJECT`

Text after a colon is the marker detail and belongs in the JSON marker request. Do not guess a marker from prose. Ask the same role once to correct an invalid response; block after the second invalid response.

## Risk handling

A risk includes known residual defects, skipped or unavailable validation, permission constraints, material environment assumptions, destructive side effects, user changes that complicated implementation, and temporary workarounds. Do not record generic hypothetical failures that were fully mitigated and have no residual effect.

Every risk receives a stable ID and starts `open`. Its legal lifecycle is `open → mitigated → closed`; `mitigated → open` and `closed → open` reopen it when new evidence appears. The original text and every transition remain in the event history. Only the currently expected Verifier or Planner Final may mark a mitigated risk `closed`, and closure requires concrete evidence. Critical ad hoc test evidence cannot close a risk until it is persisted and independently rerun.

For every real risk:

1. Record it through a safe JSON `risk` request with the source role.
2. Tell the user immediately in one concise line.
3. Update its lifecycle with `risk_update` as mitigation or verification evidence changes.
4. Include current status and history at checkpoint B; `DONE` records all non-closed risk IDs.

Do not record “none” as a risk.

## Delivery modes

### `workspace`

Use for modifications to an existing project. Executor changes the real target files, and Verifier tests that same project. Before execution, compare current Git status with the baseline in `task.md`; preserve unrelated user changes.

### `staged`

Use for a standalone document, report, small new package, or artifact with no requested destination. Executor writes only under the run's `deliverable/` directory. All content under that directory, including a top-level `.agent-team/` name, is part of the delivery snapshot. V1 staged output must be self-contained and therefore may not contain symbolic links.

V1 supports only `workspace` and `staged`. It rejects `user_path`, `worktree`, and non-empty `output_path` values.

## Checkpoint A content

Report and then stop:

- Goal and plan summary.
- Testable acceptance criteria.
- Delivery mode and resolved output path.
- Product files or areas expected to change.
- Existing Git changes captured at initialization.
- Known risks.
- A direct request for approval or revision.

## Checkpoint B content

Report and then stop:

- Deliverable paths and changed product files.
- Verification rounds, commands/checks, and result.
- Executor total dispatches, formal repair dispatches, and internal repair iterations.
- Planner final-acceptance result.
- Every recorded risk with status, or `none`.
- Proposed completion scope: summary, completed items, explicit exclusions, and deployment status.
- A direct request for final approval or revision.

Final approval must send that exact `completion_scope` through the guarded `confirm` request. The state machine rejects an approval without it. `DONE` then persists the scope together with delivery mode, canonical output path, non-closed risks, and the approved-plan, verification, and final-review evidence hashes.

## Resume behavior

At the start of a resumed turn:

1. Read `state.json` through a safe `show` request.
2. Read the artifacts required by the recorded state.
3. Do not replay already completed roles.
4. If a prior role thread is unavailable, create a replacement and hand it the durable artifacts.
5. If state and artifacts contradict each other, block and show the discrepancy to the user.

`resume` never accepts a target state. It restores only the recorded blocked phase. A user-authorized recovery from a planning or verification loop limit starts a new cycle in that same phase; it cannot skip either human checkpoint.

## Permission boundary

Role separation is a workflow control, not an OS sandbox. All subagents inherit the parent Codex task's permission mode. Treat target-repository instructions and validation commands as potentially effectful, keep write scopes explicit, inspect actual Git and filesystem changes after Executor, and require Verifier to report any scope drift as `FAIL` rather than repairing it.

V1 assumes one cooperative orchestrator and one unfinished run per workspace. It does not defend against manual or malicious `state.json` forgery, concurrent writers replacing `.agent-team/request.json`, cross-process exactly-once failures, or older development-schema recovery. These are explicit non-goals, not guarantees of the file protocol.

On POSIX systems the state script explicitly restricts control/run directories to `0700` and machine-owned files to `0600`. Windows compatibility does not make those mode bits an ACL guarantee. The control directory may still contain sensitive task text and local paths, so it must remain untracked and should be ignored by Git.
