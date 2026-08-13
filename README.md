# Codex Agent Team

[简体中文](README.zh-CN.md)

Codex Agent Team is a Codex plugin that runs complex work through four specialized subagents:

```text
Planner ↔ Plan Reviewer → Human checkpoint A
                         ↓
Executor ↔ Verifier → Planner final acceptance → Human checkpoint B
```

The workflow keeps durable artifacts on disk, caps automatic review loops, reports risks immediately, and resumes from the recorded state instead of relying on chat memory.

## What it provides

- Real Codex subagents rather than simulated role-play.
- Planner, adversarial Plan Reviewer, single-writer Executor, and independent Verifier roles.
- Up to 5 plan-review rounds and 8 complete verification rounds per cycle.
- Human approval before product changes and before final completion.
- Structured markers for deterministic routing.
- A zero-dependency Python state machine with atomic JSON updates and idempotent request receipts.
- Durable handoffs under `.agent-team/runs/`.
- Acceptance-critical ad hoc tests must become standard-command regression tests before they can support `PASS` or risk closure.
- Stable risk IDs, an `open → mitigated → closed` lifecycle, and immutable event history.
- Separate counts for all Executor dispatches, verification-return repairs, and internal self-check repair iterations.
- A `DONE` receipt containing completed scope, explicit exclusions, deployment status, open risks, and final evidence hashes.
- Two deliberately bounded delivery modes: an existing workspace or a run-local staged artifact.

The first release intentionally does not require custom `.codex/agents/*.toml` files or an MCP server. Role instructions ship inside the Skill, and subagents inherit the active Codex model and permission settings.

## Requirements

- A current Codex release with Plugins and subagents enabled.
- Python 3.10 or newer for the bundled state helper.
- Git is optional, but recommended for workspace delivery.

| Platform | v0.1.0 support |
|---|---|
| macOS | Supported |
| Linux | Supported |
| Windows | Experimental; CI-covered, but Marketplace installation must be validated on each target environment |

## Install from GitHub

Add the public repository as a Marketplace and install the Plugin:

```bash
codex plugin marketplace add Pengyf04/codex-agent-team
codex plugin add codex-agent-team@codex-agent-team
```

Start a new Codex task after installation so the new Skill is loaded.

## Local development install

From a local clone, add the repository root as a marketplace and install the plugin:

```bash
codex plugin marketplace add /absolute/path/to/codex-agent-team
codex plugin add codex-agent-team@codex-agent-team
```

The repository marketplace is defined at `.agents/plugins/marketplace.json`; the installable plugin lives at `plugins/codex-agent-team/`.

## Update and uninstall

Refresh the Git Marketplace snapshot, reinstall the Plugin, and then start a new task:

```bash
codex plugin marketplace upgrade codex-agent-team
codex plugin add codex-agent-team@codex-agent-team
```

Remove the installed Plugin before removing its Marketplace source:

```bash
codex plugin remove codex-agent-team@codex-agent-team
codex plugin marketplace remove codex-agent-team
```

Plugin removal does not delete `.agent-team/` records from target workspaces. Review and clean those records separately if they are no longer needed.

## Use

Invoke the Skill explicitly from Codex's Skill/Plugin mention picker:

```text
$agent-team Add optimistic locking to the order update flow. Preserve existing API behavior and add regression tests.
```

In the ChatGPT desktop app, you can also type `@` and select **Agent Team** from installed plugins. In an interactive Codex CLI session, type `$` and select `agent-team` from autocomplete. Selecting the mention is more reliable than passing the same characters as unstructured text through a non-interactive wrapper, especially when the host has a very large Skill catalog.

The Skill does not implicitly activate for ordinary tasks. If acceptance criteria or a material delivery decision is missing, the orchestrator asks before starting.

For environments with very large Skill catalogs, the optional [fail-closed invocation guard](docs/explicit-invocation-guard.md) prevents an explicit Agent Team request from silently falling back when the canonical Skill was not exposed. The guard detects and stops; it does not modify the Codex host or force-load a missing Skill.

### Human checkpoint A

After Planner and Plan Reviewer agree, Codex shows:

- The approved plan and acceptance criteria.
- Delivery mode plus requested and canonical output paths.
- Expected write scope.
- Existing Git changes and known risks.

No Executor work starts until the user explicitly approves.

### Human checkpoint B

After verification and independent final acceptance, Codex shows:

- Deliverable and changed-file locations.
- Verification commands and evidence.
- Executor dispatch, formal-repair, and internal-repair counts.
- Final-acceptance result.
- Every recorded risk and current status, or `none`.
- The completed items, explicit exclusions, and deployment status that will be recorded in `DONE`.

The run is marked complete only after explicit approval of that `completion_scope`; the state machine rejects a final approval that omits it.

## Delivery modes

| Mode | Use case | Product output |
|---|---|---|
| `workspace` | Modify an existing project | Real target workspace |
| `staged` | Produce a standalone artifact | Run-local `deliverable/` |

Only Executor intentionally edits product files. Planner, Plan Reviewer, and Verifier are read-only with respect to the product; Verifier may generate normal transient test/build output. These are workflow rules, not separate OS sandboxes: all subagents inherit the parent task's permissions, so the workflow also checks fresh role evidence and requires Verifier to detect actual write-scope drift.

V1 intentionally rejects `user_path`, `worktree`, and non-empty `output_path` values. Staged output must be self-contained and cannot contain symbolic links. This keeps checkpoint identity and recovery behavior small enough to audit reliably.

## Run data

Each run creates:

```text
.agent-team/runs/<timestamp>-<slug>/
├── state.json
├── task.md
├── plan.md
├── review-log.md
├── verify-report.md
├── final-review.md
├── deliverables.md
├── risk-log.md
├── .state.lock        # machine-owned concurrency lock
└── deliverable/        # staged mode only
```

The orchestrator uses a guarded `.agent-team/request.json` placeholder for each state operation. `state.py guard` applies private local permissions, creates or reuses that placeholder, and returns a fresh `request_id`; every successful operation, including `show`, binds the ID to the complete JSON payload digest in `state.json` and then removes the request file. Only an exact retained request is idempotent, and a different payload cannot reuse its ID. Free-form task text, feedback, risks, and marker details are written as JSON with a file-edit tool and are never interpolated into shell commands. Structured Verifier `test_evidence` prevents a critical one-off test from supporting `PASS`: the workflow returns `NEEDS_TEST_PERSISTENCE` until Executor persists it and Verifier reruns the same evidence ID through a standard command. `risk_update` mitigates, closes, or reopens risks without deleting their history. State also records the three Executor counters and requires explicit completion scope at final approval. The checkpoint-A plan remains bound through execution; `PASS` freezes delivery content, `deliverables.md`, and `verify-report.md`; checkpoint B also binds final-review and risk evidence. `DONE` is terminal and is never reactivated by later artifact drift.

Add `.agent-team/` to the target project's ignore rules. The Skill warns when it is not ignored, refuses to run when it is already tracked, and never edits `.gitignore` automatically. Records are retained by default; they may contain task text, evidence, risks, staged output, and local absolute paths. See [Security and privacy](docs/security-and-privacy.md) before using the Plugin with sensitive work.

## Safety and cost

- Subagents inherit the parent Codex permission mode.
- Role separation is a behavioral quality control, not a security boundary between processes.
- The Skill never commits, pushes, creates remotes, merges, or deletes branches without explicit authorization.
- Pre-existing user changes must be preserved.
- Multi-agent workflows consume substantially more time and tokens than a single-agent task; use Agent Team for genuinely complex work. Hard loop limits prevent unbounded retries.
- A `BLOCKED` run resumes only after the user resolves the blocker or approves a recovery path.
- A blocked run can resume only to its recorded phase; delivery changes after review force a new planning cycle.
- V1 allows one unfinished Agent Team run and one cooperative orchestrator per workspace.
- V1 does not claim protection against malicious local `state.json` forgery, concurrent request writers, cross-process exactly-once delivery, OS-level role isolation, or recovery of older development schemas.
- The Plugin itself adds no telemetry, account login, or API-key requirement. Tools used by the parent Codex task remain governed by their own permissions and network behavior.

## Troubleshooting

Use `codex plugin list --available --json` to confirm Marketplace discovery and installation state. If Codex warns that its Skill catalog exceeded the context budget, open a new task and explicitly select **Agent Team** or `agent-team` from the mention picker. A literal `$agent-team` inside a raw `codex exec` prompt is not a dependable substitute for structured Skill selection in a heavily populated catalog.

## Development

Run the repository checks:

```bash
python3 scripts/validate.py
python3 -m unittest discover -s tests -v
python3 scripts/smoke_local_install.py
python3 scripts/smoke_local_install.py \
  --marketplace-source Pengyf04/codex-agent-team \
  --exercise-upgrade
```

CI runs the regression suite on Ubuntu 3.10/3.12/3.14 plus macOS and Windows 3.12. It also runs pinned OpenAI Skill/Plugin validators, Ruff, Mypy strict, Bandit, CodeQL, pull-request dependency review, package inventory checks, and secret/PII checks. Development dependencies are pinned in [requirements-dev.txt](requirements-dev.txt).

The approved implementation design is recorded in [plan.md](plan.md).

Architecture, the optional invocation guard, contribution, release, and vulnerability-reporting details are in [docs/architecture.md](docs/architecture.md), [docs/explicit-invocation-guard.md](docs/explicit-invocation-guard.md), [CONTRIBUTING.md](CONTRIBUTING.md), [docs/development-and-release.md](docs/development-and-release.md), and [SECURITY.md](SECURITY.md).

## Origin

This Codex-native implementation adapts the workflow concepts from [Pengyf04/claude-agent-team](https://github.com/Pengyf04/claude-agent-team) while using Codex Plugins, Skills, and native subagents.

## License

[MIT](LICENSE)
