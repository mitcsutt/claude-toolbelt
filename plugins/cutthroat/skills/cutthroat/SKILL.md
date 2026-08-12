---
name: cutthroat
description: Use when the user wants to control the cutthroat output style mid-session, or to apply it to a document. Fires on "/cutthroat", "cutthroat off", "stop cutthroat", "normal mode", "be cutthroat", "cutthroat this", "that document is too long", "trim this doc", "too verbose", or when the user asks how cutthroat is switched on or off.
---

# cutthroat control

The `cutthroat` output style is active when `~/.claude/settings.json` contains
`"outputStyle": "cutthroat:cutthroat"`. It is a system-prompt layer, so it is
read once at session start.

## Applying cutthroat to a document

By default the style governs terminal prose only — documents, specs, plans,
commits, and code are exempt so they keep their normal register.

When the user points it at an artifact — "be cutthroat", "cutthroat this",
"that doc is too long, trim it" — the exemption is overridden **for that piece
of work**. Apply the full ruleset to that artifact: cut preamble, narration,
recap, hedging and filler; keep every reason, edge case, step, number, command
and error verbatim. Report what you cut. Do not touch the artifact's required
structure if a skill or template defines one.

The override lasts for that artifact only. Do not carry it into the next one.

## Standing down for the session

If the user says "cutthroat off", "stop cutthroat", or "normal mode": stop
applying the style for the rest of this session and confirm in one line.

State the limitation plainly if it matters: this is a request to the model, not
a change to the system prompt. The style is still loaded. The hard switch is
below.

## Full depth for one turn

No skill needed. `explain`, `why`, `details`, `expand`, or `walk me through` in
the user's message opens up full depth for that turn. That is built into the
style.

## Turning it on or off for real

Edit `~/.claude/settings.json` by hand:

```json
{ "outputStyle": "cutthroat:cutthroat" }
```

Set it to `"Default"` to turn it off. Either way it takes effect after `/clear`
or a new session — the output style is part of the system prompt, which Claude
Code reads once at session start.

Do **not** use `/config` to pick it. `/config` writes the selection to
project-local `.claude/settings.local.json`, which scopes the style to one repo.
