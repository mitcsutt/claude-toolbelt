# cutthroat

Report discipline: say it once, say it fully, say it where it will be read.

## Why

Claude's default register opens with a warm-up, narrates while it works, and
closes by re-summarising the diff you are about to read. That part is now
handled by the built-in `Concise` output style, so this plugin no longer ships
a style of its own — see [Install](#install) for the migration.

What `Concise` does not handle is where the words land.

**Subagents never see an output style.** A subagent runs its own system
prompt, so no style or `CLAUDE.md` register reaches it. Its final message lands
in the parent conversation as a tool result and is re-sent with every later API
call, so subagent padding is a recurring cost, not a one-off. A
`SubagentStart` hook injects a short report brief into every spawned agent
instead.

**There is no single final message once subagents run.** Every subagent return
arrives as a new turn, so the assistant stops several times per unit of work
and each stop is a de facto last message. The measured failure is that the
standing asks get stated in full once, at the first stop-point, and every later
stop-point carries them forward as a pointer: "still waiting on those five
questions", "your two decisions are recorded", "the double-worktree
redundancy". By the time you read the last message, the thing it points at is
four messages and forty minutes back.

The Claude Code system prompt already asks for a last message that stands on
its own. Every measured failure postdates it, across three modes: by-reference
asks, open items compressed into coined labels, and by-reference results ("as
reported above"). A `Stop` hook enforces it instead of asking for it.

## What it ships

| Component | Kind | What |
| --- | --- | --- |
| `SubagentStart` hook | hook | Injects a short report brief into every spawned subagent, since output styles never reach them. |
| `Stop` hook | hook | Blocks the turn once when the final message refers to open questions, decisions, items or results instead of stating them. |
| `cutthroat` skill | skill | Points the same discipline at a document on request — "trim this doc", "too verbose". |

### The Stop gate

One call to the default fast model per stop-point reads `last_assistant_message`
and blocks (`{"ok": false}`) if any of:

- **(a)** the message says it is waiting on, blocked on, or needs your answer,
  decision, go-ahead or approval, and does not state each such item in full in
  that same message;
- **(b)** it lists open, remaining, deferred or leftover items as bare labels
  with no one-line meaning;
- **(c)** it says a result, reason, decision or finding is "above", "earlier",
  "as discussed", "already reported" or "recorded" instead of stating it.

Anything else passes, including a message that asks nothing of you. On a block,
the reason is fed back as Claude's next instruction: resend with a `Waiting on
you` section listing every open question and decision in full with its options.

The prompt returns `{"ok": true}` unconditionally when `stop_hook_active` is
`true`, so the gate can extend a turn by at most one message. Claude Code's own
cap is 8 consecutive blocks; this never approaches it.

### Scope of the skill

Documents, specs, plans, commits, PR bodies, code, code comments and
translation strings keep their normal register by default — a terse spec is a
bad spec. That exemption is a default, not a prohibition: say "be cutthroat" or
"that doc is too long, trim it" and it applies to that artifact for that piece
of work.

### What it does not do

- No terminal output style. Whatever register you run — the built-in `Concise`
  style, a custom one, or the harness default — sets verbosity; this plugin
  sets none.
- No token-cost optimisation. The benefit is reading time and signal density.
- No tool-output or log compression.
- Nothing an author-voice tool owns. This plugin governs the agent's own
  reporting, not text drafted as a person to other people.

## Install

```bash
/plugin marketplace add mitcsutt/claude-toolbelt
/plugin install cutthroat@claude-toolbelt
```

Both hooks are registered by the plugin and take effect on the next session.
Nothing needs to go in `~/.claude/settings.json`.

**Migrating from 1.x.** 1.x shipped a `cutthroat` output style activated with
`"outputStyle": "cutthroat:cutthroat"`. That style is gone: a concise output
style now covers seven of its nine sections at least as strongly, and two of
them actively conflicted with the final-message rule this version enforces. If
you were on `cutthroat:cutthroat`, switch to whatever register you prefer — the
built-in `Concise` style is the closest equivalent (`"outputStyle": "Concise"`
in `~/.claude/settings.json`). The eight rules a concise register does not
carry — the anti-sycophancy stance, state-scope-not-time, the completion block,
the blocker template, the debug-loop override, Mermaid-past-three-parts, the
destructive-action scope-of-loss line, and the protocol-opener carve-out — are
listed as plain rules in the author's own `shared-rules.md` (the companion
import at this repo's root; it is not shipped with the plugin), and hold under
any style.

## Usage

Both hooks are passive. The skill fires on "be cutthroat", "cutthroat this",
"trim this doc", "that document is too long", "too verbose".

## Configuration

| Key | Where | Effect |
| --- | --- | --- |
| `CUTTHROAT_SUBAGENT=off` | `env` block of `~/.claude/settings.json` | Stops the `SubagentStart` brief. |

The `Stop` gate has no env-var switch: prompt hooks receive only the hook input
JSON and cannot read the environment. Disabling the plugin stops both hooks.

**Cost.** The gate is one fast-model call per stop-point, 30s timeout. That is
per *stop*, not per prompt — a turn with three subagent returns costs three
calls.

**Known limits.** The gate is model-judged, so expect the occasional false pass
on a subtle reference and the occasional false block. It does not fire between
tool calls, by design; those are progress updates. It never sees an
`AskUserQuestion` turn, because the tool blocks before `Stop` fires — which is
the desired path anyway.

## Tests

```bash
bash plugins/cutthroat/tests/all.sh
```

Structural only, and free to run. Whether the gate actually changes the model's
final message is graded by `evals/final-message-restates-open-asks`, which costs
money per run — see [`docs/testing.md`](../../docs/testing.md).

## Related

- The author's `shared-rules.md` (companion import at this repo's root, not
  shipped with the plugin) — its "Agent voice" and "Final message discipline"
  sections carry the same rules in prose, including the conventions no hook
  enforces: route decision batches through `AskUserQuestion`, orchestrators
  speak once per wave, the plan file holds open decisions but the message still
  restates them.
- Author-voice tooling (e.g. a `my-voice` plugin, kept separately) — for text
  drafted as a person to other people, which this plugin does not touch.
