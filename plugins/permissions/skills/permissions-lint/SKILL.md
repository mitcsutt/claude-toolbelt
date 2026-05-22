---
name: permissions-lint
description: This skill should be used when the user asks to "lint permissions", "audit rule syntax", "find dead allow rules", "dedup permissions", "check for permission conflicts", or says "/permissions-lint". Reads ~/.claude/settings.json plus any project-level settings, validates each rule against rule-matcher semantics, cross-references with ~/.claude/permission-log.jsonl, and reports matcher syntax bugs, subsumed rules, allow/deny conflicts, and zero-match rules.
---

# Permissions Lint

Static and log-driven analysis of the user's permission rules. Reads the merged set of `permissions.allow` / `permissions.deny` / `permissions.ask` rules from global and project settings, checks each rule against the four pitfalls catalogued in `${CLAUDE_PLUGIN_ROOT}/references/matcher-syntax-pitfalls.md`, and presents findings with per-rule actions (keep / remove / rewrite).

The matcher itself is correct — `lib/rule-matcher.mjs` faithfully implements the semantics in `references/rule-syntax.md`. The pitfalls this skill detects are configuration mistakes (rules that pass the matcher's contract but defeat the user's intent) plus stale or conflicting entries that accrue over months of editing.

`/permissions-lint` is the cleanup counterpart to `/permissions-promote`: promote adds rules, lint prunes them.

## Inputs

| File | Purpose |
|------|---------|
| `~/.claude/settings.json` | Global rules. Primary lint target. |
| `<cwd>/.claude/settings.json` | Project rules (committed). Merged with global. |
| `<cwd>/.claude/settings.local.json` | Project overrides (gitignored). Merged on top. |
| `~/.claude/permission-log.jsonl` | Log of every tool call. Used to compute match sets for each rule. |
| `${CLAUDE_PLUGIN_ROOT}/references/matcher-syntax-pitfalls.md` | Pitfalls catalogue and detection recipes. Canonical. |
| `${CLAUDE_PLUGIN_ROOT}/references/rule-syntax.md` | Matcher semantics. |
| `${CLAUDE_PLUGIN_ROOT}/lib/rule-matcher.mjs` | `matchRule(rule, entry)` implementation. Use this for all rule-vs-entry checks. |

If `~/.claude/settings.json` is missing or its `permissions.allow` / `permissions.deny` / `permissions.ask` arrays are empty (and no project-level settings are present), report: "No rules to lint." Stop. Do not invent rules to lint.

If `~/.claude/permission-log.jsonl` is missing, the syntax-narrowing check (pitfall 1) and the log-based subsumption / conflict / zero-match checks (pitfalls 2–4) cannot run. Report the missing log explicitly and offer to run pitfall 1's static check (`Bash(cmd)` literal detection without log corroboration is still partly possible, but with much lower confidence). If the user wants a full lint, prompt them to enable the PreToolUse hook and rerun after a week of log data.

## Workflow

### 1. Load merged settings

Read each of the three settings files. For each, run `JSON.parse`. If parsing fails on any file, stop and report which file and the parse error verbatim. Do not attempt repair.

Build a merged view per bucket:

- `allow`: concatenation of global + project + local, with origin tagged per rule (so the fix step knows which file to edit).
- `deny`: same.
- `ask`: same.

Tag each rule with `{ rule, bucket, source_file }` so later steps can route writes correctly. Two identical rule strings in two files are not redundant — they are distinct entries with distinct edit targets; keep both.

### 2. Load and window the log

Read `~/.claude/permission-log.jsonl`. If the file is larger than ~10 MB, restrict processing to entries within the last 30 days by `timestamp`. Otherwise read all entries. Record the actual time window covered (`window_start`, `window_end`) so the report can annotate it.

Parse each line with `JSON.parse`. Skip malformed lines silently, count them, and surface the count in the summary.

### 3. Compute per-rule match sets

For every rule from step 1, compute `S_rule` — the set of log entries the rule matches. Use `matchRule(rule, entry)` from `${CLAUDE_PLUGIN_ROOT}/lib/rule-matcher.mjs`. Do not reimplement matching here; the contract in that module is the single source of truth.

Cache `S_rule` keyed by rule string so the next steps can intersect / compare without re-scanning the log.

### 4. Detect pitfalls

Apply each pitfall in `${CLAUDE_PLUGIN_ROOT}/references/matcher-syntax-pitfalls.md`. The reference file owns the detection recipes; do not duplicate them in the skill. In order:

1. **Pitfall 1 — `Bash(cmd)` too narrow.** For each `allow` rule of the form `Bash(X)` with no `*` and no `:` in `X`, count log entries whose `tool` is `Bash` and whose stripped-env first token equals `X` but `detail !== X`. Non-zero count → flag.
2. **Pitfall 2 — Subsumed rules.** For each rule X, check whether some other rule Y in the same bucket has `S_X ⊆ S_Y` (computed over the windowed log). Non-empty `S_X` and Y exists → flag X as subsumed by Y.
3. **Pitfall 3 — Allow/deny conflict.** For every (A, D) with A in `allow` and D in `deny`, check `S_A ∩ S_D`. Non-empty → flag.
4. **Pitfall 4 — Zero-match rules.** For each rule, if `S_rule` is empty over the window, flag with a confidence note: high confidence for `allow`, low confidence for `deny` and `ask` (preventive intent).

Skip the zero-match check entirely if the log window is shorter than 7 days — annotate the report instead.

### 5. Present findings

One scannable block, ordered by pitfall severity (conflicts first, then subsumed, then narrow, then zero-match). Per-rule actions trail each finding. Example shape:

```
Permissions Lint
════════════════

Loaded 47 rules from 2 settings files. Log window: 2026-04-22 → 2026-05-22 (30d, 1,840 calls).

Pitfall 3 — allow/deny conflict (1)
  Bash(git push:*)              [allow, ~/.claude/settings.json]
  Bash(git push --force*)       [deny,  ~/.claude/settings.json]
  Overlap: 4 calls match both. Sample: git push --force-with-lease origin HEAD
  Action: (k)eep both / (r)emove allow / (e)dit allow to exclude --force

Pitfall 2 — subsumed rules (2)
  Bash(git status:*)            subsumed by Bash(git:*)
    [allow, ~/.claude/settings.json]
    Action: (k)eep / (r)emove
  Bash(npm install:*)           subsumed by Bash(npm:*)
    [allow, /Users/me/proj/.claude/settings.json]
    Action: (k)eep / (r)emove

Pitfall 1 — Bash(cmd) too narrow (1)
  Bash(jq)                      [allow, ~/.claude/settings.json]
  Log shows 23 calls of "jq ..." with args bypassing this rule.
  Suggested rewrite: Bash(jq:*)
  Action: (k)eep / (w)rite-rewrite / (r)emove

Pitfall 4 — zero-match rules (3)
  Bash(terraform:*)             [allow, ~/.claude/settings.json] — no matches in 30d
    Action: (k)eep / (r)emove
  Bash(rm -rf *)                [deny,  ~/.claude/settings.json] — no matches in 30d (likely preventive)
    Action: (k)eep
  Bash(sudo:*)                  [deny,  ~/.claude/settings.json] — no matches in 30d (likely preventive)
    Action: (k)eep

Apply actions? (e.g. "remove 1,3,5", "all subsumed remove", "skip")
```

Support bulk selections: "all subsumed remove", "all narrow rewrite", "remove 1,3,5", "skip". Do not loop per finding.

### 6. Apply approved edits

For each approved finding, edit the appropriate settings file (from the rule's `source_file` tag). Mirror the safety contract from `sandbox-fix` exactly:

1. **Parse-or-stop.** Read the target file. If `JSON.parse` throws, stop and report the parse error verbatim. Do not attempt repair. Do not write.
2. **Mutate `permissions.{allow,deny,ask}` only.** Every other top-level key (`hooks`, `env`, `enabledPlugins`, `permissions.sandbox`, anything else) is preserved untouched. Do not reorder. Round-trip the user's existing object and mutate in place — do not reconstruct the file from a model of "what settings.json should look like".
3. **Per-action semantics:**
   - `remove` → delete the rule string from its array. Match by exact string equality. If multiple identical strings exist in the same array (legitimate duplicate), remove only the first.
   - `write-rewrite` → replace the rule string with the suggested rewrite (e.g. `Bash(jq)` → `Bash(jq:*)`) at the same array index.
   - `edit allow to exclude --force` → for the allow/deny conflict case, do not silently rewrite. Surface a suggested narrowed rule, show the diff, and require the user to type-confirm the new rule before applying.
4. **Diff preview.** For each pending edit, show a minimal contextual diff (the relevant array, three lines before and after the change). Require explicit approval before each write.

   ```
   Diff for action 1
   ═════════════════
     "permissions": {
       "allow": [
         "Bash(git:*)",
   -     "Bash(git status:*)",
         "Bash(jq:*)"
       ]
     }

   Write? (yes / no / show full file)
   ```

5. **Re-read before write.** Between the diff preview and the actual write, re-read the file. If contents changed since the preview (concurrent edit), abort and rerun the diff. Settings files are small; the re-read cost is negligible.
6. **Serialise** with 2-space indentation and a trailing newline. Write the file. Read it back and confirm the new array length matches the expectation (old length ± 1).
7. **One read-mutate-write cycle per edit.** Do not batch all mutations into one write. A failure midway leaves the file in a consistent state and the user can resume from a clean point.

### 7. Summary

```
Permissions lint applied
════════════════════════
  ✓ Bash(git status:*)          removed from ~/.claude/settings.json (subsumed by Bash(git:*))
  ✓ Bash(npm install:*)         removed from /Users/me/proj/.claude/settings.json (subsumed by Bash(npm:*))
  ✓ Bash(jq) → Bash(jq:*)       rewritten in ~/.claude/settings.json
  ○ Bash(terraform:*)            kept (user declined)
  ○ Bash(git push:*) conflict   kept both (user declined; review later)

Net change: 2 rules removed, 1 rewritten. Rerun /permissions-audit to see the
classifier-cost impact.
```

## Edge cases

- **Empty log.** Pitfalls 1, 2, 3, and 4 all need log data. With an empty log, report "no log data — only static checks possible" and offer a degraded check that catches obvious literal-string duplicates within a bucket (same string twice in the same array). Do not attempt anything probabilistic.
- **Short log window (< 7 days).** Skip pitfall 4 entirely. Annotate the report. Pitfalls 1–3 still run but the user should know the corpus is small.
- **Malformed log entries.** Skip and count. Surface the count in the summary. If more than ~10% of lines are malformed, recommend the user inspect the hook output.
- **Identical rule string in multiple settings files.** Not redundant — each file is its own scope and removing one without the other changes scope semantics. Treat as two distinct rules with distinct origins; lint each independently.
- **MCP-server wildcard subsumption.** `mcp__exa__web_search_exa` is subsumed by `mcp__exa__*`. The matcher in `lib/rule-matcher.mjs` handles MCP wildcards; the subsumption check uses `S_rule` intersection and so picks this up automatically. Do not add MCP-specific logic.
- **Path-tool rule overlap.** `Read(~/.config/**)` subsumes `Read(~/.config/foo)` over any reasonable log. The same `S_rule` intersection logic handles this. No special-case code.
- **Compound commands.** `rule-syntax.md` notes that allow rules match per sub-command (`git status && echo done` requires rules for both `git` and `echo`). The log entry's `detail` is the raw command string — the matcher in `lib/rule-matcher.mjs` does not split on `&&` or `;`. This means a compound call may produce an entry that matches none of the rules even though both halves are individually allowed. Do not flag this as a pitfall; it is a matcher limitation, not a config bug. Mention it in the report only if asked.
- **`skipAutoPermissionPrompt: true` settings.** When this is set, `permissions.ask` rules behave like `permissions.allow` for matching purposes — the user is never prompted. Pitfall 3 (allow/deny conflict) should treat `ask` rules as if they were `allow` when this flag is set; otherwise leave them in their own bucket.
- **Concurrent settings.json edits.** Covered by step 6's re-read-before-write rule. Abort on contents drift; do not merge.
- **Settings files that contain comments.** Standard JSON has no comment syntax. If a settings file fails to parse because of `//` or `/* */` comments, report the parse error and do not attempt to strip comments. Settings files are JSON, not JSONC.

## Reference material

- `${CLAUDE_PLUGIN_ROOT}/references/matcher-syntax-pitfalls.md` — canonical pitfalls catalogue and detection recipes. Every detection step in this skill resolves against an entry there.
- `${CLAUDE_PLUGIN_ROOT}/references/rule-syntax.md` — matcher semantics, including the `Bash(cmd)` vs `Bash(cmd:*)` distinction that pitfall 1 detects.
- `${CLAUDE_PLUGIN_ROOT}/lib/rule-matcher.mjs` — `matchRule(rule, entry)` implementation. Use directly; do not reimplement.
- `${CLAUDE_PLUGIN_ROOT}/references/security-considerations.md` — consult before suggesting `remove` on any deny rule. A zero-match deny is usually preventive and should default to keep.
