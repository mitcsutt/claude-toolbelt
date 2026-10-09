import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { loadScopes } from "./scopes.mjs";

const FIX = fileURLToPath(new URL("../tests/fixtures/settings/", import.meta.url));
const load = (id, kind) =>
  loadScopes({ cwd: join(FIX, id, kind, "proj"), home: join(FIX, id, kind, "home"), managedPaths: [] });

test("precedence: scalar defaultMode resolves managed > local > shared > user", () => {
  const { effective } = load("precedence", "pos");
  assert.deepEqual(effective.defaultMode, { value: "dontAsk", scope: "projectLocal" });
});

test("arrays concatenate across scopes with scope + file", () => {
  const { effective } = load("precedence", "pos");
  assert.deepEqual(effective.allow.map((r) => [r.rule, r.scope]), [
    ["Bash(ls:*)", "user"],
    ["Bash(git status)", "projectShared"],
  ]);
  assert.equal(effective.deny[0].scope, "projectLocal");
  assert.match(effective.deny[0].file, /settings\.local\.json$/);
});

test("P4: project auto/bypass defaultMode is ignored for the effective value", () => {
  const { effective } = load("project-auto", "pos");
  assert.deepEqual(effective.defaultMode, { value: "plan", scope: "user" });
});

test("unparseable file: exists true, data null, error string, no throw", () => {
  const { scopes, effective } = load("broken", "pos");
  const user = scopes.find((s) => s.name === "user");
  assert.equal(user.exists, true);
  assert.equal(user.data, null);
  assert.equal(typeof user.error, "string");
  assert.deepEqual(effective.allow, []);
});

test("missing files: exists false", () => {
  const { scopes } = load("D4", "pos");
  assert.equal(scopes.find((s) => s.name === "projectLocal").exists, false);
  assert.equal(scopes.find((s) => s.name === "projectShared").exists, true);
});

test("env: keys only, values never present anywhere in the result", () => {
  const loaded = load("R4", "pos");
  assert.deepEqual(loaded.effective.env.map((e) => e.key), ["EXA_API_KEY"]);
  assert.ok(!JSON.stringify(loaded).includes("sk-SECRET-VALUE-123"));
});

test("sandbox, flags and additionalDirectories resolve", () => {
  assert.equal(load("D5", "neg").effective.sandbox.enabled, true);
  assert.equal(load("D5", "neg").effective.sandbox.scope, "user");
  assert.equal(load("D5", "pos").effective.sandbox.enabled, false);
  assert.deepEqual(load("R12", "pos").effective.flags.skipDangerousModePermissionPrompt, { value: true, scope: "user" });
  assert.deepEqual(load("R10", "pos").effective.additionalDirectories, [{ path: "~/.claude", scope: "user" }]);
});

test("cwd === home: projectLocal is ~/.claude/settings.local.json, shared not double counted", () => {
  const home = join(FIX, "R1", "pos", "home");
  const { scopes, effective } = loadScopes({ cwd: home, home, managedPaths: [] });
  assert.equal(scopes.find((s) => s.name === "projectLocal").file, join(home, ".claude", "settings.local.json"));
  assert.equal(effective.allow.length, 1);
});

test("managed paths: file and managed-settings.d are loaded, highest precedence", () => {
  const dir = join(FIX, "precedence", "pos", "home", ".claude");
  const { scopes } = loadScopes({ cwd: "/nonexistent", home: "/nonexistent", managedPaths: [join(dir, "settings.json")] });
  assert.equal(scopes[0].name, "managed");
  assert.equal(scopes[0].exists, true);
});
