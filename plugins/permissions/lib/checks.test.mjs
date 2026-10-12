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
  { dir: "R13", id: "R13", sev: "high" },
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
  deny: [{ rule: "Bash(rm -rf *)", scope: "managed" }, { rule: "Edit(//etc/**)", scope: "user" }],
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
  assert.equal(decide("timeout 5 NODE_ENV=1 git status", eff).decision, "allow");
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

// ---- 3.1 fix round -------------------------------------------------------------

const ids = (dir, kind, opts = {}) => runChecks(load(dir, kind), { home: homeOf(dir, kind), ...opts });
const idRules = (r, id) => r.filter((f) => f.id === id).map((f) => f.rule).sort();

test("R3 fires on wildcard source/./env/runner rules (and not on pinned inner commands)", () => {
  const rules = idRules(ids("R3-wild", "pos"), "R3");
  for (const want of [
    "Bash(source *)", "Bash(source:*)", "Bash(. *)", "Bash(env *)", "Bash(env:*)", "Bash(npx *)", "Bash(npx prettier:*)",
    "Bash(pnpm dlx *)", "Bash(bunx *)", "Bash(yarn dlx *)", "Bash(nvm exec *)", "Bash(nvm run *)", "Bash(fnm exec *)",
    "Bash(volta run *)", "Bash(asdf exec *)", "Bash(mise exec *)", "Bash(mise x *)", "Bash(pyenv exec *)",
    "Bash(rbenv exec *)", "Bash(direnv exec *)", "Bash(uv run *)", "Bash(poetry run *)", "Bash(bundle exec *)",
  ]) assert.ok(rules.includes(want), `missing R3 for ${want}`);
  assert.deepEqual(idRules(ids("R3-wild", "neg"), "R3"), []);
});

test("R13: open sudo/doas/su allows are high; pinned sudo and lookalikes are not", () => {
  assert.deepEqual(idRules(ids("R13", "pos"), "R13"), ["Bash(sudo *)"]);
  assert.deepEqual(idRules(ids("R13-colon", "pos"), "R13"), ["Bash(doas *)", "Bash(su)", "Bash(sudo:*)"]);
  assert.deepEqual(idRules(ids("R13", "neg"), "R13"), []);
});

test("D1: command -v and xargs rules are not dead; timeout/nohup rules still are", () => {
  assert.deepEqual(idRules(ids("D1-lookup", "neg"), "D1"), []);
  assert.deepEqual(idRules(ids("D1-timeout", "pos"), "D1"), ["Bash(nohup node server.js)", "Bash(timeout 5 npm test)"]);
  assert.deepEqual(idRules(ids("D1", "pos"), "D1"), ["Bash(command:*)"]);
});

test("R8: needs curl AND a shell/interpreter allow; a pipe deny is not mitigation; segment denies are", () => {
  assert.equal(idRules(ids("R8-nointerp", "neg"), "R8").length, 0);
  assert.equal(idRules(ids("R8-pipedeny", "pos"), "R8").length, 1);
  assert.equal(idRules(ids("R8", "neg"), "R8").length, 0);
});

test("L3 ignores *-inline data dirs", () => {
  const r = ids("L3", "pos", { pluginDataDirs: ["permissions-inline", "permission-advisor-x"], selfDataDir: "permissions-toolbelt" });
  assert.deepEqual(r.filter((f) => f.id === "L3").map((f) => f.rule), ["permission-advisor-x"]);
});

test("L3 why keeps the whole delete command (no truncation)", () => {
  const name = "permissions-" + "x".repeat(150);
  const [f] = ids("L3", "pos", { pluginDataDirs: [name], selfDataDir: "s" }).filter((x) => x.id === "L3");
  assert.ok(f.why.endsWith(name));
});

test("findings carry file and list; non-rule findings omit list; D6 names covered rule", () => {
  const r = ids("D6", "pos");
  const d6 = r.filter((f) => f.id === "D6");
  assert.ok(d6.length >= 1);
  for (const f of d6) {
    assert.match(f.file, /settings\.json$/);
    assert.equal(f.list, "allow");
    assert.ok(f.coveredBy);
  }
  const r12 = ids("R12", "pos").find((f) => f.id === "R12");
  assert.equal("list" in r12, false);
  assert.ok(r12.file);
});

test("why is clipped at a word boundary within 140 chars", () => {
  const rule = "Bash(curl:*)";
  const f = ids("R8", "pos").find((x) => x.id === "R8" && x.rule === rule);
  assert.ok(f.why.length <= 140);
});

// ---- decide(): fail closed (audit3 S3, S4, S5) ----------------------------------

const mkEff = (o) => ({ allow: [], ask: [], deny: [], ...Object.fromEntries(Object.entries(o).map(([k, v]) => [k, v.map((rule) => ({ rule, scope: "user" }))])) });
const dc = (cmd, o, ctx) => decide(cmd, mkEff(o), ctx);

test("S3: allow does not see past an unsafe env assignment; deny does", () => {
  assert.equal(dc("PATH=/evil git status", { allow: ["Bash(git status)"] }).decision, "unmatched");
  assert.equal(dc("LD_PRELOAD=x git status", { allow: ["Bash(git status)"] }).decision, "unmatched");
  assert.equal(dc("FOO=$(x) git status", { allow: ["Bash(git status)"] }).decision, "unsure");
  assert.equal(dc("NODE_ENV=test git status", { allow: ["Bash(git status)"] }).decision, "allow");
  assert.equal(dc("PATH=x git status", { deny: ["Bash(git status)"] }).decision, "deny");
  assert.equal(dc("FOO=bar rm -rf tmp/", { deny: ["Bash(rm *)"] }).decision, "deny");
});

test("S4: substitutions and subshells: deny still found, otherwise unsure", () => {
  const o = { allow: ["Bash(echo *)"], deny: ["Bash(rm:*)"] };
  assert.equal(dc("echo $(rm x)", o).decision, "deny");
  assert.equal(dc("echo `rm x`", o).decision, "deny");
  assert.equal(dc("echo <(rm x)", o).decision, "deny");
  assert.equal(dc("(cd x; rm y)", o).decision, "deny");
  assert.equal(dc('echo "$(rm x)"', o).decision, "deny");
  assert.equal(dc("echo $(date)", o).decision, "unsure");
  assert.equal(dc("echo `date`", o).decision, "unsure");
  assert.equal(dc("(echo hi)", o).decision, "unsure");
  assert.equal(dc("echo 'it $(rm x)'", o).decision, "allow");
  assert.equal(dc("for f in a b; do rm $f; done", o).decision, "deny");
  assert.equal(dc("for f in a b; do echo $f; done", o).decision, "unsure");
  assert.equal(dc("eval 'rm x'", { deny: ["Bash(rm:*)"] }).decision, "deny");
  assert.equal(dc("bash -c 'echo $(ls'", o).decision, "unsure");
  assert.equal(dc("echo 'unterminated", o).decision, "unsure");
});

test("S4: heredoc bodies are not commands", () => {
  const o = { allow: ["Bash(cat *)"], deny: ["Bash(rm:*)"] };
  assert.equal(dc("cat <<EOF\nrm x\nEOF", o).decision, "allow");
  assert.equal(dc("cat <<'EOF'\n$(rm x)\nEOF", o).decision, "allow");
  assert.equal(dc("cat <<EOF\n$(rm x)\nEOF", o).decision, "deny");
});

test("S1/S2 via decide: bare-command wildcard and bare mcp server", () => {
  assert.equal(dc("ls", { allow: ["Bash(ls *)"] }).decision, "allow");
  assert.equal(dc("git push", { deny: ["Bash(git push *)"] }).decision, "deny");
  assert.equal(dc({ tool: "mcp__srv__t", detail: "" }, { deny: ["mcp__srv"] }).decision, "deny");
});

test("S5: path rules per docs (// absolute, / anchored at settings source, ./, bare, **/)", () => {
  const ctx = { cwd: "/proj", home: "/home/u" };
  const o = (rule) => ({ allow: ["Bash(cat *)", "Bash(echo *)"], deny: [rule] });
  assert.equal(dc("cat .env", o("Read(**/.env)"), ctx).decision, "deny");
  assert.equal(dc("cat .env", o("Read(./.env)"), ctx).decision, "deny");
  assert.equal(dc("cat sub/a.pem", o("Read(*.pem)"), ctx).decision, "deny");
  assert.equal(dc("cat $HOME/.ssh/id", o("Read(~/.ssh/**)"), ctx).decision, "deny");
  assert.equal(dc("cat < ~/.ssh/id", o("Read(~/.ssh/**)"), ctx).decision, "deny");
  assert.equal(dc("echo x>/etc/passwd", o("Edit(//etc/**)"), ctx).decision, "deny");
  assert.equal(dc("echo x >>/etc/passwd", o("Write(//etc/**)"), ctx).decision, "deny");
  assert.equal(dc("echo x >| /etc/passwd", o("Edit(//etc/**)"), ctx).decision, "deny");
  assert.equal(dc('echo "a > /etc/b"', o("Edit(//etc/**)"), ctx).decision, "allow");
  assert.equal(dc("echo x 2>&1", o("Edit(//etc/**)"), ctx).decision, "allow");
  assert.equal(dc({ tool: "Write", detail: "/etc/x" }, { deny: ["Edit(//etc/**)"] }, ctx).decision, "deny");
  // "/etc/**" is <project>/etc/**, not the real /etc
  assert.equal(dc({ tool: "Edit", detail: "/etc/x" }, { deny: ["Edit(/etc/**)"] }, ctx).decision, "unmatched");
  const proj = { allow: [], ask: [], deny: [{ rule: "Edit(/etc/**)", scope: "projectShared" }] };
  assert.equal(decide({ tool: "Edit", detail: "/proj/etc/x" }, proj, ctx).decision, "deny");
  assert.equal(decide({ tool: "Edit", detail: "/home/u/.claude/etc/x" }, mkEff({ deny: ["Edit(/etc/**)"] }), ctx).decision, "deny"); // user scope anchors at ~/.claude
});

test("D6 named rule is the dead one (not the broader rule)", () => {
  const loaded = {
    scopes: [],
    effective: { ...mkEff({ allow: ["Bash(git:*)", "Bash(git status)"] }), env: [], additionalDirectories: [], sandbox: { enabled: false }, flags: {} },
  };
  loaded.effective.allow.forEach((e, i) => { e.file = "/f"; e.scope = i === 0 ? "user" : "user"; });
  const d6 = runChecks(loaded, {}).filter((f) => f.id === "D6");
  assert.deepEqual(d6.map((f) => [f.rule, f.coveredBy]), [["Bash(git status)", "Bash(git:*)"]]);
});

test("D6: equivalent rules (git:* vs git *) flag only one, never both", () => {
  const loaded = {
    scopes: [],
    effective: { ...mkEff({ allow: ["Bash(git:*)", "Bash(git *)"] }), env: [], additionalDirectories: [], sandbox: { enabled: false }, flags: {} },
  };
  loaded.effective.allow.forEach((e) => { e.file = "/f"; });
  const d6 = runChecks(loaded, {}).filter((f) => f.id === "D6");
  assert.deepEqual(d6.map((f) => [f.rule, f.coveredBy]), [["Bash(git *)", "Bash(git:*)"]]);
});
