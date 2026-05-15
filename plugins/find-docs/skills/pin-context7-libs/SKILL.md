---
name: pin-context7-libs
description: >-
  This skill should be used when the user asks to "pin context7 libraries",
  "register context7 libs", "set up context7 for this project", "scan deps
  for context7", "update context7 library list", "populate the Context7
  Libraries table", "set up find-docs for this project", or otherwise wants
  to seed or refresh the `## Context7 Libraries` table in a CLAUDE.md file
  based on the current project's dependencies.

  Detects the project's package manager(s), extracts pinned dependency
  versions, resolves each library to a Context7 ID via the Context7 MCP,
  and writes the verified entries to a CLAUDE.md file chosen by the user
  (project root by default).

  Runs as either a first-time setup or as an update over an existing table —
  preserves entries already pinned, adds new ones, and refreshes versions
  when the project has moved to a different pinned version.
---

# Pin Context7 Libraries

Seed or refresh the `## Context7 Libraries` table that the `find-docs` skill reads. The table lets `find-docs` skip the `resolve-library-id` step for known project dependencies, saving lookups and reducing wrong-match risk.

## When to run

- First time setting up `find-docs` in a project.
- After a major dependency upgrade (framework version bump, new core library added).
- When the user notices `find-docs` is mis-resolving a known dependency.

## Workflow

### 1. Confirm the target CLAUDE.md file

Use `AskUserQuestion` to confirm where to write the table. Default suggestion is the **project root `CLAUDE.md`** (i.e. `<repo-root>/CLAUDE.md` — find the repo root via `git rev-parse --show-toplevel`).

Offer three options:

| Option | Path |
|---|---|
| Project root CLAUDE.md *(default, recommended)* | `<repo-root>/CLAUDE.md` |
| User root CLAUDE.md | `~/.claude/CLAUDE.md` |
| Other | Free-text path entered by the user |

If the chosen file does not exist, confirm with the user before creating it.

### 2. Detect package manifests

Scan the repo root and obvious subdirectories (one level deep) for manifests. Run detection in parallel. Common manifests:

| Ecosystem | Manifest files |
|---|---|
| Node / JS / TS | `package.json` (+ `package-lock.json` / `yarn.lock` / `pnpm-lock.yaml` / `bun.lockb` for resolved versions) |
| Ruby | `Gemfile`, `Gemfile.lock` |
| Python | `pyproject.toml` (poetry / uv / pdm), `requirements*.txt`, `Pipfile`, `Pipfile.lock`, `setup.py` |
| Go | `go.mod`, `go.sum` |
| Rust | `Cargo.toml`, `Cargo.lock` |
| PHP | `composer.json`, `composer.lock` |
| Java / Kotlin | `pom.xml`, `build.gradle`, `build.gradle.kts` |
| .NET | `*.csproj`, `packages.config` |
| Elixir | `mix.exs`, `mix.lock` |

Stop after collecting manifests — do not run package-manager commands (`npm ls`, `bundle show`, etc.) unless lockfiles are missing.

### 3. Extract dependencies and pinned versions

Read each manifest plus its lockfile. Prefer the **lockfile's resolved version** over the manifest's range. Examples:

- `package.json` says `"next": "^14.0.0"`; `package-lock.json` resolves to `14.2.5` → use `14.2.5`.
- `Gemfile.lock` shows `rails (7.1.3)` → use `7.1.3`.
- `pyproject.toml` says `django = "^4.2"`; `poetry.lock` shows `4.2.11` → use `4.2.11`.

Only include **direct dependencies** by default — skip transitive deps unless the user asks for them. Transitive deps are noisy and rarely the subject of doc lookups.

Filter out internal / workspace / local packages (versions like `workspace:*`, `link:`, `file:`, `path:`, `git+ssh://`, monorepo internal packages).

### 4. Resolve each library via Context7

For each direct dependency, call `mcp__plugin_find-docs_context7__resolve-library-id` with:

- `libraryName`: the package name (e.g. `"next"`, `"rails"`, `"django"`)
- `query`: a short intent phrase, e.g. `"framework documentation"` or `"<package> usage and API"`

Both fields are required strings — omitting `query` returns `MCP error -32602`.

Run resolves in parallel batches (e.g. 5–10 at a time) to keep the workflow snappy. Do not exceed a sensible batch — large parallel fans can rate-limit.

**Match selection:**
- Accept a result when: name matches exactly (or matches with common org prefix like `/vercel/next.js` for `next`), and the description matches the library's purpose.
- Reject when: only fuzzy/unrelated matches return (e.g. `lost-pixel` returning "Pixel Editor"), or no result has good source reputation.

**Version selection:**
- If a result exposes version-specific IDs (form `/org/project/vX.Y.Z`) and one closely matches the pinned version, prefer that ID.
- If only an unversioned ID exists (`/org/project`), use that. Note the project's pinned version in a comment or adjacent column so future updates can compare.

Do NOT retry a failed resolve with reworded names. One attempt per package — if it doesn't match, skip and list the package in a "could not resolve" report at the end.

### 5. Read the existing CLAUDE.md and merge

If the chosen CLAUDE.md already has a `## Context7 Libraries` section:

- Parse the existing table.
- Keep entries the user added manually that aren't in the dependency scan (they may have pinned non-direct libs intentionally).
- Update IDs for entries where the resolved Context7 ID has changed or a version-specific ID is now available.
- Add new entries from the scan.
- Do not remove an entry just because it's no longer a direct dep — the user may rely on it elsewhere. Surface removed deps in the summary and ask if they should be pruned.

If the section does not exist, append it to the end of the file with a blank line separating it from the previous content.

### 6. Write the table

Use this exact format so `find-docs` can parse it:

```markdown
## Context7 Libraries

| Library        | Context7 ID              |
|----------------|--------------------------|
| Next.js        | /vercel/next.js          |
| Prisma         | /prisma/prisma           |
| TanStack Query | /tanstack/query          |
```

Sort entries alphabetically by display name for stable diffs.

Use version-specific IDs (`/vercel/next.js/v14.3.0`) only when the project pins a specific version that has a matching versioned ID. Avoid version-pinning when the project tracks the latest stable line.

### 7. Report

After writing, summarize:

- Path of the file updated.
- Count of entries added / updated / unchanged.
- Any packages that couldn't be resolved (skipped), with brief reasons.
- Optional follow-up: "Re-run when you upgrade major versions or add a new core dependency."

## Edge cases

**Monorepo / multiple package managers**
Treat each workspace/package as a candidate. Ask the user whether to merge all into a single table or scope to a single workspace.

**No manifests found**
Stop and tell the user — there's nothing to scan. Do not invent dependencies from imports.

**Project root CLAUDE.md not in the repo root**
Some teams keep CLAUDE.md under `.claude/CLAUDE.md` or `docs/`. If `<repo-root>/CLAUDE.md` does not exist but another CLAUDE.md does in the repo, surface it as a fourth option in the prompt.

**Private / proprietary packages**
Internal packages (e.g. `@mycompany/*`, `git+ssh://...`) won't be on Context7. Skip silently — don't list them as failures.

**Large dependency trees**
If direct deps exceed ~40 packages, ask the user before resolving all of them. Resolving 80+ packages in one go burns calls and produces a noisy table. Suggest scoping to "core" dependencies (frameworks, ORMs, UI libs, test runners) and skipping utility / micro-libs.

## What not to do

- Do not run install commands (`npm install`, `bundle install`, etc.) — only read existing manifests/lockfiles.
- Do not include transitive dependencies by default.
- Do not retry `resolve-library-id` with reworded names when the first call doesn't match.
- Do not write versioned IDs that aren't supported by Context7 (the resolver will return them if they exist).
- Do not overwrite manual entries the user added — merge, don't replace.

## Cross-reference

This skill complements the `find-docs` skill. `find-docs` reads the table this skill writes. Keep the table format consistent with what `find-docs/SKILL.md` describes under "CLAUDE.md Library Registry".
