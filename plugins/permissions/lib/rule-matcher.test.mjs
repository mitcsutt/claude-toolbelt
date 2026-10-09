import { test } from "node:test";
import assert from "node:assert/strict";
import { homedir } from "node:os";
import { matchRule } from "./rule-matcher.mjs";

// --- Plan's 7 baseline tests (semantics per https://code.claude.com/docs/en/permissions) ---
// Note: the plan's bullet asserted `Bash(eval:*)` must return false for "eval foo",
// but the permissions docs (https://code.claude.com/docs/en/permissions) and the parallel `Bash(git:*)` test both require
// `Bash(cmd:*)` to match `cmd ARGS`. We treat the eval bullet as a transcription
// error and follow the docs here. See report.
test("Bash(eval:*) matches a literal 'eval foo' (per https://code.claude.com/docs/en/permissions)", () => {
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

test("Bash(git push --force*) matches git push --force-with-lease", () => {
  assert.equal(matchRule("Bash(git push --force*)", { tool: "Bash", detail: "git push --force-with-lease" }), true);
});

test("Bash(git push --force*) matches bare git push --force", () => {
  assert.equal(matchRule("Bash(git push --force*)", { tool: "Bash", detail: "git push --force" }), true);
});

test("Read(**/.env) matches nested path but not bare .env", () => {
  assert.equal(matchRule("Read(**/.env)", { tool: "Read", detail: "foo/bar/.env" }), true);
  assert.equal(matchRule("Read(**/.env)", { tool: "Read", detail: ".env" }), false);
});

// --- Compound handling (P5) ---
import { splitCompound, stripWrappers, commandHead } from "./rule-matcher.mjs";

test("splitCompound splits on all P5 separators", () => {
  assert.deepEqual(splitCompound("a && b || c ; d | e |& f & g\nh"), ["a", "b", "c", "d", "e", "f", "g", "h"]);
});

test("splitCompound respects quotes and redirects", () => {
  assert.deepEqual(splitCompound(`echo "a && b" | grep 'x;y'`), [`echo "a && b"`, `grep 'x;y'`]);
  assert.deepEqual(splitCompound("make 2>&1 | tee out &> log"), ["make 2>&1", "tee out &> log"]);
  assert.deepEqual(splitCompound(""), []);
});

test("stripWrappers removes env + wrappers repeatedly", () => {
  assert.equal(stripWrappers("FOO=1 timeout 30 nice -n 5 nohup git status"), "git status");
  assert.equal(stripWrappers("time command builtin noglob ls"), "ls");
  assert.equal(stripWrappers("stdbuf -oL xargs grep x"), "grep x");
  assert.equal(stripWrappers("A='x y' B=2 npm test"), "npm test");
});

test("stripWrappers keeps xargs with flags and a lone wrapper", () => {
  assert.equal(stripWrappers("xargs -I {} sh -c x"), "xargs -I {} sh -c x");
  assert.equal(stripWrappers("time"), "time");
});

test("commandHead: first word, plus subcommand for multi-command tools", () => {
  assert.equal(commandHead("git status && ls"), "git status");
  assert.equal(commandHead("FOO=1 timeout 5 npm run build"), "npm run");
  assert.equal(commandHead("git -C x status"), "git");
  assert.equal(commandHead("ls -la"), "ls");
  assert.equal(commandHead("cd x && git push"), "cd");
  assert.equal(commandHead(""), "");
});
