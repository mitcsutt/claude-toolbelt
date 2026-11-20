import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { tables, backticked } from './skill-tables.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PLUGIN = path.resolve(HERE, '..');
const SKILL_DIR = path.join(PLUGIN, 'skills', 'assemble-panel');
const SKILL = fs.readFileSync(path.join(SKILL_DIR, 'SKILL.md'), 'utf8');
const REFS = path.join(SKILL_DIR, 'references');

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

// Reviewer agents: every agents/*.md whose description doesn't mark it as a
// helper ("Helper dispatched by ...").
function rosterAgents() {
  return fs.readdirSync(path.join(PLUGIN, 'agents'))
    .filter((f) => f.endsWith('.md'))
    .filter((f) => {
      const fm = frontmatter(fs.readFileSync(path.join(PLUGIN, 'agents', f), 'utf8'));
      return !/Helper dispatched by/.test(fm.description || '');
    })
    .map((f) => f.slice(0, -3))
    .sort();
}

const skillTables = tables(SKILL);
const find = (heading, first) => skillTables.find(
  (t) => t.heading && t.heading.startsWith(heading) && t.header[0] === first,
);
const column = (t, name) => t.rows.map((r) => r[t.header.indexOf(name)]);

// The bullet under Step 2's "Rules:" that starts with **<label>**.
function ruleBullet(label) {
  const line = SKILL.split('\n').find((l) => l.startsWith(`- **${label}`));
  assert.ok(line, `rule bullet "${label}" exists`);
  return line;
}

test('tables() reads pipe tables under their nearest ## heading and skips fenced ones', () => {
  const md = [
    '## One', '| a | b |', '| --- | --- |', '| 1 | x \\| y |', '',
    '```', '| c | d |', '| --- | --- |', '| 2 | z |', '```',
    '## Two', 'text', '| e |', '|---|', '| 3 |', '| 4 |',
  ].join('\n');
  assert.deepEqual(tables(md), [
    { heading: 'One', header: ['a', 'b'], rows: [['1', 'x | y']] },
    { heading: 'Two', header: ['e'], rows: [['3'], ['4']] },
  ]);
  assert.deepEqual(backticked('use `a` then `b-c`'), ['a', 'b-c']);
});

test('the roster table lists exactly the non-helper agents', () => {
  const roster = find('The roster', 'Agent');
  assert.ok(roster, 'roster table exists');
  const names = column(roster, 'Agent').map((c) => backticked(c)[0]).sort();
  assert.deepEqual(names, rosterAgents());
});

test('every reviewer is routed by the Core or Signal table, and both name only roster agents', () => {
  const core = find('Step 2', 'Artifact');
  const signal = find('Step 2', 'Signal');
  assert.ok(core && signal, 'Step 2 has a Core table and a Signal table');
  const roster = new Set(rosterAgents());
  const routed = new Set([
    ...column(core, 'Core').flatMap(backticked),
    ...column(signal, 'Add').flatMap(backticked),
  ]);
  for (const name of routed) assert.ok(roster.has(name), `routing names a roster agent: ${name}`);
  for (const name of roster) {
    if (name === 'noise-filter') continue;
    assert.ok(routed.has(name), `${name} is routed by a Core or Signal row`);
  }
});

test('every Signal row adds exactly one agent', () => {
  const signal = find('Step 2', 'Signal');
  for (const row of signal.rows) {
    assert.equal(backticked(row[signal.header.indexOf('Add')]).length, 1, `one agent for: ${row[0]}`);
  }
});

test('the claim-owner priority names roster agents, led by the agent-instructions owner', () => {
  const roster = new Set(rosterAgents());
  const owners = backticked(ruleBullet('One claim owner'));
  assert.ok(owners.length >= 2, 'a priority of at least two owners');
  for (const o of owners) assert.ok(roster.has(o), `claim owner is a roster agent: ${o}`);
  const overlapOwner = backticked(ruleBullet('No overlap'))[0];
  assert.equal(owners[0], overlapOwner, 'first claim owner is the one "No overlap" names for agent instructions');
});

test('every relative link in SKILL.md and its references resolves to a file', () => {
  const files = [path.join(SKILL_DIR, 'SKILL.md'),
    ...fs.readdirSync(REFS).filter((f) => f.endsWith('.md')).map((f) => path.join(REFS, f))];
  let links = 0;
  for (const file of files) {
    const text = fs.readFileSync(file, 'utf8');
    for (const m of text.matchAll(/\]\(([^)\s]+)\)/g)) {
      const target = m[1].split('#')[0];
      if (!target || /^[a-z]+:/.test(target)) continue;
      links += 1;
      const resolved = path.resolve(path.dirname(file), target);
      assert.ok(fs.existsSync(resolved), `${path.relative(PLUGIN, file)} links to a missing file: ${m[1]}`);
    }
  }
  assert.ok(links > 0, 'found links to check');
});

test('the skill has a blocked-dispatch section and a numbered Step 5 checklist', () => {
  const headings = SKILL.split('\n').filter((l) => l.startsWith('## '));
  assert.ok(headings.includes('## If dispatch is blocked'), 'has "## If dispatch is blocked"');
  const lines = SKILL.split('\n');
  const start = lines.findIndex((l) => l.startsWith('## Step 5'));
  const end = lines.findIndex((l, i) => i > start && l.startsWith('## '));
  const items = lines.slice(start, end).filter((l) => /^\d+\. /.test(l)).map((l) => Number(l.match(/^\d+/)[0]));
  assert.ok(items.length >= 2, 'Step 5 is a numbered checklist');
  assert.deepEqual(items, items.map((_, i) => i + 1), 'checklist numbered 1..n in order');
});

test("the reviewer brief template carries the sections the orchestrator fills", () => {
  const text = fs.readFileSync(path.join(REFS, 'brief-template.md'), 'utf8');
  const block = text.match(/```markdown\n([\s\S]*?)\n```/);
  assert.ok(block, 'brief template has a fenced block');
  const headings = block[1].split('\n').filter((l) => l.startsWith('## '));
  for (const h of ['## The goal', '## Context you get', '## Lens boundaries (this panel)', '## Report']) {
    assert.ok(headings.some((l) => l.startsWith(h)), `brief template has ${h}`);
  }
});
