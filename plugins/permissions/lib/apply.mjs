// perm-apply logic: the single writer for permissions.allow/deny/ask.
// Prints only the permissions diff, never the file body (env values stay private).
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const LISTS = ['allow', 'deny', 'ask'];
const BLANKET = new Set(['Bash', 'Bash(*)', 'Bash(bash *)', 'Bash(sh *)']);

export class ApplyError extends Error {
  constructor(message, exitCode = 1) {
    super(message);
    this.exitCode = exitCode;
  }
}

const emptyLists = () => ({ allow: [], deny: [], ask: [] });

function counts(perms) {
  const out = {};
  for (const l of LISTS) out[l] = Array.isArray(perms?.[l]) ? perms[l].length : 0;
  return out;
}

// sandbox lists live at top-level `sandbox` (never permissions.sandbox).
const SB = [
  { key: 'AllowWrite', label: 'allowWrite', keys: ['filesystem', 'allowWrite'] },
  { key: 'ExcludedCommand', label: 'excludedCommands', keys: ['excludedCommands'] },
];

export function isHomeOrRoot(p, home = os.homedir()) {
  let v = String(p).trim().replace(/\/+$/, '');
  if (v === '') return true; // was '/'
  v = v.replace(/^\$\{HOME\}/, '$HOME');
  return v === '~' || v === '$HOME' || (!!home && v === home.replace(/\/+$/, ''));
}

export function isBlanketShell(rule) {
  return BLANKET.has(String(rule).trim());
}

// changes: { addAllow, removeAllow, addDeny, removeDeny, addAsk, removeAsk } (string arrays)
export function applyChanges({ file, changes = {}, dryRun = false, now = new Date(), force = false }) {
  const cap = (l) => l[0].toUpperCase() + l.slice(1);
  const adds = {};
  const removes = {};
  for (const l of LISTS) {
    adds[l] = changes[`add${cap(l)}`] || [];
    removes[l] = changes[`remove${cap(l)}`] || [];
  }
  if (!force) {
    const bad = adds.allow.filter(isBlanketShell);
    if (bad.length) {
      throw new ApplyError(
        `refusing to add blanket shell allow rule(s): ${bad.join(', ')} (pass --force to override)`,
        3,
      );
    }
  }
  const sbAdd = {};
  const sbRem = {};
  for (const x of SB) {
    sbAdd[x.label] = changes[`addSandbox${x.key}`] || [];
    sbRem[x.label] = changes[`removeSandbox${x.key}`] || [];
  }
  if (!force) {
    const bad = sbAdd.allowWrite.filter((p) => isHomeOrRoot(p));
    if (bad.length) {
      throw new ApplyError(
        `refusing to add sandbox allowWrite for root/home: ${bad.join(', ')} (pass --force to override)`,
        3,
      );
    }
  }
  const wantsAdd = LISTS.some((l) => adds[l].length > 0) || SB.some((x) => sbAdd[x.label].length > 0);

  let raw = null;
  let doc;
  if (fs.existsSync(file)) {
    raw = fs.readFileSync(file, 'utf8');
    try {
      doc = JSON.parse(raw);
    } catch (e) {
      throw new ApplyError(`${file} is not valid JSON (${e.message}); refusing to touch it`, 1);
    }
    if (doc === null || typeof doc !== 'object' || Array.isArray(doc)) {
      throw new ApplyError(`${file} is not a JSON object; refusing to touch it`, 1);
    }
  } else {
    doc = {};
  }

  if (doc.permissions !== undefined && (doc.permissions === null || typeof doc.permissions !== 'object' || Array.isArray(doc.permissions))) {
    throw new ApplyError(`${file}: "permissions" is not an object; refusing to touch it`, 1);
  }
  for (const l of LISTS) {
    const v = doc.permissions?.[l];
    if (v !== undefined && !Array.isArray(v)) {
      throw new ApplyError(`${file}: permissions.${l} is not an array; refusing to touch it`, 1);
    }
  }

  // Validate sandbox shape.
  if (doc.sandbox !== undefined && (doc.sandbox === null || typeof doc.sandbox !== 'object' || Array.isArray(doc.sandbox))) {
    throw new ApplyError(`${file}: "sandbox" is not an object; refusing to touch it`, 1);
  }
  const sbCur = {};
  for (const x of SB) {
    let node = doc.sandbox;
    for (let i = 0; i < x.keys.length - 1 && node !== undefined; i++) {
      node = node[x.keys[i]];
      if (node !== undefined && (node === null || typeof node !== 'object' || Array.isArray(node))) {
        throw new ApplyError(`${file}: sandbox.${x.keys.slice(0, i + 1).join('.')} is not an object; refusing to touch it`, 1);
      }
    }
    const v = node?.[x.keys[x.keys.length - 1]];
    if (v !== undefined && !Array.isArray(v)) {
      throw new ApplyError(`${file}: sandbox.${x.keys.join('.')} is not an array; refusing to touch it`, 1);
    }
    sbCur[x.label] = Array.isArray(v) ? v.slice() : null;
  }

  const before = counts(doc.permissions);
  const added = emptyLists();
  const removed = emptyLists();
  const skipped = [];

  // Work on copies so doc is only mutated when something changes.
  const working = {};
  for (const l of LISTS) working[l] = Array.isArray(doc.permissions?.[l]) ? doc.permissions[l].slice() : null;

  for (const l of LISTS) {
    for (const r of removes[l]) {
      const i = working[l] ? working[l].indexOf(r) : -1;
      if (i === -1) skipped.push({ rule: r, list: l, reason: 'not present' });
      else {
        working[l].splice(i, 1);
        removed[l].push(r);
      }
    }
    for (const r of adds[l]) {
      if (working[l] && working[l].includes(r)) {
        skipped.push({ rule: r, list: l, reason: 'already present' });
      } else {
        if (!working[l]) working[l] = [];
        working[l].push(r);
        added[l].push(r);
      }
    }
  }

  const sbAdded = {};
  const sbRemoved = {};
  for (const x of SB) {
    const lab = x.label;
    const name = `sandbox.${x.keys.join('.')}`;
    sbAdded[lab] = [];
    sbRemoved[lab] = [];
    for (const r of sbRem[lab]) {
      const i = sbCur[lab] ? sbCur[lab].indexOf(r) : -1;
      if (i === -1) skipped.push({ rule: r, list: name, reason: 'not present' });
      else {
        sbCur[lab].splice(i, 1);
        sbRemoved[lab].push(r);
      }
    }
    for (const r of sbAdd[lab]) {
      if (sbCur[lab] && sbCur[lab].includes(r)) skipped.push({ rule: r, list: name, reason: 'already present' });
      else {
        if (!sbCur[lab]) sbCur[lab] = [];
        sbCur[lab].push(r);
        sbAdded[lab].push(r);
      }
    }
  }
  const permChanged = LISTS.some((l) => added[l].length || removed[l].length);
  const sbChanged = SB.some((x) => sbAdded[x.label].length || sbRemoved[x.label].length);
  const changed = permChanged || sbChanged;
  const afterPerms = {};
  for (const l of LISTS) afterPerms[l] = working[l] ? working[l].length : 0;

  const result = {
    file,
    backup: null,
    added,
    removed,
    skipped,
    before,
    after: afterPerms,
    sandbox: { added: sbAdded, removed: sbRemoved },
    changed,
    dryRun: !!dryRun,
  };
  if (!changed) return result;

  // Missing file with no additions: nothing to create.
  if (raw === null && !wantsAdd) return result;

  if (permChanged) {
    if (doc.permissions === undefined) doc.permissions = {};
    for (const l of LISTS) {
      if (working[l] !== null && (doc.permissions[l] !== undefined || working[l].length > 0 || added[l].length)) {
        doc.permissions[l] = working[l];
      }
    }
  }
  for (const x of SB) {
    const lab = x.label;
    if (!sbAdded[lab].length && !sbRemoved[lab].length) continue;
    // create nested objects only when adding; removals only touch existing arrays
    let node = doc;
    const parents = ['sandbox', ...x.keys.slice(0, -1)];
    for (const k of parents) {
      if (node[k] === undefined) node[k] = {};
      node = node[k];
    }
    node[x.keys[x.keys.length - 1]] = sbCur[lab];
  }
  const out = JSON.stringify(doc, null, 2) + '\n';

  if (dryRun) return result;

  const dir = path.dirname(file);
  fs.mkdirSync(dir, { recursive: true });

  let backup = null;
  if (raw !== null) {
    const stamp = new Date(now).toISOString().replace(/:/g, '');
    backup = `${file}.bak-${stamp}`;
    fs.copyFileSync(file, backup);
    result.backup = backup;
  }

  const tmp = path.join(dir, `.${path.basename(file)}.tmp-${process.pid}-${Date.now()}`);
  const mode = raw !== null ? fs.statSync(file).mode & 0o777 : 0o644;
  try {
    fs.writeFileSync(tmp, out, { mode });
    fs.renameSync(tmp, file);
  } catch (e) {
    try { fs.unlinkSync(tmp); } catch { /* ignore */ }
    throw new ApplyError(`write failed: ${e.message}`, 1);
  }

  try {
    JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (e) {
    if (backup) fs.copyFileSync(backup, file);
    else fs.unlinkSync(file);
    throw new ApplyError(`read-back of ${file} failed (${e.message}); original restored`, 1);
  }
  return result;
}

const FLAGS = {
  '--add-allow': 'addAllow',
  '--remove-allow': 'removeAllow',
  '--add-deny': 'addDeny',
  '--remove-deny': 'removeDeny',
  '--add-ask': 'addAsk',
  '--remove-ask': 'removeAsk',
  '--add-sandbox-allow-write': 'addSandboxAllowWrite',
  '--remove-sandbox-allow-write': 'removeSandboxAllowWrite',
  '--add-sandbox-excluded-command': 'addSandboxExcludedCommand',
  '--remove-sandbox-excluded-command': 'removeSandboxExcludedCommand',
};

export function parseArgs(argv) {
  const opts = { file: null, changes: {}, dryRun: false, json: false, force: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--dry-run') opts.dryRun = true;
    else if (a === '--json') opts.json = true;
    else if (a === '--force') opts.force = true;
    else if (a === '--file' || FLAGS[a]) {
      const v = argv[++i];
      if (v === undefined) throw new ApplyError(`${a} needs a value`, 2);
      if (a === '--file') opts.file = v;
      else (opts.changes[FLAGS[a]] ||= []).push(v);
    } else throw new ApplyError(`unknown argument: ${a}`, 2);
  }
  if (!opts.file) throw new ApplyError('usage: perm-apply --file <path> [--add-allow R]… [--remove-allow R]… [--add-deny R]… [--remove-deny R]… [--add-ask R]… [--remove-ask R]… [--add-sandbox-allow-write P]… [--remove-sandbox-allow-write P]… [--add-sandbox-excluded-command C]… [--remove-sandbox-excluded-command C]… [--dry-run] [--json] [--force]', 2);
  return opts;
}

export function formatText(r) {
  const lines = [];
  for (const l of LISTS) for (const x of r.added[l]) lines.push(`+ ${l}: ${x}`);
  for (const l of LISTS) for (const x of r.removed[l]) lines.push(`- ${l}: ${x}`);
  for (const x of SB) for (const v of r.sandbox.added[x.label]) lines.push(`+ sandbox.${x.keys.join('.')}: ${v}`);
  for (const x of SB) for (const v of r.sandbox.removed[x.label]) lines.push(`- sandbox.${x.keys.join('.')}: ${v}`);
  for (const s of r.skipped) lines.push(`skipped ${s.list}: ${s.rule} (${s.reason})`);
  if (!r.changed) lines.push('no changes');
  if (r.dryRun) lines.push('dry-run: nothing written');
  else if (r.backup) lines.push(`backup: ${r.backup}`);
  return lines.join('\n');
}

export function main(argv) {
  try {
    const opts = parseArgs(argv);
    const r = applyChanges({
      file: path.resolve(opts.file),
      changes: opts.changes,
      dryRun: opts.dryRun,
      force: opts.force,
    });
    process.stdout.write((opts.json ? JSON.stringify(r, null, 2) : formatText(r)) + '\n');
    return 0;
  } catch (e) {
    if (e instanceof ApplyError) {
      process.stderr.write(`perm-apply: ${e.message}\n`);
      return e.exitCode;
    }
    process.stderr.write(`perm-apply: unexpected error: ${e.message}\n`);
    return 1;
  }
}
