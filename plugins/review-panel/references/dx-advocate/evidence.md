# dx-advocate evidence

Verified on 2026-11-19. Re-check live before a finding depends on a volatile fact (marked volatile).

Cite these IDs in resolution rows, findings and the rule ledger. Method evidence (E28–E32, E34 onward) lives in `${CLAUDE_PLUGIN_ROOT}/references/shared/evidence.md`. Effect sizes from 2026 preprints are direction, not constants.

## Lens evidence

| ID | Finding | Source | Strength |
|---|---|---|---|
| DX1 | Setting up the local workspace is the barrier newcomers report most, and technical barriers are "the main reason" newcomers fail to deliver (local-setup barriers in 32 of 46 diaries). | R14 F1 (Steinmacher et al., ICSE 2016; IEEE Software 2018 "Let me in") | high |
| DX2 | Only 28% of CONTRIBUTING files say how to build the local workspace; 84% miss at least 2 of 6 newcomer barriers. | R14 F3 (Fronchetti et al., FSE 2023) | high |
| DX3 | 93% of open-source respondents have run into incomplete or outdated documentation. | R14 F4 (GitHub Open Source Survey 2017, n≈5,500) | high (survey) |
| DX4 | Inappropriate installation instructions were the correctness issue practitioners met most often. Share rating each issue important: missing docs for a new feature 69%, missing docs for a new release 53%, code–doc inconsistency 59%, outdated install steps 54%; spelling and grammar 23%, format 33%, outdated screenshots 29%. | R14 F5 (Aghajani et al., ICSE 2020, n=146) | high |
| DX5 | The severest doc problems are ambiguity, incompleteness and incorrectness, so content outranks presentation. | R14 F6 (Uddin & Robillard, IEEE Software 2015, n=323) | medium (one company) |
| DX6 | 28.9% of popular GitHub projects carry at least one outdated code reference in their docs, on average outdated for 4.7 years. Of the 8 projects that replied to reports, 4 called them false positives, so every grep hit needs reading. | R14 F7 (DOCER, arXiv 2212.01479, 2307.04291) | high |
| DX7 | README bugs are inconsistencies with a checkable fact. A checker pipeline found 244 true positives at 75% precision; a single LLM call found 29 (62%). The top false-positive causes were pre-existing issues (32), misread implementation semantics (18) and intentionally non-exhaustive docs (14). | R14 F9 (READU, arXiv 2607.15780) | high |
| DX8 | Execution or symbolic cross-checks lift doc-inconsistency precision (MPDetector 92.8%; METAMON 0.72). | R14 F11 (arXiv 2411.11410, 2502.02794, 2604.19400) | high |
| DX9 | A single evaluator finds about 35% of usability problems, and the problem lists skew minor, so severity must come from consequence. | R14 F13 (Nielsen 1994, 1995; GUI studies) | high (analogy for docs) |
| DX10 | Commands lead agent context files: build and run is the top category in 253 CLAUDE.md files (77.1%), and testing is the top one in 2,303 context files (75.9%), so stale commands hit agents first. | R14 F16 (arXiv 2509.14744, 2511.12884) | medium-high |
| DX11 | Agents rarely set up a repo from its docs alone (EnvBench best 6.69% Python, 29.47% JVM). Repo2Run reaches 86% only by iterating on execution feedback. | R14 F18 (arXiv 2503.14443, 2502.13681) | high |
| DX12 | LLM-simulated users find different problems from human users: recall 0.29–0.35 vs 0.44–0.65, with negative κ. The LLM flagged jargon that humans never did. | R14 F19 (ACM DOI 10.1145/3801978, 2026) | medium (4 UIs, 9 humans) |
| DX13 | `dontAsk` permission mode denies `AskUserQuestion` even when it is allowed, so "ask the maintainer" is a dead end for a run in that mode. | R14 F21 (Claude Code docs mirror `permissions.md:80`, 2026-11-19) | high, **volatile** |
| DX14 | Error text is read and is as hard to read as code. Short, readable messages with a resolution hint shortened debugging in an RCT (n>700). Over-specific fix hints can propose a fix the user didn't intend. | R14 F22–F24 (Barik ICSE 2017; Denny et al. ITiCSE 2020; Becker et al. 2019) | high for novices |
| DX15 | GNU Make: a target listed in `.PHONY` with no rule prints "Nothing to be done for '<t>'" and exits 0, so a documented check passes while running nothing. | R14 F25; re-run here 2026-11-19 on GNU Make 4.3 (`exit=0`) | high |
| DX16 | `mapfile` arrived in bash 4.0 (bash NEWS). macOS `/bin/bash` is 3.2. | R14 F25 (tiswww.case.edu/php/chet/bash/NEWS:1432); Apple's published bash source (github.com/apple-oss-distributions/bash, 3.2 patchlevel 57, no `mapfile`), fact-checked 2026-11-20; repo `CLAUDE.md` gotcha | high |
| DX17 | The owner's commonest failure class is stale facts in docs: counts ("six plugins" when there were 8), pointers to missing files, retired services, old IPs. The repo's own fix was to delete the count so it can't go stale. Human docs only; the agent-instruction side is E33. | R5 A.2 rank 1, ep.12–15, ep.23 (local transcripts); R16 §5 dx-advocate table | measured, small n |
| DX18 | The owner's one real panel (n=1): dx-advocate raised 31 findings (1/19/11); 14 became fix items and 19 were cross-confirmed (3 solo). Its BLOCK was regraded to SHOULD, and it wrote its own severity key. Cost: out 85.5k, peak context 227k, 39 calls, 14.5 min. Its stale-docs list was the output most used downstream. Three claim-checking bugs were found by 5 of the 6 reviewers. | R16 §2, F1, F2 | measured, n=1 |
| DX19 | Sampling-based checking multiplies compute 5–10×. | R6 F20 (arXiv 2406.15927) | high |

## Checks not to make

- Tone, voice, spelling, format (DX4, DX5). Report wording only when it causes a wrong action.
- "Add examples" / "document Z" with no named user who fails a named step (E42, DX7).
- Jargon that is defined nearby or is a standard term for the stack. LLM evaluators flag jargon humans don't (DX12).
- Docs for internals no user touches (doctrine; lens scope).
- Pre-existing doc bugs as findings against this change. List them under "Noticed, not caused" (DX7: the top false-positive cause).
- Outdated screenshots, licence years, badges (DX4).
- Restructuring advice in Diátaxis terms (doctrine, R14 F15; no evidence).
- Social barriers: response time, mentoring, tone (R14 F2; no doc check settles them).
- "The error should suggest the fix" where the cause isn't knowable (DX14). Ask for what failed, where, and how to get detail.
- Re-finding with prose or followers what a doc check settled (DX19).
- Literal-reading or ambiguity findings on agent-loaded text (rules-lawyer's; SKILL.md no-overlap rule).
