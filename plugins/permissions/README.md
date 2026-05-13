# Permissions Plugin

A thin observability layer that complements Claude Code's built-in [Auto mode](https://code.claude.com/docs/en/permission-modes#eliminate-prompts-with-auto-mode). Logs every tool call, then gives you three skills for seeding, auditing, and growing a settings.json allow list that skips Auto mode's classifier (and its per-call token cost).

## Why

Auto mode already does AI-based permission evaluation, prompt-injection defence, subagent safety checks, and block-based fallback. What it does not do:

- Keep a persistent, machine-readable record of every tool call on your machine.
- Tell you which patterns are hitting the classifier every time (costing tokens) instead of being short-circuited by an allow rule.

This plugin fills that gap. It is intentionally small — one PreToolUse hook, three skills, some reference docs.

## What it does

1. **`PreToolUse` hook** (`hooks/log.mjs`) — appends every tool invocation to `~/.claude/permission-log.jsonl` as one JSON object per line. Non-blocking, best-effort. Requires Node (same runtime Claude Code itself already uses).
2. **`/permissions-seed` skill** — one-shot: merges curated rule sets (essential safety denies, Git, Node, Python, Docker, etc.) from `references/recipes.md` into `settings.json`'s `permissions.allow` / `deny` / `ask`. Never removes existing rules. Use to get a sensible baseline.
3. **`/permissions-audit` skill** — reads the log plus your current `permissions.allow` rules, cross-references them, and shows which patterns are still going through Auto mode's classifier on every run.
4. **`/permissions-promote` skill** — picks frequent classifier-hitting patterns out of the log and offers to write narrow allow rules into `~/.claude/settings.json` (or a project-level settings file), so those calls stop paying classifier cost.

That is the whole plugin.

## Install

In `~/.claude/settings.json`:

```json
{
  "enabledPlugins": {
    "permissions@claude-toolbelt": true
  }
}
```

No install step, no API key, no build. Node is the only runtime requirement and you already have it.

## Usage

**First time:**

```
/permissions-seed
```

Walks through the curated recipes in `references/recipes.md` — essential safety denies, Git, Node, Python, Docker, MCP wildcards, etc. — and merges the ones you pick into `~/.claude/settings.json`. Safe to re-run any time you pick up a new stack.

**After a few sessions:**

```
/permissions-audit
```

Shows a breakdown: calls covered by allow rules, calls auto-approved by Auto mode's path-based fast path, and calls hitting the classifier every time.

```
/permissions-promote
```

Turns the top classifier hitters into narrow allow rules in `settings.json` after you approve each one. Run it again whenever `/permissions-audit` shows a growing classifier bucket.

## Log format

`~/.claude/permission-log.jsonl`, one JSON object per line:

```jsonl
{"timestamp":"2026-04-20T13:02:11.412Z","session_id":"abc123","tool":"Bash","detail":"npm test","cwd":"/Users/me/proj","sandbox_disabled":false}
```

- `timestamp` — ISO 8601 UTC
- `session_id` — Claude Code session id, for grouping
- `tool` — tool name as reported by Claude Code (`Bash`, `Read`, `mcp__...`, etc.)
- `detail` — for `Bash`, the command string; for `Read`/`Write`/`Edit`, the file path; for other tools, the first few input keys
- `cwd` — working directory at the time of the call
- `sandbox_disabled` — only present for `Bash`; `true` when the call used `dangerouslyDisableSandbox`

## References

- [`references/rule-syntax.md`](references/rule-syntax.md) — allow/deny pattern matching semantics.
- [`references/security-considerations.md`](references/security-considerations.md) — risk tiers for deciding what is safe to auto-approve.
- [`references/recipes.md`](references/recipes.md) — pre-built rule sets for common stacks.

## Relationship to Auto mode

This plugin is additive, not a replacement. Auto mode decides whether to run a call; this plugin records what ran so you can tune the rules that Auto mode consults first. They compose:

```
Auto mode decision order:
  1. permissions.allow / permissions.deny  ← /permissions-promote writes here
  2. read-only + in-repo-edit fast path
  3. classifier                            ← tokens + latency
```

Every rule `/permissions-promote` adds moves one pattern from step 3 to step 1.
