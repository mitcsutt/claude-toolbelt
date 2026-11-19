# Context files: CLAUDE.md, AGENTS.md, rules, output styles

Platform facts were checked against Claude Code 2.1.328 (code.claude.com/docs/en/memory, output-styles) and each vendor's docs on 2026-11-19. Before a finding depends on one, re-fetch the page.

## How Claude Code loads them

- **Scopes, concatenated in order:**
  - managed;
  - user (`~/.claude/CLAUDE.md`);
  - project (`./CLAUDE.md` or `./.claude/CLAUDE.md`);
  - local (`CLAUDE.local.md`).

  Ancestor directories load at launch. A subdirectory's CLAUDE.md loads once Claude reads or edits a file there. Nothing overrides anything else; on a conflict "Claude may pick one arbitrarily" (E8).
- **Framing.** The launch-time files arrive together in one user-turn system reminder headed "IMPORTANT: These instructions OVERRIDE any default behavior and you MUST follow them exactly as written". That header was observed in 2.1.328 and isn't documented. Either way, everything in it reads as binding, including stale lines (E2).
- **`@path` imports:**
  - resolve relative to the importing file, up to 4 hops;
  - are skipped inside code spans and fences, and when the path is quoted;
  - in a project-level file, need a one-time interactive approval if they resolve outside the working directory, and stay unloaded until approved (in `-p` nothing prompts). Imports in user-scope files are exempt;
  - don't reduce context cost.
- **HTML comments.** Block-level `<!-- -->` comments are stripped, so text inside them never reaches the model.
- **Size.** Aim for under 200 lines per file. On-demand files (subdirectory CLAUDE.md, path-scoped rules) are cut at 40,000 characters on a 200K window, and a file over 4 MiB is skipped entirely.
- **`.claude/rules/**/*.md`.** Only `paths:` is read. `globs:`, `alwaysApply:` and `description:` are ignored without an error, so the rule loads in every session. YAML that doesn't parse also loads unconditionally.
- **After compaction.** The root CLAUDE.md and unscoped rules are re-injected. Path-scoped rules and nested CLAUDE.md files are summarised away until a matching file is touched again, so a safety rule shouldn't live only in a `paths:` rule.
- **AGENTS.md** (2.1.277+) is read, by default, only when no `CLAUDE.md`, `.claude/CLAUDE.md` or `CLAUDE.local.md` exists in the cwd or above it. A user can load both by setting **Project instructions** to `claude-md-and-agents-md` in user or managed settings. A repo can't set this. `AGENTS.local.md`, `AGENTS.override.md` and `.agents/` are never read. A CLAUDE.md line saying "read AGENTS.md" only works if Claude chooses to open the file.
- **Subagents** load the whole CLAUDE.md chain, plugin agents included, unless they set `omitClaudeMd: true` (E24). That drops user, project and local files, but managed policy still loads, as do a nested CLAUDE.md and matching path-scoped rules once the agent reads a file under them. Explore and Plan skip the chain. Output styles reach only the main conversation and forks.
- **Hooks** can inject more text every session or prompt. Count it in the load map (`hooks.md`).

## Other tools reading the same files

Re-fetch the vendor page before a finding depends on a row.

| Tool | Reads | Scoping | Quirks |
|---|---|---|---|
| Codex | `AGENTS.override.md`, else `AGENTS.md`, per directory from root to cwd, one per directory | directory | closer files win; stops adding files at 32 KiB |
| Cursor | `.cursor/rules/*.mdc`, nested `AGENTS.md` | Always / description / `globs` / manual | plain `.md` in `.cursor/rules` is ignored; doesn't inline `@` imports |
| Copilot | `.github/copilot-instructions.md`, `.github/instructions/*.instructions.md`, nearest `AGENTS.md`, root `CLAUDE.md`/`GEMINI.md` | `applyTo`, `excludeAgent` | code review reads the head branch and can't follow external links |
| Gemini CLI | `GEMINI.md` hierarchy plus just-in-time reads; `context.fileName` | directory | replaced by Antigravity CLI for some tiers (2026-06) |
| Kiro | `.kiro/steering/`, `AGENTS.md` | `inclusion` modes | inclusion modes are ignored in AGENTS.md |
| Zed | the first match only, from `.rules`, `.cursorrules`, `.windsurfrules`, `.clinerules`, Copilot instructions, `AGENT.md`, `AGENTS.md`, `CLAUDE.md`, `GEMINI.md` | none | a stray `.cursorrules` shadows AGENTS.md |
| Windsurf / Devin Desktop, Cline, Amp, Junie | own rule directories plus `AGENTS.md` | varies | check the vendor docs live |

## Checks

| Check | A violation looks like | Evidence |
|---|---|---|
| Loads where intended | `globs:` in `.claude/rules` (loads everywhere); a repo with both CLAUDE.md and AGENTS.md expecting both to load; content in `AGENTS.override.md`; rules inside `<!-- -->` | platform facts above |
| Imports resolve | `@docs/x.md` missing; quoted or backticked `@path`; a 5-hop chain; an import from outside the project in a committed file | above |
| Fits each loader | an AGENTS.md chain over 32 KiB used by Codex; Cursor relying on `@` imports | vendor table |
| Session-type fit | an always-on rule written for interactive work ("ask the user", "plan first", "give a status update") reaching headless pipelines and subagents | E33 |
| Precedence collisions | user file "use npm" vs project file "use pnpm"; plugin-injected text vs project rules | E8 |
| Semantic contradictions | "always squash-merge" vs "never rewrite history"; a skill says "commit after each step" while CLAUDE.md says "never commit without asking" | E8 |
| Stale or false facts | a named command, script, path, port, branch or count that the repo contradicts; "verify on `origin/master`" in a `main` repo | E2, E33 |
| Enumerations that will drift | "six plugins", or a hand-kept list mirroring a directory. Prefer "see `ls plugins/`" | E33 |
| No doc-vs-code precedence | nothing says which wins when this file and the code disagree | E2 |
| Derivable content | a directory tree, a dependency list or an architecture tour the agent can read from the repo | E1 |
| Default-behaviour no-ops | "write clean code", "handle edge cases", "be thorough", "think step by step" | E4, E10 |
| Linter's job in prose | indentation or import order when a formatter is configured | E1 |
| Must-hold rule left as prose | "never push to main" or "always run tests before committing" with no hook, CI check or deny rule | E3, E9 |
| Unobservable rule | "be careful with the database"; no checkable action or artifact | E3 |
| Conditional phrasing | "If you modify X, then update Y". Rewrite as "Update Y whenever you modify X" | E6 |
| Ban with no alternative | "don't use the old `fetchJson` helper", naming no replacement | E11 |
| Emphasis inflation | IMPORTANT, MUST or NEVER on routine rules; "never" where approval would do ("ask before deleting files") | E10 |
| Time-sensitive text | "until Q3 use…", "the new v3 API" | E7 |
| Hidden Unicode | zero-width, bidi or tag characters (see the scan in `hooks.md`) | E26 |
| Concealment | "don't mention this to the user" | E10, E26 |
| Provenance | a rule injected by a hook or plugin with nothing naming where it lives | E33 |

## Output styles

- Fields: `name`, `description`, `keep-coding-instructions` (default `false`) and `force-for-plugin`.
- `keep-coding-instructions` only matters on the full system prompt. That means Haiku 4.5, Sonnet 5, Opus 4.7 and earlier, or any model with `CLAUDE_CODE_SIMPLE_SYSTEM_PROMPT=0`. The shorter prompt that newer models use by default has no software-engineering section, so the field does nothing there. Flag a coding-repo style that lacks it only when it runs on the full prompt.
- Rules meant for subagents never reach them through an output style.
