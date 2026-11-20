# fact-checker evidence

Method evidence E28–E32 and E34 onward: ${CLAUDE_PLUGIN_ROOT}/references/shared/evidence.md.

Verified on 2026-11-20 (Claude Code 2.1.329, Ubuntu 24.04.4). Re-check live before a finding depends on a volatile fact.

`CCD/<page>:<line>` cites the Claude Code docs (`https://code.claude.com/docs/en/<page>`) as snapshotted on 2026-11-19; live line numbers drift, so search for the quoted text.

| ID | Finding | Source | Strength |
|---|---|---|---|
| FC1 | Fact-check scores depend on how claims were decomposed. Splitting into atoms adds four error types (omitted context, ambiguity, over-decomposition, altered meaning) and hurts strong verifiers (MiniCheck 80.01 → 71.11 BAcc). Keep "molecular" claims that carry their tool, version and scope. | R8 F1–F3 (arXiv 2403.11903, 2411.02400, 2406.20079, 2412.13175) | high |
| FC2 | Current extractors deliberately drop unverifiable or ambiguous text. Logging such text as `not checkable` keeps it visible instead of losing it silently. | R8 F4 (VeriScore 2406.19276; Claimify 2502.10855; SAFE 2403.18802 body) | high |
| FC3 | Implicit premises are missed often. Listing "assumptions that must hold" raised half-truth recall from 37.9 to 66.8. No study measuring recall of implicit claims in technical designs was found. | R8 F5 (2508.00489 body; CREPE 2211.17257) | medium |
| FC4 | Obvious sub-claims inflate decompose-then-verify scores. Trivia padding hides the claims that matter. | R8 F6 (Core, 2407.03572) | high |
| FC5 | Models adopt wrong retrieved content over a correct prior more than 60% of the time, and show confirmation bias toward evidence that matches their memory. | R8 F9 (ClashEval 2404.10198; 2305.13300) | high |
| FC6 | Frontier models still hallucinate packages in 3.6–5.0% of package mentions (2026), more in Python than in JS. Web search raised persistence from 11.2% to 17.3% (publication date UNVERIFIED). | R8 F10 (2406.10279; 2605.17062 v4; ACM TOSEM 10.1145/3830237) | high |
| FC7 | A model's effective knowledge cutoff varies by topic and differs from the reported one. | R8 F11 (Dated Data, 2403.12958) | medium |
| FC8 | The installed binary can disagree with upstream docs for the same version. Ubuntu 24.04's `cp (GNU coreutils) 9.4` exits 0 on `cp -n` even though upstream 9.2–9.4 exit nonzero. The distro patched it: `/usr/share/doc/coreutils/changelog.Debian.gz:94` "revert cp -n behavior to debian 12 & prior (Closes: #1058752)", package `9.4-3ubuntu6.3`. | R8 F13 (local run; coreutils NEWS); changelog line verified 2026-11-19 by plan author | high |
| FC9 | Moving labels change during a review. actions/runner-images#14748 says the `ubuntu-latest` migration started 2026-10-19 and was due to finish 2026-11-19. | R8 F14 | high |
| FC10 | Worked example: the rules-lawyer reference pass gave 120 rows, 108 CONFIRMED / 6 WRONG / 6 UNVERIFIED. Every WRONG was an absolute or an incomplete list. The UNVERIFIED rows were unfetched third-party docs, docs that say nothing, and local anecdotes. (R8 F18 says "114 claims"; R7:140's totals sum to 120.) | R7:140, rows 22, 66, 92, 101, 109; R8 F18 | measured, n=1 |
| FC11 | A fact-checker correction taken from docs alone ("background subagents … keep only 20 built-ins", no `Agent`) was disproved by a live test, after it had already caused a false downstream finding. | R16 F7, M7 (`499fb057`:1553) | measured, n=1 |
| FC12 | Confidence-gated search skipping saves 7.6× on LLM cost, but only for open-domain facts. No per-claim cost data exists for technical claims; the working estimate is 20–60k tokens per 10 claims. | R8 F19 (FIRE 2411.00784; SAFE body; R6) | medium |
| FC13 | Docs drift from code: 28.9% of top-1000 projects contain an outdated reference, which has about a 55% chance of surviving another month. In one README study, 81 of 244 bugs were implementation–doc drift. | R8 F21 (2212.01479; READU 2607.15780) | high |
| FC14 | Diffing the flags a README claims against `--help` output produced 9 phantom-flag reports, 7 of them real. LLM verdicts on prose needed correction about a quarter of the time. | R8 F23 (dev.to, 2026-08-12) | low (practitioner) |
| FC15 | A plugin subagent with an explicit `tools:` list gets an MCP tool only if it names that tool exactly, and the name depends on the install (`mcp__context7__*` vs `mcp__plugin_find-docs_context7__*`). Plugin agents can't declare `mcpServers`. Whether `ToolSearch` is needed, and whether an unmatched entry warns, are UNVERIFIED. | R8 F24 (CCD/sub-agents.md:234, 443, 468, 490–494; CCD/mcp.md:732–738; CCD/errors.md:4872–4876) | high (documented parts) |
| FC16 | Real-run cost. P2 produced a 75-row ledger (58 C / 12 W / 5 U), 17 findings, 100k output tokens, a 252k peak context, 112 calls (103 Bash, 7 WebFetch), 16.8M cache reads and took 18.9 min. P3 produced 120 rows, and all 11 of its findings were applied. | R16 §2 table, F10 | measured, n=2 |
| FC17 | Three claim-checking bugs were each found by 5 of the 6 reviewers. Other lenses re-derive fact-checker's work. | R16 F1 | measured, n=1 |
