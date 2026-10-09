import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { main, capReport, collapseD6, resolveDataDir, parseArgs, MAX_BYTES } from "./scan.mjs";

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
  assert.equal(r.version, "3");
  assert.equal(typeof r.cwd, "string");
  assert.deepEqual(Object.keys(r.scopes[0]).sort(), ["counts", "exists", "file", "name"]);
  const user = r.scopes.find((s) => s.name === "user");
  assert.deepEqual(user.counts, { allow: 3, deny: 2, ask: 1 });
  assert.ok(Array.isArray(r.findings));
  assert.equal(r.friction, null);
  assert.equal(typeof r.truncated, "boolean");
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
  assert.equal(r.truncated, true);
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
  assert.equal(r.truncated, true);
});

test("D6 collapses per covering rule", () => {
  const mk = (i) => ({ id: "D6", severity: "dead", scope: "user", rule: `Bash(x${i}:*)`, why: "Already covered by broader allow Bash (user)." });
  const out = collapseD6([...Array.from({ length: 10 }, (_, i) => mk(i)), { id: "R1", severity: "high", scope: "user", rule: "Bash", why: "w" }]);
  assert.equal(out.length, 2);
  const g = out.find((f) => f.id === "D6");
  assert.equal(g.count, 10);
  assert.equal(g.rule, "Bash");
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

test("F4 candidates: narrow rules, no interpreters/blankets, no write-verb MCP tools, respects min-calls", async () => {
  const home = mkHome({ permissions: { allow: ["Bash(git status:*)"] } });
  const lines = [
    ...bash("pnpm test", 25, "/work/a"),
    ...bash("pnpm test", 3, "/work/b"),
    ...bash("make build", 12, "/work/a"), // below min-calls
    ...bash("git status", 40), // already allowed
    ...Array.from({ length: 30 }, (_, i) => toolUse("Bash", { command: `python3 script${i}.py` }, "/work/a")),
    ...Array.from({ length: 30 }, (_, i) => toolUse("Bash", { command: `docker run img${i}` }, "/work/a")),
    ...Array.from({ length: 22 }, () => toolUse("mcp__srv__list_things", {}, "/work/a")),
    ...Array.from({ length: 30 }, () => toolUse("mcp__srv__create_thing", {}, "/work/a")),
    ...Array.from({ length: 30 }, () => toolUse("mcp__srv__run_query", {}, "/work/a")),
    ...Array.from({ length: 30 }, () => toolUse("mcp__srv__send_message", {}, "/work/a")),
  ];
  writeTranscript(home, lines);
  const { out } = await run(["--json"], home);
  const r = JSON.parse(out);
  const rules = r.friction.candidates.map((c) => c.rule);
  const pnpm = r.friction.candidates.find((c) => c.rule === "Bash(pnpm test)");
  assert.ok(pnpm, JSON.stringify(rules));
  assert.equal(pnpm.calls, 28);
  assert.equal(pnpm.scopeSuggestion, "user"); // seen in 2 cwds
  assert.ok(rules.includes("mcp__srv__list_things"));
  assert.ok(!rules.some((x) => /python3|create_thing|run_query|send_message|git status|make/.test(x)), JSON.stringify(rules));
  // docker run: head "docker run" is a wildcard on a code runner and trips nothing in R*, but mostly-unique
  // commands fall back to the head rule; it must not be an interpreter/blanket form
  assert.ok(!rules.includes("Bash"));
  const hi = JSON.parse((await run(["--json", "--min-calls", "100"], home)).out);
  assert.deepEqual(hi.friction.candidates, []);
});

test("F4 candidate of a single-cwd command suggests projectShared", async () => {
  const home = mkHome({ permissions: { allow: [] } });
  writeTranscript(home, bash("bin/tool.sh --go", 30, "/work/a"));
  const r = JSON.parse((await run(["--json"], home)).out);
  assert.equal(r.friction.candidates[0].scopeSuggestion, "projectShared");
});

function result(id, content, extra = {}) {
  return { type: "user", sessionId: "s1", timestamp: new Date().toISOString(), ...extra, message: { role: "user", content: [{ type: "tool_result", tool_use_id: id, is_error: true, content }] } };
}
function deniedBash(cmd, times, cwd = "/w") {
  const out = [];
  for (let i = 0; i < times; i++) {
    out.push(toolUse("Bash", { command: cmd }, cwd), result(`t${tid - 1}`, "denied", { toolDenialKind: "permission-rule" }));
  }
  return out;
}

test("F5 groups by the deny rule that fired, not the command head", async () => {
  const home = mkHome({ permissions: { deny: ["Bash(git push:*)", "Bash(rm:*)"] } });
  writeTranscript(home, [
    ...deniedBash("rm -rf /tmp/a", 3),
    ...deniedBash("cd x && rm -rf /tmp/b", 2), // head "cd", rule "rm"
    ...deniedBash("sudo reboot", 2), // no current deny rule
  ]);
  const r = JSON.parse((await run(["--json"], home)).out);
  const g = r.friction.ruleDenied;
  const by = Object.fromEntries(g.map((x) => [x.rule, x]));
  assert.equal(by["Bash(rm:*)"].count, 5, JSON.stringify(g));
  assert.equal(by["Bash(rm:*)"].scope, "user");
  assert.deepEqual(by["Bash(rm:*)"].tools, ["Bash"]);
  assert.equal(by["(no current deny rule — removed or managed?)"].count, 2);
  assert.ok(!g.some((x) => /^(cd|&&)$/.test(x.rule)));
  const text = (await run(["--cwd", "/nonexistent-cwd"], home)).out;
  assert.match(text, /F5 deny rule fired Bash\(rm:\*\) x5/);
});

test("F4 drops launchers/shells and heads the classifier already blocked, with reasons", async () => {
  const home = mkHome({ permissions: { allow: [] } });
  const classifier = (id) => result(id, "Permission for this action was denied by the Claude Code auto mode classifier. Reason: [Auto-Mode Bypass]. If you", {});
  const lines = [
    ...bash("tmux send-keys x", 30),
    ...bash("osascript -e y", 30),
    ...bash("pnpm build", 30), // fine
    ...bash("flaky-tool --go", 30), // classifier-blocked below
    toolUse("Bash", { command: "flaky-tool --blocked" }, "/w"),
  ];
  lines.push(classifier(`t${tid - 1}`));
  writeTranscript(home, lines);
  const r = JSON.parse((await run(["--json"], home)).out);
  const rules = r.friction.candidates.map((c) => c.rule);
  assert.ok(rules.includes("Bash(pnpm build)"), JSON.stringify(rules));
  assert.ok(!rules.some((x) => /tmux|osascript|flaky/.test(x)), JSON.stringify(rules));
  const d = Object.fromEntries(r.friction.candidatesDropped.map((x) => [x.head, x.reason]));
  assert.match(d.tmux, /launcher/);
  assert.match(d.osascript, /launcher/);
  assert.match(d["flaky-tool"], /classifier/);
  assert.ok(r.friction.candidatesDropped.length <= 10);
});

test("F4 projectShared candidates name the project (top cwd); other project is flagged", async () => {
  const home = mkHome({ permissions: { allow: [] } });
  const other = path.join(home, "Projects", "firstmate");
  writeTranscript(home, [...bash("bin/wake.sh --go", 25, other), ...bash("bin/wake.sh --go", 2, other)]);
  const r = JSON.parse((await run(["--json", "--cwd", path.join(home, "Projects", "mine")], home)).out);
  const c = r.friction.candidates[0];
  assert.equal(c.scopeSuggestion, "projectShared");
  assert.equal(c.project, "~/Projects/firstmate");
  assert.equal(c.otherProject, true);
  const same = JSON.parse((await run(["--json", "--cwd", other], home)).out).friction.candidates[0];
  assert.equal(same.project, "~/Projects/firstmate");
  assert.ok(!("otherProject" in same));
  const text = (await run(["--cwd", other], home)).out;
  assert.match(text, /F4 candidate Bash\(bin\/wake\.sh --go\) \(27 calls\) -> projectShared ~\/Projects\/firstmate/);
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
  assert.equal(JSON.parse(out).version, "3");
  let status = 0;
  try {
    execFileSync(process.execPath, [LAUNCHER, "--bogus"], { env: { PATH: process.env.PATH, HOME: home }, stdio: "pipe" });
  } catch (e) {
    status = e.status;
  }
  assert.equal(status, 2);
});
