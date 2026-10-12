// SAFETY: the hook renames files under HOME. Every run here uses a temp HOME and temp
// CLAUDE_PLUGIN_DATA passed explicitly; the real environment is never inherited.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync, spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const HOOK = path.join(HERE, "session-alert.mjs");
const FIX = path.join(HERE, "..", "tests", "fixtures", "alert");
const T0 = Date.parse("2026-10-09T12:00:00Z");
const DAY = 86400000;
let tmp;
let n = 0;

before(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "alert-test-"));
});
after(() => fs.rmSync(tmp, { recursive: true, force: true }));

function sandbox({ high = false, logs = [] } = {}) {
  const base = path.join(tmp, `s${n++}`);
  const home = path.join(base, "home");
  const data = path.join(base, "data");
  const cwd = path.join(base, "proj");
  fs.mkdirSync(path.join(home, ".claude"), { recursive: true });
  fs.mkdirSync(cwd, { recursive: true });
  if (high) fs.copyFileSync(path.join(FIX, "high-home", ".claude", "settings.json"), path.join(home, ".claude", "settings.json"));
  for (const l of logs) fs.writeFileSync(path.join(home, ".claude", l), "line\n".repeat(1000));
  return { home, data, cwd };
}

function hook(sb, { now = T0, env = {}, stdin } = {}) {
  const e = { PATH: process.env.PATH, HOME: sb.home, TMPDIR: os.tmpdir(), ...(sb.data === null ? {} : { CLAUDE_PLUGIN_DATA: sb.data }), PERMISSIONS_NOW: String(now), PERMISSIONS_CACHE_TMP: "1", ...env };
  const r = spawnSync(process.execPath, [HOOK], { env: e, input: stdin ?? JSON.stringify({ cwd: sb.cwd, hook_event_name: "SessionStart" }), encoding: "utf8" });
  assert.equal(r.status, 0, r.stderr);
  return r.stdout;
}

test("migration: moves logs + rotations, writes marker and MOVED.json, one message, second run silent", () => {
  const sb = sandbox({ logs: ["permission-log.jsonl", "prompt-log.jsonl", "sandbox-denials.jsonl", "permission-log.jsonl.1", "other.jsonl"] });
  const out = hook(sb);
  const j = JSON.parse(out);
  assert.deepEqual(Object.keys(j), ["systemMessage"]);
  assert.match(j.systemMessage, /^permissions 3\.0: archived 2\.x logs \(\d+\.\d MB\) to .*legacy-v2; auto-deleted after 30 days\.$/);
  assert.equal(out.trim().split("\n").length, 1);
  const left = fs.readdirSync(path.join(sb.home, ".claude")).sort();
  assert.deepEqual(left, ["other.jsonl"]); // only 2.x log names are touched
  assert.deepEqual(fs.readdirSync(path.join(sb.data, "legacy-v2")).sort(), ["MOVED.json", "permission-log.jsonl", "permission-log.jsonl.1", "prompt-log.jsonl", "sandbox-denials.jsonl"]);
  assert.equal(fs.readFileSync(path.join(sb.data, "schema"), "utf8").trim(), "3");
  const moved = JSON.parse(fs.readFileSync(path.join(sb.data, "legacy-v2", "MOVED.json"), "utf8"));
  assert.equal(moved.moved.length, 4);
  assert.equal(moved.moved[0].bytes, 5000);
  assert.equal(moved.at, new Date(T0).toISOString());
  assert.equal(hook(sb, { now: T0 + 1000 }), "");
  // a 2.x downgrade recreating logs is not re-migrated (marker wins)
  fs.writeFileSync(path.join(sb.home, ".claude", "permission-log.jsonl"), "x\n");
  assert.equal(hook(sb, { now: T0 + 2000 }), "");
  assert.ok(fs.existsSync(path.join(sb.home, ".claude", "permission-log.jsonl")));
});

test("no logs: silent, marker still written", () => {
  const sb = sandbox();
  assert.equal(hook(sb), "");
  assert.equal(fs.readFileSync(path.join(sb.data, "schema"), "utf8").trim(), "3");
  assert.ok(!fs.existsSync(path.join(sb.data, "legacy-v2")));
});

test("missing CLAUDE_PLUGIN_DATA: silent, nothing touched", () => {
  const sb = sandbox({ high: true, logs: ["permission-log.jsonl"] });
  const out = hook({ ...sb, data: null });
  assert.equal(out, "");
  assert.ok(fs.existsSync(path.join(sb.home, ".claude", "permission-log.jsonl")));
});

test("retention: archive deleted only when MOVED.json.at is >= 30 days old; siblings survive", () => {
  const sb = sandbox({ logs: ["permission-log.jsonl"] });
  hook(sb);
  fs.writeFileSync(path.join(sb.data, "keep.txt"), "k");
  assert.equal(hook(sb, { now: T0 + 29 * DAY }), "");
  assert.ok(fs.existsSync(path.join(sb.data, "legacy-v2")));
  assert.equal(hook(sb, { now: T0 + 30 * DAY }), "");
  assert.ok(!fs.existsSync(path.join(sb.data, "legacy-v2")));
  assert.ok(fs.existsSync(path.join(sb.data, "keep.txt")));
  assert.ok(fs.existsSync(path.join(sb.data, "schema")));
});

test("HIGH fixture: stdout is a single JSON object with only systemMessage, <= 5 lines", () => {
  const sb = sandbox({ high: true });
  const out = hook(sb);
  const j = JSON.parse(out);
  assert.deepEqual(Object.keys(j), ["systemMessage"]);
  const lines = j.systemMessage.split("\n");
  assert.ok(lines.length <= 5);
  assert.match(lines[0], /HIGH-risk/);
  assert.ok(lines.some((l) => l.startsWith("HIGH Bash(bash *) in ~/.claude/settings.json")));
  assert.ok(lines.some((l) => l.startsWith("HIGH Bash(python3 *) in ")));
  assert.equal(lines[lines.length - 1], "Run /permissions-review for details.");
});

test("fingerprint unchanged -> silent; weekly re-show; unrelated edit does not re-nag", () => {
  const sb = sandbox({ high: true });
  assert.notEqual(hook(sb), "");
  assert.equal(hook(sb, { now: T0 + 60 * 1000 }), "");
  assert.equal(hook(sb, { now: T0 + 6 * DAY }), "");
  const again = hook(sb, { now: T0 + 7 * DAY });
  assert.match(JSON.parse(again).systemMessage, /HIGH Bash\(bash \*\)/);
  assert.equal(hook(sb, { now: T0 + 7 * DAY + 1000 }), ""); // reset the clock after showing
  // settings change that adds no new HIGH finding: silent
  const f = path.join(sb.home, ".claude", "settings.json");
  const s = JSON.parse(fs.readFileSync(f, "utf8"));
  s.permissions.allow.push("Bash(ls:*)");
  fs.writeFileSync(f, JSON.stringify(s, null, 2));
  assert.equal(hook(sb, { now: T0 + 7 * DAY + 2000 }), "");
  // a new HIGH rule: only the new one is shown
  s.permissions.allow.push("Bash(perl *)");
  fs.writeFileSync(f, JSON.stringify(s, null, 3));
  const j = JSON.parse(hook(sb, { now: T0 + 7 * DAY + 3000 }));
  assert.match(j.systemMessage, /perl/);
  assert.ok(!/bash \*/.test(j.systemMessage));
});

test("cache is keyed by cwd and stays small", () => {
  const sb = sandbox({ high: true });
  hook(sb);
  const other = path.join(path.dirname(sb.cwd), "other");
  fs.mkdirSync(other);
  hook(sb, { stdin: JSON.stringify({ cwd: other }) });
  const cache = JSON.parse(fs.readFileSync(path.join(sb.data, "alert-cache.json"), "utf8"));
  assert.deepEqual(Object.keys(cache).sort(), [other, sb.cwd].sort());
  assert.ok(fs.statSync(path.join(sb.data, "alert-cache.json")).size <= 1024 * 2);
});

test("migration and alert are combined into ONE systemMessage", () => {
  const sb = sandbox({ high: true, logs: ["permission-log.jsonl"] });
  const out = hook(sb);
  assert.equal(out.trim().split("\n").length, 1);
  const j = JSON.parse(out);
  assert.deepEqual(Object.keys(j), ["systemMessage"]);
  assert.match(j.systemMessage, /archived 2\.x logs/);
  assert.match(j.systemMessage, /HIGH Bash\(bash \*\)/);
  assert.ok(j.systemMessage.split("\n").length <= 6);
});

test("garbage stdin and unreadable settings never fail the session", () => {
  const sb = sandbox();
  fs.writeFileSync(path.join(sb.home, ".claude", "settings.json"), "{not json");
  assert.equal(hook(sb, { stdin: "}{ nope" }), "");
});

test("timing smoke: cached path is fast (loose bound; real numbers are in the report)", () => {
  const sb = sandbox({ high: true });
  hook(sb);
  const times = [];
  for (let i = 0; i < 5; i++) {
    const t = process.hrtime.bigint();
    hook(sb, { now: T0 + 1000 });
    times.push(Number(process.hrtime.bigint() - t) / 1e6);
  }
  times.sort((a, b) => a - b);
  assert.ok(times[2] < 250, `p50 ${times[2]} ms`);
});

test("alert text: why cut at a word boundary (<= 90 chars, ellipsis); path ~ under HOME", async () => {
  const { clipWords, shortPath } = await import("./session-alert.mjs");
  const long = "Allows any shell command; auto mode drops this rule at entry because it is far too broad to ever be safe for use";
  const c = clipWords(long, 90);
  assert.ok(c.length <= 90 && c.endsWith("…"), c);
  assert.ok(long.startsWith(c.slice(0, -1)));
  assert.ok(/\s/.test(long[c.length - 1]), "cut lands on a word boundary");
  assert.equal(clipWords("short text", 90), "short text");
  assert.equal(shortPath("/h/.claude/settings.json", "/h"), "~/.claude/settings.json");
  assert.equal(shortPath("/etc/claude-code/managed-settings.json", "/h"), "claude-code/managed-settings.json");
  assert.equal(shortPath(undefined, "/h"), "settings");
  const out = hook(sandbox({ high: true }));
  for (const l of JSON.parse(out).systemMessage.split("\n")) assert.ok(l.length <= 160);
});

test("hooks.json: SessionStart matcher is startup|resume|clear and the command quotes ${CLAUDE_PLUGIN_ROOT}", () => {
  const j = JSON.parse(fs.readFileSync(path.join(HERE, "hooks.json"), "utf8"));
  const entries = j.hooks.SessionStart;
  assert.equal(entries.length, 1);
  assert.equal(entries[0].matcher, "startup|resume|clear");
  const cmd = entries[0].hooks[0].command;
  assert.ok(cmd.includes('"${CLAUDE_PLUGIN_ROOT}/hooks/session-alert.mjs"'), cmd);
});

test("alert cache: scratch cwds are not cached; vanished cwds dropped; LRU by lastSeen; mode 0600; capped", () => {
  const sb = sandbox({ high: true });
  const noSeam = { PERMISSIONS_CACHE_TMP: "" };
  // a cwd under the OS temp dir is evaluated (and alerts) but never written to the cache
  assert.match(hook(sb, { env: noSeam }), /HIGH-risk/);
  assert.ok(!fs.existsSync(path.join(sb.data, "alert-cache.json")));
  // seed 25 entries: 1 nonexistent dir, 24 real ones; entry 0 is the oldest "at" but most recently used
  fs.mkdirSync(sb.data, { recursive: true });
  const seeded = {};
  const dirs = [];
  for (let i = 0; i < 24; i++) {
    const d = path.join(tmp, `seed${n}-${i}`);
    fs.mkdirSync(d, { recursive: true });
    dirs.push(d);
    seeded[d] = { fingerprint: "x", at: new Date(T0 - (100 - i) * 1000).toISOString(), lastSeen: new Date(T0 - (i === 0 ? 1 : 100 - i) * 1000).toISOString(), high: [] };
  }
  seeded[path.join(tmp, "gone-forever")] = { fingerprint: "x", at: new Date(T0).toISOString(), lastSeen: new Date(T0).toISOString(), high: [] };
  fs.writeFileSync(path.join(sb.data, "alert-cache.json"), JSON.stringify(seeded), { mode: 0o644 });
  hook(sb);
  const f = path.join(sb.data, "alert-cache.json");
  const cache = JSON.parse(fs.readFileSync(f, "utf8"));
  assert.ok(!(path.join(tmp, "gone-forever") in cache));
  assert.ok(sb.cwd in cache);
  assert.ok(Object.keys(cache).length <= 20, String(Object.keys(cache).length));
  assert.ok(dirs[0] in cache, "recently used entry survives despite an old 'at'");
  assert.ok(!(dirs[1] in cache), "least recently used entry evicted");
  assert.equal(fs.statSync(f).mode & 0o777, 0o600);
});

test("alert cache: plugin upgrade (version in the key) re-evaluates; cached text names the file", () => {
  const sb = sandbox({ high: true });
  hook(sb);
  const f = path.join(sb.data, "alert-cache.json");
  const cache = JSON.parse(fs.readFileSync(f, "utf8"));
  const e = cache[sb.cwd];
  assert.ok(e.high.every((h) => h.file && h.file.startsWith(sb.home)));
  assert.equal(hook(sb, { now: T0 + 1000 }), ""); // unchanged: silent
  e.fingerprint = "from-an-older-plugin-version";
  e.high = e.high.map((h) => ({ ...h, lastShown: new Date(T0).toISOString() }));
  fs.writeFileSync(f, JSON.stringify(cache));
  hook(sb, { now: T0 + 2000 }); // re-evaluates rather than trusting the stale entry
  assert.notEqual(JSON.parse(fs.readFileSync(f, "utf8"))[sb.cwd].fingerprint, "from-an-older-plugin-version");
});

test("migration crash safety: MOVED.json absent -> archive aged by directory mtime; stale lock is broken", () => {
  const sb = sandbox();
  fs.mkdirSync(path.join(sb.data, "legacy-v2"), { recursive: true });
  fs.writeFileSync(path.join(sb.data, "legacy-v2", "permission-log.jsonl"), "x");
  const old = new Date(T0 - 40 * DAY);
  fs.utimesSync(path.join(sb.data, "legacy-v2"), old, old);
  fs.writeFileSync(path.join(sb.data, "schema"), "3\n");
  hook(sb);
  assert.ok(!fs.existsSync(path.join(sb.data, "legacy-v2")));
  // a lock left by a dead process (older than 30 s) does not block migration
  const sb2 = sandbox({ logs: ["permission-log.jsonl"] });
  fs.mkdirSync(path.join(sb2.data, "migrate.lock"), { recursive: true });
  const t = new Date(Date.now() - 120000);
  fs.utimesSync(path.join(sb2.data, "migrate.lock"), t, t);
  assert.match(hook(sb2), /archived 2\.x logs/);
  assert.ok(!fs.existsSync(path.join(sb2.data, "migrate.lock")));
  // a fresh lock means someone else is migrating: skip silently, leave files, no marker
  const sb3 = sandbox({ logs: ["permission-log.jsonl"] });
  fs.mkdirSync(path.join(sb3.data, "migrate.lock"), { recursive: true });
  assert.equal(hook(sb3), "");
  assert.ok(fs.existsSync(path.join(sb3.home, ".claude", "permission-log.jsonl")));
  assert.ok(!fs.existsSync(path.join(sb3.data, "schema")));
});

test("migration never overwrites an existing archive file", () => {
  const sb = sandbox({ logs: ["permission-log.jsonl"] });
  fs.mkdirSync(path.join(sb.data, "legacy-v2"), { recursive: true });
  fs.writeFileSync(path.join(sb.data, "legacy-v2", "permission-log.jsonl"), "OLD");
  hook(sb);
  assert.equal(fs.readFileSync(path.join(sb.data, "legacy-v2", "permission-log.jsonl"), "utf8"), "OLD");
  const names = fs.readdirSync(path.join(sb.data, "legacy-v2")).filter((x) => x.startsWith("permission-log.jsonl"));
  assert.equal(names.length, 2);
});

function runAsync(sb) {
  return new Promise((resolve) => {
    const e = { PATH: process.env.PATH, HOME: sb.home, CLAUDE_PLUGIN_DATA: sb.data, PERMISSIONS_NOW: String(T0), PERMISSIONS_CACHE_TMP: "1" };
    const c = spawn(process.execPath, [HOOK], { env: e });
    let out = "";
    c.stdout.on("data", (d) => (out += d));
    c.stdin.end(JSON.stringify({ cwd: sb.cwd }));
    c.on("close", (code) => resolve({ code, out }));
  });
}

test("migration race: 5 concurrent first-run hooks x 20 rounds -> MOVED.json lists every file, <= 1 message", async () => {
  const names = ["permission-log.jsonl", "prompt-log.jsonl", "sandbox-denials.jsonl"];
  for (let round = 0; round < 20; round++) {
    const all = [...names, ...names.flatMap((x) => [1, 2, 3, 4, 5, 6].map((i) => `${x}.${i}`))];
    const sb = sandbox({ logs: all });
    const rs = await Promise.all([1, 2, 3, 4, 5].map(() => runAsync(sb)));
    assert.ok(rs.every((r) => r.code === 0));
    const msgs = rs.filter((r) => r.out.includes("archived 2.x logs")).length;
    assert.equal(msgs, 1, `round ${round}: ${msgs} migration messages`);
    const arch = fs.readdirSync(path.join(sb.data, "legacy-v2")).filter((x) => x !== "MOVED.json").sort();
    assert.deepEqual(arch, [...all].sort(), `round ${round}: archive incomplete`);
    const listed = JSON.parse(fs.readFileSync(path.join(sb.data, "legacy-v2", "MOVED.json"), "utf8")).moved.map((m) => m.file).sort();
    assert.deepEqual(listed, arch, `round ${round}: MOVED.json incomplete`);
    assert.equal(fs.readdirSync(path.join(sb.home, ".claude")).filter((x) => x.includes("-log")).length, 0);
    assert.ok(!fs.existsSync(path.join(sb.data, "migrate.lock")));
  }
});
