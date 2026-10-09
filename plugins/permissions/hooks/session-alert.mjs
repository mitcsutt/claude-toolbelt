// SessionStart hook (spec sections 5 and 12). In-process only: reads settings via
// lib/scopes.mjs + lib/checks.mjs; never touches transcripts and never spawns.
//   M1  one-time archive of 2.x logs from ~/.claude into <data>/legacy-v2/
//   M3  delete that archive once it is >= 30 days old
//   Alert: cached fingerprint of the scope files; HIGH findings re-shown at most weekly.
// Output: at most one {"systemMessage": "..."} on stdout (P8: user-visible, no context tokens).
// Any failure exits 0 silently: a hook must never get in the way of a session.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import { pathToFileURL } from "node:url";

const DAY = 86400000;
const RESHOW_MS = 7 * DAY;
const RETAIN_MS = 30 * DAY;
const LOGS = ["permission-log.jsonl", "prompt-log.jsonl", "sandbox-denials.jsonl"];
const MAX_CACHED_CWDS = 20;

function nowMs(env) {
  const v = env.PERMISSIONS_NOW; // test seam
  if (v) {
    const n = /^\d+$/.test(v) ? Number(v) : Date.parse(v);
    if (Number.isFinite(n)) return n;
  }
  return Date.now();
}

const readJson = (file) => {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return null;
  }
};

function writeAtomic(file, text) {
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, text);
  fs.renameSync(tmp, file);
}

// ---- M1 / M3 ----------------------------------------------------------------

function migrate(home, data, now) {
  const marker = path.join(data, "schema");
  const archive = path.join(data, "legacy-v2");
  let message = null;

  let markerValue = null;
  try {
    markerValue = fs.readFileSync(marker, "utf8").trim();
  } catch {
    /* absent */
  }
  if (markerValue !== "3") {
    const claudeDir = path.join(home, ".claude");
    let names = [];
    try {
      names = fs.readdirSync(claudeDir).filter((n) => LOGS.some((l) => n === l || new RegExp(`^${l.replace(".", "\\.")}\\.\\d+$`).test(n)));
    } catch {
      names = [];
    }
    const moved = [];
    if (names.length) {
      fs.mkdirSync(archive, { recursive: true });
      for (const n of names) {
        try {
          const src = path.join(claudeDir, n);
          const bytes = fs.statSync(src).size;
          fs.renameSync(src, path.join(archive, n));
          moved.push({ file: n, bytes });
        } catch {
          /* leave the file where it is; perm-scan reports it as L1 */
        }
      }
    }
    fs.mkdirSync(data, { recursive: true });
    if (moved.length) {
      writeAtomic(path.join(archive, "MOVED.json"), JSON.stringify({ moved, at: new Date(now).toISOString() }) + "\n");
      const mb = (moved.reduce((s, m) => s + m.bytes, 0) / 1048576).toFixed(1);
      message = `permissions 3.0: archived 2.x logs (${mb} MB) to ${archive.replace(home, "~")}; auto-deleted after 30 days.`;
    }
    writeAtomic(marker, "3\n");
  }

  // M3: retention. Only ever removes the legacy-v2 directory itself.
  const info = readJson(path.join(archive, "MOVED.json"));
  if (info && path.basename(archive) === "legacy-v2") {
    const at = Date.parse(info.at);
    if (Number.isFinite(at) && now - at >= RETAIN_MS) fs.rmSync(archive, { recursive: true, force: true });
  }
  return message;
}

// ---- alert ------------------------------------------------------------------

function scopeFiles(cwd, home, managedPaths) {
  const files = [];
  for (const p of managedPaths) {
    files.push(p);
    const dir = path.join(path.dirname(p), "managed-settings.d");
    try {
      for (const n of fs.readdirSync(dir).filter((x) => x.endsWith(".json")).sort()) files.push(path.join(dir, n));
    } catch {
      /* no drop-in dir */
    }
  }
  files.push(path.join(home, ".claude", "settings.json"), path.join(cwd, ".claude", "settings.json"), path.join(cwd, ".claude", "settings.local.json"));
  return files;
}

function fingerprint(files) {
  const parts = files.map((f) => {
    try {
      const st = fs.statSync(f);
      return `${f}|${st.mtimeMs}|${st.size}`;
    } catch {
      return `${f}|missing`;
    }
  });
  return crypto.createHash("sha1").update(parts.join("\n")).digest("hex");
}

// Truncate at a word boundary with an ellipsis; the result is at most max chars.
export function clipWords(text, max) {
  const s = String(text ?? "").replace(/\s+/g, " ").trim();
  if (s.length <= max) return s;
  const cut = s.slice(0, max - 1);
  const sp = cut.lastIndexOf(" ");
  const base = sp > 0 ? cut.slice(0, sp) : cut;
  return base.replace(/[\s,;:.\-]+$/, "") + "…";
}

// "~/..." when under HOME, otherwise "<parent>/<basename>".
export function shortPath(file, home) {
  if (!file) return "settings";
  if (home && (file === home || file.startsWith(home + path.sep))) return "~" + file.slice(home.length);
  return path.join(path.basename(path.dirname(file)), path.basename(file));
}

async function alert(cwd, home, data, now, room) {
  const { defaultManagedPaths, loadScopes } = await import("../lib/scopes.mjs");
  const fp = fingerprint(scopeFiles(cwd, home, defaultManagedPaths()));
  const cacheFile = path.join(data, "alert-cache.json");
  const cache = readJson(cacheFile) ?? {};
  const entry = cache[cwd];
  const iso = new Date(now).toISOString();
  const stale = (h) => !Number.isFinite(Date.parse(h.lastShown)) || now - Date.parse(h.lastShown) >= RESHOW_MS;

  let high;
  let show;
  if (entry && entry.fingerprint === fp && Array.isArray(entry.high)) {
    high = entry.high;
    show = high.filter(stale);
    if (show.length === 0) return null; // cached path: no parse, no write
  } else {
    const { runChecks } = await import("../lib/checks.mjs");
    const found = runChecks(loadScopes({ cwd, home }), { home }).filter((f) => f.severity === "high");
    const prev = new Map(((entry && entry.high) || []).map((h) => [h.key, h]));
    high = found.map((f) => {
      const key = `${f.id}|${f.scope}|${f.rule}`;
      const old = prev.get(key);
      return { key, rule: f.rule, file: f.file, why: f.why, lastShown: old ? old.lastShown : null };
    });
    // First sight of a finding, or a weekly reminder for one already shown.
    show = high.filter((h) => !h.lastShown || stale(h));
  }
  for (const h of show) h.lastShown = iso;

  cache[cwd] = { fingerprint: fp, at: iso, high };
  const keys = Object.keys(cache);
  if (keys.length > MAX_CACHED_CWDS) {
    keys.sort((a, b) => Date.parse(cache[a].at) - Date.parse(cache[b].at));
    for (const k of keys.slice(0, keys.length - MAX_CACHED_CWDS)) delete cache[k];
  }
  fs.mkdirSync(data, { recursive: true });
  writeAtomic(cacheFile, JSON.stringify(cache));

  if (show.length === 0) return null;
  const shown = show.slice(0, room);
  const lines = [`permissions: ${show.length} HIGH-risk permission rule${show.length === 1 ? "" : "s"} in your settings.`];
  for (const h of shown) lines.push(`HIGH ${h.rule} in ${shortPath(h.file, home)}: ${clipWords(h.why, 90)}`);
  if (show.length > shown.length) lines.push(`(+${show.length - shown.length} more)`);
  lines.push("Run /permissions-review for details.");
  return lines.join("\n");
}

// ---- entry ------------------------------------------------------------------

export async function run({ env, home, stdin }) {
  const data = env.CLAUDE_PLUGIN_DATA;
  if (!data) return null;
  let cwd = process.cwd();
  try {
    const j = JSON.parse(stdin || "{}");
    if (j && typeof j.cwd === "string" && j.cwd) cwd = j.cwd;
  } catch {
    /* use process cwd */
  }
  const now = nowMs(env);
  let migration = null;
  try {
    migration = migrate(home, data, now);
  } catch {
    /* migration is best effort */
  }
  let alertText = null;
  try {
    alertText = await alert(cwd, home, data, now, migration ? 2 : 3);
  } catch {
    /* alerting is best effort */
  }
  const parts = [migration, alertText].filter(Boolean);
  return parts.length ? parts.join("\n") : null;
}

async function main() {
  let stdin = "";
  try {
    stdin = fs.readFileSync(0, "utf8");
  } catch {
    /* no stdin */
  }
  const msg = await run({ env: process.env, home: os.homedir(), stdin });
  if (msg) process.stdout.write(JSON.stringify({ systemMessage: msg }) + "\n");
}

if (process.argv[1] && import.meta.url === pathToFileURL(fs.realpathSync(process.argv[1])).href) {
  main().then(
    () => process.exit(0),
    () => process.exit(0),
  );
}
