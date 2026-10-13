# review-panel

Ten read-only persona reviewers, plus a skill that picks the right panel for the job and filters what comes back.

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
| `noise-filter` | agent | Final pass: merges duplicates, marks cross-confirmed findings, spot-checks evidence, drops noise |
| `/assemble-panel` | skill | Sizes and picks the panel, prints the roster, briefs and dispatches in parallel, runs `noise-filter`, presents one triage |

Every reviewer agent:

- is read-only apart from its own report file;
- has no `Agent` or `Edit` tool;
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

Plugin agents don't load your CLAUDE.md. The skill passes the relevant repo rules in each brief.

## Tests

```bash
bash plugins/review-panel/tests/all.sh
```

## Related

- [`orchestrate`](../orchestrate/README.md): the token-frugal delegation doctrine. A panel is one shape of fan-out.
