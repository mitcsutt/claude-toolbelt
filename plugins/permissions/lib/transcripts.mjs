// Streaming reader for Claude Code transcript JSONL (~/.claude/projects/**).
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
//  - toolDenialKind "permission-rule" is NOT only deny rules (observed 2026-10-12, 30 days of real data):
//    the result text tells the sources apart, see classifyPermissionRule().
//
// Cache design: each file's parsed records are cached WITH their timestamps, independent of the
// scan window; the window is applied when partials are merged. Denial attribution (which deny
// rule fired) is also done at merge time, against whatever rules the caller passes, so a cached
// partial never goes stale when settings change.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import { classifyDenial } from "./classify-denial.mjs";
import { commandHead as ruleHead, splitCompound, stripWrappers } from "./rule-matcher.mjs";

// Bump when the per-file partial's shape or the reading logic changes: invalidates every cache entry.
export const READER_VERSION = 3; // 3: window-independent timestamped records, denial classification, redaction fixes
const CACHE_FILE = "cache.json";
const CACHE_MAX_BYTES = 40 * 1024 * 1024;

const CAP_KEYS = 50000; // bound on every counting map
const CAP_SEEN = 400000; // bound on tool_use dedupe set
const CAP_PENDING = 5000; // per-file unmatched tool_use ids
const MAX_LINE = 5 * 1024 * 1024; // lines longer than this are skipped (counted in skippedLongLines)
const MAX_DENIED_CMD = 20000; // full command kept for exact deny-rule attribution
const MAX_SAMPLES = 3; // display samples per denial group
const MAX_SAMPLE_LEN = 160;
const MAX_UNKNOWN = 200;

const CLASSIFIER_RE = /denied by the Claude Code auto mode classifier\.\s*Reason:\s*\[([^\]]+)\]/;
const HEADLESS_RE = /requested permissions to use (\S+?), but you haven't granted it/;
const REJECT_RE = /^The user doesn't want to proceed with this tool use/;
const SANDBOX_HINT = /Operation not permitted|EPERM|Permission denied|Host key verification|Could not read from remote|hostkeys_foreach|unable to talk to your watchman/;

// toolDenialKind "permission-rule" result texts (all observed 2026-10-12):
//  hook:     "PreToolUse:Bash hook error: [~/.claude/hooks/public-repo-guard.py]: Blocked by ..."
//            (the bracket holds the hook command, e.g. "bash /path/x.sh" or an inline shell snippet)
//  deny rule:"Permission to use Bash with command <cmd> has been denied." / "Permission to use Read has been denied."
//  builtin:  "Permission for this command was denied by a built-in Claude Code safety check, not by the user. ..."
//  builtin:  "grep reads '-B5' after a cd whose target cannot be resolved, and a deny rule (~/.ssh/**) names that file under a directory it may run in"
const HOOK_RE = /^PreToolUse:[\w-]+ hook error: \[([\s\S]*?)\]:/;
const HOOK_LOOSE_RE = /^PreToolUse:[\w-]+ hook error: \[([^\]]{0,200})/;
const SAFETY_RE = /^Permission for this command was denied by a built-in Claude Code safety check/;
const PATHCHECK_RE = /^[\s\S]{0,300}?after a cd whose target cannot be resolved, and a deny rule \(/;
const DENY_RULE_RE = /^Permission to use \S+[\s\S]*\bhas been denied\b/;

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
  [/\b(Bearer)\s+[A-Za-z0-9._~+/=-]{4,}/gi, "$1 [REDACTED]"],
  [/\b(Basic|Token)\s+(?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{8,}/gi, "$1 [REDACTED]"],
  [/(Authorization|X-Api-Key|X-Auth-Token|Cookie)(["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s"']+)/gi, "$1$2[REDACTED]"],
  [/\bsk-[A-Za-z0-9_-]{4,}/g, "[REDACTED]"],
  [/\b(?:ghp|gho|ghs|ghu|github_pat|xox[abprs]|AKIA|AIza)[A-Za-z0-9_-]{8,}/g, "[REDACTED]"],
  [/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/g, "[UUID]"],
  [/\b([A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)[A-Za-z0-9_]*)=(?:"[^"]*"|'[^']*'|[^\s"']+)/gi, "$1=[REDACTED]"],
  // aws configure set aws_secret_access_key <value>
  [/\b(aws_secret_access_key|aws_session_token)(\s+)(?:"[^"]*"|'[^']*'|\S+)/gi, "$1$2[REDACTED]"],
  // --password x / --password=x and friends, quoted or not
  [/(--(?:password|passwd|pass|secret|token|api-?key|client-secret|access-token|auth-token)[= ])(?:"[^"]*"|'[^']*'|\S+)/gi, "$1[REDACTED]"],
  [/(\s(?:-u|--user|--password|--token)[ =])\S+/g, "$1[REDACTED]"],
  // mysql -pPW / mysql -p PW, sshpass -p PW, docker login -p PW, redis-cli -a PW. Over-redacts
  // `mysql -p dbname`, which is the safe failure mode.
  [/\b(mysql|mysqldump|mysqladmin|mariadb|mysqlsh|sshpass|docker\s+login|podman\s+login|nerdctl\s+login)\b([^\n|;&]*?\s-p)\s*(?!-)(?:"[^"]*"|'[^']*'|\S+)/g, "$1$2 [REDACTED]"],
  [/\b(redis-cli)\b([^\n|;&]*?\s-a)\s*(?!-)(?:"[^"]*"|'[^']*'|\S+)/g, "$1$2 [REDACTED]"],
  // scheme://user:password@host, password may contain "/" (a numeric port followed by a path is not a password)
  [/(:\/\/[^\s/:@]+:)(?!\d+(?:[/?#]|$))[^\s@]+@/g, "$1[REDACTED]@"],
];

export function redact(s) {
  let out = String(s ?? "");
  for (const [re, rep] of SECRET_RES) out = out.replace(re, rep);
  return out;
}

function resultText(c) {
  if (typeof c === "string") return c;
  if (Array.isArray(c)) return c.map((x) => (x && typeof x.text === "string" ? x.text : "")).join(" ");
  return "";
}

// Reads a JSONL file line by line, discarding (and counting) lines longer than MAX_LINE without
// ever holding them whole.
async function* readLines(file, onSkip) {
  const stream = fs.createReadStream(file, { encoding: "utf8" });
  let buf = "";
  let skipping = false;
  for await (const chunk of stream) {
    let s = chunk;
    for (;;) {
      const nl = s.indexOf("\n");
      if (nl < 0) {
        if (skipping) break;
        buf += s;
        if (buf.length > MAX_LINE) {
          skipping = true;
          buf = "";
          onSkip();
        }
        break;
      }
      const seg = s.slice(0, nl);
      s = s.slice(nl + 1);
      if (skipping) {
        skipping = false;
        continue;
      }
      const line = buf + seg;
      buf = "";
      if (line.length > MAX_LINE) {
        onSkip();
        continue;
      }
      yield line.endsWith("\r") ? line.slice(0, -1) : line;
    }
  }
  if (!skipping && buf) yield buf;
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

// Heads that say nothing about what a compound command actually does.
const NOISE_HEADS = new Set(["cd", "pushd", "popd", "export", "set", "unset", "source", ".", "true", "false", ":", "echo", "exit", "wait", "pwd", "test", "[", "[[", "for", "while", "until", "do", "done", "if", "then", "else", "elif", "fi", "case", "esac", "in", "function", "local", "declare", "readonly", "trap"]);
const LEADING_ASSIGN = /^(?:[A-Za-z_][A-Za-z0-9_]*=(?:"[^"]*"|'[^']*'|\$\((?:[^()]|\([^()]*\))*\)|`[^`]*`|\$(?!\()|[^\s"'`$])*\s+)+/;
const BARE_ASSIGN = /^[A-Za-z_][A-Za-z0-9_]*=/;

// The first segment that does real work ("cd x && rm -rf y" -> "rm", never "cd" or "&&"; assignment-only
// segments and shell keywords are skipped; leading VAR=value assignments are stripped, they may hold secrets).
export function commandHead(cmd) {
  const segs = splitCompound(String(cmd ?? ""))
    .map((s) => s.trim())
    .filter((s) => s && !/^[&|;\\]+$/.test(s));
  const headOf = (seg) => {
    let s = seg.replace(LEADING_ASSIGN, "");
    if (BARE_ASSIGN.test(s)) return "";
    s = s.replace(/^(?:(?:do|then|else|elif|\{|\()\s+)+/, "");
    s = stripWrappers(s) ?? s;
    s = s.replace(LEADING_ASSIGN, "");
    if (!s || BARE_ASSIGN.test(s)) return "";
    return redact(ruleHead(s)).slice(0, 60);
  };
  for (const seg of segs) {
    const h = headOf(seg);
    if (h && !NOISE_HEADS.has(h.split(" ")[0])) return h;
  }
  return segs.length ? headOf(segs[0]) || redact(ruleHead(segs[0].replace(LEADING_ASSIGN, ""))).slice(0, 60) : "";
}

const HEREDOC_RE = /<<-?\s*(['"`]?)([A-Za-z_][A-Za-z0-9_]*)\1[\s\S]*?\n[ \t]*\2[ \t]*$/gm;
// Full command kept for deny-rule attribution: every line kept (the rule may fire on a later
// segment), heredoc bodies dropped, secrets redacted. Capped generously, never at a few hundred chars.
function fullCommand(cmd) {
  let s = String(cmd ?? "").replace(HEREDOC_RE, (_m, _q, tag) => `<<'${tag}' …`);
  s = s
    .replace(/\\\n\s*/g, " ")
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .join(" ; ");
  s = redact(s);
  return s.length > MAX_DENIED_CMD ? s.slice(0, MAX_DENIED_CMD) : s;
}

function toolDetail(name, input) {
  if (!input || typeof input !== "object") return "";
  if (name === "Bash") return typeof input.command === "string" ? input.command : "";
  for (const k of ["file_path", "path", "url", "notebook_path", "pattern"]) if (typeof input[k] === "string") return input[k];
  return "";
}

function hookName(raw) {
  const files = [...String(raw).matchAll(/([^\s/"'\\]+\.(?:sh|py|mjs|cjs|js|ts|rb|pl))\b/g)];
  if (files.length) return files[files.length - 1][1];
  const t = redact(String(raw).trim()).split(/\s+/)[0] || "hook";
  return path.basename(t).slice(0, 40) || "hook";
}

// -> { kind: "hook", hook } | { kind: "builtin", check } | { kind: "deny-rule" }
export function classifyPermissionRule(text) {
  const t = String(text ?? "").trimStart();
  const h = HOOK_RE.exec(t) ?? HOOK_LOOSE_RE.exec(t);
  if (h) return { kind: "hook", hook: hookName(h[1]) };
  if (SAFETY_RE.test(t)) return { kind: "builtin", check: "safety-check" };
  if (DENY_RULE_RE.test(t)) return { kind: "deny-rule" };
  if (PATHCHECK_RE.test(t)) return { kind: "builtin", check: "path-check" };
  return { kind: "builtin", check: "other" };
}

const idHash = (id) => crypto.createHash("sha1").update(id).digest("base64").slice(0, 10);
const toMs = (ts) => {
  if (typeof ts !== "string" || !ts) return 0;
  const n = Date.parse(ts);
  return Number.isFinite(n) ? n : 0;
};

// ---- per-file partial -------------------------------------------------------
// A partial is plain JSON (cacheable) holding every record one transcript file contributes, each
// with its timestamp in epoch ms (0 = record had none; always inside the window). The window is
// applied when partials are merged, so the cache never depends on it. Cross-file work (tool_use
// dedupe, caps, ordering) also happens only at merge, in directory-walk order.

async function readFilePartial(file, { sandboxEnabled }) {
  const isSub = file.split(path.sep).includes("subagents");
  let fileSid = path.basename(file, ".jsonl");
  if (isSub) fileSid = path.basename(path.dirname(path.dirname(file)));

  const p = { parseErrors: 0, skippedLongLines: 0 };
  const modes = new Map(); // mode + "\0" + sid -> latest ms
  const classifier = [];
  const rejected = [];
  const headless = [];
  const denials = [];
  const sandbox = [];
  const unknown = [];
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
  const pending = new Map(); // tool_use id -> {name, cmd, detail, cwd}
  let lastMode = null;

  const noteMode = (mode, sid, ms) => {
    if (typeof mode !== "string" || !mode) return;
    lastMode = mode;
    const k = mode + "\u0000" + sid;
    if ((modes.get(k) ?? -1) < ms) {
      if (modes.size >= CAP_KEYS && !modes.has(k)) return;
      modes.set(k, ms);
    }
  };

  for await (const line of readLines(file, () => p.skippedLongLines++)) {
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
      if (unknown.length < MAX_UNKNOWN) unknown.push(0);
      continue;
    }
    const ms = toMs(o.timestamp);
    const sid = typeof o.sessionId === "string" ? o.sessionId : fileSid;

    if (o.type === "permission-mode") {
      noteMode(o.permissionMode, sid, ms);
      continue;
    }
    if (o.type === "user" && o.permissionMode) noteMode(o.permissionMode, sid, ms);

    const content = o.message && Array.isArray(o.message.content) ? o.message.content : null;
    if (!content) {
      if (o.type !== "user" && o.type !== "assistant" && !IGNORED_TYPES.has(o.type) && unknown.length < MAX_UNKNOWN) unknown.push(ms);
      continue;
    }

    if (o.type === "assistant") {
      for (const c of content) {
        if (!c || c.type !== "tool_use" || typeof c.name !== "string") continue;
        const id = typeof c.id === "string" ? c.id : null;
        uses.push([ms, id ? idHash(id) : null, intern(c.name)]);
        if (id) {
          if (pending.size >= CAP_PENDING) pending.delete(pending.keys().next().value);
          const detail = toolDetail(c.name, c.input);
          pending.set(id, { name: c.name, detail, cwd: typeof o.cwd === "string" ? o.cwd : null });
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
        const isBash = toolName === "Bash" && use && use.detail;
        const head = isBash ? commandHead(use.detail) : "";

        if (c.is_error === true) {
          if (o.toolDenialKind === "permission-rule") {
            const k = classifyPermissionRule(text);
            const full = k.kind === "deny-rule" && use ? fullCommand(use.detail) : "";
            denials.push([ms, intern(toolName), k.kind, intern(k.hook ?? k.check ?? null), intern(full || null), intern(use && use.cwd), intern(lastMode)]);
            continue;
          }
          const hl = HEADLESS_RE.exec(text);
          if (hl) {
            headless.push([ms, intern(hl[1]), intern(head)]);
            continue;
          }
          const cl = CLASSIFIER_RE.exec(text);
          if (cl) {
            classifier.push([ms, intern(cl[1]), intern(head || toolName)]);
            continue;
          }
          if (o.toolDenialKind === "user-rejected" || REJECT_RE.test(text)) {
            if (toolName === "AskUserQuestion") continue; // dismissal, not a permission signal
            rejected.push([ms, intern(toolName), intern(head)]);
            continue;
          }
        }
        if (sandboxEnabled && toolName === "Bash" && SANDBOX_HINT.test(text)) {
          const d = classifyDenial({ command: use ? use.detail : "", stderr: text });
          if (d) sandbox.push([ms, intern(d.signature), intern(d.matched_path ? redact(d.matched_path) : null), intern(d.command_head ? redact(d.command_head).slice(0, 60) : null)]);
        }
      }
    }
  }

  p.modes = [...modes].map(([k, ms]) => {
    const i = k.indexOf("\u0000");
    return [k.slice(0, i), k.slice(i + 1), ms];
  });
  p.classifier = classifier;
  p.rejected = rejected;
  p.headless = headless;
  p.denials = denials;
  p.sandbox = sandbox;
  p.unknown = unknown;
  p.strs = strs;
  p.uses = uses;
  return p;
}

const inWindow = (ms, cutoffMs) => ms === 0 || ms >= cutoffMs;

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
    fs.mkdirSync(cacheDir, { recursive: true, mode: 0o700 });
    // leftovers of a crashed save
    for (const n of fs.readdirSync(cacheDir)) {
      if (!n.startsWith(`${CACHE_FILE}.`) || !n.endsWith(".tmp")) continue;
      try {
        const f = path.join(cacheDir, n);
        if (Date.now() - fs.statSync(f).mtimeMs > 3600000) fs.rmSync(f, { force: true });
      } catch {
        /* ignore */
      }
    }
    const tmp = path.join(cacheDir, `${CACHE_FILE}.${process.pid}.tmp`);
    fs.writeFileSync(tmp, text, { mode: 0o600 });
    fs.renameSync(tmp, path.join(cacheDir, CACHE_FILE));
  } catch {
    /* the cache is best effort */
  }
}

// ---- scan -------------------------------------------------------------------

// attribute(tool, detail, cwd) -> { decision, rule?, scope?, tag? } | null, called once per distinct
// deny-rule hit (memoized) to find which CURRENT rule fires; `tag` is counted per group.
export async function scanTranscripts({
  projectsDir = path.join(os.homedir(), ".claude", "projects"),
  days = 30,
  now = new Date(),
  sandboxEnabled = false,
  cacheDir = null, // null: no cache is read or written
  attribute = null,
} = {}) {
  const t0 = Date.now();
  const cutoffMs = now.getTime() - days * 86400000;

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
    denials: new Map(),
    toolCounts: new Map(),
    sandbox: new Map(),
    unknownLines: 0,
    parseErrors: 0,
    skippedLongLines: 0,
  };
  const seenToolUse = new Set();
  const attrMemo = new Map();
  const iso = (ms) => (ms ? new Date(ms).toISOString() : "");
  const stamp = (ms) => {
    if (!ms) return;
    if (st.oldest === null || ms < st.oldest) st.oldest = ms;
    if (st.newest === null || ms > st.newest) st.newest = ms;
  };
  const bumpGroup = (map, key, ms, init) => {
    let e = map.get(key);
    if (!e) {
      if (map.size >= CAP_KEYS) return null;
      e = init();
      map.set(key, e);
    }
    e.count++;
    if (ms > e.lastMs) e.lastMs = ms;
    return e;
  };

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
    if (hit && hit.k === key && hit.p && typeof hit.p === "object") p = hit.p;
    if (!p) {
      p = await readFilePartial(file, { sandboxEnabled });
      dirty = true;
    }
    if (cacheDir) fresh[file] = { k: key, m: stat.mtimeMs, p };

    // ---- merge this file's partial, applying the window (walk order) ----
    const S = (i) => (i >= 0 ? p.strs[i] : null);
    st.parseErrors += p.parseErrors;
    st.skippedLongLines += p.skippedLongLines ?? 0;
    for (const ms of p.unknown) if (inWindow(ms, cutoffMs)) st.unknownLines++;
    for (const [mode, sid, ms] of p.modes) {
      if (!inWindow(ms, cutoffMs)) continue;
      let set = st.modeSessions.get(mode);
      if (!set) st.modeSessions.set(mode, (set = new Set()));
      if (set.size < CAP_KEYS) set.add(sid);
    }
    for (const [ms, id, nameI] of p.uses) {
      if (!inWindow(ms, cutoffMs)) continue;
      if (id) {
        if (seenToolUse.has(id)) continue;
        if (seenToolUse.size < CAP_SEEN) seenToolUse.add(id);
      }
      stamp(ms);
      const name = S(nameI);
      if (st.toolCounts.size < CAP_KEYS || st.toolCounts.has(name)) st.toolCounts.set(name, (st.toolCounts.get(name) || 0) + 1);
    }
    for (const [ms, reasonI, headI] of p.classifier) {
      if (!inWindow(ms, cutoffMs)) continue;
      stamp(ms);
      const reason = S(reasonI);
      const e = bumpGroup(st.classifier, reason, ms, () => ({ reason, count: 0, lastMs: 0, heads: [] }));
      const h = S(headI);
      if (e && h && !e.heads.includes(h) && e.heads.length < 3) e.heads.push(h);
    }
    for (const [ms, toolI, headI] of p.rejected) {
      if (!inWindow(ms, cutoffMs)) continue;
      stamp(ms);
      const tool = S(toolI);
      const head = S(headI) ?? "";
      bumpGroup(st.rejected, tool + "\u0000" + head, ms, () => ({ tool, head, count: 0, lastMs: 0 }));
    }
    for (const [ms, toolI, headI] of p.headless) {
      if (!inWindow(ms, cutoffMs)) continue;
      stamp(ms);
      const tool = S(toolI);
      const head = S(headI) ?? "";
      bumpGroup(st.headless, tool + "\u0000" + head, ms, () => ({ tool, head, count: 0, lastMs: 0 }));
    }
    for (const [ms, toolI, kind, labelI, fullI, cwdI, modeI] of p.denials) {
      if (!inWindow(ms, cutoffMs)) continue;
      stamp(ms);
      const tool = S(toolI);
      const label = S(labelI);
      const full = S(fullI);
      const mode = S(modeI);
      let g = { kind };
      let tag = null;
      if (kind === "hook") g.hook = label;
      else if (kind === "builtin") g.check = label;
      else {
        let a = null;
        if (attribute && full != null) {
          const mk = tool + "\u0000" + full + "\u0000" + (S(cwdI) ?? "");
          if (attrMemo.has(mk)) a = attrMemo.get(mk);
          else {
            try {
              a = attribute(tool, full, S(cwdI));
            } catch {
              a = null;
            }
            attrMemo.set(mk, a);
          }
        }
        if (a && a.decision === "deny" && a.rule) {
          g.rule = a.rule;
          g.scope = a.scope ?? null;
        } else {
          g.rule = null;
          g.scope = null;
          g.note = a && a.decision === "unsure" ? "rule no longer present, or command too complex to analyze" : "rule no longer present";
          g.tool = tool;
        }
        tag = a && a.tag ? a.tag : null;
      }
      const gk = [kind, g.hook ?? "", g.check ?? "", g.rule ?? "", g.scope ?? "", g.tool ?? ""].join("\u0000");
      const e = bumpGroup(st.denials, gk, ms, () => ({ ...g, count: 0, lastMs: 0, samples: [], modes: {}, tags: {} }));
      if (!e) continue;
      if (mode) e.modes[mode] = (e.modes[mode] || 0) + 1;
      if (tag) e.tags[tag] = (e.tags[tag] || 0) + 1;
      if (full && e.samples.length < MAX_SAMPLES) {
        const sm = full.length > MAX_SAMPLE_LEN ? full.slice(0, MAX_SAMPLE_LEN) + "…" : full;
        if (!e.samples.includes(sm)) e.samples.push(sm);
      }
    }
    for (const [ms, sigI, pathI, headI] of p.sandbox) {
      if (!inWindow(ms, cutoffMs)) continue;
      stamp(ms);
      const signature = S(sigI);
      const pth = S(pathI);
      const e = bumpGroup(st.sandbox, signature + "\u0000" + (pth ?? ""), ms, () => ({ signature, count: 0, lastMs: 0 }));
      if (e && pth) e.path = pth;
      if (e && !e.head && S(headI)) e.head = S(headI);
    }
  }

  if (cacheDir && (dirty || Object.keys(cached).length !== Object.keys(fresh).length)) saveCache(cacheDir, fresh);

  const byCount = (a, b) => b.count - a.count || b.lastMs - a.lastMs;
  const out = (list) =>
    list.sort(byCount).map(({ lastMs, ...e }) => ({ ...e, last: iso(lastMs) }));
  const modes = {};
  for (const [m, set] of st.modeSessions) modes[m] = set.size;
  const toolCounts = {};
  for (const [k, v] of st.toolCounts) toolCounts[k] = v;

  return {
    window: {
      days,
      files: st.files,
      subagentFiles: st.subagentFiles,
      oldestTimestamp: st.oldest ? iso(st.oldest) : null,
      newestTimestamp: st.newest ? iso(st.newest) : null,
    },
    modes,
    classifierBlocks: out([...st.classifier.values()]),
    rejected: out([...st.rejected.values()]),
    headlessDenied: out([...st.headless.values()]),
    denials: out([...st.denials.values()]),
    toolCounts,
    sandboxDenials: sandboxEnabled ? out([...st.sandbox.values()]) : [],
    unknownLines: st.unknownLines,
    parseErrors: st.parseErrors,
    skippedLongLines: st.skippedLongLines,
    ms: Date.now() - t0,
  };
}
