# site

The claude-toolbelt documentation site, built on the published
[`@mitcsutt/kiln-docs`](https://www.npmjs.com/package/@mitcsutt/kiln-docs). It
renders this repo's READMEs, `SKILL.md` and agent files, manifests, `docs/` and
shared rules where they live, so a fact is written once: change the source
file, not a page. Where the site lives is in
[`docs/repo-structure.md`](../docs/repo-structure.md).

## Run it

Node 24 and pnpm (`corepack enable` gives you the pinned one). From `site/`:

```bash
pnpm install
pnpm dev          # generate, then next dev
pnpm build        # generate, next build, then kiln-docs postbuild into out/
pnpm check-links  # serve out/ with wrangler and follow every internal link
pnpm test         # the generator's tests
pnpm typecheck
```

## How it works

`scripts/generate.ts` reads the repo and writes the pages to `.generated/pages`
(gitignored) through kiln-docs' `writeGenerated`. The pages are plain Markdown,
never escaped. It rewrites repo-relative links to routes, or to GitHub when the
target isn't a page, and it **fails the build** when a link resolves to nothing,
when a README's "What it ships" row names a component that isn't there, or when a
skill, agent or `bin/` command has no row.

| Source | Page |
| --- | --- |
| `README.md` | Overview, Statusline, Shared rules (lead) |
| `plugins/<name>/README.md`, `plugin.json`, `hooks.json`, `.mcp.json`, `bin/` | `/docs/plugins/<name>` |
| `plugins/<name>/skills/<skill>/SKILL.md` | `/docs/plugins/<name>/skills/<skill>`, body in full |
| `plugins/<name>/agents/<agent>.md` | `/docs/plugins/<name>/agents/<agent>`, body in full |
| `shared-rules.md`, `rules/*.md` | `/docs/shared-rules` |
| `CONTRIBUTING.md`, `docs/*.md`, `SECURITY.md` | `/docs/contributing` |

The theme is fixed (Flightdeck) with the light/dark/system toggle. The route
files under `app/` are kiln-docs' stamped templates; `kiln-docs postbuild`
fails if one is missing.

## Deploy

`docs.yml` builds and link-checks pull requests that touch what the site renders.
`docs-deploy.yml` deploys `main` with `wrangler deploy` to the Worker
`claude-toolbelt-docs`, using the repository secrets `CLOUDFLARE_API_TOKEN` and
`CLOUDFLARE_ACCOUNT_ID`. The Worker, the custom domain and those secrets are set
up outside this repo, and `wrangler.jsonc` declares no routes.
