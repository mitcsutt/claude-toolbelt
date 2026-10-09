// Streaming, windowed reader for Claude Code transcript JSONL (~/.claude/projects/**).
// The record format is undocumented (https://github.com/anthropics/claude-code/issues/53516),
// so every access is defensive: unknown shapes are counted, never thrown.
//
// Shapes relied on (all "observed 2026-10-09" on this machine, CC 2.1.268-2.1.293):
//  - {type:"permission-mode", permissionMode, sessionId}
//  - {type:"user", permissionMode, ...}
//  - {type:"assistant", message:{content:[{type:"tool_use", id, name, input}]}}
//  - {type:"user", message:{content:[{type:"tool_result", tool_use_id, is_error, content}]},
//     toolDenialKind?: "user-rejected"|"automode-blocked"|"automode-parsing-error"|"permission-rule"}
//  - classifier: content "Permission for this action was denied by the Claude Code auto mode
//    classifier. Reason: [Auto-Mode Bypass]. If you have other tasks..." (observed 2026-10-09)
//  - user rejection: content "The user doesn't want to proceed with this tool use. The tool use was
//    rejected (eg. ..." with toolDenialKind "user-rejected" (observed 2026-10-09)
//  - headless: content "Claude requested permissions to use Bash, but you haven't granted it yet."
//    ALSO carries toolDenialKind "user-rejected" (+ permissionDecision.source "config"), so the
//    text is tested first (observed 2026-10-09)

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import readline from "node:readline";
import crypto from "node:crypto";
import { sanitizeDetail } from "./detail-sanitize.mjs";
import { classifyDenial } from "./classify-denial.mjs";
import { commandHead as ruleHead, splitCompound } from "./rule-matcher.mjs";

// Bump when the per-file partial's shape or the reading logic changes: invalidates every cache entry.
export const READER_VERSION = 2; // 2: bare TOKEN=/KEY= assignments redacted
const CACHE_FILE = "cache.json";
const CACHE_MAX_BYTES = 20 * 1024 * 1024;

const CAP_KEYS = 50000; // bound on every counting map
const CAP_SEEN = 400000; // bound on tool_use dedupe set
const CAP_PENDING = 5000; // per-file unmatched tool_use ids
const TOP_BASH = 2000;
const MAX_CMD = 200;
const MAX_SAMPLES = 5;
const MAX_SAMPLE_LEN = 400;
const MAX_CWD_COUNTS = 5; // per command, per file

const CLASSIFIER_RE = /denied by the Claude Code auto mode classifier\.\s*Reason:\s*\[([^\]]+)\]/;
const HEADLESS_RE = /requested permissions to use (\S+?), but you haven't granted it/;
const REJECT_RE = /^The user doesn't want to proceed with this tool use/;
const SANDBOX_HINT = /Operation not permitted|EPERM|Permission denied|Host key verification|Could not read from remote|hostkeys_foreach|unable to talk to your watchman/;

// Record types seen in real transcripts (observed 2026-10-09) that carry no friction signal;
// they match the prefilter incidentally (e.g. skill_listing mentions permissionMode).
const IGNORED_TYPES = new Set(["attachment", "queue-operation", "system", "summary", "progress", "file-history-snapshot", "last-prompt", "custom-title", "agent-name"]);

// Prefilter: a line is parsed only if it contains one of these substrings.
const PREFILTER = [
  '"type":"tool_use"',
  "permission-mode",
  "permissionMode",
  "toolDenialKind",
  '"is_error":true',
  "Operation not permitted",
  "EPERM",
];

const SECRET_RES = [
  [/\b(Bearer|Basic|Token)\s+[A-Za-z0-9._~+/=-]{4,}/gi, "$1 [REDACTED]"],
  [/(Authorization|X-Api-Key|X-Auth-Token|Cookie)(["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s"']+)/gi, "$1$2[REDACTED]"],
  [/\bsk-[A-Za-z0-9_-]{4,}/g, "[REDACTED]"],
  [/\b(?:ghp|gho|ghs|ghu|github_pat|xox[abprs]|AKIA|AIza)[A-Za-z0-9_-]{8,}/g, "[REDACTED]"],
  [/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/g, "[UUID]"],
  [/\b([A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)[A-Za-z0-9_]*)=(?:"[^"]*"|'[^']*'|\S+)/gi, "$1=[REDACTED]"],
  [/(\s(?:-u|--user|--password|--token)[ =])\S+/g, "$1[REDACTED]"],
  [/(:\/\/[^\s/:@]+:)[^\s/@]+@/g, "$1[REDACTED]@"],
];

export function redact(s) {
  let out = String(s ?? "");
  for (const [re, rep] of SECRET_RES) out = out.replace(re, rep);
  return out;
}

function normalizeCommand(cmd) {
  let s = sanitizeDetail(cmd).replace(/ …\(\+\d+ lines\)$/, "").trim();
  s = redact(s);
  return s.length > MAX_CMD ? s.slice(0, MAX_CMD) + "…" : s;
}

function resultText(c) {
  if (typeof c === "string") return c;
  if (Array.isArray(c)) return c.map((x) => (x && typeof x.text === "string" ? x.text : "")).join(" ");
  return "";
}

function bump(map, key, ts, init) {
  let e = map.get(key);
  if (!e) {
    if (map.size >= CAP_KEYS) return null;
    e = init();
    map.set(key, e);
  }
  e.count++;
  if (ts && ts > e.last) e.last = ts;
  return e;
}

async function* walk(dir) {
  let ents;
  try {
    ents = await fs.promises.readdir(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const e of ents) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) yield* walk(p);
    else if (e.isFile() && e.name.endsWith(".jsonl")) yield p;
  }
}

// Head of the first non-empty compound segment ("cd x && rm -rf /" -> "cd", never "&&"), with
// leading VAR=value assignments skipped (they may hold secrets).
function commandHead(cmd) {
  const segs = splitCompound(String(cmd ?? ""))
    .map((s) => s.trim())
    .filter((s) => s && !/^[&|;\\]+$/.test(s));
  const stripped = (segs[0] ?? "").replace(/^(?:[A-Za-z_][A-Za-z0-9_]*=(?:"[^"]*"|'[^']*'|\$\((?:[^()]|\([^()]*\))*\)|\S*)\s+)+/, "");
  return redact(ruleHead(stripped)).slice(0, 60);
}

const HEREDOC_RE = /<<-?\s*(['"`]?)([A-Za-z_][A-Za-z0-9_]*)\1[\s\S]*?\n[ \t]*\2[ \t]*$/gm;
// Full-command sample for deny-rule attribution: every line kept (the rule may fire on a later
// segment), heredoc bodies dropped, secrets redacted.
function sampleCommand(cmd) {
  let s = String(cmd ?? "").replace(HEREDOC_RE, (_m, _q, tag) => `<<'${tag}' …`);
  s = s
    .replace(/\\\n\s*/g, " ")
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .join(" ; ");
  s = redact(s);
  return s.length > MAX_SAMPLE_LEN ? s.slice(0, MAX_SAMPLE_LEN) + "…" : s;
}

const idHash = (id) => crypto.createHash("sha1").update(id).digest("base64").slice(0, 10);

// ---- per-file partial -------------------------------------------------------
// A partial is plain JSON (cacheable) holding everything one transcript file contributes.
// Cross-file work (tool_use dedupe, caps, ordering) happens only when partials are merged,
// in directory-walk order, so results are identical with and without the cache.

async function readFilePartial(file, { cutoffIso, sandboxEnabled }) {
  const isSub = file.split(path.sep).includes("subagents");
  let fileSid = path.basename(file, ".jsonl");
  if (isSub) fileSid = path.basename(path.dirname(path.dirname(file)));

  const p = { cut: cutoffIso, dropped: 0, minTs: null, oldest: null, newest: null, unknown: 0, parseErrors: 0 };
  const modes = new Map(); // mode -> Set(sid)
  const classifier = new Map();
  const rejected = new Map();
  const headless = new Map();
  const ruleDenied = new Map();
  const sandbox = new Map();
  const strs = [];
  const strIdx = new Map();
  const intern = (s) => {
    if (s == null) return -1;
    let i = strIdx.get(s);
    if (i === undefined) {
      i = strs.length;
      strs.push(s);
      strIdx.set(s, i);
    }
    return i;
  };
  const uses = [];
  const pending = new Map(); // tool_use id -> {name, cmd}

  const noteMode = (mode, sid) => {
    if (typeof mode !== "string" || !mode) return;
    let set = modes.get(mode);
    if (!set) modes.set(mode, (set = new Set()));
    if (set.size < CAP_KEYS) set.add(sid);
  };

  const rl = readline.createInterface({ input: fs.createReadStream(file, { encoding: "utf8" }), crlfDelay: Infinity });
  for await (const line of rl) {
    if (line.length < 10) continue;
    let hit = false;
    for (const pf of PREFILTER) {
      if (line.includes(pf)) {
        hit = true;
        break;
      }
    }
    if (!hit) continue;
    let o;
    try {
      o = JSON.parse(line);
    } catch {
      p.parseErrors++;
      continue;
    }
    if (!o || typeof o !== "object") {
      p.unknown++;
      continue;
    }
    const ts = typeof o.timestamp === "string" ? o.timestamp : "";
    if (ts && (p.minTs === null || ts < p.minTs)) p.minTs = ts;
    if (ts && ts < cutoffIso) {
      p.dropped++;
      continue;
    }
    if (ts) {
      if (!p.oldest || ts < p.oldest) p.oldest = ts;
      if (!p.newest || ts > p.newest) p.newest = ts;
    }
    const sid = typeof o.sessionId === "string" ? o.sessionId : fileSid;

    if (o.type === "permission-mode") {
      noteMode(o.permissionMode, sid);
      continue;
    }
    if (o.type === "user" && o.permissionMode) noteMode(o.permissionMode, sid);

    const content = o.message && Array.isArray(o.message.content) ? o.message.content : null;
    if (!content) {
      if (o.type !== "user" && o.type !== "assistant" && !IGNORED_TYPES.has(o.type)) p.unknown++;
      continue;
    }

    if (o.type === "assistant") {
      for (const c of content) {
        if (!c || c.type !== "tool_use" || typeof c.name !== "string") continue;
        const id = typeof c.id === "string" ? c.id : null;
        let cmd = null;
        let norm = null;
        if (c.name === "Bash" && c.input && typeof c.input.command === "string") {
          cmd = c.input.command;
          norm = normalizeCommand(cmd) || null;
        }
        uses.push([id ? idHash(id) : null, intern(c.name), intern(norm), intern(typeof o.cwd === "string" ? o.cwd : null)]);
        if (id) {
          if (pending.size >= CAP_PENDING) pending.delete(pending.keys().next().value);
          pending.set(id, { name: c.name, cmd });
        }
      }
      continue;
    }

    if (o.type === "user") {
      for (const c of content) {
        if (!c || c.type !== "tool_result") continue;
        const use = pending.get(c.tool_use_id);
        if (use) pending.delete(c.tool_use_id);
        const text = resultText(c.content);
        const toolName = use ? use.name : "unknown";
        const head = use && use.cmd != null ? commandHead(use.cmd) : "";

        if (c.is_error === true) {
          if (o.toolDenialKind === "permission-rule") {
            // deny-rule hit (observed 2026-10-09): text "Permission to use <Tool> with command ... has been denied."
            const e = bump(ruleDenied, toolName + "\u0000" + head, ts, () => ({ tool: toolName, head, count: 0, last: "", samples: [], sampleCounts: [] }));
            if (e && toolName === "Bash" && use && use.cmd != null) {
              const s = sampleCommand(use.cmd);
              const at = e.samples.indexOf(s);
              if (at >= 0) e.sampleCounts[at]++;
              else if (e.samples.length < MAX_SAMPLES) {
                e.samples.push(s);
                e.sampleCounts.push(1);
              }
            }
            continue;
          }
          const hl = HEADLESS_RE.exec(text);
          if (hl) {
            bump(headless, hl[1], ts, () => ({ tool: hl[1], count: 0, last: "" }));
            continue;
          }
          const cl = CLASSIFIER_RE.exec(text);
          if (cl) {
            const reason = cl[1];
            const e = bump(classifier, reason, ts, () => ({ reason, count: 0, last: "", heads: [] }));
            const h = head || toolName;
            if (e && h && !e.heads.includes(h) && e.heads.length < 3) e.heads.push(h);
            continue;
          }
          if (o.toolDenialKind === "user-rejected" || REJECT_RE.test(text)) {
            if (toolName === "AskUserQuestion") continue; // dismissal, not a permission signal
            bump(rejected, toolName + "\u0000" + head, ts, () => ({ tool: toolName, head, count: 0, last: "" }));
            continue;
          }
        }
        if (sandboxEnabled && toolName === "Bash" && SANDBOX_HINT.test(text)) {
          const d = classifyDenial({ command: use ? use.cmd : "", stderr: text });
          if (d) {
            const pth = d.matched_path ? redact(d.matched_path) : undefined;
            const e = bump(sandbox, d.signature + "\u0000" + (pth ?? ""), ts, () => ({ signature: d.signature, count: 0, last: "" }));
            if (e && pth) e.path = pth;
          }
        }
      }
    }
  }

  p.modes = {};
  for (const [m, set] of modes) p.modes[m] = [...set];
  p.classifier = [...classifier.values()];
  p.rejected = [...rejected.values()];
  p.headless = [...headless.values()];
  p.ruleDenied = [...ruleDenied.values()];
  p.sandbox = [...sandbox.values()];
  p.strs = strs;
  p.uses = uses;
  return p;
}

// A cached partial is reusable when it was computed for the same record cutoff, or when the
// cutoff never dropped anything and still would not (every timestamp is inside the window).
function partialValid(p, cutoffIso) {
  if (!p || typeof p !== "object") return false;
  if (p.cut === cutoffIso) return true;
  return p.dropped === 0 && (p.minTs === null || p.minTs >= cutoffIso);
}

function mergeInto(map, key, e, ts, sum) {
  let t = map.get(key);
  if (!t) {
    if (map.size >= CAP_KEYS) return null;
    t = sum.init();
    map.set(key, t);
  }
  t.count += e.count;
  if (e.last && e.last > t.last) t.last = e.last;
  return t;
}

// ---- cache ------------------------------------------------------------------

function loadCache(cacheDir) {
  try {
    const j = JSON.parse(fs.readFileSync(path.join(cacheDir, CACHE_FILE), "utf8"));
    if (j && j.v === READER_VERSION && j.entries && typeof j.entries === "object") return j.entries;
  } catch {
    /* absent or corrupt: start empty */
  }
  return {};
}

function saveCache(cacheDir, entries) {
  try {
    const keep = Object.entries(entries).sort((a, b) => b[1].m - a[1].m); // newest files first
    let text = "";
    let used = keep.length;
    for (;;) {
      text = JSON.stringify({ v: READER_VERSION, entries: Object.fromEntries(keep.slice(0, used)) });
      if (text.length <= CACHE_MAX_BYTES || used === 0) break;
      used = Math.floor(used * 0.8);
    }
    fs.mkdirSync(cacheDir, { recursive: true });
    const tmp = path.join(cacheDir, `${CACHE_FILE}.${process.pid}.tmp`);
    fs.writeFileSync(tmp, text);
    fs.renameSync(tmp, path.join(cacheDir, CACHE_FILE));
  } catch {
    /* the cache is best effort */
  }
}

// ---- scan -------------------------------------------------------------------

export async function scanTranscripts({
  projectsDir = path.join(os.homedir(), ".claude", "projects"),
  days = 30,
  now = new Date(),
  sandboxEnabled = false,
  cacheDir = null, // null: no cache is read or written
} = {}) {
  const t0 = Date.now();
  const cutoffMs = now.getTime() - days * 86400000;
  const cutoffIso = new Date(cutoffMs).toISOString();

  const cached = cacheDir ? loadCache(cacheDir) : {};
  const fresh = {};
  let dirty = false;

  const st = {
    files: 0,
    subagentFiles: 0,
    oldest: null,
    newest: null,
    modeSessions: new Map(),
    classifier: new Map(),
    rejected: new Map(),
    headless: new Map(),
    ruleDenied: new Map(),
    toolCounts: new Map(),
    bash: new Map(),
    sandbox: new Map(),
    unknownLines: 0,
    parseErrors: 0,
  };
  const seenToolUse = new Set();

  for await (const file of walk(projectsDir)) {
    let stat;
    try {
      stat = await fs.promises.stat(file);
    } catch {
      continue;
    }
    if (stat.mtimeMs < cutoffMs) continue;
    st.files++;
    if (file.split(path.sep).includes("subagents")) st.subagentFiles++;

    const key = `${stat.mtimeMs}|${stat.size}|${sandboxEnabled ? 1 : 0}`;
    let p = null;
    const hit = cacheDir ? cached[file] : null;
    if (hit && hit.k === key && partialValid(hit.p, cutoffIso)) p = hit.p;
    if (!p) {
      p = await readFilePartial(file, { cutoffIso, sandboxEnabled });
      dirty = true;
    }
    if (cacheDir) fresh[file] = { k: key, m: stat.mtimeMs, p };

    // ---- merge this file's partial (walk order) ----
    st.unknownLines += p.unknown;
    st.parseErrors += p.parseErrors;
    if (p.oldest && (!st.oldest || p.oldest < st.oldest)) st.oldest = p.oldest;
    if (p.newest && (!st.newest || p.newest > st.newest)) st.newest = p.newest;
    for (const [mode, sids] of Object.entries(p.modes)) {
      let set = st.modeSessions.get(mode);
      if (!set) st.modeSessions.set(mode, (set = new Set()));
      for (const sid of sids) if (set.size < CAP_KEYS) set.add(sid);
    }
    for (const u of p.uses) {
      const id = u[0];
      if (id) {
        if (seenToolUse.has(id)) continue;
        if (seenToolUse.size < CAP_SEEN) seenToolUse.add(id);
      }
      const name = p.strs[u[1]];
      if (st.toolCounts.size < CAP_KEYS || st.toolCounts.has(name)) st.toolCounts.set(name, (st.toolCounts.get(name) || 0) + 1);
      const norm = u[2] >= 0 ? p.strs[u[2]] : null;
      if (norm) {
        let be = st.bash.get(norm);
        if (!be && st.bash.size < CAP_KEYS) st.bash.set(norm, (be = { count: 0, cwds: new Map() }));
        if (be) {
          be.count++;
          const cwd = u[3] >= 0 ? p.strs[u[3]] : null;
          if (cwd && (be.cwds.has(cwd) || be.cwds.size < MAX_CWD_COUNTS)) be.cwds.set(cwd, (be.cwds.get(cwd) || 0) + 1);
        }
      }
    }
    for (const e of p.classifier) {
      const t = mergeInto(st.classifier, e.reason, e, null, { init: () => ({ reason: e.reason, count: 0, last: "", heads: [] }) });
      if (t) for (const h of e.heads) if (!t.heads.includes(h) && t.heads.length < 3) t.heads.push(h);
    }
    for (const e of p.rejected) mergeInto(st.rejected, e.tool + "\u0000" + e.head, e, null, { init: () => ({ tool: e.tool, head: e.head, count: 0, last: "" }) });
    for (const e of p.headless) mergeInto(st.headless, e.tool, e, null, { init: () => ({ tool: e.tool, count: 0, last: "" }) });
    for (const e of p.ruleDenied) {
      const t = mergeInto(st.ruleDenied, e.tool + "\u0000" + e.head, e, null, { init: () => ({ tool: e.tool, head: e.head, count: 0, last: "", samples: [], sampleCounts: [] }) });
      if (!t) continue;
      e.samples.forEach((s, i) => {
        const at = t.samples.indexOf(s);
        if (at >= 0) t.sampleCounts[at] += e.sampleCounts[i];
        else if (t.samples.length < MAX_SAMPLES) {
          t.samples.push(s);
          t.sampleCounts.push(e.sampleCounts[i]);
        }
      });
    }
    for (const e of p.sandbox) {
      const t = mergeInto(st.sandbox, e.signature + "\u0000" + (e.path ?? ""), e, null, { init: () => ({ signature: e.signature, count: 0, last: "" }) });
      if (t && e.path) t.path = e.path;
    }
  }

  if (cacheDir && (dirty || Object.keys(cached).length !== Object.keys(fresh).length)) saveCache(cacheDir, fresh);

  const byCount = (a, b) => b.count - a.count || (a.last < b.last ? 1 : -1);
  const modes = {};
  for (const [m, set] of st.modeSessions) modes[m] = set.size;
  const toolCounts = {};
  for (const [k, v] of st.toolCounts) toolCounts[k] = v;

  return {
    window: {
      days,
      files: st.files,
      subagentFiles: st.subagentFiles,
      oldestTimestamp: st.oldest,
      newestTimestamp: st.newest,
    },
    modes,
    classifierBlocks: [...st.classifier.values()].sort(byCount),
    rejected: [...st.rejected.values()].sort(byCount),
    headlessDenied: [...st.headless.values()].sort(byCount),
    ruleDenied: [...st.ruleDenied.values()].sort(byCount),
    toolCounts,
    bashCommands: [...st.bash.entries()]
      .map(([command, e]) => {
        let topCwd = null;
        let best = 0;
        for (const [cwd, n] of e.cwds) {
          if (n > best) {
            best = n;
            topCwd = cwd;
          }
        }
        return { command, count: e.count, cwds: Math.min(e.cwds.size, 3), topCwd };
      })
      .sort((a, b) => b.count - a.count)
      .slice(0, TOP_BASH),
    sandboxDenials: sandboxEnabled ? [...st.sandbox.values()].sort(byCount) : [],
    unknownLines: st.unknownLines,
    parseErrors: st.parseErrors,
    ms: Date.now() - t0,
  };
}
