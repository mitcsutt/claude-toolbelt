#!/usr/bin/env node
/**
 * generate-timeline.mjs
 * Parses Claude Code JSONL transcripts into self-contained HTML timeline visualizations.
 *
 * Usage:
 *   node generate-timeline.mjs [--file <path.jsonl>] [--output <path.html>] [--project <cwd>]
 *
 * If --file is omitted, discovers the most recent session for --project (or cwd).
 * If --output is omitted, writes to $TMPDIR/claude/session-timeline-<id>.html
 */

import fs from 'fs'
import os from 'os'
import path from 'path'
import readline from 'readline'

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------
const argv = process.argv.slice(2)
function flag(name, dflt) {
  const i = argv.indexOf(name)
  if (i === -1) return dflt
  const v = argv[i + 1]
  return v === undefined || v.startsWith('--') ? true : v
}

const FILE = flag('--file', null)
const OUTPUT = flag('--output', null)
const PROJECT = flag('--project', process.cwd())
const LIST = argv.includes('--list')

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const TOOL_DISPLAY = {
  Bash: 'Terminal',
  Read: 'Read File',
  Write: 'Write File',
  Edit: 'Edit File',
  Agent: 'Subagent',
  Skill: 'Skill',
  Grep: 'Search',
  Glob: 'Search',
  WebFetch: 'Web Fetch',
  WebSearch: 'Web Search',
  ToolSearch: 'ToolSearch',
  TaskCreate: 'Create Task',
  TaskUpdate: 'Update Task',
  TaskGet: 'Get Task',
  TaskList: 'List Tasks',
  Monitor: 'Monitor',
  EnterPlanMode: 'Plan Mode',
  ExitPlanMode: 'Exit Plan',
  NotebookEdit: 'Notebook Edit',
  LSP: 'LSP',
}

const TOOL_ICON = {
  Bash: 'terminal',
  Read: 'file-text',
  Write: 'file-plus',
  Edit: 'edit',
  Agent: 'users',
  Skill: 'zap',
  Grep: 'search',
  Glob: 'search',
  WebFetch: 'globe',
  WebSearch: 'globe',
  ToolSearch: 'map',
  TaskCreate: 'plus-square',
  TaskUpdate: 'check-circle',
  TaskGet: 'check-square',
  TaskList: 'check-square',
  Monitor: 'tool',
  EnterPlanMode: 'tool',
  ExitPlanMode: 'tool',
}

const IDLE_GAP_MS = 5 * 60 * 1000
const TIME_GAP_DISPLAY_MS = 30 * 1000

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------
function escapeHtml(s) {
  if (!s) return ''
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#x27;')
}

function formatDuration(ms) {
  if (ms < 0) ms = 0
  const s = Math.floor(ms / 1000)
  const m = Math.floor(s / 60)
  const h = Math.floor(m / 60)
  if (h > 0) return `${h}h ${m % 60}m ${s % 60}s`
  if (m > 0) return `${m}m ${s % 60}s`
  return `${(ms / 1000).toFixed(1)}s`
}

function formatTokens(n) {
  if (n == null) return '0'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

function formatTime(d) {
  return d.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: true,
  })
}

function formatDate(d) {
  return d.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'long',
    day: '2-digit',
  })
}

function truncate(s, n) {
  if (!s) return ''
  s = String(s)
  return s.length > n ? s.slice(0, n) + '…' : s
}

function modelShort(model) {
  if (!model) return ''
  if (model.includes('opus')) return 'Opus'
  if (model.includes('sonnet')) return 'Sonnet'
  if (model.includes('haiku')) return 'Haiku'
  return model.split('-').pop()
}

function modelClass(model) {
  if (!model) return ''
  if (model.includes('opus')) return 'opus'
  if (model.includes('sonnet')) return 'sonnet'
  if (model.includes('haiku')) return 'haiku'
  return ''
}

function toolDisplayName(name) {
  if (TOOL_DISPLAY[name]) return TOOL_DISPLAY[name]
  const m = name.match(/^mcp__(.+?)__(.+)$/)
  if (m) return `MCP: ${m[1]}/${m[2]}`
  return name
}

function toolIcon(name) {
  if (TOOL_ICON[name]) return TOOL_ICON[name]
  if (name.startsWith('mcp__')) return 'plug'
  return 'tool'
}

function toolDetail(name, input) {
  if (!input) return ''
  try {
    if (name === 'Bash') return escapeHtml(input.description || truncate(input.command, 80))
    if (name === 'Read') return escapeHtml(`Reading ${path.basename(input.file_path || '')}`)
    if (name === 'Write') return escapeHtml(`Writing ${path.basename(input.file_path || '')}`)
    if (name === 'Edit') return escapeHtml(`Editing ${path.basename(input.file_path || '')}`)
    if (name === 'Agent') return escapeHtml(input.description || '')
    if (name === 'Skill') return escapeHtml(`Invoking /${input.skill || ''}`)
    if (name === 'ToolSearch') return escapeHtml(JSON.stringify({ query: input.query, max_results: input.max_results }))
    if (name === 'TaskCreate') return escapeHtml(truncate(JSON.stringify({ subject: input.subject, description: input.description }), 100))
    if (name === 'TaskUpdate') return escapeHtml(JSON.stringify({ taskId: input.taskId, status: input.status }))
    if (name.startsWith('mcp__')) {
      const keys = Object.keys(input)
      if (keys.length === 0) return ''
      const key = keys[0]
      return escapeHtml(`${key}: ${truncate(String(input[key]), 60)}`)
    }
    return ''
  } catch {
    return ''
  }
}

// ---------------------------------------------------------------------------
// File discovery
// ---------------------------------------------------------------------------
function projectSlug(dir) {
  return dir.replace(/\//g, '-')
}

function discoverFile() {
  if (FILE) return FILE

  const claudeProjects = path.join(os.homedir(), '.claude', 'projects')
  const slug = projectSlug(PROJECT)

  // Try exact match first, then prefix match
  let projectDir = path.join(claudeProjects, slug)
  if (!fs.existsSync(projectDir)) {
    const dirs = fs.readdirSync(claudeProjects).filter(d => d.startsWith(slug.slice(0, 20)))
    if (dirs.length === 0) {
      console.error(`No project directory found for: ${PROJECT}`)
      process.exit(1)
    }
    // Pick the one matching our full slug or the longest match
    const best = dirs.sort((a, b) => b.length - a.length).find(d => slug.startsWith(d) || d.startsWith(slug)) || dirs[0]
    projectDir = path.join(claudeProjects, best)
  }

  const files = fs.readdirSync(projectDir)
    .filter(f => f.endsWith('.jsonl') && !f.includes('/'))
    .map(f => ({
      name: f,
      path: path.join(projectDir, f),
      mtime: fs.statSync(path.join(projectDir, f)).mtimeMs,
    }))
    .sort((a, b) => b.mtime - a.mtime)

  if (LIST) {
    console.log('Recent sessions:')
    for (const f of files.slice(0, 15)) {
      const id = f.name.replace('.jsonl', '')
      const date = new Date(f.mtime)
      const size = (fs.statSync(f.path).size / 1024).toFixed(0)
      console.log(`  ${id}  ${date.toLocaleString()}  ${size}KB`)
    }
    process.exit(0)
  }

  if (files.length === 0) {
    console.error(`No session files found in: ${projectDir}`)
    process.exit(1)
  }

  return files[0].path
}

// ---------------------------------------------------------------------------
// JSONL parsing
// ---------------------------------------------------------------------------
async function parseJsonl(filePath) {
  const entries = []
  const stream = fs.createReadStream(filePath, { encoding: 'utf8' })
  const rl = readline.createInterface({ input: stream, crlfDelay: Infinity })

  for await (const line of rl) {
    if (!line.trim()) continue
    try {
      entries.push(JSON.parse(line))
    } catch {
      // skip malformed lines
    }
  }
  return entries
}

// ---------------------------------------------------------------------------
// Analysis
// ---------------------------------------------------------------------------
function analyze(entries) {
  let title = ''
  let sessionId = ''
  let firstTs = null
  let lastTs = null
  const seenRequests = new Map() // requestId -> { usage, model, speed }
  const toolCounts = {}
  const agentCalls = [] // { description, type, timestamp, toolUseId }
  const models = new Set()
  let turnCount = 0
  let subagentCount = 0

  // Token accumulators
  let totalInputTokens = 0
  let totalOutputTokens = 0

  // Timeline events (chronological)
  const events = []

  // Separate main conversation from sidechains
  const mainEntries = entries.filter(e => !e.isSidechain)

  // First pass: extract title and sessionId
  for (const e of entries) {
    if (e.type === 'ai-title' && e.aiTitle) title = e.aiTitle
    if (e.sessionId && !sessionId) sessionId = e.sessionId
  }

  // Second pass: build timeline and compute stats
  let turnStartTs = null
  let turnMessages = 0
  let inTurn = false

  for (const e of mainEntries) {
    const ts = e.timestamp ? new Date(e.timestamp) : null
    if (ts) {
      if (!firstTs || ts < firstTs) firstTs = ts
      if (!lastTs || ts > lastTs) lastTs = ts
    }

    if (e.type === 'user' && !e.isMeta) {
      const content = e.message?.content
      if (typeof content === 'string' && !content.includes('<tool_result>') && !content.includes('interrupt_marker')) {
        // New user turn
        if (inTurn && turnStartTs) {
          // Close previous turn
          const dur = ts ? ts - turnStartTs : 0
          events.push({
            type: 'turn-marker',
            timestamp: ts,
            turnNumber: turnCount,
            duration: dur,
            messages: turnMessages,
          })
        }

        turnCount++
        turnStartTs = ts
        turnMessages = 1
        inTurn = true

        // Check for time gap
        if (events.length > 0) {
          const lastEvent = events[events.length - 1]
          const lastEventTs = lastEvent.timestamp
          if (lastEventTs && ts && (ts - lastEventTs) > TIME_GAP_DISPLAY_MS) {
            events.push({
              type: 'time-gap',
              timestamp: ts,
              gap: ts - lastEventTs,
            })
          }
        }

        // Extract user text
        let text = ''
        if (typeof content === 'string') {
          text = content
            .replace(/<command-name>.*?<\/command-name>/gs, '')
            .replace(/<command-message>.*?<\/command-message>/gs, '')
            .replace(/<command-args>.*?<\/command-args>/gs, '')
            .replace(/<system-reminder>[\s\S]*?<\/system-reminder>/g, '')
            .replace(/<local-command-caveat>[\s\S]*?<\/local-command-caveat>/g, '')
            .replace(/<local-command-stdout>[\s\S]*?<\/local-command-stdout>/g, '')
            .trim()
        }

        if (text) {
          events.push({
            type: 'user',
            timestamp: ts,
            text,
          })
        }
      } else {
        turnMessages++
      }
    }

    if (e.type === 'assistant') {
      turnMessages++
      const msg = e.message || {}
      const requestId = e.requestId || msg.id
      const contentBlocks = msg.content || []
      const usage = msg.usage || {}
      const model = msg.model || ''
      const speed = usage.speed || 'standard'

      if (model) models.add(model)

      // Deduplicate token counting by requestId
      if (requestId) {
        const existing = seenRequests.get(requestId)
        if (!existing) {
          seenRequests.set(requestId, { usage, model, speed })
          totalInputTokens += (usage.input_tokens || 0) + (usage.cache_creation_input_tokens || 0) + (usage.cache_read_input_tokens || 0)
          totalOutputTokens += usage.output_tokens || 0
        } else if ((usage.output_tokens || 0) > (existing.usage.output_tokens || 0)) {
          // Update with better token count
          totalOutputTokens += (usage.output_tokens || 0) - (existing.usage.output_tokens || 0)
          seenRequests.set(requestId, { usage, model, speed })
        }
      }

      // Extract text and tool_use content
      const textBlocks = contentBlocks.filter(b => b.type === 'text' && b.text)
      const toolBlocks = contentBlocks.filter(b => b.type === 'tool_use')

      // Skip entries that only have thinking
      if (textBlocks.length === 0 && toolBlocks.length === 0) continue

      if (textBlocks.length > 0) {
        const text = textBlocks.map(b => b.text).join('\n')
        events.push({
          type: 'assistant-text',
          timestamp: ts,
          model,
          speed,
          outputTokens: usage.output_tokens || 0,
          text,
        })
      }

      if (toolBlocks.length > 0) {
        const tools = toolBlocks.map(b => ({
          name: b.name,
          displayName: toolDisplayName(b.name),
          icon: toolIcon(b.name),
          detail: toolDetail(b.name, b.input),
        }))

        // Count tools
        for (const b of toolBlocks) {
          const dn = toolDisplayName(b.name)
          toolCounts[dn] = (toolCounts[dn] || 0) + 1

          // Track Agent calls for subagent linking
          if (b.name === 'Agent') {
            subagentCount++
            agentCalls.push({
              description: b.input?.description || 'Subagent',
              agentType: b.input?.subagent_type || 'general-purpose',
              model: b.input?.model || '',
              timestamp: ts,
              toolUseId: b.id,
            })
          }
        }

        events.push({
          type: 'assistant-tools',
          timestamp: ts,
          model,
          speed,
          outputTokens: usage.output_tokens || 0,
          tools,
        })
      }
    }
  }

  // Close final turn
  if (inTurn && turnStartTs && lastTs) {
    events.push({
      type: 'turn-marker',
      timestamp: lastTs,
      turnNumber: turnCount,
      duration: lastTs - turnStartTs,
      messages: turnMessages,
    })
  }

  // If no ai-title, derive from first user message
  if (!title) {
    const firstUser = events.find(e => e.type === 'user')
    if (firstUser) title = truncate(firstUser.text, 60)
  }

  const duration = firstTs && lastTs ? lastTs - firstTs : 0

  // Sort tool counts
  const sortedTools = Object.entries(toolCounts)
    .sort((a, b) => b[1] - a[1])

  // Sort models for display
  const modelList = [...models].map(modelShort).sort()

  return {
    title,
    sessionId,
    firstTs,
    lastTs,
    duration,
    turnCount,
    subagentCount,
    totalInputTokens,
    totalOutputTokens,
    sortedTools,
    modelList,
    events,
    agentCalls,
  }
}

// ---------------------------------------------------------------------------
// Subagent analysis
// ---------------------------------------------------------------------------
async function analyzeSubagents(filePath, agentCalls) {
  const sessionId = path.basename(filePath, '.jsonl')
  const projectDir = path.dirname(filePath)
  const subagentDir = path.join(projectDir, sessionId, 'subagents')

  if (!fs.existsSync(subagentDir)) return []

  const files = fs.readdirSync(subagentDir)
    .filter(f => f.endsWith('.jsonl'))
    .sort()

  const subagents = []
  for (const file of files) {
    const jsonlPath = path.join(subagentDir, file)
    const metaPath = jsonlPath.replace('.jsonl', '.meta.json')

    let meta = {}
    if (fs.existsSync(metaPath)) {
      try { meta = JSON.parse(fs.readFileSync(metaPath, 'utf8')) } catch { /* skip */ }
    }

    const entries = await parseJsonl(jsonlPath)
    const assistantEntries = entries.filter(e => e.type === 'assistant')
    const seenReqs = new Map()
    let inputTok = 0
    let outputTok = 0
    let model = ''
    const toolCnts = {}
    let lastText = ''
    let firstTs = null
    let lastTs = null

    for (const e of entries) {
      const ts = e.timestamp ? new Date(e.timestamp) : null
      if (ts) {
        if (!firstTs || ts < firstTs) firstTs = ts
        if (!lastTs || ts > lastTs) lastTs = ts
      }

      if (e.type === 'assistant') {
        const msg = e.message || {}
        const rId = e.requestId || msg.id
        const usage = msg.usage || {}
        if (msg.model) model = msg.model

        if (rId && !seenReqs.has(rId)) {
          seenReqs.set(rId, true)
          inputTok += (usage.input_tokens || 0) + (usage.cache_creation_input_tokens || 0) + (usage.cache_read_input_tokens || 0)
          outputTok += usage.output_tokens || 0
        }

        for (const b of (msg.content || [])) {
          if (b.type === 'tool_use') {
            const dn = toolDisplayName(b.name)
            toolCnts[dn] = (toolCnts[dn] || 0) + 1
          }
          if (b.type === 'text' && b.text) {
            lastText = b.text
          }
        }
      }
    }

    const duration = firstTs && lastTs ? lastTs - firstTs : 0

    // Try to match to an agent call by timestamp proximity
    let agentType = meta.agentType || ''
    let description = ''
    if (firstTs) {
      let bestMatch = null
      let bestDelta = Infinity
      for (const ac of agentCalls) {
        if (!ac.timestamp) continue
        const delta = Math.abs(firstTs - ac.timestamp)
        if (delta < bestDelta) {
          bestDelta = delta
          bestMatch = ac
        }
      }
      if (bestMatch && bestDelta < 30000) {
        if (!agentType) agentType = bestMatch.agentType
        description = bestMatch.description
      }
    }

    if (!agentType) {
      // Extract from filename: agent-a<label>-<hash>.jsonl
      const fm = file.match(/^agent-a(.+?)-[a-f0-9]+\.jsonl$/)
      if (fm) agentType = fm[1]
    }

    const sortedTools = Object.entries(toolCnts).sort((a, b) => b[1] - a[1])

    subagents.push({
      agentType: agentType || 'general-purpose',
      description: description || agentType || 'Subagent',
      duration,
      turns: entries.length,
      inputTokens: inputTok,
      outputTokens: outputTok,
      model,
      sortedTools,
      summary: truncate(lastText, 300),
      startTs: firstTs,
    })
  }

  // Sort by start time
  subagents.sort((a, b) => (a.startTs || 0) - (b.startTs || 0))
  return subagents
}

// ---------------------------------------------------------------------------
// HTML generation
// ---------------------------------------------------------------------------
function renderCss() {
  return `
:root {
    --bg: #0d1117;
    --surface: #161b22;
    --surface2: #1c2333;
    --border: #30363d;
    --text: #e6edf3;
    --text-dim: #8b949e;
    --accent: #c084fc;
    --accent2: #818cf8;
    --user-bg: #1a1f35;
    --user-border: #3b4876;
    --assistant-bg: #161b22;
    --green: #3fb950;
    --orange: #d29922;
    --red: #f85149;
    --blue: #58a6ff;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
}

.container {
    max-width: 960px;
    margin: 0 auto;
    padding: 2rem 1.5rem;
}

.header {
    text-align: center;
    margin-bottom: 3rem;
    padding-bottom: 2rem;
    border-bottom: 1px solid var(--border);
}

.header h1 {
    font-size: 2rem;
    font-weight: 700;
    margin-bottom: 0.5rem;
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}

.header .subtitle {
    color: var(--text-dim);
    font-size: 1.1rem;
}

.stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 1rem;
    margin-bottom: 2.5rem;
}

.stat-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.25rem;
    text-align: center;
}

.stat-card .stat-value {
    font-size: 1.8rem;
    font-weight: 700;
    color: var(--accent);
    display: block;
}

.stat-card .stat-label {
    color: var(--text-dim);
    font-size: 0.85rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

.section {
    margin-bottom: 2.5rem;
}

.section h2 {
    font-size: 1.3rem;
    font-weight: 600;
    margin-bottom: 1rem;
    color: var(--text);
    display: flex;
    align-items: center;
    gap: 0.5rem;
}

.section h2::before {
    content: '';
    display: inline-block;
    width: 4px;
    height: 1.3rem;
    background: var(--accent);
    border-radius: 2px;
}

.tool-table {
    width: 100%;
    border-collapse: collapse;
    background: var(--surface);
    border-radius: 12px;
    overflow: hidden;
    border: 1px solid var(--border);
}

.tool-table th {
    text-align: left;
    padding: 0.75rem 1rem;
    background: var(--surface2);
    color: var(--text-dim);
    font-weight: 600;
    font-size: 0.85rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

.tool-table td {
    padding: 0.6rem 1rem;
    border-top: 1px solid var(--border);
    font-size: 0.95rem;
}

.tool-table .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
    color: var(--accent);
    font-weight: 600;
}

.agents-grid {
    display: grid;
    gap: 0.75rem;
}

.agent-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1rem 1.25rem;
}

.agent-header {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 0.5rem;
    flex-wrap: wrap;
}

.agent-type {
    background: var(--accent);
    color: #000;
    font-size: 0.7rem;
    font-weight: 700;
    padding: 0.15rem 0.5rem;
    border-radius: 4px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

.agent-desc {
    font-weight: 600;
    font-size: 0.95rem;
}

.agent-duration {
    color: var(--text-dim);
    font-size: 0.85rem;
    margin-left: auto;
}

.agent-stats {
    display: flex;
    gap: 1rem;
    color: var(--text-dim);
    font-size: 0.8rem;
    margin-bottom: 0.5rem;
}

.agent-tools {
    display: flex;
    gap: 0.4rem;
    flex-wrap: wrap;
}

.tool-tag {
    background: var(--surface2);
    color: var(--text-dim);
    font-size: 0.75rem;
    padding: 0.15rem 0.5rem;
    border-radius: 4px;
    border: 1px solid var(--border);
}

.agent-summary {
    margin-top: 0.5rem;
    color: var(--text-dim);
    font-size: 0.85rem;
    border-left: 2px solid var(--border);
    padding-left: 0.75rem;
}

.timeline {
    position: relative;
    padding-left: 2rem;
}

.timeline::before {
    content: '';
    position: absolute;
    left: 0;
    top: 0;
    bottom: 0;
    width: 2px;
    background: var(--border);
}

.event {
    position: relative;
    margin-bottom: 1.25rem;
}

.event::before {
    content: '';
    position: absolute;
    left: -2rem;
    top: 0.7rem;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    border: 2px solid var(--border);
    background: var(--bg);
    transform: translateX(-4px);
}

.user-event::before {
    border-color: var(--blue);
    background: var(--blue);
}

.assistant-event::before {
    border-color: var(--accent);
    background: var(--accent);
}

.queued-event::before {
    border-color: var(--orange);
    background: var(--orange);
}

.subagent-event::before {
    border-color: var(--green);
    background: var(--green);
}

.subagent-block {
    background: linear-gradient(135deg, rgba(63, 185, 80, 0.08), rgba(63, 185, 80, 0.03));
    border: 1px solid rgba(63, 185, 80, 0.25);
    border-radius: 10px;
    padding: 0.75rem 1rem;
}

.subagent-block-header {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    margin-bottom: 0.4rem;
    flex-wrap: wrap;
}

.subagent-block-desc {
    font-weight: 600;
    font-size: 0.9rem;
}

.subagent-block-stats {
    display: flex;
    gap: 1rem;
    color: var(--text-dim);
    font-size: 0.8rem;
    margin-bottom: 0.4rem;
}

.subagent-event .event-label { color: var(--green); }

.event-meta {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.4rem;
    flex-wrap: wrap;
}

.event-time {
    color: var(--text-dim);
    font-size: 0.8rem;
    font-variant-numeric: tabular-nums;
    min-width: 90px;
}

.event-label {
    font-weight: 600;
    font-size: 0.85rem;
}

.user-event .event-label { color: var(--blue); }
.assistant-event .event-label { color: var(--accent); }
.queued-event .event-label { color: var(--orange); }

.model-badge {
    font-size: 0.7rem;
    font-weight: 700;
    padding: 0.1rem 0.4rem;
    border-radius: 4px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

.model-badge.opus {
    background: rgba(192, 132, 252, 0.2);
    color: var(--accent);
    border: 1px solid rgba(192, 132, 252, 0.3);
}

.model-badge.sonnet {
    background: rgba(88, 166, 255, 0.2);
    color: var(--blue);
    border: 1px solid rgba(88, 166, 255, 0.3);
}

.model-badge.haiku {
    background: rgba(63, 185, 80, 0.2);
    color: var(--green);
    border: 1px solid rgba(63, 185, 80, 0.3);
}

.speed-badge {
    font-size: 0.65rem;
    font-weight: 700;
    padding: 0.1rem 0.35rem;
    border-radius: 3px;
    background: rgba(210, 153, 34, 0.2);
    color: var(--orange);
    border: 1px solid rgba(210, 153, 34, 0.3);
    text-transform: uppercase;
}

.token-info {
    color: var(--text-dim);
    font-size: 0.75rem;
}

.user-bubble {
    background: var(--user-bg);
    border: 1px solid var(--user-border);
    border-radius: 10px;
    padding: 0.75rem 1rem;
    font-size: 0.95rem;
}

.queued-bubble {
    background: rgba(210, 153, 34, 0.08);
    border: 1px solid rgba(210, 153, 34, 0.2);
    border-radius: 10px;
    padding: 0.75rem 1rem;
    font-size: 0.95rem;
}

.assistant-text {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 0.75rem 1rem;
    font-size: 0.95rem;
    margin-bottom: 0.5rem;
}

.assistant-text p { margin-bottom: 0.5rem; }
.assistant-text p:last-child { margin-bottom: 0; }
.assistant-text h2, .assistant-text h3, .assistant-text h4 {
    margin-top: 0.5rem;
    margin-bottom: 0.25rem;
}
.assistant-text li {
    margin-left: 1.5rem;
    margin-bottom: 0.25rem;
}
.assistant-text code {
    background: var(--surface2);
    padding: 0.1rem 0.3rem;
    border-radius: 3px;
    font-size: 0.9em;
}

.tool-calls {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
}

.tool-call {
    display: flex;
    align-items: flex-start;
    gap: 0.5rem;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.5rem 0.75rem;
    font-size: 0.85rem;
}

.tool-icon {
    color: var(--text-dim);
    flex-shrink: 0;
    width: 16px;
    text-align: center;
}

.tool-icon::after {
    font-size: 0.8rem;
}

.tool-icon[data-icon="terminal"]::after { content: ">_"; font-family: monospace; font-size: 0.7rem; }
.tool-icon[data-icon="file-text"]::after { content: "\\1F4C4"; }
.tool-icon[data-icon="file-plus"]::after { content: "\\1F4DD"; }
.tool-icon[data-icon="edit"]::after { content: "\\270F\\FE0F"; }
.tool-icon[data-icon="users"]::after { content: "\\1F916"; }
.tool-icon[data-icon="zap"]::after { content: "\\26A1"; }
.tool-icon[data-icon="search"]::after { content: "\\1F50D"; }
.tool-icon[data-icon="globe"]::after { content: "\\1F310"; }
.tool-icon[data-icon="map"]::after { content: "\\1F5FA"; }
.tool-icon[data-icon="check-square"]::after { content: "\\2705"; }
.tool-icon[data-icon="plus-square"]::after { content: "\\2795"; }
.tool-icon[data-icon="check-circle"]::after { content: "\\2713"; }
.tool-icon[data-icon="plug"]::after { content: "\\1F50C"; }
.tool-icon[data-icon="tool"]::after { content: "\\1F527"; }

.tool-name {
    font-weight: 600;
    color: var(--text);
    white-space: nowrap;
}

.tool-detail {
    color: var(--text-dim);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
}

.turn-marker {
    display: flex;
    align-items: center;
    gap: 1rem;
    margin: 1.5rem 0;
    margin-left: -2rem;
}

.turn-line {
    flex: 1;
    height: 1px;
    background: var(--border);
}

.turn-label {
    color: var(--text-dim);
    font-size: 0.75rem;
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
}

.time-gap {
    text-align: center;
    margin: 1rem 0;
    margin-left: -2rem;
}

.time-gap span {
    background: var(--surface);
    color: var(--text-dim);
    font-size: 0.75rem;
    padding: 0.2rem 0.75rem;
    border-radius: 10px;
    border: 1px solid var(--border);
}

@media (max-width: 640px) {
    .container { padding: 1rem; }
    .stats-grid { grid-template-columns: repeat(2, 1fr); }
    .header h1 { font-size: 1.5rem; }
    .timeline { padding-left: 1.5rem; }
    .turn-marker { margin-left: -1.5rem; }
    .time-gap { margin-left: -1.5rem; }
}

@media print {
    body { background: #fff; color: #000; }
    .event { page-break-inside: avoid; }
}
`
}

function renderHeader(data) {
  const dateStr = data.firstTs ? `${formatDate(data.firstTs)} at ${formatTime(data.firstTs)}` : 'Unknown date'
  return `
<div class="header">
    <h1>${escapeHtml(data.title || 'Claude Code Session')}</h1>
    <div class="subtitle">Claude Code Session — ${dateStr} — ${formatDuration(data.duration)}</div>
</div>`
}

function renderStatsGrid(data) {
  return `
<div class="stats-grid">
    <div class="stat-card">
        <span class="stat-value">${formatDuration(data.duration)}</span>
        <span class="stat-label">Session Duration</span>
    </div>
    <div class="stat-card">
        <span class="stat-value">${data.turnCount}</span>
        <span class="stat-label">Turns</span>
    </div>
    <div class="stat-card">
        <span class="stat-value">${data.subagentCount}</span>
        <span class="stat-label">Subagents</span>
    </div>
    <div class="stat-card">
        <span class="stat-value">${formatTokens(data.totalInputTokens)}</span>
        <span class="stat-label">Input Tokens</span>
    </div>
    <div class="stat-card">
        <span class="stat-value">${formatTokens(data.totalOutputTokens)}</span>
        <span class="stat-label">Output Tokens</span>
    </div>
    <div class="stat-card">
        <span class="stat-value">${escapeHtml(data.modelList.join(', ') || 'Unknown')}</span>
        <span class="stat-label">Models Used</span>
    </div>
</div>`
}

function renderToolsTable(data) {
  if (data.sortedTools.length === 0) return ''
  const rows = data.sortedTools
    .map(([name, count]) => `<tr><td>${escapeHtml(name)}</td><td class="num">${count}</td></tr>`)
    .join('\n')
  return `
<div class="section">
    <h2>Tools Used</h2>
    <table class="tool-table">
        <thead><tr><th>Tool</th><th style="text-align:right">Calls</th></tr></thead>
        <tbody>${rows}
</tbody>
    </table>
</div>`
}

function renderSubagents(subagents) {
  if (subagents.length === 0) return ''
  const cards = subagents.map(sa => {
    const tools = sa.sortedTools
      .map(([name, count]) => `<span class="tool-tag">${escapeHtml(name)} x${count}</span>`)
      .join('')
    return `
        <div class="agent-card">
            <div class="agent-header">
                <span class="agent-type">${escapeHtml(sa.agentType)}</span>
                <span class="agent-desc">${escapeHtml(sa.description)}</span>
                <span class="agent-duration">${formatDuration(sa.duration)}</span>
            </div>
            <div class="agent-stats">
                <span>${sa.turns} turns</span>
                <span>${formatTokens(sa.inputTokens)} in / ${formatTokens(sa.outputTokens)} out</span>
                <span>${escapeHtml(sa.model)}</span>
            </div>
            <div class="agent-tools">${tools}</div>
            ${sa.summary ? `<div class="agent-summary">${escapeHtml(sa.summary)}</div>` : ''}
        </div>`
  }).join('\n        ')

  return `
<div class="section">
    <h2>Subagents (${subagents.length})</h2>
    <div class="agents-grid">${cards}
    </div>
</div>`
}

function simpleMarkdownToHtml(text) {
  if (!text) return ''
  let html = escapeHtml(text)
  // Bold
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  // Inline code
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>')
  // Line breaks to paragraphs
  html = html
    .split(/\n{2,}/)
    .map(p => `<p>${p.replace(/\n/g, '<br>')}</p>`)
    .join('')
  // List items
  html = html.replace(/<br>- /g, '<br><li>')
  html = html.replace(/^- /gm, '<li>')
  return html
}

function renderTimeline(data, subagents) {
  const parts = []

  // Build a set of subagent events to insert at the right time
  const subagentsByTime = subagents
    .filter(sa => sa.startTs)
    .map(sa => ({ ...sa, tsMs: sa.startTs.getTime() }))
    .sort((a, b) => a.tsMs - b.tsMs)
  let saIdx = 0

  for (const ev of data.events) {
    const evTs = ev.timestamp ? ev.timestamp.getTime() : 0

    // Insert any subagent events that started before this event
    while (saIdx < subagentsByTime.length && subagentsByTime[saIdx].tsMs <= evTs) {
      const sa = subagentsByTime[saIdx]
      const mc = modelClass(sa.model)
      const tools = sa.sortedTools
        .map(([name, count]) => `<span class="tool-tag">${escapeHtml(name)} x${count}</span>`)
        .join('')
      parts.push(`
            <div class="event subagent-event">
                <div class="event-meta">
                    <span class="event-time">${formatTime(sa.startTs)}</span>
                    <span class="event-label">Subagent</span>
                    ${mc ? `<span class="model-badge ${mc}">${modelShort(sa.model)}</span>` : ''}
                </div>
                <div class="subagent-block">
                    <div class="subagent-block-header">
                        <span class="agent-type">${escapeHtml(sa.agentType)}</span>
                        <span class="subagent-block-desc">${escapeHtml(sa.description)}</span>
                        <span class="agent-duration">${formatDuration(sa.duration)}</span>
                    </div>
                    <div class="subagent-block-stats">
                        <span>${sa.turns} turns</span>
                        <span>${formatTokens(sa.inputTokens)} in / ${formatTokens(sa.outputTokens)} out</span>
                    </div>
                    <div class="agent-tools">${tools}</div>
                </div>
            </div>`)
      saIdx++
    }

    if (ev.type === 'user') {
      parts.push(`
            <div class="event user-event">
                <div class="event-meta">
                    <span class="event-time">${formatTime(ev.timestamp)}</span>
                    <span class="event-label">You</span>
                </div>
                <div class="event-content user-bubble">
                    ${simpleMarkdownToHtml(ev.text)}
                </div>
            </div>`)
    }

    if (ev.type === 'assistant-text') {
      const mc = modelClass(ev.model)
      parts.push(`
            <div class="event assistant-event">
                <div class="event-meta">
                    <span class="event-time">${formatTime(ev.timestamp)}</span>
                    <span class="event-label">Claude</span>
                    ${mc ? `<span class="model-badge ${mc}">${modelShort(ev.model)}</span>` : ''}
                    ${ev.speed === 'fast' ? '<span class="speed-badge">Fast</span>' : ''}
                    <span class="token-info">${formatTokens(ev.outputTokens)} tokens out</span>
                </div>
                <div class="assistant-text">${simpleMarkdownToHtml(ev.text)}</div>
            </div>`)
    }

    if (ev.type === 'assistant-tools') {
      const mc = modelClass(ev.model)
      const toolsHtml = ev.tools
        .map(t => `
                    <div class="tool-call">
                        <span class="tool-icon" data-icon="${t.icon}"></span>
                        <span class="tool-name">${escapeHtml(t.displayName)}</span>
                        <span class="tool-detail">${t.detail}</span>
                    </div>`)
        .join('')
      parts.push(`
            <div class="event assistant-event">
                <div class="event-meta">
                    <span class="event-time">${formatTime(ev.timestamp)}</span>
                    <span class="event-label">Claude</span>
                    ${mc ? `<span class="model-badge ${mc}">${modelShort(ev.model)}</span>` : ''}
                    ${ev.speed === 'fast' ? '<span class="speed-badge">Fast</span>' : ''}
                    <span class="token-info">${formatTokens(ev.outputTokens)} tokens out</span>
                </div>
                <div class="tool-calls">${toolsHtml}
                    </div>
            </div>`)
    }

    if (ev.type === 'turn-marker') {
      parts.push(`
            <div class="turn-marker">
                <div class="turn-line"></div>
                <span class="turn-label">Turn ${ev.turnNumber} complete — ${formatDuration(ev.duration)}, ${ev.messages} messages</span>
                <div class="turn-line"></div>
            </div>`)
    }

    if (ev.type === 'time-gap') {
      parts.push(`
            <div class="time-gap"><span>${formatDuration(ev.gap)} pause</span></div>`)
    }
  }

  // Any remaining subagents
  while (saIdx < subagentsByTime.length) {
    const sa = subagentsByTime[saIdx]
    const mc = modelClass(sa.model)
    const tools = sa.sortedTools
      .map(([name, count]) => `<span class="tool-tag">${escapeHtml(name)} x${count}</span>`)
      .join('')
    parts.push(`
            <div class="event subagent-event">
                <div class="event-meta">
                    <span class="event-time">${sa.startTs ? formatTime(sa.startTs) : ''}</span>
                    <span class="event-label">Subagent</span>
                    ${mc ? `<span class="model-badge ${mc}">${modelShort(sa.model)}</span>` : ''}
                </div>
                <div class="subagent-block">
                    <div class="subagent-block-header">
                        <span class="agent-type">${escapeHtml(sa.agentType)}</span>
                        <span class="subagent-block-desc">${escapeHtml(sa.description)}</span>
                        <span class="agent-duration">${formatDuration(sa.duration)}</span>
                    </div>
                    <div class="subagent-block-stats">
                        <span>${sa.turns} turns</span>
                        <span>${formatTokens(sa.inputTokens)} in / ${formatTokens(sa.outputTokens)} out</span>
                    </div>
                    <div class="agent-tools">${tools}</div>
                </div>
            </div>`)
    saIdx++
  }

  return `
<div class="section">
    <h2>Timeline</h2>
    <div class="timeline">${parts.join('')}
    </div>
</div>`
}

function generateHtml(data, subagents) {
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>${escapeHtml(data.title || 'Claude Code Session')} — Claude Code Session</title>
<style>${renderCss()}</style>
</head>
<body>
<div class="container">
${renderHeader(data)}
${renderStatsGrid(data)}
${renderToolsTable(data)}
${renderSubagents(subagents)}
${renderTimeline(data, subagents)}
</div>
</body>
</html>`
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
async function main() {
  const filePath = discoverFile()

  if (!fs.existsSync(filePath)) {
    console.error(`File not found: ${filePath}`)
    process.exit(1)
  }

  console.error(`Parsing: ${filePath}`)
  const entries = await parseJsonl(filePath)
  console.error(`Entries: ${entries.length}`)

  const data = analyze(entries)
  console.error(`Title: ${data.title}`)
  console.error(`Duration: ${formatDuration(data.duration)}`)
  console.error(`Turns: ${data.turnCount}`)

  const subagents = await analyzeSubagents(filePath, data.agentCalls)
  if (subagents.length > 0) {
    console.error(`Subagents: ${subagents.length}`)
    // Update subagent count from actual analysis
    data.subagentCount = Math.max(data.subagentCount, subagents.length)
  }

  const html = generateHtml(data, subagents)

  const sessionId = data.sessionId || path.basename(filePath, '.jsonl')
  const tmpDir = process.env.TMPDIR || '/tmp'
  const outDir = path.join(tmpDir, 'claude')
  if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true })

  const outputPath = OUTPUT || path.join(outDir, `session-timeline-${sessionId.slice(0, 8)}.html`)
  fs.writeFileSync(outputPath, html, 'utf8')

  const sizeKb = (Buffer.byteLength(html) / 1024).toFixed(0)
  console.error(`Written: ${outputPath} (${sizeKb}KB)`)

  // Print path to stdout for callers to capture
  console.log(outputPath)
}

main().catch(err => {
  console.error(err)
  process.exit(1)
})
