# Migration safety

Verified on 2026-11-19 against the sources in evidence.md; re-check live before a finding depends on a volatile fact (marked [V]).

Dated rules for schema migrations, by database and version. Every version fact carries [V]: re-read the cited doc page for the target version before a BLOCK rests on it. Evidence IDs resolve in `evidence.md`. Process death mid-migration, partial apply, re-runs, throttling and resume are paranoid-sre's; name it in Fix when your finding touches them.

## First: the database version

```bash
git grep -nE 'postgres(ql)?:[0-9]+|image: *postgres|POSTGRES_VERSION|sqlite' -- '*.yml' '*.yaml' '.github/' 'Dockerfile*' 'docs/'
```

Map each migration to the database it runs on (a directory, a header comment, a connection name). Several databases can run different versions. Version unknown: state both outcomes and grade by the worse.

## Postgres

(CK14, CK15, CK16)

- [V] **ADD COLUMN with a DEFAULT.**
  - PG ≤10: any DEFAULT rewrites the table and its indexes.
  - PG ≥11: a non-null constant default doesn't rewrite.
  - Every version: a volatile default rewrites. The ALTER TABLE page's example is `clock_timestamp()`; `random()` and `gen_random_uuid()` are also volatile (`provolatile => 'v'` in Postgres's `pg_proc.dat`). For another function, read its `provolatile` there.
  - `now()` is STABLE in `pg_proc.dat`, so a `DEFAULT now()` doesn't force a rewrite on PG ≥11.
- A rewrite runs under ACCESS EXCLUSIVE, which "Conflicts with locks of all modes" and is the only mode that blocks SELECT. Grade by table size where the repo states it.
- [V] **SET NOT NULL** scans the whole table unless a valid CHECK constraint proves no NULLs (PG 12+). Safe recipe: `ADD CONSTRAINT … CHECK (col IS NOT NULL) NOT VALID`, then `VALIDATE CONSTRAINT`, then `SET NOT NULL`.
- **CREATE INDEX** without CONCURRENTLY on a large table blocks writes. CONCURRENTLY "cannot" run inside a transaction block, and migration frameworks wrap each migration in one unless disabled (`disable_ddl_transaction!`, `atomic = false`). The INVALID index left by a failed build and the `IF NOT EXISTS` re-run are paranoid-sre's; mention them only in Fix.
- **No `SET lock_timeout`** before DDL that takes ACCESS EXCLUSIVE: the default 0 waits forever, and every later query queues behind the waiting DDL. strong_migrations' install generator writes a 10 s `lock_timeout` into the initializer; check whether the repo's initializer has it.
- **Rule ids to cite** (squawk; strong_migrations names the same operations): `adding-field-with-default`, `adding-not-nullable-field`, `require-concurrent-index-creation`, `constraint-missing-not-valid`, `renaming-column`, `require-lock-timeout`, `ban-drop-column`, `changing-column-type`.

## Renames and drops

(CK17)

- Expand, migrate, contract: add the new column, write both, backfill, move reads, stop writing the old one, then drop it.
- A rename or drop in the same release as the code that stops using it breaks old instances mid-rollout and breaks rollback.

## Backfills

(CK17)

- Correctness for existing rows that don't match the new shape: what value do they get, and does every reader accept it?
- An idempotent predicate (`WHERE new_col IS NULL`) so a second pass only touches what's left.
- Batching outside a transaction. Throttling under load and resume after a kill are paranoid-sre's.
- "Strong Migrations does not detect dangerous backfills": read them yourself.

## SQLite

(CK18)

- ALTER TABLE supports only rename table, rename column, add column and drop column. Anything else needs the 12-step table rebuild.
- ADD COLUMN may not default to `CURRENT_TIME` or another expression; with NOT NULL it needs a non-NULL default.
- DROP COLUMN fails if the column is indexed, used in a view or trigger, and in other listed cases.

## Evidence gap

Expand/contract has practitioner consensus but no controlled study (CK17). Don't claim a measured benefit.
