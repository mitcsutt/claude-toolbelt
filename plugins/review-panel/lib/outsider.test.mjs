import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseTemplate } from './report-parse.mjs';

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

// The first fenced block after `heading`.
function fencedBlock(text, heading) {
  const lines = text.split('\n');
  const start = lines.findIndex((l) => l === heading);
  if (start < 0) return null;
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
