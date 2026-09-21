---
name: cutthroat
description: Use when the user points cutthroat at a document or artifact to trim it. Fires on "/cutthroat", "be cutthroat", "cutthroat this", "that document is too long", "trim this doc", "this is too verbose", "tighten this up", or when the user asks how to disable the plugin's subagent brief or final-message gate.
---

# cutthroat

Terminal register is the built-in `Concise` output style, plus the "Agent
voice" rules in `shared-rules.md`. This skill is for the other thing: pointing
the same discipline at an artifact on demand.

## Trimming an artifact

Documents, specs, plans, commits, PR bodies, code, code comments and
translation strings keep their normal register by default — a terse spec is a
bad spec. That exemption is a default, not a prohibition. When the user points
at one, apply the full ruleset to that artifact for that piece of work:

**Cut.** Openers, running narration, closing recap (prose that restates what a
diff, table or code block already shows), closing offers, filler (`just`,
`really`, `basically`, `actually`, `simply`), hedging tails, restating the
request back, and menus of options nobody will pursue.

**Never cut.** Code, error text verbatim, commands and flags, numbers,
`file:line` references, security and destructive-action warnings, multi-step
sequences where fragment order risks a misread, and the reasons, edge cases and
steps of any procedure. The single test: never cut information that changes the
answer or the reader's next decision.

**Grammar stays normal.** Complete, ordinary sentences. No dropped articles, no
dropped copulas, no telegraphic fragments, no abbreviating ordinary words.
Compression is structural, never grammatical — degraded prose reads as a
degraded thinker, costs the reader more to parse, and saves almost nothing.

**Structure a skill or template defines wins.** Do not trim a required section
out of a TRD, a PR template, or anything CI-gated.

Report what you cut, in one line. The override lasts for that artifact only;
do not carry it into the next one.

## Disabling what the plugin enforces

Two hooks, independent of each other and of the output style:

- **`SubagentStart` brief** — injects report discipline into every spawned
  subagent, since output styles never reach them. Set `CUTTHROAT_SUBAGENT=off`
  in the `env` block of `~/.claude/settings.json`.
- **`Stop` gate** — one fast-model call per stop-point that blocks the turn
  once if the final message refers to open questions, decisions or results
  without stating them. No env-var switch; prompt hooks cannot read the
  environment.

Disabling the plugin stops both.
