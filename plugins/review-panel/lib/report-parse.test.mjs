import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { parseReport, parseTemplate } from './report-parse.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PLUGIN = path.resolve(HERE, '..');
const FIX = path.join(HERE, 'fixtures');
const fixture = (name) => fs.readFileSync(path.join(FIX, name), 'utf8');

// Agents whose output isn't a findings report: helpers return a trace, and
// noise-filter writes a triage (its own plan adds parseTriage for it).
const NOT_REVIEWERS = new Set(['instruction-follower', 'noise-filter']);

function agentNames() {
  return fs.readdirSync(path.join(PLUGIN, 'agents'))
    .filter((f) => f.endsWith('.md'))
    .map((f) => f.slice(0, -3))
    .sort();
}

// The first fenced block after the "## Output contract" heading.
function outputTemplate(body) {
  const lines = body.split('\n');
  const start = lines.findIndex((l) => l === '## Output contract');
  if (start < 0) return null;
  const open = lines.findIndex((l, i) => i > start && l.startsWith('```'));
  if (open < 0) return null;
  const close = lines.findIndex((l, i) => i > open && l.startsWith('```'));
  if (close < 0) return null;
  return lines.slice(open + 1, close).join('\n');
}

test('a valid report parses with no problems', () => {
  const r = parseReport(fixture('valid.md'));
  assert.deepEqual(r.problems, []);
  assert.equal(r.reviewer, 'goal-auditor');
  assert.equal(r.artifact, 'plans/rollout.md');
  assert.equal(r.verdict, 'SHIP WITH FIXES');
  assert.equal(r.verdictReason, 'one test passes with or without the fix.');
  assert.equal(r.tier, 1);
  assert.deepEqual(r.sections, ['Goal scorecard', 'Findings', 'Coverage', 'Checked and fine']);
  assert.equal(r.findings.length, 2);
  assert.deepEqual(r.findings.map((f) => [f.id, f.severity, f.confidence]), [
    ['F1', 'SHOULD', 'high'],
    ['F2', 'COULD', 'medium'],
  ]);
});

test('continuation lines, including fenced ones, stay with their field', () => {
  const [f1] = parseReport(fixture('valid.md')).findings;
  assert.match(f1.evidence, /^ran `git stash; npm test` and got\n {2}14 passing\n/);
  assert.match(f1.evidence, /## not a heading: inside a code fence/);
  assert.equal(f1.fix, 'Assert the retry count.');
});

test('Where splits into path and line when it names a file:line', () => {
  const [f1, f2] = parseReport(fixture('valid.md')).findings;
  assert.deepEqual(f1.where, { raw: '`src/retry.ts:42`', path: 'src/retry.ts', line: 42 });
  assert.deepEqual(f2.where, { raw: 'plan section "Rollout"', path: null, line: null });
});

for (const [file, pattern] of [
  ['missing-verdict.md', /Verdict/],
  ['bad-severity.md', /F1.*Severity.*MAJOR/],
  ['bad-confidence.md', /F2.*Confidence.*sure/],
  ['missing-evidence.md', /F2.*Evidence/],
]) {
  test(`${file} yields exactly one problem naming the defect`, () => {
    const { problems } = parseReport(fixture(file));
    assert.equal(problems.length, 1, JSON.stringify(problems));
    assert.match(problems[0], pattern);
  });
}

test('a report with no Findings section is a problem; zero findings is not', () => {
  const noSection = fixture('valid.md').replace(/^## Findings$/m, '## Notes');
  assert.ok(parseReport(noSection).problems.some((p) => /Findings/.test(p)));
  const clean = '# outsider review: x\nVerdict: SHIP\n## Findings\n## Checked and fine\nall of it\n';
  assert.deepEqual(parseReport(clean).problems, []);
});

test('a missing Tier line is null, and a report may not use template placeholders', () => {
  const r = parseReport(fixture('valid.md').replace(/^Tier:.*\n/m, ''));
  assert.equal(r.tier, null);
  assert.deepEqual(r.problems, []);
  const t = parseReport('# x review: y\nVerdict: SHIP | SHIP WITH FIXES | DO NOT SHIP: <one sentence>\n## Findings\n');
  assert.ok(t.problems.some((p) => /Verdict/.test(p)));
});

test('the CLI prints one JSON line per finding and exits 1 on a non-conforming report', () => {
  const cli = path.join(HERE, 'report-parse.mjs');
  const ok = spawnSync(process.execPath, [cli, 'findings', path.join(FIX, 'valid.md')], { encoding: 'utf8' });
  assert.equal(ok.status, 0, ok.stderr);
  const rows = ok.stdout.trim().split('\n').map((l) => JSON.parse(l));
  assert.deepEqual(rows.map((r) => [r.reviewer, r.id, r.severity]), [
    ['goal-auditor', 'F1', 'SHOULD'],
    ['goal-auditor', 'F2', 'COULD'],
  ]);
  const bad = spawnSync(process.execPath, [cli, 'findings', path.join(FIX, 'bad-severity.md')], { encoding: 'utf8' });
  assert.equal(bad.status, 1);
  assert.match(bad.stderr, /MAJOR/);
});

test('every reviewer agent template conforms to the shared contract', () => {
  const checked = [];
  for (const name of agentNames()) {
    if (NOT_REVIEWERS.has(name)) continue;
    const tpl = outputTemplate(fs.readFileSync(path.join(PLUGIN, 'agents', `${name}.md`), 'utf8'));
    assert.ok(tpl, `${name}: has a fenced template under "## Output contract"`);
    const r = parseTemplate(tpl);
    assert.deepEqual(r.problems, [], `${name}: template problems`);
    assert.equal(r.reviewer, name, `${name}: template title names the agent`);
    assert.ok(r.sections.includes('Findings'), `${name}: template has ## Findings`);
    checked.push(name);
  }
  assert.ok(checked.length >= 10, `checked ${checked.length} templates`);
});

test('upgraded reviewers (those with references/<name>/) carry the stricter contract', () => {
  const dirs = fs.readdirSync(path.join(PLUGIN, 'references'), { withFileTypes: true })
    .filter((d) => d.isDirectory() && d.name !== 'shared' && d.name !== 'noise-filter')
    .map((d) => d.name);
  assert.ok(dirs.length >= 1);
  for (const name of dirs) {
    const body = fs.readFileSync(path.join(PLUGIN, 'agents', `${name}.md`), 'utf8');
    const r = parseTemplate(outputTemplate(body));
    assert.notEqual(r.tier, null, `${name}: template has a Tier: line`);
    assert.ok(r.sections.includes('Coverage'), `${name}: template has ## Coverage`);
    assert.ok(body.includes('${CLAUDE_PLUGIN_ROOT}/references/shared/review-method.md'),
      `${name}: loads the shared review method`);
  }
});
