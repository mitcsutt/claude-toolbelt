// perm-scan engine: settings scopes + checks + transcript friction, with a hard
// output-size cap. Read-only: this module never writes anything.
// Spec: docs/superpowers/specs/2026-10-09-permissions-v3.md section 5.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { loadScopes } from "./scopes.mjs";
import { runChecks, decide } from "./checks.mjs";
import { splitCompound, stripWrappers } from "./rule-matcher.mjs";
import { scanTranscripts } from "./transcripts.mjs";
import { SIGNATURES } from "./signatures.mjs";

export const MAX_BYTES = 8192;
export const HINT = "For new allow rules use /fewer-permission-prompts.";
const VERSION = "3.1";
const SEV_ORDER = ["high", "med", "low", "dead", "info"];
const PROJECT_SCOPES = new Set(["projectShared", "projectLocal"]);
const LEGACY_LOGS = ["permission-log.jsonl", "prompt-log.jsonl", "sandbox-denials.jsonl"];
const DOC_PERMS = "https://code.claude.com/docs/en/permissions";
const DOC_SETTINGS = "https://code.claude.com/docs/en/settings";
const D9_MIN_HITS = 5;
const D9_SHARE = 0.6;
const MAX_S1 = 5;
export const NO_RULE_NOTE = "rule no longer present";

// ---- args -------------------------------------------------------------------

export function parseArgs(argv) {
  const o = { json: false, noCache: false, settingsOnly: false, days: 30, cwd: null, allProjects: null, dataDir: null, check: null, error: null };
  const a = [...argv];
  if (a[0] === "check") {
    o.check = [];
    a.shift();
  }
  const val = (name, i) => {
    if (i + 1 >= a.length) o.error = `${name} needs a value`;
    return a[i + 1];
  };
  for (let i = 0; i < a.length; i++) {
    const x = a[i];
    if (x === "--json") o.json = true;
    else if (x === "--settings-only") o.settingsOnly = true;
    else if (x === "--no-cache") o.noCache = true;
    else if (x === "--cwd") o.cwd = val(x, i++);
    else if (x === "--all-projects") o.allProjects = val(x, i++);
    else if (x === "--data-dir") o.dataDir = val(x, i++);
    else if (x === "--days") o.days = Number(val(x, i++));
    else if (o.check && !x.startsWith("--")) o.check.push(x);
    else if (x === "--help" || x === "-h") o.error = "help";
    else o.error = `unknown argument ${x}`;
  }
  if (!(o.days > 0)) o.error = o.error ?? "--days must be a positive number";
  if (o.check && o.check.length === 0 && !o.error) o.error = "check needs at least one command";
  return o;
}

// ---- data dir ---------------------------------------------------------------

// Plugin env vars are NOT set for commands run via the Bash tool
// (https://code.claude.com/docs/en/skills), so derive the data dir from the install path.
export function resolveDataDir({ flag, env, home, selfFile }) {
  if (flag) return flag;
  if (env.CLAUDE_PLUGIN_DATA) return env.CLAUDE_PLUGIN_DATA;
  try {
    const real = fs.realpathSync(selfFile);
    const root = path.join(fs.realpathSync(home), ".claude", "plugins", "cache") + path.sep;
    if (real.startsWith(root)) {
      const parts = real.slice(root.length).split(path.sep); // <marketplace>/<plugin>/<version>/lib/scan.mjs
      if (parts.length >= 5) return path.join(home, ".claude", "plugins", "data", `${parts[1]}-${parts[0]}`);
    }
  } catch {
    /* fall through */
  }
  return null;
}

function listDir(dir) {
  try {
    return fs.readdirSync(dir);
  } catch {
    return null;
  }
}

const shq = (p) => (/^[\w@%+=:,./~-]+$/.test(p) ? p : `'${p.replace(/'/g, `'\\''`)}'`);

// ---- F5: attributing deny-rule hits -----------------------------------------

const FS_HEADS = new Set(["rm", "rmdir", "mv", "cp", "mkdir", "touch", "chmod", "chown", "ln", "cat", "ls", "find", "sed", "tee", "dd", "truncate", "shred", "unlink", "head", "tail", "tar", "unzip", "zip"]);

function projectRoot(cwd) {
  let dir = path.resolve(cwd);
  for (let i = 0; i < 40; i++) {
    if (fs.existsSync(path.join(dir, ".git"))) return dir;
    const up = path.dirname(dir);
    if (up === dir) break;
    dir = up;
  }
  return path.resolve(cwd);
}

function tempRoots() {
  const roots = new Set(["/tmp", "/private/tmp", "/var/folders", "/private/var/folders"]);
  for (const t of [os.tmpdir(), process.env.TMPDIR]) {
    if (!t) continue;
    roots.add(path.resolve(t).replace(/\/+$/, ""));
    try {
      roots.add(fs.realpathSync(t));
    } catch {
      /* ignore */
    }
  }
  return [...roots];
}

const within = (p, root) => p === root || p.startsWith(root.endsWith(path.sep) ? root : root + path.sep);

// "temp" | "project" | "other" for the path arguments of one command segment.
export function classifyTargets(segment, { project, home, recCwd, temps = tempRoots() }) {
  const words = String(segment).split(/\s+/).filter(Boolean).map((w) => w.replace(/^["']|["']$/g, ""));
  const head = words[0];
  const base = recCwd && path.isAbsolute(recCwd) ? recCwd : project;
  const kinds = new Set();
  for (const w of words.slice(1)) {
    if (w.startsWith("-") || /^[|&;<>]/.test(w) || w.includes("://")) continue;
    let abs = null;
    if (/^\$\{?TMPDIR\}?(\/|$)/.test(w)) {
      kinds.add("temp");
      continue;
    }
    if (w.startsWith("$") || w.includes("$(") || w.includes("`")) {
      kinds.add("other"); // unknown expansion: be conservative
      continue;
    }
    if (w === "~" || w.startsWith("~/")) abs = path.join(home, w.slice(1));
    else if (w.startsWith("/")) abs = path.normalize(w.replace(/\/\*+$/, "/"));
    else if (w.startsWith("./") || w.startsWith("../") || w === "." || w === "..") abs = path.resolve(base, w);
    else if (FS_HEADS.has(head) && /^[\w@%+=,.-]/.test(w)) abs = path.resolve(base, w);
    if (!abs) continue;
    if (temps.some((t) => within(abs, t))) kinds.add("temp");
    else if (within(abs, project)) kinds.add("project");
    else kinds.add("other");
  }
  if (kinds.size === 0 || kinds.has("other")) return "other";
  return kinds.has("temp") ? "temp" : "project";
}

// Callback for scanTranscripts: which CURRENT rule fires for this denied call, and where it pointed.
export function makeAttribute(eff, { cwd, home }) {
  const project = projectRoot(cwd);
  const temps = tempRoots();
  return (tool, detail, recCwd) => {
    if (tool !== "Bash") {
      const d = decide({ tool, detail }, eff, { cwd: recCwd || cwd, home });
      return { decision: d.decision, rule: d.rule, scope: d.scope };
    }
    const d = decide(detail, eff, { cwd: recCwd || cwd, home });
    if (d.decision !== "deny") return { decision: d.decision, rule: d.rule, scope: d.scope };
    // find the segment that the rule fires on and look at its path arguments
    const segs = splitCompound(detail).map(stripWrappers).filter(Boolean);
    let target = null;
    for (const seg of segs) {
      const sd = decide(seg, eff, { cwd: recCwd || cwd, home });
      if (sd.decision === "deny" && sd.rule === d.rule) {
        target = seg;
        break;
      }
    }
    const tag = classifyTargets(target ?? segs.join(" "), { project, home, recCwd, temps });
    return { decision: d.decision, rule: d.rule, scope: d.scope, tag };
  };
}

// D9: a deny rule whose exact hits are mostly routine temp/project commands.
export function d9Findings(denials, scopes) {
  const out = [];
  for (const g of denials) {
    if (g.kind !== "deny-rule" || !g.rule || g.count < D9_MIN_HITS) continue;
    const temp = g.tags?.temp ?? 0;
    const proj = g.tags?.project ?? 0;
    if ((temp + proj) / g.count < D9_SHARE) continue;
    const parts = [];
    if (temp) parts.push(`${temp} under /tmp`);
    if (proj) parts.push(`${proj} inside the project`);
    out.push({
      id: "D9",
      severity: "med",
      scope: g.scope ?? "user",
      file: scopes.find((s) => s.name === g.scope)?.file ?? null,
      list: "deny",
      rule: g.rule,
      why: `Blocks routine commands: ${temp + proj} of ${g.count} hits were ${parts.join(" and ")}.`,
      doc: DOC_PERMS,
    });
  }
  return out;
}

// S1: sandbox denials seen in transcripts -> a suggested sandbox setting (only when sandbox.enabled).
export function s1Findings(sandboxDenials, userFile) {
  const fixOf = new Map(SIGNATURES.map((s) => [s.id, s.fix]));
  const sugg = new Map();
  for (const d of sandboxDenials) {
    const fix = fixOf.get(d.signature);
    let key = null;
    let value = null;
    if (fix === "claude-sandbox-allowwrite" && d.path) {
      key = "filesystem.allowWrite";
      let p = d.path;
      const git = p.indexOf("/.git/");
      const cl = /^(.*\/\.claude\/(?:agents|skills|worktrees))(?:\/|$)/.exec(p);
      if (git >= 0) p = p.slice(0, git + 5);
      else if (cl) p = cl[1];
      else p = path.dirname(p);
      if (p.split("/").filter(Boolean).length < 2) continue; // never suggest "/" or a top-level dir
      value = p;
    } else if (fix === "dangerouslyDisableSandbox" && d.head) {
      key = "excludedCommands";
      value = d.head;
    }
    if (!key) continue;
    const k = key + "\u0000" + value;
    const e = sugg.get(k) ?? { key, value, count: 0, sigs: new Set() };
    e.count += d.count;
    e.sigs.add(d.signature);
    sugg.set(k, e);
  }
  return [...sugg.values()]
    .sort((a, b) => b.count - a.count)
    .slice(0, MAX_S1)
    .map((e) => ({
      id: "S1",
      severity: "low",
      scope: "user",
      file: userFile,
      rule: `sandbox.${e.key}: ${e.value}`,
      why: `${e.count} sandbox denial${e.count === 1 ? "" : "s"} (${[...e.sigs].slice(0, 2).join(", ")}); ${e.key === "excludedCommands" ? "exclude this command from the sandbox" : "allow writes here"} to stop them.`,
      doc: DOC_SETTINGS,
      sandbox: { key: e.key, value: e.value },
    }));
}

// ---- legacy -----------------------------------------------------------------

const MB = (b) => (b / 1048576).toFixed(1);

function legacyFindings(home, dataDir, now) {
  const out = [];
  const claudeDir = path.join(home, ".claude");
  const names = (listDir(claudeDir) ?? []).filter((n) => LEGACY_LOGS.some((l) => n === l || n.startsWith(l + ".")));
  let bytes = 0;
  for (const n of names) {
    try {
      bytes += fs.statSync(path.join(claudeDir, n)).size;
    } catch {
      /* ignore */
    }
  }
  if (names.length) {
    out.push({ id: "L1", severity: "info", scope: "user", file: claudeDir, rule: "~/.claude/*.jsonl", why: `${names.length} 2.x log file(s), ${MB(bytes)} MB still in ~/.claude; archived at next session start`, doc: DOC_SETTINGS });
  }
  if (dataDir) {
    try {
      const archive = path.join(dataDir, "legacy-v2");
      const moved = JSON.parse(fs.readFileSync(path.join(archive, "MOVED.json"), "utf8"));
      const age = Math.floor((now - Date.parse(moved.at)) / 86400000);
      const mb = MB((moved.moved ?? []).reduce((s, m) => s + (m.bytes || 0), 0));
      const command = `rm -r ${shq(archive)}`;
      out.push({ id: "L1", severity: "info", scope: "user", file: archive, rule: "legacy-v2/", why: `2.x archive ${mb} MB, ${age} days old; auto-deleted after 30 days. Delete now: ${command}`, command, doc: DOC_SETTINGS });
    } catch {
      /* no archive */
    }
  }
  return out;
}

// L3 delete commands are rebuilt from `file` and never truncated; *-inline dirs are not orphans.
export function fixLegacy(findings) {
  const out = [];
  for (const f of findings) {
    if (f.id === "L3") {
      if (String(f.rule ?? "").endsWith("-inline") || String(f.file ?? "").endsWith("-inline")) continue;
      out.push(f.file ? { ...f, command: `rm -r ${shq(f.file)}` } : f);
      continue;
    }
    out.push(f);
  }
  return out;
}

// ---- projects ---------------------------------------------------------------

export function findProjects(root, maxDepth = 4) {
  const found = [];
  const walk = (dir, depth) => {
    const ents = (() => {
      try {
        return fs.readdirSync(dir, { withFileTypes: true });
      } catch {
        return [];
      }
    })();
    if (ents.some((e) => e.isDirectory() && e.name === ".claude")) {
      const cdir = path.join(dir, ".claude");
      if (["settings.json", "settings.local.json"].some((f) => fs.existsSync(path.join(cdir, f)))) found.push(dir);
    }
    if (depth >= maxDepth) return;
    for (const e of ents) {
      if (!e.isDirectory() || e.name === "node_modules" || e.name === ".git" || e.name.startsWith(".")) continue;
      walk(path.join(dir, e.name), depth + 1);
    }
  };
  walk(path.resolve(root), 0);
  return found;
}

// ---- report -----------------------------------------------------------------

const sevRank = (s) => SEV_ORDER.indexOf(s);
const sortFindings = (list) => [...list].sort((a, b) => sevRank(a.severity) - sevRank(b.severity) || a.id.localeCompare(b.id));
const fkey = (f) => [f.id, f.scope, f.file ?? "", f.rule ?? ""].join("\u0000");

// Cut at a word boundary with an ellipsis; never mid-word.
export function clip(s, max) {
  s = String(s ?? "");
  if (s.length <= max) return s;
  const cut = s.slice(0, max - 1);
  const sp = cut.lastIndexOf(" ");
  return (sp > max * 0.5 ? cut.slice(0, sp) : cut).replace(/[\s,;:.-]+$/, "") + "…";
}

const MAX_RULE = 200;
function slim(f, lvl = 0) {
  const o = { id: f.id, severity: f.severity, scope: f.scope };
  if (f.file) o.file = f.file;
  if (f.list) o.list = f.list;
  o.rule = clip(f.rule, MAX_RULE);
  if (f.coveredBy) o.coveredBy = clip(f.coveredBy, MAX_RULE);
  if (f.rules && lvl < 3) o.rules = f.rules.slice(0, lvl < 2 ? 6 : 2).map((r) => clip(r, MAX_RULE));
  o.why = f.id === "L3" || f.id === "L1" ? String(f.why ?? "") : clip(f.why, 140);
  if (f.doc && lvl < 4) o.doc = f.doc;
  if (f.count) o.count = f.count;
  if (f.command) o.command = f.command;
  if (f.sandbox) o.sandbox = f.sandbox;
  return o;
}

// D6 is noisy (a bare `Bash` rule "covers" every other Bash allow): one finding per covering rule.
// The group names the DEAD rules in `rule`/`rules` and the broader rule in `coveredBy`.
export function collapseD6(findings) {
  const out = [];
  const groups = new Map();
  for (const f of findings) {
    if (f.id !== "D6") {
      out.push(f);
      continue;
    }
    const m = /(?:broader allow|deny rule) (.+?) \(/.exec(f.why ?? "");
    const by = f.coveredBy ?? (m ? m[1] : null);
    const key = (by ?? "duplicate") + "\u0000" + f.scope + "\u0000" + (f.file ?? "");
    if (!groups.has(key)) groups.set(key, { by, items: [] });
    groups.get(key).items.push(f);
  }
  for (const { by, items } of groups.values()) {
    if (items.length <= 2) {
      out.push(...items.map((i) => (by && !i.coveredBy ? { ...i, coveredBy: by } : i)));
      continue;
    }
    const first = items[0];
    out.push({
      id: "D6",
      severity: "dead",
      scope: first.scope,
      file: first.file,
      list: first.list,
      rule: first.rule,
      rules: items.map((i) => i.rule),
      ...(by ? { coveredBy: by } : {}),
      count: items.length,
      why: `${items.length} ${by ? "rules are shadowed by a broader rule" : "duplicate rules"}; remove them (rules lists the first ones).`,
      doc: first.doc,
    });
  }
  return out;
}

const LEVELS = [
  { per: 12, fr: 8, proj: 10, samples: 2 },
  { per: 6, fr: 5, proj: 6, samples: 1 },
  { per: 4, fr: 3, proj: 4, samples: 0 },
  { per: 2, fr: 2, proj: 2, samples: 0 },
  { per: 1, fr: 1, proj: 1, samples: 0 },
  { per: 1, fr: 0, proj: 0, samples: 0 },
];

function limitFindings(list, per, lvl) {
  const seen = new Map();
  const kept = [];
  for (const f of sortFindings(list)) {
    const k = f.id + "\u0000" + f.scope;
    const n = (seen.get(k) ?? 0) + 1;
    seen.set(k, n);
    if (n <= per) kept.push(slim(f, lvl));
  }
  return kept;
}

function frictionView(fr, n, samples) {
  if (!fr) return null;
  const d = (s) => (s ? s.slice(0, 10) : "");
  const top = (arr, map) => arr.slice(0, n).map(map);
  const view = {
    modes: fr.modes,
    classifierBlocks: top(fr.classifierBlocks, (b) => ({ reason: b.reason, count: b.count, last: d(b.last), heads: b.heads })),
    rejected: top(fr.rejected, (r) => ({ tool: r.tool, head: r.head, count: r.count })),
    headlessDenied: top(fr.headlessDenied, (r) => ({ tool: r.tool, head: r.head, count: r.count })),
    denials: top(fr.denials, (g) => {
      const o = { kind: g.kind };
      if (g.rule !== undefined) o.rule = g.rule;
      if (g.hook) o.hook = g.hook;
      if (g.check) o.check = g.check;
      o.count = g.count;
      o.last = d(g.last);
      if (g.scope) o.scope = g.scope;
      if (g.note) o.note = g.note;
      if (g.tool) o.tool = g.tool;
      if (samples > 0 && g.samples?.length) o.samples = g.samples.slice(0, samples).map((x) => clip(x, 100));
      if (samples > 0 && g.modes && Object.keys(g.modes).length) o.modes = g.modes;
      return o;
    }),
  };
  if (fr.sandboxDenials?.length) view.sandboxDenials = top(fr.sandboxDenials, (s) => ({ signature: s.signature, count: s.count, ...(s.path ? { path: s.path } : {}) }));
  return view;
}

export function render(full, lvl) {
  const L = LEVELS[Math.min(lvl, LEVELS.length - 1)];
  const out = {
    version: VERSION,
    cwd: full.cwd,
    scopes: full.scopes,
    findings: limitFindings(collapseD6(full.findings), L.per, lvl),
    friction: frictionView(full.friction, L.fr, L.samples),
  };
  if (full.window) out.window = full.window;
  if (full.projects) {
    out.projects = full.projects.slice(0, L.proj).map((pr) => ({ dir: pr.dir, findings: limitFindings(collapseD6(pr.findings), Math.max(1, Math.min(L.per, 4)), lvl) }));
  }
  out.truncated = false;
  out.ms = full.ms;
  out.hint = HINT;
  return out;
}

// What the final report omits, relative to the full data. false when nothing was dropped.
function computeTruncated(full, r) {
  const t = {};
  const keptKeys = new Set(r.findings.map(fkey));
  const dropped = collapseD6(full.findings).filter((f) => !keptKeys.has(fkey(f)));
  if (dropped.length) {
    t.findings = dropped.length;
    for (const f of dropped) if (f.list) t[f.list] = (t[f.list] ?? 0) + 1;
  }
  if (full.projects) {
    const keptP = new Map((r.projects ?? []).map((p) => [p.dir, p]));
    const lost = full.projects.filter((p) => !keptP.has(p.dir)).length;
    if (lost) t.projects = lost;
    let pf = 0;
    for (const p of full.projects) {
      const k = keptP.get(p.dir);
      if (k) pf += Math.max(0, collapseD6(p.findings).length - k.findings.length);
    }
    if (pf) t.projectFindings = pf;
  }
  if (full.friction && r.friction) {
    for (const [fk, rk] of [["classifierBlocks", "classifierBlocks"], ["rejected", "rejected"], ["headlessDenied", "headlessDenied"], ["denials", "denials"]]) {
      const n = (full.friction[fk]?.length ?? 0) - (r.friction[rk]?.length ?? 0);
      if (n > 0) t[rk === "denials" ? "denialGroups" : rk] = n;
    }
  }
  return Object.keys(t).length ? t : false;
}

const size = (r) => Buffer.byteLength(JSON.stringify(r));

export function capReport(full, max = MAX_BYTES) {
  let last = null;
  for (let lvl = 0; lvl < LEVELS.length; lvl++) {
    const r = render(full, lvl);
    r.truncated = computeTruncated(full, r);
    last = r;
    if (size(r) <= max) return r;
  }
  // Last resort: drop lowest severities until it fits, then friction detail.
  const refresh = () => {
    last.truncated = computeTruncated(full, last) || { findings: 0 };
  };
  while (size(last) > max && last.findings.length > 0) {
    last.findings.pop();
    refresh();
  }
  while (size(last) > max && last.projects?.length) {
    last.projects.pop();
    refresh();
  }
  while (size(last) > max && last.friction) {
    const fr = last.friction;
    const k = ["denials", "classifierBlocks", "rejected", "headlessDenied", "sandboxDenials"].find((x) => fr[x]?.length);
    if (!k) {
      last.friction = null;
      last.truncated = { ...(last.truncated || {}), friction: true };
      break;
    }
    fr[k].pop();
    refresh();
  }
  while (size(last) > max && last.scopes.length) {
    last.scopes.pop();
    last.truncated = { ...(last.truncated || {}), scopes: true };
  }
  return last;
}

export async function buildReport(opts) {
  const t0 = Date.now();
  const home = opts.home ?? os.homedir();
  const cwd = path.resolve(opts.cwd ?? process.cwd());
  const now = opts.now ?? Date.now();
  const env = opts.env ?? process.env;
  const dataDir = resolveDataDir({ flag: opts.dataDir, env, home, selfFile: fileURLToPath(import.meta.url) });
  const loaded = loadScopes({ cwd, home });
  const eff = loaded.effective;

  let transcripts = null;
  if (!opts.settingsOnly) {
    transcripts = await scanTranscripts({
      projectsDir: opts.projectsDir ?? path.join(home, ".claude", "projects"),
      days: opts.days ?? 30,
      sandboxEnabled: eff.sandbox.enabled === true,
      cacheDir: dataDir && !opts.noCache ? path.join(dataDir, "transcript-cache") : null,
      attribute: makeAttribute(eff, { cwd, home }),
    });
  }
  // A dir holding a 3.x schema marker is a live install of this plugin (any marketplace id),
  // never an orphan — even when --data-dir points elsewhere.
  const dataRoot = path.join(home, ".claude", "plugins", "data");
  const dataNames = listDir(dataRoot)?.filter((n) => !fs.existsSync(path.join(dataRoot, n, "schema")));
  let findings = runChecks(loaded, {
    seenTools: transcripts ? Object.keys(transcripts.toolCounts) : undefined,
    home,
    pluginDataDirs: dataNames ?? undefined,
    selfDataDir: dataDir ? path.basename(dataDir) : undefined,
  });
  findings.push(...legacyFindings(home, dataDir, now));
  findings = fixLegacy(findings);

  const userFile = loaded.scopes.find((s) => s.name === "user")?.file ?? path.join(home, ".claude", "settings.json");
  let friction = null;
  if (transcripts) {
    friction = {
      modes: transcripts.modes,
      classifierBlocks: transcripts.classifierBlocks,
      rejected: transcripts.rejected,
      headlessDenied: transcripts.headlessDenied,
      denials: transcripts.denials,
    };
    findings.push(...d9Findings(transcripts.denials, loaded.scopes));
    if (eff.sandbox.enabled === true && transcripts.sandboxDenials.length) {
      friction.sandboxDenials = transcripts.sandboxDenials;
      findings.push(...s1Findings(transcripts.sandboxDenials, userFile));
    }
  }

  let projects = null;
  if (opts.allProjects) {
    projects = [];
    for (const dir of findProjects(opts.allProjects)) {
      if (dir === home) continue;
      const l = loadScopes({ cwd: dir, home });
      const fl = fixLegacy(runChecks(l, { home })).filter((x) => PROJECT_SCOPES.has(x.scope));
      projects.push({ dir, findings: fl });
    }
  }

  const full = {
    cwd,
    scopes: loaded.scopes.map((s) => {
      const c = (k) => (s.data && s.data.permissions && Array.isArray(s.data.permissions[k]) ? s.data.permissions[k].length : 0);
      return { name: s.name, file: s.file, exists: s.exists, counts: { allow: c("allow"), deny: c("deny"), ask: c("ask") }, ...(s.error ? { error: String(s.error).slice(0, 80) } : {}) };
    }),
    findings,
    friction,
    projects,
    window: transcripts ? { days: transcripts.window.days, files: transcripts.window.files, oldest: transcripts.window.oldestTimestamp?.slice(0, 10) ?? null, ...(transcripts.skippedLongLines ? { skippedLongLines: transcripts.skippedLongLines } : {}) } : undefined,
    ms: 0,
  };
  full.ms = Date.now() - t0;
  return full;
}

// ---- text -------------------------------------------------------------------

const LEGEND = "F1 auto-mode classifier blocked | F2 you rejected | F3 headless, not granted | F5 permission denied (deny rule / hook / built-in)";

export function renderText(r, maxLines = 60) {
  const lines = [];
  const home = os.homedir();
  const short = (p) => (p ? p.replace(home, "~") : "(managed: none)");
  const rule = (s) => clip(s, 90);
  lines.push(`perm-scan  cwd=${short(r.cwd)}  ${r.ms} ms${r.window ? `  transcripts: ${r.window.files} files / ${r.window.days}d` : "  (settings only)"}`);
  for (const s of r.scopes) lines.push(`  ${s.name.padEnd(13)} ${s.exists ? `allow ${s.counts.allow} / ask ${s.counts.ask} / deny ${s.counts.deny}` : "absent"}  ${short(s.file)}${s.error ? "  ERROR " + s.error : ""}`);
  const body = [];
  const fline = (f) => `${f.severity.toUpperCase().padEnd(4)} ${f.id} ${f.scope} ${rule(f.rule)}${f.coveredBy ? ` (covered by ${rule(f.coveredBy)})` : ""}${f.count ? ` x${f.count}` : ""} — ${f.why}`;
  for (const f of r.findings) body.push(fline(f));
  if (r.findings.length === 0) body.push("no findings");
  const fr = r.friction;
  if (fr) {
    body.push(`friction (${LEGEND}):`);
    for (const b of fr.classifierBlocks) body.push(`  F1 classifier [${b.reason}] x${b.count} last ${b.last} (${b.heads.join(", ")})`);
    for (const x of fr.rejected) body.push(`  F2 rejected ${x.tool}${x.head ? " " + x.head : ""} x${x.count}`);
    for (const x of fr.headlessDenied) body.push(`  F3 headless not-granted ${x.tool}${x.head ? " " + x.head : ""} x${x.count}`);
    for (const g of fr.denials) {
      if (g.kind === "hook") body.push(`  F5 hook ${g.hook} blocked x${g.count} — change the hook or reword the command`);
      else if (g.kind === "builtin") body.push(`  F5 built-in ${g.check} x${g.count}`);
      else if (g.rule) body.push(`  F5 deny rule ${g.rule}${g.scope ? ` (${g.scope})` : ""} x${g.count}`);
      else body.push(`  F5 deny rule hit${g.tool ? ` (${g.tool})` : ""}, ${g.note ?? NO_RULE_NOTE} x${g.count}`);
    }
  }
  for (const p of r.projects ?? []) {
    body.push(`project ${short(p.dir)}:`);
    for (const f of p.findings) body.push(`  ${fline(f)}`);
    if (p.findings.length === 0) body.push("  clean");
  }
  if (fr) body.push(`tip: ${HINT.replace(/^For/, "for")}`);
  const room = maxLines - lines.length - 2;
  let hidden = 0;
  if (body.length > room) {
    hidden = body.length - (room - 1);
    lines.push(...body.slice(0, room - 1), `... ${hidden} more lines (use --json)`);
  } else {
    lines.push(...body);
  }
  if (r.truncated) {
    const t = r.truncated;
    const parts = [];
    if (t.findings) parts.push(`${t.findings} findings`);
    if (t.denialGroups) parts.push(`${t.denialGroups} denial groups`);
    for (const k of ["classifierBlocks", "rejected", "headlessDenied"]) if (t[k]) parts.push(`${t[k]} ${k}`);
    if (t.projects) parts.push(`${t.projects} projects`);
    lines.push(`(output truncated: ${parts.join(", ") || "detail"})`);
  }
  return lines.join("\n");
}

// ---- check mode -------------------------------------------------------------

export function runCheck(cmds, { cwd, home }) {
  const loaded = loadScopes({ cwd: path.resolve(cwd ?? process.cwd()), home });
  const results = cmds.map((cmd) => {
    const d = decide(cmd, loaded.effective, { cwd: path.resolve(cwd ?? process.cwd()), home });
    const r = { cmd, decision: d.decision, rule: d.rule ?? null, scope: d.scope ?? null };
    if (d.reason) r.reason = d.reason;
    if (d.decision === "deny") r.bypassNote = "deny rules still apply under bypassPermissions";
    if (d.decision === "unmatched") r.note = "no rule: auto mode classifier decides (default mode prompts)";
    if (d.decision === "unsure") r.note = "complex command (substitution, subshell or eval): cannot tell; treat as needing approval";
    return r;
  });
  return { version: "3", mode: loaded.effective.defaultMode?.value ?? null, results };
}

export function renderCheckText(rep) {
  return rep.results
    .map((r) => `${r.decision.padEnd(9)} ${r.cmd}${r.rule ? `  [${r.rule} (${r.scope})]` : ""}${r.bypassNote ? `  — ${r.bypassNote}` : ""}`)
    .join("\n");
}

// ---- main -------------------------------------------------------------------

const USAGE = "usage: perm-scan [--cwd DIR] [--json] [--settings-only] [--days N] [--all-projects ROOT] [--data-dir DIR] [--no-cache]\n       perm-scan check [--json] [--cwd DIR] <cmd>...";

export async function main(argv, { env = process.env, out = (s) => process.stdout.write(s), err = (s) => process.stderr.write(s), home = os.homedir() } = {}) {
  const o = parseArgs(argv);
  if (o.error) {
    err(`perm-scan: ${o.error}\n${USAGE}\n`);
    return o.error === "help" ? 0 : 2;
  }
  try {
    if (o.check) {
      const rep = runCheck(o.check, { cwd: o.cwd, home });
      out((o.json ? JSON.stringify(rep) : renderCheckText(rep)) + "\n");
      return 0;
    }
    const full = await buildReport({ ...o, home, env });
    const rep = capReport(full);
    out((o.json ? JSON.stringify(rep) : renderText(rep)) + "\n");
    return 0;
  } catch (e) {
    err(`perm-scan: ${e && e.message}\n`);
    return 1;
  }
}
