#!/usr/bin/env bash
# Seeds a build plan whose technical claims include 6 planted WRONG claims and
# 2 TRUE-but-surprising controls. Ground truth checked 2026-11-20; each grader
# in case.yaml names its line as PLAN.md:<n> (tests/all.sh checks the mapping).
set -euo pipefail

cat > PLAN.md <<'EOF'
# tsnotes: build plan

tsnotes is a small note-taking CLI: a Node front end, a Python indexer and bash helpers, with CI on GitHub Actions and a Claude Code setup for contributors.

## Runtimes

- Node: `.nvmrc` pins 22.20.0 for the CLI. CI also runs the TypeScript entry point on Node 24.
- Python: the indexer targets Python 3.15.
- Shell helpers target bash 5.3 or later and GNU coreutils 9.5 or later.
- Contributors use Claude Code 2.1.300 or later.

## Design notes

1. Python still has no built-in immutable mapping type, so the indexer wraps its config in `types.MappingProxyType`.
2. On Python 3.15, `open()` without `encoding=` still uses the locale encoding (cp1252 on Windows), so the Windows CI job sets `PYTHONUTF8=1`.
3. `bin/tsnotes.ts` uses type annotations only (no enums, no namespaces) and is started with `node --experimental-strip-types`, because Node 24 can't run a `.ts` file without that flag.
4. `cli.cjs` loads `chalk` 5 with a dynamic `import()`, because an ESM-only package can't be `require()`d on Node 22.20: it throws ERR_REQUIRE_ESM.
5. With GNU coreutils 9.5 or later, `cp -n` exits 1 when it skips an existing file, so `install.sh` wraps it in `|| true`.
6. `lib/count.sh` needs bash 5.3 or later: it builds its summary with `summary=${ print_counts; }`, a command substitution that captures the shell function's output without forking a child process.
7. Claude Code ignores `AGENTS.md`, so `setup.sh` symlinks `CLAUDE.md` to `AGENTS.md`.
8. `.claude/agents/reviewer.md` lists `Agent(explorer)` in `tools`. That type list is ignored when the reviewer runs as a subagent, so "spawns only explorer" is enforced by its prompt, not by the platform.

## Out of scope

Sync, encryption and a GUI.
EOF
