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

Retrieve current documentation and code examples. Two backends are available:

- **Context7 MCP** — curated, versioned library docs. Best for popular OSS libraries, frameworks, SDKs.
- **Exa AI MCP** — live web search and page extraction. Best for everything else (first-party vendor docs, niche/new libraries, blog posts, GitHub-only projects, research-style questions).

Pick the right backend up front. Do not default to "Context7 first, Exa fallback" — that wastes lookups when Context7 will never have the source.

## Routing Decision

Choose the backend BEFORE the first tool call:

| Question shape | Backend |
|---|---|
| Popular OSS library/framework (React, Next.js, Prisma, Express, TanStack, Tailwind, Django, Spring, Cypress, etc.) | **Context7** |
| Library pinned in a project CLAUDE.md `## Context7 Libraries` table | **Context7** (skip resolve, use the listed ID) |
| First-party vendor SaaS docs (Datadog, Stripe, AWS, GCP, Azure, Atlassian, Anthropic, etc.) | **Exa** |
| Tool that lives only on GitHub or has docs only in a repo README | **Exa** |
| Brand-new or niche library (released <6 months ago, or <1k stars) | **Exa** |
| Comparing approaches, surveying ecosystem, "what's the best way to…" | **Exa** |
| People / company research | **Exa** (see end of doc) |

When in doubt about a library, try Context7 once. If `resolve-library-id` returns no obvious match on the first call, switch to Exa — do not retry the resolve with reworded names.

## Hard limits

- Maximum **3 total tool calls** per user question across all backends. If 3 calls don't surface the answer, summarize what was tried and ask the user how to proceed.
- Never include secrets, API keys, credentials, personal data, or proprietary code in any query.

## Context7 Flow

### 1. Check CLAUDE.md for pre-registered libraries

Scan CLAUDE.md files in the project hierarchy for a `## Context7 Libraries` section. If the technology is listed, use that ID directly and skip step 2.

### 2. Resolve the library

Call `resolve-library-id`. Both parameters are required strings:

- `libraryName` — package name or short identifier (e.g. `"next.js"`, `"prisma"`, `"tanstack/query"`)
- `query` — a descriptive phrase derived from the user's intent (e.g. `"server-side rendering with app router"`)

Omitting `query` returns `MCP error -32602: Invalid input: expected string, received undefined`.

Pick the best match by name relevance, documentation coverage, and source reputation. **If no result clearly matches (wrong package, low reputation, or generic "Pixel Editor"-style fuzzy match), abandon Context7 and switch to Exa — do not retry resolve with reworded queries.** Repeated resolves on ambiguous names rarely succeed.

### 3. Query documentation

Call `query-docs` with the resolved ID. Use the user's full question (or a close paraphrase) as the query — not single keywords.

If the user mentions a specific version and a version-specific ID is available, use the form `/org/project/version` (e.g. `/vercel/next.js/v14.3.0`).

Cache the library ID in memory for the rest of the session. Do not re-resolve.

## Exa Flow

Use Exa when routing pointed here, or when Context7 returned nothing useful.

### Tool selection

| Need | Tool |
|---|---|
| Find pages/articles by topic | `web_search_exa` |
| Find pages with advanced filters (domains, date, category) | `web_search_advanced_exa` |
| Extract verbatim content from a known URL | `crawling_exa` |
| Pull idiomatic code examples for a library/API | `get_code_context_exa` |
| Find a person by role/expertise | `people_search_exa` |
| Research a company | `company_research_exa` |

### web_search_exa

- `numResults` is a **number**, not a string. Send `5`, not `"5"`. Default 3–5 is usually enough.
- Use `category: "news"` (recent events), `"research paper"` (academic), `"tweet"` (X/Twitter) only when the question demands it. Otherwise omit — narrow categories silently exclude relevant docs.
- Use `maxAgeHours` only when freshness matters (release notes, recent breaking changes). Omit for evergreen docs.
- Use `includeDomains` / `excludeDomains` only when targeting a known authoritative source (e.g. `["docs.datadoghq.com"]`). Don't pre-filter; it hides relevant hits.
- Query length: aim for 1 sentence (~100 chars). Long keyword-soup queries (>250 chars) rarely outperform a clear sentence — they often retrieve the same pages.

### crawling_exa — extracting verbatim content

After a search returns a URL, use `crawling_exa` to pull the page content. **Prefer this over WebFetch** when the URL came from an Exa search; it stays in the same tool family and supports `maxCharacters`.

URL shape constraints:

- **GitHub `blob/` URLs (`github.com/<org>/<repo>/blob/<branch>/<path>`) often fail with `CRAWL_NOT_FOUND`.** Use the raw form instead: `raw.githubusercontent.com/<org>/<repo>/<branch>/<path>`.
- If both forms return `CRAWL_NOT_FOUND`, the file is private, deleted, or rate-limited. Stop crawling — try `get_code_context_exa` for the same library or report the gap to the user.
- Always pass `maxCharacters` (5,000–10,000 is plenty for most doc pages). Crawled HTML can balloon to 50KB+ otherwise.

### get_code_context_exa

Best when the user wants idiomatic usage examples for a specific library, function, or API. Pass the library name and the topic/intent as the query.

### Reusing results

Within a single user question, do not re-call the same tool with the same query or re-crawl the same URL. If you've already retrieved a page, reference the prior result.

## People and Company Search (Exa only)

Context7 does not apply. Use these tools directly when the user explicitly asks for people or company research.

**`people_search_exa`**
- Use SINGULAR role: `"software engineer"`, not `"software engineers"`.
- Describe what they work on: `"researcher training open source LLMs"`.
- No date filters, no text filters.
- `includeDomains` only accepts LinkedIn domains.

**`company_research_exa`**
- Singular form, entity+attribute style: `"AI startup healthcare"`.
- Returns company objects, not articles about companies.

## Query Guidelines

- Be specific. `"How to set up JWT auth in Express.js"` beats `"auth"`.
- Use the user's full question as the query basis when possible — paraphrase only to remove noise.
- Never include secrets, credentials, personal data, or proprietary code.
- Apply to both Context7 and Exa.

## CLAUDE.md Library Registry

Pre-register Context7 library IDs in any CLAUDE.md file to skip the resolve step for commonly used libraries:

```markdown
## Context7 Libraries

| Library        | Context7 ID       |
|----------------|-------------------|
| TanStack Query | /tanstack/query   |
| Next.js        | /vercel/next.js   |
| Prisma         | /prisma/prisma    |
```

Version-specific IDs are supported (e.g., `/vercel/next.js/v14.3.0`). When a matching entry is found, use that ID directly — no resolve step needed.

## Tool Quirks (quick reference)

- `resolve-library-id`: both `libraryName` and `query` are **required strings**. Missing `query` → `MCP error -32602`.
- `web_search_exa.numResults`: type is **number**. String values may pass but are wrong-shape.
- `crawling_exa`: prefer `raw.githubusercontent.com` over `github.com/blob/`. Always set `maxCharacters`.
- Deprecated Exa params to avoid: `useAutoprompt`, `livecrawl` (use `maxAgeHours`), `numSentences` / `highlightsPerUrl` (use `maxCharacters`), `stream: true`.
- Parameter nesting: in `/search`, content options (`text`, `highlights`) must be nested inside `contents`. In `/contents`, they're top-level.
- After a successful lookup, do not re-resolve, re-search, or re-crawl the same target in the same turn.
