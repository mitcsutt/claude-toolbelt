import { test } from "node:test";
import assert from "node:assert/strict";
import { classifyDenial } from "./classify-denial.mjs";

test("extracts matched path from EPERM mkdir under .claude/worktrees", () => {
  const r = classifyDenial({
    command: "git worktree add .claude/worktrees/foo bar",
    stderr: "EPERM: operation not permitted, mkdir '/Users/m/p/.claude/worktrees/foo'",
    excludedCommands: [],
  });
  assert.equal(r.signature, "fs-eperm-claude-dir");
  assert.equal(r.matched_path, "/Users/m/p/.claude/worktrees/foo");
  assert.equal(r.category, "fs-perm");
});

test("returns null when stderr matches only a cosmetic suppressor", () => {
  const r = classifyDenial({
    command: "pnpm install",
    stderr: "EPERM: operation not permitted, open '/Users/m/.npmrc'",
    excludedCommands: [],
  });
  assert.equal(r, null);
});

test("tags macos-posix when command is in excludedCommands list", () => {
  const r = classifyDenial({
    command: "git push origin main",
    stderr: "Host key verification failed.\nCould not read from remote repository.",
    excludedCommands: ["git", "gh", "pnpm", "npx"],
  });
  assert.equal(r.signature, "ssh-host-key");
  assert.equal(r.category, "macos-posix");
});

test("strips env-var prefix when computing command_head", () => {
  const r = classifyDenial({
    command: "DEBUG=1 NODE_ENV=test git worktree add x y",
    stderr: "EPERM: operation not permitted, mkdir '/p/.claude/worktrees/x'",
    excludedCommands: ["git"],
  });
  assert.equal(r.command_head, "git");
  assert.equal(r.category, "macos-posix");
});

test("returns null when stderr does not match any signature", () => {
  const r = classifyDenial({
    command: "ls /tmp",
    stderr: "ls: /tmp/missing: No such file or directory",
    excludedCommands: [],
  });
  assert.equal(r, null);
});
