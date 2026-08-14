# Changelog

All notable public changes are recorded here. This project follows Semantic Versioning.

## [0.1.0] - 2026-08-13

### Added

- Repository Marketplace and installable Codex Plugin containing the `$agent-team` Skill.
- Durable Planner, Plan Reviewer, Executor, Verifier, and Planner Final workflow with two human approval checkpoints.
- Evidence-gated state transitions, bounded planning and verification loops, resumable local run records, and two delivery modes.
- Mandatory persistence of acceptance-critical ad hoc tests before `PASS` or risk closure.
- Auditable risk lifecycle, Executor iteration counters, and explicit `DONE` completion scope.
- POSIX-private control data permissions and refusal to run when `.agent-team/` is tracked by Git.
- English and Chinese user documentation, public security guidance, release checks, and cross-platform CI.

### Changed

- Checkpoint B now strictly preflights and binds the completion scope before human approval, so final confirmation consumes the prepared value instead of rebuilding it.
- State summaries expose the canonical absolute output path and separately named approved-plan, verification, and final-review evidence hashes.
- Pre-initialization acceptance confirmation is explicitly distinguished from human checkpoints A and B, and checkpoint/final handoffs require absolute delivery paths.
- The durable state schema is now version 6; older development runs remain non-resumable by design.

[0.1.0]: https://github.com/Pengyf04/codex-agent-team/releases/tag/v0.1.0
