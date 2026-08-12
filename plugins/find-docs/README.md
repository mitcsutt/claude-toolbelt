# find-docs

Retrieves up-to-date technical documentation, API references, config details, and code examples for any developer technology, plus people/company research. Backed by two MCP servers so lookups come from a live source instead of stale training data.

## Why

Model recall of library APIs and vendor docs goes stale, and answering from memory produces confident-but-wrong output. This plugin routes each question to whichever backend actually has the source — a curated docs index for popular OSS libraries, live web search for everything else — under a hard per-question call budget so lookups stay cheap.

## What it ships

| Component | Kind | What |
| --- | --- | --- |
| `/find-docs` | skill | Routes a documentation or code-lookup question to Context7 or Exa, picking the backend before the first tool call. Caps itself at 3 tool calls per question. |
| `/pin-context7-libs` | skill | Scans a project's package manifests and seeds/refreshes a `## Context7 Libraries` table in CLAUDE.md, so `/find-docs` can skip the resolve step for known dependencies. |
| `context7` MCP server | MCP | Declared in `.mcp.json`, runs `@upstash/context7-mcp` via `npx`. Curated, versioned docs for popular libraries and frameworks. |
| `exa` MCP server | MCP | Declared in `.mcp.json`, a hosted HTTP MCP at `mcp.exa.ai`. Live web search, page extraction, and people/company research. |

## Install

```text
/plugin marketplace add mitcsutt/claude-toolbelt
/plugin install find-docs@claude-toolbelt
```

The `exa` MCP server will not connect until `EXA_API_KEY` is set — see Configuration.

## Usage

Ask a technical question — "how do I set up JWT auth in Express.js", "what changed in Next.js 15's app router" — and `/find-docs` picks a backend from a routing table before calling any tool: Context7 for popular OSS libraries and anything pinned in a project's CLAUDE.md, Exa for first-party vendor docs, GitHub-only projects, niche/new libraries, and people/company research.

Run `/pin-context7-libs` once per project to populate the CLAUDE.md registry that lets `/find-docs` skip `resolve-library-id` for known dependencies.

## Configuration

- `EXA_API_KEY` — required environment variable. Substituted into the `exa` MCP server's URL in `.mcp.json`; without it, all Exa-backed lookups (web search, crawling, code context, people/company research) fail to connect.
- The `context7` MCP server needs no key — it runs locally via `npx`.

## Tests

```bash
bash plugins/find-docs/tests/all.sh
```
