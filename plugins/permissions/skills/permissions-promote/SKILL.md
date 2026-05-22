---
name: permissions-promote
description: This skill should be used when the user asks to "promote permissions", "promote rules", "add allow rules from log", "reduce auto mode cost", "stop hitting the classifier for X", "save common commands", "update settings.json with frequent patterns", or says "/permissions-promote". Finds frequent tool calls in ~/.claude/permission-log.jsonl that are not yet covered by permissions.allow, derives narrow rules, and — after user approval — writes them to settings.json so they skip Auto mode's classifier entirely.
---

# Promote Frequent Patterns to Allow Rules

Every tool call Claude Code makes either matches a rule in `permissions.allow` / `deny` (instant, free) or goes to Auto mode's classifier (tokens + latency per invocation). The plugin's PreToolUse hook records every call to `~/.claude/permission-log.jsonl`. This skill mines that log for repeat patterns and promotes them into `permissions.allow` so they stop paying classifier cost.

The target file for writes is a settings file — not the old `rules/default.yaml`. Depending on scope, one of:

- `~/.claude/settings.json` — global, applies everywhere (default target for cross-project tools like `git`, `jq`)
- `<project>/.claude/settings.json` — committed project rules (for project-specific tools like `rake`, `terraform`)
- `<project>/.claude/settings.local.json` — gitignored project overrides (for personal paths, secrets)

## Workflow

### 0. Pre-flight: dedup existing allow list

Before deriving new promotions, scan the target settings file's existing `permissions.allow` for duplicates and subsumed rules. Use `${CLAUDE_PLUGIN_ROOT}/lib/rule-matcher.mjs` matcher semantics:

- **Duplicate**: same rule string appears twice (string-equality after trim).
- **Subsumed**: rule X is fully covered by another rule Y in the same bucket — that is, every entry in `~/.claude/permission-log.jsonl` that matches X also matches Y, AND Y matches at least one additional log entry that X does not. Treat MCP rule wildcards (`mcp__server__*`) as superset-of more specific MCP tool-name rules.

If duplicates or subsumptions are found, present them with the proposed removal and offer:

```
Pre-flight: 3 redundant rules in ~/.claude/settings.json
  1. Bash(git status:*)      → covered by Bash(git:*)           (drop)
  2. mcp__plugin_buildkite_buildkite__list_builds → covered by mcp__plugin_buildkite_buildkite-* (drop)
  3. Bash(npm:*)              ← duplicate (drop second occurrence)

Clean these before promoting new rules? (yes / skip / open /permissions-lint)
```

If the user says `open /permissions-lint`, exit and tell them to invoke `/permissions-lint` directly — do not perform the deeper lint (subsumption against all buckets, conflicts, zero-match) in this pre-flight; this pre-flight only covers the duplicate + same-bucket subsumption cases relevant to "don't promote a duplicate".

If the user says `yes`, apply the drops using the same settings.json safety contract as step 7 of this skill (parse-or-stop, mutate only `permissions.allow`, preserve key order, diff preview, re-read before write). If `skip`, continue to step 1 unchanged.

### 1. Read the log

Load `~/.claude/permission-log.jsonl`. If missing or empty, report: "No log data yet. The PreToolUse hook hasn't captured any calls — confirm the plugin is enabled and run a few commands first."

### 2. Read current rules

Read `~/.claude/settings.json` and, if the user is working inside a project, `<cwd>/.claude/settings.json` and `<cwd>/.claude/settings.local.json`. Collect the merged `permissions.allow` and `permissions.deny`.

### 3. Filter to candidates

For each log entry, skip it if:

- The tool is `Read`, `Grep`, `Glob`, `WebFetch`, `WebSearch`, `Write`, or `Edit` with a path inside `cwd` — Auto mode handles these free already.
- The entry matches any existing `permissions.allow` rule (use `${CLAUDE_PLUGIN_ROOT}/references/rule-syntax.md` for matching semantics).
- The entry matches any `permissions.deny` rule — these should stay denied; do not suggest overriding a deny.

What remains is the classifier-hitting set. Aggregate it:

- For `Bash`: group by command prefix. Strip leading env-var assignments (`FOO=bar cmd args` → `cmd args`). Start with the two-word prefix (`npm install`, `git push`, `docker compose`, `pnpm test`) and only broaden to one word if three-plus distinct two-word prefixes share the same first word.
- For MCP tools: group by exact tool name; suggest the tool-name rule first and offer to widen to `mcp__server__*` if the user requests it.
- For other tools: group by tool name.

### 4. Rank and classify

Rank candidates by call count (highest first). For each, look up its risk tier in `${CLAUDE_PLUGIN_ROOT}/references/security-considerations.md`:

- **LOW**: safe to promote to `allow`.
- **MEDIUM**: safe to promote, but mention the tradeoff in the presentation.
- **HIGH**: suggest as `ask` rather than `allow`, or recommend a narrower pattern.
- **CRITICAL**: do not suggest. If they appear in the log they indicate a prior manual approval — flag as a concern.

Exclude candidates with fewer than 3 calls by default, unless the user asks for the full list. (Three is the rough floor where a pattern is stable rather than one-off.)

### 5. Pick the target file

Default to `~/.claude/settings.json` (global) for tools that appear across multiple `cwd` values in the log, and to `<cwd>/.claude/settings.json` for tools that only appear inside one project. Mention the default target per candidate; let the user override before writing.

### 6. Present candidates

One scannable block. Example:

```
Promotion Candidates
════════════════════
(filtered to calls not yet covered by permissions.allow, grouped by pattern)

1. Bash(pnpm:*)                 [LOW]   72 calls across 14 sessions
   Examples: pnpm install · pnpm test · pnpm run build
   Target: ~/.claude/settings.json (seen in 6 different projects)

2. Bash(gh pr:*)                [MEDIUM] 34 calls
   Examples: gh pr view · gh pr list · gh pr diff
   Target: ~/.claude/settings.json

3. Bash(docker compose:*)       [HIGH]  21 calls
   Examples: docker compose up -d · docker compose logs
   Recommend: ask rule rather than allow (docker can mount sensitive paths)
   Target: ~/.claude/settings.json

4. mcp__plugin_bugsnag_bugsnag__list_errors [LOW] 14 calls
   Target: ~/.claude/settings.json

Approve which? (e.g. "1,2,4", "all low", "skip")
```

Support bulk selections: "all", "all low", "1,2,5", "first 3", "skip". Do not loop one-by-one.

### 7. Apply approved promotions

For each approval:

1. Read the target settings file (create it if missing, with `{ "permissions": { "allow": [] } }` skeleton).
2. Ensure `permissions.allow` (or `permissions.ask` for HIGH-risk promotions) exists as an array.
3. Append the new rule. Do not remove or reorder existing rules.
4. Write the file back with 2-space indentation, preserving any other keys (hooks, env, enabledPlugins, etc.).
5. Confirm by reading it back.

JSON files have no comment syntax, so do not attempt to annotate the rule inline. Instead, maintain the running history in git (if the target is project-level and under git) or accept that provenance lives in the log.

### 8. Optional: trim the log

After successful promotion the old log entries are now redundant (they will be matched by an allow rule next time). Do not trim automatically. If the log is over 50 MB, offer to archive entries older than the most recent promotion window.

### 9. Summary

```
Promoted 3 rules:
  ✓ Bash(pnpm:*)          → ~/.claude/settings.json :: permissions.allow
  ✓ Bash(gh pr:*)         → ~/.claude/settings.json :: permissions.allow
  ✓ Bash(docker compose:*) → ~/.claude/settings.json :: permissions.ask (HIGH risk, demoted from allow)

Skipped 1:
  ○ mcp__plugin_bugsnag_bugsnag__list_errors — user declined

Effect: the 127 matching calls in the log would have skipped the Auto mode
classifier. Run /permissions-audit in a week to confirm the savings.
```

## Edge cases

- **Conflicts**: if a candidate rule would be subsumed by an existing broader rule (e.g. promoting `Bash(git status:*)` when `Bash(git:*)` is already present), skip it silently.
- **Overlap with deny**: if a candidate would match an existing deny rule, never promote. Flag it so the user can audit why the call is happening despite the deny.
- **Settings file is malformed**: if `JSON.parse` throws, stop and tell the user. Do not attempt to repair JSON.
- **Path-scoped Read/Write/Edit**: when suggesting path-scoped rules, prefer paths that end in `/**` to cover subdirectories rather than a single literal path, and scope to the project root rather than a child directory.

## Reference material

- `${CLAUDE_PLUGIN_ROOT}/references/rule-syntax.md` — pattern matching semantics for allow/deny.
- `${CLAUDE_PLUGIN_ROOT}/references/security-considerations.md` — risk tiers and principle-of-least-privilege guidance.
- `${CLAUDE_PLUGIN_ROOT}/references/recipes.md` — pre-built rule sets for common stacks (Node, Python, Docker, Git) to seed a fresh settings.json.
