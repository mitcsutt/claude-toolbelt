import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { main, capReport, collapseD6, resolveDataDir, parseArgs, clip, fixLegacy, MAX_BYTES, buildReport } from "./scan.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIX = path.join(HERE, "..", "tests", "fixtures", "scan");
const LAUNCHER = path.join(HERE, "..", "bin", "perm-scan");
let tmp;

before(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "scan-test-"));
});
after(() => fs.rmSync(tmp, { recursive: true, force: true }));

let n = 0;
function mkHome(settings, { copyFixture = false } = {}) {
  const home = path.join(tmp, `home${n++}`);
  fs.mkdirSync(path.join(home, ".claude"), { recursive: true });
  if (copyFixture) fs.copyFileSync(path.join(FIX, "home", ".claude", "settings.json"), path.join(home, ".claude", "settings.json"));
  else if (settings) fs.writeFileSync(path.join(home, ".claude", "settings.json"), JSON.stringify(settings));
  return home;
}

async function run(argv, home, env = {}) {
  let out = "";
  let err = "";
  const code = await main(argv, { env: { ...env }, home, out: (s) => (out += s), err: (s) => (err += s) });
  return { code, out, err };
}

function writeTranscript(home, lines) {
  const dir = path.join(home, ".claude", "projects", "-proj");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "s1.jsonl"), lines.map((l) => JSON.stringify(l)).join("\n") + "\n");
}
let tid = 0;
function toolUse(name, input, cwd) {
  return { type: "assistant", cwd, sessionId: "s1", timestamp: new Date().toISOString(), message: { role: "assistant", content: [{ type: "tool_use", id: `t${tid++}`, name, input }] } };
}
function bash(cmd, times, cwd = "/work/a") {
  return Array.from({ length: times }, () => toolUse("Bash", { command: cmd }, cwd));
}

test("json shape: settings-only", async () => {
  const home = mkHome(null, { copyFixture: true });
  const { code, out } = await run(["--json", "--settings-only", "--cwd", path.join(tmp, "nowhere")], home);
  assert.equal(code, 0);
  const r = JSON.parse(out);
  assert.equal(r.version, "3.1");
  assert.equal(r.hint, "For new allow rules use /fewer-permission-prompts.");
  assert.equal(typeof r.cwd, "string");
  assert.deepEqual(Object.keys(r.scopes[0]).sort(), ["counts", "exists", "file", "name"]);
  const user = r.scopes.find((s) => s.name === "user");
  assert.deepEqual(user.counts, { allow: 3, deny: 2, ask: 1 });
  assert.ok(Array.isArray(r.findings));
  assert.equal(r.friction, null);
  assert.equal(r.truncated, false);
  assert.equal(typeof r.ms, "number");
  assert.ok(!("window" in r));
});

test("never prints env values (json, text, check)", async () => {
  const home = mkHome(null, { copyFixture: true });
  for (const argv of [["--json", "--settings-only"], ["--settings-only"], ["check", "echo hi"], ["check", "--json", "printenv"]]) {
    const { out, err } = await run(argv, home);
    assert.ok(out.length > 0);
    assert.ok(!out.includes("topsecretvalue123") && !err.includes("topsecretvalue123"));
  }
});

test("hard 8 KB cap with 500 findings: truncated:true, sorted by severity", async () => {
  const allow = Array.from({ length: 500 }, (_, i) => `Bash(kill -9 ${10000 + i})`);
  allow.push("Bash(curl:*)", "Bash(python3 *)");
  const home = mkHome({ permissions: { allow } });
  const { out } = await run(["--json", "--settings-only"], home);
  assert.ok(Buffer.byteLength(out.trim()) <= MAX_BYTES, `size ${out.length}`);
  const r = JSON.parse(out);
  assert.equal(typeof r.truncated, "object");
  assert.ok(r.truncated.findings > 0 && !r.findings.some((f) => f.scope === "*"), "no +N more pseudo-findings");
  const rank = { high: 0, med: 1, low: 2, dead: 3, info: 4 };
  const ranks = r.findings.map((f) => rank[f.severity]);
  assert.deepEqual(ranks, [...ranks].sort((a, b) => a - b));
  assert.equal(r.findings[0].severity, "high"); // the important ones survive the cut
  assert.ok(r.findings.some((f) => f.id === "D7"));
});

test("capReport fits tiny budgets by dropping from the tail", () => {
  const full = {
    cwd: "/x", scopes: [], friction: null, ms: 1,
    findings: Array.from({ length: 50 }, (_, i) => ({ id: "D7", severity: "dead", scope: "user", rule: `r${i}`, why: "w".repeat(100) })),
  };
  const r = capReport(full, 600);
  assert.ok(Buffer.byteLength(JSON.stringify(r)) <= 600);
  assert.ok(r.truncated && r.truncated.findings > 0);
});

test("D6 collapses per covering rule", () => {
  const mk = (i) => ({ id: "D6", severity: "dead", scope: "user", rule: `Bash(x${i}:*)`, why: "Already covered by broader allow Bash (user)." });
  const out = collapseD6([...Array.from({ length: 10 }, (_, i) => mk(i)), { id: "R1", severity: "high", scope: "user", rule: "Bash", why: "w" }]);
  assert.equal(out.length, 2);
  const g = out.find((f) => f.id === "D6");
  assert.equal(g.count, 10);
  assert.equal(g.coveredBy, "Bash"); // the broader rule is named, the DEAD rules are in rule/rules
  assert.equal(g.rule, "Bash(x0:*)");
  assert.equal(g.rules.length, 10);
});

test("text mode is <= 60 lines and grouped by severity", async () => {
  const allow = Array.from({ length: 500 }, (_, i) => `Bash(kill -9 ${10000 + i})`);
  allow.push("Bash(python3 *)");
  const home = mkHome({ permissions: { allow } });
  const { out } = await run(["--settings-only"], home);
  const lines = out.trimEnd().split("\n");
  assert.ok(lines.length <= 60, `lines ${lines.length}`);
  assert.ok(lines.some((l) => /^HIGH R2 user Bash\(python3 \*\) — /.test(l)));
  const first = lines.findIndex((l) => /^HIGH/.test(l));
  const dead = lines.findIndex((l) => /^DEAD/.test(l));
  assert.ok(first >= 0 && dead > first);
});

function result(id, content, extra = {}) {
  return { type: "user", sessionId: "s1", timestamp: new Date().toISOString(), ...extra, message: { role: "user", content: [{ type: "tool_result", tool_use_id: id, is_error: true, content }] } };
}
const denyText = (cmd) => `Permission to use Bash with command ${cmd} has been denied.`;
function deniedBash(cmd, times, cwd = "/w") {
  const out = [];
  for (let i = 0; i < times; i++) {
    out.push(toolUse("Bash", { command: cmd }, cwd), result(`t${tid - 1}`, denyText(cmd), { toolDenialKind: "permission-rule" }));
  }
  return out;
}
function blocked(cmd, text, times = 1) {
  const out = [];
  for (let i = 0; i < times; i++) out.push(toolUse("Bash", { command: cmd }, "/w"), result(`t${tid - 1}`, text, { toolDenialKind: "permission-rule" }));
  return out;
}

test("F5 denials: attributed to the deny rule that fired, exactly, not the command head", async () => {
  const home = mkHome({ permissions: { deny: ["Bash(git push:*)", "Bash(rm:*)"] } });
  const long = "echo " + "x".repeat(450) + " && rm -rf /tmp/beyond";
  writeTranscript(home, [
    ...deniedBash("rm -rf /tmp/a", 3),
    ...deniedBash("cd x && rm -rf /tmp/b", 2), // head "cd", rule "rm"
    ...deniedBash(long, 4), // trigger beyond char 400
    ...deniedBash("sudo reboot", 2), // no current deny rule
  ]);
  const r = JSON.parse((await run(["--json"], home)).out);
  const g = r.friction.denials;
  const rm = g.find((x) => x.rule === "Bash(rm:*)");
  assert.equal(rm.kind, "deny-rule");
  assert.equal(rm.count, 9, JSON.stringify(g));
  assert.equal(rm.scope, "user");
  const none = g.find((x) => x.kind === "deny-rule" && x.rule === null);
  assert.equal(none.count, 2);
  assert.equal(none.note, "rule no longer present");
  assert.ok(!g.some((x) => /^(cd|&&)$/.test(x.rule ?? "")));
  assert.ok(!("ruleDenied" in r.friction) && !("candidates" in r.friction) && !("candidatesDropped" in r.friction));
  const text = (await run(["--cwd", "/nonexistent-cwd"], home)).out;
  assert.match(text, /F5 deny rule Bash\(rm:\*\) \(user\) x9/);
  assert.match(text, /F5 deny rule hit \(Bash\), rule no longer present x2/);
});

test("F5 denials: hook blocks and built-in checks are their own kinds, never deny-rule hits", async () => {
  const home = mkHome({ permissions: { deny: ["Bash(git push:*)"] } });
  writeTranscript(home, [
    ...blocked("git push -f", "PreToolUse:Bash hook error: [~/.claude/hooks/public-repo-guard.py]: Blocked by public-repo-guard", 4),
    ...blocked("rm -rf /", "Permission for this command was denied by a built-in Claude Code safety check, not by the user.", 2),
    ...deniedBash("git push origin main", 1),
  ]);
  const r = JSON.parse((await run(["--json"], home)).out);
  const g = r.friction.denials;
  assert.equal(g.find((x) => x.kind === "hook").hook, "public-repo-guard.py");
  assert.equal(g.find((x) => x.kind === "hook").count, 4);
  assert.equal(g.find((x) => x.kind === "builtin").count, 2);
  assert.equal(g.find((x) => x.kind === "deny-rule").rule, "Bash(git push:*)");
  assert.equal(g.find((x) => x.kind === "deny-rule").count, 1);
  const text = (await run(["--cwd", "/nonexistent-cwd"], home)).out;
  assert.match(text, /F5 hook public-repo-guard\.py blocked x4/);
  assert.match(text, /F5 built-in safety-check x2/);
});

test("D9: a deny rule whose hits are mostly temp/project paths is flagged; others are not", async () => {
  const proj = path.join(tmp, "d9proj");
  fs.mkdirSync(path.join(proj, ".git"), { recursive: true });
  const home = mkHome({ permissions: { deny: ["Bash(rm:*)", "Bash(git push:*)"] } });
  writeTranscript(home, [
    ...deniedBash("rm -rf /tmp/a", 3),
    ...deniedBash("rm -rf /private/var/folders/x/T/tmp.1", 1),
    ...deniedBash(`rm -rf ${proj}/dist`, 1),
    ...deniedBash("rm -rf /Users/someone/Documents", 1), // other
    ...deniedBash("git push --force origin main", 8), // no path targets: never routine
  ]);
  const r = JSON.parse((await run(["--json", "--cwd", proj], home)).out);
  const d9 = r.findings.filter((f) => f.id === "D9");
  assert.equal(d9.length, 1, JSON.stringify(d9));
  assert.equal(d9[0].rule, "Bash(rm:*)");
  assert.equal(d9[0].list, "deny");
  assert.equal(d9[0].severity, "med");
  assert.match(d9[0].why, /^Blocks routine commands: 5 of 6 hits were /);
  assert.ok(d9[0].file.endsWith(path.join(".claude", "settings.json")));
  assert.ok(d9[0].doc.startsWith("https://"));
  // below the hit threshold: no finding
  const home2 = mkHome({ permissions: { deny: ["Bash(rm:*)"] } });
  writeTranscript(home2, deniedBash("rm -rf /tmp/a", 4));
  const r2 = JSON.parse((await run(["--json", "--cwd", proj], home2)).out);
  assert.ok(!r2.findings.some((f) => f.id === "D9"));
});

test("JSON findings keep file and doc (and list when the checker supplies it); no pseudo-findings", async () => {
  const allow = Array.from({ length: 40 }, (_, i) => `Bash(kill -9 ${10000 + i})`);
  allow.push("Bash(python3 *)");
  const home = mkHome({ permissions: { allow } });
  const r = JSON.parse((await run(["--json", "--settings-only"], home)).out);
  const r2 = r.findings.find((f) => f.id === "R2");
  assert.ok(r2.file.endsWith(path.join(".claude", "settings.json")));
  assert.ok(r2.doc.startsWith("https://"));
  assert.ok(!r.findings.some((f) => f.scope === "*"));
  const syn = { cwd: "/x", scopes: [], friction: null, ms: 1, findings: [{ id: "D8", severity: "dead", scope: "user", file: "/h/.claude/settings.json", list: "deny", rule: "Bash(a)", why: "w", doc: "https://d" }] };
  const f = capReport(syn).findings[0];
  assert.equal(f.list, "deny");
  assert.equal(f.file, "/h/.claude/settings.json");
  assert.equal(f.doc, "https://d");
});

test("truncated is an object naming what was dropped (per list and denial groups), still within 8 KB", async () => {
  const syn = {
    cwd: "/x", scopes: [], ms: 1,
    findings: Array.from({ length: 80 }, (_, i) => ({ id: "D8", severity: "dead", scope: "user", file: "/h/.claude/settings.json", list: i % 2 ? "deny" : "allow", rule: `Bash(rule number ${i} ${"y".repeat(60)})`, why: "w".repeat(120), doc: "https://code.claude.com/docs/en/permissions" })),
    friction: {
      modes: {}, classifierBlocks: [], rejected: [], headlessDenied: [],
      denials: Array.from({ length: 30 }, (_, i) => ({ kind: "hook", hook: `hook-${i}.py`, count: 30 - i, last: "2026-10-01T00:00:00Z", samples: [], modes: {}, tags: {} })),
    },
  };
  const r = capReport(syn);
  assert.ok(Buffer.byteLength(JSON.stringify(r)) <= MAX_BYTES);
  assert.equal(typeof r.truncated, "object");
  assert.ok(r.truncated.findings > 0 && r.truncated.allow > 0 && r.truncated.deny > 0);
  assert.ok(r.truncated.denialGroups > 0);
  assert.ok(!r.findings.some((f) => f.scope === "*"));
});

test("clip never cuts mid-word", () => {
  assert.equal(clip("short", 10), "short");
  const c = clip("alpha beta gamma delta epsilon zeta", 20);
  assert.ok(c.endsWith("…") && c.length <= 20 && !/\w…$/.test(c.replace(/ [a-z]+…$/, "…")) || /\s/.test(c));
  assert.equal(clip("alpha beta gamma delta", 18), "alpha beta gamma…");
});

test("S1: sandbox denials become sandbox suggestions only when sandbox.enabled", async () => {
  const lines = [
    toolUse("Bash", { command: "git commit -m x" }, "/w"),
    result(`t${tid - 1}`, "fatal: renaming '/repo/.git/objects/tmp_x' failed: Operation not permitted", {}),
    toolUse("Bash", { command: "ssh host ls" }, "/w"),
    result(`t${tid - 1}`, "Host key verification failed.", {}),
  ].map((l) => (l.message?.content?.[0]?.type === "tool_result" ? { ...l } : l));
  const on = mkHome({ sandbox: { enabled: true }, permissions: {} });
  writeTranscript(on, lines);
  const r = JSON.parse((await run(["--json"], on)).out);
  const s1 = r.findings.filter((f) => f.id === "S1");
  const byKey = Object.fromEntries(s1.map((f) => [f.sandbox.key, f.sandbox.value]));
  assert.equal(byKey["filesystem.allowWrite"], "/repo/.git");
  assert.equal(byKey["excludedCommands"], "ssh");
  assert.ok(s1.every((f) => f.severity === "low" && f.scope === "user" && f.file.endsWith("settings.json") && f.doc && f.rule));
  const off = mkHome({ permissions: {} });
  writeTranscript(off, lines);
  assert.ok(!JSON.parse((await run(["--json"], off)).out).findings.some((f) => f.id === "S1"));
});

test("L1 carries the full legacy-v2 delete command; L3 delete command is never truncated and skips *-inline", async () => {
  const home = mkHome({ permissions: {} });
  const data = path.join(tmp, `data${n++}`, "a-very-long-directory-name-".repeat(4));
  fs.mkdirSync(path.join(data, "legacy-v2"), { recursive: true });
  fs.writeFileSync(path.join(data, "legacy-v2", "MOVED.json"), JSON.stringify({ at: new Date().toISOString(), moved: [{ bytes: 1048576 }] }));
  const r = JSON.parse((await run(["--json", "--settings-only", "--data-dir", data], home)).out);
  const l1 = r.findings.find((f) => f.id === "L1" && f.rule === "legacy-v2/");
  assert.equal(l1.command, `rm -r ${path.join(data, "legacy-v2")}`);
  assert.ok(l1.why.includes(l1.command));
  const long = "/h/.claude/plugins/data/" + "orphan-".repeat(30);
  const fixed = fixLegacy([
    { id: "L3", severity: "info", scope: "user", file: long, rule: "orphan", why: "Orphaned data dir; delete with rm -r ~/.claude/plugins/data/orphan..." },
    { id: "L3", severity: "info", scope: "user", file: "/h/.claude/plugins/data/foo-inline", rule: "foo-inline", why: "w" },
  ]);
  assert.equal(fixed.length, 1);
  assert.equal(fixed[0].command, `rm -r ${long}`);
  const out = capReport({ cwd: "/x", scopes: [], friction: null, ms: 1, findings: fixed }).findings[0];
  assert.equal(out.command, `rm -r ${long}`);
});

test("text: legend, F-sections in ID order, F3 shows the head, truncation names what was cut", async () => {
  const home = mkHome({ permissions: { deny: ["Bash(rm:*)"] } });
  writeTranscript(home, [
    ...deniedBash("rm -rf /tmp/a", 1),
    toolUse("Bash", { command: "cd /x && gh variable list" }, "/w"),
    result(`t${tid - 1}`, "Claude requested permissions to use Bash, but you haven't granted it yet.", { toolDenialKind: "user-rejected" }),
    toolUse("Bash", { command: "npm publish" }, "/w"),
    result(`t${tid - 1}`, "The user doesn't want to proceed with this tool use. The tool use was rejected", { toolDenialKind: "user-rejected" }),
  ]);
  const text = (await run(["--cwd", "/nonexistent-cwd"], home)).out;
  assert.match(text, /^friction \(F1 .*F2 .*F3 .*F5 /m);
  assert.match(text, /F3 headless not-granted Bash gh variable x1/);
  assert.ok(text.indexOf("  F2 rejected") < text.indexOf("  F3 headless") && text.indexOf("  F3 headless") < text.indexOf("  F5 deny rule"));
  assert.match(text, /tip: for new allow rules use \/fewer-permission-prompts\./);
  assert.ok(!text.includes("F4"));
  // truncation line
  const allow = Array.from({ length: 500 }, (_, i) => `Bash(kill -9 ${10000 + i})`);
  const big = mkHome({ permissions: { allow } });
  const t2 = (await run(["--settings-only"], big)).out;
  assert.match(t2, /\(output truncated: \d+ findings\)/);
});

test("transcript cache: only with a data dir, identical output, --no-cache skips it", async () => {
  const home = mkHome({ permissions: { deny: ["Bash(rm:*)"] } });
  writeTranscript(home, [...bash("pnpm test", 25), ...deniedBash("rm -rf q", 2)]);
  const data = path.join(tmp, `data${n++}`);
  const strip = (o) => ({ ...JSON.parse(o), ms: 0 });
  const nodata = await run(["--json"], home);
  assert.ok(!fs.existsSync(data));
  const c1 = await run(["--json", "--data-dir", data], home);
  assert.ok(fs.existsSync(path.join(data, "transcript-cache", "cache.json")));
  const c2 = await run(["--json", "--data-dir", data], home);
  assert.deepEqual(strip(c1.out), strip(nodata.out));
  assert.deepEqual(strip(c2.out), strip(nodata.out));
  const data2 = path.join(tmp, `data${n++}`);
  await run(["--json", "--no-cache", "--data-dir", data2], home);
  assert.ok(!fs.existsSync(path.join(data2, "transcript-cache")));
  assert.equal(parseArgs(["--no-cache"]).noCache, true);
});

test("check mode: allow / ask / deny / unmatched / compound / wrapper", async () => {
  const home = mkHome(null, { copyFixture: true });
  const cmds = ["git status", "git push origin main", "rm -rf /tmp/x", "terraform apply", "git status && rm -rf /tmp/x", "timeout 5 npm test", "git status && terraform apply"];
  const { out } = await run(["check", "--json", ...cmds], home);
  const r = JSON.parse(out);
  const by = Object.fromEntries(r.results.map((x) => [x.cmd, x]));
  assert.equal(by["git status"].decision, "allow");
  assert.equal(by["git status"].rule, "Bash(git status:*)");
  assert.equal(by["git push origin main"].decision, "ask");
  assert.equal(by["rm -rf /tmp/x"].decision, "deny");
  assert.match(by["rm -rf /tmp/x"].bypassNote, /bypass/);
  assert.equal(by["terraform apply"].decision, "unmatched");
  assert.equal(by["git status && rm -rf /tmp/x"].decision, "deny");
  assert.equal(by["timeout 5 npm test"].decision, "allow");
  assert.equal(by["git status && terraform apply"].decision, "unmatched");
  assert.ok(!("bypassNote" in by["git status"]));
  const text = (await run(["check", "git push origin main", "rm -rf x"], home)).out.trim().split("\n");
  assert.equal(text.length, 2);
  assert.match(text[1], /^deny\s+rm -rf x/);
});

test("--all-projects groups project-scope findings per project, skips node_modules, omits user findings", async () => {
  const home = mkHome({ permissions: { allow: ["Bash(python3 *)"] } });
  const root = path.join(tmp, "proj-root");
  const mk = (rel, file, data) => {
    const d = path.join(root, rel, ".claude");
    fs.mkdirSync(d, { recursive: true });
    fs.writeFileSync(path.join(d, file), JSON.stringify(data));
  };
  mk("a", "settings.json", { permissions: { allow: ["Bash(bash *)"] } });
  mk("b/deep", "settings.local.json", { permissions: { allow: ["Bash(kill -9 4242)"] } });
  mk("clean", "settings.json", { permissions: { allow: ["Bash(git status:*)"] } });
  mk("node_modules/pkg", "settings.json", { permissions: { allow: ["Bash(bash *)"] } });
  const r = JSON.parse((await run(["--json", "--settings-only", "--all-projects", root, "--cwd", root], home)).out);
  const dirs = r.projects.map((p) => path.relative(root, p.dir)).sort();
  assert.deepEqual(dirs, ["a", "b/deep", "clean"]);
  const a = r.projects.find((p) => p.dir.endsWith(path.sep + "a"));
  assert.deepEqual(a.findings.map((f) => f.id + ":" + f.scope), ["R1:projectShared"]);
  const b = r.projects.find((p) => p.dir.endsWith("deep"));
  assert.deepEqual(b.findings.map((f) => f.id + ":" + f.scope), ["D7:projectLocal"]);
  assert.deepEqual(r.projects.find((p) => p.dir.endsWith("clean")).findings, []);
  // the user-scope R2 appears once, at top level, never in a project
  assert.equal(r.findings.filter((f) => f.id === "R2").length, 1);
  assert.ok(!JSON.stringify(r.projects).includes("python3"));
});

test("legacy logs are reported as L1", async () => {
  const home = mkHome({ permissions: {} });
  fs.writeFileSync(path.join(home, ".claude", "permission-log.jsonl"), "x".repeat(2048));
  const r = JSON.parse((await run(["--json", "--settings-only"], home)).out);
  assert.ok(r.findings.some((f) => f.id === "L1" && f.severity === "info"));
});

test("resolveDataDir order: flag, env, derived from cache path, else null", () => {
  const home = path.join(tmp, "dd-home");
  const lib = path.join(home, ".claude", "plugins", "cache", "mkt", "permissions", "3.0.0", "lib");
  fs.mkdirSync(lib, { recursive: true });
  const self = path.join(lib, "scan.mjs");
  fs.writeFileSync(self, "");
  assert.equal(resolveDataDir({ flag: "/f", env: { CLAUDE_PLUGIN_DATA: "/e" }, home, selfFile: self }), "/f");
  assert.equal(resolveDataDir({ flag: null, env: { CLAUDE_PLUGIN_DATA: "/e" }, home, selfFile: self }), "/e");
  assert.equal(resolveDataDir({ flag: null, env: {}, home, selfFile: self }), path.join(home, ".claude", "plugins", "data", "permissions-mkt"));
  assert.equal(resolveDataDir({ flag: null, env: {}, home, selfFile: path.join(HERE, "scan.mjs") }), null);
});

test("argument errors exit 2; launcher runs end to end with a temp HOME", () => {
  assert.equal(parseArgs(["--days", "0"]).error !== null, true);
  assert.equal(parseArgs(["check"]).error, "check needs at least one command");
  const home = mkHome(null, { copyFixture: true });
  const out = execFileSync(process.execPath, [LAUNCHER, "--json", "--settings-only", "--cwd", home], { env: { PATH: process.env.PATH, HOME: home }, encoding: "utf8" });
  assert.equal(JSON.parse(out).version, "3.1");
  let status = 0;
  try {
    execFileSync(process.execPath, [LAUNCHER, "--bogus"], { env: { PATH: process.env.PATH, HOME: home }, stdio: "pipe" });
  } catch (e) {
    status = e.status;
  }
  assert.equal(status, 2);
});

test("L3 never flags a live 3.x data dir (schema marker), even with --data-dir elsewhere", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "l3live-"));
  const data = path.join(home, ".claude", "plugins", "data");
  fs.mkdirSync(path.join(data, "permissions-claude-toolbelt"), { recursive: true });
  fs.writeFileSync(path.join(data, "permissions-claude-toolbelt", "schema"), "3");
  fs.mkdirSync(path.join(data, "permissions-old-market"), { recursive: true });
  fs.mkdirSync(path.join(home, ".claude"), { recursive: true });
  fs.writeFileSync(path.join(home, ".claude", "settings.json"), "{}");
  const other = fs.mkdtempSync(path.join(os.tmpdir(), "l3data-"));
  const r = await buildReport({ cwd: home, home, settingsOnly: true, dataDir: other });
  const l3 = r.findings.filter((f) => f.id === "L3").map((f) => f.rule);
  assert.ok(!l3.includes("permissions-claude-toolbelt"), JSON.stringify(l3));
  assert.ok(l3.includes("permissions-old-market"), JSON.stringify(l3));
});
