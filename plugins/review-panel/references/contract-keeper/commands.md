# Commands and census budget

Verified on 2026-11-19 against the sources in evidence.md; re-check live before a finding depends on a volatile fact (marked [V]).

The only commands contract-keeper runs, and how far the consumer census goes. Every command is read-only on the repo. Temp files go in `T=$(mktemp -d)`, removed afterwards. Evidence IDs resolve in `evidence.md`.

## Allowed

- git: `git rev-parse`, `git merge-base`, `git diff`, `git show <ref>:<path>`, `git grep`, `git log --oneline` and `git log -S`, `git ls-files`, `git tag -l`.
- Text: `grep`, `wc`, `sed -n`, `head`, `sort`, `uniq`, `diff`, `find` (read-only), `mktemp -d`; `yq` if installed.
- Snippets: `node -e` and `python3 -I -c` that read only files given as arguments, import no repo code and touch no network (for example `node -e 'console.log(new Date(null).toISOString())'` to show what a consumer does with null).
- T2 only: a no-op probe of a consumer tool that is already installed, in a form that installs nothing (`exec true`, `--help`, `--version`, a dry run).

## Forbidden

`psql`, `sqlite3` on repo database files, any migration runner, `docker`, `systemctl`, `curl`, `wget`, package-manager `install`/`add`/`update`/`dlx`, `npx <pkg>`, and anything that writes outside `$T` or your report file.

## Base ref

The brief's base, else `git merge-base HEAD origin/main` (or `main`, `master`; check each with `git rev-parse --verify`). Say which you used.

## Checks

From R12 §3. Each has its trap: a check that comes back empty isn't proof until its trap is ruled out.

- **C1, changed surface list.** `git diff --name-status <base>...HEAD`; `git diff -U0 <base>...HEAD -- '*.yaml' '*.json' '*.proto' '*.graphql' '*.sql' '*.d.ts' 'migrations/**'`. Trap: Generated files mask hand edits.
- **C2, OpenAPI / JSON Schema diff.** check `yq --version` first: mikefarah yq takes `diff <(git show <base>:api/openapi.yaml | yq -o=json 'sort_keys(..)') <(yq -o=json 'sort_keys(..)' api/openapi.yaml)`, the Python yq takes `yq -S . <file>`; else `python3 -I -c 'import json,sys;…'` with sorted keys; classify each hunk by direction (`break-rules.md`). Trap: `$ref` changes hide behind refs: resolve them before diffing. yq may be missing.
- **C3, exported TS/JS surface.** `diff <(git show <base>:etc/pkg.api.md) etc/pkg.api.md` if API Extractor is used, else `git diff <base>...HEAD -- '*.d.ts' | grep -E '^[-+]\s*export'`. Trap: Re-exports via `export *`.
- **C4, removed env vars.** `git diff <base>...HEAD | grep -E '^-' | grep -oE '(process\.env(\.|\[["'\''])|import\.meta\.env\.|os\.environ(\.get)?\(?\[?["'\'']?|[Gg]etenv\(["'\'']|\$\{?)[A-Z][A-Z0-9_]{2,}' | grep -oE '[A-Z][A-Z0-9_]{2,}$' | sort -u`, then per name `git grep -n NAME -- . ':!*.lock'`. Trap: names built dynamically, and other languages' accessors the pattern lacks; readers in other repos or `/etc/default/*`: say "not checked".
- **C5, removed CLI flags, config keys, defaults.** `git diff <base>...HEAD | grep -E '^-.*(--[a-z][a-z0-9-]+|add_argument|\.option\(|getopts)'`; config keys by diffing `git show <base>:<file>` against the file; diff `default=` lines explicitly. Trap: Default changes are silent breaks (CK5).
- **C6, consumer count.** `git grep -c -w '<name>' -- . ':!<changed-file>'`, then `git grep -n '<name>' -- '*.sh' '*cron*' 'docs/' '*.md' 'test*'`. Trap: A count isn't usage: read the hits. Zero hits is "no in-repo consumer", never "no consumer" (external clients, old mobile builds).
- **C7, migration lint by eye.** grep the SQL for `ADD COLUMN .* DEFAULT`, `SET NOT NULL`, `CREATE (UNIQUE )?INDEX` without `CONCURRENTLY`, `CONCURRENTLY` inside `BEGIN`, `RENAME`, `DROP COLUMN`, `ALTER TYPE`, a missing `SET lock_timeout`; then the DB-version grep in `migrations.md`. Trap: Frameworks wrap migrations in a transaction unless disabled.

## Census budget

From CK21 and CK22: the one real run spent its time on whole-file reads and library internals, not on the census.

- At most 12 interfaces, most-reached first.
- Per name: count first, then read at most 5 hits in `sed -n` windows of about 40 lines. Non-test code, scripts, cron and clients come before tests.
- No whole-file read over 200 lines, unless the file is the interface itself.
- Never read `node_modules/`, `vendor/`, `dist/`, `out/`, `.generated/` or other build output. How a library behaves is fact-checker's.
- Soft cap of 40 tool calls at T1. Past it, list what's left under `## Coverage` with its commands.

## Dynamic keys

`env[name]`, `getattr`, string-built routes and keys evade grep. Write "consumer unknown". At T2, try `git log -S'<name>' --oneline | head`.
