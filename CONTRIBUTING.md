# Contributing

Thank you for improving Codex Agent Team.

## Before opening a change

- Use a current Codex build with Plugin and subagent support.
- Use Python 3.10 or later.
- Keep runtime code on the Python standard library unless a dependency is explicitly justified and approved.
- Open a private vulnerability report instead of a public issue for security-sensitive findings.

## Development setup

Create an isolated Python environment, then install the pinned development dependencies from `requirements-dev.txt`.

Run the standard local checks:

```bash
python3 scripts/validate.py
python3 -m unittest discover -s tests -v
ruff check .
ruff format --check .
mypy --strict plugins/codex-agent-team/skills/agent-team/scripts/state.py scripts/validate.py tests
bandit -q -r plugins/codex-agent-team/skills/agent-team/scripts scripts
python3 scripts/smoke_local_install.py
```

## Change rules

- Preserve the two human approval checkpoints and the single-writer Executor rule.
- Add a formal regression test for every defect or security invariant being fixed.
- Keep critical one-off verification probes from becoming final acceptance evidence until they are persisted under `tests/` and reached by the standard test command.
- Update both README languages when user-visible behavior changes.
- Do not commit `.agent-team/`, credentials, local paths, caches, installation snapshots, or generated logs.
- Do not commit a local `+codex.<cachebuster>` version suffix.

## Pull requests

Describe the problem, the smallest solution, affected workflow states, validation commands, and residual risks. CI must pass on every supported test platform. A passing test is evidence only for the behavior it actually exercises.

See [Development and release](docs/development-and-release.md) for package validation and local Plugin installation.
