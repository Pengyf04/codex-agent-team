# Local pre-release report for 0.1.0

Date: 2026-08-13

This is a historical evidence snapshot captured immediately before the first public
candidate push.

Status at capture: **local release candidate passed; public candidate publication
was authorized, but a version tag and GitHub Release were not authorized**.

## Completed scope

- Public Codex Plugin manifest, local Marketplace manifest, embedded `$agent-team`
  Skill, four role contracts, and the schema 5 state machine are packaged as
  version `0.1.0`.
- Public identity is `Pengyf04`; the tree contains no published personal email,
  real name, credential, or machine-specific absolute path.
- Runtime remains Python 3.10+ standard-library only. Development dependencies are
  pinned separately.
- POSIX control directories and files are enforced as `0700` and `0600` where
  applicable. Git-tracked `.agent-team/` control data is rejected.
- Documentation covers installation, explicit invocation, update, uninstall,
  local record retention, privacy, security, architecture, and contribution.
- CI is defined for Ubuntu, macOS, and experimental Windows, with minimal
  permissions, pinned Action revisions, CodeQL, and pull-request dependency
  review.

## Local evidence

- Repository validator: 33 public files passed after the security workflows were
  added.
- Standard regression suite: 90 tests passed; the suite was exercised on Python
  3.10, 3.12, and 3.14 during local validation.
- Ruff check and format, Mypy strict, and Bandit passed.
- The OpenAI Skill and Plugin validators passed.
- An isolated temporary `HOME` and `CODEX_HOME` completed Marketplace discovery,
  first install, an `add` while already installed, removal, reinstall, second
  removal, and cleanup. Each installed state matched the source package digest.
- An independent read-only Verifier returned `PASS` for the local public-release
  candidate scope with no P0-P2 finding.

Two findings were closed only after they became standard regression evidence:

1. Transient Python bytecode could race with package-digest comparison. The digest
   now ignores bytecode on both sides, and a regression test covers it.
2. The documented installed-state update path had only an ad hoc probe. The
   standard smoke now performs it before the first removal, and a regression test
   enforces that ordering.

## Open evidence gates

These items are deliberately not counted as completed:

1. The current task capability snapshot does not provide a structured-mention
   activation receipt for `codex-agent-team:agent-team`. Installed/enabled state,
   cache parity, or a fail-closed raw-prompt guard is not evidence that a new task
   loaded and executed the complete four-role workflow.
2. Linux and experimental Windows jobs must be confirmed from the GitHub Actions
   run triggered by the first public push. Green supported-platform results are
   required before a release tag is created.
3. Installation and update from the public GitHub Marketplace source must be
   tested from a fresh clone after the first public push.
4. The initial public commit, branch protection, and GitHub security settings are
   candidate-publication operations. A version tag and GitHub Release remain a
   separately authorized phase.

## Release decision

No commit, Git remote, push, tag, GitHub repository, or GitHub Release was created
during the local-implementation phase covered by this snapshot. The first four
items may be created under the separate public-candidate authorization; a version
tag and GitHub Release remain blocked until a later approval. A structured-mention
new-task test should be recorded before that approval whenever the host exposes the
canonical Skill.
