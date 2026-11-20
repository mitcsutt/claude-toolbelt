// dx-advocate's doc checks: every command block stays inside the
// reference's own read-only allowlist, and each of the 14 checks finds its
// planted defect in the eval fixture (1.4.0) and stays quiet on 1.3.0.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PLUGIN = path.resolve(HERE, '..');
const DOC = fs.readFileSync(path.join(PLUGIN, 'references/dx-advocate/doc-checks.md'), 'utf8');
const FIXTURE = path.join(PLUGIN, 'evals/dx-advocate-planted-defects/fixture.sh');

// --- block extraction ------------------------------------------------------

/** The first ```bash block under the heading that starts with `heading`. */
function block(doc, heading) {
  const lines = doc.split('\n');
  const h = lines.findIndex((l) => l.startsWith(heading));
  if (h < 0) return null;
  const open = lines.findIndex((l, i) => i > h && l === '```bash');
  const next = lines.findIndex((l, i) => i > h && /^## /.test(l));
  if (open < 0 || (next >= 0 && next < open)) return null;
  const close = lines.findIndex((l, i) => i > open && l === '```');
  return lines.slice(open + 1, close).join('\n');
}

const allBlocks = (doc) => [...doc.matchAll(/^```bash\n([\s\S]*?)^```$/gm)].map((m) => m[1]);

// --- the allowlist, read from the reference's own Rules ------------------

function allowlist(doc) {
  const line = doc.split('\n').find((l) => l.startsWith('- Allowed:'));
  assert.ok(line, 'doc-checks.md has an "- Allowed:" rule');
  const items = [...line.matchAll(/`([^`]+)`/g)].map((m) => m[1]);
  const commands = new Set(items.map((i) => i.split(/\s+/)[0]).filter((w) => !w.startsWith('<')));
  const gitItem = items.find((i) => i.startsWith('git '));
  const git = new Set(gitItem.slice(4).split('|'));
  return { commands, git };
}

const KEYWORDS = new Set(['while', 'do', 'done', 'for', 'in', 'if', 'then', 'else', 'elif', 'fi', 'continue', '!']);

/** Replace quoted strings: '…' and "…" become Q, except "$T/…" which becomes TFILE. */
function stripQuotes(line) {
  let out = '';
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (c === "'") {
      const j = line.indexOf("'", i + 1);
      out += 'Q';
      i = j < 0 ? line.length : j;
    } else if (c === '"') {
      let j = i + 1;
      while (j < line.length && line[j] !== '"') j += line[j] === '\\' ? 2 : 1;
      out += /^\$T\//.test(line.slice(i + 1, j)) ? 'TFILE' : 'Q';
      i = j;
    } else {
      out += c;
    }
  }
  return out;
}

/** Problems with one line of a check block, against the allowlist. */
function lineProblems(raw, allow) {
  const problems = [];
  const line = raw.trim();
  if (!line || line.startsWith('#')) return problems;
  const s = stripQuotes(line);
  // Every command substitution, even one hidden inside double quotes.
  const words = [...line.matchAll(/\$\(\s*([^\s)"'|;]+)/g)].map((m) => m[1]);
  for (const seg of s.split(/\|\||&&|\$\(|[|;(]/)) {
    let toks = seg.replace(/\d*[<>]{1,2}&?\s*\S*/g, ' ').replace(/[)]/g, ' ').trim().split(/\s+/).filter(Boolean);
    if (toks[0] === 'for' && toks[2] === 'in') toks = [];
    while (toks.length && (KEYWORDS.has(toks[0]) || /^[A-Za-z_][A-Za-z0-9_]*=/.test(toks[0]))) toks.shift();
    if (!toks.length) continue;
    words.push(toks[0]);
    const [cmd, ...args] = toks;
    if (cmd === 'git' && !allow.git.has(args[0])) problems.push(`git ${args[0]} is not an allowed subcommand`);
    if (cmd === 'node' && !['-e', '--version'].includes(args[0])) problems.push('node only with -e or --version');
    if (cmd === 'bash' && args[0] !== '-n') problems.push('bash only with -n');
    if (cmd === 'sed' && args[0] !== '-n') problems.push('sed only with -n');
    if (cmd === 'mktemp' && args[0] !== '-d') problems.push('mktemp only with -d');
    if (cmd === 'find' && args.some((a) => a === '-delete' || a.startsWith('-exec') || a === '-ok')) problems.push('find with -delete or -exec');
  }
  for (const w of words) if (!allow.commands.has(w)) problems.push(`command not allowed: ${w}`);
  if (/\bsed\s+(-[a-z]*\s+)*-i/.test(s)) problems.push('sed -i');
  if (/\bawk\b/.test(s) && /system\s*\(|getline|print[^;']*>/.test(line)) problems.push('awk that does more than print');
  for (const m of s.matchAll(/\d*(>>?)(&\d|\s*([^\s;|)]+))/g)) {
    if (m[2].startsWith('&')) continue;
    if (!['TFILE', '/dev/null'].includes(m[3])) problems.push(`redirect into ${m[3]}`);
  }
  return problems;
}

const blockProblems = (text, allow) => text.split('\n').flatMap((l) => lineProblems(l, allow));

test('doc-checks.md has a setup block and one bash block for each of checks 1-14', () => {
  assert.ok(block(DOC, '## Setup'), 'setup block');
  for (let n = 1; n <= 14; n++) assert.ok(block(DOC, `## ${n}. `), `check ${n} has a bash block`);
});

test('every command in doc-checks.md is on its own allowlist, read-only and redirects only into $T', () => {
  const allow = allowlist(DOC);
  const blocks = allBlocks(DOC);
  assert.ok(blocks.length >= 15);
  for (const b of blocks) assert.deepEqual(blockProblems(b, allow), [], b);
});

test('the allowlist checker rejects what the rules forbid', () => {
  const allow = allowlist(DOC);
  const bad = {
    'make -n test': /command not allowed: make/,
    'grep -n foo README.md > out.txt': /redirect into out\.txt/,
    'echo x >> "$HOME/log"': /redirect into Q/,
    'sed -i s/a/b/ README.md': /sed only with -n/,
    'find . -name x -delete': /find with -delete/,
    'find . -exec rm {} +': /find with -delete or -exec/,
    'node scripts/setup.js': /node only with -e/,
    'bash scripts/setup.sh --help': /bash only with -n/,
    'git checkout main': /git checkout/,
    'x=$(npm run build)': /command not allowed: npm/,
    'echo "$(./scripts/setup.sh)"': /command not allowed: \.\/scripts\/setup\.sh/,
    "awk '{ system(\"rm -rf x\") }' f": /awk that does more/,
    'grep a f | xargs rm': /command not allowed: xargs/,
  };
  for (const [line, want] of Object.entries(bad)) {
    const p = lineProblems(line, allow);
    assert.ok(p.some((x) => want.test(x)), `${line} -> ${JSON.stringify(p)}`);
  }
  for (const ok of ['grep -c x f 2>/dev/null', 'sort -u > "$T/a"', 'echo hi >&2', 'node --version']) {
    assert.deepEqual(lineProblems(ok, allow), [], ok);
  }
});

// --- behaviour: run each check against the eval fixture -------------------

const hasTools = ['git', 'bash'].every((t) => spawnSync('sh', ['-c', `command -v ${t}`]).status === 0);

function buildFixture() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dx-fixture-'));
  const r = spawnSync('bash', [FIXTURE], { cwd: dir, encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr);
  return dir;
}

function run(dir, n, base) {
  const script = `${block(DOC, '## Setup')}\n${block(DOC, `## ${n}. `)}\n`;
  const env = { ...process.env, BASE: base, HOME: dir };
  const r = spawnSync('bash', ['-c', script], { cwd: dir, encoding: 'utf8', env });
  return r.stdout + r.stderr;
}

// What each check must print on 1.4.0, and must not print on 1.3.0.
const EXPECT = {
  1: { hit: /README\.md:\d+:.*DATABASE_URL/, quiet: /DATABASE_URL/ },
  2: { hit: /missing \.\/docs\/extra\.md:\d+ scripts\/reset\.sh/, quiet: /reset\.sh/, never: /missing .*(scripts\/setup\.sh|docs\/ci\.md|\.env\.example)/ },
  3: { hit: /broken \.\/docs\/extra\.md:\d+ missing\.md/, quiet: /broken/, never: /broken .*ci\.md/ },
  4: { hit: /phony with no rule \(exits 0, runs nothing\): test-unit/, quiet: /test-unit|no rule/, never: /: (test|lint)$/m },
  5: { hit: /documented script not in package\.json: seed$/m, quiet: /not in package/, never: /: migrate/ },
  6: { hit: /flag not in a case arm of scripts\/setup\.sh: --skip-seed \(\.\/README\.md:\d+\)/, quiet: /flag not in/, never: /--no-seed/ },
  7: { hit: /read by code, not documented: LEDGER_AUDIT_TIMEOUT_MS[\s\S]*documented, read by no code: DATABASE_URL[\s\S]*config\.js:\d+:env\.LEDGER_DB_URL \?\?/, quiet: /not documented|read by no code|\?\?/, never: /SESSION_SECRET/ },
  8: { hit: /no migration note for removed key: port$/m, quiet: /migration/ },
  9: { hit: /\.nvmrc: 18[\s\S]*script start: node --env-file=\.env[\s\S]*Node 18/, quiet: /--env-file/ },
  10: { hit: /scripts\/setup\.sh: #!\/bin\/bash[\s\S]*scripts\/setup\.sh:\d+:mapfile/, quiet: /mapfile/ },
  11: { hit: /scripts\/setup\.sh:\d+:\s+echo "config error" >&2/, quiet: /config error/ },
  12: { hit: /CLAUDE\.md:\d+:.*ask the maintainer[\s\S]*docs\/ci\.md:\d+:.*dontAsk/, quiet: /./ },
  13: { hit: /\$HOME\/\.config\/ledger\/credentials[\s\S]*\.git\/hooks\/pre-commit[\s\S]*## Uninstall/, quiet: /\.git\/hooks|\.config\/ledger/ },
  14: { hit: /CLAUDE\.md:\d+:- Every env var the code reads must be listed[\s\S]*extra\.md:\d+:- Never commit/, quiet: /make test-unit/, never: /make test-unit|ask the maintainer/ },
};

test('each doc check finds its planted defect in the 1.4.0 fixture and is quiet on 1.3.0', { skip: !hasTools && 'git or bash missing' }, () => {
  const dir = buildFixture();
  try {
    // Defects for checks 2, 3 and 5, which the eval case doesn't plant.
    fs.writeFileSync(path.join(dir, 'docs/extra.md'),
      '# Extra\n\nRun `scripts/reset.sh`, then `npm run seed`. See [the guide](missing.md) and [CI](ci.md).\n\n- Never commit a real `.env`.\n');
    const before = spawnSync('git', ['status', '--porcelain'], { cwd: dir, encoding: 'utf8' }).stdout;
    for (const [n, e] of Object.entries(EXPECT)) {
      const out = run(dir, n, 'HEAD~1');
      assert.match(out, e.hit, `check ${n} on 1.4.0:\n${out}`);
      if (e.never) assert.doesNotMatch(out, e.never, `check ${n} on 1.4.0 flags a sound item:\n${out}`);
    }
    const after = spawnSync('git', ['status', '--porcelain'], { cwd: dir, encoding: 'utf8' }).stdout;
    assert.equal(after, before, 'the checks write nothing in the repo they read');

    fs.rmSync(path.join(dir, 'docs/extra.md'));
    assert.equal(spawnSync('git', ['checkout', '-q', 'HEAD~1'], { cwd: dir }).status, 0);
    for (const [n, e] of Object.entries(EXPECT)) {
      if (n === '12' || n === '14') continue; // 12 is checked below; 14 lists rules, which 1.3.0 also has
      const out = run(dir, n, 'HEAD');
      assert.doesNotMatch(out, e.quiet, `check ${n} on 1.3.0 should be quiet:\n${out}`);
    }
    assert.equal(run(dir, 12, 'HEAD').trim(), '', 'check 12 is quiet without an unattended run');
    assert.doesNotMatch(run(dir, 14, 'HEAD'), EXPECT[14].quiet, 'check 14 on 1.3.0');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
