# Safe state request protocol

All state operations use one transient JSON file. Never interpolate task text, acceptance criteria, paths, role messages, marker details, risks, or user feedback into a shell command.

## Transport

1. Run `python3 <state.py> guard` with the target workspace set as the command tool's working directory. This validates that `.agent-team/` and `.agent-team/runs/` are real, untracked directories rather than symlinks, applies private local permissions where POSIX modes are available, creates or reuses an empty private `.agent-team/request.json` placeholder, and returns that path with a fresh `request_id`.
2. Update the guarded `.agent-team/request.json` placeholder with a file-edit tool such as `apply_patch`. Copy the exact `request_id` returned by that guard call. Do not use shell redirection, `printf`, `echo`, a here-document, or an inline Python expression to construct it.
3. Run `python3 <state.py> request` from the same target workspace. The script serializes request handling, executes the operation, deletes the request only after success, and returns a JSON state summary. A failed request remains available for inspection and correction.
4. Use the returned `run_id` for later requests. Do not put an absolute run path on the command line.

The only shell tokens should be the trusted state-script path and the fixed `guard` or `request` subcommand. User-controlled data belongs only in the JSON file.

Request handling uses `.agent-team/.request.lock`. Every successful request binds its `request_id` to the command and complete-payload digest in `state.json`, then removes the request file; the next `guard` recreates a private placeholder. A mutating request commits the receipt atomically with its transition; `show` commits it with any safe reconciliation. Retrying the exact retained request after request-file deletion or process failure returns the current committed state without executing the operation twice, while the same ID with a different payload is rejected. The ID is not an authorization token, and V1 does not support concurrent writers. Never replace a retained failed request with a new operation before reconciling it.

## Requests

Initialize:

```json
{
  "request_id": "req-0123456789abcdef0123456789abcdef",
  "command": "init",
  "slug": "short-task-name",
  "task": "The exact user-approved task, kept literal even when it contains $() or quotes.",
  "acceptance_criteria": [
    "First objective, testable criterion.",
    "Second objective, testable criterion."
  ],
  "delivery_mode": "workspace"
}
```

Executor `EXECUTION_DONE` must include its internal repair iterations:

```json
{
  "request_id": "req-0123456789abcdef0123456789abcdef",
  "command": "marker",
  "run_id": "20260811-120000-short-task-name",
  "role": "executor",
  "marker": "EXECUTION_DONE",
  "execution_iterations": [
    {
      "trigger": "self-check",
      "failed_check": "Boundary regression failed.",
      "fix": "Corrected the boundary condition.",
      "validation": "Standard test command now passes."
    }
  ]
}
```

Every non-`BLOCKED` Verifier marker must include all test evidence used in that round. A critical ad hoc item requires `NEEDS_TEST_PERSISTENCE` (or `FAIL` if the product also fails), and its ID remains pending until a later round declares the same ID as formal and persisted:

```json
{
  "request_id": "req-0123456789abcdef0123456789abcdef",
  "command": "marker",
  "run_id": "20260811-120000-short-task-name",
  "role": "verifier",
  "marker": "NEEDS_TEST_PERSISTENCE",
  "test_evidence": [
    {
      "id": "wall-boundary",
      "description": "Wall collision fails without moving the snake.",
      "kind": "ad_hoc",
      "critical": true,
      "persisted": false,
      "target": "",
      "risk_ids": ["R-0001"]
    }
  ]
}
```

V1 accepts only `workspace` and `staged`. Omit `output_path`; non-empty values and other delivery modes are rejected.

Show or start:

```json
{"request_id":"req-0123456789abcdef0123456789abcdef","command":"show","run_id":"20260811-120000-short-task-name"}
```

```json
{"request_id":"req-0123456789abcdef0123456789abcdef","command":"start","run_id":"20260811-120000-short-task-name"}
```

For an active run, `show` is a safe reconciliation point, not merely a raw read: before returning `expected.role`, it restores accidentally missing owned evidence as empty templates and checks the approved plan and frozen verification/final subjects. Recorded-subject drift starts the appropriate new cycle; deleting only the current role's unfrozen artifact refreshes that role's expectation. If the deleted file held the prior `NEEDS_REVISION`, `FAIL`, or `FINAL_REJECT` feedback required by the next role, the run enters `BLOCKED` until the user explicitly chooses a recovery path. `BLOCKED` otherwise remains frozen until `resume`; `DONE` remains terminal even if its artifacts later drift.

Apply a role marker only after the role has updated the artifact named in `expected.artifact` and ended it with the exact `expected.artifact_token`, replacing `<MARKER>` with its real conclusion:

```json
{
  "request_id": "req-0123456789abcdef0123456789abcdef",
  "command": "marker",
  "run_id": "20260811-120000-short-task-name",
  "role": "planner",
  "marker": "PLAN_READY",
  "detail": "Optional literal detail."
}
```

Checkpoint decision:

```json
{
  "request_id": "req-0123456789abcdef0123456789abcdef",
  "command": "confirm",
  "run_id": "20260811-120000-short-task-name",
  "checkpoint": "plan",
  "decision": "approve",
  "detail": "The user's exact decision or revision feedback."
}
```

Final approval requires the exact scope shown to and approved by the user:

```json
{
  "request_id": "req-0123456789abcdef0123456789abcdef",
  "command": "confirm",
  "run_id": "20260811-120000-short-task-name",
  "checkpoint": "final",
  "decision": "approve",
  "detail": "The user's exact final approval.",
  "completion_scope": {
    "summary": "Implemented and independently verified the approved workspace change.",
    "completed": ["Product change", "Formal regression tests"],
    "excluded": ["Production deployment"],
    "deployment_status": "not_deployed"
  }
}
```

Allowed deployment statuses are `not_applicable`, `local_only`, `not_deployed`, and `deployed`. Both `completed` and `excluded` must be non-empty; use an explicit `none` item rather than omitting exclusions. The state machine derives delivery paths, open risk IDs, and evidence hashes instead of trusting request input.

Other operations:

```json
{"request_id":"req-0123456789abcdef0123456789abcdef","command":"set_delivery","run_id":"20260811-120000-short-task-name","mode":"staged"}
```

```json
{"request_id":"req-0123456789abcdef0123456789abcdef","command":"risk","run_id":"20260811-120000-short-task-name","source":"verifier","text":"A real unresolved risk or environmental dependency."}
```

`risk` returns the new stable risk ID in the state summary. Change status without deleting history:

```json
{"request_id":"req-0123456789abcdef0123456789abcdef","command":"risk_update","run_id":"20260811-120000-short-task-name","risk_id":"R-0001","source":"executor","status":"mitigated","detail":"Implemented the planned mitigation."}
```

Only the currently expected `verifier` or `planner_final` may move `mitigated` to `closed`; closure requires `evidence` and a `test_evidence` array, which may be empty only when closure does not rely on a test. Reopen a mitigated or closed risk by setting status to `open` with the new evidence in `detail`.

```json
{"request_id":"req-0123456789abcdef0123456789abcdef","command":"block","run_id":"20260811-120000-short-task-name","reason":"Specific blocker."}
```

```json
{"request_id":"req-0123456789abcdef0123456789abcdef","command":"resume","run_id":"20260811-120000-short-task-name","reason":"What the user resolved or explicitly authorized."}
```

`resume` has no target field. The state machine restores only the blocked phase, reconciles its recorded subjects before returning a role, and starts a new cycle if they drifted. Loop-limit recovery starts a new cycle at the safe role for that same phase.

## Returned expectation

Every active state summary contains an `expected` object:

```json
{
  "role": "verifier",
  "markers": ["PASS", "FAIL", "NEEDS_TEST_PERSISTENCE", "BLOCKED"],
  "artifact": "verify-report.md",
  "artifact_token": "<!-- agent-team:verification cycle=1 round=1 marker=<MARKER> -->",
  "subject": null
}
```

Dispatch only this role. Its non-`BLOCKED` result is accepted only when the named artifact is fresh, append-only where required, contains exactly one `Decision: <MARKER>` immediately before the exact final token, and any non-null `subject` snapshot is unchanged. A rejected request is a protocol error: retry the retained request unchanged first so an already committed `request_id` can be recognized; then inspect state with a new `show` request and reconcile. Never edit `state.json`.
