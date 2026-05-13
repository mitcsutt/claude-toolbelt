---
name: find-docs
description: >-
  Retrieves authoritative, up-to-date technical documentation, API references,
  configuration details, and code examples for any developer technology.
  Also performs people and company research via Exa AI.

  Use this skill whenever answering technical questions or writing code that
  interacts with external technologies. This includes libraries, frameworks,
  programming languages, SDKs, APIs, CLI tools, cloud services, infrastructure
  tools, and developer platforms.

  Common scenarios:
  - looking up API endpoints, classes, functions, or method parameters
  - checking configuration options or CLI commands
  - answering "how do I" technical questions
  - generating code that uses a specific library or service
  - debugging issues related to frameworks, SDKs, or APIs
  - retrieving setup instructions, examples, or migration guides
  - verifying version-specific behavior or breaking changes
  - searching for people by role, expertise, or what they work on
  - researching companies by industry, criteria, or attributes

  Prefer this skill whenever documentation accuracy matters or when model
  knowledge may be outdated.
---

# Documentation Lookup

Retrieve current documentation and code examples using Context7 MCP tools, with Exa AI MCP as a fallback.

## Lookup Flow

Follow these steps in order. Do not attempt more than 3 total lookups (across both tools) per question.

### 1. Check CLAUDE.md for pre-registered libraries

Before resolving a library, scan all CLAUDE.md files in the project hierarchy for a `## Context7 Libraries` section. If a matching Context7 library ID exists for the technology in question, skip directly to step 3 using that ID.

### 2. Resolve the library via Context7 MCP

Use Context7 MCP tools to resolve the library or package name to a Context7 library ID. Pass a descriptive query derived from the user's intent — not just the library name. Select the best match based on name relevance, documentation coverage, and source reputation.

### 3. Query documentation via Context7 MCP

Use Context7 MCP tools to fetch documentation for the resolved library ID. Use specific, descriptive queries — never single-word queries. Use the user's full question as the query basis when possible.

If the user mentions a specific version and a version-specific ID is available, use it (format: `/org/project/version`).

### 4. Fallback to Exa AI MCP

If Context7 MCP tools are unavailable, return an error, return no results, or return insufficient/irrelevant results, fall back to Exa AI MCP tools to search the web for the documentation.

When using the Exa fallback, inform the user that results came from a web search rather than Context7.

### 5. People and Company Search (Exa only)

For people or company lookups, use Exa AI MCP tools directly (Context7 is not applicable).

**People search** (`people_search_exa`):
- Use SINGULAR form: "software engineer" not "software engineers"
- Describe what they work on: "researcher training open source LLMs"
- Does NOT support date filters or text filters
- `includeDomains` only accepts LinkedIn domains

**Company search** (`company_research_exa`):
- Use SINGULAR form for queries
- Use simple entity/attribute queries: "AI startup healthcare"
- Returns company objects, not articles about companies

## Exa Search Best Practices

- **Search type `auto`** is recommended for most queries — balanced relevance and speed
- **Use `highlights`** for token-efficient excerpts; use `text` only when you need full contiguous content
- If `text` content is needed, set `max_characters` (e.g., 10000-20000) to avoid excessive token usage
- **Categories**: Use `category: "news"` for news, `category: "research paper"` for academic papers, `category: "tweet"` for Twitter/X posts. Omit category if results are too restrictive
- **Content freshness**: Use `maxAgeHours` to control cached vs livecrawled content (0 = always fresh, omit for default)
- **Domain filtering**: Usually not needed — use `includeDomains`/`excludeDomains` only when targeting specific authoritative sources
- **Deprecated params to avoid**: `useAutoprompt`, `livecrawl` (use `maxAgeHours`), `numSentences`/`highlightsPerUrl` (use `maxCharacters`), `stream: true`
- **Parameter nesting**: In `/search`, content options (`text`, `highlights`) must be nested inside `contents`. In `/contents` endpoint, they are top-level

## CLAUDE.md Library Registry

Users can pre-register Context7 library IDs in any CLAUDE.md file (project-level, user-level, etc.) to skip the resolve step for commonly used libraries. Add a section like this:

```markdown
## Context7 Libraries

| Library        | Context7 ID       |
|----------------|-------------------|
| TanStack Query | /tanstack/query   |
| Next.js        | /vercel/next.js   |
| Prisma         | /prisma/prisma    |
```

Version-specific IDs are supported (e.g., `/vercel/next.js/v14.3.0`).

When a matching entry is found, use that ID directly — no resolve step needed.

## Query Guidelines

- Be specific: `"How to set up authentication with JWT in Express.js"` not `"auth"`
- Use the user's full question as the query basis when possible
- Never include sensitive information (API keys, passwords, credentials, personal data, or proprietary code) in queries
- These guidelines apply to both Context7 and Exa queries
