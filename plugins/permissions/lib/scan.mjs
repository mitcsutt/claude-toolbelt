// perm-scan engine: settings scopes + checks + transcript friction, with a hard
// output-size cap. Read-only: this module never writes anything.
// Spec: docs/superpowers/specs/2026-10-09-permissions-v3.md section 5.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { loadScopes, computeEffective } from "./scopes.mjs";
import { runChecks, decide } from "./checks.mjs";
import { commandHead, splitCompound, stripWrappers } from "./rule-matcher.mjs";
import { scanTranscripts } from "./transcripts.mjs";

export const MAX_BYTES = 8192;
const SEV_ORDER = ["high", "med", "low", "dead", "info"];
const WRITE_VERB_TOOL = /(create|update|delete|post|send|comment|transition|run|execute|click|type|navigate|evaluate|fill|upload|write|edit|merge|push|javascript|script|eval|exec|shell|computer|batch)/i;
const PROJECT_SCOPES = new Set(["projectShared", "projectLocal"]);
// Shell builtins/assignments that show up as compound-command segments; allowing them is noise.
const NOISE_HEADS = new Set(["cd", "export", "set", "unset", "true", "false", "echo", "exit", "wait", "pwd", "for", "while", "until", "do", "done", "if", "then", "else", "elif", "fi", "case", "esac", "in", "function"]);
// Runners/launchers: a wildcard allow on these is arbitrary code execution, so never propose one.
const RUNNER_HEADS = new Set([
  "npx", "pnpx", "bunx", "uvx", "corepack", "sudo", "doas", "env", "ssh", "exec", "nohup", "setsid", "watch", "open",
  "tmux", "screen", "parallel", "osascript", "script", "expect", "xargs",
  "bash", "sh", "zsh", "fish", // a shell head runs whatever its argument says
]);
// Multi-word heads that are launchers (commandHead keeps the subcommand for these tools).
const RUNNER_PHRASES = new Set(["docker exec", "docker run", "kubectl exec", "kubectl run"]);
const RUNNER_REASON = "launcher or shell: can run arbitrary commands, so a wildcard allow is unsafe";
const CODE_RUNNERS = new Set(["python", "python3", "node", "ruby", "perl", "deno", "bun", "php", "bash", "sh", "zsh", "fish"]);
const MAX_DROPPED = 10;
const LEGACY_LOGS = ["permission-log.jsonl", "prompt-log.jsonl", "sandbox-denials.jsonl"];

// ---- args -------------------------------------------------------------------

export function parseArgs(argv) {
  const o = { json: false, noCache: false, settingsOnly: false, days: 30, minCalls: 20, cwd: null, allProjects: null, dataDir: null, check: null, error: null };
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
    else if (x === "--min-calls") o.minCalls = Number(val(x, i++));
    else if (o.check && !x.startsWith("--")) o.check.push(x);
    else if (x === "--help" || x === "-h") o.error = "help";
    else o.error = `unknown argument ${x}`;
  }
  if (!(o.days > 0)) o.error = o.error ?? "--days must be a positive number";
  if (!(o.minCalls >= 1)) o.error = o.error ?? "--min-calls must be >= 1";
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

// ---- F4 candidates ----------------------------------------------------------

function syntheticLoaded(rule, eff) {
  const mk = (name, data) => ({ name, file: "<synthetic>", exists: true, data, error: null });
  const scopes = [
    mk("user", {
      permissions: { allow: [rule], deny: eff.deny.map((e) => e.rule), ask: eff.ask.map((e) => e.rule) },
    }),
  ];
  return { scopes, effective: computeEffective(scopes) };
}

function tripsRisk(rule, eff, home) {
  const found = runChecks(syntheticLoaded(rule, eff), { home });
  return found.some((f) => f.id.startsWith("R") || f.id === "D7");
}

const tilde = (p, home) => (home && p && (p === home || p.startsWith(home + path.sep)) ? "~" + p.slice(home.length) : p);

export function buildCandidates({ bashCommands = [], toolCounts = {}, effective, minCalls = 20, home = "", cwd = null, classifierHeads = [], dropped = [] }) {
  const groups = new Map();
  const f1 = new Set();
  for (const h of classifierHeads) {
    f1.add(h);
    f1.add(String(h).split(" ")[0]);
  }
  const runnerReason = (head, seg) => {
    if (RUNNER_HEADS.has(head.split(" ")[0]) || RUNNER_PHRASES.has(head)) return RUNNER_REASON;
    if (head === "find" && /\s-(?:exec|execdir|delete)\b/.test(seg)) return "find -exec/-delete runs arbitrary commands";
    return null;
  };
  const noise = (h) => !h || NOISE_HEADS.has(h) || h.includes("=") || /[^\w.\/ -]/.test(h);
  for (const bc of bashCommands) {
    if (!bc || typeof bc.command !== "string") continue;
    // Rules match per compound segment, so attribute each unmatched segment to its own head.
    const segs = splitCompound(bc.command).map(stripWrappers).filter(Boolean);
    const single = segs.length === 1;
    for (const seg of segs) {
      const head = commandHead(seg);
      if (noise(head)) continue;
      let g = groups.get(head);
      if (!g) groups.set(head, (g = { head, total: 0, unmatched: 0, cmds: [], maxCwds: 0, blocked: null, cwdCalls: new Map() }));
      g.blocked = g.blocked ?? runnerReason(head, seg);
      g.total += bc.count;
      if (decide(seg, effective).decision === "unmatched") {
        g.unmatched += bc.count;
        g.cmds.push({ command: seg, count: bc.count, cwds: bc.cwds ?? 0, single, topCwd: bc.topCwd ?? null });
        g.maxCwds = Math.max(g.maxCwds, bc.cwds ?? 0);
        if (bc.topCwd) g.cwdCalls.set(bc.topCwd, (g.cwdCalls.get(bc.topCwd) || 0) + bc.count);
      }
    }
  }
  const topOf = (m) => {
    let best = null;
    let n = 0;
    for (const [k, v] of m) if (v > n) ((best = k), (n = v));
    return best;
  };
  const drop = (head, reason) => {
    if (dropped.length < MAX_DROPPED && !dropped.some((d) => d.head === head)) dropped.push({ head, reason });
  };
  const out = [];
  const ranked = [...groups.values()]
    .filter((g) => g.unmatched >= minCalls && g.unmatched * 2 > g.total)
    .sort((a, b) => b.unmatched - a.unmatched)
    .slice(0, 30);
  for (const g of ranked) {
    if (g.blocked) {
      drop(g.head, g.blocked);
      continue;
    }
    if (f1.has(g.head) || f1.has(g.head.split(" ")[0])) {
      drop(g.head, "auto-mode classifier already blocked this command in the same window");
      continue;
    }
    g.cmds.sort((a, b) => b.count - a.count);
    const top = g.cmds[0];
    let rule = null;
    let calls = g.unmatched;
    let cwds = g.maxCwds;
    let topCwd = topOf(g.cwdCalls);
    const simple = (c) => !/[\n|;&`$<>()]|\[REDACTED\]|\[UUID\]|…/.test(c);
    if (top.single && top.count / g.unmatched >= 0.8 && simple(top.command)) {
      rule = `Bash(${top.command})`;
      calls = top.count;
      cwds = top.cwds ?? 0;
      topCwd = top.topCwd ?? topCwd;
    } else if (/^[\w.\/-]+(?: [\w-]+)?$/.test(g.head)) {
      rule = `Bash(${g.head} *)`;
    }
    if (rule && CODE_RUNNERS.has(g.head.split(" ")[0])) rule = null; // exact script runs are as open-ended as wildcards
    if (!rule || tripsRisk(rule, effective, home)) continue;
    const scopeSuggestion = cwds >= 2 ? "user" : "projectShared";
    const c = { rule, calls, scopeSuggestion };
    if (scopeSuggestion === "projectShared" && topCwd) {
      c.project = tilde(topCwd, home);
      if (cwd && topCwd !== cwd && !topCwd.startsWith(cwd + path.sep)) c.otherProject = true;
    }
    out.push(c);
  }
  for (const [name, n] of Object.entries(toolCounts)) {
    if (!name.startsWith("mcp__") || n < minCalls || WRITE_VERB_TOOL.test(name)) continue;
    if (decide({ tool: name, detail: "" }, effective).decision !== "unmatched") continue;
    if (tripsRisk(name, effective, home)) continue;
    // transcripts give no per-tool cwd, so MCP candidates default to user scope
    out.push({ rule: name, calls: n, scopeSuggestion: "user" });
  }
  return out.sort((a, b) => b.calls - a.calls);
}

// F5: group deny-rule hits by the deny rule that fired (recomputed against the current effective
// rules from sampled commands), not by the command's first word.
export const NO_RULE = "(no current deny rule — removed or managed?)";
export function groupRuleDenied(ruleDenied = [], effective) {
  const groups = new Map();
  const add = (rule, scope, n, last, tool) => {
    const k = rule + "\u0000" + (scope ?? "");
    let g = groups.get(k);
    if (!g) groups.set(k, (g = { rule, scope: scope ?? null, count: 0, last: "", tools: [] }));
    g.count += n;
    if (last && last > g.last) g.last = last;
    if (!g.tools.includes(tool)) g.tools.push(tool);
  };
  for (const e of ruleDenied) {
    if (e.tool !== "Bash") {
      add(e.tool, null, e.count, e.last, e.tool);
      continue;
    }
    const samples = e.samples ?? [];
    const weights = e.sampleCounts ?? [];
    const total = weights.reduce((a, b) => a + b, 0);
    if (samples.length === 0 || total === 0) {
      add(NO_RULE, null, e.count, e.last, "Bash");
      continue;
    }
    // the sampled commands stand in for all hits of this entry, proportionally
    samples.forEach((cmd, i) => {
      const d = decide(cmd, effective);
      const share = (weights[i] * e.count) / total;
      if (d.decision === "deny") add(d.rule, d.scope, share, e.last, "Bash");
      else add(NO_RULE, null, share, e.last, "Bash");
    });
  }
  return [...groups.values()]
    .map((g) => ({ ...g, count: Math.round(g.count) }))
    .sort((a, b) => b.count - a.count || (a.last < b.last ? 1 : -1));
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
    out.push({ id: "L1", severity: "info", scope: "user", rule: "~/.claude/*.jsonl", why: `${names.length} 2.x log file(s), ${MB(bytes)} MB still in ~/.claude; archived at next session start` });
  }
  if (dataDir) {
    try {
      const moved = JSON.parse(fs.readFileSync(path.join(dataDir, "legacy-v2", "MOVED.json"), "utf8"));
      const age = Math.floor((now - Date.parse(moved.at)) / 86400000);
      const mb = MB((moved.moved ?? []).reduce((s, m) => s + (m.bytes || 0), 0));
      out.push({ id: "L1", severity: "info", scope: "user", rule: "legacy-v2/", why: `2.x archive ${mb} MB, ${age} days old; auto-deleted after 30 days` });
    } catch {
      /* no archive */
    }
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
const slim = (f) => ({ id: f.id, severity: f.severity, scope: f.scope, rule: String(f.rule ?? "").slice(0, 90), why: String(f.why ?? "").slice(0, 110), ...(f.count ? { count: f.count } : {}) });

// D6 is noisy (a bare `Bash` rule "covers" every other Bash allow): one finding per covering rule.
export function collapseD6(findings) {
  const out = [];
  const groups = new Map();
  for (const f of findings) {
    if (f.id !== "D6") {
      out.push(f);
      continue;
    }
    const m = /(?:broader allow|deny rule) (.+?) \(/.exec(f.why ?? "");
    const key = (m ? m[1] : "duplicate") + "\u0000" + f.scope;
    if (!groups.has(key)) groups.set(key, { by: m ? m[1] : null, items: [] });
    groups.get(key).items.push(f);
  }
  for (const { by, items } of groups.values()) {
    if (items.length <= 2) {
      out.push(...items);
      continue;
    }
    const first = items[0];
    out.push({
      id: "D6",
      severity: "dead",
      scope: first.scope,
      rule: by ?? "(duplicates)",
      count: items.length,
      why: `${by ? "covers/shadows" : "duplicated:"} ${items.length} rules, e.g. ${items.slice(0, 2).map((i) => i.rule).join("; ")}`,
    });
  }
  return out;
}

const LEVELS = [
  { per: 12, fr: 8, proj: 10 },
  { per: 6, fr: 5, proj: 6 },
  { per: 4, fr: 3, proj: 4 },
  { per: 2, fr: 2, proj: 2 },
  { per: 1, fr: 1, proj: 1 },
  { per: 1, fr: 0, proj: 0 },
];

function limitFindings(list, per) {
  const seen = new Map();
  const kept = [];
  const dropped = new Map();
  for (const f of sortFindings(list)) {
    const k = f.id + "\u0000" + f.scope;
    const n = (seen.get(k) ?? 0) + 1;
    seen.set(k, n);
    if (n <= per) kept.push(slim(f));
    else dropped.set(f.id, { sev: f.severity, n: (dropped.get(f.id)?.n ?? 0) + 1 });
  }
  for (const [id, d] of dropped) kept.push({ id, severity: d.sev, scope: "*", rule: `+${d.n} more`, why: "trimmed; rerun with --settings-only or fewer scopes" });
  return { list: sortFindings(kept), cut: dropped.size > 0 };
}

function frictionView(fr, n) {
  if (!fr) return { view: null, cut: false };
  let cut = false;
  const top = (arr, map) => {
    if (arr.length > n) cut = true;
    return arr.slice(0, n).map(map);
  };
  const d = (s) => (s ? s.slice(0, 10) : "");
  const view = {
    modes: fr.modes,
    classifierBlocks: top(fr.classifierBlocks, (b) => ({ reason: b.reason, count: b.count, last: d(b.last), heads: b.heads })),
    rejected: top(fr.rejected, (r) => ({ tool: r.tool, head: r.head, count: r.count })),
    headlessDenied: top(fr.headlessDenied, (r) => ({ tool: r.tool, count: r.count })),
    ruleDenied: top(fr.ruleDenied, (r) => ({ rule: r.rule, scope: r.scope, count: r.count, last: d(r.last), tools: r.tools })),
    candidates: top(fr.candidates, (c) => c),
    candidatesDropped: (fr.candidatesDropped ?? []).slice(0, Math.max(0, n)),
  };
  return { view, cut };
}

export function render(full, lvl) {
  const L = LEVELS[Math.min(lvl, LEVELS.length - 1)];
  let truncated = false;
  const f = limitFindings(collapseD6(full.findings), L.per);
  const fr = frictionView(full.friction, L.fr);
  const out = {
    version: "3",
    cwd: full.cwd,
    scopes: full.scopes,
    findings: f.list,
    friction: fr.view,
  };
  if (full.window) out.window = full.window;
  if (full.projects) {
    const p = full.projects.slice(0, L.proj).map((pr) => {
      const lf = limitFindings(collapseD6(pr.findings), Math.max(1, Math.min(L.per, 4)));
      if (lf.cut || lf.list.length < pr.findings.length) truncated = true;
      return { dir: pr.dir, findings: lf.list };
    });
    if (full.projects.length > p.length) truncated = true;
    out.projects = p;
  }
  // D6 collapse is lossless-ish by design; only counted as truncation when something was dropped.
  if (f.cut || fr.cut) truncated = true;
  out.truncated = truncated;
  out.ms = full.ms;
  return out;
}

export function capReport(full, max = MAX_BYTES) {
  let last = null;
  for (let lvl = 0; lvl < LEVELS.length; lvl++) {
    const r = render(full, lvl);
    if (lvl > 0) r.truncated = true;
    last = r;
    if (Buffer.byteLength(JSON.stringify(r)) <= max) return r;
  }
  // Last resort: drop lowest severities until it fits.
  last.truncated = true;
  while (Buffer.byteLength(JSON.stringify(last)) > max && last.findings.length > 0) last.findings.pop();
  while (Buffer.byteLength(JSON.stringify(last)) > max && last.projects?.length) last.projects.pop();
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
    });
  }
  const dataNames = listDir(path.join(home, ".claude", "plugins", "data"));
  const findings = runChecks(loaded, {
    seenTools: transcripts ? Object.keys(transcripts.toolCounts) : undefined,
    home,
    pluginDataDirs: dataNames ?? undefined,
    selfDataDir: dataDir ? path.basename(dataDir) : undefined,
  });
  findings.push(...legacyFindings(home, dataDir, now));

  let friction = null;
  if (transcripts) {
    const candidatesDropped = [];
    const candidates = buildCandidates({
      bashCommands: transcripts.bashCommands,
      toolCounts: transcripts.toolCounts,
      effective: eff,
      minCalls: opts.minCalls ?? 20,
      home,
      cwd,
      classifierHeads: transcripts.classifierBlocks.flatMap((b) => b.heads),
      dropped: candidatesDropped,
    });
    friction = {
      modes: transcripts.modes,
      classifierBlocks: transcripts.classifierBlocks,
      rejected: transcripts.rejected,
      headlessDenied: transcripts.headlessDenied,
      ruleDenied: groupRuleDenied(transcripts.ruleDenied, eff),
      candidates,
      candidatesDropped,
    };
  }

  let projects = null;
  if (opts.allProjects) {
    projects = [];
    for (const dir of findProjects(opts.allProjects)) {
      if (dir === home) continue;
      const l = loadScopes({ cwd: dir, home });
      const fl = runChecks(l, { home }).filter((x) => PROJECT_SCOPES.has(x.scope));
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
    window: transcripts ? { days: transcripts.window.days, files: transcripts.window.files, oldest: transcripts.window.oldestTimestamp?.slice(0, 10) ?? null } : undefined,
    ms: 0,
  };
  full.ms = Date.now() - t0;
  return full;
}

// ---- text -------------------------------------------------------------------

export function renderText(r, maxLines = 60) {
  const lines = [];
  const home = os.homedir();
  const short = (p) => (p ? p.replace(home, "~") : "(managed: none)");
  lines.push(`perm-scan  cwd=${short(r.cwd)}  ${r.ms} ms${r.window ? `  transcripts: ${r.window.files} files / ${r.window.days}d` : "  (settings only)"}`);
  for (const s of r.scopes) lines.push(`  ${s.name.padEnd(13)} ${s.exists ? `allow ${s.counts.allow} / ask ${s.counts.ask} / deny ${s.counts.deny}` : "absent"}  ${short(s.file)}${s.error ? "  ERROR " + s.error : ""}`);
  const body = [];
  for (const f of r.findings) body.push(`${f.severity.toUpperCase().padEnd(4)} ${f.id} ${f.scope} ${f.rule} — ${f.why}`);
  if (r.findings.length === 0) body.push("no findings");
  const fr = r.friction;
  if (fr) {
    body.push("friction:");
    for (const b of fr.classifierBlocks) body.push(`  F1 classifier [${b.reason}] x${b.count} last ${b.last} (${b.heads.join(", ")})`);
    for (const x of fr.rejected) body.push(`  F2 rejected ${x.tool}${x.head ? " " + x.head : ""} x${x.count}`);
    for (const x of fr.headlessDenied) body.push(`  F3 headless not-granted ${x.tool} x${x.count}`);
    for (const x of fr.ruleDenied) body.push(`  F5 deny rule fired ${x.rule} x${x.count}`);
    for (const c of fr.candidates) body.push(`  F4 candidate ${c.rule} (${c.calls} calls) -> ${c.scopeSuggestion}${c.project ? " " + c.project : ""}${c.otherProject ? " (other project)" : ""}`);
  }
  for (const p of r.projects ?? []) {
    body.push(`project ${short(p.dir)}:`);
    for (const f of p.findings) body.push(`  ${f.severity.toUpperCase().padEnd(4)} ${f.id} ${f.scope} ${f.rule} — ${f.why}`);
    if (p.findings.length === 0) body.push("  clean");
  }
  const room = maxLines - lines.length - 1;
  if (body.length > room) {
    const hidden = body.length - (room - 1);
    lines.push(...body.slice(0, room - 1), `... ${hidden} more lines (use --json)`);
  } else {
    lines.push(...body);
  }
  if (r.truncated) lines.push("(output truncated)");
  return lines.join("\n");
}

// ---- check mode -------------------------------------------------------------

export function runCheck(cmds, { cwd, home }) {
  const loaded = loadScopes({ cwd: path.resolve(cwd ?? process.cwd()), home });
  const results = cmds.map((cmd) => {
    const d = decide(cmd, loaded.effective);
    const r = { cmd, decision: d.decision, rule: d.rule ?? null, scope: d.scope ?? null };
    if (d.decision === "deny") r.bypassNote = "deny rules still apply under bypassPermissions";
    if (d.decision === "unmatched") r.note = "no rule: auto mode classifier decides (default mode prompts)";
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

const USAGE = "usage: perm-scan [--cwd DIR] [--json] [--settings-only] [--days N] [--all-projects ROOT] [--data-dir DIR] [--min-calls N] [--no-cache]\n       perm-scan check [--json] [--cwd DIR] <cmd>...";

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
