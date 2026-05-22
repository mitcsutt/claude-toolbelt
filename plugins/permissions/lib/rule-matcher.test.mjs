import { test } from "node:test";
import assert from "node:assert/strict";
import { homedir } from "node:os";
import { matchRule } from "./rule-matcher.mjs";

// --- Plan's 7 baseline tests (semantics per references/rule-syntax.md) ---
// Note: the plan's bullet asserted `Bash(eval:*)` must return false for "eval foo",
// but references/rule-syntax.md and the parallel `Bash(git:*)` test both require
// `Bash(cmd:*)` to match `cmd ARGS`. We treat the eval bullet as a transcription
// error and follow the reference doc here. See report.
test("Bash(eval:*) matches a literal 'eval foo' (per rule-syntax.md)", () => {
  assert.equal(matchRule("Bash(eval:*)", { tool: "Bash", detail: "eval foo" }), true);
});

test("Bash(rm -rf *) matches rm -rf .lostpixel/", () => {
  assert.equal(matchRule("Bash(rm -rf *)", { tool: "Bash", detail: "rm -rf .lostpixel/" }), true);
});

test("Bash(git:*) matches git status", () => {
  assert.equal(matchRule("Bash(git:*)", { tool: "Bash", detail: "git status" }), true);
});

test("Bash(git:*) matches git worktree add x y", () => {
  assert.equal(matchRule("Bash(git:*)", { tool: "Bash", detail: "git worktree add x y" }), true);
});

test("Bash(npm:*) does not match pnpm install", () => {
  assert.equal(matchRule("Bash(npm:*)", { tool: "Bash", detail: "pnpm install" }), false);
});

test("Read(~/.ssh/**) matches ~/.ssh/id_rsa", () => {
  assert.equal(
    matchRule("Read(~/.ssh/**)", { tool: "Read", detail: `${homedir()}/.ssh/id_rsa` }),
    true,
  );
});

test("Read(~/.ssh/**) matches subdirectories", () => {
  assert.equal(
    matchRule("Read(~/.ssh/**)", { tool: "Read", detail: `${homedir()}/.ssh/sub/key` }),
    true,
  );
});

test("Read matches bare tool name without arg", () => {
  assert.equal(matchRule("Read", { tool: "Read", detail: "/x" }), true);
});

test("Edit matches bare tool name without arg", () => {
  assert.equal(matchRule("Edit", { tool: "Edit", detail: "/foo/bar" }), true);
});

test("mcp__plugin_buildkite_* matches mcp__plugin_buildkite_buildkite__list_builds", () => {
  assert.equal(
    matchRule("mcp__plugin_buildkite_*", {
      tool: "mcp__plugin_buildkite_buildkite__list_builds",
      detail: "",
    }),
    true,
  );
});

// --- Additional tests from the task spec ---
test("strips env-var prefix before Bash matching", () => {
  assert.equal(
    matchRule("Bash(git:*)", { tool: "Bash", detail: "DEBUG=1 NODE_ENV=test git status" }),
    true,
  );
});

test("Bash(git:*) matches bare 'git' (no args) per colon-prefix semantics", () => {
  assert.equal(matchRule("Bash(git:*)", { tool: "Bash", detail: "git" }), true);
});

test("Bash(rm -rf *) does NOT match rm -r ./tmp (flag mismatch)", () => {
  assert.equal(matchRule("Bash(rm -rf *)", { tool: "Bash", detail: "rm -r ./tmp" }), false);
});

test("Read(.env) matches literal .env path", () => {
  assert.equal(matchRule("Read(.env)", { tool: "Read", detail: ".env" }), true);
});

test("returns false for malformed rule", () => {
  assert.equal(matchRule("not-a-real-rule(", { tool: "Bash", detail: "anything" }), false);
});

// --- Edge cases reinforcing the reference doc ---
test("tool mismatch returns false even with matching arg pattern", () => {
  assert.equal(matchRule("Bash(git:*)", { tool: "Read", detail: "git status" }), false);
});

test("Bash(cmd) with no arg matches bare 'cmd' only", () => {
  assert.equal(matchRule("Bash(cmd)", { tool: "Bash", detail: "cmd" }), true);
});

test("Bash(cmd) with no arg does not match 'cmd extra'", () => {
  assert.equal(matchRule("Bash(cmd)", { tool: "Bash", detail: "cmd extra" }), false);
});

test("Write(src/**) matches nested file", () => {
  assert.equal(matchRule("Write(src/**)", { tool: "Write", detail: "src/lib/foo.ts" }), true);
});

test("Edit(src/**/*.ts) matches TypeScript files only", () => {
  assert.equal(matchRule("Edit(src/**/*.ts)", { tool: "Edit", detail: "src/a/b.ts" }), true);
});

test("Edit(src/**/*.ts) does not match .js files", () => {
  assert.equal(matchRule("Edit(src/**/*.ts)", { tool: "Edit", detail: "src/a/b.js" }), false);
});

test("mcp__exa__* matches any tool on exa server", () => {
  assert.equal(
    matchRule("mcp__exa__*", { tool: "mcp__exa__web_search_exa", detail: "" }),
    true,
  );
});

test("mcp__exa__* does not match a different server's tools", () => {
  assert.equal(
    matchRule("mcp__exa__*", { tool: "mcp__other__web_search", detail: "" }),
    false,
  );
});

test("Bash(npm run:*) matches npm run build", () => {
  assert.equal(matchRule("Bash(npm run:*)", { tool: "Bash", detail: "npm run build" }), true);
});

test("Bash(npm run:*) does not match plain npm install", () => {
  assert.equal(matchRule("Bash(npm run:*)", { tool: "Bash", detail: "npm install" }), false);
});

test("matching is case-sensitive", () => {
  assert.equal(matchRule("Bash(Git:*)", { tool: "Bash", detail: "git status" }), false);
});
