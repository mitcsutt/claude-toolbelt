import { test } from "node:test";
import assert from "node:assert/strict";
import { homedir } from "node:os";
import { matchRule, splitCompound, stripWrappers, commandHead } from "./rule-matcher.mjs";

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

test("Read(**/.env) matches nested path and bare .env (zero dirs, gitignore semantics)", () => {
  assert.equal(matchRule("Read(**/.env)", { tool: "Read", detail: "foo/bar/.env" }), true);
  assert.equal(matchRule("Read(**/.env)", { tool: "Read", detail: ".env" }), true);
});

// --- Compound handling (P5) ---

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
  assert.equal(commandHead("cd x && git push"), "git push"); // cd is setup, skipped (3.1)
  assert.equal(commandHead(""), "");
});

// ---- 3.1 fix round (audit3 S1-S5, audit1 #7, #12) -----------------------------------

const B = (rule, detail, extra = {}) => matchRule(rule, { tool: "Bash", detail, ...extra });

test("S1: trailing ' *' as the only wildcard also matches the bare command", () => {
  assert.equal(B("Bash(ls *)", "ls"), true);
  assert.equal(B("Bash(npm run *)", "npm run"), true);
  assert.equal(B("Bash(ls *)", "lsof"), false);
  assert.equal(B("Bash(* --help *)", "npm --help"), false);
  assert.equal(B("Bash(git:*)", "git"), true);
});

test("S2: bare mcp__server matches every tool of that server only", () => {
  assert.equal(matchRule("mcp__srv", { tool: "mcp__srv__t" }), true);
  assert.equal(matchRule("mcp__srv", { tool: "mcp__srv2__t" }), false);
  assert.equal(matchRule("mcp__srv", { tool: "mcp__srv" }), true);
  assert.equal(matchRule("mcp__srv__t", { tool: "mcp__srv__u" }), false);
});

test("S3: env stripping: allow strips only safe vars, deny (anyEnv) strips any", () => {
  assert.equal(B("Bash(git status)", "PATH=/evil git status"), false);
  assert.equal(B("Bash(git status)", "LD_PRELOAD=x git status"), false);
  assert.equal(B("Bash(git status)", "FOO=$(x) git status"), false);
  assert.equal(B("Bash(git status)", "NODE_ENV=test LC_ALL=C TZ=UTC git status"), true);
  assert.equal(B("Bash(git status)", "NODE_ENV=$(x) git status"), false);
  assert.equal(B("Bash(git status)", "PATH=/evil git status", { anyEnv: true }), true);
  assert.equal(B("Bash(rm *)", 'FOO="a b" rm -rf tmp', { anyEnv: true }), true);
});

test("stripWrappers: quoted and $( ) env values are consumed whole (audit1 #7)", () => {
  assert.equal(stripWrappers('N="/p/iso v7.6.2 - React admin"; x'), 'N="/p/iso v7.6.2 - React admin"; x'); // `;` is not whitespace: not an env prefix
  assert.equal(stripWrappers('N="a b c" x'), "x");
  assert.equal(stripWrappers("R=$(ls -d x*)/y cmd args"), "cmd args");
  assert.equal(stripWrappers("J=`readlink -f a` cmd"), "cmd");
  assert.equal(stripWrappers("A='x y' B=2 npm test"), "npm test");
  assert.equal(stripWrappers("FOO=bar"), "FOO=bar");
  assert.equal(stripWrappers("PATH=x git status", { forAllow: true }), "PATH=x git status");
  assert.equal(stripWrappers("CI=1 PATH=x git status", { forAllow: true }), "PATH=x git status");
  assert.equal(stripWrappers("timeout -s KILL 5 rm x"), "rm x");
  assert.equal(stripWrappers("command -v rm"), "command -v rm");
});

test("splitCompound: no splits inside $( ), ( ), backticks, comments, heredocs; >| is a redirect", () => {
  assert.deepEqual(splitCompound("echo $(rm -rf x; ls)"), ["echo $(rm -rf x; ls)"]);
  assert.deepEqual(splitCompound("(cd x; ls) && pwd"), ["(cd x; ls)", "pwd"]);
  assert.deepEqual(splitCompound("echo `a;b` ; ls"), ["echo `a;b`", "ls"]);
  assert.deepEqual(splitCompound("echo x >| f"), ["echo x >| f"]);
  assert.deepEqual(splitCompound("x <<EOF\na;b\nEOF"), ["x <<EOF"]);
  assert.deepEqual(splitCompound("cat <<EOF && ls\nbody; rm\nEOF\npwd"), ["cat <<EOF", "ls", "pwd"]);
  assert.deepEqual(splitCompound("echo # c; rm"), ["echo"]);
  assert.deepEqual(splitCompound('git commit -m "$(cat <<\'EOF\'\nmsg ) ; here\nEOF\n)" && ls'), ['git commit -m "$(cat <<\'EOF\'\nmsg ) ; here\nEOF\n)"', "ls"]);
  assert.deepEqual(splitCompound('NR="$(gh api user --jq \'"\\(.id)"\')" && git status'), ['NR="$(gh api user --jq \'"\\(.id)"\')"', "git status"]);
});

test("S5: path rule resolution", () => {
  const ctx = { cwd: "/proj", home: "/home/u" };
  const R = (rule, detail, extra = {}) => matchRule(rule, { tool: "Read", detail, ...ctx, ...extra });
  assert.equal(R("Read(//etc/**)", "/etc/passwd"), true);
  assert.equal(R("Read(/src/**)", "/src/a"), false);
  assert.equal(R("Read(/src/**)", "/proj/src/a"), true);
  assert.equal(R("Read(/src/**)", "/home/u/.claude/src/a", { root: "/home/u/.claude" }), true); // user-scope anchor
  assert.equal(R("Read(**/.env)", ".env"), true);
  assert.equal(R("Read(./.env)", ".env"), true);
  assert.equal(R("Read(*.pem)", "/proj/a/b/k.pem"), true);
  assert.equal(R("Read(*.pem)", "/elsewhere/k.pem"), false);
  assert.equal(R("Read(~/.ssh/**)", "~/.ssh/id"), true);
  assert.equal(R("Read(src/*.ts)", "src/a.ts"), true);
  assert.equal(R("Read(src/*.ts)", "src/x/a.ts"), false);
  assert.equal(matchRule("Write(//etc/**)", { tool: "Edit", detail: "/etc/x" }), true);
  assert.equal(matchRule("Edit(//etc/**)", { tool: "MultiEdit", detail: "/etc/x" }), true);
  assert.equal(matchRule("Edit", { tool: "Write", detail: "x" }), true);
});

test("commandHead skips cd/export/VAR=/loops to the real command (audit1 #12)", () => {
  assert.equal(commandHead('cd ~/p && NR="$(gh api user --jq .id)" && git config user.email x'), "git config");
  assert.equal(commandHead("U=~/.claude/settings.json; T=$(mktemp); jq '.a' > $T && mv $T $U"), "jq");
  assert.equal(commandHead("F=/Users/x/firstmate; $F/bin/fm-pr-merge.sh --pr 1"), "$F/bin/fm-pr-merge.sh");
  assert.equal(commandHead("export S=nmwc; E=/tmp/e; mkdir -p $E; chrome-devtools-axi open x"), "chrome-devtools-axi");
  assert.equal(commandHead("T=/var/folders/8l/GPE2Z; W=$PWD; for d in a b; do ln -s $d $T; done"), "ln");
  assert.equal(commandHead("cd ~/.treehouse/w/w; gh-axi variable list"), "gh-axi variable");
  assert.equal(commandHead("cd x"), "cd");
  for (const c of ['R=$(ls -d x*)/y cmd args', 'J=$(readlink -f ~/.nvm/z) tool a', 'N="a v7.6.2 - b" ; real x']) {
    assert.ok(!commandHead(c).startsWith("-") && !/^v\d/.test(commandHead(c)), c);
  }
});

test("stripWrappers: timeout forms, and no catastrophic backtracking (CodeQL js/redos)", () => {
  assert.equal(stripWrappers("timeout 5 npm test"), "npm test");
  assert.equal(stripWrappers("timeout -s KILL 5 npm test"), "npm test");
  assert.equal(stripWrappers("timeout --kill-after 2 -v 10s npm test"), "npm test");
  assert.equal(stripWrappers("timeout -k 1 --preserve-status 3 git status"), "git status");
  const evil = "timeout " + "-k -! ".repeat(5000) + "x";
  const t0 = Date.now();
  stripWrappers(evil);
  assert.ok(Date.now() - t0 < 200, `took ${Date.now() - t0} ms`);
});
