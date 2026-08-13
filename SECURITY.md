# Security Policy

## Supported versions

Security fixes are provided for the latest released minor line. Development snapshots from `main` are intended for local evaluation and may change without a migration path.

| Version | Supported |
|---|---|
| 0.1.x | Yes |
| Older development builds | No |

## Report a vulnerability privately

Do not open a public issue for a suspected vulnerability or exposed credential. Open the repository's **Security** tab and use **Report a vulnerability** (GitHub Private Vulnerability Reporting). Include the affected version, platform, reproduction steps, impact, and the smallest safe proof of concept.

Reports are handled on a best-effort basis. Please avoid accessing data that does not belong to you, disrupting third-party systems, or publishing exploit details before a fix is available.

## Security boundary

Agent Team is a workflow and evidence-control layer, not an operating-system sandbox:

- Every subagent inherits the parent Codex task's model, tools, permissions, and network access.
- Role write boundaries are behavioral controls and are independently checked at handoffs.
- The local state protocol assumes one cooperative orchestrator and one unfinished run per workspace.
- Request IDs provide idempotency, not authentication or authorization.
- V1 does not defend against a malicious local user who can rewrite control files, hostile cross-process writers, or forged state created outside the workflow.

The Plugin does not require credentials, add telemetry, or proactively call a network service. Tasks executed by Codex may still use tools or credentials already authorized by the user.

See [Security and privacy](docs/security-and-privacy.md) for the complete data and threat model.

## Credential exposure

If a password, token, API key, private key, or cookie is ever committed or included in a public artifact, revoke and rotate it immediately. Removing the file or rewriting Git history is not a substitute for rotation. Then remove the value from all reachable history and review related access logs.
