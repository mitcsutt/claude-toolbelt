---
name: sandbox-fix
description: This skill should be used when the user asks to "fix sandbox restrictions", "fix permission denied errors", "stop sandbox blocking", "investigate sandbox denials", "review sandbox log", "audit sandbox", "permissions sandbox", "why is sandbox blocking X", or says "/sandbox-fix". Reads ~/.claude/sandbox-denials.jsonl (written by this plugin's PostToolUse hook), groups by signature + matched_path, recommends targeted fixes — either adding a path to permissions.sandbox.filesystem.allowWrite, adding a command head to permissions.sandbox.excludedCommands, or pre-setting dangerouslyDisableSandbox for known remote-network commands.
---

# Sandbox Denial Triage and Fix

Triage the sandbox denial log written by this plugin's PostToolUse hook and propose narrow fixes that stop the same denial firing again. Every entry in the log was produced when a tool call ran under the Claude Code sandbox and emitted stderr matching one of the patterns in `${CLAUDE_PLUGIN_ROOT}/references/sandbox-signatures.md`. The hook captured the signature, the offending path (when one was extractable), and the command head — this skill turns that record into a settings.json change.

Sandbox denials usually resolve in one of three ways: add the path to `permissions.sandbox.filesystem.allowWrite`, exclude the command from the sandbox entirely via `permissions.sandbox.excludedCommands`, or pre-set `dangerouslyDisableSandbox: true` at call sites that hit the network. The signature dictionary in `references/sandbox-signatures.md` already records the recommended remediation per `signature` id; this skill applies it.

## Inputs

| File | Purpose |
|------|---------|
| `~/.claude/sandbox-denials.jsonl` | Append-only log of sandbox denial events, one JSON object per line. Written by the plugin's PostToolUse hook. |
| `~/.claude/permission-log.jsonl` | Tool call log written by the plugin's PreToolUse hook. Used here only to detect the DDS retry pattern (a denied call followed by the same command run with `sandbox_disabled: true`). |
| `~/.claude/settings.json` | Global user settings. Target file for `permissions.sandbox.*` writes. Every other top-level key (hooks, env, enabledPlugins, permissions.allow, etc.) must be left untouched. |
| `${CLAUDE_PLUGIN_ROOT}/references/sandbox-signatures.md` | Signature dictionary. Resolves the `signature` id on each log entry to a recommended `fix` and a description of the root cause. |

## Workflow

### 1. Load the denial log

Read `~/.claude/sandbox-denials.jsonl`. Each line looks roughly like:

```jsonl
{"timestamp":"2026-05-18T11:47:02.118Z","session_id":"abc-123","cwd":"/Users/me/proj","command":"git push origin HEAD","signature":"ssh-host-key","category":"ssh","fix":"dangerouslyDisableSandbox","matched_path":null,"command_head":"git"}
```

If the file does not exist, report: "No denials logged yet — confirm the PostToolUse hook is enabled." Stop there. Do not attempt to fabricate denials from elsewhere.

If the file exists but is empty, say so: "Denial log is empty — nothing to triage." This is the expected steady state once fixes are in place.

If a line fails `JSON.parse`, skip it and increment a malformed-entry counter. Do not stop the run. Report the counter at the end of the summary so the user knows the hook may be writing corrupted records.

### 2. Group and rank

Aggregate the parsed entries:

- Primary key: `signature`. All entries with the same signature share a root cause and a recommended fix.
- Secondary key: `matched_path`. Within a signature group, collapse paths that share a parent directory (e.g. `.../proj/.git/refs/heads/foo` and `.../proj/.git/refs/heads/bar` both roll up to `.../proj/.git/refs/heads/`). For ssh signatures `matched_path` is usually `null` — group by `command_head` instead.

Rank groups by raw count, highest first. Within a group, also surface:

- The number of distinct `session_id` values (a denial that spans many sessions is recurring, not one-off).
- The number of distinct `cwd` values (a denial in many projects argues for a global fix in `~/.claude/settings.json`; a denial in one project may suggest a project-scoped sandbox config later).

Do not show entries flagged with `category: "macos-posix"` in the main list. Surface them in a separate section — see step 4.

### 3. Correlate with retry behavior

For each denial, look forward in `~/.claude/permission-log.jsonl` within the same `session_id` for a tool call where:

- `timestamp` is within 30 seconds after the denial entry's timestamp.
- `tool` is `Bash`.
- `detail` shares the same `command_head` (and ideally the same first two tokens).
- `sandbox_disabled` is `true`.

A match proves the user (or the agent) retried with `dangerouslyDisableSandbox: true` after the denial — i.e. the sandbox was getting in the way of legitimate work. Track the retry rate per group: "21 of 22 denials in this group were retried with DDS within 30s" is strong evidence the fix should be applied permanently.

If `~/.claude/permission-log.jsonl` is missing, skip this correlation step silently and note the gap in the final summary. The skill still works without it — the retry rate is corroborating evidence, not a prerequisite.

### 4. Present grouped findings

One scannable block, ordered by count descending. Example shape:

```
Sandbox Denial Triage
═════════════════════

42 denials across 8 sessions (signatures grouped)

  1. fs-eperm-claude-dir          22× (15 in .claude/worktrees, 7 in .claude/skills)
     Fix: add .../.claude/{worktrees,skills} to sandbox.filesystem.allowWrite
     Retry rate: 21/22 retried with dangerouslyDisableSandbox: true

  2. ssh-host-key                  12× (all git remote ops)
     Already mitigated: git is in excludedCommands. macos-posix category.

  3. git-rename                     8× (all in /Users/me/proj/.git/refs)
     Fix: add /Users/me/proj/.git to sandbox.filesystem.allowWrite
     Retry rate: 8/8 retried with dangerouslyDisableSandbox: true

  4. _cosmetic_npmrc              308× — suppressed, not shown

Apply fixes? (1,2 / all / skip)
```

Notes for the presentation:

- Resolve each `fix` value against the dictionary in `references/sandbox-signatures.md` so the proposed remediation matches what's documented there. Do not invent new remediation strategies.
- `fix: "claude-sandbox-allowwrite"` → propose adding the collapsed parent directory to `permissions.sandbox.filesystem.allowWrite`. Prefer the narrowest path that still covers every entry in the group (e.g. `.../.git` over `.../`).
- `fix: "dangerouslyDisableSandbox"` → propose adding `command_head` to `permissions.sandbox.excludedCommands` for git, gh, ssh, or any command that genuinely needs `~/.ssh` access. For ad-hoc one-off commands, recommend pre-setting `dangerouslyDisableSandbox: true` at the call site instead.
- `category: "macos-posix"` → annotate that this is not a Claude sandbox denial. The command is already excluded from the sandbox, so the EPERM came from macOS's own POSIX permissions. Suggest checking the matched path's owner and mode with `ls -le` rather than touching settings.json.
- Cosmetic suppressors should already be filtered out by the hook (`signatures.mjs` returns `null` for them), but if any leak through, note them as "suppressed, not shown" and skip.

Support bulk selections: "1,3", "all", "all allowwrite", "skip". Do not loop one finding at a time.

### 5. Apply approved fixes

For each approved finding, edit `~/.claude/settings.json` with strict safety guards:

1. Read the current file. If `JSON.parse` throws, stop and report the parse error verbatim. Do not attempt to repair the file. Do not write anything.
2. Mutate `permissions.sandbox.*` only. Specifically:
   - For `claude-sandbox-allowwrite` fixes: ensure `permissions.sandbox.filesystem.allowWrite` exists as an array, then append the proposed path. Deduplicate against existing entries with string-equality match. Skip silently if a broader existing path already covers the new one (e.g. existing `.../.git` makes `.../.git/refs` redundant).
   - For `dangerouslyDisableSandbox` fixes: ensure `permissions.sandbox.excludedCommands` exists as an array, then append the proposed `command_head`. Deduplicate the same way.
3. Preserve every other top-level key — `hooks`, `enabledPlugins`, `env`, `permissions.allow`, `permissions.deny`, `permissions.ask`, anything else — exactly as it was. Do not reorder. Do not rewrite the file from a model of "what settings.json should look like"; round-trip the user's existing object and mutate in place.
4. Show the resulting diff to the user. Each fix should produce a small diff — typically one or two new array entries. Require explicit approval before each write.

   ```
   Diff for fix 1
   ══════════════
     "permissions": {
       "sandbox": {
         "filesystem": {
           "allowWrite": [
             "/Users/me/.claude/agents",
   +         "/Users/me/.claude/worktrees",
   +         "/Users/me/.claude/skills"
           ]
         }
       }
     }

   Write? (yes / no / show full file)
   ```

5. Serialise with 2-space indentation and a trailing newline. Write the file. Read it back and confirm the new array length to verify.
6. If the user has multiple fixes approved in one turn, apply them in sequence with one read-mutate-write cycle each, so a failure midway leaves the file in a consistent state. Do not batch all mutations into one write — round-trip per fix.

After every write, do not restart anything automatically. The user must restart their Claude Code session to pick up new sandbox settings; mention this once in the final summary.

### 6. Summary

```
Sandbox fixes applied
═════════════════════
  ✓ fs-eperm-claude-dir → sandbox.filesystem.allowWrite += [.../.claude/worktrees, .../.claude/skills]
  ✓ git-rename          → sandbox.filesystem.allowWrite += [/Users/me/proj/.git]
  ○ ssh-host-key        → skipped (already mitigated)

Effect: the 30 matching denials in the log would no longer fire under the new sandbox config.
Restart your Claude Code session for the changes to take effect.
```

## Fix dictionary

| `fix` value | settings.json change | Source of truth |
|-------------|---------------------|-----------------|
| `claude-sandbox-allowwrite` | Append the collapsed parent of `matched_path` to `permissions.sandbox.filesystem.allowWrite` | `references/sandbox-signatures.md` |
| `dangerouslyDisableSandbox` | Append `command_head` to `permissions.sandbox.excludedCommands`, or pre-set `dangerouslyDisableSandbox: true` at the call site | `references/sandbox-signatures.md` |
| (no fix, `category: "macos-posix"`) | No settings change — investigate file ownership / mode on the matched path | macOS POSIX, not the Claude sandbox |

The dictionary in `references/sandbox-signatures.md` is canonical. If a signature lists a different `fix` than what is summarised above, follow the reference file.

## Edge cases

- **macos-posix entries**: when the offending command head is already in `excludedCommands`, the hook tags the entry as `macos-posix`. The Claude sandbox cannot be the cause — the kernel or the file mode is. Do not propose a settings.json edit. Suggest `ls -le <matched_path>` and a file-owner check instead. Surface these in a separate section so they don't dilute the actionable list.
- **Empty log**: report "Denial log is empty — nothing to triage." This is the goal state. Do not propose preventive fixes from `references/sandbox-signatures.md` without observed denials; speculative fixes broaden the sandbox without justification.
- **Malformed entries**: skip lines that fail `JSON.parse`, count them, surface the count at the end. If more than ~10% of lines are malformed, recommend the user inspect the hook output. Do not attempt to repair entries.
- **Missing `matched_path`**: ssh signatures and some EPERM lines do not carry a path. Group by `command_head` and propose the `dangerouslyDisableSandbox` fix path. Never invent a synthetic path.
- **Concurrent settings.json edits**: between the diff preview and the write, re-read the file to catch external modifications. If the contents changed since the preview, abort the write and rerun the diff. Settings file is small; the re-read cost is negligible.
- **Path inside a worktree**: `${HOME}/.claude/worktrees/<id>/...` paths come from the agent's own scratch worktrees. Adding `${HOME}/.claude/worktrees` to `allowWrite` once handles every future worktree; do not add the per-id path.
- **Cosmetic suppressors leaking through**: the hook should never write entries with `signature` starting in `_cosmetic_`. If any appear (e.g. an old log from before the suppressor was added), filter them out at load time and do not propose fixes.

## Reference material

- `${CLAUDE_PLUGIN_ROOT}/references/sandbox-signatures.md` — canonical dictionary of signatures, categories, and recommended fixes. The skill resolves every `signature` id against this file.
- `${CLAUDE_PLUGIN_ROOT}/references/security-considerations.md` — risk tiers and least-privilege guidance. Consult before broadening `excludedCommands` — every excluded command is a hole in the sandbox.
