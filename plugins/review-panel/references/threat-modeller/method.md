# Method: threat-modeller

Verified on 2026-11-19 (Claude Code 2.1.328 docs; tools as in TM25). Re-check live before a finding depends on a volatile fact (marked ⟳).

This adds the lens-specific steps to `${CLAUDE_PLUGIN_ROOT}/references/shared/review-method.md`. Evidence IDs resolve in `evidence.md` in this directory, the shared evidence file and `rules-lawyer/evidence.md`.

## Tiers for this lens

Costs assume Opus 5.5 list prices ($4 in, $5 for a 5-minute cache write, $0.20 cache read, $20 out per MTok; R13 F1, fetched 2026-11-19) and about 40 turns per run.

| Tier | Adds | Trigger | Added tokens | Added $ |
|---|---|---|---|---|
| T0 | Read the references, inventory, execution context, trifecta, threat table; no commands | Doc-only plan with no repo; the brief says static | ~8k context (refs ≈ 24 KB) | ≈ $0.10 |
| T1 (default) | T0 plus the `checks.md` rows whose file types exist (`git ls-files` first) and the kill-chain trace per BLOCK/SHOULD | Any repo or diff | +10–15k context, +3–4k out | ≈ $0.25–0.35 |
| T2 | Up to 3 sink probes in `mktemp -d`: copy one **pure** function (escaping, path join, URL parse, allowlist regex) and feed it a crafted input with `node -e` or `python3 -I -c` | A BLOCK/SHOULD whose link (b) turns on what a pure function does with hostile input | +~3k per probe | ≈ $0.05 per probe |

The T2 rule in this lens's terms: don't run a probe when a grep already shows the sink takes a constant or repo-own input (refuted, TM10), when the doc quote settles the behaviour, or when the function does I/O, network or imports anything beyond the standard library (describe the path instead).

## Inventory

Use claude-security's five headings (TM5). At most ~10 boundaries, each with a doc section or `path:line`.

- **Entry points:** where outside input arrives (HTTP routes, CLI args, webhooks, workflow triggers, issue and PR text, fetched pages, tool results, files a user drops in).
- **Sinks:** where input can do harm (shell, SQL, file paths, templates, an agent's prompt, auth decisions, outbound requests, a pushed commit).
- **Assumptions:** what the design takes on trust ("LAN only", "localhost", "contributors are friendly", "the token is set").
- **Trust boundaries:** where data crosses from less-trusted to more-trusted, and who sits outside each one.
- **Hot files:** the few files where the boundaries are enforced or broken.

## Execution-context question

Ask it first: does this run interactive, under `-p`/SDK, in CI, or in a cloud session? The answer decides what committed config does (TM12, TM13, E23). Every committed-agent-config finding states the context and the Claude Code version it assumes.

| Context | Committed config | Project `env` | Project `defaultMode` |
|---|---|---|---|
| interactive, trusted | hooks run after trust | applies after trust | auto/bypass don't take effect ⟳ |
| interactive, folder never trusted | trust dialog shown at start; nothing below applies until accepted | not applied (except "safe" vars) | (same) |
| interactive, only a parent trusted (non-repo subfolder) | hooks, `env`, `apiKeyHelper` used; allow rules wait; `.mcp.json` asks; shared `enableAllProjectMcpServers` ignored | applies | (same) |
| `-p`/SDK | hooks, `apiKeyHelper` and `.mcp.json` servers used without asking | applies at startup | (same) |
| CI | same as `-p`, plus whatever the workflow's token and secrets grant | (same as `-p`) | (same) |

Mitigations to look for: `--setting-sources user` (reads neither project settings nor `.mcp.json`); `--bare` (no project hooks, skills, plugins or `.mcp.json`, but the project `env` block and `awsAuthRefresh`-style helpers still apply); `--settings '{"disableAllHooks": true}'` (hooks only) (TM12).

## STRIDE prompt card

Ask these per boundary, silently (TM1). Output attackers, preconditions and paths, never a grid or category labels.

- Spoofing: can someone outside claim to be someone inside?
- Tampering: can they change data, code or config the inside trusts?
- Repudiation: can they act with no record that ties the act to them?
- Information disclosure: can they read secrets, private data or transcripts?
- Denial of service: skip it unless it is a lockout an attacker controls (the checks not to make in `evidence.md`).
- Elevation of privilege: can they get the inside's permissions, tokens or tools?

## Three adversaries

Use only when the artifact designs an API, config schema, hook or skill surface (TM6): the Scoundrel (abuses it on purpose), the Lazy Developer (copies the example, takes the default) and the Confused Developer (misreads the name or the order of arguments). "Nobody would actually do that" is not a refutation.

## Kill chain

Every BLOCK and SHOULD starts refuted and needs three links, each with evidence (TM8, TM11, E32):

- **(a) reachable:** the entry point, and an attacker who holds the precondition.
- **(b) impact:** the insecure value or path is the one that runs and reaches a security decision; cite the sink.
- **(c) no defence:** you looked where a defence would be and quote what's there.

A missing link makes it COULD, and you name the missing link. Worked examples:

- (b) fails: `env['K']` crashes when `K` is unset, so there is no fallback to exploit. `env.get('K','x')` runs `x`: check the unit or compose file for `K` before claiming the fallback runs.
- (a) fails: the input is something only the repo's own build produces (TM10).
- (c) fails: the defence lives in another layer, and you read and quote it.

Write the chain into `Evidence:` as `entry path:line → hop → sink path:line`.

## Diff scope rule

From claude-security (TM10): a finding belongs to a change only if the path runs through an added or altered line, a weakened check, or newly exposed code. Otherwise it is `PREDATES_CHANGE`: put it under Checked and fine as `predates change`.

## Sink probes (T2)

At most 3. Allowed shape only:

1. `d=$(mktemp -d)`
2. Copy one function into `$d` with `git show HEAD:<path>` (cut to the function).
3. Run it from `$d` with `node -e` or `python3 -I -c`, passing the payload.

Only standard-library string, path and URL functions. No `require` or `import` of repo modules with side effects, no network, no services. Report payload, result and verdict.

## Severity for this lens

The scale is in `${CLAUDE_PLUGIN_ROOT}/skills/assemble-panel/references/persona-contract.md`. Anchors for this lens:

- **BLOCK:** a fork PR gets code execution with a write token; a three-leg agent with no gate; a committed fallback secret that runs in production; a token scoped account-wide for a one-resource job (TM24).
- **SHOULD:** unpinned `npx -y …@latest` in shared MCP config; a localhost API with state-changing routes and no Origin/Host check.
- **COULD:** a first-party action at a major tag; an ineffective committed `bypassPermissions` (an intent smell).
- On test, example and debug paths, "between two tiers take the lower" (TM22).

## When to hand off to `/claude-security`

This lens covers plans and diffs at design level, trust boundaries across components, CI, agent and MCP configuration, supply-chain design, and hostile-repo and hostile-PR questions. Code-level sink hunting across a codebase or a large diff belongs to `/claude-security` (TM5, TM10, TM23). Recommend it under Coverage when any of these holds:

- the artifact is a code change touching auth, crypto, parsing or deserialisation across more than a few files;
- a sink candidate's trace couldn't be completed within T1/T2;
- the user asks "is this codebase secure?".

It also has a change mode (`scan-changes`) for a diff range. Coverage line: `/claude-security (scan changes, range <base>..<head>)`.

## Boundary with rules-lawyer

On agent-instruction text, rules-lawyer owns the text: wording, what loads, hook exit codes, tool-description quality. Report only a finding that names an attacker, a delivery route (PR, plugin update, fetched page, MCP rug pull) and a payoff beyond the text, citing E-rows rather than restating them (E18, E21, E23, E25–E27, TM2).
