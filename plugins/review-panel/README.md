# review-panel

Read-only persona reviewers, plus a skill that picks the right panel for the job and filters what comes back.

## Why

"Spin up some subagent reviewers with different personalities" works well when the personas are built properly, and it takes effort to rebuild them from scratch every time. The research is clear that a persona doesn't make a model more accurate. What a panel adds is coverage:

- independent reviewers, each with a lens that doesn't overlap the others;
- an evidence rule and a fixed output contract for every finding;
- a separate pass that filters the noise.

This plugin keeps those pieces in one place, so a panel takes one sentence to ask for.

## What it ships

| Component | Kind | What |
| --- | --- | --- |
| `grumpy-senior` | agent | Hunts over-engineering; delivers a "ship it on Friday" cut-down version |
| `paranoid-sre` | agent | Failure modes (restarts, partial failure, races, ports, locks, rollback) as concrete event sequences |
| `fact-checker` | agent | Builds a capped, version-anchored claim ledger (CONFIRMED / WRONG / UNVERIFIED) from harmless probes, source at the matching tag and fetched versioned docs; a WRONG needs a quoted contradiction and survives a refutation pass |
| `dx-advocate` | agent | Walks a new engineer and an unattended agent through each journey; finds stale docs and repo-rule breaches |
| `threat-modeller` | agent | Trust boundaries, auth, secrets, agent/CI/MCP and supply-chain attack paths, each with a traced source and sink; locate-only checks that never print secret values |
| `goal-auditor` | agent | Re-derives "fixed"/"done" claims: reads the pre-fix code, runs a fail-before/pass-after check in a temp copy, hunts missed callers, wrappers and siblings; scores the goal with evidence per row |
| `outsider` | agent | Cold take before reading the artifact; build vs buy; prior art and competitors |
| `contract-keeper` | agent | Every consumer of a changed interface, named and quoted; break rules borrowed per interface type (OpenAPI, protobuf, GraphQL, events, exports, CLI, env, config); dated migration-safety rules; version skew and safe rollout order |
| `cost-hawk` | agent | Steady-state cost, growth, hot paths, token and dollar spend, as a cost sheet |
| `rules-lawyer` | agent | Agent instructions (CLAUDE.md, AGENTS.md, rules, skills, commands, hooks, subagents, output styles, MCP descriptions): a load map, literal readings, a claim ledger, hook fixture runs and follower runs on risky lines |
| `instruction-follower` | agent (helper) | Dispatched only by `rules-lawyer`: follows an artifact on paper for one scenario and reports where it had to guess |
| `noise-filter` | agent | Final pass: parses every report in code, runs yes/no checks on each finding, spot-checks evidence, merges only same-cause findings, and accounts for every finding (`check-triage`) in a short triage with an at-a-glance block |
| `/assemble-panel` | skill | Sizes and picks the panel, prints the roster, briefs and dispatches in parallel, runs `noise-filter`, presents one triage |

Every reviewer agent:

- is read-only apart from its own report file;
- has no `Edit` tool, and no `Agent` tool except `rules-lawyer`, which spawns only the read-only `instruction-follower`, within a fixed budget;
- never checks out branches;
- never prints secret values;
- returns one line pointing at its report file.

Shared method lives in `references/shared/`: the evidence table every reviewer cites, the review method (tiers, refutation, coverage, severity anchors) and the claim-ledger procedure. `lib/report-parse.mjs` parses the shared report contract; the tests use it to check every agent's output template, and `noise-filter` runs it to index the findings, propose merge candidates and check that its triage accounts for every finding exactly once (`findings`, `merges`, `check-triage`).

## Install

```text
/plugin marketplace add mitcsutt/claude-toolbelt
/plugin install review-panel@claude-toolbelt
```

## Usage

Ask the way you normally would:

- "Create some subagent reviewers for this plan and give them personalities that match."
- "Review PR 123 with a panel; make one a grumpy senior dev."
- `/assemble-panel docs/specs/foo.md`

What the skill does:

1. Classifies the artifact and its risk signals.
2. Picks the core reviewers plus one per signal: 3 to 4 for normal work, 5 to 6 for high-stakes work.
3. Prints the roster, with why each reviewer was picked, what was skipped, and the one reviewer who owns claim checks.
4. Prints one status line with the expected wall time; if a hook or permission refuses the dispatch, it stops and asks instead of working around it.
5. Dispatches them all in parallel, then runs `noise-filter` over their reports (a panel of one reviewer skips the filter).
6. Hands back:
   - the triage's at-a-glance block;
   - Fix before merge;
   - Decisions for you;
   - counts for "worth doing", "pre-existing" and "dropped".

Cost: one measured panel (six reviewers plus the filter) took about 50 minutes and about $20–35 at list price; see the skill's Step 0.

For a lens the roster doesn't cover, the skill writes an ad-hoc persona from `references/persona-contract.md`.

You can dispatch any agent directly too, e.g. `review-panel:fact-checker` on a spec. It checks at most 15 claims, most consequential first; for a full pass over reference docs, say "check every claim" in the brief.

Reviewers load your CLAUDE.md chain like any subagent. The skill passes rules that live elsewhere in each brief.

### Reviewing agent instructions with `rules-lawyer`

Point it at a CLAUDE.md, a skill, a hook, a subagent or a whole plugin: "have the rules lawyer go over our CLAUDE.md and the deploy skill". It reviews from two sides: the agent that has to follow the text, and the person who has to live with what the agent does.

- **What's in force.** A load map of every file, rule, skill and hook in scope: when each loads or fires, which sessions it reaches (interactive, headless, subagent, other tools), and its context cost.
- **Domain expertise without a persona.** Expert personas don't make a model more accurate. Checking claims against sources does. So it builds a claim ledger: every flag, path, API behaviour and number the agent will act on, checked against `--help`, the source or the live docs, and marked CONFIRMED, WRONG or UNVERIFIED. It also checks that the artifact says what to do when those actions fail in documented ways.
- **Ambiguity shown, not asserted.** At tier T2 it hands risky lines to fresh `instruction-follower` agents, at most 3 scenarios × 3 runs. A line where the followers act differently becomes a finding, with each reading quoted.
- **Hooks run, not just read.** Guards are piped fixture events and probed for fail-open and bypass paths.

It picks a tier and says why: T0 static only, T1 static plus verification (the default), T2 with followers. Its checklists and the evidence behind each check live in [`references/rules-lawyer/`](references/rules-lawyer/evidence.md), with the method evidence in [`references/shared/`](references/shared/evidence.md). It complements Claude Code's own `/doctor prompt-audit`, which doesn't cover hooks, settings or MCP config, run the instructions, or check domain claims against outside sources.

### Auditing a "fixed it" claim with `goal-auditor`

Dispatch it alone with the claim as the goal: "have the goal auditor check that PR 123 really fixes #12". It scores each part of the goal, with evidence on every row, and anything it couldn't settle stays "claimed only".

- It picks a tier and says why: T0 reads and quotes, T1 (the default) adds diff checks, pre-fix reads and hunts for missed callers, wrappers and siblings, and T2 adds the revert check.
- At T2 it runs the new tests against the pre-fix tree and the fixed tree, each in a temp copy built with `git archive`. It never touches your checkout, never installs anything, and never runs the tests of an untrusted outside contributor.

Its checks, the revert-check recipe and the evidence behind them live in [`references/goal-auditor/`](references/goal-auditor/evidence.md).

## Tests

```bash
bash plugins/review-panel/tests/all.sh
node --test plugins/review-panel/lib/*.test.mjs
```

Seven eval cases grade a reviewer against a no-plugin baseline, and six grade the skill's routing. Each planted defect has its own grader, so the score shows which kinds get caught.

- `rules-lawyer-planted-defects` seeds a Terraform repo with familiar defects across CLAUDE.md, a skill, two hooks and a subagent.
- `rules-lawyer-platform-traps` seeds a Node repo whose instructions rely on Claude Code behaviour that isn't true.
- `fact-checker-planted-claims` seeds a build plan with six version-qualified claims that were true or widely believed before mid-2026 and are wrong for the versions it names, plus two true-but-surprising controls. Regex graders check the verdict on each planted `PLAN.md:<line>`.
- `noise-filter-planted-triage` seeds a small Node rate limiter, one change under review and five pre-written reports with 15 findings: real bugs worded differently, folklore, a wrong line, a mis-stated duplicate, a doc mismatch that is really a code bug, a pre-existing bug, nits and process noise. Both arms are told the triage format, so the score compares judgement, not format.
- `goal-auditor-fix-claim` seeds a small Node repo whose branch claims to fix two issues. One fix has a test that passes without it, misses a post-URL path behind a legacy helper and trims only the issue's exact spacing; the other is sound. Every defect can be decided by reading, so the case measures T1, not the revert check.
- `assemble-panel-*` (6 cases) run with `Agent` withheld and grade the roster, claim owner and status line the skill prints before it would dispatch, so no reviewer runs. `assemble-panel-route-typo-control` is the control: a one-word fix that should get no panel. `assemble-panel-blocked-add-persona` checks that adding a reviewer doesn't re-read the earlier reports. The blocked-dispatch fallback (`Panel blocked:`) isn't covered, because plugin eval can't stage a refused `Agent` call. Run with `--ablation none` and compare against the same cases on `main`'s skill.
- `threat-modeller-homelab-agent` seeds a homelab-agent repo whose plan and first cut add a PR review bot, an issue-triage agent, a LAN ops API, a localhost dashboard bridge, a research agent with team memory and a shared MCP setup: ten agentic and design-level attack paths, one restraint item (a committed permission mode that doesn't take effect), a control (AWS's documentation example key in a test fixture) and a grader that fails if the report reproduces the committed ops token.
- `contract-keeper-orders-v2` seeds a small orders service whose latest commit breaks eight consumers: a response enum value hitting an exhaustive switch, a required request field "safe because it has a default", a field turned nullable, an env var a cron script still reads, a volatile column default, a constant default on a database pinned to Postgres 10, a request enum value an old mobile build still sends, and an ordinal enum insert. Its two controls (an optional response field, an unused export removed) must not be graded BLOCK or SHOULD.

`rules-lawyer-planted-defects`, `fact-checker-planted-claims`, `noise-filter-planted-triage`, `goal-auditor-fix-claim` and `threat-modeller-homelab-agent` each have controls that should survive: a sound rule, two true claims that recall tends to mark wrong, a real BLOCK raised by one hedged, low-confidence reviewer, a sound fix, and a documented example key in a test fixture that should be left alone. The cases cost real model calls, so run them by hand (see [`docs/testing.md`](../../docs/testing.md)):

```bash
claude plugin eval plugins/review-panel --case 'rules-lawyer-*' --scaffold --runs 1 --allow-tools Bash Write WebFetch WebSearch
claude plugin eval plugins/review-panel --case 'noise-filter-*' --scaffold --runs 3 --allow-tools Bash Write
claude plugin eval plugins/review-panel --case 'goal-auditor-*' --scaffold --runs 2 --allow-tools Write --disallow-tools Bash PowerShell
claude plugin eval plugins/review-panel --case 'assemble-panel-*' --scaffold --runs 3 --ablation none --disallow-tools Bash PowerShell
claude plugin eval plugins/review-panel --case 'fact-checker-*' --scaffold --runs 2 --allow-tools Write WebFetch WebSearch --disallow-tools Bash PowerShell
claude plugin eval plugins/review-panel --case 'threat-modeller-*' --scaffold --runs 2 --allow-tools Write --disallow-tools Bash PowerShell
claude plugin eval plugins/review-panel --case 'contract-keeper-*' --scaffold --runs 2 --allow-tools Write --disallow-tools Bash PowerShell
```

Granting Bash needs a sandbox backend. Without one, use `--allow-tools Write WebFetch WebSearch --disallow-tools Bash` for the rules-lawyer cases and `--allow-tools Write --disallow-tools Bash PowerShell` for the noise-filter case; both arms then skip shell checks, and `noise-filter` runs its no-shell fallback (T0), so record which mode ran. The goal-auditor case is built to run without Bash; on a host with a sandbox, run it once with `--allow-tools Bash Write` and check that `REVIEW.md` shows `before … exit=` and `after … exit=` pairs. The threat-modeller case always runs without Bash, so its checks go through the Grep tool.

## Related

- [`orchestrate`](../orchestrate/README.md): the token-frugal delegation doctrine. A panel is one shape of fan-out.
