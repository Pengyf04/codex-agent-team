# Architecture

## Package shape

The public repository is a repository Marketplace. Its `codex-agent-team` Plugin contains one explicitly invoked `$agent-team` Skill. The Skill ships role definitions, the orchestration protocol, and a Python standard-library state machine as one versioned unit.

No MCP Server or separate `.codex/agents/*.toml` installation is required.

## Workflow

```text
Planner <-> Plan Reviewer -> Human checkpoint A
                                  |
                                  v
Executor <-> Verifier -> Planner Final -> Human checkpoint B -> DONE
```

- **Planner** defines scope, write boundaries, tests, risks, and recovery.
- **Plan Reviewer** adversarially challenges completeness and executability.
- **Executor** is the only role authorized by the workflow to modify product files.
- **Verifier** independently checks every acceptance criterion and the actual write scope.
- **Planner Final** compares the delivered result with the original task and approved plan.
- **Orchestrator** dispatches roles and applies state transitions; it does not perform their work itself.

Planner Final is a second mode of the Planner role, not a fifth persistent role definition.

## State and evidence

Every run stores its machine state and human-readable handoffs under `.agent-team/runs/<run-id>/`. `state.json` is the machine source of truth; role artifacts are evidence bound to the currently expected role with a fresh token and subject digest.

The state machine enforces:

- at most five unresolved planning reviews per planning cycle;
- at most eight complete Verifier rounds per verification cycle;
- explicit approval at both human checkpoints;
- request ID binding to the complete JSON payload;
- approved-plan and verified-delivery subject snapshots;
- append-only review evidence where required;
- test persistence before critical ad hoc evidence can support `PASS` or risk closure;
- stable risk IDs and immutable lifecycle history;
- separate Executor dispatch, repair-dispatch, and internal-repair counters;
- explicit completed, excluded, deployment, and residual-risk scope at `DONE`.

## Delivery modes

- `workspace`: Executor modifies the current target workspace and preserves unrelated user changes.
- `staged`: Executor creates a self-contained artifact inside the run's `deliverable/` directory.

V1 rejects arbitrary user paths and Git worktrees. It permits one unfinished Agent Team run per workspace.

## Trust boundaries and non-goals

The roles share the parent Codex process permissions. Separation is therefore a quality and accountability control, not a process sandbox. V1 assumes cooperative agents that may make mistakes; it does not promise malicious-state tamper resistance, operating-system role isolation, authenticated requests, hostile multi-process coordination, or cross-process exactly-once delivery.

See [Security and privacy](security-and-privacy.md) for local data and permission details.
