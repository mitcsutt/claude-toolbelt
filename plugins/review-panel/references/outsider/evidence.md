# outsider evidence

Verified on 2026-11-20 (Claude Code 2.1.329 docs); re-check live before a finding depends on a volatile fact.

Cite these IDs in the cold take, prior-art rows and findings. Method evidence lives in `${CLAUDE_PLUGIN_ROOT}/references/shared/evidence.md`; how to apply these rows is in `method.md`, and the platform checklist in `built-ins.md` (both in this directory). `R15 F<n>` and `R16 §<n>` point to the research archive behind each row. Strength follows the archive's labels.

## Evidence

| ID | Finding | Source | Strength |
|---|---|---|---|
| OU1 | Frontier models stay susceptible to *plausible* anchors even at >95% control accuracy; plausible anchors shift answers more than irrelevant ones. A design doc is a plausible anchor. | R15 F1 (AnchorBench, arXiv 2608.14320) | high |
| OU2 | In LLM judges, anchors shift ratings (\|d\| up to 0.71) and block 48% of error corrections on categorical data. Neither chain-of-thought nor a "disregard this" warning reduces the effect. That withholding the anchor is what helps is an inference, UNVERIFIED. | R15 F2 (arXiv 2608.25869) | high |
| OU3 | Simple mitigations (CoT, reflection, "ignore the hint") don't remove anchoring; collecting hints from comprehensive angles does, and reasoning helps somewhat. | R15 F3 (arXiv 2412.06593; 2505.15392) | medium (older models, numeric tasks) |
| OU4 | Framing (33.2%) and anchoring (23.5%) flip LLM technical verdicts; semantic judgements are more susceptible than pattern-matched ones. | R15 F4 (arXiv 2606.30587) | high |
| OU5 | Sycophancy is driven most by how confidently an opinion is stated, who it is attributed to, and whether it is presented as shared. Larger models resist wrong opinions better but don't recover from their own errors. | R15 F5 (arXiv 2610.18159; Sharma et al. 2310.13548; SycEval 2502.08177) | high |
| OU6 | Judging is only weakly correlated with generation ability, mostly from sensitivity to the response judged; using the model's own answer as a reference strengthens it. Caveat: measured as correlation, not defect recall on plans. | R15 F6 (arXiv 2509.19880) | medium |
| OU7 | Listing arguments against an anchor reduces anchoring, even in experts. That forewarning doesn't is UNVERIFIED (not in the abstract), as are effect sizes. | R15 F7 (Mussweiler, Strack & Pfeiffer, PSPB 26(9) 2000) | high for direction (human study) |
| OU8 | The premortem's "30%" counts reasons generated, not decision quality. Use it to generate candidates, not as an accuracy step. Primary 1989 paper UNVERIFIED. | R15 F8 (Klein, HBR 2007; secondary critique) | medium |
| OU9 | Seeing others' answers first reduces diversity without improving accuracy, and raises confidence. | R15 F10 (Lorenz et al., PNAS 2011) | high (human study) |
| OU10 | LLMs hallucinate packages: on average at least 5.2% of the packages commercial models recommend are hallucinated, and 21.7% for open-source ones. | R15 F11 (Spracklen et al., arXiv 2406.10279) | high |
| OU11 | GitHub stars are fakeable and lose value as a decision signal. | R15 F12 (He et al., arXiv 2412.13459) | high |
| OU12 | 16% of 1,932 popular GitHub projects were abandoned; 41% of those survived through new core developers. | R15 F13 (Avelino et al., arXiv 1906.08058) | high |
| OU13 | 81.5% of studied systems keep outdated dependencies; 69% of interviewees were unaware of their vulnerable ones. | R15 F14 (Kula et al., arXiv 1709.04621) | high |
| OU14 | Single packages can affect large parts of npm; unmaintained packages threaten large code bases. | R15 F15 (Zimmermann et al., arXiv 1902.09217) | high |
| OU15 | Abandonment imposes real cost on "free" dependencies; mitigations are monitoring, abstraction layers, community involvement. | R15 F16 (Miller, Kästner & Vasilescu, ESEC/FSE 2023) | medium (33 interviews) |
| OU16 | Claude Code keeps shipping relevant built-ins (four in one month below; the rate is not measured). `/skill-doctor` (2.1.261), `claude plugin eval` (2.1.269), native AGENTS.md (2.1.277) and `/doctor prompt-audit` (2.1.283) all shipped Sept 2026, after a June-2026 knowledge cutoff. | R15 F17 (npm registry `time` field; CHANGELOG.md) | high |
| OU17 | "Use the built-in" can be wrong: a Bash permission rule "isn't a security boundary around the program"; `/loop` runs "while the session stays open"; `/schedule` runs on cloud infra. | R15 F18 (`permissions.md:261`, `commands.md:108`, `glossary.md:110`) | high |
| OU18 | Owner's one real panel (n=1): outsider raised 9 findings (0 BLOCK / 4 SHOULD / 5 COULD), 3 dropped as nit/unsupported; its medium-confidence, outsider-only Pagefind suggestion became kiln-docs' default search; its cold take ("theme + preset over fumadocs-ui") was overruled with no evidence either way. Cost: 42.5k output, 134k peak context, 23 calls, 15 WebFetch + 3 WebSearch, 8.5 min. | R16 §2 scorecard, §5 outsider table | measured, n=1 |
| OU19 | Reference-class view: ask for base rates of comparable tools (abandonment, OU12), not inside-view effort estimates. Flyvbjerg's overrun figures are UNVERIFIED; don't quote them. | R15 F9 (secondary only) | low |

Out-of-scope advice as the main noise source is shared E32; self-preference is E31; snippet staleness is E35; post-cutoff API knowledge is E36. They are cited, not copied.

Method evidence E28–E32 and E34 onward: ${CLAUDE_PLUGIN_ROOT}/references/shared/evidence.md.
