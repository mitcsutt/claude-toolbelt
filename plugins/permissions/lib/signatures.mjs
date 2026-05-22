// Order matters: cosmetic suppressors first so they short-circuit before broad EPERM rule.
export const SIGNATURES = [
  // ── cosmetic suppressors (return null, no log entry) ────────────────────
  { id: "_cosmetic_npmrc", suppress: true, re: /EPERM:[^\n]*open '[^']*\/\.npmrc'/ },
  { id: "_cosmetic_vite_bind", suppress: true, re: /EPERM:[^\n]*\b(bind|listen)\b[^\n]*\d+:\d+/ },
  { id: "_cosmetic_watchman", suppress: true, re: /unable to talk to your watchman on/ },

  // ── real denials ────────────────────────────────────────────────────────
  { id: "git-config-write",     re: /could not write config file [^\n]*\.git\/config: Operation not permitted/, category: "fs-perm",  fix: "claude-sandbox-allowwrite" },
  { id: "git-rename",           re: /fatal: renaming '[^']*' failed: Operation not permitted/,                  category: "fs-perm",  fix: "claude-sandbox-allowwrite" },
  { id: "git-unlink",           re: /unable to unlink [^\n]*: Operation not permitted/,                          category: "fs-perm",  fix: "claude-sandbox-allowwrite" },
  { id: "git-worktree-delete",  re: /failed to delete '[^']*': Operation not permitted/,                         category: "fs-perm",  fix: "claude-sandbox-allowwrite" },
  { id: "ssh-known-hosts",      re: /hostkeys_foreach failed for [^\n]*\/\.ssh\/known_hosts/,                   category: "ssh",      fix: "dangerouslyDisableSandbox" },
  { id: "ssh-host-key",         re: /Host key verification failed/,                                              category: "ssh",      fix: "dangerouslyDisableSandbox" },
  { id: "ssh-publickey",        re: /Permission denied \(publickey\)/,                                           category: "ssh",      fix: "dangerouslyDisableSandbox" },
  { id: "git-remote-fetch",     re: /Could not read from remote repository/,                                     category: "ssh",      fix: "dangerouslyDisableSandbox" },
  { id: "fs-eperm-claude-dir",  re: /EPERM:[^\n]*'[^']*\/\.claude\/(agents|skills|worktrees)[^']*'/,            category: "fs-perm",  fix: "claude-sandbox-allowwrite" },
  { id: "fs-eperm-git-dir",     re: /EPERM:[^\n]*'[^']*\/\.git\/[^']*'/,                                        category: "fs-perm",  fix: "claude-sandbox-allowwrite" },
  { id: "fs-eperm-claude-settings", re: /EPERM:[^\n]*'[^']*\/\.claude\/settings\.json'/,                        category: "fs-perm",  fix: "claude-sandbox-allowwrite" },
  { id: "coreutil-permission-denied", re: /^(touch|mkdir|cp|mv|rm|ln|chmod|chown): [^\n]+: Permission denied/m, category: "fs-perm",  fix: "claude-sandbox-allowwrite" },
];

export function matchSignature(text) {
  if (!text) return null;
  for (const sig of SIGNATURES) {
    if (sig.re.test(text)) return sig;
  }
  return null;
}
