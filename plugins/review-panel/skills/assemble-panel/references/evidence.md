# assemble-panel evidence

Cite these IDs in SKILL.md and its references. Compiled 2026-11-19. Most 2026 papers are unreviewed preprints that test one model or one harness, so treat effect sizes as direction rather than as constants. The owner's panel figures are one run (n=1); update them after the next real panel.

Shared method evidence (E37: agreement ranks findings, never gates them; E41: the owner's panel numbers) is in `${CLAUDE_PLUGIN_ROOT}/references/shared/evidence.md`, cited here rather than restated.

| ID | Finding | Source | Strength |
|---|---|---|---|
| AP1 | Multi-agent review adds about +2.8% at 3.68× the cost. Single-agent setups perform "comparably to—and sometimes outperform" multi-agent ones. | R9 F28 (CRBugBench arXiv 2610.13668; arXiv 2611.23283) | high |
| AP2 | Two reviewers find about as many defects as four (classic inspection). In an LLM study, the marginal gain falls with each added agent (+14.9, +13.5, +11.2 pp for agents 2–4). | R9 F30 (Rigby & Bird FSE 2013; arXiv 2511.16708, n=29) | medium |
| AP3 | Specialised lenses pay only when they find different defects. Where two perspectives overlapped, perspective-based reading lost its advantage. | R9 F29 (Ericsson arXiv 2609.15877, no ablation; Carver EMSE 2006) | medium |
| AP4 | Effort should scale with change size. On PRs under 50 lines, 31% get findings (0.5 issues on average); on large PRs, 84% (7.5 issues). "Large or complex changes get more agents". Agents are less effective on large, heterogeneous PRs. | R9 F12(a), F31 (claude.com/resources/articles/code-review 2026-03-09; CRBugBench) | medium (vendor) |
| AP5 | Debate rarely beats voting at equal compute. It wins only when it is gated by tools and the agents err differently. | R6 F28–F29 (arXiv 2311.17371, 2502.08788, 2508.17536, 2610.33002) | high |
| AP6 | Anthropic found one judge call more consistent than several. | R6 F30 (Anthropic multi-agent post, 2025-06-13) | medium |
| AP7 | Multi-agent systems use about 15× the tokens of chat, and token use explains 80% of the performance variance. | R6 F34 (same post) | medium |
| AP8 | Owner's panel P2: reviewer wall time 8.5–21.6 min, and the slowest reviewer gated the filter. Wave 22 min, filter 12.3 min, board about 10 min. Per agent, about 0.45M–17.1M tokens in total including cache reads (recounted from the P2 transcripts 2026-11-19; R16 §2 gives 1.6M–16.9M, whose low end is outsider's, not grumpy-senior's), against a 134k–307k peak context. About 44M cache reads across the panel. | R16 §2 totals, F5 (n=1) | measured, n=1 |
| AP9 | The orchestrator relayed each reviewer as it landed and Read all 6 full reports (about 240 KB) into the main context, breaking Steps 5–6 as written. | R16 M2 (`19ea81e4`:551-743) | measured, n=1 |
| AP10 | A host PreToolUse hook refused all 6 dispatches. The skill had no fallback, and the orchestrator improvised a route at the cost of a worker boot. | R16 P1, M1 (`firstmate/106f206e`:340-359) | measured, n=1 |
| AP11 | Overlap: three triage items were found by 5 of 6 reviewers, all of them claim-checking bugs (fact-checker's lens), re-derived by the other lenses. | R16 F1, M4 (`KP/triage.md`) | measured, n=1 |
| AP12 | Routing skipped threat-modeller as a "static site", but the plans added a CI deploy token. goal-auditor then found its account-wide scope, which later shipped. | R16 F8, M3 | inferred, n=1 |
| AP13 | The orchestrator told the user the filter "took about 40 minutes"; the measured time was 12.3. | R16 F5, M6 | measured, n=1 |
| AP14 | Routing for agent instructions was identical across 3 eval runs, and each run followed the no-overlap rule. | R16 F9 (R16 §1, its three eval runs) | measured, n=3 |
| AP15 | An artifact-only grumpy-senior was the cheapest reviewer (1/70th of fact-checker's cache reads) at similar output tokens per Fix item. | R16 F10 | measured, n=1 |
| AP16 | Opus 5.5 list price: $4 in, $0.20 cache read, $20 out per MTok. Sonnet 5.5: $2, $0.10, $10. Fetched 2026-11-19. | R13 F1, F3 (platform.claude.com/docs/en/about-claude/pricing) | high, volatile |
| AP17 | Claude Code's dollar figures are list-price estimates. On a subscription the cost is a share of plan limits, not a bill. | R13 F6 (code.claude.com/docs/en/costs) | high |
| AP18 | On this machine cache traffic is about 80% of agent spend and output about 21%. Pricing output alone undercounts. | R13 F9 (local, 6,150 responses) | high for this host |
| AP19 | A fixture's hooks can't stage a refused dispatch under `claude plugin eval`: "no `.claude/` directory … loads from above the workspace or inside it, even one a `scaffold_script` wrote", and user hooks are absent too. So the assemble-panel cases withhold `Agent` and grade the printed roster instead. R16's three routing eval runs, where the rules-lawyer fixture's Stop hook re-fired about 9 times per run alongside a user-level hook, can't have used that harness's isolation. | `claude-code-docs-2026-11-19/plugin-evals.md:496` (live page matched, 2026-11-19); R16 M8 (transcripts recounted 2026-11-19) | high (docs) |
| AP20 | PreToolUse matches the `Agent` tool, and a hook printing `permissionDecision: "deny"` blocks the call. | `claude-code-docs-2026-11-19/hooks.md:1785`, `:205-211` | high |
| AP21 | `tool_used` counts calls whose JSON-encoded input matches `input_match`, between `min` and `max`. `min: 0, max: 0` asserts "never". `--ablation none` runs one arm and excludes nothing. | `claude-code-docs-2026-11-19/plugin-evals.md:591`, `:231`, `:245` | high |
| AP22 | The eval trace doesn't show a subagent's own tool calls, so a `Read` in the trace is a main-context read. | `plugins/review-panel/tests/all.sh:80-84` (owner observation; inferred for `Read`) | medium |

Dollar arithmetic behind Step 0 (AP8, AP16, AP18): 587k output × $20/M = $11.74, plus 44M cache reads × $0.20/M = $8.80, so about $20.5 is a floor before cache writes, which are about a third of this machine's spend; hence "about $20–35". Per reviewer, recomputed from the P2 transcripts at list price with the 5-minute cache writes they used: about $1.8–6.6 (outsider lowest, fact-checker highest; panel total about $28), rounded to $2–7. All estimated.
