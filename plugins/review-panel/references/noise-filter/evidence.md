# noise-filter evidence

Verified on 2026-11-19; re-check live before a decision depends on a volatile fact (platform behaviour, tool versions).

Cite these IDs in the triage's reasons and Coverage. Compiled 2026-11-19. Most 2026 papers are unreviewed preprints that test one model or one harness, so treat effect sizes as direction rather than as constants.

Method evidence E28–E32 and E34 onward: ${CLAUDE_PLUGIN_ROOT}/references/shared/evidence.md.

| ID | Finding | Source | Strength |
|---|---|---|---|
| NF1 | Suppressing named non-actionable categories moved usefulness the most (54% → 66%). In Greptile's audit 79% of comments were nits and 2% were incorrect, so most noise is nits, not false claims. | R9 F5 (arXiv 2405.13565); R9 F6 (greptile.com/blog/make-llms-shut-up) | medium-high |
| NF2 | A strict LLM validity filter got 85.1% valid-class precision at only 36.6% recall, so it silently dropped about 60% of valid comments. Adding the diff didn't help. | R9 F16 (arXiv 2502.02757, GPT-3.5 era) | high for that study; possibly stale |
| NF3 | With "never drop a true positive" as the stated goal, LLM triage removed 33.85% of false positives on real code (Phi-4; 62.5% on OWASP) with no genuine weakness missed; GPT-4o removed 24 of 65 and missed one genuine weakness. | R9 F17 (arXiv 2506.16899) | high |
| NF4 | Triage that follows the evidence step by step kept recall at 1.00 for BugLens (precision 0.10 → 0.72; the human pass got 0.83 recall). LLM4PFA removed 72–96% of false positives and missed 3 of 45 real bugs. | R9 F18 (arXiv 2504.11711, 2506.10322) | high, narrow |
| NF5 | A fact-check judge that doesn't open the repo had "minimal impact", while an actionability check gave 15% more correctly located comments. | R9 F3 (arXiv 2601.01129, RovoDev) | high |
| NF6 | Shipping filters keep dropped comments, with their reasons, in the output. | R9 F9 (nsbradford.com/blog/how-we-built-ellipsis) | medium, no numbers |
| NF7 | Same-pattern comments get clustered into one unit, and saying nothing is a valid result (29% of Copilot reviews). | R9 F10 (github.blog, 2026-03-05) | medium (vendor) |
| NF8 | A doc mismatch "leaves open whether the implementation or the prose is wrong", and the same comment can be a nit in one change and crucial in another. | R9 F11 (ai.engineer talk, S); HN 42451968 (S) | medium |
| NF9 | Pre-existing flaws get reported separately: PREDATES_CHANGE is "not a refutation of the flaw". The official code-review plugin's false-positive list includes "Pre-existing issues". | `claude-security/0.12.0/agents/scan-verifier.md:34`; R9 F12(b) (`code-review.md:80-87`) | high (source) |
| NF10 | "A finding whose reported line is wrong but whose described vulnerability is real at another line: say so". It sets no verdict; this plugin reads it as: re-locate a wrong line rather than refute the finding. | `scan-verifier.md:32`; R9 §3 #6 | high (source) |
| NF11 | claude-security's tally runs in code. Kept means 3 voters and ≥2 TRUE_POSITIVE; an unparseable vote counts as FALSE_POSITIVE; merging takes the max severity; voters cap severity at the upper median and can only lower it; HIGH confidence drops to MEDIUM unless the vote was unanimous; the dedupe key is file + line + CWE; on change scans a finding within 5 lines of a candidate with the same CWE class also joins it (never for CWE-798 or lines from the same pass); ranking is severity, then reports, then confidence. | `workflows/scan.js:130-152` (lines checked 2026-11-19); R9 F13 | high (source) |
| NF12 | Every surfaced item costs reviewer time: showing AI output raised time-in-review 5.5% (p=.029). | R9 F15 (arXiv 2507.13499) | high |
| NF13 | The P2 triage (n=1): 534 lines and 52 KB, longer by bytes than 5 of the 6 reports (by lines, longer than all 6). Its heading drifted to `## Fix before build` (`triage.md:17`). It added `## Revised roadmap` and `## Owner decisions at a glance` (`:506`, `:524`), used a drop reason "absorbed" that isn't in the list, made 4 of its 5 re-grades *upward* to BLOCK, and had 0 "checked: wrong" in 26 checks. The user had said "I'm a visual guy so hard to review", and the orchestrator then spent about 10 more minutes building a decision board. | R16 F2, F3, M5; `106f206e`:276; `triage.md` read 2026-11-19 | measured, n=1 |
| NF14 | Bugbot's launch pipeline randomised read order and voted out single-pass finds, but those were same-lens resamples. It later moved to an agentic design; the post calls that new agent "too cautious" at first and gave it more aggressive prompts, and does not say voting was why it switched. | R9 F1, §5 (cursor.com/blog/building-bugbot) | medium (vendor) |
| NF15 | `${CLAUDE_PLUGIN_ROOT}` is "substituted in skill, command, and agent content", so a command written in the agent body gets the installed path. | `rules-lawyer-2026-11-19/claude-code-docs-2026-11-19/plugins_components.md:608` (snapshot 2026-11-19) | high (docs); live-check it |
