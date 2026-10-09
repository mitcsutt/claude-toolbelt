import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { scanTranscripts, redact } from "./transcripts.mjs";

const FIXTURES = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "tests", "fixtures", "transcripts");
const NOW = new Date("2026-10-09T12:00:00.000Z");
let tmp;

function copyDir(src, dst) {
  fs.mkdirSync(dst, { recursive: true });
  for (const e of fs.readdirSync(src, { withFileTypes: true })) {
    const s = path.join(src, e.name);
    const d = path.join(dst, e.name);
    if (e.isDirectory()) copyDir(s, d);
    else fs.copyFileSync(s, d);
  }
}

before(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "transcripts-"));
  copyDir(FIXTURES, tmp);
  // mtimes are not stable in git checkouts: pin them relative to NOW.
  const recent = new Date("2026-10-08T12:00:00.000Z");
  const stale = new Date("2026-07-01T00:00:00.000Z");
  const walk = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p);
      else fs.utimesSync(p, e.name === "stale.jsonl" ? stale : recent, e.name === "stale.jsonl" ? stale : recent);
    }
  };
  walk(tmp);
});

after(() => fs.rmSync(tmp, { recursive: true, force: true }));

test("window counts files, subagent files, and excludes stale-mtime files", async () => {
  const s = await scanTranscripts({ projectsDir: tmp, now: NOW });
  assert.equal(s.window.days, 30);
  assert.equal(s.window.files, 3);
  assert.equal(s.window.subagentFiles, 1);
  assert.ok(s.window.oldestTimestamp >= "2026-09-09");
  assert.equal(s.window.newestTimestamp, "2026-10-08T11:00:03.000Z");
  assert.ok(!("stale-mode" in s.modes));
  assert.equal(typeof s.ms, "number");
});

test("modes come from permission-mode records and user.permissionMode, per session", async () => {
  const s = await scanTranscripts({ projectsDir: tmp, now: NOW });
  assert.deepEqual(s.modes, { auto: 2, bypassPermissions: 1, dontAsk: 1 });
});

test("classifier blocks group by reason, with redacted heads and subagent records", async () => {
  const s = await scanTranscripts({ projectsDir: tmp, now: NOW });
  const by = Object.fromEntries(s.classifierBlocks.map((b) => [b.reason, b]));
  assert.equal(by["Self-Modification"].count, 2);
  assert.deepEqual(by["Self-Modification"].heads, ["Edit"]);
  assert.equal(by["Self-Modification"].last, "2026-10-08T11:00:03.000Z");
  assert.equal(by["Credential Exposure"].count, 1);
  assert.deepEqual(by["Credential Exposure"].heads, ["curl"]);
  assert.equal(s.classifierBlocks[0].reason, "Self-Modification"); // sorted by count
});

test("user rejection is recorded with tool+head; env-assignment secret skipped; AskUserQuestion excluded", async () => {
  const s = await scanTranscripts({ projectsDir: tmp, now: NOW });
  assert.equal(s.rejected.length, 1);
  assert.deepEqual(s.rejected[0], { tool: "Bash", head: "npm publish", count: 1, last: "2026-10-08T10:04:05.000Z" });
  assert.ok(!s.rejected.some((r) => r.tool === "AskUserQuestion"));
});

test("headless not-granted is not counted as a user rejection", async () => {
  const s = await scanTranscripts({ projectsDir: tmp, now: NOW });
  assert.deepEqual(s.headlessDenied, [{ tool: "Bash", count: 1, last: "2026-10-08T10:06:01.000Z" }]);
});

test("toolCounts include mcp tools, dedupe repeated tool_use ids, ignore out-of-window records", async () => {
  const s = await scanTranscripts({ projectsDir: tmp, now: NOW });
  assert.equal(s.toolCounts.Bash, 6); // curl, git status (dup collapsed), npm, claude, git commit, sub git status -> 6
  assert.equal(s.toolCounts.Edit, 2);
  assert.equal(s.toolCounts.AskUserQuestion, 1);
  assert.equal(s.toolCounts.mcp__srv__create_thing, 1);
  const cmds = s.bashCommands.map((c) => c.command);
  assert.ok(!cmds.includes("old-out-of-window-command"));
  assert.equal(s.bashCommands.find((c) => c.command === "git status").count, 2);
});

test("bash commands are first-line normalized and secrets never appear anywhere in output", async () => {
  const s = await scanTranscripts({ projectsDir: tmp, now: NOW, sandboxEnabled: true });
  const json = JSON.stringify(s);
  assert.ok(!json.includes("sk-test-123"));
  assert.ok(!json.includes("abc123secret"));
  assert.ok(s.bashCommands.some((c) => c.command.startsWith("curl -H") && c.command.includes("[REDACTED]")));
  const npm = s.bashCommands.find((c) => c.command.includes("npm publish"));
  assert.ok(npm && !npm.command.includes("second line") && npm.command.includes("API_TOKEN=[REDACTED]"));
});

test("garbage line counts a parse error; unknown-type line counts unknown, no throw", async () => {
  const s = await scanTranscripts({ projectsDir: tmp, now: NOW });
  assert.equal(s.parseErrors, 1);
  assert.equal(s.unknownLines, 1);
});

test("sandbox denials only when sandboxEnabled", async () => {
  const off = await scanTranscripts({ projectsDir: tmp, now: NOW });
  assert.deepEqual(off.sandboxDenials, []);
  const on = await scanTranscripts({ projectsDir: tmp, now: NOW, sandboxEnabled: true });
  assert.equal(on.sandboxDenials.length, 1);
  assert.equal(on.sandboxDenials[0].signature, "git-rename");
  assert.equal(on.sandboxDenials[0].path, "/repo/.git/objects/tmp_x");
  assert.equal(on.sandboxDenials[0].count, 1);
});

test("missing projectsDir yields an empty summary rather than throwing", async () => {
  const s = await scanTranscripts({ projectsDir: path.join(tmp, "nope"), now: NOW });
  assert.equal(s.window.files, 0);
  assert.deepEqual(s.modes, {});
});

test("days narrows the window by record timestamp", async () => {
  // cutoff = 10:58:44; file mtimes (12:00) pass, but only the 11:00 subagent records are in-window
  const s = await scanTranscripts({ projectsDir: tmp, now: new Date("2026-10-08T11:00:10.000Z"), days: 0.001 });
  assert.equal(s.classifierBlocks.length, 1);
  assert.equal(s.classifierBlocks[0].count, 1);
  assert.deepEqual(s.rejected, []);
});

test("redact removes common secret shapes", () => {
  assert.ok(!redact("curl -u admin:hunter2 https://x").includes("hunter2"));
  assert.ok(!redact("x ghp_abcdefghijklmnop").includes("ghp_abc"));
  assert.ok(!redact("git clone https://user:pw123@host/repo").includes("pw123"));
});

test("ruleDenied counts deny-rule hits by tool+head; cwds counted per bash command", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "transcripts-rules-"));
  try {
    copyDir(path.join(FIXTURES, "..", "transcripts-rules"), dir);
    const f = path.join(dir, "-proj-d", "sess-4.jsonl");
    fs.utimesSync(f, new Date("2026-10-08T12:00:00Z"), new Date("2026-10-08T12:00:00Z"));
    const s = await scanTranscripts({ projectsDir: dir, now: NOW });
    assert.deepEqual(s.ruleDenied[0], { tool: "Bash", head: "rm", count: 2, last: "2026-10-08T10:01:01.000Z", samples: ["rm -rf /tmp/x"], sampleCounts: [2] });
    assert.deepEqual(s.ruleDenied[1], { tool: "Read", head: "", count: 1, last: "2026-10-08T10:02:01.000Z", samples: [], sampleCounts: [] });
    assert.deepEqual(s.rejected, []);
    assert.equal(s.bashCommands[0].cwds, 2);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

const line = (o) => JSON.stringify(o) + "\n";
const use = (id, command, cwd, ts) => line({ type: "assistant", cwd, sessionId: "s", timestamp: ts, message: { role: "assistant", content: [{ type: "tool_use", id, name: "Bash", input: { command } }] } });
const denied = (id, ts) => line({ type: "user", sessionId: "s", timestamp: ts, toolDenialKind: "permission-rule", message: { role: "user", content: [{ type: "tool_result", tool_use_id: id, is_error: true, content: "denied" }] } });

test("ruleDenied head is the first real segment (never &&); samples keep later lines and are redacted", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "transcripts-head-"));
  try {
    fs.mkdirSync(path.join(dir, "-p"));
    const f = path.join(dir, "-p", "s.jsonl");
    const T = "2026-10-08T10:00:00.000Z";
    fs.writeFileSync(
      f,
      use("a1", "cd x\n&& rm -rf /tmp/y MY_TOKEN=abc", "/w", T) + denied("a1", T) +
        use("a2", "&& echo hi", "/w", T) + denied("a2", T) +
        use("a3", "cd x && rm -rf z", "/w", T) + denied("a3", T),
    );
    fs.utimesSync(f, new Date(T), new Date(T));
    const s = await scanTranscripts({ projectsDir: dir, now: NOW });
    const heads = s.ruleDenied.map((r) => r.head).sort();
    assert.deepEqual(heads, ["cd", "echo"]);
    const cd = s.ruleDenied.find((r) => r.head === "cd");
    assert.equal(cd.count, 2);
    assert.ok(cd.samples.some((x) => x.includes("rm -rf /tmp/y")));
    assert.ok(!JSON.stringify(s).includes("abc"));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("bashCommands carry topCwd (most calls)", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "transcripts-top-"));
  try {
    fs.mkdirSync(path.join(dir, "-p"));
    const f = path.join(dir, "-p", "s.jsonl");
    const T = "2026-10-08T10:00:00.000Z";
    fs.writeFileSync(f, use("b1", "make x", "/a", T) + use("b2", "make x", "/b", T) + use("b3", "make x", "/b", T));
    fs.utimesSync(f, new Date(T), new Date(T));
    const s = await scanTranscripts({ projectsDir: dir, now: NOW });
    assert.equal(s.bashCommands[0].topCwd, "/b");
    assert.equal(s.bashCommands[0].cwds, 2);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("cache: identical results with/without it, invalidated by mtime, never written without cacheDir", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "transcripts-cache-"));
  try {
    copyDir(FIXTURES, path.join(dir, "p"));
    copyDir(path.join(FIXTURES, "..", "transcripts-rules"), path.join(dir, "p"));
    const recent = new Date("2026-10-08T12:00:00.000Z");
    const touchAll = (d) => {
      for (const e of fs.readdirSync(d, { withFileTypes: true })) {
        const p = path.join(d, e.name);
        if (e.isDirectory()) touchAll(p);
        else if (e.name !== "stale.jsonl") fs.utimesSync(p, recent, recent);
      }
    };
    touchAll(path.join(dir, "p"));
    const cacheDir = path.join(dir, "cache");
    const strip = (s) => ({ ...s, ms: 0 });
    const opts = { projectsDir: path.join(dir, "p"), now: NOW, sandboxEnabled: true };

    const plain = strip(await scanTranscripts(opts));
    assert.ok(!fs.existsSync(cacheDir));
    const first = strip(await scanTranscripts({ ...opts, cacheDir }));
    assert.ok(fs.existsSync(path.join(cacheDir, "cache.json")));
    const second = strip(await scanTranscripts({ ...opts, cacheDir }));
    assert.deepEqual(first, plain);
    assert.deepEqual(second, plain);
    // a different window (cutoff) must not return stale data
    const narrow = { ...opts, days: 1, now: new Date("2026-10-09T12:00:00.000Z") };
    assert.deepEqual(strip(await scanTranscripts({ ...narrow, cacheDir })), strip(await scanTranscripts(narrow)));
    await scanTranscripts({ ...opts, cacheDir });

    // mtime change invalidates just that entry
    const f = path.join(dir, "p", "-proj-d", "sess-4.jsonl");
    const T = "2026-10-08T11:00:00.000Z";
    fs.appendFileSync(f, use("z1", "extra-cmd --go", "/z", T) + denied("z1", T));
    fs.utimesSync(f, new Date("2026-10-08T13:00:00Z"), new Date("2026-10-08T13:00:00Z"));
    const after = strip(await scanTranscripts({ ...opts, cacheDir }));
    assert.deepEqual(after, strip(await scanTranscripts(opts)));
    assert.notDeepEqual(after, plain);
    assert.ok(after.bashCommands.some((b) => b.command === "extra-cmd --go"));
    // a stale entry for a file that left the window is pruned
    fs.rmSync(f);
    await scanTranscripts({ ...opts, cacheDir });
    assert.ok(!fs.readFileSync(path.join(cacheDir, "cache.json"), "utf8").includes("sess-4.jsonl"));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("redact: bare secret-named assignments are redacted", () => {
  for (const s of ["TOKEN=abc123 gh api x", "KEY='abc 123' node x", "export PASSWORD=hunter2", "MY_TOKEN=abc123 curl x"]) {
    const out = redact(s);
    for (const secret of ["abc123", "abc 123", "hunter2"]) assert.ok(!out.includes(secret), `${s} -> ${out}`);
  }
  assert.equal(redact("MONKEY=1"), "MONKEY=[REDACTED]"); // over-redaction is the safe failure mode
});
