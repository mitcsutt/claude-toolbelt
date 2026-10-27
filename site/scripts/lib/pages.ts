// Builds the site's pages from the repo model. Every page renders a file that already exists, so a
// fact is written in one place: here only the frames, the tables of manifest data and the links
// between pages are new. Pages are plain Markdown, never escaped (ADR 0046).
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { GeneratedFile } from '@mitcsutt/kiln-docs/generate'
import { createLinkRewriter } from './links.ts'
import {
  cell,
  clip,
  demote,
  firstParagraph,
  frontmatter,
  mapOutsideFences,
  oneLine,
  splitSections,
  takeTitle,
} from './markdown.ts'
import type { Agent, Plugin, Repo, Skill } from './repo.ts'

export interface SiteInput {
  repo: Repo
  repoUrl: string
  branch: string
  errors: string[]
}

const DOCS = '/docs'
const pluginRoute = (p: Plugin) => `${DOCS}/plugins/${p.name}`
const skillRoute = (p: Plugin, s: Skill) => `${pluginRoute(p)}/skills/${s.name}`
const agentRoute = (p: Plugin, a: Agent) => `${pluginRoute(p)}/agents/${a.name}`

/** Contributor docs: repo path, page path inside `contributing/`, and a title when the file has no H1. */
const CONTRIBUTING_PAGES = [
  { source: 'CONTRIBUTING.md', page: 'index', fallback: 'Contributing' },
  { source: 'docs/authoring-plugins.md', page: 'authoring-plugins', fallback: 'Authoring plugins' },
  { source: 'docs/testing.md', page: 'testing', fallback: 'Testing' },
  { source: 'docs/repo-structure.md', page: 'repo-structure', fallback: 'Repo structure' },
  { source: 'docs/ideas.md', page: 'ideas', fallback: 'Ideas' },
  { source: 'SECURITY.md', page: 'security', fallback: 'Security' },
] as const

/** Sections of the root README that became pages of their own: a link to one goes to the page. */
const README_SECTION_PAGES: Record<string, string> = {
  'README.md#install': `${DOCS}#install`,
  'README.md#whats-inside': `${DOCS}/plugins`,
  'README.md#plugin-details': `${DOCS}/plugins`,
  'README.md#statusline': `${DOCS}/statusline`,
  'README.md#scripts': `${DOCS}/statusline#scripts`,
  'README.md#statusline-env-overrides': `${DOCS}/statusline#statusline-env-overrides`,
  'README.md#shared-rules': `${DOCS}/shared-rules`,
  'README.md#contributing': `${DOCS}/contributing`,
}

export function buildPages(input: SiteInput): { files: GeneratedFile[]; landing: Landing } {
  const { repo, repoUrl, branch, errors } = input
  const rules = listRules(repo.root)

  const routes: Record<string, string> = {
    'README.md': DOCS,
    'config/README.md': `${DOCS}/statusline`,
    'docs/README.md': `${DOCS}/contributing`,
    'shared-rules.md': `${DOCS}/shared-rules`,
    ...Object.fromEntries(CONTRIBUTING_PAGES.map((p) => [p.source, contributingRoute(p.page)])),
    ...Object.fromEntries(rules.map((r) => [r.source, `${DOCS}/shared-rules/${r.slug}`])),
  }
  for (const plugin of repo.plugins) {
    routes[`${plugin.dir}/README.md`] = pluginRoute(plugin)
    for (const skill of plugin.skills) routes[skill.path] = skillRoute(plugin, skill)
    for (const agent of plugin.agents) routes[agent.path] = agentRoute(plugin, agent)
  }

  const links = createLinkRewriter({
    root: repo.root,
    repoUrl,
    branch,
    routes,
    anchorRoutes: README_SECTION_PAGES,
  })
  const files: GeneratedFile[] = []
  const page = (path: string, fields: Record<string, string>, body: string) =>
    files.push({ path, content: `${frontmatter(fields)}\n${body.trim()}\n` })
  const meta = (path: string, title: string, pages?: string[], root = false) =>
    files.push({
      path: `${path}meta.json`,
      content: `${JSON.stringify({ title, ...(root ? { root: true } : {}), ...(pages ? { pages } : {}) }, null, 2)}\n`,
    })

  const readme = takeTitle(readFileSync(join(repo.root, 'README.md'), 'utf8')).body
  const readmeSections = splitSections(readme)
  const readmeSection = (title: string) => {
    const found = readmeSections.sections.find((s) => s.title === title)
    if (!found) errors.push(`README.md: no "## ${title}" section, which the site renders`)
    return found?.body ?? ''
  }

  // Overview
  page(
    'index.md',
    { title: repo.name, description: repo.description },
    [
      links.rewrite(firstParagraph(readmeSections.lead), { source: 'README.md', route: DOCS }),
      "## What's inside",
      pluginTable(repo.plugins, false),
      '## Install',
      links.rewrite(readmeSection('Install'), { source: 'README.md', route: DOCS }),
    ].join('\n\n'),
  )
  meta('', repo.name, ['index', 'plugins', 'reference', 'statusline', 'shared-rules', 'contributing'])

  // Plugins
  page(
    'plugins/index.md',
    { title: 'Plugins', description: `The ${String(repo.plugins.length)} plugins in ${repo.name}, and what each ships.` },
    [
      'Install only the ones you want. Each page has the plugin\'s README, plus its skills, agents, hooks, commands and MCP servers.',
      pluginTable(repo.plugins, true),
    ].join('\n\n'),
  )
  meta('plugins/', 'Plugins', ['index', ...repo.plugins.map((p) => p.name)], true)

  for (const plugin of repo.plugins) {
    const base = `plugins/${plugin.name}`
    page(`${base}/index.md`, { title: plugin.name, description: clip(plain(plugin.summary)) }, pluginBody(plugin))
    meta(`${base}/`, plugin.name, [
      'index',
      ...(plugin.skills.length > 0 ? ['skills'] : []),
      ...(plugin.agents.length > 0 ? ['agents'] : []),
    ])
    if (plugin.skills.length > 0) {
      meta(`${base}/skills/`, 'Skills', plugin.skills.map((s) => s.name))
      for (const skill of plugin.skills) {
        page(`${base}/skills/${skill.name}.md`, componentFields(plugin, skill.name, 'skill'), skillBody(plugin, skill))
      }
    }
    if (plugin.agents.length > 0) {
      meta(`${base}/agents/`, 'Agents', plugin.agents.map((a) => a.name))
      for (const agent of plugin.agents) {
        page(`${base}/agents/${agent.name}.md`, componentFields(plugin, agent.name, 'agent'), agentBody(plugin, agent))
      }
    }
  }

  // Reference
  page(
    'reference/index.md',
    { title: 'Reference', description: 'Every skill and agent in the kit, one table each.' },
    `- [Skills](${DOCS}/reference/skills): every skill, with the plugin it ships in and who can invoke it.\n- [Agents](${DOCS}/reference/agents): every subagent, with its tools.`,
  )
  page(
    'reference/skills.md',
    { title: 'Skills', description: 'Every skill in the kit, with its plugin and who can invoke it.' },
    table(
      ['Skill', 'Plugin', 'Invoked by', 'What'],
      repo.plugins.flatMap((p) =>
        p.skills.map((s) => [
          `[\`/${s.name}\`](${skillRoute(p, s)})`,
          `[${p.name}](${pluginRoute(p)})`,
          invocation(s).short,
          cell(plain(shipRow(p, 'skill', s.name)?.what ?? '')),
        ]),
      ),
    ),
  )
  page(
    'reference/agents.md',
    { title: 'Agents', description: 'Every subagent in the kit, with its plugin and its tools.' },
    table(
      ['Agent', 'Plugin', 'Tools', 'What'],
      repo.plugins.flatMap((p) =>
        p.agents.map((a) => [
          `[\`${p.name}:${a.name}\`](${agentRoute(p, a)})`,
          `[${p.name}](${pluginRoute(p)})`,
          tools(a)
            .map((t) => `\`${t}\``)
            .join(', '),
          cell(plain(shipRow(p, 'agent', a.name)?.what ?? '')),
        ]),
      ),
    ),
  )
  meta('reference/', 'Reference', ['index', 'skills', 'agents'], true)

  // Statusline: the README's section, then what config/ holds
  const statusline = readmeSection('Statusline')
  const config = takeTitle(readFileSync(join(repo.root, 'config/README.md'), 'utf8')).body
  page(
    'statusline.md',
    { title: 'Statusline', description: clip(plain(firstParagraph(statusline))) },
    [
      links.rewrite(statusline, { source: 'README.md', route: `${DOCS}/statusline`, droppedAnchors: new Set(['statusline']) }),
      '## What config/ holds',
      links.rewrite(demote(config, 1), { source: 'config/README.md', route: `${DOCS}/statusline` }),
      sourceLine('README.md', 'config/README.md'),
    ].join('\n\n'),
  )

  // Shared rules
  const sharedRules = takeTitle(readFileSync(join(repo.root, 'shared-rules.md'), 'utf8'))
  const sharedRoute = `${DOCS}/shared-rules`
  page(
    'shared-rules/index.md',
    {
      title: 'Shared rules',
      description: 'The author\'s own Claude Code rules, portable through an @ import. Personal rules, not repo policy.',
    },
    [
      '> **These are personal rules.** They are how the author wants Claude Code to behave, not the policy of this repo. Import them if they suit you, or read them as an example of a rules file.',
      links.rewrite(readmeSection('Shared rules'), { source: 'README.md', route: sharedRoute }),
      '## The rules',
      links.rewrite(demote(sharedRules.body, 1), { source: 'shared-rules.md', route: sharedRoute }),
      sourceLine('shared-rules.md'),
    ].join('\n\n'),
  )
  for (const rule of rules) {
    const { title, body } = takeTitle(readFileSync(join(repo.root, rule.source), 'utf8'))
    const route = `${sharedRoute}/${rule.slug}`
    page(
      `shared-rules/${rule.slug}.md`,
      { title: title ?? rule.slug, description: clip(plain(firstParagraph(body))) || `Situational rule: ${title ?? rule.slug}.` },
      [links.rewrite(body, { source: rule.source, route }), sourceLine(rule.source)].join('\n\n'),
    )
  }
  meta('shared-rules/', 'Shared rules', ['index', ...rules.map((r) => r.slug)], true)

  // Contributing
  for (const entry of CONTRIBUTING_PAGES) {
    const { title, body } = takeTitle(readFileSync(join(repo.root, entry.source), 'utf8'))
    const route = contributingRoute(entry.page)
    page(
      `contributing/${entry.page}.md`,
      { title: title ?? entry.fallback, description: clip(plain(firstParagraph(body))) },
      [links.rewrite(body, { source: entry.source, route }), sourceLine(entry.source)].join('\n\n'),
    )
  }
  meta('contributing/', 'Contributing', CONTRIBUTING_PAGES.map((p) => p.page), true)

  errors.push(...links.errors)
  return { files, landing: landing(repo, firstParagraph(readmeSections.lead), readmeSection('Install')) }

  function contributingRoute(slug: string): string {
    return slug === 'index' ? `${DOCS}/contributing` : `${DOCS}/contributing/${slug}`
  }

  function sourceLine(...paths: string[]): string {
    return `Source: ${paths.map((p) => `[\`${p}\`](${links.github(p)})`).join(', ')}`
  }

  function pluginBody(plugin: Plugin): string {
    const route = pluginRoute(plugin)
    const { lead, sections } = splitSections(plugin.readme)
    const context = { source: `${plugin.dir}/README.md`, route }
    const generated = [
      hooksSection(plugin),
      commandsSection(plugin),
      mcpSection(plugin),
    ].filter((s) => s !== '')
    const parts = [
      links.rewrite(lead, context),
      list([
        ['Version', `\`${plugin.version}\``],
        ['Category', plugin.category],
        ['Install', `\`/plugin install ${plugin.name}@${repo.name}\``],
        ['Source', `[\`${plugin.dir}\`](${links.github(plugin.dir, 'tree')})`],
      ]),
    ]
    const hasShips = sections.some((s) => s.title === 'What it ships')
    let inserted = false
    for (const section of sections) {
      if (!hasShips && section.title === 'Install' && !inserted) {
        parts.push(...generated)
        inserted = true
      }
      const body = section.title === 'What it ships' ? linkShips(plugin, section.body) : section.body
      parts.push(`## ${section.title}\n\n${links.rewrite(body, context)}`)
      if (section.title === 'What it ships') parts.push(...generated)
    }
    return parts.join('\n\n')
  }

  /** Links each skill and agent in the "What it ships" table to its page. */
  function linkShips(plugin: Plugin, body: string): string {
    return mapOutsideFences(body, (line) => {
      for (const row of plugin.ships) {
        const kind = row.kind.toLowerCase()
        const target = kind.includes('skill')
          ? plugin.skills.find((s) => s.name === row.name)
          : kind.includes('agent')
            ? plugin.agents.find((a) => a.name === row.name)
            : undefined
        if (!target || !line.trimStart().startsWith('|') || !line.includes(row.component)) continue
        const route = kind.includes('skill')
          ? skillRoute(plugin, target as Skill)
          : agentRoute(plugin, target as Agent)
        return line.replace(row.component, `[${row.component}](${route})`)
      }
      return line
    })
  }

  function hooksSection(plugin: Plugin): string {
    if (!plugin.hooks) return ''
    return [
      '## Hooks',
      plugin.hooks.description,
      table(
        ['Event', 'Matcher', 'Command', 'Timeout'],
        plugin.hooks.commands.map((h) => [
          `\`${h.event}\``,
          h.matcher ? `\`${cell(h.matcher)}\`` : 'every time',
          `\`${cell(h.command)}\``,
          h.timeout === undefined ? '' : `${String(h.timeout)} s`,
        ]),
      ),
      `From [\`${plugin.dir}/hooks/hooks.json\`](${links.github(`${plugin.dir}/hooks/hooks.json`)}).`,
    ]
      .filter((part) => part !== '')
      .join('\n\n')
  }

  function commandsSection(plugin: Plugin): string {
    if (plugin.bin.length === 0) return ''
    return [
      '## Commands on PATH',
      `Executables in \`${plugin.dir}/bin/\`, on your PATH while the plugin is enabled.`,
      table(
        ['Command', 'What'],
        plugin.bin.map((name) => [
          `[\`${name}\`](${links.github(`${plugin.dir}/bin/${name}`)})`,
          cell(plugin.ships.find((r) => r.name === name)?.what ?? ''),
        ]),
      ),
    ].join('\n\n')
  }

  function mcpSection(plugin: Plugin): string {
    if (plugin.mcp.length === 0) return ''
    return [
      '## MCP servers',
      table(
        ['Server', 'Transport', 'Runs', 'Environment'],
        plugin.mcp.map((m) => [
          `\`${m.name}\``,
          m.transport,
          `\`${cell(m.runs)}\``,
          m.env.length > 0 ? m.env.map((e) => `\`${e}\``).join(', ') : 'none',
        ]),
      ),
      `${plugin.mcp.some((m) => m.env.length > 0) ? 'A server that names an environment variable needs it set before Claude Code starts. ' : ''}From [\`${plugin.dir}/.mcp.json\`](${links.github(`${plugin.dir}/.mcp.json`)}).`,
    ].join('\n\n')
  }

  function componentFields(plugin: Plugin, name: string, kind: 'skill' | 'agent') {
    const what = shipRow(plugin, kind, name)?.what ?? ''
    return { title: kind === 'skill' ? `/${name}` : `${plugin.name}:${name}`, description: clip(plain(what)) }
  }

  function skillBody(plugin: Plugin, skill: Skill): string {
    const mode = invocation(skill)
    const facts: [string, string][] = [
      ['Plugin', `[\`${plugin.name}\`](${pluginRoute(plugin)}) ${plugin.version}`],
      ['Name', `\`/${skill.name}\`, or \`${plugin.name}:${skill.name}\` when another plugin has a skill of that name`],
      ['Invoked by', mode.long],
    ]
    if (skill.data['allowed-tools']) facts.push(['Tools', skill.data['allowed-tools']])
    facts.push(['Source', `[\`${skill.path}\`](${links.github(skill.path)})`])
    return [
      list(facts),
      links.rewrite(shipRow(plugin, 'skill', skill.name)?.what ?? '', { source: `${plugin.dir}/README.md`, route: skillRoute(plugin, skill) }),
      readsSection(plugin, skill.path, skill.data.description ?? '', skill.body, skillRoute(plugin, skill), 'skill'),
    ].join('\n\n')
  }

  function agentBody(plugin: Plugin, agent: Agent): string {
    const facts: [string, string][] = [
      ['Plugin', `[\`${plugin.name}\`](${pluginRoute(plugin)}) ${plugin.version}`],
      ['Name', `\`${plugin.name}:${agent.name}\`, a subagent Claude dispatches, or you can ask for it by name`],
      ['Tools', tools(agent).map((t) => `\`${t}\``).join(', ')],
    ]
    if (agent.data.color) facts.push(['Colour', agent.data.color])
    facts.push(['Source', `[\`${agent.path}\`](${links.github(agent.path)})`])
    return [
      list(facts),
      links.rewrite(shipRow(plugin, 'agent', agent.name)?.what ?? '', { source: `${plugin.dir}/README.md`, route: agentRoute(plugin, agent) }),
      readsSection(plugin, agent.path, agent.data.description ?? '', agent.body, agentRoute(plugin, agent), 'agent'),
    ].join('\n\n')
  }

  function readsSection(
    plugin: Plugin,
    path: string,
    description: string,
    body: string,
    route: string,
    kind: 'skill' | 'agent',
  ): string {
    const quoted = description
      .split('\n')
      .map((line) => (line === '' ? '>' : `> ${line}`))
      .join('\n')
    const { body: withoutTitle } = takeTitle(body)
    return [
      '## What Claude reads',
      `This is the ${kind} as Claude loads it from \`${path}\`. The description is what Claude matches against the task to decide whether to use it:`,
      quoted,
      `The ${kind === 'skill' ? 'skill body' : 'agent prompt'} follows, as written.`,
      links.rewrite(demote(withoutTitle, 1), { source: path, route }),
    ].join('\n\n')
  }
}

export interface Landing {
  tagline: string
  install: string[]
  plugins: { name: string; version: string; category: string; summary: string; href: string; skills: number; agents: number }[]
}

function landing(repo: Repo, tagline: string, install: string): Landing {
  const fence = /```[a-z]*\n([\s\S]*?)```/.exec(install)?.[1] ?? ''
  return {
    tagline,
    install: fence.split('\n').filter((line) => line.trim() !== ''),
    plugins: repo.plugins.map((p) => ({
      name: p.name,
      version: p.version,
      category: p.category,
      summary: plain(p.summary),
      href: pluginRoute(p),
      skills: p.skills.length,
      agents: p.agents.length,
    })),
  }
}

function pluginTable(plugins: readonly Plugin[], withCategory: boolean): string {
  return table(
    withCategory ? ['Plugin', 'Version', 'Category', 'What it does'] : ['Plugin', 'Version', 'What it does'],
    plugins.map((p) => [
      `[\`${p.name}\`](${pluginRoute(p)})`,
      p.version,
      ...(withCategory ? [p.category] : []),
      cell(p.summary),
    ]),
  )
}

function shipRow(plugin: Plugin, kind: string, name: string) {
  return plugin.ships.find((r) => r.kind.toLowerCase().includes(kind) && r.name === name)
}

function invocation(skill: Skill): { short: string; long: string } {
  if (skill.data['disable-model-invocation'] === 'true') {
    return { short: 'You', long: 'You only. Claude never runs it on its own, so you start it with the slash command.' }
  }
  if (skill.data['user-invocable'] === 'false') {
    return { short: 'Claude', long: 'Claude only. It isn\'t offered as a slash command.' }
  }
  return {
    short: 'You or Claude',
    long: 'You or Claude. You can run the slash command, and Claude can load it when the description matches the task.',
  }
}

function tools(agent: Agent): string[] {
  return (agent.data.tools ?? '')
    .split(',')
    .map((t) => t.trim())
    .filter(Boolean)
}

function list(facts: readonly (readonly [string, string])[]): string {
  return facts.map(([label, value]) => `- **${label}:** ${value}`).join('\n')
}

function table(header: readonly string[], rows: readonly (readonly string[])[]): string {
  const line = (cells: readonly string[]) => `| ${cells.join(' | ')} |`
  return [line(header), line(header.map(() => '---')), ...rows.map(line)].join('\n')
}

/** Text for a description or a table cell: no links, code ticks or emphasis. */
function plain(text: string): string {
  return oneLine(
    text
      .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
      .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
      .replace(/`/g, '')
      .replace(/\*\*/g, ''),
  )
}

function listRules(root: string): { source: string; slug: string }[] {
  return readdirSync(join(root, 'rules'))
    .filter((file) => file.endsWith('.md'))
    .sort()
    .map((file) => ({ source: `rules/${file}`, slug: file.replace(/\.md$/, '') }))
}
