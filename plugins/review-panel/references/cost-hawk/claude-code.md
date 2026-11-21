# cost-hawk checklist: Claude Code, agents and LLM API calls

Verified on 2026-11-19 against the sources named per row; re-check live before a finding depends on a volatile fact (prices, platform behaviour).

Ordered by the owner's cost episodes (CH23), not by cost-hawk's own record, which is empty so far. Hook correctness belongs to rules-lawyer; you price the consequence. Prices: `prices.md`. Arithmetic: `measuring.md`.

| # | Check | Looks like | Price it as | Evidence |
|---|---|---|---|---|
| 1 | Fan-out and context | N subagents or reviewers per run, each with tens of requests | subagents × requests × context at the cache-read rate, plus writes and output. Peak context is not consumption: one reviewer can read 1.6M–16.9M tokens | CH22, CH10, CH11, E41 |
| 2 | Loops and wakes | Stop or SubagentStop hook that blocks until a condition holds; scheduled task or poll inside an agent session | Stop: the 8-continuation cap counts *consecutive* continuations and resets each time Claude calls a tool, so a block that makes Claude run a tool is not bounded by it. Price per continuation × unbounded, and look for `stop_hook_active`, a counter, `--max-turns` or `--max-budget-usd`. Scheduled tasks and polls: full context per tick | E20, CH24, CH9 |
| 3 | Redundant expensive tiers | A costly step (bigger model, T2 run, extra reviewer) re-proving what a cheap check settled | the expensive step's full cost, as waste | CH23 |
| 4 | Model switches | Skill or command frontmatter `model:` ≠ the session model; `opusplan` | each switch turn reads the whole history uncached on the new model; on Haiku 5.5 a history over 100k pays the higher tier. Each `opusplan` plan-mode toggle is a switch. Plugin skills, agents and hooks never invalidate the cache, but a plugin skill's own `model:` is a switch like any other | CH13, CH2, CH3 |
| 5 | Cache-busters | A timestamp, random ID or non-deterministic serialisation before a cache breakpoint; sequential `-p` runs in different directories or after a git change | every request writes the prefix instead of reading it: 5m write vs read is 25× on Opus/Sonnet 5.5. Check the TTL bucket and billing type | CH12, CH14, CH15, CH3 |
| 6 | Hooks that call a model | `type: "prompt"` or `"agent"`, a broad matcher (`"*"`), a hot event (PreToolUse, PostToolUse), a `model` override | calls = events per session × sessions; each call = its prompt and tool payload at the hook model's rate. Command hooks: process time only | CH16 |
| 7 | Injected text | SessionStart or UserPromptSubmit stdout or `additionalContext` | over 10,000 characters Claude gets a file path and a ≤2,000-character preview, so price ≤2k characters, not the file | E22 |
| 8 | Model choice | "Switch to the cheaper model" | price both tiers at CH1 rates on the same token counts; for Haiku 5.5 check each request's prompt length against 100k | CH1, CH2 |
| 9 | Headless and CI agent runs | `claude -p` in CI, cron or a timer | runs per day × requests × context; API-key billing gets the 5m TTL; check for a budget brake | CH14, CH24 |

## Looks expensive, isn't

- A 2k-token CLAUDE.md addition against a 135k median prompt: ~1.5% (CH10).
- Enabling a plugin that provides only skills, commands, agents, hooks, monitors or themes, or invoking a plugin skill with no `model:` override: doesn't invalidate the cache (CH13). MCP-server and code-intelligence plugins can.
- Command hooks: no model call, no context unless they return text (CH16).
- A one-off `/compact` (CH9).
