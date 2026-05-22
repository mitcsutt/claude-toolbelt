import { test } from "node:test";
import assert from "node:assert/strict";
import { matchSignature, SIGNATURES } from "./signatures.mjs";

// Cosmetic suppressors return their sig from matchSignature; classify-denial.mjs filters them via sig.suppress.
const cases = [
  ["error: could not write config file .git/config: Operation not permitted", "git-config-write"],
  ["fatal: renaming 'foo' failed: Operation not permitted", "git-rename"],
  ["hostkeys_foreach failed for /Users/m/.ssh/known_hosts: Operation not permitted", "ssh-known-hosts"],
  ["Host key verification failed.", "ssh-host-key"],
  ["Could not read from remote repository.", "git-remote-fetch"],
  ["Permission denied (publickey).", "ssh-publickey"],
  ["EPERM: operation not permitted, mkdir '/Users/m/proj/.claude/worktrees/x'", "fs-eperm-claude-dir"],
  ["EPERM: operation not permitted, open '/Users/m/.npmrc'", "_cosmetic_npmrc"],
  ["EPERM: operation not permitted, bind 0.0.0.0:24678", "_cosmetic_vite_bind"],
  ["failed to delete '.claude/worktrees/x': Operation not permitted", "git-worktree-delete"],
  ["unable to unlink old 'a': Operation not permitted", "git-unlink"],
  ["unable to talk to your watchman on /tmp/watchman-state/sock", "_cosmetic_watchman"],
  ["EPERM: operation not permitted, open '/Users/m/proj/.git/config'", "fs-eperm-git-dir"],
  ["EPERM: operation not permitted, open '/Users/m/.claude/settings.json'", "fs-eperm-claude-settings"],
  ["touch: /Users/m/blocked: Permission denied", "coreutil-permission-denied"],
  ["mkdir: /opt/blocked: Permission denied", "coreutil-permission-denied"],
  ["normal compiler output mentioning permission denied for some fixture", null],
];

for (const [output, expected] of cases) {
  test(`matchSignature: ${output.slice(0, 60)}`, () => {
    const hit = matchSignature(output);
    assert.equal(hit?.id ?? null, expected);
  });
}

test("SIGNATURES catalog has unique ids", () => {
  const ids = SIGNATURES.map((s) => s.id);
  assert.equal(ids.length, new Set(ids).size);
});
