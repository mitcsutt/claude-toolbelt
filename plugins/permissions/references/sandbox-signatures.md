# Sandbox Denial Signatures

Catalog of every regex in `plugins/permissions/lib/signatures.mjs`. Consumed by the `sandbox-watch` PostToolUse hook (which writes matches to `~/.claude/sandbox-denials.jsonl`) and by the `/sandbox-fix` skill (which reads this file as a lookup dictionary when proposing a fix). When you add a new signature, update both `lib/signatures.mjs` and this catalog so the skill can resolve the `id` to a recommended remediation.

## How to read an entry

Each subsection documents one signature:

- **regex** — the literal pattern from `signatures.mjs`.
- **category** — `fs-perm` or `ssh` (matches the `category` field on the entry).
- **fix** — recommended remediation, one of:
  - `claude-sandbox-allowwrite` — add the matched parent directory to `permissions.sandbox.filesystem.allowWrite` in `~/.claude/settings.json`.
  - `dangerouslyDisableSandbox` — pre-set `dangerouslyDisableSandbox: true` on the offending command, or add the command head to `permissions.sandbox.excludedCommands`.
- **root cause** — why the macOS sandbox profile denies this operation.
- **empirical example** — a transcript citation from the May 2026 audit, or a note that the entry was added defensively.

Catalog order matters at runtime: cosmetic suppressors are listed first in `signatures.mjs` so they short-circuit before the broader `fs-eperm-*` patterns. The order below mirrors the source file.

## Real denials

### git-config-write
- regex: `/could not write config file [^\n]*\.git\/config: Operation not permitted/`
- category: fs-perm
- fix: claude-sandbox-allowwrite
- root cause: The agent shell runs under a sandbox profile that denies write to `.git/` subtrees outside the project's `allowWrite` list. `git config --local <key> <value>` opens `.git/config` for write and fails with EPERM.
- empirical example: no empirical citation in the May 2026 audit; signature added defensively from sandbox profile knowledge (any `git config --local` write in a non-allowlisted worktree will hit this).

### git-rename
- regex: `/fatal: renaming '[^']*' failed: Operation not permitted/`
- category: fs-perm
- fix: claude-sandbox-allowwrite
- root cause: Git uses `rename(2)` against files inside `.git/` (refs, packed-refs, index lock) during ref updates, commits, branch creation, and worktree teardown. The sandbox profile denies write on `.git/` paths it has not been told to allow.
- empirical example: `~/.claude/projects/-Users-mitchellsutton-Documents-projects-rise/024ada36-ea00-4bc0-89ea-62adc83870b5.jsonl:1490`.

### git-unlink
- regex: `/unable to unlink [^\n]*: Operation not permitted/`
- category: fs-perm
- fix: claude-sandbox-allowwrite
- root cause: Git calls `unlink(2)` on lock files and stale refs during commit / fetch / gc. Same sandbox restriction as `git-rename`.
- empirical example: no empirical citation in the May 2026 audit; signature added defensively from sandbox profile knowledge (paired with `git-rename` — same root cause, different syscall).

### git-worktree-delete
- regex: `/failed to delete '[^']*': Operation not permitted/`
- category: fs-perm
- fix: claude-sandbox-allowwrite
- root cause: `git worktree remove` and `git worktree prune` delete files under the linked worktree's `.git/worktrees/<name>/` directory. The sandbox profile denies write on these paths.
- empirical example: `~/.claude/projects/-Users-mitchellsutton-Documents-projects-rise/b8826f52-dfc1-4d16-b18b-00dd3cd9c503.jsonl:63`.

### ssh-known-hosts
- regex: `/hostkeys_foreach failed for [^\n]*\/\.ssh\/known_hosts/`
- category: ssh
- fix: dangerouslyDisableSandbox
- root cause: The sandbox profile denies read on `~/.ssh`. `ssh` (invoked transitively by `git fetch`, `git push`, `gh`) tries to read `known_hosts` for host key verification and emits this warning. Adding `~/.ssh` to `allowWrite` does not help — the deny is on read. The correct fix is to bypass the sandbox for the offending command via `dangerouslyDisableSandbox` or `excludedCommands: [git, gh, ssh]`.
- empirical example: `~/.claude/projects/-Users-mitchellsutton-Documents-projects-rise/418ae1f6-14b4-4ce2-b5f1-d6c9e71c0cf8.jsonl:25` (then retried with `dangerouslyDisableSandbox` at line 38).

### ssh-host-key
- regex: `/Host key verification failed/`
- category: ssh
- fix: dangerouslyDisableSandbox
- root cause: Same `~/.ssh` read denial as `ssh-known-hosts`. Without access to `known_hosts`, ssh cannot verify the remote host key and aborts the connection.
- empirical example: `~/.claude/projects/-Users-mitchellsutton-Documents-projects-rise/418ae1f6-14b4-4ce2-b5f1-d6c9e71c0cf8.jsonl:25`.

### ssh-publickey
- regex: `/Permission denied \(publickey\)/`
- category: ssh
- fix: dangerouslyDisableSandbox
- root cause: ssh cannot read the private key in `~/.ssh/id_*` under the sandbox profile, so key-based auth fails and the server rejects the connection with `Permission denied (publickey)`. Same fix as the other ssh signatures — exclude the command from the sandbox.
- empirical example: no empirical citation in the May 2026 audit; signature added defensively. Co-occurs with `ssh-known-hosts` / `ssh-host-key` when the agent's first `git push` lands in the sandbox.

### git-remote-fetch
- regex: `/Could not read from remote repository/`
- category: ssh
- fix: dangerouslyDisableSandbox
- root cause: Generic git error printed after ssh transport failure. Almost always downstream of `ssh-known-hosts`, `ssh-host-key`, or `ssh-publickey` — the actual cause is the sandbox blocking `~/.ssh` access, not a missing remote.
- empirical example: `~/.claude/projects/-Users-mitchellsutton-Documents-projects-rise/418ae1f6-14b4-4ce2-b5f1-d6c9e71c0cf8.jsonl:25`.

### fs-eperm-claude-dir
- regex: `/EPERM:[^\n]*'[^']*\/\.claude\/(agents|skills|worktrees)[^']*'/`
- category: fs-perm
- fix: claude-sandbox-allowwrite
- root cause: The sandbox profile treats `~/.claude/agents`, `~/.claude/skills`, and `~/.claude/worktrees` as read-only for the agent shell. Tools that write into these subtrees (plugin installers, worktree creation, skill scaffolding) fail with EPERM until the relevant directory is added to `allowWrite`.
- empirical example: no empirical citation in the May 2026 audit; signature added defensively from sandbox profile knowledge.

### fs-eperm-git-dir
- regex: `/EPERM:[^\n]*'[^']*\/\.git\/[^']*'/`
- category: fs-perm
- fix: claude-sandbox-allowwrite
- root cause: Broad catch-all for any EPERM writing under a `.git/` directory. Matches whenever git's structured error messages are bypassed (e.g. a hook or porcelain command surfaces the raw libuv error instead of git's own wording). Same underlying restriction as `git-rename` / `git-unlink`.
- empirical example: no empirical citation in the May 2026 audit; signature added defensively from sandbox profile knowledge.

### fs-eperm-claude-settings
- regex: `/EPERM:[^\n]*'[^']*\/\.claude\/settings\.json'/`
- category: fs-perm
- fix: claude-sandbox-allowwrite
- root cause: `~/.claude/settings.json` is read-only under the sandbox profile to prevent the agent from silently rewriting its own permission rules. Tools that legitimately need to edit it (the `/permissions-promote` skill, manual rule edits) must run with `dangerouslyDisableSandbox` or have `~/.claude/settings.json` added to `allowWrite`.
- empirical example: no empirical citation in the May 2026 audit; signature added defensively from sandbox profile knowledge.

## Cosmetic suppressors

These patterns fire constantly but the underlying command succeeds anyway. The hook returns `null` for them so they never reach the denial log and never trigger `/sandbox-fix`. They are listed first in `signatures.mjs` so they short-circuit before the broader `fs-eperm-*` rules.

### _cosmetic_npmrc
- regex: `/EPERM:[^\n]*open '[^']*\/\.npmrc'/`
- root cause: pnpm (and occasionally npm) probes for a user `.npmrc` at startup. When the sandbox denies the read, pnpm logs the EPERM and falls back to defaults. Install / run / build all complete normally.
- empirical example: 308 events across `~/.claude/projects/-Users-mitchellsutton-Documents-projects-rise/*.jsonl` in the May 2026 audit; every pnpm invocation produces one. Harmless.

### _cosmetic_vite_bind
- regex: `/EPERM:[^\n]*\b(bind|listen)\b[^\n]*\d+:\d+/`
- root cause: Vite's dev server retries `bind(2)` on adjacent ports when the requested port is in use. Under the sandbox the first attempt sometimes surfaces as EPERM instead of EADDRINUSE; Vite catches it, retries the next port, and the server comes up. The log line is noise.
- empirical example: no count recorded in the May 2026 audit; suppressor added defensively to avoid spamming the denial log on every `vite dev` start.

### _cosmetic_watchman
- regex: `/unable to talk to your watchman on/`
- root cause: Tools that probe for watchman (Jest, Metro, some Vite plugins) print this message when the watchman socket is unreachable from inside the sandbox. They fall back to polling-based file watching and continue running.
- empirical example: no count recorded in the May 2026 audit; suppressor added defensively to keep the denial log clean on machines without watchman or where the socket is sandboxed away.

## Adding a new signature

1. Add the entry to `lib/signatures.mjs` in catalog order — cosmetic suppressors first (so they short-circuit before broader EPERM rules), real denials after, grouped by category.
2. Add a matching test case in `lib/signatures.test.mjs` covering at least one positive match and one near-miss.
3. Add a `### <id>` subsection to this file with regex, category, fix, root cause, and an empirical example (or an explicit "added defensively" note if the audit has no citation).
