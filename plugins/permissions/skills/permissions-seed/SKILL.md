---
name: permissions-seed
description: This skill should be used when the user asks to "seed permissions", "seed rules", "bootstrap permissions", "set up allow rules", "add recipe rules", "initialise settings.json permissions", "apply the starter rule set", "port recipes to settings.json", "add Node/Python/Docker/Git rules", or says "/permissions-seed". Merges pre-built rule sets from references/recipes.md into permissions.allow / permissions.deny / permissions.ask in settings.json without deleting anything already there.
---

# Seed settings.json with Recipe Rules

Take curated rule sets from `${CLAUDE_PLUGIN_ROOT}/references/recipes.md` and merge them into a settings file's `permissions.allow`, `permissions.deny`, and `permissions.ask` arrays. Never removes existing entries; never overwrites unrelated keys (`hooks`, `enabledPlugins`, `env`, etc.).

Use this once to get a sensible starting point, and again later whenever you adopt a new stack (e.g. first time you need the Docker rules).

## Inputs

| File | Purpose |
|------|---------|
| `${CLAUDE_PLUGIN_ROOT}/references/recipes.md` | Source of curated rule sets, grouped by workflow. |
| `${CLAUDE_PLUGIN_ROOT}/references/security-considerations.md` | Risk tiers — surface these when a recipe contains HIGH-risk rules. |

## Target file selection

Default target: `~/.claude/settings.json` (global).

Offer to change target only if the user says so. Alternatives:
- `<cwd>/.claude/settings.json` — project-scoped, committed. Prefer this for project-specific recipes (e.g. Terraform, Rails).
- `<cwd>/.claude/settings.local.json` — gitignored project overrides. Prefer this for personal paths or secrets.

Do NOT write to `.claude/settings.json` inside the plugin repo or inside `${CLAUDE_PLUGIN_ROOT}` — settings must live in the user's Claude config.

## Workflow

### 1. Load the recipes

Read `${CLAUDE_PLUGIN_ROOT}/references/recipes.md`. It is organised by workflow:

- **Essential safety (deny)** — rules from the Security Considerations doc: sudo, shutdown, reboot, mkfs, dd, bash/sh/zsh -c, rm -rf /, credential-file reads, shell-config writes. These are always recommended.
- **Git workflow** — allow `Bash(git:*)`, `Bash(gh:*)`; ask on `git push --force`, `git reset --hard`, `git clean -f`, `git checkout --`; deny `git push --force origin main/master`.
- **Node.js** — allow `node`, `npm`, `npx`, `pnpm`, `tsx`, `tsc`, `jest`, `vitest`, `eslint`, `prettier`.
- **Python** — allow `python`, `python3`, `pip`, `pip3`, `pytest`, `ruff`, `mypy`, `black`, `uv`.
- **Ruby / Rails** — allow `ruby`, `bundle`, `gem`, `rake`, `rails`, `rspec`, `rubocop`.
- **Docker** — allow `Bash(docker:*)`, `Bash(docker-compose:*)`, `Bash(docker compose:*)`; ask on destructive ops.
- **MCP servers** — wildcards for commonly trusted servers.
- **Read-only research** — `Read`, `Grep`, `Glob`, `WebFetch`, `WebSearch`, `Agent`.
- **System utilities** — `ls`, `find`, `cat`, `head`, `tail`, `wc`, `sort`, `uniq`, `diff`, `which`, `whoami`, `date`, `echo`, `printf`, `jq`, `grep`, `awk`, `sed`, `cut`, `tr`, `xargs`.

### 2. Load the target settings file

Read the target file. If it doesn't exist yet, treat it as `{}`. If `JSON.parse` throws, stop and tell the user — do not attempt repair.

Extract the current `permissions.allow`, `permissions.deny`, `permissions.ask` arrays (each defaulting to `[]` if missing).

### 3. Present the menu

Show a checklist-style menu. Mark "Essential safety (deny)" as default-on. Let the user pick in one turn:

```
Seed settings.json with recipe rules
════════════════════════════════════
Target: ~/.claude/settings.json
Currently:  12 allow · 0 deny · 0 ask rules

Recipes:
  [*] Essential safety (deny)           — always recommended (adds 14 deny rules)
  [ ] Git workflow                      — 8 rules across allow/ask/deny
  [ ] Node.js                           — 10 allow rules
  [ ] Python                            — 9 allow rules
  [ ] Ruby / Rails                      — 7 allow rules
  [ ] Docker (contains HIGH-risk rules) — 5 rules; docker can mount sensitive paths
  [ ] MCP server wildcards              — tune after you see which MCP tools you use
  [ ] Read-only research tools          — 6 allow rules (Read, Grep, Glob, WebFetch...)
  [ ] System utilities                  — 23 allow rules (ls, cat, jq, grep...)

Which? (e.g. "essential, node, git", "all low", "essential + docker")
```

Support freeform selections: recipe names, `all`, `all except docker`, numeric indices. Ask for the target file up front if the cwd is a project root and the user might want project-scoped rules — but default to global without asking.

### 4. Compute the diff

For each selected recipe, expand its rules into three buckets (`allow`, `deny`, `ask`). Then, for each bucket:

- **Deduplicate** against what's already in the target. String-equality match — do not try to detect semantic overlap (that is `/permissions-audit`'s job).
- **Flag redundancy** if a new allow rule is already covered by an existing broader rule (e.g. adding `Bash(git status:*)` when `Bash(git:*)` is already present). Skip the redundant rule and note the skip.
- **Respect existing denys** — if a new allow rule would match an existing deny pattern, skip it and warn. Never auto-add an allow that conflicts with deny.

### 5. Show the diff

```
Diff preview
════════════
Target: ~/.claude/settings.json

permissions.deny    +14 rules, 0 skipped
permissions.allow   +28 rules, 2 skipped (already present)
permissions.ask     +5 rules, 0 skipped

Examples of what will be added:
  deny   "Bash(sudo:*)"
  deny   "Read(~/.ssh/**)"
  allow  "Bash(git:*)"
  allow  "Bash(jq:*)"
  ask    "Bash(git push --force*)"
  ask    "Bash(rm -rf *)"

Write? (yes / no / show full list)
```

Only write after explicit confirmation.

### 6. Apply

1. Re-read the target file (catch concurrent modifications).
2. Build the updated object — preserve every top-level key, preserve key ordering where possible, only mutate `permissions.allow` / `.deny` / `.ask`.
3. Append new rules to the end of each array (preserve existing ordering of user rules — they may have ordering significance the user cares about).
4. Serialise with 2-space indentation, trailing newline.
5. Write. Do not back up the file — git history in the repo (for project files) and Time Machine (for `~/.claude/`) cover recovery. If the user is writing to a non-git location and wants a backup, they'll ask.
6. Confirm: read the file back and count the new rule counts. Report.

### 7. Summary

```
Seeded ~/.claude/settings.json
  deny:  +14 (total now 14)
  allow: +28 (total now 40)
  ask:   +5  (total now 5)

Restart your Claude Code session to pick up the new rules. Run /permissions-audit next
week to see how many log entries are now covered.
```

## Edge cases

- **No `permissions` key at all**: create `{ "permissions": { "allow": [], "deny": [], "ask": [] } }` and merge into that.
- **`permissions` is `null` or not an object**: refuse to write. Tell the user their settings.json is in an unexpected shape and let them fix it.
- **Array items aren't strings**: preserve them as-is; only append new string rules.
- **User says "apply all"**: walk through every recipe. For HIGH-risk recipes (Docker), pause and confirm specifically.
- **Target file is read-only**: report the filesystem error; do not retry.

## Notes

This skill is the "starting point" complement to `/permissions-promote`. `/permissions-seed` gives you a baseline that covers common workflows; `/permissions-promote` then narrows based on what you actually run.

Running `/permissions-seed` a second time is idempotent for already-present rules — everything already there gets skipped silently.
