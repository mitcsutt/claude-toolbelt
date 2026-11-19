import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import {
  parseReport, parseTemplate, parseTriage, checkTriage, collectFindings, mergeCandidates, shuffle,
  TRIAGE_SECTIONS,
} from './report-parse.mjs';

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
  assert.match(f1.evidence, /\n```\n## not a heading: inside a code fence\n### F9 not a finding\n```$/);
  assert.equal(f1.fix, 'Assert the retry count.');
});

test('Where splits into path and line when it names a file:line', () => {
  const [f1, f2] = parseReport(fixture('valid.md')).findings;
  assert.deepEqual(f1.where, { raw: '`src/retry.ts:42`', path: 'src/retry.ts', line: 42, lineEnd: null });
  assert.deepEqual(f2.where, { raw: 'plan section "Rollout"', path: null, line: null, lineEnd: null });
});

test('Where strips a leading ./ and captures a range end', () => {
  const { findings } = collectFindings([path.join(FIX, 'panel', 'alpha.md')]);
  assert.deepEqual(findings.map((f) => [f.where.path, f.where.line, f.where.lineEnd]), [
    ['src/app.js', 41, 43],
    ['src/app.js', 90, null],
  ]);
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
    .filter((d) => d.isDirectory() && d.name !== 'shared')
    .map((d) => d.name);
  assert.ok(dirs.length >= 1);
  for (const name of dirs) {
    const body = fs.readFileSync(path.join(PLUGIN, 'agents', `${name}.md`), 'utf8');
    // noise-filter's template is a triage, not a findings report.
    const r = name === 'noise-filter'
      ? parseTriage(outputTemplate(body), { template: true })
      : parseTemplate(outputTemplate(body));
    assert.notEqual(r.tier, null, `${name}: template has a Tier: line`);
    assert.ok(r.sections.includes('Coverage'), `${name}: template has ## Coverage`);
    assert.ok(body.includes('${CLAUDE_PLUGIN_ROOT}/references/shared/review-method.md'),
      `${name}: loads the shared review method`);
  }
});

// --- noise-filter: shuffle, index, merge candidates, triage accounting ---

const PANEL = ['alpha', 'beta', 'gamma'].map((n) => path.join(FIX, 'panel', `${n}.md`));
const CLI = path.join(HERE, 'report-parse.mjs');
const triage = (name) => parseTriage(fixture(`triage-${name}.md`));
const check = (name) => checkTriage(triage(name), collectFindings(PANEL).findings);

test('shuffle is a deterministic, pure permutation that varies with the seed', () => {
  const items = ['a', 'b', 'c', 'd', 'e'];
  const once = shuffle(items, 42);
  assert.deepEqual(shuffle(items, 42), once);
  assert.deepEqual([...once].sort(), items);
  assert.deepEqual(items, ['a', 'b', 'c', 'd', 'e']);
  const orders = new Set();
  for (let seed = 1; seed <= 10; seed++) orders.add(shuffle(items, seed).join(''));
  assert.ok(orders.size >= 2, `seeds 1-10 gave ${orders.size} distinct order(s)`);
});

test('collectFindings keys every finding "<reviewer> F<n>" in the shuffled report order', () => {
  const c = collectFindings(PANEL, { seed: 7 });
  assert.deepEqual(c.problems, []);
  assert.equal(c.seed, 7);
  assert.deepEqual([...c.order].sort(), ['alpha', 'beta', 'gamma']);
  assert.deepEqual(c.order, shuffle(['alpha', 'beta', 'gamma'], 7));
  const keys = c.findings.map((f) => f.key);
  assert.equal(new Set(keys).size, keys.length);
  assert.deepEqual([...keys].sort(), ['alpha F1', 'alpha F2', 'beta F1', 'beta F2', 'gamma F1']);
  // Findings follow the report order.
  assert.deepEqual([...new Set(c.findings.map((f) => f.reviewer))], c.order);
  assert.deepEqual(collectFindings(PANEL).order, ['alpha', 'beta', 'gamma']);
});

test('two reports with the same reviewer name are a problem', () => {
  const c = collectFindings([PANEL[0], PANEL[0]]);
  assert.equal(c.problems.length, 1);
  assert.match(c.problems[0], /alpha/);
  assert.equal(c.findings.length, 2);
});

test('mergeCandidates groups by path and nearby lines only', () => {
  const f = (key, p, line, lineEnd = null) => ({
    key, reviewer: key.split(' ')[0], severity: 'SHOULD', where: { raw: '', path: p, line, lineEnd },
  });
  const keysOf = (fs_) => mergeCandidates(fs_).map((g) => g.keys.sort());
  assert.deepEqual(keysOf([f('a F1', 'x.js', 10), f('b F1', 'x.js', 15)]), [['a F1', 'b F1']]);
  assert.deepEqual(keysOf([f('a F1', 'x.js', 10), f('b F1', 'x.js', 16)]), []);
  assert.deepEqual(keysOf([f('a F1', 'x.js', 41, 43), f('b F1', 'x.js', 44)]), [['a F1', 'b F1']]);
  assert.deepEqual(keysOf([f('a F1', 'x.js', 30, 43), f('b F1', 'x.js', 48)]), [['a F1', 'b F1']]);
  assert.deepEqual(keysOf([f('a F1', 'x.js', 10), f('b F1', 'y.js', 10)]), []);
  assert.deepEqual(keysOf([f('a F1', null, null), f('b F1', null, null)]), []);
  const [g] = mergeCandidates([f('a F1', 'x.js', 1), f('a F2', 'x.js', 3), f('b F1', 'x.js', 5)]);
  assert.deepEqual(g.reviewers.sort(), ['a', 'b']);
  assert.deepEqual(g.lines, [1, 3, 5]);
});

test('mergeCandidates on the panel fixtures proposes the retry pair with the max severity', () => {
  const groups = mergeCandidates(collectFindings(PANEL).findings);
  assert.equal(groups.length, 1);
  assert.deepEqual(groups[0].keys.sort(), ['alpha F1', 'beta F1']);
  assert.equal(groups[0].sevMax, 'BLOCK');
  assert.deepEqual(groups[0].sevSpread, ['BLOCK', 'SHOULD']);
});

test('parseTriage reads a valid triage with no problems', () => {
  const t = triage('valid');
  assert.deepEqual(t.problems, []);
  assert.deepEqual(t.sections, TRIAGE_SECTIONS);
  assert.equal(t.overall, 'SHIP WITH FIXES');
  assert.equal(t.tier, 1);
  assert.equal(t.seed, 7);
  assert.deepEqual(t.order, ['beta', 'gamma', 'alpha']);
  assert.deepEqual(t.items.map((i) => [i.section, i.id, i.sources.length]), [
    ['Fix before merge', 'T1', 2],
    ['Fix before merge', 'T2', 0],
    ['Worth doing', 'W1', 1],
    ['Pre-existing', 'P1', 1],
  ]);
  assert.equal(t.items[1].newSources, 1);
  assert.deepEqual(t.items[0].tagged, ['alpha', 'beta']);
  assert.match(t.items[0].sameCause, /attempt counter/);
  assert.deepEqual(t.dropped.map((d) => d.sources), [['alpha F2']]);
});

test('a drifted heading is one problem naming the contract heading', () => {
  const { problems } = triage('heading-drift');
  assert.equal(problems.length, 1, JSON.stringify(problems));
  assert.match(problems[0], /## Fix before merge/);
});

test('extra sections and bad drop reasons are problems', () => {
  const extra = fixture('triage-valid.md').replace('## Coverage', '## Revised roadmap\nstuff\n## Coverage');
  const p1 = parseTriage(extra).problems;
  assert.equal(p1.length, 1, JSON.stringify(p1));
  assert.match(p1[0], /Revised roadmap/);
  const absorbed = fixture('triage-valid.md').replace('nit: no consequence', 'absorbed into T1');
  assert.ok(parseTriage(absorbed).problems.some((p) => /reason/.test(p)));
});

test('checkTriage accounts for every finding exactly once', () => {
  const ok = check('valid');
  assert.equal(ok.accounted, ok.total);
  assert.equal(ok.total, 5);
  for (const k of ['missing', 'duplicated', 'unknown', 'tagErrors', 'causeErrors', 'problems']) {
    assert.deepEqual(ok[k], [], k);
  }
  assert.deepEqual(ok.counts, { fix: 2, decide: 0, worth: 1, preExisting: 1, dropped: 1, findings: 5 });
  assert.deepEqual(check('missing').missing, ['alpha F2']);
  assert.deepEqual(check('duplicate').duplicated.map((d) => d.key), ['alpha F1']);
  assert.deepEqual(check('unknown').unknown, ['goal-auditor F9']);
  assert.deepEqual(check('badtag').tagErrors.sort(), ['T1', 'W1']);
  assert.deepEqual(check('no-cause').causeErrors, ['T1']);
  // A key mentioned in prose is not an accounting.
  assert.deepEqual(check('prose-mention').missing, ['alpha F2']);
});

test('checkTriage warns, not fails, on budget and sort order', () => {
  const long = fixture('triage-valid.md').replace('- Sources: gamma F1\n', '- Sources: gamma F1\n- a\n- b\n- c\n');
  const r = checkTriage(parseTriage(long), collectFindings(PANEL).findings);
  assert.ok(r.warnings.some((w) => /W1/.test(w)));
  assert.deepEqual(r.missing, []);
  const swapped = fixture('triage-valid.md').replace('- Severity: BLOCK (BLOCK', '- Severity: COULD (BLOCK');
  assert.ok(checkTriage(parseTriage(swapped), collectFindings(PANEL).findings)
    .warnings.some((w) => /sorted/.test(w)));
});

test('the CLI check-triage exits 0 on a valid triage and 1 on a missing finding', () => {
  const run = (name) => spawnSync(process.execPath, [CLI, 'check-triage', path.join(FIX, `triage-${name}.md`), ...PANEL], { encoding: 'utf8' });
  const ok = run('valid');
  assert.equal(ok.status, 0, ok.stdout + ok.stderr);
  assert.match(ok.stdout, /^accounted 5\/5 /);
  const bad = run('missing');
  assert.equal(bad.status, 1);
  assert.match(bad.stdout, /missing: alpha F2/);
});

test('the CLI findings --shuffle prints the order object first, then one finding per line', () => {
  const r = spawnSync(process.execPath, [CLI, 'findings', '--shuffle', '--seed', '7', '--compact', ...PANEL], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr);
  const [head, ...rows] = r.stdout.trim().split('\n').map((l) => JSON.parse(l));
  assert.deepEqual(head, { order: shuffle(['alpha', 'beta', 'gamma'], 7), seed: 7 });
  assert.equal(rows.length, 5);
  assert.deepEqual(Object.keys(rows[0]), ['key', 'sev', 'conf', 'where', 'title']);
  const m = spawnSync(process.execPath, [CLI, 'merges', ...PANEL], { encoding: 'utf8' });
  assert.equal(m.status, 0, m.stderr);
  assert.equal(m.stdout.trim().split('\n').length, 1);
});

test("noise-filter's output template conforms to the triage contract", () => {
  const tpl = outputTemplate(fs.readFileSync(path.join(PLUGIN, 'agents', 'noise-filter.md'), 'utf8'));
  assert.ok(tpl, 'noise-filter has a fenced template under "## Output contract"');
  const r = parseTriage(tpl, { template: true });
  assert.deepEqual(r.problems, []);
  assert.deepEqual(r.sections, TRIAGE_SECTIONS);
  assert.equal(r.tier, '<n>');
  assert.deepEqual(r.items.map((i) => i.id), ['T1', 'D1', 'W1', 'P1']);
});
