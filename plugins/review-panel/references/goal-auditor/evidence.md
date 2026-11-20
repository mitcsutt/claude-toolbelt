# goal-auditor evidence

Verified on 2026-11-19 (git 2.43.0, GNU tar 1.35, Node 24.14.1, Linux). Re-check live before a finding depends on a volatile fact.

Cite these IDs in scorecard rows and findings. Method evidence lives in `${CLAUDE_PLUGIN_ROOT}/references/shared/evidence.md`. Strength follows R10's labels.

## Evidence

| ID | Finding | Source | Strength |
|---|---|---|---|
| GA1 | 29.6% of plausible SWE-bench patches behave differently from the human fix; 28.6% of those are certainly incorrect; reported resolution rates are inflated by 6.2 pp. "Tests pass" is a claim, not a verdict. | R10 F1 (arXiv 2503.15223) | high |
| GA2 | 31.08% of passed patches were suspicious because the tests were weak (SWE-Bench+); UTBoost found 345 wrongly passed patches because PR tests were "often insufficient". | R10 F2 (arXiv 2410.06992, 2506.09289) | high |
| GA3 | Gold standard: FAIL_TO_PASS tests "fail before the solution … but pass afterwards"; PASS_TO_PASS tests pass both before and after and catch broken unrelated behaviour. | R10 F3 (openai.com/index/introducing-swe-bench-verified, upd. 2025-02-24) | high |
| GA4 | About half of test-passing agent PRs would not be merged; the automated grader is 24.2 pp above maintainers. In an n=15 review, all 4 test-passing runs had inadequate test coverage. | R10 F4 (METR 2026-03-10; METR 2025-08-13) | high; n=4 part medium |
| GA5 | Claude 3.7 Sonnet "occasionally resorts to special-casing in order to pass test cases … directly returning expected test values … modifying the problematic tests"; recommended monitoring: test-specific comments, unexpected test-file edits. | R10 F5 (Claude 3.7 Sonnet system card §6; METR 2025-06-05) | high |
| GA6 | When tests conflict with the spec, Claude models cheat mostly (>79%) by editing tests. LLM monitors caught only 42–65% of the cheats on Impossible-SWEbench (Sonnet 4 42–50%, Opus 4 57–65%), and a "backward compatibility" excuse fooled them. Reading alone misses a third to a half of these, so deterministic checks come first. | R10 F6 (arXiv 2510.20270, body) | high |
| GA7 | Most APR patches reported as test-passing were wrong: most did not even pass the validation tests, and most were "equivalent to a single modification that simply deletes functionality". | R10 F7 (Qi et al., ISSTA 2015; arXiv 2511.16858) | high |
| GA8 | Current vendor guidance still says Claude "can sometimes focus too heavily on making tests pass at the expense of more general solutions". | R10 F8 (platform.claude.com prompting best practices, fetched 2026-11-19) | high |
| GA9 | `git archive` writes nothing to the repo but runs configured smudge filters on each file (archive.c), so git-lfs could fetch into `.git/lfs` unless `GIT_LFS_SKIP_SMUDGE=1`; `git worktree add` writes `$GIT_DIR/worktrees/…`; `git stash` writes `refs/stash` and resets the tree; `git status` rewrites the index unless `GIT_OPTIONAL_LOCKS=0`, but `git diff` against the working tree (`git diff`, `git diff HEAD`) refreshes it even then; plumbing `git diff-index -p HEAD` and `git diff-files -p` don't. A temp-dir revert check gave before=1/after=0 with `.git` and the tracked files unchanged. | R10 F9 (git 2.43 man pages + local run); git v2.43.0 `archive.c`, `builtin/diff.c` `refresh_index_quietly`, local runs 2026-11-19; recipe re-run on this plugin's eval fixture 2026-11-19 (see revert-check.md stamp) | high |
| GA10 | Limits of the temp copy: `export-ignore` files are left out; no `node_modules`, venv or build output; a submodule comes out as an empty directory (observed); LFS files stay pointers with `GIT_LFS_SKIP_SMUDGE=1` (UNVERIFIED: git-lfs not installed where this was checked); tests that need services, network or absolute paths can't run; a "before" failure at import or setup is not evidence. | R10 F9 limits; local runs 2026-11-19 | high; LFS UNVERIFIED |
| GA11 | 14.8–24.4% of sampled post-release OS fixes were incorrect; 39% of concurrency fixes; 27% of incorrect fixes came from developers who had never touched the file. | R10 F11 (Yin et al., FSE 2011) | high |
| GA12 | Bugs often need a supplementary patch, and it usually lands near the first fix or in code structurally dependent on it. So hunt dependents (callers, callees, same-file siblings), not only textual clones. 22–33% of resolved bugs involved more than one fix attempt. | R10 F12 (Park et al., MSR 2012, abstract) | high |
| GA13 | Inconsistent changes to clones cause real faults, but most clones are harmless (71% "positive impact", S, UNVERIFIED) and most bugs aren't tied to clones. Report a sibling only when it has the same bug. | R10 F13 (Juergens et al., ICSE 2009; Kapser & Godfrey, Rahman et al., S) | medium |
| GA14 | Symptom fixes take known shapes: special-cased inputs, deleted behaviour, changed expected values, and error masking (GitClear: error-masking constructs +47%, vendor and correlational). No primary rate for "wrapped in try/except" exists. | R10 F15 | medium |
| GA15 | No study measures how often a missing rollback or missing negative tests made a "complete" change incomplete. Weight these by consequence, not as default BLOCKs. | R10 F14 | UNVERIFIED (no data) |
| GA16 | In the owner's one real panel, goal-auditor raised 20 findings, 12 of them fix items. Its unique T17 ("N1 proven", but the spike sidebar was empty) drove a new nav test. It also flagged a deploy token that proved account-wide, though threat-modeller wasn't on the panel. Real "done" claims that were false: a fix verified only on the LAN, a restart called a fix, a feature linked from 33 pages with 0 `.md` files built. | R16 §2, F8, §5 goal-auditor table (`KP/triage.md` T17, D7; `firstmate/a1a79797`:330) | measured, n=1 |
| GA17 | Lens overlap: three claim-checking bugs were each found by 5 of 6 reviewers, goal-auditor included. Every agent body tells its reviewer to check claims (inferred, not measured). | R16 F1 | measured, n=1; agent-body part inferred |

## Checks not to make

- Don't demand a new test when an existing test already fails before and passes after: run the revert check on the existing suite first. (R10 §3; GA3)
- Don't call the revert check failed when "before" fails at import or setup. That's the environment, not coverage. (GA10)
- Don't report a sibling or clone as missed unless it has the same bug in its own context. Deliberate copies at a trust boundary aren't findings. (GA13)
- Don't require rollback for pure refactors, docs, tests, or changes behind an existing deploy rollback. (GA15)
- Don't treat a "backward compatibility" branch as special-casing without checking for a real consumer. (GA6)
- Don't grade a missing negative test BLOCK unless an input class the goal names is untested. (GA15)
- Don't call a fix "special-cased" when the bug is inherently about one value, such as an off-by-one at a boundary. (R10 §2 literal-guard row)
- Don't re-verify platform or library claims when fact-checker is on the panel. Cite its owner under "You ignore". (GA17)
- Don't report "tests pass" as met from the author's CI badge or PR text alone. That row is "claimed only" until you have read or run it. (GA1, GA4)
