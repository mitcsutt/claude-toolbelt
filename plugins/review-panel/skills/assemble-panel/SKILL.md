---
name: assemble-panel
description: Use when the user asks for a panel of subagent reviewers with different personas or personalities. Triggers include "create some subagent reviewers", "give each a different personality", "review this with personas", "panel review", "make one a grumpy senior dev", and "/assemble-panel". The target can be a plan, spec, TRD, diff, PR stack or decision. Picks the right reviewers from the review-panel agents, briefs and dispatches them in parallel, then filters their reports into one triage.
---

# Assemble a review panel

Pick a small panel of persona reviewers, each with its own lens, brief them well, run them in parallel, and hand back one filtered triage.

Personas don't make a model more accurate. What a panel gives you is **coverage**: independent reviewers, each with a lens that doesn't overlap the others, a strict output contract, and a separate filter pass. Every step below protects one of those.

## The roster

Agents are dispatched as `review-panel:<name>`.

| Agent | Hunts | Signature deliverable |
| --- | --- | --- |
| `grumpy-senior` | Over-engineering, scope creep, reinvention | "Ship it on Friday" cut-down version |
| `paranoid-sre` | Restarts, partial failure, races, ports, locks, rollback | Failure table as event sequences |
| `fact-checker` | Every concrete technical claim | Claim ledger: CONFIRMED / WRONG / UNVERIFIED |
| `dx-advocate` | New engineer + unattended agent journeys, stale docs, repo rules | Journey walk + stale-docs list |
| `threat-modeller` | Trust boundaries, auth, injection, secrets, supply chain | Threat table with attack paths |
| `goal-auditor` | Is the goal actually met? Overclaims, absences (tests, rollback, bad inputs) | Goal scorecard |
| `outsider` | Right route? Build vs buy? Prior art and competitors | Cold take + prior-art table |
| `contract-keeper` | Consumers of changed interfaces, migrations, backfill, version skew | Consumer matrix + safe rollout order |
| `cost-hawk` | Steady-state cost, growth, hot paths, token and dollar spend | Cost sheet |
| `noise-filter` | Final pass only: merge, spot-check, drop noise | Consolidated triage |

## Step 0: Size the panel

| Situation | Panel |
| --- | --- |
| Trivial (typo, one-line fix, config bump) | Say a panel isn't worth it. Offer `goal-auditor` alone, or nothing. Stop unless the user insists. |
| Normal (a feature, a plan, a PR) | 3 to 4 reviewers |
| High-stakes (architecture, data, auth, multi-PR stack, irreversible) | 5 to 6 reviewers |

Then always add `noise-filter` as the final pass, on top of that count. Never run all nine reviewers by reflex: each one in past panels cost 100k to 170k tokens.

## Step 1: Classify

Name the **artifact type** (plan, spec/TRD, diff/PR, PR stack, decision, docs) and scan it for **risk signals**:

- auth, secrets, input from outside, or tools an LLM can call;
- a changed API, schema, config, CLI, event or file format;
- data migration or backfill;
- processes, daemons, containers, ports, installers or hooks;
- polling, background work, fan-out, paid APIs or LLM calls;
- something new people or agents will set up and use;
- claims about how a platform, library or tool behaves;
- a build-vs-buy question, or a new system where tools already exist.

## Step 2: Pick

Start from the core for the artifact type, then add one reviewer per risk signal until you reach the size from Step 0.

| Artifact | Core |
| --- | --- |
| Plan / spec / TRD | `goal-auditor`, `grumpy-senior`, `fact-checker` |
| Diff / PR / stack | `goal-auditor`, `paranoid-sre` |
| Decision / proposal | `outsider`, `grumpy-senior` |
| Docs / onboarding / skills | `dx-advocate`, `fact-checker` |

| Signal | Add |
| --- | --- |
| Auth, secrets, outside input, agent tools | `threat-modeller` |
| Interface, schema or format change; migration | `contract-keeper` |
| Processes, ports, installs, hooks, retries | `paranoid-sre` |
| Polling, background work, paid calls, LLM fan-out | `cost-hawk` |
| New thing people or agents will use | `dx-advocate` |
| Platform or library behaviour claims | `fact-checker` |
| Build vs buy; prior art exists | `outsider` |

Rules:

- **The user's picks win.** If they named a persona ("make one a grumpy senior dev"), include the matching agent and fill the rest yourself. If they named a lens no agent covers (accessibility, test coverage, product owner), write an **ad-hoc persona** with [persona-contract.md](references/persona-contract.md) and dispatch it as `general-purpose`.
- **No overlap.** If two picks would hunt the same thing for this artifact, keep one.
- **At most one ad-hoc persona per panel**, unless the user asked for more.

## Step 3: Print the roster, then dispatch

Show one line per reviewer and one line for what you skipped, then go straight on. Don't wait for approval; the user can interrupt.

```
Panel (4 + filter):
- goal-auditor: core for a plan; full context
- grumpy-senior: user asked for one; spec only, no repo history
- paranoid-sre: plan adds a daemon and a port; full repo
- fact-checker: 6 claims about Docker on macOS; spec + live docs
- noise-filter: final pass
Skipped: threat-modeller (no new trust boundary), cost-hawk (no steady-state work)
```

## Step 4: Brief each reviewer

Build every brief from [brief-template.md](references/brief-template.md). The agent files already carry each persona's lens, contract and boundaries; the brief supplies what changes per run. Non-negotiables:

- **Goal first, fixed.** State the goal and say "don't argue against the goal itself".
- **Choose a context level for each reviewer on purpose.** Vary it:
  - `outsider` gets the problem statement first and the artifact second;
  - `grumpy-senior` often does best with the artifact alone;
  - `fact-checker` and `goal-auditor` get the repo.
- **Pass the repo's hard rules.** Plugin agents don't load CLAUDE.md. Paste the rules that bear on the artifact, or name the files to read.
- **Give each reviewer a report path** under the session scratchpad, e.g. `<scratchpad>/panel-<slug>/<agent>.md`. The reviewer returns one line.
- **Use read-only snapshots.** If reviewers need a branch state, point them at `git show <ref>:<path>` or a separate read-only worktree. Never have a reviewer check out branches in a tree others are reading.

## Step 5: Dispatch

Send every reviewer in **one message** so they run in parallel, in the background. Don't relay results as they land; wait for the whole wave.

## Step 6: Filter

Dispatch `review-panel:noise-filter` with the goal, the report paths and a triage path. It merges duplicates, marks cross-confirmed findings, spot-checks evidence and drops noise. Don't do this pass yourself in the main context. The reports are long, and the filter's independence is the point.

## Step 7: Present

Read the triage and give the user:

1. The overall verdict and the panel line (`reviewer · verdict`).
2. **Fix before merge**, with cross-confirmed findings first. Keep each to one or two lines with its location.
3. **Decisions for you**, through AskUserQuestion when there are any, each with its options.
4. Counts for "worth doing" and "dropped", plus the triage file path for detail.

Don't apply fixes, post comments or publish anything until the user says so. Any review that's going to another person stays a draft until the user approves it.

## Follow-ups

- **"Run it again after the fixes."** Re-run only the reviewers who raised BLOCKs, with the previous triage attached. Don't re-run the whole panel.
- **"Add a `<persona>`."** Dispatch that one reviewer, then re-run `noise-filter` over all reports.
