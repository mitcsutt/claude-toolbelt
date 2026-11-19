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
| `fact-checker` | agent | Marks every technical claim CONFIRMED / WRONG / UNVERIFIED against live docs, source or a real run |
| `dx-advocate` | agent | Walks a new engineer and an unattended agent through each journey; finds stale docs and repo-rule breaches |
| `threat-modeller` | agent | Trust boundaries, auth, injection, secrets, supply chain, each as an attack path; never prints secret values |
| `goal-auditor` | agent | Re-derives the author's claims; scores whether the goal is met; checks absences (tests, rollback, bad inputs) |
| `outsider` | agent | Cold take before reading the artifact; build vs buy; prior art and competitors |
| `contract-keeper` | agent | Every consumer of a changed interface; migrations, backfill, version skew, safe rollout order |
| `cost-hawk` | agent | Steady-state cost, growth, hot paths, token and dollar spend, as a cost sheet |
| `rules-lawyer` | agent | Agent instructions (CLAUDE.md, AGENTS.md, rules, skills, commands, hooks, subagents, output styles, MCP descriptions): a load map, literal readings, a claim ledger, hook fixture runs and follower runs on risky lines |
| `instruction-follower` | agent (helper) | Dispatched only by `rules-lawyer`: follows an artifact on paper for one scenario and reports where it had to guess |
| `noise-filter` | agent | Final pass: merges duplicates, marks cross-confirmed findings, spot-checks evidence, drops noise |
| `/assemble-panel` | skill | Sizes and picks the panel, prints the roster, briefs and dispatches in parallel, runs `noise-filter`, presents one triage |

Every reviewer agent:

- is read-only apart from its own report file;
- has no `Edit` tool, and no `Agent` tool except `rules-lawyer`, which spawns only the read-only `instruction-follower`, within a fixed budget;
- never checks out branches;
- never prints secret values;
- returns one line pointing at its report file.

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
3. Prints the roster, with why each reviewer was picked and what was skipped.
4. Dispatches them all in parallel, then runs `noise-filter` over their reports.
5. Hands back:
   - Fix before merge;
   - Decisions for you;
   - counts for "worth doing" and "dropped".

For a lens the roster doesn't cover, the skill writes an ad-hoc persona from `references/persona-contract.md`.

You can dispatch any agent directly too, e.g. `review-panel:fact-checker` on a spec.

Reviewers load your CLAUDE.md chain like any subagent. The skill passes rules that live elsewhere in each brief.

### Reviewing agent instructions with `rules-lawyer`

Point it at a CLAUDE.md, a skill, a hook, a subagent or a whole plugin: "have the rules lawyer go over our CLAUDE.md and the deploy skill". It reviews from two sides: the agent that has to follow the text, and the person who has to live with what the agent does.

- **What's in force.** A load map of every file, rule, skill and hook in scope: when each loads or fires, which sessions it reaches (interactive, headless, subagent, other tools), and its context cost.
- **Domain expertise without a persona.** Expert personas don't make a model more accurate. Checking claims against sources does. So it builds a claim ledger: every flag, path, API behaviour and number the agent will act on, checked against `--help`, the source or the live docs, and marked CONFIRMED, WRONG or UNVERIFIED. It also checks that the artifact says what to do when those actions fail in documented ways.
- **Ambiguity shown, not asserted.** At tier T2 it hands risky lines to fresh `instruction-follower` agents, at most 3 scenarios × 3 runs. A line where the followers act differently becomes a finding, with each reading quoted.
- **Hooks run, not just read.** Guards are piped fixture events and probed for fail-open and bypass paths.

It picks a tier and says why: T0 static only, T1 static plus verification (the default), T2 with followers. Its checklists and the evidence behind each check live in [`references/rules-lawyer/`](references/rules-lawyer/evidence.md). It complements Claude Code's own `/doctor prompt-audit`, which doesn't cover hooks, settings or MCP config, run the instructions, or check domain claims against outside sources.

## Tests

```bash
bash plugins/review-panel/tests/all.sh
```

Two eval cases grade `rules-lawyer` against a no-plugin baseline. Each planted defect has its own grader, so the score shows which kinds get caught.

- `rules-lawyer-planted-defects` seeds a Terraform repo with familiar defects across CLAUDE.md, a skill, two hooks and a subagent.
- `rules-lawyer-platform-traps` seeds a Node repo whose instructions rely on Claude Code behaviour that isn't true.

The first case also has a sound rule that should survive the audit. It costs real model calls, so run it by hand (see [`docs/testing.md`](../../docs/testing.md)):

```bash
claude plugin eval plugins/review-panel --scaffold --runs 1 --allow-tools Bash Write WebFetch WebSearch
```

Granting Bash needs a sandbox backend. Without one, use `--allow-tools Write WebFetch WebSearch --disallow-tools Bash` instead; both arms then skip shell checks.

## Related

- [`orchestrate`](../orchestrate/README.md): the token-frugal delegation doctrine. A panel is one shape of fan-out.
