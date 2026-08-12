# cutthroat

Dense, not degraded.

Claude's default register opens with a warm-up, narrates while it works, and
closes by re-summarising the diff you are about to read. `cutthroat` removes
that and nothing else.

## Why not caveman

`caveman` — and `crisp`, despite its README — compress **grammar**: they drop
articles and copulas and emit fragments. The result reads as a degraded
thinker, costs the reader more to parse, and saves very little.

`cutthroat` compresses **structure**: preamble, narration, recap, hedging,
filler, and option menus. Grammar stays normal. Technical substance is
protected explicitly — code, verbatim errors, commands, numbers, `file:line`
references, and the reasons, edge cases and steps of any procedure are never
cut.

## Switching it on

Add to `~/.claude/settings.json` by hand:

```json
{ "outputStyle": "cutthroat:cutthroat" }
```

Takes effect after `/clear` or a new session. Set it to `"Default"` to stop
the terminal style — but the `SubagentStart` hook below keeps running, since
it is registered by the plugin and gated only by `CUTTHROAT_SUBAGENT`, not by
the output style. Disabling the plugin entirely is what stops both.

Do **not** use `/config` — it writes to project-local
`.claude/settings.local.json`, scoping the style to one repo.

## Scope

Governs terminal prose only. Documents, specs, plans, commits, PR bodies, code,
code comments, and translation strings keep their normal register by default —
a terse spec is a bad spec.

That exemption is a default, not a prohibition. Say "be cutthroat" or "that doc
is too long, trim it" and it applies to that artifact for that piece of work.

## Subagents

Output styles never reach subagents — a subagent runs its own system prompt. A
`SubagentStart` hook injects a short report brief into every spawned agent
instead. This matters more than it looks: a subagent's final message lands in
the parent conversation as a tool result and is re-sent with every later API
call, so subagent padding is a recurring cost.

Disable by setting `CUTTHROAT_SUBAGENT=off` in the `env` block of
`~/.claude/settings.json`.

## What it does not do

- No token-cost optimisation. The benefit is reading time and signal density.
- No tool-output or log compression.
- No enforcement hook. One was designed and cut — see the spec for why.
- Nothing `my-voice` owns. `my-voice` governs text addressed to other people;
  `cutthroat` governs text addressed to you.

## Tests

```bash
bash plugins/cutthroat/tests/all.sh
```
