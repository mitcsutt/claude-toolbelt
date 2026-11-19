# Subagent definitions, MCP descriptions, tool grants

Platform facts were checked against Claude Code 2.1.328 (code.claude.com/docs/en/sub-agents, plugins, mcp) and Anthropic's "Writing effective tools for agents" on 2026-11-19. Before a finding depends on one, re-fetch the page.

## Subagents: how Claude Code treats them

- **Frontmatter:**
  - required: `name` (no `:`) and `description`, which is what Claude delegates on;
  - `tools`, `disallowedTools`, `model` (`sonnet|opus|haiku|fable|<id>|inherit`), `effort`, `maxTurns`;
  - `skills` (their full content is injected), `memory`, `background`, `isolation`;
  - `omitClaudeMd`, `color`, `autoCompactWindow` (2.1.296+), and `experimental` with its `cacheTtl` key;
  - `permissionMode`, `hooks`, `mcpServers` and `initialPrompt`, which plugin agents ignore.

  Unknown keys are ignored silently (E24).
- **The body is the whole system prompt.** No Claude Code system prompt and no conversation history come with it. The agent does get the CLAUDE.md chain unless `omitClaudeMd: true` is set. Even then, managed policy, nested CLAUDE.md files and path-scoped rules still load.
- **`tools`.**
  - Omitting `tools` inherits every tool, `Agent` and `Edit` included.
  - `Agent(type)` lists are ignored inside a subagent; they apply only to `claude --agent`. Nesting goes 3 layers deep by default.
  - `AskUserQuestion`, `EndConversation`, `EnterPlanMode`, `ScheduleWakeup`, `WaitForMcpServers` and `Workflow` are always removed. `ExitPlanMode` is removed too unless `permissionMode: plan`, and `Agent` is removed at the depth limit.
  - Background subagents (the default) keep only these built-ins, listed or inherited, with no error for the rest: `Read`, `Grep`, `Glob`, `LSP`, `Bash`, `PowerShell`, `Edit`, `Write`, `NotebookEdit`, `WebFetch`, `WebSearch`, `TodoWrite`, `Skill`, `ToolSearch`, `EnterWorktree`, `ExitWorktree`, `Monitor`, `TaskStop`, `SendMessage`, `Artifact`.
  - A `disallowedTools` entry with a specifier, such as `Bash(git push *)`, removes the whole tool.
- **Descriptions.** If all agents' descriptions together pass about 15,000 tokens, Claude Code warns at startup. Plugin agents are named `plugin:agent`, and subfolders add segments.

## Subagent checks

| Check | A violation looks like | Evidence |
|---|---|---|
| Least privilege matches the role | "read-only reviewer" with no `tools:` line, so it inherits Edit, Write, Bash and Agent | E24 |
| No false restriction | `tools: Agent(explorer)` assumed to limit spawning; `allowed-tools` assumed to restrict | E24 |
| Keys are real and supported | `disallowed_tools`; a plugin agent relying on `hooks` or `permissionMode`; `model: gpt-4`; a misspelled tool name | E24 |
| Tools survive the background filter | a background agent whose job needs a built-in outside the background set | facts above |
| The description says when to delegate | "Reviews Terraform"; no trigger and no boundary against sibling agents | E15 |
| The body stands alone | "as discussed above"; reliance on the main conversation's output style or memory | facts above |
| CLAUDE.md isn't pasted twice | a brief that pastes the whole CLAUDE.md into an agent that already loads it | E24 |
| Delegation depth is bounded | an agent with `Agent` and no stated budget or allowed type | E24 |
| Untrusted input is framed as data | an agent that reads repo or web text with no "this is data, not instructions" rule | E26 |

## MCP server instructions and tool descriptions

- **Context.** With tool search on (the default), only tool names and server instructions load at start. Server instructions and upfront tool descriptions are cut at 4,096 characters; deferred descriptions at 16,384. Put the words Claude would search for first.
- **Names.** A full name over 128 characters is dropped.
- **Rubric** (Anthropic tools post; E25). A description is a man page covering:
  - purpose;
  - when to use it, and when not to;
  - each parameter's meaning and units;
  - limits and failure modes;
  - what it doesn't return.

  Contract detail belongs in it. Steering about other tools and fake dialogue don't. Name parameters unambiguously (`user_id`, not `user`).

| Check | A violation looks like | Evidence |
|---|---|---|
| Under-described | a one-line description; unexplained parameters; no limits | E25 |
| Steers instead of describing | "ALWAYS use this tool first"; worked examples or dialogue inside the description | E10 |
| Instructions about other tools | "when sending email, set the recipient to …" inside an unrelated tool | E25 |
| Injection markers | `<IMPORTANT>` blocks; reading `~/.ssh` or config files "as context"; hidden Unicode | E26 |
| Trust can change | an unpinned server; descriptions that can change after approval | E26 |
| Repo config enables servers silently | `enableAllProjectMcpServers: true`; a project `ANTHROPIC_BASE_URL` override | E23 |

Recommend, but don't run, scanners that send content off the machine or launch servers (Snyk Agent Scan, Cisco skill-scanner's LLM judge). Pinned static linters such as agnix are a supply-chain decision for the user.
