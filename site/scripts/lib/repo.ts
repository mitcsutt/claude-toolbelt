// Reads the repo into the model the pages are built from. Nothing here writes, and nothing runs
// repo code: the site renders what the files say.
import { existsSync, globSync, readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { parseFrontmatter, parseTable, splitSections, takeTitle } from './markdown.ts'

export interface ShipRow {
  /** The first backticked name in the Component cell, `/permissions-review` without its slash. */
  name: string
  /** The Component cell as written. */
  component: string
  kind: string
  what: string
}

export interface Skill {
  name: string
  /** Repo path of its SKILL.md. */
  path: string
  data: Record<string, string>
  body: string
}

export interface Agent {
  name: string
  path: string
  data: Record<string, string>
  body: string
}

export interface HookCommand {
  event: string
  matcher: string
  command: string
  timeout: number | undefined
}

export interface McpServer {
  name: string
  transport: string
  runs: string
  env: string[]
}

export interface Plugin {
  name: string
  /** `plugins/<name>`, from the repo root. */
  dir: string
  version: string
  category: string
  /** The README without its `# name` title. */
  readme: string
  /** The README's first paragraph. */
  summary: string
  ships: ShipRow[]
  skills: Skill[]
  agents: Agent[]
  hooks: { description: string; commands: HookCommand[] } | undefined
  mcp: McpServer[]
  bin: string[]
}

export interface Repo {
  root: string
  name: string
  description: string
  plugins: Plugin[]
}

const read = (root: string, path: string) => readFileSync(join(root, path), 'utf8')
const json = <T>(root: string, path: string) => JSON.parse(read(root, path)) as T

export function loadRepo(root: string, errors: string[]): Repo {
  const market = json<{
    name: string
    description: string
    plugins: { name: string; category: string }[]
  }>(root, '.claude-plugin/marketplace.json')
  const plugins = market.plugins.flatMap((entry) => {
    const plugin = loadPlugin(root, entry.name, entry.category, errors)
    return plugin ? [plugin] : []
  })
  return { root, name: market.name, description: market.description, plugins }
}

function loadPlugin(root: string, name: string, category: string, errors: string[]): Plugin | undefined {
  const dir = `plugins/${name}`
  for (const required of [`${dir}/.claude-plugin/plugin.json`, `${dir}/README.md`]) {
    if (!existsSync(join(root, required))) {
      errors.push(`${name}: marketplace.json lists it, but ${required} doesn't exist`)
      return undefined
    }
  }
  const manifest = json<{ version: string }>(root, `${dir}/.claude-plugin/plugin.json`)
  const { title, body: readme } = takeTitle(read(root, `${dir}/README.md`))
  if (title !== name) errors.push(`${dir}/README.md: first line should be "# ${name}"`)

  const shipsSection = splitSections(readme).sections.find((s) => s.title === 'What it ships')
  const ships = shipsSection ? parseShips(shipsSection.body, dir, errors) : []

  const skills = listDirs(root, `${dir}/skills`).flatMap((skill) => {
    const path = `${dir}/skills/${skill}/SKILL.md`
    if (!existsSync(join(root, path))) return []
    const { data, body } = parseFrontmatter(read(root, path))
    return [{ name: data.name ?? skill, path, data, body }]
  })
  const agents = listFiles(root, `${dir}/agents`, '.md').map((file) => {
    const path = `${dir}/agents/${file}`
    const { data, body } = parseFrontmatter(read(root, path))
    return { name: data.name ?? file.replace(/\.md$/, ''), path, data, body }
  })

  return {
    name,
    dir,
    version: manifest.version,
    category,
    readme,
    summary: splitSections(readme).lead.split(/\n\s*\n/)[0]?.replace(/\s+/g, ' ').trim() ?? '',
    ships,
    skills,
    agents,
    hooks: loadHooks(root, dir),
    mcp: loadMcp(root, dir),
    bin: listFiles(root, `${dir}/bin`),
  }
}

function parseShips(section: string, dir: string, errors: string[]): ShipRow[] {
  const table = parseTable(section)
  if (!table || table.header.join('|') !== 'Component|Kind|What') {
    errors.push(`${dir}/README.md: "What it ships" should open with a | Component | Kind | What | table`)
    return []
  }
  return table.rows.flatMap((cells) => {
    const [component = '', kind = '', what = ''] = cells
    const name = /`([^`]+)`/.exec(component)?.[1]?.replace(/^\//, '')
    if (!name) {
      errors.push(`${dir}/README.md: "What it ships" row "${component}" has no \`name\` in backticks`)
      return []
    }
    return [{ name, component, kind, what: what.replace(/\\\|/g, '|') }]
  })
}

function loadHooks(root: string, dir: string): Plugin['hooks'] {
  const path = `${dir}/hooks/hooks.json`
  if (!existsSync(join(root, path))) return undefined
  const file = json<{
    description?: string
    hooks: Record<string, { matcher?: string; hooks: { command: string; timeout?: number }[] }[]>
  }>(root, path)
  const commands = Object.entries(file.hooks).flatMap(([event, groups]) =>
    groups.flatMap((group) =>
      group.hooks.map((hook) => ({
        event,
        matcher: group.matcher ?? '',
        command: hook.command,
        timeout: hook.timeout,
      })),
    ),
  )
  return { description: file.description ?? '', commands }
}

function loadMcp(root: string, dir: string): McpServer[] {
  const path = `${dir}/.mcp.json`
  if (!existsSync(join(root, path))) return []
  const file = json<{
    mcpServers: Record<string, { type?: string; command?: string; args?: string[]; url?: string }>
  }>(root, path)
  return Object.entries(file.mcpServers).map(([name, server]) => ({
    name,
    transport: server.type ?? 'stdio',
    runs: server.url ?? [server.command, ...(server.args ?? [])].filter(Boolean).join(' '),
    env: [...new Set([...JSON.stringify(server).matchAll(/\$\{([A-Z_][A-Z0-9_]*)\}/g)].map((m) => m[1] ?? ''))],
  }))
}

function listDirs(root: string, path: string): string[] {
  if (!existsSync(join(root, path))) return []
  return readdirSync(join(root, path), { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort()
}

function listFiles(root: string, path: string, extension = ''): string[] {
  if (!existsSync(join(root, path))) return []
  return readdirSync(join(root, path), { withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith(extension))
    .map((entry) => entry.name)
    .sort()
}

/**
 * The drift check: every row of a README's "What it ships" table must name something that exists,
 * and every skill, agent and `bin/` command on disk must have a row. Returns one message per miss.
 */
export function checkShips(root: string, plugin: Plugin): string[] {
  const problems: string[] = []
  const where = `${plugin.dir}/README.md`
  const has = (kind: string, name: string) =>
    plugin.ships.some((row) => row.kind.toLowerCase().includes(kind) && row.name === name)

  for (const row of plugin.ships) {
    const kind = row.kind.toLowerCase()
    let exists: boolean
    if (kind.includes('skill')) exists = plugin.skills.some((s) => s.name === row.name)
    else if (kind.includes('agent')) exists = plugin.agents.some((a) => a.name === row.name)
    else if (kind.startsWith('mcp')) exists = plugin.mcp.some((m) => m.name === row.name)
    else if (kind.includes('command')) exists = plugin.bin.includes(row.name)
    else if (/[/.]/.test(row.name)) exists = pathExists(root, plugin.dir, row.name)
    else {
      problems.push(`${where}: "What it ships" row \`${row.name}\` has kind "${row.kind}", which the site can't check`)
      continue
    }
    if (!exists) problems.push(`${where}: "What it ships" names ${row.kind} \`${row.name}\`, which doesn't exist`)
  }
  for (const skill of plugin.skills) {
    if (!has('skill', skill.name)) problems.push(`${where}: skill ${skill.name} has no row in "What it ships"`)
  }
  for (const agent of plugin.agents) {
    if (!has('agent', agent.name)) problems.push(`${where}: agent ${agent.name} has no row in "What it ships"`)
  }
  for (const command of plugin.bin) {
    if (!has('command', command)) problems.push(`${where}: command ${command} has no row in "What it ships"`)
  }
  return problems
}

function pathExists(root: string, dir: string, name: string): boolean {
  const path = join(root, dir, name)
  return name.includes('*') ? globSync(path).length > 0 : existsSync(path)
}
