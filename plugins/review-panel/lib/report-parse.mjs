// Parser for the review-panel report contract
// (skills/assemble-panel/references/persona-contract.md, each agent's
// "Output contract") and for noise-filter's triage (agents/noise-filter.md).
// No dependencies.
//
//   import { parseReport, parseTemplate, parseTriage, ... } from './report-parse.mjs';
//   node report-parse.mjs findings [--shuffle] [--seed N] [--compact] <report.md>...
//   node report-parse.mjs merges [--window N] <report.md>...
//   node report-parse.mjs check-triage <triage.md> <report.md>...

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const FIELDS = ['Where', 'What', 'Consequence', 'Evidence', 'Fix', 'Severity', 'Confidence'];
const SEVERITIES = ['BLOCK', 'SHOULD', 'COULD'];
const CONFIDENCES = ['high', 'medium', 'low'];

const TITLE = /^# (\S+) review: (.+)$/;
const VERDICT = /^Verdict: (SHIP WITH FIXES|SHIP|DO NOT SHIP)(?::\s*(.*))?$/;
const VERDICT_TEMPLATE = 'Verdict: SHIP | SHIP WITH FIXES | DO NOT SHIP: <one sentence>';
const TIER = /^Tier: T([0-2])\b/;
const TIER_TEMPLATE = /^Tier: T<n>/;
const SECTION = /^## (.+)$/;
const FINDING = /^### (F\d+)\s+(.*)$/;
const FIELD = new RegExp(`^- (${FIELDS.join('|')}):\\s*(.*)$`);
const WHERE = /^([^\s:]+):(\d+)(?:-(\d+))?/;

function splitWhere(raw) {
  const m = raw.replace(/`/g, '').replace(/^\.\//, '').match(WHERE);
  if (!m) return { raw, path: null, line: null, lineEnd: null };
  const line = Number(m[2]);
  return { raw, path: m[1], line, lineEnd: m[3] ? Number(m[3]) : null };
}

function parse(text, template) {
  const out = {
    reviewer: null, artifact: null, verdict: null, verdictReason: null,
    tier: null, sections: [], findings: [], problems: [],
  };
  let section = null; // current "## " heading
  let finding = null; // current "### F<n>" under ## Findings
  let field = null;   // field the next continuation line belongs to
  let fenced = false;
  let tierLine = null;

  for (const line of text.split('\n')) {
    if (fenced || line.startsWith('```')) {
      if (line.startsWith('```')) fenced = !fenced;
      if (finding && field) finding.fields[field] += `\n${line}`;
      continue;
    }
    let m;
    if (out.reviewer === null && (m = line.match(TITLE))) {
      [, out.reviewer, out.artifact] = m;
      continue;
    }
    if (out.verdict === null && section === null) {
      if (template && line === VERDICT_TEMPLATE) {
        out.verdict = 'SHIP | SHIP WITH FIXES | DO NOT SHIP';
        out.verdictReason = '<one sentence>';
        continue;
      }
      if (!template && (m = line.match(VERDICT))) {
        out.verdict = m[1];
        out.verdictReason = m[2] ?? null;
        continue;
      }
    }
    if (tierLine === null && section === null && line.startsWith('Tier:')) {
      tierLine = line;
      if ((m = line.match(TIER))) out.tier = Number(m[1]);
      else if (template && TIER_TEMPLATE.test(line)) out.tier = '<n>';
      else out.problems.push(`Tier line is not "Tier: T0|T1|T2": ${line}`);
      continue;
    }
    if ((m = line.match(SECTION))) {
      section = m[1].trim();
      out.sections.push(section);
      finding = null;
      field = null;
      continue;
    }
    if (section === 'Findings' && (m = line.match(FINDING))) {
      finding = { id: m[1], title: m[2].trim(), fields: {} };
      out.findings.push(finding);
      field = null;
      continue;
    }
    if (finding && (m = line.match(FIELD))) {
      field = m[1];
      finding.fields[field] = m[2];
      continue;
    }
    if (finding && field && line.trim() !== '') finding.fields[field] += `\n${line}`;
  }

  if (out.reviewer === null) out.problems.push('missing title "# <reviewer> review: <artifact>"');
  if (out.verdict === null) out.problems.push('missing "Verdict: SHIP | SHIP WITH FIXES | DO NOT SHIP" line');
  if (!out.sections.includes('Findings')) out.problems.push('missing "## Findings" section');

  out.findings = out.findings.map(({ id, title, fields }) => {
    for (const f of FIELDS) {
      if (!(f in fields)) out.problems.push(`${id}: missing "- ${f}:"`);
    }
    const severity = (fields.Severity ?? '').trim();
    const confidence = (fields.Confidence ?? '').trim();
    const sevOk = template ? severity === SEVERITIES.join(' | ') : SEVERITIES.includes(severity);
    const confOk = template ? confidence === CONFIDENCES.join(' | ') : CONFIDENCES.includes(confidence);
    if ('Severity' in fields && !sevOk) out.problems.push(`${id}: Severity "${severity}" is not one of ${SEVERITIES.join('|')}`);
    if ('Confidence' in fields && !confOk) out.problems.push(`${id}: Confidence "${confidence}" is not one of ${CONFIDENCES.join('|')}`);
    return {
      id,
      title,
      where: splitWhere((fields.Where ?? '').trim()),
      what: fields.What ?? null,
      consequence: fields.Consequence ?? null,
      evidence: fields.Evidence ?? null,
      fix: fields.Fix ?? null,
      severity: fields.Severity === undefined ? null : severity,
      confidence: fields.Confidence === undefined ? null : confidence,
    };
  });
  return out;
}

/** Parse a written report. `problems` is empty when it conforms. */
export function parseReport(text) {
  return parse(text, false);
}

/** Parse an agent's output template: placeholders allowed, shape checked. */
export function parseTemplate(text) {
  return parse(text, true);
}

// ---------------------------------------------------------------------------
// noise-filter: the bookkeeping half of the triage, in code.

const SEV_RANK = { BLOCK: 3, SHOULD: 2, COULD: 1 };

/** mulberry32 + Fisher-Yates. Returns a new array; the input is untouched. */
export function shuffle(items, seed) {
  let a = seed >>> 0;
  const rand = () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const out = items.slice();
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

/**
 * Parse every report and index its findings as "<reviewer> F<n>".
 * With a numeric seed the report order is shuffled; otherwise it is the
 * order given. Report problems are prefixed with the file.
 */
export function collectFindings(paths, { seed = null } = {}) {
  const problems = [];
  const reports = [];
  const seen = new Map();
  for (const file of paths) {
    const r = parseReport(fs.readFileSync(file, 'utf8'));
    const reviewer = r.reviewer ?? path.basename(file).replace(/\.md$/, '');
    for (const p of r.problems) problems.push(`${file}: ${p}`);
    if (seen.has(reviewer)) {
      problems.push(`${file}: reviewer "${reviewer}" also names ${seen.get(reviewer)}; keys would collide`);
      continue;
    }
    seen.set(reviewer, file);
    reports.push({ reviewer, file, findings: r.findings });
  }
  const ordered = seed === null ? reports : shuffle(reports, seed);
  const findings = [];
  for (const { reviewer, file, findings: fs_ } of ordered) {
    for (const f of fs_) {
      findings.push({
        key: `${reviewer} ${f.id}`, reviewer, id: f.id,
        severity: f.severity, confidence: f.confidence, where: f.where, title: f.title, file,
      });
    }
  }
  return { order: ordered.map((r) => r.reviewer), seed, findings, problems };
}

/**
 * Candidate merge groups: same normalised path and line ranges within
 * `window` lines of each other (or overlapping). Findings without a line are
 * never grouped. Every group is only a candidate: location alone doesn't
 * make two findings the same cause.
 */
export function mergeCandidates(findings, { window = 5 } = {}) {
  const located = findings.filter((f) => f.where && f.where.path && Number.isInteger(f.where.line));
  const parent = located.map((_, i) => i);
  const find = (i) => (parent[i] === i ? i : (parent[i] = find(parent[i])));
  const span = (f) => [f.where.line, f.where.lineEnd ?? f.where.line];
  for (let i = 0; i < located.length; i++) {
    for (let j = i + 1; j < located.length; j++) {
      if (located[i].where.path !== located[j].where.path) continue;
      const [a1, a2] = span(located[i]);
      const [b1, b2] = span(located[j]);
      const gap = Math.max(0, Math.max(a1, b1) - Math.min(a2, b2));
      if (gap <= window) parent[find(i)] = find(j);
    }
  }
  const groups = new Map();
  located.forEach((f, i) => {
    const root = find(i);
    if (!groups.has(root)) groups.set(root, []);
    groups.get(root).push(f);
  });
  return [...groups.values()].filter((g) => g.length >= 2).map((g) => {
    const sevs = [...new Set(g.map((f) => f.severity).filter((s) => s in SEV_RANK))]
      .sort((x, y) => SEV_RANK[y] - SEV_RANK[x]);
    return {
      keys: g.map((f) => f.key),
      path: g[0].where.path,
      lines: [...new Set(g.map((f) => f.where.line))].sort((x, y) => x - y),
      reviewers: [...new Set(g.map((f) => f.reviewer))],
      sevMax: sevs[0] ?? null,
      sevSpread: sevs,
    };
  });
}

export const TRIAGE_SECTIONS = [
  'At a glance', 'Fix before merge', 'Decisions for you', 'Worth doing',
  'Pre-existing', 'Dropped', 'Coverage',
];
export const DROP_REASONS = ['evidence', 'unsupported', 'out of goal', 'nit', 'unspecific', 'self-solving'];
const ITEM_LETTER = { 'Fix before merge': 'T', 'Decisions for you': 'D', 'Worth doing': 'W', 'Pre-existing': 'P' };

const TRIAGE_TITLE = /^# Panel triage: (.+)$/;
const OVERALL = /^Overall: (SHIP WITH FIXES|SHIP|DO NOT SHIP)(?::\s*(.*))?$/;
const OVERALL_TEMPLATE = 'Overall: SHIP | SHIP WITH FIXES | DO NOT SHIP: <one sentence>';
const READ_ORDER = /read order ([^(]+) \(seed (\d+)\)/;
const ITEM = /^### ([TDWP])(\d+)\s+(.*?)(?:\s+\[cross-confirmed: ([^\]]+)\])?\s*$/;
const SOURCES = /^- Sources:\s*(.+)$/;
const KEY = /([^\s,|()[\]]+) F(\d+)\b/g;
const NEW_SOURCE = /new \(spot-check of [^)]+\)/g;
const SAME_CAUSE = /\(same cause:\s*([^)]*)\)/;
const ITEM_SEVERITY = /^- Severity:\s*(BLOCK|SHOULD|COULD)\b/;
const TABLE_SEPARATOR = /^\|[\s:|-]+\|?\s*$/;

const unwrap = (name) => name.replace(/^[`*_"'<>]+|[`*_"'<>]+$/g, '');
const keysIn = (text) => [...text.matchAll(KEY)].map((m) => `${unwrap(m[1])} F${m[2]}`);

/** Parse a noise-filter triage (or, with template: true, its output template). */
export function parseTriage(text, { template = false } = {}) {
  const out = {
    artifact: null, overall: null, overallReason: null, tier: null, order: null, seed: null,
    sections: [], glanceLines: 0, items: [], dropped: [], problems: [],
  };
  let section = null;
  let item = null;
  let tableHeaderSeen = false;
  let fenced = false;
  let m;
  for (const line of text.split('\n')) {
    if (line.startsWith('```')) { fenced = !fenced; if (item) item.lines += 1; continue; }
    if (fenced) { if (item) item.lines += 1; continue; }
    if (out.artifact === null && section === null && (m = line.match(TRIAGE_TITLE))) {
      out.artifact = m[1];
      continue;
    }
    if (section === null && out.overall === null && line.startsWith('Overall:')) {
      if (template && line === OVERALL_TEMPLATE) {
        out.overall = 'SHIP | SHIP WITH FIXES | DO NOT SHIP';
      } else if (!template && (m = line.match(OVERALL))) {
        out.overall = m[1];
        out.overallReason = m[2] ?? null;
      } else {
        out.problems.push(`Overall line is not "Overall: SHIP | SHIP WITH FIXES | DO NOT SHIP": ${line}`);
      }
      continue;
    }
    if (section === null && out.tier === null && line.startsWith('Tier:')) {
      if ((m = line.match(TIER))) out.tier = Number(m[1]);
      else if (template && TIER_TEMPLATE.test(line)) out.tier = '<n>';
      else out.problems.push(`Tier line is not "Tier: T0|T1|T2": ${line}`);
      if (!template && (m = line.match(READ_ORDER))) {
        out.order = m[1].trim().split(/[\s,]+/).filter(Boolean);
        out.seed = Number(m[2]);
      }
      continue;
    }
    if ((m = line.match(SECTION))) {
      section = m[1].trim();
      out.sections.push(section);
      item = null;
      continue;
    }
    if ((m = line.match(ITEM))) {
      const [, letter, num, title, tag] = m;
      const want = ITEM_LETTER[section];
      // An item under an unknown heading is covered by that heading's problem.
      if (want === undefined && TRIAGE_SECTIONS.includes(section)) out.problems.push(`### ${letter}${num} sits under "## ${section}", which takes no items`);
      else if (want !== undefined && letter !== want) out.problems.push(`### ${letter}${num} sits under "## ${section}", whose items are ${want}<n>`);
      item = {
        section, id: `${letter}${num}`, title: title.trim(),
        tagged: tag ? tag.split(',').map((t) => unwrap(t.trim())).filter(Boolean) : [],
        tag: Boolean(tag), sources: [], newSources: 0, sameCause: null,
        sourcesLines: 0, severity: null, lines: 0,
      };
      out.items.push(item);
      continue;
    }
    if (item) {
      if (line.trim() !== '') item.lines += 1;
      if ((m = line.match(SOURCES))) {
        item.sourcesLines += 1;
        let rest = m[1];
        item.newSources += (rest.match(NEW_SOURCE) || []).length;
        rest = rest.replace(NEW_SOURCE, '');
        const cause = rest.match(SAME_CAUSE);
        if (cause) item.sameCause = cause[1].trim();
        item.sources.push(...keysIn(rest.replace(SAME_CAUSE, '')));
      } else if ((m = line.match(ITEM_SEVERITY))) {
        item.severity = m[1];
      }
      continue;
    }
    if (section === 'At a glance' && line.trim() !== '') out.glanceLines += 1;
    if (section === 'Dropped' && line.startsWith('|')) {
      if (TABLE_SEPARATOR.test(line)) continue;
      if (!tableHeaderSeen) { tableHeaderSeen = true; continue; }
      const cells = line.replace(/^\|/, '').replace(/\|\s*$/, '').split(/(?<!\\)\|/).map((c) => c.trim());
      const reason = cells.slice(2).join(' | ');
      const row = { sources: keysIn(cells[0] ?? ''), reason, line };
      if (!DROP_REASONS.some((r) => reason.startsWith(`${r}:`))) {
        out.problems.push(`Dropped row reason must start with one of ${DROP_REASONS.join(', ')} and ":": ${line}`);
      }
      if (row.sources.length === 0) out.problems.push(`Dropped row names no "<reviewer> F<n>" in its first cell: ${line}`);
      out.dropped.push(row);
    }
  }

  if (out.artifact === null) out.problems.push('missing title "# Panel triage: <artifact>"');
  if (out.overall === null) out.problems.push('missing "Overall: SHIP | SHIP WITH FIXES | DO NOT SHIP" line');
  if (out.tier === null) out.problems.push('missing "Tier: T<n>" line');

  // Sections: exactly the contract, in order. An unknown heading in a
  // required slot is reported once, naming the heading it should have been.
  let idx = 0;
  out.sections.forEach((h, i) => {
    if (h === TRIAGE_SECTIONS[idx]) { idx += 1; return; }
    const at = TRIAGE_SECTIONS.indexOf(h);
    if (at > idx) {
      for (const missing of TRIAGE_SECTIONS.slice(idx, at)) out.problems.push(`missing section; expected "## ${missing}" before "## ${h}"`);
      idx = at + 1;
    } else if (at >= 0) {
      out.problems.push(`section "## ${h}" is repeated or out of order`);
    } else {
      const expected = TRIAGE_SECTIONS[idx];
      out.problems.push(expected
        ? `unknown section "## ${h}"; expected "## ${expected}"`
        : `unknown section "## ${h}"; the triage takes no sections beyond "## Coverage"`);
      if (expected && !out.sections.slice(i + 1).includes(expected)) idx += 1;
    }
  });
  for (const missing of TRIAGE_SECTIONS.slice(idx)) out.problems.push(`missing section; expected "## ${missing}"`);

  for (const it of out.items) {
    if (it.sourcesLines !== 1) out.problems.push(`${it.id}: needs exactly one "- Sources:" line, has ${it.sourcesLines}`);
  }
  return out;
}

const reviewerOf = (key) => key.replace(/ F\d+$/, '');

/**
 * Account for every input finding against a parsed triage. `findings` is
 * collectFindings().findings (or any [{ key }]).
 */
export function checkTriage(triage, findings) {
  const input = new Set(findings.map((f) => f.key));
  const places = new Map();
  const note = (key, place) => {
    if (!places.has(key)) places.set(key, []);
    places.get(key).push(place);
  };
  for (const it of triage.items) for (const k of it.sources) note(k, it.id);
  triage.dropped.forEach((row, i) => { for (const k of row.sources) note(k, `Dropped row ${i + 1}`); });

  const missing = [...input].filter((k) => !places.has(k));
  const duplicated = [...places].filter(([, p]) => p.length > 1).map(([key, p]) => ({ key, places: p }));
  const unknown = [...places.keys()].filter((k) => !input.has(k));
  const tagErrors = [];
  const causeErrors = [];
  const warnings = [];
  for (const it of triage.items) {
    const distinct = [...new Set(it.sources.map(reviewerOf))].sort();
    const tagged = [...new Set(it.tagged)].sort();
    if (it.tag && distinct.length < 2) tagErrors.push(it.id);
    else if (!it.tag && distinct.length >= 2) tagErrors.push(it.id);
    else if (it.tag && tagged.join(',') !== distinct.join(',')) tagErrors.push(it.id);
    if (it.sources.length >= 2 && !it.sameCause) causeErrors.push(it.id);
    const budget = /^[TD]/.test(it.id) ? 8 : 3;
    if (it.lines > budget) warnings.push(`${it.id} has ${it.lines} lines (budget ${budget})`);
  }
  if (triage.glanceLines > 9) warnings.push(`At a glance has ${triage.glanceLines} lines (budget 9)`);
  const fix = triage.items.filter((it) => it.section === 'Fix before merge');
  for (let i = 1; i < fix.length; i++) {
    if ((SEV_RANK[fix[i].severity] ?? 0) > (SEV_RANK[fix[i - 1].severity] ?? 0)) {
      warnings.push(`Fix before merge is not sorted by severity: ${fix[i].id} (${fix[i].severity}) after ${fix[i - 1].id} (${fix[i - 1].severity ?? 'none'})`);
    }
  }
  const count = (s) => triage.items.filter((it) => it.section === s).length;
  return {
    accounted: [...input].filter((k) => places.has(k)).length,
    total: input.size,
    missing, duplicated, unknown, tagErrors, causeErrors,
    counts: {
      fix: count('Fix before merge'), decide: count('Decisions for you'), worth: count('Worth doing'),
      preExisting: count('Pre-existing'), dropped: triage.dropped.length, findings: input.size,
    },
    warnings,
    problems: triage.problems,
  };
}

// ---------------------------------------------------------------------------
// CLI

const USAGE = `usage: node report-parse.mjs findings [--shuffle] [--seed N] [--compact] <report.md>...
       node report-parse.mjs merges [--window N] <report.md>...
       node report-parse.mjs check-triage <triage.md> <report.md>...
`;

function parseFlags(args, valued) {
  const flags = {};
  const rest = [];
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (!a.startsWith('--')) { rest.push(a); continue; }
    const name = a.slice(2);
    if (valued.includes(name)) {
      const v = Number(args[++i]);
      if (!Number.isInteger(v) || v < 0) throw new Error(`--${name} needs a non-negative integer`);
      flags[name] = v;
    } else {
      flags[name] = true;
    }
  }
  return { flags, rest };
}

const out = (line) => process.stdout.write(`${line}\n`);
const err = (line) => process.stderr.write(`${line}\n`);

function cmdFindings(args) {
  const { flags, rest } = parseFlags(args, ['seed']);
  if (rest.length === 0) return 2;
  const shuffled = flags.shuffle || flags.seed !== undefined;
  const seed = shuffled ? (flags.seed ?? Date.now() % 2 ** 32) : null;
  const c = collectFindings(rest, { seed });
  if (shuffled) out(JSON.stringify({ order: c.order, seed: c.seed }));
  if (flags.compact) {
    for (const f of c.findings) {
      out(JSON.stringify({ key: f.key, sev: f.severity, conf: f.confidence, where: f.where.raw.replace(/`/g, ''), title: f.title }));
    }
  } else {
    const byKey = new Map();
    for (const file of rest) {
      const r = parseReport(fs.readFileSync(file, 'utf8'));
      for (const f of r.findings) byKey.set(`${file}\0${f.id}`, { report: file, reviewer: r.reviewer, ...f });
    }
    for (const f of c.findings) out(JSON.stringify(byKey.get(`${f.file}\0${f.id}`)));
  }
  for (const p of c.problems) err(p);
  return c.problems.length ? 1 : 0;
}

function cmdMerges(args) {
  const { flags, rest } = parseFlags(args, ['window']);
  if (rest.length === 0) return 2;
  const c = collectFindings(rest);
  for (const g of mergeCandidates(c.findings, { window: flags.window ?? 5 })) out(JSON.stringify(g));
  for (const p of c.problems) err(p);
  return 0;
}

function cmdCheckTriage(args) {
  const [triagePath, ...reports] = args;
  if (!triagePath || reports.length === 0) return 2;
  const c = collectFindings(reports);
  const r = checkTriage(parseTriage(fs.readFileSync(triagePath, 'utf8')), c.findings);
  const n = r.counts;
  out(`accounted ${r.accounted}/${r.total} · fix ${n.fix} / decide ${n.decide} / worth ${n.worth} / pre-existing ${n.preExisting} / dropped rows ${n.dropped} (findings ${n.findings})`);
  for (const k of r.missing) out(`missing: ${k} appears on no Sources line or Dropped row`);
  for (const d of r.duplicated) out(`duplicated: ${d.key} appears in ${d.places.join(', ')}`);
  for (const k of r.unknown) out(`unknown: ${k} is not a finding in any report`);
  for (const id of r.tagErrors) out(`tag: ${id} [cross-confirmed: …] must list exactly the distinct reviewers on its Sources line, and only when there are two or more`);
  for (const id of r.causeErrors) out(`cause: ${id} has two or more sources but no "(same cause: <clause>)"`);
  for (const p of r.problems) out(`format: ${p}`);
  for (const w of r.warnings) out(`warn: ${w}`);
  for (const p of c.problems) err(p);
  const failed = r.missing.length + r.duplicated.length + r.unknown.length
    + r.tagErrors.length + r.causeErrors.length + r.problems.length;
  return failed ? 1 : 0;
}

function main(argv) {
  const [cmd, ...args] = argv;
  const commands = { findings: cmdFindings, merges: cmdMerges, 'check-triage': cmdCheckTriage };
  let status = 2;
  try {
    if (commands[cmd]) status = commands[cmd](args);
  } catch (e) {
    err(e.message); // unreadable file or bad flag value
    return 2;
  }
  if (status === 2) process.stderr.write(USAGE);
  return status;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === fs.realpathSync(process.argv[1])) {
  process.exitCode = main(process.argv.slice(2));
}
