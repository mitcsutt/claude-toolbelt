# goal-auditor T1 checks

Verified on 2026-11-19 (git 2.43.0, GNU tar 1.35, Node 24.14.1, Linux). Re-check live before a finding depends on a volatile fact.

Run these at T1, in order. Evidence IDs resolve in `evidence.md` (this directory). Comparing two commits only reads. Anything that diffs against the working tree can refresh `.git/index` even with `GIT_OPTIONAL_LOCKS=0` (GA9), so for the working tree use the plumbing forms given below.

## 1. Setup

`export GIT_OPTIONAL_LOCKS=0`.

- BASE and FIX come from the brief. Otherwise use BASE=`git merge-base HEAD origin/main` (or `main`) and FIX=`HEAD`.
- For uncommitted work, FIX is the working tree: `git diff-index -p HEAD` (not `git diff HEAD`, which can rewrite the index) plus `git ls-files -o --exclude-standard`. Where a command below says `git diff "$BASE" "$FIX"`, use `git diff-index -p "$BASE"` instead.
- Changed files: `git diff --name-status "$BASE" "$FIX"`.

## 2. Test-diff audit (GA5, GA6)

`git diff "$BASE" "$FIX" -- test/ tests/ spec/ '*_test.*' '*.test.*' '*_spec.*'`. Flag:

- an expected value that changed while the call stayed the same;
- deleted tests or assertions;
- new `skip`/`xit`/`only`/`@pytest.mark.skip`/`t.Skip`;
- a loosened matcher (`toEqual` → `toBeTruthy`, exact → contains).

Each needs a cited reason from the goal or the issue. If there is none, it's a finding. A "backward compatibility" reason counts only with a real consumer you can name (GA6).

## 3. New test vs pre-fix code (GA1–GA3)

For each new test, take its input and expected value. Read the pre-fix function with `git show "$BASE":<path>` and work out whether the old code already returns that value. If it does, the test is vacuous; quote both lines. Also check that the test uses the reported input: a name that quotes the issue while the body uses a different literal is a red flag.

## 4. Literal-guard grep (GA5, GA14)

Take the literals in the issue and the tests (strings, numbers, IDs). Run `git diff -U0 "$BASE" "$FIX" -- <src> | grep -nF -- '<literal>'` over the `+` lines. Also look for regexes or counts that fit only the reported input: anchored exact-width patterns such as `/^  /` or `/ $/`, and fixed indexes. For each, write 2–3 neighbour inputs (one more, one fewer, another position) and work out the result on paper. At T2, evaluate them with a one-liner in the temp "after" copy.

## 5. Error-masking grep (GA14)

`git diff -U0 "$BASE" "$FIX" | grep -nE '^\+.*(catch|except|rescue|recover\(|\|\| *true|\?\?|2>/dev/null|set \+e|or None)'`. It's a finding when it sits on the failing path the issue describes and the goal didn't ask for that fallback.

## 6. Behaviour removal (GA7)

`git diff --stat "$BASE" "$FIX"`, then the `^-` lines carrying `if|else|case|return|raise|throw|assert`, plus new early returns. Ask whether the fix works by no longer doing something.

## 7. Callers, two hops, and wrappers (GA12)

- `git grep -n -w '<changed symbol>' -- . ':!*test*'`, then the same for each enclosing function found (hop 2).
- Backward from the goal: for each surface the goal or issue names (a URL prefix, command, page, endpoint, event), find where it is produced (`git grep -nF "'/p/'"`, the route table, the CLI registry) and follow it forward. Does it reach the changed code? A surface served by its own implementation is a missed caller even though grep for the symbol never finds it.
- Dynamic dispatch (registries, string names, reflection, DI): note it under Coverage as a limit.

## 8. Siblings and duplicated logic (GA13)

Grep the removed line, trimmed: `git grep -nF -- '<old line>'`. Grep the key regex or literal: `git grep -nE '<pattern>'`. Search every language present (shell, SQL, templates). Report only a sibling that has the same bug in its own context. Deliberate copies at a trust boundary (client and server validation) are not findings.

## 9. Docs

dx-advocate owns doc quality; you own docs that contradict the claimed outcome. `git grep -n -i '<old behaviour phrase or symbol>' -- '*.md' docs/`.

## 10. Prerequisites and rollback (GA15, GA16)

What must exist outside the repo for the goal to hold (deploy target, domain, credential, migration run)? Who creates it, and is that step in the artifact? For a credential, record its scope. Rollback only when persisted state or a public contract changes.

## 11. By artifact type

- *Diff/PR*: sections 1–10.
- *Plan/spec*: no code to run. Check each "proven", "done" or "verified" claim against the artifact it cites (spike output, build dir, logs), e.g. `find out -name '*.md' | wc -l` against "linked from every page" (GA16). T2 only if the plan cites runnable tests.
- *Agent instructions*: rules-lawyer owns the literal reading and the claim ledger. You check that every scenario the goal names has an instruction or hook that handles it, and that the files claimed updated were.
- *Solo "fixed it" claim*: the reported symptom is row 1. If the report has a local, side-effect-free reproduction command, run it in the temp "after" copy (`revert-check.md` rules apply).
