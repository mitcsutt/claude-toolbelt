# claude-toolbelt

A small kit of Claude Code plugins and scripts I use day-to-day. Each piece is intentionally narrow and composable — install only the ones you want.

## What's inside

### Plugins

| Plugin             | What it does                                                                                                                                  |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `permissions`      | Logs every tool call and adds `/permissions-seed`, `/permissions-audit`, `/permissions-promote`, `/permissions-advisor` skills to turn patterns into allow rules that bypass Auto-mode classifier (plus a prospective pre-flight command check). |
| `find-docs`        | Pulls fresh library docs, code examples, and does people/company research via Context7 and Exa MCPs.                                          |
| `session-timeline` | Generates a self-contained HTML visualization of a Claude Code session — stats, tool usage, subagent cards, chronological timeline.            |
| `agent-loop`         | Autonomous coding loop for long-running multi-task work — a bash harness runs a fresh headless tick per task (OS-level context reset), with sprint contracts, blocker taxonomy, and a live browser dashboard. Requires `superpowers`; bundles `postmortem` and uses the `permissions` plugin's `/permissions-advisor`. |
| `postmortem`         | Generic structured retrospective generator — writes an 8-section postmortem to `docs/postmortems/` after any significant task. |
| `cutthroat`          | Detail-preserving concise output style — compresses structure (preamble, narration, closing recap, filler), never grammar and never technical substance. Scoped to terminal prose, with an explicit override for documents. Extends report discipline to subagents via a `SubagentStart` hook, which output styles never reach. |

### Scripts

The statusline's [ccstatusline](https://github.com/sirmalloc/ccstatusline) custom-command widgets live alongside the tracked layout in `config/ccstatusline/` (config is `settings.json`). All require `jq` and `git` on PATH.

| Script            | What it does                                                                                                                                   |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `ccsl-model.sh`   | Model name. Prepends an AWS glyph (orange) when `CLAUDE_CODE_USE_BEDROCK=1`, flagging a Bedrock-routed session.                                |
| `ccsl-dir.sh`     | Combined repo-root / worktree identity — repo dir name, or `⎇ <worktree>` in a worktree. The name is a click-to-open hyperlink; scheme set by `$CCSL_EDITOR`. |
| `ccsl-sandbox.sh` | Sandbox state (`sandbox` / `sandbox:auto`), or nothing when not sandboxed.                                                                     |

## Install (plugins)

Add this marketplace, then install whichever plugins you want:

```text
/plugin marketplace add mitcsutt/claude-toolbelt
/plugin install permissions@claude-toolbelt
/plugin install find-docs@claude-toolbelt
/plugin install session-timeline@claude-toolbelt
/plugin install agent-loop@claude-toolbelt
/plugin install postmortem@claude-toolbelt
/plugin install cutthroat@claude-toolbelt
```

## Shared rules (`shared-rules.md`)

`shared-rules.md` is a portable set of agent-behavior rules (verification, minimal-diff, git, bash, etc.) that I import into my user-level `CLAUDE.md` so every project picks them up. The voice rules that used to live here moved out into the `cutthroat` plugin.

Claude Code resolves `@<path>` lines in `CLAUDE.md` as file imports — the referenced file's contents are loaded into the agent's context just as if they lived inline.

1. Clone this repo somewhere stable (e.g. `~/Documents/projects/claude-toolbelt`).
2. Add one line to `~/.claude/CLAUDE.md`:

   ```md
   @~/Documents/projects/claude-toolbelt/shared-rules.md
   ```

3. Restart Claude Code (or start a new session). The rules now apply to every project.

Per-project override: drop the same `@…` line into a repo's `./CLAUDE.md` to pull the rules in only for that repo, or paired with project-specific additions.

## Install (statusline)

The statusline uses [ccstatusline](https://github.com/sirmalloc/ccstatusline); the layout is a tracked config at `config/ccstatusline/settings.json`. Claude Code's `statusLine` config is global, not plugin-level.

1. Install ccstatusline globally: `npm install -g ccstatusline` (or `bun add -g ccstatusline`).
2. Symlink the tracked config so ccstatusline and its TUI read/write it in place:

   ```bash
   mkdir -p ~/.config/ccstatusline
   ln -sfn /path/to/claude-toolbelt/config/ccstatusline/settings.json ~/.config/ccstatusline/settings.json
   ```

3. Point Claude Code at the pinned binary in `~/.claude/settings.json`. Use an **absolute** `node <script>` command so it survives nvm node-version switches (find the paths with `command -v ccstatusline` and `readlink -f`):

   ```json
   {
     "statusLine": {
       "type": "command",
       "command": "/abs/path/to/node /abs/path/to/ccstatusline.js",
       "padding": 0,
       "refreshInterval": 10
     }
   }
   ```

   (`refreshInterval` requires Claude Code ≥ 2.1.97.)

Edit the layout with the ccstatusline TUI (`ccstatusline`) — changes write through the symlink into the tracked config. The custom widgets are the three `config/ccstatusline/ccsl-*.sh` scripts (see [Scripts](#scripts)); they need `jq` and `git` on PATH.

### Statusline env overrides

These are read at render time from the environment Claude Code passes to the statusline. Set them in your **personal** env (e.g. an `"env"` block in `~/.claude/settings.json`, or your shell rc) — not in this shared repo — so the tracked config stays neutral for everyone.

| Variable                 | Effect                                                                                                          | Default              |
| ------------------------ | --------------------------------------------------------------------------------------------------------------- | -------------------- |
| `CCSL_EDITOR`            | Scheme for the `ccsl-dir.sh` folder link: `file` (OS default), `vscode`, or `cursor`. Unknown values fall back to `file`. | `file` (`file://…`)  |
| `CLAUDE_CODE_USE_BEDROCK`| When `1`, `ccsl-model.sh` prefixes the model with an AWS glyph. (Claude Code's own Bedrock switch — reused here.) | unset                |

Example — VS Code links for yourself only, in `~/.claude/settings.json`:

```json
{ "env": { "CCSL_EDITOR": "vscode" } }
```

Notes: the folder link is an OSC 8 hyperlink, so the terminal must honour the chosen scheme on click (iTerm2 and the VS Code integrated terminal do; some terminals only linkify `http(s)`/`file`). The AWS glyph needs a Nerd Font, and its orange uses truecolor (24-bit).

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
- `/permissions-advisor` — prospective pre-flight: infer the commands a task will need and check them against your allow-list (read-only, never writes)

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

### `cutthroat`

An output style that compresses the structure of terminal prose — preamble, narration, closing recap, filler — never grammar and never technical substance. Full ruleset in [`plugins/cutthroat/README.md`](plugins/cutthroat/README.md).

Activation is a hand-edited `"outputStyle": "cutthroat:cutthroat"` in `~/.claude/settings.json`. Do **not** use `/config` to select it — `/config` writes the selection to project-local `.claude/settings.local.json`, scoping the style to one repo instead of applying globally. Takes effect after `/clear` or a new session, since an output style is part of the system prompt and is read once at session start.

Set it to `"Default"` to stop the style. That does **not** stop the `SubagentStart` hook the plugin registers — it's gated only by `CUTTHROAT_SUBAGENT=off` in the `env` block of `~/.claude/settings.json`. Disabling the plugin stops both.

## License

MIT
