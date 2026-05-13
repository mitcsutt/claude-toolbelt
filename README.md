# claude-toolbelt

A small kit of Claude Code plugins and scripts I use day-to-day. Each piece is intentionally narrow and composable — install only the ones you want.

## What's inside

### Plugins

| Plugin             | What it does                                                                                                                                  |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `permissions`      | Logs every tool call and adds `/permissions-seed`, `/permissions-audit`, `/permissions-promote` skills to turn patterns into allow rules that bypass Auto-mode classifier. |
| `find-docs`        | Pulls fresh library docs, code examples, and does people/company research via Context7 and Exa MCPs.                                          |
| `session-timeline` | Generates a self-contained HTML visualization of a Claude Code session — stats, tool usage, subagent cards, chronological timeline.            |

### Scripts

| Script           | What it does                                                                                                                                   |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `statusline.sh`  | Two-line statusline: model, folder, branch, sandbox state, context bar, plan usage, session time, cache hit rate. Caches `ctx%` for hooks.     |

## Install (plugins)

Add this marketplace, then install whichever plugins you want:

```text
/plugin marketplace add mitcsutt/claude-toolbelt
/plugin install permissions@claude-toolbelt
/plugin install find-docs@claude-toolbelt
/plugin install session-timeline@claude-toolbelt
```

## Install (statusline)

The statusline lives as a script, not a plugin — Claude Code's `statusLine` config is global, not plugin-level.

1. Clone or download `scripts/statusline.sh` somewhere stable.
2. Make it executable: `chmod +x /path/to/statusline.sh`.
3. Wire it into `~/.claude/settings.json`:

   ```json
   {
     "statusLine": {
       "command": "/path/to/statusline.sh"
     }
   }
   ```

Requires `jq` and `git` on PATH.

## Plugin details

### `permissions`

Replaces Claude Code's Auto-mode classifier guesswork with a deterministic 4-tier decision engine:

1. Static allow/deny rules from `settings.json`
2. Cached decisions for prior tool calls
3. Pattern matching against seeded rules
4. AI evaluation as last resort

The included skills help you *generate* those rules from real session activity:

- `/permissions-seed` — propose new rules from recent tool calls
- `/permissions-audit` — show which rules fired, which were bypassed
- `/permissions-promote` — promote a cached decision to a permanent rule

### `find-docs`

Two MCPs glued together so you stop guessing at API surface area:

- **Context7** — version-specific docs for libraries (`/tanstack/query`, `/colinhacks/zod`, etc.). Better than web search for SDK / framework questions.
- **Exa** — fast web/code/people/company search when Context7 doesn't have it.

The skill teaches Claude when to reach for each.

### `session-timeline`

Drops a single self-contained HTML file you can open offline. Useful for:

- Reviewing what a long session actually did
- Debugging where a subagent went off the rails
- Sharing session shape without sharing raw transcripts

## License

MIT
