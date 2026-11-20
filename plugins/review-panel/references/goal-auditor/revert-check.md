# goal-auditor T2: the revert check

Verified on 2026-11-19 (git 2.43.0, GNU tar 1.35, Node 24.14.1, Linux). Re-check live before a finding depends on a volatile fact.

Read this before any T2 run. Evidence IDs resolve in `evidence.md` (this directory).

## 1. What it proves (GA3)

FAIL_TO_PASS for each new or changed test, plus one PASS_TO_PASS suite run on "after". The only mutant that matters is the revert of the fix (R10 F10: a mutation suite costs too much for a review pass).

## 2. Skip it, and say so under Coverage, when

- T1 already settled it;
- the change is docs-only, or no test changed;
- the test needs a database, docker, a non-localhost URL or an env secret (`grep -nE 'https?://|DATABASE_URL|docker|localhost:[0-9]+|process\.env|os\.environ' <test files>`);
- dependencies would need an install;
- the change comes from an untrusted outside contributor;
- the lockfile changed and the fix *is* the dependency change.

## 3. Preflight

- `git rev-parse --verify "$BASE^{commit}"` (and FIX unless FIX=WORKTREE).
- Find the per-file test command in the repo's config (`package.json` scripts, `pytest.ini`/`pyproject.toml`, Makefile). Read it; don't guess. Known per-file forms: `node --test <file>`; `<venv>/bin/python -m pytest -q -p no:cacheprovider <file>`, with `PYTHONDONTWRITEBYTECODE=1` added to the `env -i` line.
- Dependencies: `du -sm node_modules`. At ≤200 MB, `cp -a node_modules "$T/before/"` and `"$T/after/"` after the copy step. Never symlink it: caches such as `node_modules/.cache` would write into the real tree (GA10). Above 200 MB, don't run. The 200 MB threshold is a judgement call, not evidence (R10 §5 open question).
- If `git diff --quiet "$BASE" "$FIX" -- package-lock.json pnpm-lock.yaml yarn.lock poetry.lock uv.lock 'requirements*.txt'` exits non-zero (for FIX=WORKTREE, `git diff-index --quiet "$BASE" -- <same files>`, which can over-report a file whose timestamp changed), "before" runs against "after"'s deps. Say so next to the result.

## 4. The recipe

Copy it verbatim; the plugin's tests run this exact block. Set `BASE`, `FIX` (a commit, or `WORKTREE` for uncommitted work), `TESTS` (changed test files, space-separated, no spaces in names) and `RUN` (the per-file test command). Run it from the repo root in one Bash call with a timeout of at most 300000 ms.

```bash
# revert-check v1
export GIT_OPTIONAL_LOCKS=0 GIT_LFS_SKIP_SMUDGE=1
T=$(mktemp -d "${TMPDIR:-/tmp}/ga-revert.XXXXXX"); mkdir "$T/before" "$T/after" "$T/home"
git archive "$BASE" | tar -x -C "$T/before"
if [ "$FIX" = WORKTREE ]; then
  git ls-files -co --exclude-standard -z | tar --null -T - -cf - 2>/dev/null | tar -x -C "$T/after"
else
  git archive "$FIX" | tar -x -C "$T/after"
fi
for f in $TESTS; do mkdir -p "$T/before/$(dirname "$f")"; cp "$T/after/$f" "$T/before/$f"; done
for side in before after; do
  for f in $TESTS; do
    (cd "$T/$side" && env -i PATH="$PATH" HOME="$T/home" TMPDIR="$T" LANG=C.UTF-8 $RUN "$f") > "$T/$side.$(echo "$f" | tr / _).log" 2>&1
    echo "$side $f exit=$?"
  done
done
echo "logs: $T"
```

## 5. Read the result

`grep -nE 'AssertionError|assert|Expected|FAILED|✖|not ok' "$T"/before.*.log | head -20`.

- before ≠0 and the *named* new test fails on an assertion, after 0: covered. The evidence is the two exits plus the failing assertion line.
- before 0: vacuous test. This is a finding (SHOULD, per the severity anchors in review-method.md) with the run as evidence.
- before ≠0 at import, syntax or setup: inconclusive, not evidence (GA10). Say so.
- after ≠0: "tests pass" is false. This is a finding.

## 6. PASS_TO_PASS

`(cd "$T/after" && env -i PATH="$PATH" HOME="$T/home" TMPDIR="$T" LANG=C.UTF-8 <full test cmd>) > "$T/after.full.log" 2>&1; echo "after full exit=$?"`.

- If it fails, rerun once to rule out a flake.
- If it still fails, run the same command in `$T/before`, which tells a regression from a pre-existing failure.

## 7. Limits (GA10)

- `export-ignore` files are missing from both copies.
- A submodule comes out as an empty directory (observed). `git archive` runs configured smudge filters; `GIT_LFS_SKIP_SMUDGE=1` should keep LFS files as pointers with no fetch into `.git/lfs` (UNVERIFIED: git-lfs not installed where this was checked). Other custom filters still run, as they would on checkout.
- Absolute-path tests fail.
- In WORKTREE mode, a tracked file deleted in the working tree makes the first `tar` fail with `Cannot stat` and exit 2 (the recipe hides its stderr), but the copy is otherwise complete (observed 2026-11-19).
- `env -i` drops variables that some toolchains need (pyenv shims, `JAVA_HOME`). If the run fails for that, report "not run (toolchain needs env)"; don't loosen the environment.
- `tar --null -T -` on macOS bsdtar: UNVERIFIED.

## 8. Clean up

Once the report is written, `rm -rf "$T"`. Only that directory.

## 9. Stamp

Recipe run 2026-11-19 on the `goal-auditor-fix-claim` fixture in both modes. The #14 test gave before 1 / after 0, the vacuous #12 test 0 / 0. cksum of every `.git` file and tracked file unchanged, `git stash list` empty, one worktree.
