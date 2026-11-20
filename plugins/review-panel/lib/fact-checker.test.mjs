import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseTemplate } from './report-parse.mjs';

const md = readFileSync(new URL('../agents/fact-checker.md', import.meta.url), 'utf8');
const block = md.split('## Output contract')[1].match(/```\n([\s\S]*?)\n```/)[1];

test('fact-checker template: ledger, findings, coverage, checked-and-fine in order', () => {
  const r = parseTemplate(block);
  assert.deepEqual(r.problems, []);
  assert.equal(r.tier, '<n>');
  assert.deepEqual(r.sections, ['Claim ledger', 'Findings', 'Coverage', 'Checked and fine']);
});
