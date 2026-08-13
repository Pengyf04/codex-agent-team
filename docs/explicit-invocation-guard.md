# Optional fail-closed invocation guard

## Why this exists

A Plugin can be correctly installed while its Skill is absent from one task's model-visible capability snapshot. This can happen when a host has a very large Skill catalog or when unstructured text is used instead of the mention picker.

The Agent Team Skill cannot enforce anything when the host did not expose it. An optional `AGENTS.md` rule can still prevent Codex from silently replacing the requested workflow with an ordinary single-agent or generic multi-agent process.

This guard is a stop condition, not a host/runtime fix. It does not force-load a missing Skill and must not claim that installation alone proves activation.

## Recommended rule

Add the following block to a personal or project `AGENTS.md` only if you want explicit Agent Team requests to fail closed:

```markdown
## Agent Team explicit-invocation guard

- Trigger this guard only when the user explicitly selects or names the
  `codex-agent-team` Plugin or `$agent-team` Skill.
- Success requires the current task to expose and allow reading the canonical
  Skill `codex-agent-team:agent-team`. Files in a local source tree or Plugin
  cache are not activation evidence.
- On success, the first progress message must be:
  `[AGENT_TEAM_GUARD] ACTIVE: codex-agent-team:agent-team`
  Then read that Skill's complete `SKILL.md` and use it as the only
  orchestration workflow. Do not substitute a remembered or generic workflow.
- If the canonical Skill is missing, unreadable, ambiguous, or resolves to a
  different Skill, stop before product work or agent dispatch and reply:
  `[AGENT_TEAM_GUARD] BLOCKED: expected codex-agent-team:agent-team; no fallback executed.`
  Report the observed mismatch and wait for the user.
```

Keep the trigger narrow. Ordinary tasks that do not explicitly invoke Agent Team should not be blocked by this rule.

## Verification

After installing or updating the Plugin, open a new task and select **Agent Team** from the Plugin/Skill mention picker. A successful guarded start must show the `ACTIVE` line before orchestration. If it shows `BLOCKED`, use `codex plugin list --available --json` to inspect installation state, start another new task, and select the structured mention again.

Do not treat a literal `$agent-team` passed through a non-interactive wrapper as equivalent to structured selection. In a large catalog it may be omitted before the model can read it; the correct fail-closed outcome is to stop, not to fall back.
