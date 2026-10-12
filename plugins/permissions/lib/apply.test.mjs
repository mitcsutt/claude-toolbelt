import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { applyChanges, ApplyError } from './apply.mjs';

const BIN = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'bin', 'perm-apply');
const tmp = () => fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'perm-apply-')));
const NOW = new Date('2026-10-09T01:02:03.456Z');
const read = (f) => fs.readFileSync(f, 'utf8');
const baks = (f) => fs.readdirSync(path.dirname(f)).filter((n) => n.includes('.bak-'));

function seed(obj) {
  const f = path.join(tmp(), 'settings.json');
  fs.writeFileSync(f, typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2) + '\n');
  return f;
}

test('adds append at end, preserving order and other keys', () => {
  const f = seed({ env: { SECRET: 'x' }, permissions: { allow: ['Read', 'Edit'], deny: ['Bash(rm *)'] }, model: 'opus' });
  const r = applyChanges({ file: f, changes: { addAllow: ['Bash(git status)'], addAsk: ['Bash(git push *)'] }, now: NOW });
  const doc = JSON.parse(read(f));
  assert.deepEqual(doc.permissions.allow, ['Read', 'Edit', 'Bash(git status)']);
  assert.deepEqual(doc.permissions.ask, ['Bash(git push *)']);
  assert.deepEqual(Object.keys(doc), ['env', 'permissions', 'model']);
  assert.deepEqual(Object.keys(doc.permissions), ['allow', 'deny', 'ask']);
  assert.deepEqual(r.added.allow, ['Bash(git status)']);
  assert.deepEqual(r.before, { allow: 2, deny: 1, ask: 0 });
  assert.deepEqual(r.after, { allow: 3, deny: 1, ask: 1 });
  assert.ok(read(f).endsWith('}\n'));
  assert.ok(read(f).includes('\n  "env"'));
});

test('removes rules and skips missing ones', () => {
  const f = seed({ permissions: { allow: ['A', 'B', 'C'] } });
  const r = applyChanges({ file: f, changes: { removeAllow: ['B', 'Z'], removeDeny: ['Q'] }, now: NOW });
  assert.deepEqual(JSON.parse(read(f)).permissions.allow, ['A', 'C']);
  assert.deepEqual(r.removed.allow, ['B']);
  assert.deepEqual(r.skipped.map((s) => [s.rule, s.reason]), [['Z', 'not present'], ['Q', 'not present']]);
});

test('adding an existing rule is skipped and causes no write or backup', () => {
  const f = seed({ permissions: { allow: ['Read'] } });
  const before = read(f);
  const r = applyChanges({ file: f, changes: { addAllow: ['Read', 'Read'] }, now: NOW });
  assert.equal(r.skipped.length, 2);
  assert.equal(r.skipped[0].reason, 'already present');
  assert.equal(r.backup, null);
  assert.equal(read(f), before);
  assert.deepEqual(baks(f), []);
});

test('duplicate add within one call adds once', () => {
  const f = seed({ permissions: { allow: [] } });
  const r = applyChanges({ file: f, changes: { addAllow: ['X', 'X'] }, now: NOW });
  assert.deepEqual(JSON.parse(read(f)).permissions.allow, ['X']);
  assert.equal(r.skipped.length, 1);
});

test('missing file is created with additions, including parent dir', () => {
  const f = path.join(tmp(), 'nested', 'dir', 'settings.json');
  const r = applyChanges({ file: f, changes: { addDeny: ['Bash(rm -rf *)'] }, now: NOW });
  assert.deepEqual(JSON.parse(read(f)), { permissions: { deny: ['Bash(rm -rf *)'] } });
  assert.equal(r.backup, null);
});

test('missing file with only removals is not created', () => {
  const f = path.join(tmp(), 'nested', 'settings.json');
  const r = applyChanges({ file: f, changes: { removeAllow: ['X'] }, now: NOW });
  assert.equal(fs.existsSync(f), false);
  assert.equal(fs.existsSync(path.dirname(f)), false);
  assert.equal(r.skipped[0].reason, 'not present');
});

test('file without permissions key gets one appended last', () => {
  const f = seed({ a: 1, b: 2 });
  applyChanges({ file: f, changes: { addAllow: ['X'] }, now: NOW });
  assert.deepEqual(Object.keys(JSON.parse(read(f))), ['a', 'b', 'permissions']);
});

test('unparseable file is refused and left byte-identical', () => {
  const body = '{ "permissions": { "allow": [ oops ';
  const f = seed(body);
  assert.throws(() => applyChanges({ file: f, changes: { addAllow: ['X'] }, now: NOW }), (e) => e instanceof ApplyError && e.exitCode === 1);
  assert.equal(read(f), body);
  assert.deepEqual(baks(f), []);
});

test('non-array list is refused untouched', () => {
  const body = '{"permissions":{"allow":"nope"}}';
  const f = seed(body);
  assert.throws(() => applyChanges({ file: f, changes: { addAllow: ['X'] }, now: NOW }), ApplyError);
  assert.equal(read(f), body);
});

test('dry-run writes nothing and creates no backup, but reports the diff', () => {
  const f = seed({ permissions: { allow: ['A'] } });
  const before = read(f);
  const r = applyChanges({ file: f, changes: { addAllow: ['B'], removeAllow: ['A'] }, dryRun: true, now: NOW });
  assert.equal(read(f), before);
  assert.deepEqual(baks(f), []);
  assert.deepEqual(r.added.allow, ['B']);
  assert.deepEqual(r.removed.allow, ['A']);
  assert.equal(r.backup, null);
  const g = path.join(tmp(), 'new.json');
  applyChanges({ file: g, changes: { addAllow: ['B'] }, dryRun: true, now: NOW });
  assert.equal(fs.existsSync(g), false);
});

test('backup content equals original and name has no colons', () => {
  const f = seed({ permissions: { allow: ['A'] }, keep: true });
  const before = read(f);
  const r = applyChanges({ file: f, changes: { addAllow: ['B'] }, now: NOW });
  assert.equal(read(r.backup), before);
  assert.ok(r.backup.startsWith(f + '.bak-'));
  assert.equal(path.basename(r.backup).includes(':'), false);
  assert.match(path.basename(r.backup), /\.bak-2026-10-09T/);
  assert.deepEqual(fs.readdirSync(path.dirname(f)).filter((n) => n.includes('.tmp-')), []);
});

test('blanket shell allow is refused with exit 3 unless forced', () => {
  for (const rule of ['Bash', 'Bash(*)', 'Bash(bash *)', 'Bash(sh *)']) {
    const f = seed({ permissions: { allow: [] } });
    const before = read(f);
    assert.throws(() => applyChanges({ file: f, changes: { addAllow: [rule] }, now: NOW }), (e) => e.exitCode === 3);
    assert.equal(read(f), before);
  }
  const f = seed({ permissions: { allow: [] } });
  applyChanges({ file: f, changes: { addAllow: ['Bash'] }, force: true, now: NOW });
  assert.deepEqual(JSON.parse(read(f)).permissions.allow, ['Bash']);
  // deny of blanket shell is fine
  const g = seed({ permissions: {} });
  applyChanges({ file: g, changes: { addDeny: ['Bash'] }, now: NOW });
});

function run(cmd, args) {
  return spawnSync(cmd, args, { encoding: 'utf8' });
}

for (const mode of ['node', 'direct']) {
  test(`launcher end-to-end (${mode})`, () => {
    const f = seed({ env: { TOKEN: 'sekrit-value' }, permissions: { allow: ['A'] } });
    const a = ['--file', f, '--add-allow', 'B', '--remove-allow', 'A', '--add-deny', 'D'];
    const r = mode === 'node' ? run('node', [BIN, ...a]) : run(BIN, a);
    assert.equal(r.status, 0, r.stderr);
    assert.match(r.stdout, /^\+ allow: B$/m);
    assert.match(r.stdout, /^\+ deny: D$/m);
    assert.match(r.stdout, /^- allow: A$/m);
    assert.match(r.stdout, /^backup: .*\.bak-/m);
    assert.equal(r.stdout.includes('sekrit-value'), false);
    assert.deepEqual(JSON.parse(read(f)).permissions, { allow: ['B'], deny: ['D'] });
  });
}

test('launcher is mode 755 and works via symlink', () => {
  assert.equal(fs.statSync(BIN).mode & 0o777, 0o755);
  const link = path.join(tmp(), 'perm-apply');
  fs.symlinkSync(BIN, link);
  const f = seed({ permissions: {} });
  const r = run(link, ['--file', f, '--add-ask', 'X', '--json']);
  assert.equal(r.status, 0, r.stderr);
  assert.deepEqual(JSON.parse(r.stdout).added.ask, ['X']);
});

test('launcher exit codes: unparseable 1, blanket 3, usage 2; dry-run text', () => {
  const body = 'not json';
  const f = seed(body);
  let r = run(BIN, ['--file', f, '--add-allow', 'X']);
  assert.equal(r.status, 1);
  assert.match(r.stderr, /not valid JSON/);
  assert.equal(read(f), body);
  const g = seed({ permissions: {} });
  r = run(BIN, ['--file', g, '--add-allow', 'Bash(*)']);
  assert.equal(r.status, 3);
  assert.match(r.stderr, /--force/);
  r = run(BIN, ['--add-allow', 'X']);
  assert.equal(r.status, 2);
  const before = read(g);
  r = run(BIN, ['--file', g, '--add-allow', 'X', '--dry-run']);
  assert.equal(r.status, 0);
  assert.match(r.stdout, /\+ allow: X/);
  assert.equal(read(g), before);
  assert.deepEqual(baks(g), []);
});

test('sandbox adds create nested objects, preserve order, never touch permissions', () => {
  const f = seed({ env: { A: '1' }, model: 'opus' });
  const r = applyChanges({ file: f, changes: { addSandboxAllowWrite: ['/tmp/x'], addSandboxExcludedCommand: ['git'] }, now: NOW });
  const doc = JSON.parse(read(f));
  assert.deepEqual(doc.sandbox, { filesystem: { allowWrite: ['/tmp/x'] }, excludedCommands: ['git'] });
  assert.deepEqual(Object.keys(doc), ['env', 'model', 'sandbox']);
  assert.equal(doc.permissions, undefined);
  assert.equal(JSON.stringify(doc).includes('"permissions"'), false);
  assert.deepEqual(r.sandbox.added.allowWrite, ['/tmp/x']);
  assert.ok(r.backup);
});

test('sandbox appends to existing arrays, skips duplicates, removes present only', () => {
  const f = seed({ sandbox: { enabled: true, filesystem: { allowWrite: ['/a'] }, excludedCommands: ['docker', 'git'] }, permissions: { allow: ['Read'] } });
  const r = applyChanges({
    file: f,
    changes: { addSandboxAllowWrite: ['/a', '/b'], removeSandboxExcludedCommand: ['docker', 'nope'], addSandboxExcludedCommand: ['git'] },
    now: NOW,
  });
  const doc = JSON.parse(read(f));
  assert.deepEqual(doc.sandbox.filesystem.allowWrite, ['/a', '/b']);
  assert.deepEqual(doc.sandbox.excludedCommands, ['git']);
  assert.equal(doc.sandbox.enabled, true);
  assert.deepEqual(doc.permissions, { allow: ['Read'] });
  assert.deepEqual(r.skipped.map((s) => [s.list, s.rule, s.reason]), [
    ['sandbox.filesystem.allowWrite', '/a', 'already present'],
    ['sandbox.excludedCommands', 'nope', 'not present'],
    ['sandbox.excludedCommands', 'git', 'already present'],
  ]);
});

test('sandbox removal never creates nested objects; no-op leaves file untouched', () => {
  const f = seed({ model: 'x' });
  const before = read(f);
  const r = applyChanges({ file: f, changes: { removeSandboxAllowWrite: ['/a'], removeSandboxExcludedCommand: ['git'] }, now: NOW });
  assert.equal(r.changed, false);
  assert.equal(read(f), before);
  assert.deepEqual(baks(f), []);
  const g = path.join(tmp(), 'new.json');
  applyChanges({ file: g, changes: { removeSandboxAllowWrite: ['/a'] }, now: NOW });
  assert.equal(fs.existsSync(g), false);
});

test('sandbox dry-run writes nothing', () => {
  const f = seed({ a: 1 });
  const before = read(f);
  const r = applyChanges({ file: f, changes: { addSandboxExcludedCommand: ['git'] }, dryRun: true, now: NOW });
  assert.equal(read(f), before);
  assert.deepEqual(r.sandbox.added.excludedCommands, ['git']);
  assert.deepEqual(baks(f), []);
});

test('sandbox allowWrite of root/home is refused with exit 3 unless forced', () => {
  for (const p of ['/', '~', '$HOME', '${HOME}', '~/', os.homedir(), os.homedir() + '/']) {
    const f = seed({});
    const before = read(f);
    assert.throws(() => applyChanges({ file: f, changes: { addSandboxAllowWrite: [p] }, now: NOW }), (e) => e.exitCode === 3, p);
    assert.equal(read(f), before);
  }
  const f = seed({});
  applyChanges({ file: f, changes: { addSandboxAllowWrite: ['~'] }, force: true, now: NOW });
  assert.deepEqual(JSON.parse(read(f)).sandbox.filesystem.allowWrite, ['~']);
  // subdirectories of home are fine
  const g = seed({});
  applyChanges({ file: g, changes: { addSandboxAllowWrite: ['~/.cache', os.homedir() + '/proj'] }, now: NOW });
});

test('sandbox wrong-shaped values are refused untouched', () => {
  const body = '{"sandbox":{"excludedCommands":"git"}}';
  const f = seed(body);
  assert.throws(() => applyChanges({ file: f, changes: { addSandboxExcludedCommand: ['x'] }, now: NOW }), ApplyError);
  assert.equal(read(f), body);
});

test('launcher sandbox flags: text diff lines, json, exit 3', () => {
  const f = seed({ permissions: { allow: ['A'] } });
  let r = run(BIN, ['--file', f, '--add-sandbox-excluded-command', 'git', '--add-sandbox-allow-write', '/tmp/w']);
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /^\+ sandbox\.excludedCommands: git$/m);
  assert.match(r.stdout, /^\+ sandbox\.filesystem\.allowWrite: \/tmp\/w$/m);
  r = run(BIN, ['--file', f, '--remove-sandbox-excluded-command', 'git', '--json']);
  assert.equal(r.status, 0, r.stderr);
  assert.deepEqual(JSON.parse(r.stdout).sandbox.removed.excludedCommands, ['git']);
  assert.deepEqual(JSON.parse(read(f)).sandbox.excludedCommands, []);
  r = run(BIN, ['--file', f, '--add-sandbox-allow-write', '/']);
  assert.equal(r.status, 3);
  assert.match(r.stderr, /--force/);
  r = run(BIN, ['--file', f, '--remove-sandbox-allow-write', '/tmp/w', '--dry-run']);
  assert.match(r.stdout, /^- sandbox\.filesystem\.allowWrite: \/tmp\/w$/m);
  assert.equal(JSON.parse(read(f)).permissions.sandbox, undefined);
});

test('symlinked settings file: target is written, link stays a link', () => {
  const d = tmp();
  const target = path.join(d, 'real.json');
  fs.writeFileSync(target, '{"permissions":{"allow":["Read"]}}\n');
  const link = path.join(d, 'link.json');
  fs.symlinkSync(target, link);
  const r = applyChanges({ file: link, changes: { addAllow: ['Bash(git status)'] }, now: NOW });
  assert.ok(fs.lstatSync(link).isSymbolicLink());
  assert.deepEqual(JSON.parse(read(target)).permissions.allow, ['Read', 'Bash(git status)']);
  assert.equal(fs.realpathSync(r.file), fs.realpathSync(target));
  assert.ok(baks(target).length === 1);
});

test('tilde paths expand to HOME; literal ~ segment refused', () => {
  const home = tmp();
  const cwd = tmp();
  const env = { ...process.env, HOME: home };
  const r = spawnSync('node', [BIN, '--file', '~/x/.claude/settings.json', '--add-allow', 'Bash(git status)'], { encoding: 'utf8', env, cwd });
  assert.equal(r.status, 0, r.stderr);
  assert.ok(fs.existsSync(path.join(home, 'x/.claude/settings.json')));
  assert.ok(!fs.existsSync(path.join(cwd, '~')));
  const r2 = spawnSync('node', [BIN, '--file', 'a/~/settings.json', '--add-allow', 'Bash(git status)'], { encoding: 'utf8', env, cwd });
  assert.equal(r2.status, 2);
  assert.match(r2.stderr, /literal "~"/);
});

test('risky allow rules (R-checks) refused with exit 3; safe one passes; --force overrides', () => {
  for (const rule of ['Bash(bash:*)', 'Bash(zsh *)', 'Bash(python3 *)', 'Bash(*)', 'Bash']) {
    const f = seed({});
    assert.throws(() => applyChanges({ file: f, changes: { addAllow: [rule] }, now: NOW }), (e) => e instanceof ApplyError && e.exitCode === 3, rule);
  }
  const f = seed({});
  applyChanges({ file: f, changes: { addAllow: ['Bash(git status)'] }, now: NOW });
  applyChanges({ file: f, changes: { addAllow: ['Bash(python3 *)'] }, force: true, now: NOW });
  assert.deepEqual(JSON.parse(read(f)).permissions.allow, ['Bash(git status)', 'Bash(python3 *)']);
});

test('trailing-newline style preserved', () => {
  const f = seed('{"permissions":{"allow":[]}}');
  applyChanges({ file: f, changes: { addAllow: ['Bash(git status)'] }, now: NOW });
  assert.ok(read(f).endsWith('}'));
  assert.ok(read(f).includes('\n  "permissions"'));
});

test('stale lock is broken; fresh lock blocks then errors', () => {
  const f = seed({});
  const lock = `${f}.lock`;
  fs.mkdirSync(lock);
  const old = new Date(Date.now() - 20000);
  fs.utimesSync(lock, old, old);
  applyChanges({ file: f, changes: { addAllow: ['Bash(git status)'] }, now: NOW });
  assert.ok(!fs.existsSync(lock));
  assert.deepEqual(JSON.parse(read(f)).permissions.allow, ['Bash(git status)']);
});
