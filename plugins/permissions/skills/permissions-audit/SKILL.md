---
name: permissions-audit
description: This skill should be used when the user asks to "audit permissions", "check permission log", "analyze tool usage", "permission report", "what commands am I running", "what's hitting auto mode", or says "/permissions-audit". Reads ~/.claude/permission-log.jsonl alongside current permissions.allow rules to surface the tool-call patterns that are still hitting the Auto mode classifier (i.e. not yet short-circuited by an allow rule).
---

# Permission Log Audit

Analyse `~/.claude/permission-log.jsonl` — a complete record of every tool call Claude Code has made on this machine — and cross-reference it against the user's current `permissions.allow` rules to show what's still paying for Auto mode's classifier on each invocation.

Auto mode's decision order (from the Claude Code docs):

1. Tool calls matching `permissions.allow` / `permissions.deny` resolve immediately (no classifier).
2. Read-only actions and in-repo edits are auto-approved (no classifier).
3. Everything else is sent to the classifier — this costs tokens and latency.

The goal of `/permissions-audit` is to spot step-3 patterns that could be moved to step 1 by adding narrow allow rules.

## Inputs

| File | What it is |
|------|------------|
| `~/.claude/permission-log.jsonl` | Tool call log, one JSON object per line, appended by the PreToolUse hook in this plugin. |
| `~/.claude/settings.json` | Global user settings, including `permissions.allow`, `permissions.deny`, `permissions.ask`. |
| `<cwd>/.claude/settings.json` | Project settings, if present. Merged on top of global. |
| `<cwd>/.claude/settings.local.json` | Gitignored local overrides, if present. |
| `~/.claude/prompt-log.jsonl` | Optional: UserPromptSubmit excerpts for the per-prompt analysis (see step 5). Missing is fine. |

If the log file does not exist, report: "No tool call log found yet — the plugin's PreToolUse hook may not have fired. Confirm the plugin is enabled in settings.json and run a few commands."

## Workflow

### 1. Load the log

Each line looks like:

```jsonl
{"timestamp":"2026-04-20T13:02:11.412Z","session_id":"abc","tool":"Bash","detail":"npm test","cwd":"/Users/me/proj","sandbox_disabled":false}
```

Read the whole file. If it is larger than ~10 MB, only process the last 30 days by timestamp.

### 2. Load current allow rules

Read `~/.claude/settings.json` and any project-level settings in scope for the cwds seen in the log. Collect the merged `permissions.allow` and `permissions.deny` rule strings. Use `${CLAUDE_PLUGIN_ROOT}/references/rule-syntax.md` to understand the syntax if needed.

### 3. Classify each log entry

For every log line, decide which bucket it lands in:

- **`covered_by_allow`** — at least one rule in `permissions.allow` would match. For `Bash`, match by prefix (respecting `:*` and bare-command rules as documented in `references/rule-syntax.md`). For `Read`/`Write`/`Edit`, match by file path against the path patterns. For MCP tools, match by tool name with wildcard support.
- **`covered_by_deny`** — matches a `permissions.deny` rule. Flag these; they should not be appearing in the log at all unless the deny rule was added after the fact.
- **`read_only_tools`** — the tool is `Read`, `Grep`, `Glob`, `WebFetch`, `WebSearch` and the log entry represents a call Auto mode would auto-approve without classifier cost.
- **`in_repo_edit`** — `Write` or `Edit` whose `detail` path is inside the session's `cwd`. Auto mode auto-approves these without classifier cost.
- **`classifier_hit`** — everything else. These are the tool calls that are paying classifier tokens on every run.

Record counts per bucket. Within `classifier_hit`, also aggregate by tool name and (for Bash) by command prefix.

### 4. Present summary

Keep the output scannable. Example shape:

```
Permission Log Audit
════════════════════

Log: 1,284 tool calls across 41 sessions (2026-03-22 → 2026-04-20)
Current allow rules: 18 (from ~/.claude/settings.json)

Bucket breakdown:
  Covered by allow rule        812  (63%)
  Read-only / in-repo edit     287  (22%)
  Hitting Auto mode classifier 183  (14%)  ← these cost tokens on every run
  Covered by deny rule (!)       2  (0.2%) ← investigate

Top classifier hits (by frequency):
  1. Bash: pnpm install                  37 calls
  2. Bash: docker compose up -d          21 calls
  3. Bash: gh pr view                    18 calls
  4. mcp__plugin_bugsnag_bugsnag__list_errors   14 calls
  5. Bash: kubectl get pods              11 calls
  ...

Run /permissions-promote to turn the top entries into allow rules.
```

### 5. Deny-rule effectiveness check

Surface deny rules that the log shows tool calls slipping past — either because the rule post-dates the matches or because an ask-rule has been silently auto-collapsed.

Iterate through every `permissions.deny` rule (and every `permissions.ask` rule when the user has `skipAutoPermissionPrompt: true` in settings — those rules functionally behave like allow in that case and should be included in this scan). For each, scan the permission log for entries the rule would match. Use the matcher semantics described in `${CLAUDE_PLUGIN_ROOT}/references/rule-syntax.md` (implemented by `${CLAUDE_PLUGIN_ROOT}/lib/rule-matcher.mjs`) — do not restate them here.

Group log matches by deny-rule. For each rule with matches, report the count, the timestamp range, a sample `detail`, and a reason hypothesis. Example shape:

```
Deny rules with bypass events:
  Bash(eval:*)            2 matches (2026-05-12 → 2026-05-20)
    Sample: eval "$(apdev env)" && docker compose exec -T mysql8.0 …
    Reason check: rule added after 2026-05-20? confirm with user.
  Bash(rm -rf *) [ask]   37 matches
    Sample: rm -rf .lostpixel/current .lostpixel/difference
    Reason: ask rule + skipAutoPermissionPrompt:true → silent allow.
```

Flag two failure modes:

1. **Matcher syntax suspicion** — if a deny rule has documented matches in the log AND the matcher is supposed to catch them per `references/rule-syntax.md`, the rule may have been added after the matching calls were logged. Surface the timestamp range of the matches and ask the user whether the rule was recently introduced. Genuine matcher bugs should be reported to the Claude Code team rather than worked around in this skill.
2. **Ask-rule auto-collapse** — when `skipAutoPermissionPrompt: true` is in settings and the matching rule is in `permissions.ask`, the prompt is suppressed and the call runs. Quote the exact counts (e.g. 37 `rm -rf` calls under the audit) so the user can see the silent-allow exposure.

### 6. Optional deeper analyses

Offer these when the user asks for more detail or "deep audit":

- **Sandbox-disabled calls**: list every entry with `sandbox_disabled: true`. These bypass filesystem/network isolation and warrant review.
- **Unmatched deny rules**: deny rules that never appeared in the log (not necessarily dead — may be preventive).
- **Dead allow rules**: allow rules that never matched any log entry (candidates for removal to keep the config tidy).
- **Per-cwd breakdown**: for users who want to know which project is driving classifier calls, group `classifier_hit` by `cwd`.
- **Trend**: daily counts of `classifier_hit` over the log's date range. A downward trend after `/permissions-promote` runs validates the workflow.
- **Per-prompt classifier load**: when `~/.claude/prompt-log.jsonl` exists (written by this plugin's UserPromptSubmit hook), for every session with more than 10 classifier-hit entries, join the session's `classifier_hit` bucket with its `prompt_excerpt` from the prompt log (match on `session_id`, take the most recent prompt prior to the tool call timestamp). Surface the top 5 prompts whose downstream tool bursts drive the largest classifier load — answers "which user requests are paying for the classifier?". If the prompt log is missing, skip this section silently.

## Reference material

- `${CLAUDE_PLUGIN_ROOT}/references/rule-syntax.md` — how allow/deny patterns match.
- `${CLAUDE_PLUGIN_ROOT}/references/security-considerations.md` — risk tiers for deciding which patterns are safe to promote.
- `${CLAUDE_PLUGIN_ROOT}/references/recipes.md` — pre-built allow/deny sets for common stacks.

## Interaction with Auto mode

If the user is not using Auto mode, the "classifier hit" bucket is named misleadingly — the relevant friction is permission prompts instead. Classification is the same (still "not covered by any allow rule"); only the framing of the cost changes. Mention this once if you notice the user is in `default` mode rather than `auto`.
