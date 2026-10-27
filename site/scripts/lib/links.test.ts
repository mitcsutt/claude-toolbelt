import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { createLinkRewriter } from './links.ts'

function repo() {
  const root = mkdtempSync(join(tmpdir(), 'toolbelt-links-'))
  mkdirSync(join(root, 'plugins/a'), { recursive: true })
  mkdirSync(join(root, 'config'), { recursive: true })
  writeFileSync(join(root, 'plugins/a/README.md'), '# a\n')
  writeFileSync(join(root, 'config/settings.json'), '{}\n')
  return root
}

function rewriter(root: string) {
  return createLinkRewriter({
    root,
    repoUrl: 'https://github.com/o/r',
    branch: 'main',
    routes: { 'plugins/a/README.md': '/docs/plugins/a', 'README.md': '/docs' },
    anchorRoutes: { 'README.md#statusline': '/docs/statusline' },
  })
}

test('a link to a page goes to its route and keeps the anchor', () => {
  const links = rewriter(repo())
  const out = links.rewrite('[a](../a/README.md#usage) and [dir](../a/)', { source: 'plugins/b/README.md', route: '/x' })
  assert.equal(out, '[a](/docs/plugins/a#usage) and [dir](/docs/plugins/a)')
  assert.deepEqual(links.errors, [])
})

test('a section that became a page links to the page', () => {
  const links = rewriter(repo())
  assert.equal(links.rewrite('[s](../README.md#statusline)', { source: 'config/README.md', route: '/x' }), '[s](/docs/statusline)')
})

test('a repo file that is not a page goes to GitHub', () => {
  const links = rewriter(repo())
  const out = links.rewrite('[c](../../config/settings.json) [d](../../config)', { source: 'plugins/a/README.md', route: '/x' })
  assert.equal(out, '[c](https://github.com/o/r/blob/main/config/settings.json) [d](https://github.com/o/r/tree/main/config)')
})

test('a link that resolves to nothing is an error', () => {
  const links = rewriter(repo())
  links.rewrite('[gone](missing.md)', { source: 'README.md', route: '/docs' })
  assert.equal(links.errors.length, 1)
  assert.match(links.errors[0] ?? '', /missing\.md.*doesn't exist/)
})

test('external links, site routes, code spans and fences are left alone', () => {
  const links = rewriter(repo())
  const text = '[e](https://x.dev/a) [r](/docs/p) `[c](missing.md)`\n```\n[f](missing.md)\n```'
  assert.equal(links.rewrite(text, { source: 'README.md', route: '/docs' }), text)
  assert.deepEqual(links.errors, [])
})

test('a same-file anchor whose heading the page drops links to the page', () => {
  const links = rewriter(repo())
  const context = { source: 'README.md', route: '/docs/statusline', droppedAnchors: new Set(['statusline']) }
  assert.equal(links.rewrite('[a](#statusline) [b](#scripts)', context), '[a](/docs/statusline) [b](#scripts)')
})
