---
name: permissions-bootstrap-project
description: This skill should be used when the user asks to "bootstrap project permissions", "set up project-local settings.json", "derive project rules from log", "make a project allowlist", or says "/permissions-bootstrap-project". Filters ~/.claude/permission-log.jsonl to entries whose cwd is inside the current project root, derives allow rules covering only those entries, and writes <cwd>/.claude/settings.json (committed) or .claude/settings.local.json (gitignored).
---

# Bootstrap Project-Level Permissions

Derive a project-scoped allowlist from the permission log. Long-lived projects develop tool patterns that have no business in the user's global settings — monorepo-specific package manager scripts (`pnpm turbo:*`), project-scoped MCP servers, repo-local CLIs (`bin/console`, `make:*`), and personal paths under the project root. Promoting those globally pollutes every other project; leaving them in Auto mode pays classifier cost on every call. The right home is `<cwd>/.claude/settings.json` (committed) or `<cwd>/.claude/settings.local.json` (gitignored).

This skill is the project-scoped counterpart to `/permissions-promote`. Promote reads the whole log and writes globally by default. Bootstrap-project filters the log to entries whose `cwd` is inside the current project root, subtracts anything the global allow list already covers, and writes the survivors into the project's own settings file. Run once when adopting the plugin in an existing repo; rerun when the project's tool surface area changes.

## Inputs

| File | Purpose |
|------|---------|
| `~/.claude/permission-log.jsonl` | Source data — every tool call recorded by the PreToolUse hook. |
| `~/.claude/settings.json` | Global allow list. Used as the "subtract" set. |
| `<cwd>/.claude/settings.json` | Project-committed settings. Default write target. |
| `<cwd>/.claude/settings.local.json` | Project-local overrides. Gitignored. Target for personal paths and per-developer MCP tokens. |
| `${CLAUDE_PLUGIN_ROOT}/lib/rule-matcher.mjs` | `matchRule(rule, entry)`. Use directly for dedup and global-subtract checks. |
| `${CLAUDE_PLUGIN_ROOT}/references/security-considerations.md` | Risk tiers. Drives the allow-vs-ask split. |
| `${CLAUDE_PLUGIN_ROOT}/references/recipes.md` | Fallback rule sets when the log is too thin to derive rules empirically. |

If `~/.claude/permission-log.jsonl` is missing or empty, report: "No log data yet. The PreToolUse hook hasn't captured any calls — confirm the plugin is enabled and run a few commands inside this project first." Offer `/permissions-seed` as a fallback for users who want recipe-based rules without observed calls, then stop. Do not fabricate candidates from `recipes.md` here; speculative rules broaden the allow list without justification.

## Workflow

### 1. Determine project root

Run `git rev-parse --show-toplevel` from `cwd`. If it succeeds, that path is the project root.

If the command fails (cwd is not inside a git working tree), fall back to `cwd` itself and surface the fallback: "Not in a git repo — using `<cwd>` as project root. Confirm? (yes / pick a different directory / abort)". Do not silently widen scope to a parent directory.

For monorepos with multiple `.claude/` directories (e.g. one at the toplevel and one per workspace), ask the user which layer to target before continuing.

### 2. Filter log entries

Read `~/.claude/permission-log.jsonl`. Parse each line with `JSON.parse`. Skip malformed lines silently and count them; surface the count in the final summary. Keep only entries whose `cwd` is the project root or a descendant — string-prefix match against the resolved root, with a trailing `/` to avoid `/foo/bar` matching `/foo/barbarian`.

Report the filtered count alongside the total ("log has 18,402 entries, 2,847 inside `/Users/m/Documents/projects/rise`"). If the filtered count is under ~50 entries, warn that the corpus is small and offer to skip or fall back to `/permissions-seed`.

### 3. Group and rank

Aggregate the filtered entries with the same logic `/permissions-promote` uses, applied to the filtered slice rather than the full log:

- **Skip free-by-default tools.** `Read`, `Grep`, `Glob`, `Write`, `Edit`, `WebFetch`, `WebSearch` cost no classifier tokens for paths inside `cwd`. No project rules needed.
- **Bash.** Strip leading env-var assignments (`FOO=bar cmd args` → `cmd args`). Group at the two-word prefix (`pnpm turbo`, `make deploy`). Broaden to one word only when three or more distinct two-word prefixes share the same first word.
- **MCP tools.** Group by exact tool name; offer `mcp__server__*` as a broader alternative on request. Only project-scoped MCP servers (those wired through a project-local `.mcp.json`) belong in a project allowlist.
- **Other tools.** Group by tool name; sub-group by `detail` only when invocation patterns are obviously distinct.

Rank groups by call count descending. Use `${CLAUDE_PLUGIN_ROOT}/lib/rule-matcher.mjs` to dedupe candidate rules against each other — two candidates matching identical entry sets collapse to the broader of the two. Do not reimplement matching; that module is the single source of truth.

Exclude candidates with fewer than 3 calls by default — three is the rough floor where a pattern is stable rather than one-off. User can request the full list explicitly.

### 4. Subtract globally-covered rules

For each candidate rule, run `matchRule(rule, entry)` against the entries it covers. If every entry the candidate matches is also matched by some rule already in `~/.claude/settings.json`'s `permissions.allow`, drop the candidate — it would be a duplicate at the project layer. The point of the project file is to add coverage the global file does not already provide.

- Resolve global allow rules into their match sets over the filtered (project-local) slice.
- For each candidate R: if `S_R ⊆ ⋃ S_global_allow_rule` over that slice, drop R.
- If a global deny rule overlaps with R, drop R and flag it: "global deny `Bash(rm -rf *)` would conflict with project allow `Bash(rm:*)`". Never propose a project rule that contradicts a global deny — global wins, but the user should know why calls are blocked.

What remains is the classifier-hitting set that is genuinely project-specific. Show that.

### 5. Pick the target file (committed vs gitignored)

Default to `<cwd>/.claude/settings.json` (committed, project-wide). This is the file teammates see — patterns that benefit the whole team belong here.

Route to `<cwd>/.claude/settings.local.json` (gitignored) for any candidate that:

- References an absolute filesystem path under a personal directory (`/Users/<name>/...`, `~/.config/...`).
- Touches user-specific MCP tokens or credentials (any rule body containing a token-shaped string).
- Allows a tool the user enables individually but the team does not (rare; ask the user).

When in doubt, surface the choice and let the user override. Show the chosen target per candidate. Never quietly write personal paths into a committed file.

If the target file does not exist, create it with the skeleton `{ "permissions": { "allow": [] } }` and a trailing newline. Do not touch `.gitignore` — the plugin assumes `.claude/settings.local.json` is already ignored; if it is not, surface the warning in the summary.

### 6. Present candidates

One scannable block, grouped by target file. Ordered by call count descending within each group. Example shape:

```
Project Allowlist Candidates for rise
═════════════════════════════════════

(filtered to log entries in /Users/m/Documents/projects/rise; rules already in global allow are excluded)

1. Bash(pnpm turbo:*)           [LOW]   402 calls across 24 sessions
   Examples: pnpm turbo run check · pnpm turbo run build
   Target: <cwd>/.claude/settings.json (committed — project-wide)

2. mcp__plugin_api-activepipe-data-access_*  [LOW]  48 calls
   Target: <cwd>/.claude/settings.json (committed)

3. Bash(cd /Users/m/Documents/projects/rise/apps/storybook *)  [LOW]  836 calls
   Target: <cwd>/.claude/settings.local.json (gitignored — contains user-specific path)

4. Bash(docker compose:*)       [HIGH]  21 calls
   Recommend: ask rule rather than allow (docker can mount sensitive paths)
   Target: <cwd>/.claude/settings.json :: permissions.ask

Approve which? (1,2,3 / all / all low / skip)
```

Support bulk selections: "all", "all low", "1,2,4", "first 3", "skip". Do not loop one finding at a time.

HIGH-risk candidates: propose as `ask` rather than `allow`; note the tradeoff inline. CRITICAL-risk: do not propose. If any appear in the log, flag in a separate "concern" section.

### 7. Apply approved rules

For each approved candidate, edit the chosen target file. Mirror the six-point safety contract `sandbox-fix` and `permissions-lint` use; do not deviate:

1. **Parse-or-stop.** Read the target file. If `JSON.parse` throws, stop and report the parse error verbatim. Do not attempt JSON repair. Do not write. If the file does not exist, create the step-5 skeleton and proceed.
2. **Mutate `permissions.allow` only** (or `permissions.ask` for HIGH-risk demotions, matching `/permissions-promote`). Every other top-level key — `hooks`, `env`, `enabledPlugins`, `permissions.deny`, `permissions.sandbox`, anything else — is preserved untouched. Round-trip the user's existing object; do not reconstruct from a model of "what settings.json should look like".
3. **Preserve key order.** When the existing file already has `permissions.allow`, append to the end of that array. When the key has to be created, leave sibling top-level keys in their original positions.
4. **Diff preview, per-rule approval.** Show a minimal contextual diff (the relevant array, three lines of context either side). Require explicit `yes` before each write. Do not batch diffs into one approval prompt.

   ```
   Diff for rule 1
   ═══════════════
     "permissions": {
       "allow": [
         "Bash(make:*)",
   +     "Bash(pnpm turbo:*)"
       ]
     }

   Write? (yes / no / show full file)
   ```

5. **Re-read before write.** Between the diff preview and the actual write, re-read the target file. If contents changed since the preview (concurrent edit), abort the write and rerun the diff. Settings files are small; re-read cost is negligible.
6. **Read-back verification.** Serialise with 2-space indentation and a trailing newline. Write the file. Read it back and confirm the new `permissions.allow` array length matches the expectation (old length + 1). If the read-back disagrees, surface the discrepancy and stop — do not retry blindly.

One read-mutate-write cycle per rule. A failure midway leaves the file consistent and the user can resume from a clean point. Do not batch multiple rules into one write.

No restart is required — the matcher reads settings on each tool call, so new rules take effect immediately. Mention this once in the final summary.

### 8. Summary

```
Project bootstrap applied for rise
══════════════════════════════════
  ✓ Bash(pnpm turbo:*)              → <cwd>/.claude/settings.json :: permissions.allow
  ✓ mcp__plugin_api-activepipe-*    → <cwd>/.claude/settings.json :: permissions.allow
  ✓ Bash(cd .../storybook *)        → <cwd>/.claude/settings.local.json :: permissions.allow
  ✓ Bash(docker compose:*)          → <cwd>/.claude/settings.json :: permissions.ask (HIGH, demoted)
  ○ Bash(rm -rf .next *)            → skipped (conflicts with global deny)

Effect: the 1,307 matching project-local calls in the log would have skipped the
Auto mode classifier. Commit the new <cwd>/.claude/settings.json so teammates
benefit. The .local.json file should already be gitignored — if it is not, add
.claude/settings.local.json to your .gitignore.
```

## Edge cases

- **cwd is not in a git repo.** Fall back to `cwd` as the project root; ask the user to confirm. Do not silently widen scope to a parent.
- **Multiple `.claude/` directories under a monorepo.** Ask which layer to target before deriving rules. Do not assume the toplevel.
- **Log has zero entries for this project.** Report: "No candidates yet — run a few commands inside this project first, then rerun. Or seed via `/permissions-seed`." Stop.
- **Existing project settings.json present.** Merge — never overwrite. Append new rules; preserve every existing rule and every other top-level key. If a candidate string already exists in `permissions.allow`, skip it silently.
- **Existing project settings.json overlaps with global.** Note overlapping rules in the summary as candidates for `/permissions-lint` cleanup. Do not remove them here.
- **Malformed existing settings.json.** Stop. Report the parse error verbatim. Do not attempt repair. The user must fix the file before any approved rules can be written.
- **Candidate would conflict with a project-level deny.** Same handling as the global-deny case in step 4 — drop the candidate and flag it.
- **Filtered log corpus small (< 50 entries).** Surfaced in step 2; user chooses to proceed, abort, or seed via `/permissions-seed`.
- **Project-local MCP server with personal tokens in the tool name.** Route to `.local.json` and surface the routing decision so the user can confirm.
- **`.claude/settings.local.json` not in `.gitignore`.** Out of scope to fix here. Surface a warning in the summary so the user can add the entry themselves.
- **Concurrent edits to the target file.** Covered by step 7's re-read-before-write rule. Abort on contents drift; do not merge.

## Reference material

- `${CLAUDE_PLUGIN_ROOT}/references/recipes.md` — pre-built rule sets. Use only when the user opts in to seeding from recipes instead of deriving from the log.
- `${CLAUDE_PLUGIN_ROOT}/references/security-considerations.md` — risk tiers and least-privilege guidance. HIGH-risk candidates default to `ask`.
- `${CLAUDE_PLUGIN_ROOT}/lib/rule-matcher.mjs` — `matchRule(rule, entry)`. Use directly; do not reimplement.
- `${CLAUDE_PLUGIN_ROOT}/references/rule-syntax.md` — matcher semantics, including `Bash(cmd)` vs `Bash(cmd:*)`.
