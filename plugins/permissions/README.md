# permissions

Finds unsafe, dead and contradictory permission rules across every settings scope, and tells you when a new one appears.

## Why

Rules pile up. A one-off `Bash(kill -9 1927)` stays forever, `Bash(bash *)` sits unnoticed, and an `mcp__buildkite__*` allow never matches the connector that actually runs. Claude Code's [auto mode](https://code.claude.com/docs/en/permission-modes) now owns prompt reduction, so this plugin does not try to cut prompts. It audits the rules you already have.

What it no longer does, and why:

- **No per-call logging.** 2.x ran a hook on every tool call; each node spawn measured about 34 ms (bare `node -e 0` is about 25 ms), and the logs held no outcome data. Transcripts already record calls, modes and denials, so 3.x reads those on demand instead.
- **No allow-list seeding or blanket promotion.** Auto mode drops broad allows anyway. Candidate rules appear in `/permissions-review` only when they are narrow and trip no risk check.

## What it ships

| Component | Kind | What |
| --- | --- | --- |
| `hooks/session-alert.mjs` | hook (`SessionStart`) | Warns via `systemMessage` about new high-risk rules; silent when settings are unchanged. Also does the one-time 2.x log archive. |
| `/permissions-review` | skill | Runs `perm-scan`, explains findings, proposes `perm-apply` changes for your approval. |
| `/permissions-advisor` | skill | Read-only pre-flight check: how would these commands fare under your effective rules. |
| `perm-scan` | command on PATH | The analysis engine: settings checks across scopes, transcript friction, `check <cmd>…` advisor mode. JSON output is capped at 8 KB. |
| `perm-apply` | command on PATH | The only settings writer: backup, atomic write, read-back, `--dry-run`. |

Scopes read: managed, user, project, and project-local, with Claude Code's [precedence](https://code.claude.com/docs/en/settings#settings-precedence). Finding ids and severities live in `lib/checks.mjs`. Sandbox checks run only when `sandbox.enabled` is true.

## Install

In `~/.claude/settings.json`:

```json
{
  "enabledPlugins": {
    "permissions@claude-toolbelt": true
  }
}
```

No install step, no API key, no build. Node is the only runtime requirement.

## Usage

```
/permissions-review
```

Scans, presents findings by severity, then proposes `perm-apply --dry-run` commands. Nothing is written until you approve. Settings writes will likely trigger the auto-mode classifier or a prompt; that is expected.

Directly from a shell or the Bash tool:

```
perm-scan --settings-only        # fast, no transcripts
perm-scan --days 30 --json
perm-scan --all-projects ~/code  # tidy view across projects
perm-scan check "git push origin main"
```

`/permissions-advisor` is also called by `agent-loop-setup` as a pre-dispatch gate.

### Upgrading from 2.x

3.0.0 removes the three logging hooks and six skills (`permissions-audit`, `-promote`, `-lint`, `-seed`, `-bootstrap-project`, `sandbox-fix`).

- **Schema marker.** `${CLAUDE_PLUGIN_DATA}/schema` containing `3`. If it is absent, the first 3.x session runs the migration below, then writes it. A second run is silent.
- **Logs.** On that first session the hook moves `permission-log.jsonl`, `prompt-log.jsonl` and `sandbox-denials.jsonl` (and rotations) from `~/.claude/` into `${CLAUDE_PLUGIN_DATA}/legacy-v2/`, records them in `MOVED.json`, and says so in one message. The archive is deleted on the first session 30 or more days later. A file that cannot be moved is left in place; `perm-scan` reports it as L1.
- **Settings are never auto-edited.** Run `/permissions-review` to find leftovers from 2.x (L2: `permissions.sandbox.*`, `Skill(permissions-…)` rules for removed skills, allow rules pointing into an old plugin cache path) and orphaned data directories (L3).
- **Old cache dirs.** Versioned directories under `~/.claude/plugins/cache/` belong to Claude Code. The plugin never touches them.
- **Downgrading** to 2.x recreates fresh logs; the archive and marker are untouched, so re-upgrading reports L1 rather than clobbering.

## Tests

```bash
bash plugins/permissions/tests/all.sh
```

## Related

- [Permission modes](https://code.claude.com/docs/en/permission-modes) and [permissions](https://code.claude.com/docs/en/permissions) — rule syntax and evaluation order.
- Built-ins: `/permissions` (rules and recent denials), `/fewer-permission-prompts`.
- [`agent-loop`](../agent-loop/README.md) — uses `/permissions-advisor` in setup.
