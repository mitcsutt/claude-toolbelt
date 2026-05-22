# Permissions Plugin

A thin observability layer that complements Claude Code's built-in [Auto mode](https://code.claude.com/docs/en/permission-modes#eliminate-prompts-with-auto-mode). Logs every tool call, then gives you three skills for seeding, auditing, and growing a settings.json allow list that skips Auto mode's classifier (and its per-call token cost).

## Why

Auto mode already does AI-based permission evaluation, prompt-injection defence, subagent safety checks, and block-based fallback. What it does not do:

- Keep a persistent, machine-readable record of every tool call on your machine.
- Tell you which patterns are hitting the classifier every time (costing tokens) instead of being short-circuited by an allow rule.

This plugin fills that gap. It is intentionally small — three hooks, six skills, some reference docs.

## What it does

### Hooks

1. **`PreToolUse` hook** (`hooks/log.mjs`) — appends every tool invocation to `~/.claude/permission-log.jsonl` as one JSON object per line. `detail` is sanitized before write (heredoc bodies stripped, multi-line scripts collapsed, capped at 200 chars). When sanitization changes the value, a 12-char `detail_sha` of the raw detail is recorded so elided entries with the same underlying command can still be correlated. Non-blocking, best-effort. Requires Node.
2. **`PostToolUse` hook** (`hooks/sandbox-watch.mjs`) — on `Bash` tool calls only, scans `stderr` + `stdout` for sandbox-denial signatures cataloged in `lib/signatures.mjs`, classifies them by category (`fs-perm` / `ssh` / `macos-posix`), and appends one line per detected denial to `~/.claude/sandbox-denials.jsonl`. Cosmetic suppressors are filtered out.
3. **`UserPromptSubmit` hook** (`hooks/prompt-log.mjs`) — records a 200-char excerpt of each user prompt to `~/.claude/prompt-log.jsonl`. Used by `/permissions-audit` to correlate tool bursts with the prompt that triggered them.

### Skills

4. **`/permissions-seed`** — one-shot: merges curated rule sets (essential safety denies, Git, Node, Python, Docker, etc.) from `references/recipes.md` into `settings.json`'s `permissions.allow` / `deny` / `ask`. Never removes existing rules. Use to get a sensible baseline.
5. **`/permissions-audit`** — reads the log plus your current `permissions.allow` rules, cross-references them, and shows which patterns are still going through Auto mode's classifier on every run. Surfaces deny-rule effectiveness (rules with log matches that ran anyway) and, when `prompt-log.jsonl` is available, per-prompt classifier load.
6. **`/permissions-promote`** — picks frequent classifier-hitting patterns out of the log and offers to write narrow allow rules into `~/.claude/settings.json` (or a project-level settings file), so those calls stop paying classifier cost. Runs a pre-flight dedup pass over the existing allow list first.
7. **`/sandbox-fix`** — reads `sandbox-denials.jsonl`, groups by signature + `matched_path`, and recommends targeted fixes: add the path to `permissions.sandbox.filesystem.allowWrite`, add the command head to `excludedCommands`, or pre-set `dangerouslyDisableSandbox` for the call site.
8. **`/permissions-lint`** — scans `settings.json` for matcher-syntax pitfalls: too-narrow `Bash(cmd)` rules that never match because tool calls carry arguments, allow rules subsumed by broader rules, allow/deny conflicts, and rules with zero matches in the log.
9. **`/permissions-bootstrap-project`** — filters the log to entries from the current project root and proposes project-local rules (committed in `.claude/settings.json` or gitignored in `.claude/settings.local.json`).

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
- `detail` — for `Bash`, the command string; for `Read`/`Write`/`Edit`, the file path; for other tools, the first few input keys. Sanitized before write: heredoc bodies replaced with `<<'TAG' …`, multi-line scripts collapsed to the first line plus `…(+N lines)`, and the result capped at 200 chars with a trailing `…`.
- `detail_sha` — optional 12-char SHA-1 hex prefix of the raw, unsanitized detail. Present only when sanitization changed the value. Lets you correlate elided log entries that share the same raw command — for example, recognising that two truncated `gh pr create …` lines refer to the same body without storing the body.
- `cwd` — working directory at the time of the call
- `sandbox_disabled` — only present for `Bash`; `true` when the call used `dangerouslyDisableSandbox`

To see the full unsanitized command, open the session's transcript under `~/.claude/projects/<cwd-flattened>/<session_id>.jsonl`.

`~/.claude/sandbox-denials.jsonl`, one JSON object per detected denial:

```jsonl
{"timestamp":"2026-05-22T13:02:11.412Z","session_id":"abc123","cwd":"/path","command":"git worktree add ...","sandbox_disabled":false,"signature":"fs-eperm-claude-dir","category":"fs-perm","fix":"claude-sandbox-allowwrite","matched_path":"/path/.claude/worktrees/x","command_head":"git"}
```

- `signature` — signature id from `lib/signatures.mjs`
- `category` — `fs-perm` / `ssh` / `macos-posix`
- `fix` — recommended fix class: `claude-sandbox-allowwrite` / `dangerouslyDisableSandbox`
- `matched_path` — path extracted from the denial string, when present
- `command_head` — first word of the command after env-var stripping (e.g. `git`, `node`)

`~/.claude/prompt-log.jsonl`, one JSON object per user prompt:

```jsonl
{"timestamp":"2026-05-22T13:02:11.412Z","session_id":"abc123","cwd":"/path","prompt_excerpt":"first 200 chars of the prompt","prompt_len":1024}
```

- `prompt_excerpt` — first 200 chars of the submitted prompt
- `prompt_len` — full character length of the original prompt

## Log files

Three JSONL files live under `~/.claude/`. Each is append-only, written by the hooks in this plugin.

| File | Written by | Purpose |
|------|-----------|---------|
| `permission-log.jsonl` | `hooks/log.mjs` (PreToolUse) | One line per tool call. Source for `/permissions-audit`, `/permissions-promote`, `/permissions-lint`, `/permissions-bootstrap-project`. |
| `sandbox-denials.jsonl` | `hooks/sandbox-watch.mjs` (PostToolUse, Bash only) | One line per detected sandbox denial. Source for `/sandbox-fix`. Cosmetic suppressors are filtered out. |
| `prompt-log.jsonl` | `hooks/prompt-log.mjs` (UserPromptSubmit) | One line per user prompt (200-char excerpt + length). Optional input for `/permissions-audit`'s per-prompt analysis. |

None of these files are rotated automatically. If they grow large, archive or delete; the hooks recreate them on next write.

## References

- [`references/rule-syntax.md`](references/rule-syntax.md) — allow/deny pattern matching semantics.
- [`references/security-considerations.md`](references/security-considerations.md) — risk tiers for deciding what is safe to auto-approve.
- [`references/recipes.md`](references/recipes.md) — pre-built rule sets for common stacks.
- [`references/sandbox-signatures.md`](references/sandbox-signatures.md) — catalog of detected sandbox-denial signatures.
- [`references/matcher-syntax-pitfalls.md`](references/matcher-syntax-pitfalls.md) — common rule shapes the lint catches.

## Relationship to Auto mode

This plugin is additive, not a replacement. Auto mode decides whether to run a call; this plugin records what ran so you can tune the rules that Auto mode consults first. They compose:

```
Auto mode decision order:
  1. permissions.allow / permissions.deny  ← /permissions-promote writes here
  2. read-only + in-repo-edit fast path
  3. classifier                            ← tokens + latency
```

Every rule `/permissions-promote` adds moves one pattern from step 3 to step 1.
