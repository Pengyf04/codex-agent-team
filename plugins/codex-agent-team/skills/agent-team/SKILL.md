---
name: agent-team
description: Four-role multi-agent orchestration for complex tasks with planning, adversarial plan review, implementation, verification loops, final acceptance, durable state, and two human approval checkpoints. Use only when the user explicitly invokes $agent-team or explicitly asks to run the Agent Team workflow; do not trigger for ordinary tasks.
---

# Agent Team

Act only as the orchestrator. Delegate planning, plan review, execution, verification, and final acceptance to distinct Codex subagents. Do not perform those role tasks yourself.

## Load the protocol

Read both [references/protocol.md](references/protocol.md) and [references/state-requests.md](references/state-requests.md) before starting or resuming a run. Before dispatching a role, also read its complete definition:

- Planner: [references/planner.md](references/planner.md)
- Plan Reviewer: [references/plan-reviewer.md](references/plan-reviewer.md)
- Executor: [references/executor.md](references/executor.md)
- Verifier: [references/verifier.md](references/verifier.md)

Use `scripts/state.py` for every state transition. Resolve it relative to this `SKILL.md`; never reimplement state transitions from memory or edit `state.json` directly. Use only its `guard` and JSON-file `request` interface. Never place free-form data in a shell command.

## Initialize or resume

1. If the user is resuming, identify the named `run_id`, issue a safe `show` request, and continue only from the recorded status and `expected` role.
2. For a new run, require a concrete task and at least one explicitly approved, testable acceptance criterion. If the user did not provide criteria, present `[PRE-INIT ACCEPTANCE CONFIRMATION]`, explicitly state that it is an initialization prerequisite rather than checkpoint A or B, propose the criteria, and ask for approval before initializing the run.
3. Propose one delivery mode and record it during initialization:
   - `workspace`: modify an existing target project in place.
   - `staged`: create a standalone artifact under the run's `deliverable/` directory.
   V1 rejects `user_path`, `worktree`, and non-empty `output_path` values.
4. Before every operation, run `state.py guard`. It creates or reuses a private `request.json` placeholder; update that file with a file-edit tool and copy in the fresh `request_id`. The ID is an idempotency key, not an authorization token, and is bound to the complete JSON payload: retry only the exact retained request. Initialize the run, then issue a `start` request with a new guarded ID. Preserve the returned `run_id`, absolute run directory, delivery paths, and `expected` object in every role prompt.
5. Abort if `.agent-team/` is already tracked by Git. Warn if it is not ignored. Do not edit `.gitignore`, create commits, push, or create a remote unless the user explicitly asks.
6. V1 allows only one unfinished Agent Team run per workspace. Resume or complete that run before initializing another.

## Dispatch rules

- Spawn a real subagent for each role. A skill instruction explicitly authorizes this delegation.
- Keep Planner → Reviewer and Executor → Verifier sequential because each depends on the previous artifact. Parallelize only independent, read-heavy work inside a role when useful.
- Omit model and reasoning overrides so every role inherits the current Codex configuration, unless the user explicitly requests overrides.
- Include the role definition, mode, absolute workspace, absolute run directory, delivery mode, required inputs, write scope, and allowed conclusion markers in every role prompt.
- Include the exact `expected.artifact`, `expected.artifact_token`, and any non-null `expected.subject`. For any non-`BLOCKED` result, the role must write exactly one `Decision: <MARKER>` immediately before the exact token, which remains the artifact's final non-empty line.
- Prefer continuing the same role thread with a follow-up during a loop. If that thread is unavailable after a user checkpoint or resume, spawn a fresh role subagent and make it reconstruct context from run files.
- Wait for the role result. Require its last non-empty line to be an allowed marker. If absent or invalid, ask that role once to restate a valid marker; if still invalid, block the run and report it.
- Extract real unresolved risks, material workarounds, residual issues, or environmental dependencies from role artifacts and messages. Do not record generic possibilities that were fully mitigated. Record each with a safe `risk` request, retain its returned ID, and immediately tell the user in one line. Use `risk_update` for every mitigation, closure, or reopening; never delete or rewrite risk history. Only the currently expected Verifier or Planner Final may close a mitigated risk with concrete evidence.
- For every Executor `EXECUTION_DONE`, copy the structured internal repair entries from `## Execution iterations` into the marker request as `execution_iterations`. Do not count a Verifier-triggered Mode B dispatch as an internal repair; the state machine tracks dispatch and repair-dispatch counts separately.
- For every non-`BLOCKED` Verifier result, copy the complete `### Test persistence` inventory into `test_evidence`. If a critical acceptance or risk-closing test is ad hoc, accept only `NEEDS_TEST_PERSISTENCE` or `FAIL`; never submit `PASS`. Preserve pending evidence IDs through Executor Mode B and require the next Verifier round to identify each as a persisted formal test reached by a standard validation command.
- After every role result, apply its marker with a safe JSON request containing the exact expected role and a fresh guarded `request_id`. A rejected request may represent an invalid transition or a post-commit transport failure: retry the retained request unchanged once, then inspect state and reconcile instead of bypassing it.

## Run the state machine

1. **Planning loop:** dispatch Planner mode A, then Plan Reviewer. Repeat on `NEEDS_REVISION`. The script blocks after five unresolved review rounds.
2. **Human checkpoint A:** on `APPROVED`, summarize the final plan, acceptance criteria, delivery mode, requested and canonical absolute output paths, expected product-file write scope, known risks, and existing Git changes. Never present only `deliverable/` or another relative path. Stop the turn and wait for explicit approval. Do not dispatch Executor yet. The state script binds approval to the reviewed plan and delivery snapshot; never bypass a mismatch.
3. **Execution loop:** after plan approval, dispatch Executor mode A. On `EXECUTION_DONE`, dispatch Verifier. On `FAIL` or `NEEDS_TEST_PERSISTENCE`, dispatch Executor mode B with the verification report and pending test IDs, then verify again. Each verification cycle has at most eight complete Verifier rounds, including persistence returns and rounds followed by Planner `FINAL_REJECT`.
4. **Final acceptance:** on `PASS`, dispatch Planner mode B. On `FINAL_REJECT`, return to Executor mode B. On `FINAL_ACCEPT`, enter checkpoint B.
5. **Human checkpoint B:** draft an explicit `completion_scope` containing a summary, non-empty completed items, explicit exclusions, and deployment status, then submit it through a guarded `prepare_final` request before asking the user. If preflight rejects it, correct the request before showing checkpoint B; never ask the user to approve an unvalidated scope. On success, summarize deliverables, verification evidence, Executor dispatch/repair/internal-iteration counts, final acceptance, and every risk with status. Display the exact returned `prepared_completion_scope.scope`, its scope hash, `canonical_output_path`, and the named evidence hashes. Show absolute deliverable and entry-file paths; never present only `deliverable/` or another relative path. Stop the turn and wait for explicit approval. Any later delivery or evidence change invalidates the prepared scope and requires a revision cycle.
6. **Completion:** after final approval, send a final `confirm` request without reconstructing or resending `completion_scope`; the state script consumes the exact prepared scope, persists canonical delivery information, all non-closed risk IDs, and the three named evidence hashes with `DONE`, and clears the preview. Summarize completed and excluded scope, absolute location, verification, execution counts, decisions, and risks; state `none` when no risks exist.

## Human checkpoints

- The pre-initialization acceptance confirmation is a prerequisite to creating a run and is not checkpoint A or B.
- Never infer approval from silence, a previous message, or a subagent result.
- Record checkpoint decisions only after the user explicitly approves or requests revision.
- A revision at checkpoint A starts a new planning cycle. A revision at checkpoint B starts a new verification cycle.
- Resume a `BLOCKED` run only after the user resolves the blocker or explicitly chooses a recovery path; record that reason with a safe `resume` request. The script restores the blocked phase and does not accept a caller-selected target.

## Write boundaries

- Only Executor may intentionally modify product files or final deliverables.
- Planner, Plan Reviewer, and Verifier may read product files and write only their designated run artifacts. Verification commands may create normal transient build/test output, but Verifier must not fix product code.
- The orchestrator may create and update run-control artifacts only.
- Preserve all pre-existing user changes. If safe separation is impossible, block and ask the user.
- These are behavioral role boundaries, not operating-system security boundaries: subagents inherit the parent task's permissions. Verify actual file and Git changes at Executor and Verifier handoffs, and never describe role separation as a sandbox.

## V1 operating boundary

- Assume cooperative local agents that may make mistakes but do not maliciously forge `state.json` or control files.
- Run one orchestrator sequentially in a workspace; do not run multiple Agent Team workflows that race on `.agent-team/request.json`.
- The request protocol provides same-process retry safety, not cross-process exactly-once delivery, authentication, or an OS security boundary.
- Runs created by older development schemas are not resumable. They are ignored when initializing a fresh V1 run and may be archived manually after inspection.

## Progress reporting

Keep the user informed with one concise line per transition, for example:

`[VERIFYING 3/8] Verifier returned NEEDS_TEST_PERSISTENCE; sending the critical evidence IDs to Executor for durable regression coverage.`

Do not expose raw subagent logs unless the user asks. Report blockers and risks immediately.
