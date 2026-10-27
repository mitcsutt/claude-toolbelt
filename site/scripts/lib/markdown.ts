// Small Markdown helpers for the generator. Every one is fence-aware: a `#` line inside a code
// block is code, not a heading.

export interface Frontmatter {
  data: Record<string, string>
  body: string
}

/**
 * A lenient reader for SKILL.md and agent frontmatter. Claude Code accepts descriptions that strict
 * YAML rejects (`Advisory pre-flight check: reports…`), so this reads top-level `key: value` lines
 * and the block scalars (`>`, `>-`, `|`, `|-`) and nothing else. A folded scalar keeps its
 * paragraphs and its `- ` list items on lines of their own.
 */
export function parseFrontmatter(text: string): Frontmatter {
  const match = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(text)
  if (!match) return { data: {}, body: text }
  const lines = (match[1] ?? '').split(/\r?\n/)
  const data: Record<string, string> = {}
  for (let i = 0; i < lines.length; i++) {
    const entry = /^([A-Za-z][\w-]*):(?:[ \t]+(.*))?$/.exec(lines[i] ?? '')
    if (!entry) continue
    const key = entry[1] ?? ''
    const value = (entry[2] ?? '').trim()
    const block = /^([>|])[+-]?$/.exec(value)
    if (!block) {
      data[key] = unquote(value)
      continue
    }
    const raw: string[] = []
    while (i + 1 < lines.length && (lines[i + 1] === '' || /^\s/.test(lines[i + 1] ?? ''))) {
      raw.push((lines[++i] ?? '').trim())
    }
    data[key] = block[1] === '|' ? raw.join('\n').trim() : fold(raw)
  }
  return { data, body: text.slice(match[0].length) }
}

function unquote(value: string): string {
  const quoted = /^(["'])(.*)\1$/.exec(value)
  return quoted ? (quoted[2] ?? '') : value
}

function fold(lines: readonly string[]): string {
  const out: string[] = []
  let paragraph = ''
  const flush = () => {
    if (paragraph) out.push(paragraph)
    paragraph = ''
  }
  for (const line of lines) {
    if (line === '') {
      flush()
      out.push('')
    } else if (line.startsWith('- ')) {
      flush()
      paragraph = line
    } else {
      paragraph = paragraph ? `${paragraph} ${line}` : line
    }
  }
  flush()
  return out.join('\n').replace(/\n{3,}/g, '\n\n').trim()
}

/** One line, for a place that takes no line breaks: a frontmatter value or a table cell. */
export function oneLine(text: string): string {
  return text.replace(/\s+/g, ' ').trim()
}

/** Calls `visit` for every line, saying whether it sits inside (or on the edge of) a fenced block. */
function scan(text: string, visit: (line: string, fenced: boolean) => void): void {
  let fence: string | undefined
  for (const line of text.split('\n')) {
    const marker = /^\s*(`{3,}|~{3,})/.exec(line)?.[1]
    if (fence !== undefined) {
      const closes =
        marker !== undefined &&
        marker[0] === fence[0] &&
        marker.length >= fence.length &&
        /^\s*[`~]+\s*$/.test(line)
      if (closes) fence = undefined
      visit(line, true)
    } else if (marker !== undefined) {
      fence = marker
      visit(line, true)
    } else {
      visit(line, false)
    }
  }
}

/** Runs `fn` over each line outside a fenced code block, keeping the fenced lines as they are. */
export function mapOutsideFences(text: string, fn: (line: string) => string): string {
  const out: string[] = []
  scan(text, (line, fenced) => out.push(fenced ? line : fn(line)))
  return out.join('\n')
}

const HEADING = /^(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$/

export function headings(text: string): { level: number; title: string }[] {
  const found: { level: number; title: string }[] = []
  scan(text, (line, fenced) => {
    const match = fenced ? null : HEADING.exec(line)
    if (match) found.push({ level: (match[1] ?? '').length, title: match[2] ?? '' })
  })
  return found
}

export interface Section {
  title: string
  body: string
}

/** Splits a document at its H2 headings: the text before the first one, then each section. */
export function splitSections(text: string): { lead: string; sections: Section[] } {
  const lead: string[] = []
  const sections: { title: string; lines: string[] }[] = []
  scan(text, (line, fenced) => {
    const match = fenced ? null : HEADING.exec(line)
    if (match && (match[1] ?? '').length === 2) {
      sections.push({ title: match[2] ?? '', lines: [] })
    } else {
      ;(sections.at(-1)?.lines ?? lead).push(line)
    }
  })
  return {
    lead: lead.join('\n').trim(),
    sections: sections.map((s) => ({ title: s.title, body: s.lines.join('\n').trim() })),
  }
}

/** The first H1, as the page title, and the text without it. */
export function takeTitle(text: string): { title: string | undefined; body: string } {
  const lines = text.split('\n')
  const first = lines.findIndex((line) => line.trim() !== '')
  const match = first >= 0 ? /^#[ \t]+(.*?)[ \t]*$/.exec(lines[first] ?? '') : null
  if (!match) return { title: undefined, body: text.trim() }
  return { title: match[1], body: lines.slice(first + 1).join('\n').trim() }
}

/** Moves every heading `by` levels down, to a depth of six. */
export function demote(text: string, by: number): string {
  return mapOutsideFences(text, (line) => {
    const match = /^(#{1,6})([ \t].*)$/.exec(line)
    return match ? `${'#'.repeat(Math.min(6, (match[1] ?? '').length + by))}${match[2] ?? ''}` : line
  })
}

/** The first prose paragraph: no heading, table, list, quote, badge or fence. */
export function firstParagraph(text: string): string {
  const block: string[] = []
  let fenced = false
  for (const line of text.split('\n')) {
    if (/^\s*(```|~~~)/.test(line)) fenced = !fenced
    if (fenced || /^\s*(```|~~~)/.test(line)) continue
    const skip = /^(#|\||>|- |\* |\d+\. |\[!\[)/.test(line.trim())
    if (line.trim() === '') {
      if (block.length > 0) break
    } else if (skip && block.length === 0) {
      continue
    } else {
      block.push(line.trim())
    }
  }
  return oneLine(block.join(' '))
}

/** A page description: the paragraph, cut at a word before `max` characters. */
export function clip(text: string, max = 220): string {
  if (text.length <= max) return text
  const cut = text.slice(0, max).replace(/\s+\S*$/, '')
  return `${cut}…`
}

/** The body rows of the first GFM table in `text`, as trimmed cells, with the header row. */
export function parseTable(text: string): { header: string[]; rows: string[][] } | undefined {
  const lines = text.split('\n').filter((line) => line.trimStart().startsWith('|'))
  if (lines.length < 2) return undefined
  const cells = (line: string) =>
    line
      .trim()
      .replace(/^\|/, '')
      .replace(/\|$/, '')
      .split(/(?<!\\)\|/)
      .map((cell) => cell.trim())
  return { header: cells(lines[0] ?? ''), rows: lines.slice(2).map(cells) }
}

/** A table cell: no line break, and a literal `|` escaped. */
export function cell(text: string): string {
  return oneLine(text).replace(/\|/g, '\\|')
}

/** A YAML frontmatter block with double-quoted values, which JSON.stringify writes validly. */
export function frontmatter(fields: Record<string, string>): string {
  const lines = Object.entries(fields).map(([key, value]) => `${key}: ${JSON.stringify(oneLine(value))}`)
  return `---\n${lines.join('\n')}\n---\n`
}
