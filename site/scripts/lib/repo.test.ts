import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { buildPages } from './pages.ts'
import { checkShips, loadRepo } from './repo.ts'

function fixture(shipsRows: string, files: Record<string, string> = {}) {
  const root = mkdtempSync(join(tmpdir(), 'toolbelt-repo-'))
  const all: Record<string, string> = {
    '.claude-plugin/marketplace.json': JSON.stringify({
      name: 'kit',
      description: 'A kit.',
      plugins: [{ name: 'p', source: './plugins/p', category: 'tools' }],
    }),
    'plugins/p/.claude-plugin/plugin.json': JSON.stringify({ name: 'p', version: '1.2.3' }),
    'plugins/p/README.md': `# p\n\nDoes p.\n\n## What it ships\n\n| Component | Kind | What |\n| --- | --- | --- |\n${shipsRows}\n\n## Install\n\nx\n\n## Tests\n\ny\n`,
    'plugins/p/skills/s/SKILL.md': '---\nname: s\ndescription: Use for s.\n---\n# S\n\nBody.\n',
    ...files,
  }
  for (const [path, text] of Object.entries(all)) {
    mkdirSync(dirname(join(root, path)), { recursive: true })
    writeFileSync(join(root, path), text)
  }
  return root
}

test('a "What it ships" row naming a missing component is reported', () => {
  const root = fixture('| `/s` | skill | Does s. |\n| `/ghost` | skill | Not there. |\n| `bin-x` | command on PATH | Gone. |')
  const errors: string[] = []
  const plugin = loadRepo(root, errors).plugins[0]
  assert.ok(plugin)
  const problems = checkShips(root, plugin)
  assert.equal(errors.length, 0)
  assert.equal(problems.length, 2)
  assert.match(problems.join('\n'), /skill `ghost`, which doesn't exist/)
  assert.match(problems.join('\n'), /command on PATH `bin-x`, which doesn't exist/)
})

test('a skill with no row is reported', () => {
  const root = fixture('| `/other` | skill | Not s. |')
  const plugin = loadRepo(root, []).plugins[0]
  assert.ok(plugin)
  assert.match(checkShips(root, plugin).join('\n'), /skill s has no row/)
})

test('a plugin in marketplace.json without a README is reported', () => {
  const root = fixture('| `/s` | skill | Does s. |')
  writeFileSync(
    join(root, '.claude-plugin/marketplace.json'),
    JSON.stringify({ name: 'k', description: 'd', plugins: [{ name: 'nope', category: 'c' }] }),
  )
  const errors: string[] = []
  loadRepo(root, errors)
  assert.match(errors.join('\n'), /nope: marketplace\.json lists it/)
})

test("this repo's pages build with no problems, and every skill and agent has a page", () => {
  const root = join(dirname(fileURLToPath(import.meta.url)), '../../..')
  const errors: string[] = []
  const repo = loadRepo(root, errors)
  for (const plugin of repo.plugins) errors.push(...checkShips(root, plugin))
  const { files } = buildPages({ repo, repoUrl: 'https://github.com/o/r', branch: 'main', errors })
  assert.deepEqual(errors, [])
  const paths = new Set(files.map((f) => f.path))
  for (const plugin of repo.plugins) {
    assert.ok(paths.has(`plugins/${plugin.name}/index.md`))
    for (const skill of plugin.skills) assert.ok(paths.has(`plugins/${plugin.name}/skills/${skill.name}.md`))
    for (const agent of plugin.agents) assert.ok(paths.has(`plugins/${plugin.name}/agents/${agent.name}.md`))
  }
  const pageFiles = files.filter((f) => f.path.endsWith('.md'))
  assert.ok(pageFiles.every((f) => f.content.startsWith('---\ntitle: ')))
})
