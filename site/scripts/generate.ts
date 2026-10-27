// The site's generator, run before `next build`. It reads the repo (manifests, READMEs, SKILL.md and
// agent files, docs/, shared rules) and writes the pages to `.generated/pages` through kiln-docs'
// `writeGenerated`, plus `.generated/landing.json` for the landing page. Nothing is copied into the
// content folder: the pages render the files where they live.
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { writeGenerated } from '@mitcsutt/kiln-docs/generate'
import config from '../site.config.ts'
import { buildPages } from './lib/pages.ts'
import { checkShips, loadRepo } from './lib/repo.ts'

/** The branch GitHub "Source" links point at. */
const BRANCH = 'main'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const errors: string[] = []

const repo = loadRepo(root, errors)
for (const plugin of repo.plugins) errors.push(...checkShips(root, plugin))

const { files, landing } = buildPages({ repo, repoUrl: config.repository.url, branch: BRANCH, errors })

if (errors.length > 0) {
  console.error(`generate: ${String(errors.length)} problem(s)\n${errors.map((e) => `  - ${e}`).join('\n')}`)
  process.exit(1)
}

const { written, skipped } = writeGenerated(files)
mkdirSync('.generated', { recursive: true })
writeFileSync('.generated/landing.json', `${JSON.stringify(landing, null, 2)}\n`)

console.log(`generated ${String(written.length)} file(s) from ${String(repo.plugins.length)} plugins`)
for (const { path, shadowedBy } of skipped) console.log(`skipped ${path}: ${shadowedBy} wins`)
