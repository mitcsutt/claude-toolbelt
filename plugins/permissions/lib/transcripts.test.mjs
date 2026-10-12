import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { scanTranscripts, redact, commandHead, classifyPermissionRule, READER_VERSION } from "./transcripts.mjs";

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
  assert.deepEqual(s.headlessDenied, [{ tool: "Bash", head: "claude", count: 1, last: "2026-10-08T10:06:01.000Z" }]);
});

test("toolCounts include mcp tools, dedupe repeated tool_use ids, ignore out-of-window records", async () => {
  const s = await scanTranscripts({ projectsDir: tmp, now: NOW });
  assert.equal(s.toolCounts.Bash, 6); // curl, git status (dup collapsed), npm, claude, git commit, sub git status -> 6
  assert.equal(s.toolCounts.Edit, 2);
  assert.equal(s.toolCounts.AskUserQuestion, 1);
  assert.equal(s.toolCounts.mcp__srv__create_thing, 1);
  assert.equal(s.toolCounts.Bash, 6); // the out-of-window old-out-of-window-command is not counted
  assert.ok(!("bashCommands" in s));
});

test("secrets never appear anywhere in output; rejection head skips env assignments", async () => {
  const s = await scanTranscripts({ projectsDir: tmp, now: NOW, sandboxEnabled: true });
  const json = JSON.stringify(s);
  assert.ok(!json.includes("sk-test-123"));
  assert.ok(!json.includes("abc123secret"));
  assert.equal(s.rejected[0].head, "npm publish");
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

const DENIALS = path.join(FIXTURES, "..", "transcripts-denials");
const withDir = async (src, fn) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "transcripts-den-"));
  try {
    copyDir(src, dir);
    const t = new Date("2026-10-08T12:00:00Z");
    const walkT = (d) => {
      for (const e of fs.readdirSync(d, { withFileTypes: true })) {
        const p = path.join(d, e.name);
        if (e.isDirectory()) walkT(p);
        else fs.utimesSync(p, t, t);
      }
    };
    walkT(dir);
    return await fn(dir);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
};
// a minimal stand-in for scan.mjs makeAttribute: one deny rule, rm only
const attr = (tool, detail) => (tool === "Bash" && /\brm -rf\b/.test(detail) ? { decision: "deny", rule: "Bash(rm -rf:*)", scope: "user", tag: /\/tmp\//.test(detail) ? "temp" : "other" } : tool === "Read" && detail.includes(".ssh") ? { decision: "deny", rule: "Read(~/.ssh/**)", scope: "user" } : { decision: "unmatched" });

test("denials: hook blocks, built-in checks and deny rules are classified from the result text", async () => {
  await withDir(DENIALS, async (dir) => {
    const s = await scanTranscripts({ projectsDir: dir, now: NOW, attribute: attr });
    const by = (k, v) => s.denials.filter((g) => g.kind === k && (v === undefined || g.hook === v || g.check === v || g.rule === v));
    assert.equal(by("hook", "public-repo-guard.py")[0].count, 1);
    assert.equal(by("hook", "block-destructive-git.sh")[0].count, 1); // "bash /path/x.sh" -> script basename
    assert.equal(by("builtin", "safety-check")[0].count, 1);
    assert.equal(by("builtin", "path-check")[0].count, 1);
    // hook/builtin hits are never credited to a deny rule
    const rm = by("deny-rule", "Bash(rm -rf:*)")[0];
    assert.equal(rm.count, 3); // two plain + the long one
    assert.equal(rm.scope, "user");
    assert.equal(by("deny-rule", "Read(~/.ssh/**)")[0].count, 1);
    assert.equal(s.denials.reduce((n, g) => n + g.count, 0), 8);
  });
});

test("denials: exact attribution even when the triggering segment is beyond char 400; per-mode counts; samples capped", async () => {
  await withDir(DENIALS, async (dir) => {
    const seen = [];
    const s = await scanTranscripts({ projectsDir: dir, now: NOW, attribute: (t, d, c) => (seen.push(d), attr(t, d, c)) });
    assert.ok(seen.some((d) => d.length > 450 && d.indexOf("rm -rf") > 400), "attribute sees the FULL command");
    const rm = s.denials.find((g) => g.rule === "Bash(rm -rf:*)");
    assert.deepEqual(rm.tags, { temp: 3 });
    assert.deepEqual(rm.modes, { bypassPermissions: 3 });
    assert.ok(rm.samples.length <= 3 && rm.samples.every((x) => x.length <= 161));
    const hook = s.denials.find((g) => g.kind === "hook" && g.hook === "public-repo-guard.py");
    assert.deepEqual(hook.modes, { default: 1 });
    assert.equal(attrCalls(seen), 3); // memoized: the long command, `rm -rf /tmp/a` (twice, attributed once), Read
  });
});
const attrCalls = (seen) => seen.length;

test("denials: a rule that no longer exists is reported with rule null and a note", async () => {
  await withDir(DENIALS, async (dir) => {
    const s = await scanTranscripts({ projectsDir: dir, now: NOW, attribute: () => ({ decision: "unmatched" }) });
    const g = s.denials.filter((x) => x.kind === "deny-rule");
    assert.ok(g.length >= 1 && g.every((x) => x.rule === null && x.note === "rule no longer present"));
    assert.equal(g.reduce((n, x) => n + x.count, 0), 4);
  });
});

test("classifyPermissionRule on real texts (observed 2026-10-12)", () => {
  assert.deepEqual(classifyPermissionRule("PreToolUse:Bash hook error: [~/.claude/hooks/public-repo-guard.py]: Blocked by x"), { kind: "hook", hook: "public-repo-guard.py" });
  assert.deepEqual(classifyPermissionRule('PreToolUse:Bash hook error: [[ -z "${A:-}" ] || exit 0; exec "$CLAUDE_PROJECT_DIR"/bin/fm-cd-pretool-check.sh --claude]: {"x":1}'), { kind: "hook", hook: "fm-cd-pretool-check.sh" });
  assert.deepEqual(classifyPermissionRule("Permission for this command was denied by a built-in Claude Code safety check, not by the user."), { kind: "builtin", check: "safety-check" });
  assert.equal(classifyPermissionRule("Permission to use Bash with command rm -rf x has been denied.").kind, "deny-rule");
  assert.equal(classifyPermissionRule("Permission to use Read has been denied.").kind, "deny-rule");
  assert.deepEqual(classifyPermissionRule("cat reads 'a.js' after a cd whose target cannot be resolved, and a deny rule (~/.ssh/**) names that file"), { kind: "builtin", check: "path-check" });
  // a command that merely QUOTES hook text is still a deny rule hit
  assert.equal(classifyPermissionRule("Permission to use Bash with command echo PreToolUse:Bash hook error: [x]: has been denied.").kind, "deny-rule");
  assert.equal(classifyPermissionRule("Can't interact with browser-internal URLs").check, "other");
});

test("commandHead: first real command, never cd/assignments/&&", () => {
  assert.equal(commandHead("cd x && git config user.email a"), "git config");
  assert.equal(commandHead("U=~/.claude/settings.json; T=$(mktemp); jq . > $T && mv $T $U"), "jq");
  assert.equal(commandHead('N="a b c"; x y'), "x");
  assert.equal(commandHead("export A=1; E=x; mkdir -p $E; chrome-devtools-axi open"), "mkdir");
  assert.equal(commandHead("T=/var/folders/a; W=$PWD; for d in a b; do ln -s x y; done"), "ln");
  assert.equal(commandHead("&& echo hi"), "echo");
  assert.ok(!commandHead("MY_TOKEN=abc rm -rf z").includes("abc"));
});

test("headless denial carries the command head", async () => {
  const s = await scanTranscripts({ projectsDir: tmp, now: NOW });
  assert.equal(s.headlessDenied[0].head, "claude");
});

const line = (o) => JSON.stringify(o) + "\n";
const use = (id, command, cwd, ts) => line({ type: "assistant", cwd, sessionId: "s", timestamp: ts, message: { role: "assistant", content: [{ type: "tool_use", id, name: "Bash", input: { command } }] } });
const denied = (id, ts) => line({ type: "user", sessionId: "s", timestamp: ts, toolDenialKind: "permission-rule", message: { role: "user", content: [{ type: "tool_result", tool_use_id: id, is_error: true, content: "Permission to use Bash has been denied." }] } });

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
    assert.ok(after.denials.length > plain.denials.length || after.toolCounts.Bash > plain.toolCounts.Bash);
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

test("redact: -p passwords, --password, URL passwords with slash, aws secret (audit B3)", () => {
  const cases = [
    ["mysql -u root -pHunter2 db", "Hunter2"],
    ["mysql -u root -p Hunter2 db", "Hunter2"],
    ["sshpass -p hunter2 ssh h", "hunter2"],
    ["docker login -u me -p hunter2 reg.io", "hunter2"],
    ["curl --password hunter2 x", "hunter2"],
    ["curl --password=hunter2 x", "hunter2"],
    ['x --password "two words" y', "two words"],
    ["psql postgres://u:pa/ss@host/db", "pa/ss"],
    ["aws configure set aws_secret_access_key wJalrXUtnFEMI", "wJalrXUtnFEMI"],
    ['curl --data "password=hunter2" x', "hunter2"],
  ];
  for (const [input, secret] of cases) assert.ok(!redact(input).includes(secret), `${input} -> ${redact(input)}`);
  // closing quote survives; ports and ordinary prose are left alone
  assert.equal(redact('--data "password=hunter2" x'), '--data "password=[REDACTED]" x');
  assert.equal(redact("curl https://example.com:8080/path?e=a@b.c"), "curl https://example.com:8080/path?e=a@b.c");
  assert.equal(redact("Basic usage of Token handling"), "Basic usage of Token handling");
  assert.equal(redact("mysql --port 3306 -h x"), "mysql --port 3306 -h x");
});

test("cache (audit B4): a file with records older than the window is a cache hit, not rewritten each run", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "transcripts-b4-"));
  try {
    fs.mkdirSync(path.join(dir, "p"));
    const f = path.join(dir, "p", "s.jsonl");
    fs.writeFileSync(f, use("o1", "old-thing", "/w", "2026-08-01T10:00:00.000Z") + use("n1", "new-thing", "/w", "2026-10-08T10:00:00.000Z") + denied("n1", "2026-10-08T10:00:01.000Z"));
    fs.utimesSync(f, new Date("2026-10-08T12:00:00Z"), new Date("2026-10-08T12:00:00Z"));
    const cacheDir = path.join(dir, "cache");
    const a = await scanTranscripts({ projectsDir: path.join(dir, "p"), now: NOW, cacheDir });
    assert.equal(a.toolCounts.Bash, 1);
    const cf = path.join(cacheDir, "cache.json");
    const m1 = fs.statSync(cf).mtimeMs;
    await new Promise((r) => setTimeout(r, 30));
    const b = await scanTranscripts({ projectsDir: path.join(dir, "p"), now: new Date(NOW.getTime() + 1000), cacheDir });
    assert.deepEqual({ ...b, ms: 0 }, { ...a, ms: 0 });
    assert.equal(fs.statSync(cf).mtimeMs, m1, "cache.json must not be rewritten on a hit");
    // a wider window reuses the same cached records and now sees the old one
    const wide = await scanTranscripts({ projectsDir: path.join(dir, "p"), now: NOW, days: 90, cacheDir });
    assert.equal(wide.toolCounts.Bash, 2);
    assert.equal(fs.statSync(cf).mtimeMs, m1);
    assert.equal(JSON.parse(fs.readFileSync(cf, "utf8")).v, READER_VERSION);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("cache files are mode 0600 (audit E7)", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "transcripts-mode-"));
  try {
    fs.mkdirSync(path.join(dir, "p"));
    const f = path.join(dir, "p", "s.jsonl");
    fs.writeFileSync(f, use("n1", "new-thing", "/w", "2026-10-08T10:00:00.000Z"));
    fs.utimesSync(f, new Date("2026-10-08T12:00:00Z"), new Date("2026-10-08T12:00:00Z"));
    const cacheDir = path.join(dir, "cache");
    await scanTranscripts({ projectsDir: path.join(dir, "p"), now: NOW, cacheDir });
    assert.equal(fs.statSync(path.join(cacheDir, "cache.json")).mode & 0o777, 0o600);
    assert.deepEqual(fs.readdirSync(cacheDir), ["cache.json"]); // no tmp leftovers
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("lines over 5 MB are skipped and counted, neighbours still read", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "transcripts-long-"));
  try {
    fs.mkdirSync(path.join(dir, "p"));
    const f = path.join(dir, "p", "s.jsonl");
    const T = "2026-10-08T10:00:00.000Z";
    const huge = line({ type: "assistant", timestamp: T, pad: "x".repeat(5 * 1024 * 1024 + 10), message: { role: "assistant", content: [{ type: "tool_use", id: "h1", name: "Bash", input: { command: "huge" } }] } });
    fs.writeFileSync(f, use("a1", "before", "/w", T) + huge + use("a2", "after", "/w", T));
    fs.utimesSync(f, new Date(T), new Date(T));
    const s = await scanTranscripts({ projectsDir: path.join(dir, "p"), now: NOW });
    assert.equal(s.skippedLongLines, 1);
    assert.equal(s.toolCounts.Bash, 2);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("sandbox denials carry the command head", async () => {
  const s = await scanTranscripts({ projectsDir: tmp, now: NOW, sandboxEnabled: true });
  assert.equal(s.sandboxDenials[0].head, "git");
});

test("commandHead has no catastrophic backtracking on long assignment values (real-data hang, 2026-10-12)", () => {
  const t = Date.now();
  assert.equal(commandHead("S=/private/tmp/claude-501/-Users-x-Projects/eb12e81d-bb9d-4483-94b1-bebf90e297ac/scratchpad/spike-charts; mkdir -p $S"), "mkdir");
  assert.ok(Date.now() - t < 500);
});
