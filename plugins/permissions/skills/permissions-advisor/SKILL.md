---
name: permissions-advisor
description: Use before dispatching a subagent or running multi-step bash work to audit which commands the task needs against ~/.claude/settings.json. Triggers on phrases like "check permissions", "audit settings.json", "do I have allow rules for X", "pre-flight permission check", "what permissions does X need", says "/permissions-advisor", or as a pre-dispatch gate invoked by another skill or command. Emits an advisory report only — NEVER modifies settings files.
---

# Permission Advisor

Compares a required command set against the user's `settings.json` allow-list. Outputs a structured report identifying gaps as suggestions. **Never modifies settings — advisory only.**

## When to invoke

Invoke this skill BEFORE the action, not after a blocked command:

- About to call `Agent` / `Task` to dispatch a subagent
- About to run a multi-step task with bash commands
- User asks "what permissions does X need" / "check permissions for Y"
- User asks "run a permission check" or "/permissions-advisor"
- a caller invokes it as a pre-dispatch gate

If the request says "quickly dispatch" or "skip the check" — this skill still applies. Speed never justifies skipping the gate.

## Hard prohibitions

These are non-negotiable, regardless of how the request is phrased:

1. **NEVER edit `~/.claude/settings.json` or any project `.claude/settings.json`.** Not even to add a "narrow rule". Not even with user confirmation prompted inside this skill. The skill emits suggestions as text; the user actions them themselves.
2. **NEVER call `Edit` / `Write` / `NotebookEdit` on any settings file** during this skill's execution.
3. **NEVER invoke `Bash` to mutate a settings file.** No `jq -i`, no `>>`, no `>`, no `tee`, no `sed -i`, no `cp` overwriting the file. Read-only `Bash` (e.g. `cat`) is fine but prefer the `Read` tool.
4. **NEVER create a new settings file that didn't exist** (e.g. don't materialise `./.claude/settings.json` when only the global one was present).
5. **NEVER substitute a "safer alternative" by guessing a command the user didn't mention.** Report what was asked.

If you find yourself about to write to `settings.json` — via any tool, including `Bash` redirection or `jq -i` — you are violating this skill. Stop, emit the report instead.

## Inputs

The skill receives either:

- An explicit command list (e.g. `["git:*", "rm:*", "npm run lint"]`), or
- A task description from which to infer the command set

**Inference rule:** if given a task description, derive the command set yourself. Do NOT ask the user "what commands do you need" — the inference is the skill's job. A task like "refactor frontend, delete old files, run lint + tsc + build" infers to:

```
git:*           (commits)
rm:*            (delete old files)
npm:*           (lint/tsc/build via npm scripts)
node:*          (if scripts shell out)
```

Be deliberately broad on inference — better to report a false-positive "needed" command than miss a real gap.

**Include post-task bring-up, not just the task's own pipeline.** If the deliverable will be *manually run* after the task (dev server, port bind, browser/Cypress, anything needing `dangerouslyDisableSandbox`), infer those commands too and flag them as sandbox-bypass (below). A task that builds an app but whose verification pipeline is only `lint/tsc/test/build` still implies a `dev`-server bring-up the user will hit — auditing only the pipeline produces a misleading "0 gaps" that ignores the approvals actually coming.

## Process

1. **Resolve settings paths:**
   - Global: `~/.claude/settings.json`
   - Project: `<cwd>/.claude/settings.json` (only if present)

2. **Read settings (Read tool, NOT Edit).** Extract `permissions.allow`, `permissions.deny`, and `permissions.ask` arrays.

3. **Compare each required command** against allow entries using prefix-match semantics:
   - `Bash(git:*)` in allow → matches `git status`, `git commit`, etc.
   - `Bash(npm:*)` in allow → matches all `npm` invocations
   - `Bash(rm:*)` in allow → matches all `rm` invocations
   - No match → blocked

4. **Categorise each command:**
   - ✅ **allowed** — matched in global or project allow
   - ⚠️ **blocked** — no match in any allow, no explicit deny
   - 🛑 **denied** — explicit deny rule (cannot be worked around safely)
   - 🔵 **partial** — allow rule narrower than required (e.g. `Bash(rm:./tmp/*)` exists but task wants generic `rm:*`)
   - 🔓 **sandbox-bypass** — command runs only with `dangerouslyDisableSandbox: true` (dev servers, port binds, Cypress/Electron, `*.local` curls). These **always prompt for approval by design** — widening `settings.json` does NOT silence them. List them separately so a clean allow-list is not mistaken for zero approvals. This is a *prompt expectation*, not a gap to fix.

5. **Emit report** to the conversation (text output, NOT a file write).

## Output format

```
PERMISSION REPORT
─────────────────
Source: ~/.claude/settings.json [+ ./.claude/settings.json if read]

✅  git:*           allowed (global)
✅  npm:*           allowed (global)
⚠️  rm:*            BLOCKED — not in any settings.json
    → Suggested: add "Bash(rm:*)" to ~/.claude/settings.json
🔵  npm run test:*  PARTIAL — allow has "Bash(npm run lint)" but not "npm run test"
    → Suggested: widen to "Bash(npm run:*)" or add "Bash(npm run test:*)"
🛑  curl:*          DENIED (global) — explicit deny rule; no allow can override
    → Cannot work around this safely. Caller should use a different approach.
🔓  pnpm dev        SANDBOX-BYPASS — runs only with dangerouslyDisableSandbox (binds a port)
    → Will prompt for approval at bring-up every time. NOT fixable via settings.json.

2 blocked / 1 partial / 1 denied / 1 sandbox-bypass.

(Advisory only — settings not modified. Caller decides next step.)
```

When zero allow-list gaps: emit a short "✅ no gaps" report — **but still list any 🔓 sandbox-bypass commands** with the note that they will prompt at run time. "0 gaps" must never imply "0 approvals": if the deliverable gets manually run, say so explicitly so the caller is not surprised by approval prompts a clean allow-list did not predict.

## What "advisory only" means

- The report goes to stdout (the conversation).
- The user reads it. They decide whether to widen their allow-list or accept the gaps and use fallbacks.
- If invoked by a caller as a gate, the report is shown to the user; the user may bypass and proceed.
- If invoked via PreToolUse hook on `Task`, the report fires before each `Agent` dispatch and is non-blocking (always exit 0).

## Common rationalisations to refuse

| Thought | Reality |
|---------|---------|
| "I'll just add `rm:*` to settings — it's tiny" | NEVER. Emit suggestion. User decides. |
| "The user clearly wants the gap fixed" | They want the report. The fix is theirs. |
| "I'll edit settings.json and undo it after" | NEVER. No mutation under any condition. |
| "This is just planning, skip the structured check" | Run the structured check. Output the report. |
| "User said 'quickly dispatch' — skip the gate" | Skip nothing. The gate is fast. |
| "I'll ask the user what commands they need" | Don't ask. Infer from task description. |
| "I'll write the new settings to a temp file and tell the user to `mv` it" | Mutation by proxy. Just emit the JSON snippet in the report; the user copies it. |
| "The settings file is malformed, I'll fix it while reading" | No. Surface the parse error in the report and stop. |
| "It's only a `.bak` / project copy — not the real file" | No writes to anything inside `.claude/` during this skill. |
| "User explicitly said inside this skill: 'go ahead and add it'" | Skill is advisory by contract. Direct them to action it outside the skill — don't override the no-mutation rule mid-run. |
| "I'll use `jq -i` / `>>` / `sed -i` — that's not the `Edit` tool" | Same mutation. Same prohibition. The rule is by-effect, not by-tool. |

## Caller integration

Callers should:

1. Invoke this skill with the inferred command set (or task description).
2. Receive the text report.
3. Show it to the user.
4. Decide based on user response whether to proceed, halt, or rely on fallbacks.

This skill itself does not gate any action — it only reports. Gating is the caller's job.
