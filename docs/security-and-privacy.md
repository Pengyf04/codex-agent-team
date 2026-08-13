# Security and privacy

## Data flow

The Plugin itself has no telemetry, analytics endpoint, remote service, account login, or API key requirement. Its Python state machine uses the standard library and writes local workflow records only.

Codex and any tools used by the user's task remain governed by their own configuration, permissions, privacy terms, and network behavior. Installing this Plugin does not create an additional sandbox around those tools.

## Local data footprint

Each target workspace may contain `.agent-team/` with:

- original task text and acceptance criteria;
- local absolute workspace and run paths;
- plans, reviews, verification evidence, and final acceptance;
- product file summaries and validation commands;
- risks, workarounds, environmental dependencies, and lifecycle evidence;
- a transient JSON request file while a transition is being applied;
- staged deliverables when `staged` mode is selected.

Do not paste passwords, private keys, API keys, cookies, recovery codes, personal customer data, or production secrets into Agent Team task text or feedback.

## Local access controls

On POSIX systems, the state script explicitly restricts control and run directories to `0700`, and machine-owned state, lock, and guarded request files to `0600`. Run artifacts inherit protection from their private parent directory and are initially created privately.

Windows is supported experimentally in v0.1.0. POSIX mode bits are not a Windows ACL guarantee; users on shared Windows systems must review the workspace ACL and local Codex security posture themselves.

The workflow rejects symlinked control roots and refuses to operate if `.agent-team/` is already tracked by Git. If the directory is merely unignored, the Skill warns but does not edit the target repository's `.gitignore` without authorization.

## Retention and deletion

Run records are retained by default to support interruption recovery, audit, and result tracing. Nothing is deleted automatically.

Before deleting anything, inspect `.agent-team/runs/`, confirm the exact run ID, verify that the run is no longer active, and preserve any staged deliverable you still need. Use the operating system's Trash/Recycle Bin or another recoverable deletion method for the exact run directory. Remove the whole `.agent-team/` directory only after confirming that no run or staged deliverable must be retained.

Deleting a run is permanent from Agent Team's perspective; V1 has no remote backup or server-side recovery.

## Public repository hygiene

The repository validator and CI reject common credential formats, private-key headers, personal host paths, sensitive filenames, local cache directories, and unexpected Plugin package files. These checks reduce risk but cannot prove that arbitrary prose contains no private information. Maintainers must still review the complete staged diff and Git history before publication.

If a credential is exposed, rotate it first, then remove it from all reachable artifacts and history. See [SECURITY.md](../SECURITY.md).
