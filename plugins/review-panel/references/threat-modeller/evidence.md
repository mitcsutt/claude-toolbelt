# Evidence base: threat-modeller

Verified on 2026-11-19 (Claude Code 2.1.328 docs; tools as in TM25). Re-check live before a finding depends on a volatile fact (marked ⟳).

Cite these IDs in findings, the threat table and Checked and fine. Most 2026 papers are unreviewed preprints that test one model or one harness, so treat effect sizes as direction rather than as constants. A finding that rests on a version-sensitive platform fact needs the doc re-fetched (`https://code.claude.com/docs/en/<page>.md`), not this table; you have no fetch tool, so name the page under Coverage.

Method evidence (E28 and up) is in `${CLAUDE_PLUGIN_ROOT}/references/shared/evidence.md`. Agentic platform facts E18–E27 are in `${CLAUDE_PLUGIN_ROOT}/references/rules-lawyer/evidence.md`: this lens cites them and does not copy them (E18 hooks fail open, E21 blocklists are bypassable, E23 repo config is code, E25 MCP description poisoning, E26 skill risk and hidden Unicode, E27 signature rules catch 7.7%).

| ID | Finding | Source | Strength |
|---|---|---|---|
| TM1 | No study shows an LLM threat-models as well as an expert: even larger models' gains are "neither consistent nor sufficient for reliable threat modelling" (2605.10808), and a STRIDE case study found the same (2505.04101). Use STRIDE as a coverage prompt, never as the deliverable. | R11 F1 (arXiv 2605.10808, 2505.04101) | high (negative result) |
| TM2 | OWASP Top 10 for Agentic Applications 2026 (2025-12-09): ASI01 Goal Hijack … ASI06 Memory & Context Poisoning; ASI03 Identity & Privilege Abuse covers the confused deputy. ASI07–10 are runtime or emergent: COULD at most for a plan. | R11 F2 (genai.owasp.org/download/52117/) | high |
| TM3 | Cite only LLM01 and LLM06 from the 2025 LLM Top 10. The 2026 edition was a draft on 2026-11-19. | R11 F3 | medium (S for the full list) |
| TM4 | Lethal trifecta: private data + untrusted content + external communication in one agent context = exfiltration path. Guardrails claiming "95%" don't close it. | R11 F4 (simonwillison.net 2025-06-16) | high |
| TM5 | claude-security's threat-model step uses entryPoints, sinks, assumptions, trustBoundaries, hotFiles ("cite real lines"). For a change, "no tier maps the repository": no threat model on diffs. | R11 F5 (`claude-security/0.12.0/workflows/scan.js:117`; `jobs/scan-changes.md:63`) | high |
| TM6 | For API, config and hook designs the adversary can be the Scoundrel, the Lazy Developer or the Confused Developer. "Nobody would actually do that" is a rationalisation. | R11 F6 (`sharp-edges/1.1.3/skills/sharp-edges/SKILL.md:40,218-233`) | medium |
| TM7 | Unaided LLM vulnerability detection is near random on strict benchmarks, and renaming functions or variables flips 26% of answers (adding library functions, 17%). These are 2023–24 models, so possibly stale. | R11 F7 (arXiv 2403.18624, 2312.12575) | high, may be stale |
| TM8 | Anchoring verdicts to a concrete flow trace cuts false positives: LLM+static removed 94–98% of FPs in one industrial study. | R11 F8 (arXiv 2601.18844, 2510.02534, 2405.17238) | medium-high |
| TM9 | Anthropic's `/security-review` filters on confidence <8 plus a per-finding FP subtask. No published precision exists for it or for Claude Security. Its README says it is "not hardened against prompt injection". | R11 F9 | high (no numbers) |
| TM10 | Diff scope: a finding belongs to a change only if the path runs through an added or altered line, a weakened check, or newly exposed code. Otherwise it is `PREDATES_CHANGE`. Input only the repo's own code produces refutes. | R11 F10 (`scan.js:73`; `agents/scan-verifier.md:34,38`) | high |
| TM11 | Refuted-by-default kill chain: reachable in production? Is the insecure value the one that runs? Does it reach a security decision (cite the sink)? "Incomplete trace = refuted." Grep seeds use POSIX ERE, never `\s \d \b`. | R11 F11 (`insecure-defaults/2.0.3/README.md:51,67-75,87`) | high |
| TM12 | `claude -p`/SDK never shows the trust dialog, so the folder is never trusted, but settings hooks, `env` and `apiKeyHelper` are still used and `.mcp.json` servers are "Connected without asking, approved or not" (SDK: only when `settingSources` includes project). Project `permissions.allow` and `additionalDirectories` are not used. Extends E23 to CI. ⟳ | R11 F13 (code.claude.com/docs/en/permissions.md, fetched 2026-11-19) | high, volatile |
| TM13 | Committed `defaultMode: auto/bypassPermissions` doesn't take effect from project or local settings (`bypassPermissions` did before v2.1.257; `auto` already didn't). Shared-file `enableAllProjectMcpServers` is ignored before trust. Project `env` applies after trust, or at startup under `-p`. ⟳ | R11 F14 (settings-reference.md) | high, version-gated |
| TM14 | PromptPwnd: issue or PR text interpolated into an AI agent's prompt in GitHub Actions → privileged tools → leaked secrets. Named targets include Claude Code Actions. | R11 F15 (aikido.dev 2025-12-04) | high |
| TM15 | MCP Streamable HTTP: servers "MUST validate the `Origin` header" and "SHOULD bind only to localhost". CVE-2025-49596 (MCP Inspector): RCE through an unauthenticated localhost proxy. 127.0.0.1 is not a boundary against a browser (CVE-2025-49596 is CWE-306). All-interface binds are CWE-1327. | R11 F16 (MCP spec 2025-06-18; NVD) | high |
| TM16 | Exfil and auto-approve CVEs: EchoLeak CVE-2025-32711; Copilot CVE-2025-53773 (`autoApprove` mechanism is S via R4 F23); Claude Code CVE-2025-59536 (<1.0.111, code before trust) and CVE-2026-21852 (<2.0.65, API keys exfiltrated before trust). | R11 F17 (NVD); R4 F23 | high (mechanism of 53773: medium) |
| TM17 | ASI06: fetched or tool output written into a file later loaded as instructions (CLAUDE.md, memory, skill) is persistent injection. Snyk saw MEMORY.md poisoning. | R11 F18; R4 F22 | medium |
| TM18 | GitHub: avoid `pull_request_target`/`workflow_run` with untrusted code; pin actions to a SHA; least-privilege `GITHUB_TOKEN`. Security Lab: `ref: ${{ github.event.pull_request.head.sha }}` under `pull_request_target` is "INSECURE". GitHub now adds a default actions/checkout protection that refuses fork head refs unless `allow-unsafe-pr-checkout: true`; the version that introduced it is unstated ⟳. | R11 F19 (docs.github.com secure-use; securitylab.github.com 2021-08-03) | high |
| TM19 | Authorities for the other checks: OWASP HTML5 CS (no `*` or reflected Origin in CORS); npm `ignore-scripts`; Trojan Source CVE-2021-42574; CWE-78/95/494/798/829/942/1336. | R11 F19 | high |
| TM20 | Anthropic's hard exclusions (DoS, missing hardening, theoretical races, outdated libs, test-only files, docs, audit logs, "user content in system prompts") and its precedent that env vars are trusted cut noise. Three carve-outs apply here: plan docs are the subject; env set by a repo file is attacker-controlled; agentic GHA findings count when they name a trigger and an untrusted field. | R11 F20, §6 | high (list); carve-outs are R11's reasoning |
| TM21 | insecure-defaults skips credentials in docs and fixtures, local-only dev servers, and loopback debug endpoints. The localhost skip holds only with a Host/Origin check or no state-changing routes (TM15). | R11 F21 | high |
| TM22 | Fixtures are background, but a secrets pass still checks them. Test, example and debug paths: "re-examine before going above MEDIUM". "Between two tiers take the lower." | R11 F22 (`claude-security` README:49; `scan-researcher.md:43`) | high |
| TM23 | claude-security assumes "The code you scan is trusted". Its four lenses (injection, auth, memory, crypto/secrets) don't cover supply chain, CI config or agent-permission design. Those, plus hostile-repo/PR questions, are this reviewer's territory. | R11 F23, F24 (`SECURITY.md:17`; `scan.js:45`) | high |
| TM24 | Owner's only real panel (n=1) skipped threat-modeller as "static site". goal-auditor found a Workers deploy token that could redeploy any Worker, and the token later shipped account-wide. Real episodes: dev servers bound to 0.0.0.0 (twice), stale public exposure. | R16 F8, M3, §5 | measured, n=1 |
| TM25 | Locate-only commands verified 2026-11-19 (git 2.43.0, ripgrep 14.1.1, perl). `git grep -o` prints only the match (`process.env.OPS_TOKEN ||`, not the value). `rg` matches `[\x{E0000}-\x{E007F}]`. The perl scan prints `file:line` only. | local run, this plan | measured |

## Checks not to make

- DoS, rate limits, resource exhaustion, missing audit logs, generic "add validation/hardening/WAF" (TM20). Paid-API loops go to cost-hawk.
- Outdated-library CVEs (TM20). Unpinned or fetched-and-executed code stays in scope.
- Fake keys in fixtures, docs and `.example` files, and AWS's `…EXAMPLE` documentation keys, unless a production path loads them. Name the file, never the value (TM21, TM22).
- Theoretical races or timing attacks with no attacker holding the precondition (TM20).
- Pre-existing flaws in a diff review (TM10).
- Input only the repo's own code produces (TM10).
- Committed `defaultMode: bypassPermissions`/`auto` as if it took effect: it doesn't from project settings on current versions. COULD at most, unless the plan pins a version before 2.1.257 and the mode is `bypassPermissions` (TM13).
- Shared-file `enableAllProjectMcpServers` in a repo that is only ever used interactively. It becomes real under `-p`/SDK/CI (TM13, TM12).
- "User content in the system prompt" for an agent with no tools and no private data (TM20). In scope once a second trifecta leg appears (TM4).
- Loopback read-only metrics or debug endpoints (TM21). Loopback with state-changing routes is not exempt (TM15).
- A STRIDE grid or category labels as findings (TM1).
- The `/security-review` exclusions for markdown docs, env vars and GHA when the carve-outs apply (TM20): doctrine from R11 §6.
