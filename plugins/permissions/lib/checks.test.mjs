import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { loadScopes } from "./scopes.mjs";
import { runChecks, decide } from "./checks.mjs";

const FIX = fileURLToPath(new URL("../tests/fixtures/settings/", import.meta.url));
const load = (id, kind) =>
  loadScopes({ cwd: join(FIX, id, kind, "proj"), home: join(FIX, id, kind, "home"), managedPaths: [] });
const homeOf = (id, kind) => join(FIX, id, kind, "home");

// id (fixture dir) -> { finding id, severity, opts for pos/neg }
const CASES = [
  { dir: "R1", id: "R1", sev: "high" },
  { dir: "R2", id: "R2", sev: "high" },
  { dir: "R3", id: "R3", sev: "high" },
  { dir: "R4", id: "R4", sev: "high" },
  { dir: "R5", id: "R5", sev: "high" },
  { dir: "R6", id: "R6", sev: "med" },
  { dir: "R7", id: "R7", sev: "med" },
  { dir: "R8", id: "R8", sev: "med" },
  { dir: "R9", id: "R9", sev: "med", pos: { seenTools: ["mcp__jira__create_issue"] }, neg: { seenTools: ["mcp__jira__get_issue"] } },
  { dir: "R10", id: "R10", sev: "med" },
  { dir: "R11", id: "R11", sev: "low" },
  { dir: "R12", id: "R12", sev: "info" },
  { dir: "D1", id: "D1", sev: "dead" },
  { dir: "D2", id: "D2", sev: "dead" },
  { dir: "D3", id: "D3", sev: "dead", pos: { seenTools: ["mcp__claude_ai_Buildkite__list_builds"] }, neg: { seenTools: ["mcp__buildkite__list_builds"] } },
  { dir: "D4", id: "D4", sev: "dead" },
  { dir: "D5", id: "D5", sev: "info" },
  { dir: "D5-dead", id: "D5", sev: "dead" },
  { dir: "D6", id: "D6", sev: "dead" },
  { dir: "D7", id: "D7", sev: "dead" },
  { dir: "D8", id: "D8", sev: "dead" },
  { dir: "L2", id: "L2", sev: "med" },
  {
    dir: "L3", id: "L3", sev: "info",
    pos: { pluginDataDirs: ["permissions-claude-toolbelt", "other-plugin"], selfDataDir: "permissions-toolbelt" },
    neg: { pluginDataDirs: ["permissions-toolbelt", "other-plugin"], selfDataDir: "permissions-toolbelt" },
  },
];

for (const c of CASES) {
  test(`${c.dir}: positive fixture yields ${c.id} (${c.sev})`, () => {
    const found = runChecks(load(c.dir, "pos"), { home: homeOf(c.dir, "pos"), ...(c.pos ?? {}) }).filter((f) => f.id === c.id && f.severity === c.sev);
    assert.ok(found.length >= 1, `expected ${c.id}/${c.sev}`);
    const f = found[0];
    assert.ok(f.why.length > 0 && f.why.length <= 140);
    assert.match(f.doc, /^https:\/\/code\.claude\.com\/docs\//);
  });
  test(`${c.dir}: negative fixture has no ${c.id}`, () => {
    const found = runChecks(load(c.dir, "neg"), { home: homeOf(c.dir, "neg"), ...(c.neg ?? {}) }).filter((f) => f.id === c.id && f.severity === c.sev);
    assert.deepEqual(found, []);
  });
}

for (const [dir, id] of [["R2-literal", "R2"], ["R3-literal", "R3"], ["R6-literal", "R6"], ["R8-literal", "R8"]]) {
  test(`${dir}: exact literal rule does not trigger ${id}`, () => {
    const r = runChecks(load(dir, "neg"), { home: homeOf(dir, "neg") });
    assert.deepEqual(r.filter((f) => f.id === id), []);
  });
}

test("R3-source: literal source-of-a-path still fires high", () => {
  const r = runChecks(load("R3-source", "pos"), { home: homeOf("R3-source", "pos") });
  assert.deepEqual(r.filter((f) => f.id === "R3").map((f) => f.severity), ["high"]);
});

test("R9 without seenTools degrades to low; D3 skipped", () => {
  const r = runChecks(load("D3", "pos"), { home: "/h" });
  assert.deepEqual(r.filter((f) => f.id === "R9").map((f) => f.severity), ["low"]);
  assert.equal(r.filter((f) => f.id === "D3").length, 0);
});

test("R12 and D5 severities, finding shape", () => {
  const [f] = runChecks(load("R12", "pos"), { home: "/h" }).filter((x) => x.id === "R12");
  assert.deepEqual(Object.keys(f).sort(), ["doc", "file", "id", "rule", "scope", "severity", "why"]);
  assert.equal(f.scope, "user");
});

test("D4 flags project autoMode too", () => {
  const loaded = load("D4", "pos");
  loaded.scopes.find((s) => s.name === "projectShared").data.autoMode = { allow: ["x"] };
  assert.equal(runChecks(loaded, { home: "/h" }).filter((f) => f.id === "D4").length, 2);
});

test("D8: Bash(cat:*) allow does not defeat Read deny and is not itself D8", () => {
  const loaded = load("D8", "neg");
  assert.equal(decide("cat ~/.ssh/id_rsa", loaded.effective).decision, "deny");
  const readOnly = { allow: [{ rule: "Bash(cat:*)", scope: "user" }], ask: [], deny: [{ rule: "Read(~/.ssh/**)", scope: "user" }] };
  assert.equal(decide("cat ~/.ssh/x", readOnly).decision, "deny");
  assert.equal(decide("cat ~/.ssh/x", readOnly).rule, "Read(~/.ssh/**)");
  assert.equal(decide("cat ./README.md", readOnly).decision, "allow");
});

// ---- decide() ---------------------------------------------------------------

const eff = {
  allow: [
    { rule: "Bash(git status:*)", scope: "user" },
    { rule: "Bash(ls:*)", scope: "user" },
    { rule: "Bash(npm test)", scope: "projectShared" },
    { rule: "Read(~/src/**)", scope: "user" },
  ],
  ask: [{ rule: "Bash(git push:*)", scope: "user" }],
  deny: [{ rule: "Bash(rm -rf *)", scope: "managed" }, { rule: "Edit(/etc/**)", scope: "user" }],
};

test("decide: allow, unmatched", () => {
  assert.equal(decide("git status", eff).decision, "allow");
  assert.equal(decide("git status", eff).scope, "user");
  assert.equal(decide("python3 x.py", eff).decision, "unmatched");
});

test("decide: compound needs every segment allowed", () => {
  assert.equal(decide("ls && git status", eff).decision, "allow");
  assert.equal(decide("ls && python3 x.py", eff).decision, "unmatched");
});

test("decide: deny/ask in any segment wins, deny before ask", () => {
  assert.equal(decide("ls && git push origin main", eff).decision, "ask");
  assert.equal(decide("git status; rm -rf /tmp/x", eff).decision, "deny");
  assert.equal(decide("git push && rm -rf x", eff).decision, "deny");
  assert.equal(decide("git push && rm -rf x", eff).rule, "Bash(rm -rf *)");
});

test("decide: wrappers and env are stripped before matching", () => {
  assert.equal(decide("timeout 5 FOO=1 git status", eff).decision, "allow");
  assert.equal(decide("nohup rm -rf x", eff).decision, "deny");
});

test("decide: quoted separators do not split", () => {
  assert.equal(decide('ls "a && rm -rf x"', eff).decision, "allow");
});

test("decide: redirect target hits Edit deny; non-Bash tool calls", () => {
  assert.equal(decide("ls > /etc/hosts", eff).decision, "deny");
  assert.deepEqual(decide({ tool: "Read", detail: `${process.env.HOME}/src/a.js` }, eff), { decision: "allow", rule: "Read(~/src/**)", scope: "user" });
  assert.equal(decide({ tool: "Edit", detail: "/etc/passwd" }, eff).decision, "deny");
  assert.equal(decide({ tool: "WebFetch", detail: "x" }, eff).decision, "unmatched");
});
