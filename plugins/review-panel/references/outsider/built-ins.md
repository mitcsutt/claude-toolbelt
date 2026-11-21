# outsider built-ins checklist

Verified on 2026-11-20 (Claude Code 2.1.329 docs); re-check live before a finding depends on a volatile fact.

Checklist, not evidence. Before a finding depends on a row, fetch its URL and quote the sentence. Line numbers refer to the page's markdown at the URL as fetched on 2026-11-20 and will drift. The "caveat" column is the constraint under which the built-in does NOT fit; check it against the constraint ledger. Regenerate from the changelog each Claude Code minor release. Each row is outsider's to report, not grumpy-senior's (`method.md`, Overlap rules).

## Claude Code

Page `<page>.md` is `https://code.claude.com/docs/en/<page>.md`. Quotes are rendered text, so the page's own links are dropped. "since" is the version whose changelog entry added it; "—" means older than this checklist tracks.

| # | You might build | Built-in | Quote | Page:line | Since | Caveat |
|---|---|---|---|---|---|---|
| 1 | PreToolUse grep for `rm -rf` or `.env` | `permissions.deny` | "Rules are evaluated in order: deny, then ask, then allow. The first match in that order determines the outcome" | `permissions.md:56` | — | Not a security boundary (`permissions.md:261`); use the sandbox for one. |
| 2 | `.claudeignore` or a hide-secrets hook | `Read` deny rules | "If your project has a `.claudeignore` file, it has no effect, so move its entries into `Read` deny rules." | `permissions.md:347` | — | — |
| 3 | Hook that parses stdin and exits early unless the command matches | hook `if` field | "`if` uses permission rule syntax to match against the tool name and arguments together" | `hooks.md:341`, `:199` | 2.1.85 | Tool events only. |
| 4 | `while sleep` or re-prompting in a session | `/loop`, `CronCreate` | "Run a prompt repeatedly while the session stays open." | `commands.md:108`; `tools-reference.md:27` | — | Dies with the session. |
| 5 | Server cron running `claude -p` | `/schedule` | "Create, update, list, or run cloud scheduled tasks, formerly routines." | `commands.md:138` | — | Runs on cloud infrastructure (`glossary.md:110`). Whether it can reach LAN-only hosts through a self-hosted environment is UNVERIFIED. |
| 6 | SessionStart hook backgrounding `tail -f` | plugin `monitors` | "A monitor is a shell command that runs in the background for the whole session." | `plugins/components.md:536` | 2.1.105 | Not under `-p` (`:552`). |
| 7 | Stop hook blocking until tests pass | `/goal` | "sets a condition that a separate model checks against the conversation after every turn" | `best-practices.md:248`; `commands.md:94` | 2.1.139 | A model judges it, so it isn't a deterministic gate. |
| 8 | With/without-plugin `claude -p` harness plus a judge | `claude plugin eval`, `eval init` | "a suite makes roughly cases × runs agent runs with the plugin and the same number again for the no-plugin baseline" | `plugin-evals.md:46`, `:11` | 2.1.269 | Calls the model and bills usage (`plugin-evals.md:32`). |
| 9 | Version-agreement check plus a git tag script | `claude plugin tag` | "Before tagging, the command checks that the plugin's `plugin.json` and any marketplace entry that lists it agree on the version." | `plugins/cli-reference.md:538` | 2.1.118 | Tag name format is `<name>--v<version>`. |
| 10 | Manifest or frontmatter linter for CI | `claude plugin validate` | "Validate a plugin manifest, a marketplace manifest, or the skills, agents, and commands in a directory, and exit with a code a CI job can act on." | `plugins/cli-reference.md:596` | — | Static only. |
| 11 | Stale or conflicting CLAUDE.md or skill auditor | `/doctor prompt-audit` | "Claude looks for problems such as instructions written for older models, references to files or commands that don't exist, and files that contradict each other." | `memory.md:96` | 2.1.283 | Interactive, model-run. |
| 12 | Transcript miner for unused skills or token cost | `/skill-doctor`; `claude plugin details` | "Run `/skill-doctor` to see what each of your skills costs and how often it gets used" / "Show a plugin's component inventory and its projected token cost." | `skills.md:955`; `plugins/cli-reference.md:359` | 2.1.261; 2.1.139 | Usage scope (this machine or the account) is UNVERIFIED; `/skill-doctor` needs feature-flag fetching (`skills.md:959`). |
| 13 | Script copying AGENTS.md to CLAUDE.md | native AGENTS.md | "works without adding a `CLAUDE.md`, an import, or a setting" | `memory.md:355` | 2.1.277 | Only when no CLAUDE.md exists; with both, only CLAUDE.md loads (`:360`). |
| 14 | Token in a dotfile or hand-edited settings | `userConfig` with `sensitive: true` | "masks input and stores the value in secure storage instead of `settings.json`" | `plugins-reference.md:437` | — | Hooks read it as `CLAUDE_PLUGIN_OPTION_<KEY>` (`:495`). |
| 15 | Plugin state in `~/.<plugin>/` | `${CLAUDE_PLUGIN_DATA}` | "created on first reference and kept across plugin updates" | `plugins-reference.md:556` | — | — |
| 16 | Worktree wrapper for parallel agents | `isolation: worktree`, `/batch` | "Set to `worktree` to run the subagent in a temporary git worktree" | `sub-agents.md:312`; `commands.md:60` | — | — |
| 17 | Hook-appended memory file for a subagent | subagent `memory:` | "Persistent memory scope: `user`, `project`, or `local`. Enables cross-session learning" | `sub-agents.md:308` | — | — |
| 18 | git-stash undo helper | checkpoints, `/rewind` | "Claude Code snapshots files before every edit so a checkpoint can revert them." | `glossary.md:88` | — | Bash edits aren't restored (`:90`). |
| 19 | Fan-out orchestrator for many agents | dynamic workflows | "A dynamic workflow is a JavaScript script that orchestrates many subagents at once." | `workflows.md:13` | — | — |
| 20 | Transcript scanner drafting an allowlist | `/fewer-permission-prompts` | "Scan your transcripts for common read-only Bash and MCP tool calls, then add a prioritized allowlist" | `commands.md:91` | — | — |

## Home-server tooling

These pages are HTML, so there are no line numbers. Pre-cutoff, textbook facts: useful as a checklist, not as eval traps.

| # | You might build | Built-in | Quote | URL | Since | Caveat |
|---|---|---|---|---|---|---|
| H1 | Wait-for-dependency script before starting a container | Compose `depends_on: {<svc>: {condition: service_healthy}}` | "Specifies that a dependency is expected to be "healthy" (as indicated by healthcheck) before starting a dependent service." | https://docs.docker.com/reference/compose-file/services/ | — | Needs a `healthcheck` on the dependency. |
| H2 | Watchdog script restarting a crashed container | Compose `restart: unless-stopped` | "The policy restarts the container irrespective of the exit code but stops restarting when the service is stopped or removed." | https://docs.docker.com/reference/compose-file/services/ | — | Restart order relative to dependencies is UNVERIFIED; check before relying on it. |
| H3 | Cron job plus "catch up missed runs" logic | systemd timer `Persistent=true` | "This is useful to catch up on missed runs of the service when the system was powered down." | https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html | — | "only has an effect on timers configured with `OnCalendar=`". |
