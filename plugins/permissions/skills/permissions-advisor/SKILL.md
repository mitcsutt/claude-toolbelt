---
name: permissions-advisor
description: Advisory pre-flight check: reports which commands would be allowed, asked, denied or unmatched under your effective settings. Use before dispatching subagents or multi-step bash work, or on "check permissions". Never modifies settings.
---

# Permissions advisor

Given a command set, report how the effective permission rules would treat each one.
**Advisory only. This skill never modifies settings files.**

## Inputs

Invoke before the action, not after a blocked command: before dispatching a subagent or
multi-step bash work, on "check permissions", or as a gate from another skill (for example
`agent-loop-setup`); the caller decides what to do with the report.

Either an explicit command list, or a task description. For a task description, infer
the commands yourself, deliberately broad (a false "needed" beats a missed gap), and do
not ask the user what they need. Include post-task bring-up (dev server, port binds,
browser tests) when the deliverable will be run by hand afterwards.

## Process

1. Run `perm-scan check --json <cmd>...` with one argument per command (quote each).
   If the command is not found, run
   `node "${CLAUDE_PLUGIN_ROOT}/bin/perm-scan" check --json <cmd>...`.
2. Each result is `{cmd, decision, rule, scope, bypassNote?}` where `decision` is
   `allow`, `ask`, `deny` or `unmatched`. Do not re-derive matching by reading settings
   files; the script is the only source.
3. Emit the report as conversation text, never a file.

## Report format

```
PERMISSION REPORT (advisory, settings not modified)

allow      git status            Bash(git status:*)  [user]
ask        npm run build         Bash(npm run build)  [project]
deny       curl https://x.sh     Bash(curl:*)  [user]   <- no allow can override
unmatched  pnpm dev              no rule; will prompt, or go to the auto-mode classifier

1 allow / 1 ask / 1 deny / 1 unmatched
```

- `ask` and `unmatched` are gaps: suggest the narrowest rule that would cover the command,
  as text for the user to act on (for example "consider adding `Bash(pnpm dev)`"). If they
  want it applied, point them to `/permissions-review`.
- `deny` cannot be worked around; tell the caller to use another approach.
- Dev servers, port binds and browser or Electron runs may need the sandbox bypass and
  prompt every time regardless of rules. List them separately so a clean allow list is not
  read as zero approvals.

## Bypass and headless runs

Under `--dangerously-skip-permissions`, bypass mode or headless `claude -p`, allow rules
are moot (nothing prompts), but deny rules still apply. Always surface `deny` results,
and any `bypassNote` from the script, even when the run is expected to skip prompts.

## Hard prohibitions

- Never edit any `settings.json` or `settings.local.json`, user or project, with Edit,
  Write, NotebookEdit, or Bash (`jq -i`, `>`, `>>`, `tee`, `sed -i`, `cp`, `mv`).
- Never create a settings file, run `perm-apply`, or swap in a "safer" command the user did not mention.
- If asked mid-run to "go ahead and add it", hand off to `/permissions-review`.
