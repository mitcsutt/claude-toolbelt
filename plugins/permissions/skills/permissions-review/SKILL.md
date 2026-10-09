---
name: permissions-review
description: Scans your Claude Code permission settings and recent transcripts, then proposes safe fixes. Use to review, audit or lint permissions, find unsafe permission rules, tidy project settings, promote allow rules, or run "/permissions-review".
---

# Permissions review

One entry point for auditing and tidying permission rules. All analysis is done by
`perm-scan`; all edits go through `perm-apply`. You only present and decide.

## 1. Scan

Run `perm-scan --json` (bare). If the command is not found, run
`node "${CLAUDE_PLUGIN_ROOT}/bin/perm-scan" --json` instead.

- Cross-project tidy: add `--all-projects <root>`. Ask which root if unclear.
- Optional: `--days N` (transcript window), `--min-calls N` (candidate threshold).
- The JSON is the only input. Never Read settings files or transcripts directly for
  analysis, and never read old 2.x log files.

If `truncated` is true, say so and offer to re-run narrower (`--cwd`, `--days`).

## 2. Present

Keep it scannable. In this order:

1. **HIGH and MED findings**: rule, file, one plain-English sentence of why, doc link.
2. **Dead rules** (`dead` severity), grouped by finding id.
3. **Friction**: classifier blocks by reason, rejected prompts, headless denials,
   then `candidates` (rule, calls, suggested scope). Deny rules that fire are
   informational: the rules are working, so present them as such, not as a problem.
   If `candidatesDropped` is present, mention it briefly ("not suggested because …").
4. Low and info findings: one line each, or a count.

## 3. Propose changes

Turn findings into `perm-apply` commands:

| Finding | Action |
|---|---|
| Risky allow (R*) | remove, or replace with a narrower rule |
| Dead (D*) | remove |
| Candidate | add at its `scopeSuggestion` scope |
| Sandbox (S1) | `--add-sandbox-allow-write <path>` / `--add-sandbox-excluded-command <cmd>` |

Never propose adding anything flagged R*.

**Which file.** Findings carry their own `file`. A candidate goes to the file of its
`scopeSuggestion`, looked up in `scopes[]`. Exception: a candidate with
`otherProject: true` or a `project` path targets `<project>/.claude/settings.json`;
say so to the user.

Show every command with `--dry-run` first, grouped per file:

```
perm-apply --file <settings file> --remove-allow '<rule>' --add-allow '<rule>' --dry-run
```

Then ask the user which to apply. Apply only approved ones, with one `perm-apply`
call per file (same command without `--dry-run`). If `perm-apply` is not on PATH, use
`node "${CLAUDE_PLUGIN_ROOT}/bin/perm-apply"`.

- Never edit settings with Edit, Write or NotebookEdit. `perm-apply` is the only writer.
- Tell the user the auto-mode classifier or a prompt will likely ask them to confirm
  settings writes. That is expected; do not work around it.
- S1 sandbox findings only appear when `sandbox.enabled` is true.

## 4. Legacy findings

L1 and L3 findings (leftover 2.x logs, orphaned data dirs) come with a delete command.
Show it, and run it only if the user explicitly says so.

## 5. Finish

End with exactly three lines:

```
Applied: <n> (<files>)
Skipped: <n>
Pending: <n> (<what is waiting on the user>)
```
