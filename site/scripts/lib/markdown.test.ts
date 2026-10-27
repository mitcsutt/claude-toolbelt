import assert from 'node:assert/strict'
import { test } from 'node:test'
import { demote, headings, parseFrontmatter, parseTable, splitSections, takeTitle } from './markdown.ts'

test('frontmatter reads a description that strict YAML would reject', () => {
  const { data, body } = parseFrontmatter('---\nname: x\ndescription: Advisory check: reports things.\n---\nBody\n')
  assert.equal(data.description, 'Advisory check: reports things.')
  assert.equal(body, 'Body\n')
})

test('frontmatter folds a block scalar and keeps list items on their own lines', () => {
  const { data } = parseFrontmatter(
    '---\ndescription: >-\n  First line\n  continues.\n\n  Common:\n  - one\n  - two\nuser-invocable: true\n---\n',
  )
  assert.equal(data.description, 'First line continues.\n\nCommon:\n- one\n- two')
  assert.equal(data['user-invocable'], 'true')
})

test('headings and sections ignore a # line inside a fence', () => {
  const text = '# Title\n\nlead\n\n## One\n\n```bash\n## not a heading\n```\n\n## Two\n\ntext'
  assert.deepEqual(headings(text).map((h) => h.title), ['Title', 'One', 'Two'])
  const { lead, sections } = splitSections(text)
  assert.equal(lead, '# Title\n\nlead')
  assert.deepEqual(sections.map((s) => s.title), ['One', 'Two'])
  assert.match(sections[0]?.body ?? '', /## not a heading/)
})

test('takeTitle drops the first H1 and demote moves headings but not fenced lines', () => {
  assert.deepEqual(takeTitle('\n# Name\n\nText\n'), { title: 'Name', body: 'Text' })
  assert.equal(demote('## A\n```\n## B\n```\n###### C', 1), '### A\n```\n## B\n```\n###### C')
})

test('parseTable returns the header and rows, keeping an escaped pipe in its cell', () => {
  const table = parseTable('| A | B |\n| --- | --- |\n| `x` | a \\| b |\n')
  assert.deepEqual(table?.header, ['A', 'B'])
  assert.deepEqual(table?.rows, [['`x`', 'a \\| b']])
})
