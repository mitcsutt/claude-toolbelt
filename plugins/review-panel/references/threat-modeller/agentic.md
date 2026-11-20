# Agentic surface: threat-modeller

Verified on 2026-11-19 (Claude Code 2.1.328 docs; tools as in TM25). Re-check live before a finding depends on a volatile fact (marked ⟳).

Read this whenever the artifact adds or runs an agent, MCP server, hook, skill or CI job. What's here are attack paths. Defects in the instruction text itself are rules-lawyer's: cite the E-rows in `rules-lawyer/evidence.md`, don't restate them.

## Trifecta row

Fill one row of the Agent contexts table per agent context (TM4):

1. Take the tools from the agent's actual `tools:` list, its MCP grants, or the workflow's `--allowedTools`. Never from the plan's adjectives ("read-only", "just summarises").
2. **Private data:** files, secrets, tokens, notes, mail, internal APIs it can read. An unrestricted `Read` reaches everything the user can read.
3. **Untrusted content:** fetched pages, issue and PR text, tool results, files from other repos, chat messages, MCP responses.
4. **External channel:** chat or Slack posts; issue or PR comments; rendered markdown images or links built from agent output (EchoLeak shape, TM16); `WebFetch` to a URL the agent composes; git push.
5. **Human gate:** a person approves each outbound action. A permission prompt nobody sees in `-p` or CI is not a gate.

Three legs and no gate → a finding (ASI01, TM4). Two legs → Checked and fine, naming the missing leg (TM4, TM2).

## Checklist

Each item: what to look for · attacker · ID.

1. **Goal hijack** via untrusted content in an agent with tools: issue or PR text in a prompt (TM14), fetched pages, tool results · whoever writes that content · ASI01, TM2.
2. **Hostile repo under `-p`/CI**: committed hooks, `env` (for example `ANTHROPIC_BASE_URL`, which sends keys and transcripts to a third party), `apiKeyHelper`, `.mcp.json` · anyone who can land a commit or open a PR the job checks out · TM12, TM16, E23. Interactive: real after trust, so name who can commit.
3. **`pull_request_target` + head checkout + agent or build step** · a fork PR author · TM18, TM12.
4. **Tool poisoning, rug pull, shadowing**: an unpinned MCP server, or one whose description can change after approval. Delivery is a package update · the package publisher or whoever takes over the package · E25, E26 (R4 F21).
5. **Confused deputy**: a low-privilege agent's output drives a high-privilege agent or tool · whoever steers the low-privilege agent · ASI03, TM2.
6. **Memory and instruction persistence**: tool or fetched output written to CLAUDE.md, memory, skills or rules · the author of the fetched content, now standing in every later session · ASI06, TM17.
7. **Self-approval**: an agent that can write its own permission or approval config (`.claude/settings*.json`, `chat.tools.autoApprove`) · whoever steers that agent · TM16.
8. **Local agent services**: a localhost bridge to agent tools needs a Host/Origin check plus a token · any web page open in the user's browser · TM15.
9. **Guard hooks sold as boundaries**: a regex blocklist, or a hook that fails open, isn't a control; say what a deny rule or sandbox would do instead · the agent itself, or whoever steers it · E18, E21.
10. **Skills and plugins with scripts or hidden Unicode**: signature scanning alone is weak · the skill or plugin author, or whoever planted the characters · E26, E27. Report who planted it and what it makes the agent do; give file, line and code-point range, never the characters.

## Version-gated facts ⟳

State the Claude Code version you assume. When a finding turns on one of these rows, name the page to re-fetch under Coverage, or ask the panel to add `fact-checker`.

| Fact | Versions | Page | ID |
|---|---|---|---|
| Committed `defaultMode: auto`/`bypassPermissions` doesn't take effect from project or local settings | `bypassPermissions` took effect before v2.1.257; `auto` already didn't | settings-reference.md | TM13 |
| Shared-file `enableAllProjectMcpServers` is ignored before trust | current | settings-reference.md | TM13 |
| Project `env` applies after trust, or at startup under `-p` | current | settings-reference.md | TM13 |
| Under `-p`/SDK the folder is never trusted, yet `.mcp.json` servers connect without asking (SDK: only with project `settingSources`) and settings hooks, `env` and `apiKeyHelper` are used | current | permissions.md | TM12 |
| CVE-2025-59536: code ran before the trust dialog | fixed in 1.0.111 | NVD | TM16 |
| CVE-2026-21852: API keys exfiltrated before trust | fixed in 2.0.65 | NVD | TM16 |
