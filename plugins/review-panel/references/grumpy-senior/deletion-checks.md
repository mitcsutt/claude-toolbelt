# grumpy-senior deletion checks

Verified on 2026-11-19 (git 2.43.0, GNU tar 1.35, Node 24.14.1, Linux); re-check live before a finding depends on a volatile fact (versions, built-ins, doc quotes).

How to back a cut, how to refute it, how to size it, and the T2 probe. Evidence IDs resolve in `evidence.md` (this directory) or in `${CLAUDE_PLUGIN_ROOT}/references/shared/evidence.md`. Reinvention cuts also follow `reinvention.md`.

## 1. Evidence kinds (one per cut; GS2, GS4)

- **Caller count.** Grep for the symbol across the repo, excluding its definition and its own file; give the pattern and the count. Count call sites, imports and re-exports separately. For a plan: `grep -n '<name>'` over the plan and quote each consumer step.
- **Implementer count.** Production implementations, then test doubles, then how many test files import each double. A double imported by 0 tests is not a seam.
- **Read count.** For a config key, env var, flag or parameter, count reads in code, not mentions in docs or the defaults file. A parameter destructured or declared but never used in the body counts 0 (GS4: high confidence).
- **Goal trace.** Quote the goal sentence(s), quote the part, say which sentence it serves, or "none".
- **Duplicate.** Both `path:line` ranges and the one-line difference between them, if any.
- **Platform or runtime.** A row of `reinvention.md` (quote, page, since) plus the version probe output.
- **Probe (T2).** The two exit codes and the test command.

On a plan, counts are over the plan text and the repo it names.

## 2. Dynamic-reach sweep

Before calling anything unused (GS4), search for the name:

- as a string (`'name'`, `"name"`, template literals);
- in registries and decorators, dynamic `import()`, `require(variable)`, `getattr`, DI containers;
- in entry points: `package.json` `main`/`bin`/`exports`, `pyproject.toml` scripts and entry points, `plugin.json`, `hooks/hooks.json`, `.mcp.json`;
- in CI (`.github/workflows/`), deploy files (Dockerfile, compose, k8s), env files (names only, never values);
- in instruction files that tell an agent to run it (CLAUDE.md, skills), and in any other repo the brief names.

Say which places you searched. In code with registries or reflection, an "unused" claim is medium confidence at best.

## 3. External readers of config

A key read by CI, deploy, compose or another service is read. Search there before calling a key unread.

## 4. Checks not to make

Lens-specific only; the shared ones are in `${CLAUDE_PLUGIN_ROOT}/references/shared/review-method.md`.

| Don't report | Why | ID |
|---|---|---|
| An interface with one production implementation and a test double that tests actually import. That's a test seam. (A double imported by no test doesn't count.) | R10 §3 | doctrine, no evidence |
| An extension point the platform requires: a plugin manifest entry, framework lifecycle method, CLI subcommand registry, callback signature. It's one implementation of someone else's interface. | R10 §3 | doctrine, no evidence |
| A symbol as dead from a grep alone, before the dynamic-reach sweep. | R10 F19 | GS4 |
| Layered validation at a trust boundary (parse, DB constraint, authorisation) or a deliberate copy across one (client and server). | R10 §3, F13 | GS6 |
| "Reinvented stdlib" when the feature postdates the runtime floor, or the repo states a no-dependency rule. | R10 §3 | doctrine, no evidence |
| "Use the built-in" when it fails a hard constraint of the goal. | R15 F18 | GS8 |
| A cut of tests, docs or rollback that goal-auditor would require. Cut scope, not verification. | R10 §3 | doctrine, no evidence |
| "AI code is bloated" or a smell name as the argument. Argue from this artifact's counts; cost abstractions in reader time and moving parts, not bugs. | R10 F17, F18 | GS2, GS3 |
| A wrong technical claim that changes no cut (fact-checker's), or "add a library instead" (outsider's). | R16 F1 | GS9 |
| Removing something already published (a public subpath, CLI command, config key users set) as a "safe" cut. Mark it breaking and hand the removal question to contract-keeper. | R16 P2 report, "Cheap to defer" table | GS9 |

## 5. Sizing

Always label sizes "est.".

- **Code.** Deleted file: its line count (`wc -l`, or the last line number Read shows). Inlined part: lines removed minus lines added. Also files, config keys and flags.
- **Plans.** Steps, phases, PRs, pages, commands or services dropped, counted from the plan.
- **Add back later.** *Additive* if restoring it later is a minor change (a new optional key, file or command). *Breaking* if removing it after release breaks a consumer: then the cut is "cut now or keep forever".

## 6. T2 deletion probe (GS11)

Only for a BLOCK or SHOULD cut that a grep can't settle: dynamic reach suspected, or the part runs in tests. At most 3 cuts per review. Never for a part whose count is 0 with no dynamic reach found, a part T1 showed is used, a plan, or a COULD.

```bash
export GIT_OPTIONAL_LOCKS=0 GIT_LFS_SKIP_SMUDGE=1
T=$(mktemp -d "${TMPDIR:-/tmp}/gs-cut.XXXX")
git archive HEAD | tar -x -C "$T"
# uncommitted work instead: git ls-files -co --exclude-standard -z | tar --null -T - -cf - | tar -x -C "$T"
(cd "$T" && <test cmd>); echo "before=$?"     # want 0
rm -r "${T:?}/<path of the cut>"             # or edit the copy; never the real tree
(cd "$T" && <test cmd>); echo "after=$?"      # 0 means the tests don't need it
rm -rf "${T:?}"
```

- The test command is the repo's own (`npm test`, `node --test`, `pytest -p no:cacheprovider` with `PYTHONDONTWRITEBYTECODE=1`). Run it only if it needs nothing installed beyond what is already in the copy; `node_modules` is not copied, so a suite that needs deps goes to Coverage. A `git archive` copy misses `export-ignore` files, submodule contents and LFS objects (GS11, GA10). The `ls-files` copy includes export-ignored files and checked-out submodule directories (with a `.git` file that points outside the copy), and copies LFS files as their checked-out content. Neither copy has `node_modules` or other ignored files. With the `ls-files` copy, a tracked file deleted from the worktree makes tar fail without failing the pipeline, so check its stderr.
- `before` ≠ 0 means the probe proves nothing; say so.
- Both 0 shows the tests don't need the part, not that the goal doesn't. Say which goal behaviour the suite covers.
