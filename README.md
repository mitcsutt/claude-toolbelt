# claude-toolbelt

A small kit of Claude Code plugins and scripts I use day-to-day. Each piece is intentionally narrow and composable — install only the ones you want.

[![CI](https://github.com/mitcsutt/claude-toolbelt/actions/workflows/ci.yml/badge.svg)](https://github.com/mitcsutt/claude-toolbelt/actions/workflows/ci.yml)

## What's inside

| Plugin             | What it does                                                                                                                                  |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `permissions`       | Logs every tool call and gives you skills to turn the patterns into `settings.json` allow rules that bypass Auto-mode's classifier — plus a read-only, prospective pre-flight check. |
| `find-docs`         | Pulls fresh library docs, code examples, and does people/company research via Context7 and Exa MCPs.                                          |
| `session-timeline`  | Generates a self-contained HTML visualization of a Claude Code session — stats, tool usage, subagent cards, chronological timeline.            |
| `agent-loop`        | Autonomous coding loop for long-running multi-task work — a bash harness runs a fresh headless tick per task (OS-level context reset), with sprint contracts, blocker taxonomy, and a live browser dashboard. Requires `superpowers`; bundles `postmortem` and uses the `permissions` plugin's `/permissions-advisor`. |
| `postmortem`        | Generic structured retrospective generator — writes an 8-section postmortem to `docs/postmortems/` after any significant task. |
| `cutthroat`         | Detail-preserving concise output style — compresses structure (preamble, narration, closing recap, filler), never grammar and never technical substance. Scoped to terminal prose, with an explicit override for documents. Extends report discipline to subagents via a `SubagentStart` hook, which output styles never reach. |
| `orchestrate`       | Token-frugal orchestration doctrine for an expensive top-level model — reserve the expensive tier for judgment, route token-heavy bounded work to the cheapest capable subagent tier, demand compact traceable returns, and vet before acting. Model- and harness-agnostic; skill only. |
| `git`               | A home for narrow git/GitHub helper skills. Currently ships `gh-pending-review` — add inline comments to a PR that already has a pending review, via GraphQL, without orphaning comments drafted elsewhere. |

## Install

Add this marketplace, then install whichever plugins you want:

```text
/plugin marketplace add mitcsutt/claude-toolbelt
/plugin install permissions@claude-toolbelt
/plugin install find-docs@claude-toolbelt
/plugin install session-timeline@claude-toolbelt
/plugin install agent-loop@claude-toolbelt
/plugin install postmortem@claude-toolbelt
/plugin install cutthroat@claude-toolbelt
/plugin install orchestrate@claude-toolbelt
/plugin install git@claude-toolbelt
```

## Plugin details

### [`permissions`](plugins/permissions/README.md)

A thin observability layer that complements Claude Code's built-in Auto mode: three hooks log every tool call, prompt, and detected sandbox denial, and seven skills turn that history into allow rules — `/permissions-seed` (curated baseline from recipes), `/permissions-audit` (surface classifier-hitting patterns), `/permissions-promote` (turn a hitter into a rule), `/permissions-lint` (catch matcher-syntax pitfalls and conflicts), `/permissions-bootstrap-project` (project-scoped rules), `/sandbox-fix` (fixes from logged sandbox denials), and `/permissions-advisor` (read-only, prospective pre-flight check against a task's inferred commands).

### [`find-docs`](plugins/find-docs/README.md)

Two MCPs glued together so you stop guessing at API surface area: **Context7** for curated, version-specific library docs, **Exa** for everything else (web search, code context, people/company research). The `/find-docs` skill routes each question to a backend before the first tool call, capped at 3 calls per question; `/pin-context7-libs` seeds and refreshes a project's Context7 library table so lookups skip the resolve step for known dependencies.

### [`session-timeline`](plugins/session-timeline/README.md)

Drops a single self-contained HTML file you can open offline, correlating subagent transcripts back into the main timeline. Useful for:

- Reviewing what a long session actually did
- Debugging where a subagent went off the rails
- Sharing session shape without sharing raw transcripts

### [`agent-loop`](plugins/agent-loop/README.md)

An autonomous coding loop for long-running, multi-task work. A bash harness (`run.sh`) re-invokes a fresh headless Claude tick per task, giving an OS-level context reset between tasks instead of one long session accumulating drift. Each tick runs a Planner/Scout/Worker/Evaluator pipeline behind sprint contracts, a blocker taxonomy, and parent-side verification, with a live browser dashboard for progress and usage. Requires the `superpowers` plugin; bundles `postmortem` for close-out and uses the `permissions` plugin's `/permissions-advisor` when available.

### [`postmortem`](plugins/postmortem/README.md)

Structured retrospective generator. `/postmortem` interviews you about a completed task and writes a searchable 8-section document to `docs/postmortems/`, so past incidents and their causes stay greppable instead of lost in chat scrollback.

### [`cutthroat`](plugins/cutthroat/README.md)

An output style that compresses the structure of terminal prose — preamble, narration, closing recap, filler — never grammar and never technical substance. Extends the same report discipline to subagents via a `SubagentStart` hook, since output styles never reach them. Full ruleset, plus activation and disable mechanics, in [`plugins/cutthroat/README.md`](plugins/cutthroat/README.md).

### [`orchestrate`](plugins/orchestrate/README.md)

Doctrine for an expensive top-level model that should spend its tokens on judgment, not on token-heavy bounded work. `/orchestrate` walks Step 0 (decide whether to orchestrate at all — most fixes stay single-threaded), routing each slice to the cheapest capable tier by judgment demand, a five-part handoff packet, compact returns that stay traceable to a source, parallel-width limits, and a verification gate before claiming done. Model- and harness-agnostic — it names no specific models or tools.

### [`git`](plugins/git/README.md)

A home for narrow git/GitHub helper skills, installed as one plugin. Currently ships `gh-pending-review`, which appends inline comments to a GitHub PR's existing pending review via the GraphQL `addPullRequestReviewThread` mutation — the REST API rejects a second pending review and can orphan comments drafted in another tool. Future git/GitHub skills are added as siblings under `skills/`.

## Statusline

The statusline uses [ccstatusline](https://github.com/sirmalloc/ccstatusline); the layout is a tracked config at `config/ccstatusline/settings.json`. Claude Code's `statusLine` config is global, not plugin-level.

1. Install ccstatusline globally: `npm install -g ccstatusline` (or `bun add -g ccstatusline`).
2. Symlink the whole tracked directory to ccstatusline's config location, so both the layout and the widget scripts resolve from it:

   ```bash
   # If ~/.config/ccstatusline already exists, move it aside first —
   # `ln -sfn` onto an existing directory nests the link inside it.
   ln -sfn /path/to/claude-toolbelt/config/ccstatusline ~/.config/ccstatusline
   ```

   The directory (not just `settings.json`) is linked on purpose. The widget entries in the tracked config reference their scripts as
   `"${CCSL_HOME:-$HOME/.config/ccstatusline}/ccsl-<name>.sh"`, so with this symlink in place they resolve wherever you cloned the repo — no absolute paths, no env var. ccstatusline reads and writes through the symlink, so its TUI still edits the tracked config in place.

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

Edit the layout with the ccstatusline TUI (`ccstatusline`) — changes write through the symlink into the tracked config. The custom widgets are the three `config/ccstatusline/ccsl-*.sh` scripts below; they need `jq` and `git` on PATH.

### Scripts

| Script            | What it does                                                                                                                                   |
| ----------------- | -----------------------------------------------------------------------------------------------------------------------------------------------|
| `ccsl-model.sh`   | Model name. Prepends an AWS glyph (orange) when `CLAUDE_CODE_USE_BEDROCK=1`, flagging a Bedrock-routed session.                                |
| `ccsl-dir.sh`     | Combined repo-root / worktree identity — repo dir name, or `⎇ <worktree>` in a worktree. The name is a click-to-open hyperlink; scheme set by `$CCSL_EDITOR`. |
| `ccsl-sandbox.sh` | Sandbox state (`sandbox` / `sandbox:auto`), or nothing when not sandboxed.                                                                     |

### Statusline env overrides

These are read at render time from the environment Claude Code passes to the statusline. Set them in your **personal** env (e.g. an `"env"` block in `~/.claude/settings.json`, or your shell rc) — not in this shared repo — so the tracked config stays neutral for everyone.

| Variable                 | Effect                                                                                                          | Default              |
| ------------------------ | --------------------------------------------------------------------------------------------------------------- | -------------------- |
| `CCSL_HOME`              | Directory holding the `ccsl-*.sh` widget scripts. Only needed if you did **not** symlink the whole directory as in [Statusline](#statusline) — e.g. you linked just `settings.json`, or keep the scripts elsewhere. | `$HOME/.config/ccstatusline` |
| `CCSL_EDITOR`            | Scheme for the `ccsl-dir.sh` folder link: `file` (OS default), `vscode`, or `cursor`. Unknown values fall back to `file`. | `file` (`file://…`)  |
| `CLAUDE_CODE_USE_BEDROCK`| When `1`, `ccsl-model.sh` prefixes the model with an AWS glyph. (Claude Code's own Bedrock switch — reused here.) | unset                |

Example — VS Code links for yourself only, in `~/.claude/settings.json`:

```json
{ "env": { "CCSL_EDITOR": "vscode" } }
```

Notes: the folder link is an OSC 8 hyperlink, so the terminal must honour the chosen scheme on click (iTerm2 and the VS Code integrated terminal do; some terminals only linkify `http(s)`/`file`). The AWS glyph needs a Nerd Font, and its orange uses truecolor (24-bit).

## Shared rules

[`shared-rules.md`](shared-rules.md) is a portable set of agent-behavior rules I import into my user-level `CLAUDE.md` so every project picks them up. The voice rules that used to live here moved out into the `cutthroat` plugin.

Claude Code resolves `@<path>` lines in `CLAUDE.md` as file imports — the referenced file's contents are loaded into the agent's context just as if they lived inline.

1. Clone this repo somewhere stable (e.g. `~/Documents/projects/claude-toolbelt`).
2. Add one line to `~/.claude/CLAUDE.md`:

   ```md
   @~/Documents/projects/claude-toolbelt/shared-rules.md
   ```

3. Restart Claude Code (or start a new session). The rules now apply to every project.

Per-project override: drop the same `@…` line into a repo's `./CLAUDE.md` to pull the rules in only for that repo, or paired with project-specific additions.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for how to propose a plugin, the testing bar, and the versioning policy.

## License

MIT
