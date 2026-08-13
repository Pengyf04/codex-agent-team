# Development and release

## Runtime and development dependencies

Runtime requires a Codex build with Plugins and subagents plus Python 3.10 or later. `state.py` has no third-party runtime dependency. Git is optional, but recommended for `workspace` deliveries.

Development tools are pinned in `requirements-dev.txt`. Install them in an isolated environment, then run the commands in [CONTRIBUTING.md](../CONTRIBUTING.md).

## Local Marketplace installation

From a clone of this repository, add the repository root as an explicit local Marketplace and install the Plugin:

```bash
codex plugin marketplace add /absolute/path/to/codex-agent-team
codex plugin add codex-agent-team@codex-agent-team
```

Start a new Codex task after installation. Existing tasks do not prove that a newly installed or updated Skill was loaded.

For local iteration, use Codex's bundled `plugin-creator` cachebuster helper instead of incrementing the release version or hand-editing Marketplace configuration. Reinstall the Plugin, start a new task, and restore the manifest to clean SemVer before committing.

## Validation layers

1. Repository structure and secret/PII checks: `python3 scripts/validate.py`.
2. Standard regression suite: `python3 -m unittest discover -s tests -v`.
3. OpenAI Skill and Plugin validators pinned to the revision recorded in CI.
4. Ruff, Mypy strict, and Bandit.
5. Clean-room local Marketplace add/install/installed-state update/remove/reinstall checks: `python3 scripts/smoke_local_install.py`.
6. A new-task forward test that exercises both human checkpoints and at least one repair path.

Passing a narrower layer is not evidence for a broader release claim.

## Release checklist

- Keep `plugin.json` on strict SemVer without a local cachebuster suffix.
- Confirm Plugin, Marketplace, README, changelog, and release tag agree on the version.
- Review the complete file inventory and verify there are no symlinks or unexpected binaries in the Plugin.
- Run all CI-equivalent commands locally and inspect every failure rather than weakening the gate.
- Scan the pending Git history as well as the current tree for secrets and personal paths.
- Obtain explicit public-release approval before creating the remote, pushing, or tagging.
- Create an immutable `v<version>` tag and GitHub Release only from a green, reviewed commit.
- Install again from the public Git source, start a new task, and record the release evidence.

For a security regression, stop the release and follow [SECURITY.md](../SECURITY.md). Never overwrite a released tag; publish a new SemVer patch instead.

The current local candidate evidence and remaining release gates are recorded in the
[pre-release report](pre-release-report.md).
