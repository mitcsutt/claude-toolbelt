import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseTemplate } from './report-parse.mjs';
import { tables } from './skill-tables.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PLUGIN = path.resolve(HERE, '..');
const read = (...p) => fs.readFileSync(path.join(PLUGIN, ...p), 'utf8');
const AGENT = read('agents', 'outsider.md');

function frontmatter(text) {
  const m = text.match(/^---\n([\s\S]*?)\n---\n/);
  const out = {};
  if (!m) return out;
  for (const line of m[1].split('\n')) {
    const kv = line.match(/^([A-Za-z]+):\s*(.*)$/);
    if (kv) out[kv[1]] = kv[2];
  }
  return out;
}

// The first fenced block after `heading` (or after the start, when null).
function fencedBlock(text, heading = null) {
  const lines = text.split('\n');
  const start = heading === null ? -1 : lines.findIndex((l) => l === heading);
  if (heading !== null && start < 0) return null;
  const open = lines.findIndex((l, i) => i > start && l.startsWith('```'));
  if (open < 0) return null;
  const close = lines.findIndex((l, i) => i > open && l.startsWith('```'));
  if (close < 0) return null;
  return lines.slice(open + 1, close).join('\n');
}

function assertOrder(list, wanted, label) {
  const at = wanted.map((w) => list.indexOf(w));
  for (const [i, w] of wanted.entries()) assert.notEqual(at[i], -1, `${label}: has ${w}`);
  for (let i = 1; i < at.length; i += 1) {
    assert.ok(at[i - 1] < at[i], `${label}: ${wanted[i - 1]} comes before ${wanted[i]}`);
  }
}

test('outsider has the web tools its built-in sweep and prior-art search need, and no Agent', () => {
  const tools = frontmatter(AGENT).tools.split(',').map((t) => t.trim());
  assert.ok(tools.includes('WebFetch'), 'tools include WebFetch');
  assert.ok(tools.includes('WebSearch'), 'tools include WebSearch');
  assert.ok(!tools.includes('Agent'), 'tools exclude Agent');
});

test('the output template puts the sealed cold take first and Coverage after Findings', () => {
  const r = parseTemplate(fencedBlock(AGENT, '## Output contract'));
  assert.deepEqual(r.problems, []);
  assertOrder(r.sections, ['Cold take', 'Prior art', 'Build, buy or extend', 'Findings', 'Coverage'], 'template');
});

test('every built-ins checklist row is a dated, quoted, located entry', () => {
  const text = read('references', 'outsider', 'built-ins.md');
  assert.match(text, /^Verified on \d{4}-\d{2}-\d{2}/m);
  const ts = tables(text);
  const platform = ts.find((t) => t.heading === 'Claude Code');
  const home = ts.find((t) => t.heading === 'Home-server tooling');
  assert.ok(platform && home, 'both checklist tables exist');
  assert.ok(platform.rows.length >= 20, `${platform.rows.length} Claude Code rows`);
  assert.ok(home.rows.length >= 3, `${home.rows.length} home-server rows`);
  for (const t of [platform, home]) {
    assert.equal(t.header.length, 7, `${t.heading}: 7 columns`);
    for (const row of t.rows) {
      const [id, , builtIn, quote, where, since] = row;
      assert.equal(row.length, 7, `row ${id}: 7 cells`);
      assert.ok(builtIn, `row ${id}: names the built-in`);
      assert.match(quote, /^".+"$/, `row ${id}: quotes the page`);
      if (t === platform) assert.match(where, /^`?[a-z_/-]+\.md:\d+/, `row ${id}: page:line`);
      else assert.match(where, /^https:\/\//, `row ${id}: URL`);
      assert.match(since, /^(—|\d+\.\d+\.\d+(; \d+\.\d+\.\d+)*)$/, `row ${id}: since is a version or —`);
    }
  }
});

test('the brief template gives the problem statement before the author\'s rationale, both before the report', () => {
  const block = fencedBlock(read('skills', 'assemble-panel', 'references', 'brief-template.md'));
  const headings = block.split('\n')
    .filter((l) => l.startsWith('## '))
    .map((l) => l.slice(3).replace(/\s*<!--.*-->\s*$/, '').trim());
  assertOrder(headings, ['Problem statement', "Author's rationale", 'Report'], 'brief template');
});
