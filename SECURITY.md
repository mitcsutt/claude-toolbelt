# Security policy

## Supported versions

The latest `main` and the latest released version of each plugin.

## Reporting a vulnerability

Report privately through GitHub private vulnerability reporting: this repo's
**Security** tab, then **Report a vulnerability**. Do not open a public issue
for a vulnerability.

Expect a best-effort acknowledgement within 7 days.

## Known, documented behaviour

`agent-loop` deliberately runs `claude --print --dangerously-skip-permissions`
headless inside a git worktree. That is intended and documented in
[`plugins/agent-loop/README.md`](plugins/agent-loop/README.md), not a vulnerability.
