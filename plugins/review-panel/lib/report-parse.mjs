// Parser for the review-panel report contract
// (skills/assemble-panel/references/persona-contract.md, each agent's
// "Output contract"). No dependencies.
//
//   import { parseReport, parseTemplate } from './report-parse.mjs';
//   node report-parse.mjs findings <report.md>...   # JSONL, exit 1 on problems

import fs from 'node:fs';
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
const WHERE = /^([^\s:]+):(\d+)/;

function splitWhere(raw) {
  const m = raw.replace(/`/g, '').match(WHERE);
  return m ? { raw, path: m[1], line: Number(m[2]) } : { raw, path: null, line: null };
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

function main(argv) {
  const [cmd, ...files] = argv;
  if (cmd !== 'findings' || files.length === 0) {
    process.stderr.write('usage: node report-parse.mjs findings <report.md>...\n');
    return 2;
  }
  let status = 0;
  for (const file of files) {
    const r = parseReport(fs.readFileSync(file, 'utf8'));
    for (const f of r.findings) {
      process.stdout.write(`${JSON.stringify({ report: file, reviewer: r.reviewer, ...f })}\n`);
    }
    for (const p of r.problems) {
      process.stderr.write(`${file}: ${p}\n`);
      status = 1;
    }
  }
  return status;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === fs.realpathSync(process.argv[1])) {
  process.exitCode = main(process.argv.slice(2));
}
