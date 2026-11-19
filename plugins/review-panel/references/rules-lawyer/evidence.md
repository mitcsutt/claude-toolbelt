# Evidence base

Cite these IDs in findings and redline rows. Compiled 2026-11-19. Most 2026 papers are unreviewed preprints that test one model or one harness, so treat effect sizes as direction rather than as constants. A finding that rests on a version-sensitive platform fact needs the doc re-fetched (`https://code.claude.com/docs/en/<page>.md`), not this table.

## Context files and rules

| ID | Finding | Source | Strength |
|---|---|---|---|
| E1 | Context files don't raise average task success and add 20–23% cost. LLM-generated files: −0.5 to −2 pp. Developer-written files: +2.4 pp. Repo overviews don't help. Tools named in the file do get used (`uv` 1.6× per task vs <0.01×). | Gloaguen et al., arXiv 2602.11988 (ICLR 2026) | high, Python repos |
| E2 | Agents follow a stale instruction file over the code in 90.1% of choices. Claude Code named the conflict in 79/80 runs and still followed the file. One precedence sentence took code-following to 91.4%. | Xu & Wu, arXiv 2610.30571 (2026-10) | medium-high, synthetic projects |
| E3 | Compliance is all or nothing: 54% of rules are always followed and 37% never. Process rules (branching, commit format, "run tests before…") are followed least (median 0%). Only 44% of instructions are explicit, actionable and observable. | Voria et al., arXiv 2611.13709 (2026-11) | medium |
| E4 | Scope limits help: removing "do not refactor unrelated code" cost 20 pp. Generic advice ("follow existing style", "handle edge cases") gave nothing or hurt. Random rules matched curated ones. | Zhang et al., arXiv 2604.11088 (2026) | low-medium, one model |
| E5 | File size (25–500 lines), rule position, splitting and conflicts showed no detectable effect on a simple rule. Compliance decays within a session (odds ×0.944 per function written). | McMillan, arXiv 2605.10039 (2026-05) | medium |
| E6 | Conditional phrasing ("If you do X, then Y") is followed least. Adding a reason gave no gain. Style effects don't transfer across models. | Tsuchida et al., arXiv 2611.04367 (2026-11) | medium |
| E7 | Unstated requirements get guessed 41% of the time, but guessed ones regress about 2× as often after a model or prompt change. Conditional corner cases are the hardest to guess. | Yang et al., arXiv 2505.13360 (Findings ACL 2026) | medium |
| E8 | Models resolve conflicting instructions poorly, and a priority meta-rule doesn't fix it. Claude Code docs: "if two instructions contradict each other, Claude may pick one arbitrarily". | IHEval (NAACL 2025); code.claude.com memory | high |
| E9 | Instructions decay over long tasks (−39% when a task arrives across turns). CLAUDE.md is "context, not enforced configuration". To block regardless, use a PreToolUse hook or a deny rule. | Laban et al., arXiv 2505.06120; code.claude.com memory | high |
| E10 | Current Claude models over-trigger on "CRITICAL: You MUST…" and on "If in doubt, use X". ALL CAPS makes Claude over-apply a rule. "Never" is read as absolute. In third-party text, urgent framing is an injection marker (poisoned rules files reached 41–84% attack success). | platform.claude.com prompting best practices; code.claude.com best-practices; arXiv 2509.22040 | high (vendor) |
| E11 | Banning a concept the model wouldn't otherwise reach for can introduce it: 25/30 mentions with the ban vs 0/30 with a positive rewrite. A ban with no named alternative barely changes behaviour. | shahshrey/pink-elephant (2026, Sonnet 5.5); arXiv 2511.12381 | medium |
| E12 | Examples get copied, incidental details included. Use diverse examples and mark them off from instructions. | arXiv 2509.23196; Anthropic prompting docs | medium |

## Skills and descriptions

| ID | Finding | Source | Strength |
|---|---|---|---|
| E13 | Curated skills: +16 pp. Self-generated skills: −1.3 to −11.5 pp. "Comprehensive" skills: +0.7 pp, vs +19 pp for compact ones. | SkillsBench, arXiv 2602.12670 (+ v1.1, 2026-06) | high on direction |
| E14 | In 56% of cases the skill was never invoked. An 8 KB docs index in AGENTS.md scored 100%, against 79% for a skill plus an instruction to use it. | Vercel eval, 2026-01-27 | medium |
| E15 | Routing degrades as the library grows. Broad or overlapping descriptions get confused, and the usual failure is that no skill loads at all: recall fell from 73.6% to 27.4% with 1,000 distractors. | arXiv 2610.18522, 2608.04828, 2605.16508 | medium-high |
| E16 | A description that summarised the workflow was followed instead of the body. | superpowers writing-skills (anecdote) | low-medium |
| E17 | Claude Code cuts `description` + `when_to_use` at 1,536 characters. The skill listing gets about 1% of the context window, and the least-used skills' descriptions are dropped first. | code.claude.com skills | high |

## Hooks

| ID | Finding | Source | Strength |
|---|---|---|---|
| E18 | Only exit 2 or a JSON deny blocks. Exit 1, 127 (missing interpreter or script), a timeout or a crash lets the action through unless `onFailure: "block"` is set (2.1.295+; command and http hooks, not Stop-type or async). Seen live: a guard missing `jq` exited 127, so the guard was off. | code.claude.com hooks; local run 2026-11-19 | high |
| E19 | Matchers are case-sensitive. Plain names match exactly; anything else is an unanchored regex. A matcher on an event without matcher support is ignored silently. `if` on a non-tool event means the hook never runs. | code.claude.com hooks | high |
| E20 | The Stop continuation cap resets on every tool call, so a Stop hook demanding an action that another hook denies loops forever (one report: about $9.4k over 6 days). | code.claude.com hooks; anthropics/claude-code#107256 | medium |
| E21 | Command blocklists are bypassable and models route around them (base64 about 55 times in a week; six trivial bypasses of a local git guard). | code.claude.com hooks-guide; #106469; local run | high |
| E22 | Claude never sees stderr from exit 0. A synchronous hook's `systemMessage` goes to the user only; an async hook's goes to Claude only. `additionalContext` is capped at 10,000 characters, and past the cap Claude gets a 2,000-character preview and isn't told to read the rest. | code.claude.com hooks | high |
| E23 | Repo config is code. Project settings hooks and project-skill hooks run in `-p` without the trust dialog. Check Point showed RCE from committed hooks (GHSA-ph6w-f82w-28w6). | code.claude.com hooks; Check Point 2026-02-25 | high |

## Agents, MCP, security

| ID | Finding | Source | Strength |
|---|---|---|---|
| E24 | Plugin agents load the CLAUDE.md chain unless `omitClaudeMd: true` is set. They ignore `hooks`, `mcpServers`, `permissionMode` and `initialPrompt`. An `Agent(type)` list inside a subagent's `tools` is ignored. Unknown keys are ignored silently. | code.claude.com sub-agents, plugins; live test 2.1.328 | high |
| E25 | 97% of MCP tool descriptions have a defect (unclear purpose 56%, opaque parameters 84%). Fixing them adds +5.85 pp but +67% steps. Poisoned descriptions reach up to 72.8% attack success. | arXiv 2602.14878 (TOSEM 2026); MCPTox (AAAI 2026) | medium-high |
| E26 | 26.1% of 31k marketplace skills match a vulnerability pattern, and skills with scripts are 2.12× as likely to. Hidden Unicode (zero-width, bidi, tag characters) is read as instructions. | arXiv 2601.10338; Pillar "Rules File Backdoor" 2025-03 | high |
| E27 | Signature rules alone catch 7.7% of malicious skills. | Cisco skill-scanner README (self-measured) | medium |

## How to review

| ID | Finding | Source | Strength |
|---|---|---|---|
| E28 | Expert personas don't improve accuracy, and irrelevant persona detail cost up to 30 pp. | arXiv 2311.10054, 2512.05858, 2508.19764 | high |
| E29 | Checking each claim against an external source works: SAFE agrees with human annotators 72% of the time, and self-correction only works with external feedback. LLM judges agree with domain experts 60–64% of the time, against 72–75% between experts. | arXiv 2403.18802, 2406.01297, 2410.20266 | high |
| E30 | Reading alone detects ambiguity poorly (MCC ≤0.47). Comparing sampled executions detects it well (ClarifyGPT took pass@1 from 71% to 81%). | arXiv 2507.20439, 2310.10996 | medium-high, older models |
| E31 | All 8 judges tested favoured their own model's output, with a rubric or without. The bias disappears when a deterministic verifier decides. | arXiv 2611.18616, 2606.20093 | medium-high |
| E32 | 56% of an LLM reviewer's suggestions were rejected, mostly as false positives. Verifiers that try to refute each finding by default reduce this. | arXiv 2607.03316; claude-security scan-verifier | medium |
| E33 | From this repo owner's transcripts, the commonest failures:<br>- stale facts in docs (9 episodes);<br>- injected rules reaching the wrong session type (one block injected into 174 sessions, 127 of them unattended);<br>- references to disabled or missing skills, tools and binaries (5);<br>- a SessionStart hook failing silently in 173 sessions. | local transcript review, 2026-11 | medium |

## Checks not to make

These read like defects but the evidence doesn't support them. Raise them only with the stated extra reason.

- **File length on its own** (E5). Flag length only with a cost on a real load path (E1, E17) or a truncation limit.
- **A rule's position in the file on its own** (E5).
- **Markdown vs XML vs YAML structure.** Format effects are weak on current models.
- **A missing "because" on its own** (E6). A wrong reason, though, is a finding.
- **A cap on the number of rules.** Thresholds go stale with every model generation.
- **Scope-limiting prohibitions** such as "don't change files outside this stack" (E4).
- **Calibrated urgency in routing text** (skill and agent descriptions). Judge triggering with a trigger eval, not by tone. E10 applies to bodies.
- **Redundancy that agrees and works.** Flag it only where the copies can drift apart (E33).
- **Exact scripts for fragile operations.** Low freedom is right where only one sequence is safe.
- **Contract detail in tool descriptions.** Under-description is the common failure (E25).
- **A one-line role statement.**

## Judgement calls

Present these as decisions with both sides, not as defects.

- **What a description should say.** Anthropic says "what it does and when to use it". Superpowers says only when (E16). Flag a step sequence, not a one-clause "what".
- **Pushy or soft trigger wording.** Only a trigger eval on the current model settles it.
- **Prohibition or positive recipe.** Match the form to the failure: a ban for a discipline rule the agent breaks under pressure, a positive recipe for output shape (E11).
