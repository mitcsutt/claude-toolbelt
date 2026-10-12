---
name: permissions-review
description: Audits Claude Code permission settings and recent denials, then proposes safe fixes for approval. Use to review, audit or lint permissions, find unsafe or dead rules, tidy project settings, or run "/permissions-review".
---

# Permissions review

`perm-scan` does all analysis; `perm-apply` does all edits. You present and let the user decide.

## 1. Scan

Run `perm-scan --json` (bare). If not found, run `node "${CLAUDE_PLUGIN_ROOT}/bin/perm-scan" --json`.

- Cross-project tidy: `--all-projects <root>` (ask which root if unclear). Optional: `--days N`.
- The JSON is the only input. Never Read settings files, transcripts or old 2.x logs for analysis.
- `truncated` is `false` or an object counting what was dropped (`findings`, plus per-list counts). Say what was cut. For more, re-run narrower (`--cwd <project>`, `--days N`).

## 2. Present

Scannable, in this order:

1. **HIGH and MED findings**: rule, `file`, one plain sentence of `why`, `doc` link.
2. **Dead rules** (`dead` severity), grouped by id. D6: `rule` is the dead rule and `coveredBy` the broader one. Warn that if the dead rule sits in a committed project file, removal affects teammates.
3. **Friction**: classifier blocks by reason, rejected prompts, headless denials. Present `friction.denials` by `kind`: `deny-rule` (a rule blocked it; name the rule), `hook` (a hook did; name hook and check), `builtin`. Working denials are not a problem, except D9: a deny rule mostly hitting /tmp or project paths blocks routine commands. Highlight it.
4. Low and info findings: one line each, or a count.

For new allow rules, tell the user to run `/fewer-permission-prompts` (built-in). This skill does not suggest allow rules.

## 3. Propose changes

Each finding carries `file` and, for rules, `list` (`allow`, `deny` or `ask`). Use `list` to pick the flag; a wrong list silently changes nothing.

| Finding | Action |
|---|---|
| Risky allow (R*, incl. R13: open `sudo`/`doas`/`su` allow, HIGH) | `--remove-allow '<rule>'`, optionally `--add-allow` a narrower rule (ask the user for the specifics, e.g. domains for bare `WebFetch`) |
| Dead (D*, D6 too) | `--remove-allow` / `--remove-deny` / `--remove-ask` per `list` |
| D8 (redundant `Bash(cat ...)` deny) | `--remove-deny` the Bash deny; keep the Read deny, Claude Code applies it to cat/head/tail |
| D9 (over-broad deny) | Replace: `--remove-deny '<rule>'` plus narrower literal `--add-deny` rules. Ask the user which paths must stay blocked |
| S1 (sandbox) | `sandbox.key` `filesystem.allowWrite` gives `--add-sandbox-allow-write <value>`; `excludedCommands` gives `--add-sandbox-excluded-command <value>` |

Flags not listed (`--add-deny`, `--add-ask`) exist; never add anything flagged R*. Findings with no `list` (flags, env, D5, R12, L2) cannot be edited by `perm-apply`; explain them in words.

Pass absolute `file` paths (`perm-apply` also expands a leading `~`). Show every command with `--dry-run` first, grouped per file:

```
perm-apply --file <file> --remove-deny '<rule>' --add-deny '<rule>' --dry-run
```

Then ask which to apply, and run the approved ones without `--dry-run`, one call per file. If `perm-apply` is not on PATH, use `node "${CLAUDE_PLUGIN_ROOT}/bin/perm-apply"`.

- Never edit settings with Edit, Write or NotebookEdit. `perm-apply` is the only writer.
- Tell the user the auto-mode classifier or a prompt will likely ask to confirm settings writes. Expected; do not work around it.

## 4. Legacy findings

L1 (leftover 2.x logs, auto-deleted after 30 days) and L3 (orphaned data dirs) include the full delete command. Show it verbatim and run it only on the user's explicit OK.

## 5. Finish

End with exactly three lines:

```
Applied: <n> (<files>)
Skipped: <n>
Pending: <n> (<what is waiting on the user>)
```
