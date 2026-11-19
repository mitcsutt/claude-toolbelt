# Hooks: code that also talks to the model

Platform facts were checked against Claude Code 2.1.328 (code.claude.com/docs/en/hooks and hooks-guide) on 2026-11-19. Hook semantics change often. Before a finding depends on an event, a field or an exit code, re-fetch `https://code.claude.com/docs/en/hooks.md`.

## Semantics that decide findings

- **Blocking** (E18).
  - Exit 2 blocks, with stderr as the reason, but only on blocking events: PreToolUse, UserPromptSubmit, Stop, SubagentStop, PreCompact, TaskCreated/TaskCompleted, ConfigChange and similar.
  - Exit 2 does **not** block PostToolUse (the tool already ran), SessionStart, SubagentStart, Notification or PermissionRequest.
  - Any other exit code with plain output is a non-blocking error, so the action goes ahead. That covers 1, 127 and a crash.
  - Valid JSON on a non-2 exit is applied as-is.
  - A timeout lets the action through.
  - `onFailure: "block"` (2.1.295+, command and http hooks) makes a failure block. It has no effect on Stop, SubagentStop, TaskCompleted or TeammateIdle hooks, or on async hooks.
  - WorktreeCreate and WorktreeRemove fail on any non-zero exit.
  - On Windows without Git Bash, a failing or timed-out shell-form PreToolUse or PermissionRequest hook blocks by default.
- **Who sees what** (E22).
  - Claude never sees stderr from exit 0.
  - Plain stdout reaches Claude only on UserPromptSubmit, UserPromptExpansion, SessionStart and PostModelSwitch.
  - On a synchronous hook, `systemMessage` goes to the user, not Claude. An async hook delivers `systemMessage` and `additionalContext` to Claude on the next turn and shows the user nothing. Some events discard `systemMessage` entirely: Setup, InstructionsLoaded, Notification, PreCompact, PostCompact, SessionEnd, ConfigChange and Worktree*.
  - A PreToolUse `deny` reason goes to Claude; an `ask` reason goes to the user.
  - `additionalContext` is capped at 10,000 characters, and past the cap Claude gets a 2,000-character preview.
  - On a synchronous hook, stdout must hold one JSON object and nothing else. A stray `echo` before it means the JSON is ignored with no error.
- **Matchers** (E19).
  - They are case-sensitive. Names made only of letters, digits, `_`, `-`, space, `,` and `|` match exactly; anything else is an unanchored regex (`Edit.*` also hits `NotebookEdit`).
  - MCP tools are `mcp__<server>__<tool>`, and plugin servers are `mcp__plugin_<plugin>_<server>__<tool>`.
  - A matcher on an event that has no matcher support is ignored silently. `if` on a non-tool event means the hook never runs.
- **Events.**
  - A failed Bash call fires PostToolUseFailure, not PostToolUse.
  - `@file` mentions bypass PreToolUse.
  - SessionStart with no matcher re-fires on resume, clear and compact.
- **Scope.**
  - Settings and plugin hooks also run inside subagents, with `agent_id` in the input.
  - Skill hooks persist for the session once the skill runs.
  - Plugin agents ignore `hooks:` in their frontmatter.
  - Handlers run in parallel, and multiple `updatedInput` rewrites are neither merged nor ordered.
- **Loops** (E20). Stop and SubagentStop get `stop_hook_active`. The 8-continuation cap resets on every tool call.
- **Environment.**
  - Available variables: `CLAUDE_PROJECT_DIR`, `CLAUDE_PLUGIN_ROOT`, `CLAUDE_PLUGIN_DATA`, `CLAUDE_ENV_FILE`. The last exists on SessionStart, Setup, CwdChanged and FileChanged only, and its contents run as shell code before every Bash call.
  - Shell form runs under `sh -c`, so quote every placeholder: `"${CLAUDE_PLUGIN_ROOT}"`.
- **Default timeouts.**
  - Command hooks: 600 s.
  - UserPromptSubmit: 30 s, and the hook blocks the prompt until it finishes.
  - SessionEnd: a shared 1.5 s.
- **Trust** (E23). In `-p` and SDK runs, project settings hooks run without the trust dialog. Repo-shipped hooks are untrusted code.

## Checks

| Check | A violation looks like | Evidence |
|---|---|---|
| A guard blocks the right way | `exit 1` on a violation; JSON deny printed after other output | E18, E22 |
| A guard fails safe | no `onFailure: "block"` on a PreToolUse, UserPromptSubmit or PermissionRequest policy hook (it does nothing on Stop-type or async hooks); `jq`, `node` or `python3` used without a `command -v` check; a relative script path | E18 |
| The matcher fires | `"matcher": "bash"`; a removed or renamed tool; `mcp__github` without `__.*`; a matcher on Stop; `if` on SessionStart | E19 |
| The event fits the condition | a "test failed" nudge on PostToolUse, which never sees failures | events above |
| The reason is a usable prompt | "Blocked." with no alternative and no "stop and ask", which invites the agent to reword and retry | E21 |
| The model-facing channel is the intended one | text meant for Claude sent through a synchronous hook's `systemMessage` or through stderr on exit 0; a warning meant for the user sent through an async hook's `systemMessage`, which only Claude sees | E22 |
| No loop | a Stop hook that blocks without checking `stop_hook_active` or keeping its own counter; a demanded action another hook denies | E20 |
| Fires only where it fits | a "status update for the user" nudge running in subagents and headless runs; a check that ignores `agent_id` | E33 |
| Hot path stays fast | synchronous network calls on every PreToolUse or UserPromptSubmit | timeouts above |
| Injected text is small and factual | a multi-KB house-rules block on every SessionStart; imperative "MANDATORY" text; a copy of CLAUDE.md; over 10,000 characters | E22, E10 |
| Not sold as a security boundary | a regex `git push.*--force` blocker described as preventing force-push | E21 |
| Covers every path | an `Edit` guard on `.env` with no Bash, NotebookEdit or MCP coverage | E21 |
| Placeholders quoted, executables pinned | `bash ${CLAUDE_PLUGIN_ROOT}/x.sh` without quotes; a bare command resolved on PATH | platform facts |
| Secrets stay out | secret values in stdout, `additionalContext` or unescaped lines in `CLAUDE_ENV_FILE` | E23 |
| Prose agrees with the hook | CLAUDE.md says "force-push is hook-blocked" but a bypass passes; a README claims enforcement while the hook fails open | E2, E21 |
| Tested | no fixture test per event, so a silent regression goes unnoticed | E18 |

## Fixture-run protocol

1. Read the hook configuration and the whole script, including anything it sources. Don't run a script that writes outside a temp directory, touches the network, changes git or package state, or sources files you haven't read; review it on paper instead.
2. Build the event JSON from the event's input schema in the hooks reference.
3. Run it from a scratch directory and record the exit code:
   `cd "$(mktemp -d)" && printf '%s' "$EVENT_JSON" | CLAUDE_PROJECT_DIR="<repo>" bash "<script>"; echo "exit=$?"`
4. For a guard, run four kinds of input:
   - the case it must block;
   - one benign case;
   - a missing dependency, with `env PATH=/usr/bin:/bin` when `command -v jq` shows `jq` lives elsewhere;
   - two or three bypass shapes: a reordered flag, `git -C .`, a leading space, `$VAR` indirection, a chained command.
5. Quote the command, exit code and an output excerpt as evidence.

Hidden-Unicode scan for any text artifact, portable to macOS and Linux:

`perl -CSD -ne 'print "$ARGV:$.\n" if /[\x{200B}-\x{200F}\x{202A}-\x{202E}\x{2060}-\x{2064}\x{2066}-\x{2069}\x{FEFF}\x{E0000}-\x{E007F}]/; close ARGV if eof' <files>`

## Other tools' hooks

Failure defaults differ, so judge a hook against the tool that runs it.

| Tool | Blocks with | On crash | Notes |
|---|---|---|---|
| Cursor `.cursor/hooks.json` | exit 2 | fails open unless `failClosed` | invalid JSON on a permission hook blocks; loop cap of 5 for its own hooks but none for Claude Code hooks it loads |
| Codex `.codex/hooks.json` | exit 2 | fails open | trust is tied to the hook's hash, so changed hooks are skipped until re-trusted |
| Gemini CLI `settings.json` | exit 2 | fails open | project hooks are fingerprinted |
| Copilot `.github/hooks/*.json` | non-zero exit on a command `preToolUse` hook | command hooks fail closed; HTTP hooks fail open | all hooks fail open on timeout; default timeout 30 s |
