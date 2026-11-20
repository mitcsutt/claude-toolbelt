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
| `dx-advocate` | New engineer + unattended agent journeys, stale docs, repo rules | Resolution table + journey walk + stale-docs list |
| `threat-modeller` | Trust boundaries, auth, injection, secrets and credential scope, agent/CI/MCP attack paths, supply chain | Threat table with attack paths |
| `goal-auditor` | Is the goal actually met? Overclaims, tests that pass without the fix, missed callers and siblings, absences (rollback, bad inputs) | Goal scorecard with evidence per row |
| `outsider` | Right route? Build vs buy? Prior art and competitors | Cold take + prior-art table |
| `contract-keeper` | Consumers of changed interfaces, migrations, backfill, version skew | Consumer matrix + safe rollout order |
| `cost-hawk` | Steady-state cost, growth, hot paths, token and dollar spend | Cost sheet |
| `rules-lawyer` | Agent instructions: what actually loads, literal misreadings, contradictions, stale or wrong claims, hooks that fail open, context waste | Load map + redline; follower runs on risky lines |
| `noise-filter` | Final pass only: merge, spot-check, drop noise | Consolidated triage |

## Step 0: Size the panel

| Situation | Panel |
| --- | --- |
| Trivial (typo, one-line fix, config bump) | Say a panel isn't worth it. Offer `goal-auditor` alone, or nothing. Stop unless the user insists. |
| Normal (a feature, a plan, a PR) | 3 to 4 reviewers |
| High-stakes (architecture, data, auth, multi-PR stack, irreversible) | 5 to 6 reviewers |

Then add `noise-filter` as the final pass, on top of that count, unless this is a **single-reviewer panel**: the user asked for one reviewer here in Step 0. A follow-up that adds one reviewer to an earlier panel is not one; it still re-runs `noise-filter` over all reports. Never run every reviewer by reflex. Extra reviewers add cost faster than coverage (see [evidence](references/evidence.md) AP1–AP4).

What a panel costs, measured once (2026-10-26, six Opus 5.5 reviewers plus the filter):
- each reviewer peaked at 134k–307k tokens of context and processed about 0.45M–17.1M tokens in total, mostly cache reads;
- the panel produced about 587k output tokens and took about 50 minutes;
- at 2026-11-19 list prices that is roughly $20–35, or $2–7 per reviewer. On a subscription it comes out of plan limits instead.

`rules-lawyer` at tier T2 adds up to nine short follower runs.

## Step 1: Classify

Name the **artifact type** (plan, spec/TRD, diff/PR, PR stack, decision, docs, agent instructions), then check the artifact against every row of the Signal table below. For a row you skip although the artifact touches its area, the skip reason must name what you looked for and didn't find ("no credential, auth or outside input in PLAN.md"), never the artifact's genre ("static site" isn't a reason).

## Step 2: Pick

Start from the core for the artifact type, then add one reviewer per signal until you reach the size from Step 0.

| Artifact | Core |
| --- | --- |
| Plan / spec / TRD | `goal-auditor`, `grumpy-senior`, `fact-checker` |
| Diff / PR / stack | `goal-auditor`, `paranoid-sre` |
| Decision / proposal | `outsider`, `grumpy-senior` |
| Docs / onboarding | `dx-advocate`, `fact-checker` |
| Agent instructions | `rules-lawyer`, `goal-auditor` |

| Signal | Add |
| --- | --- |
| Text, hooks or tool descriptions that steer an LLM agent (CLAUDE.md, AGENTS.md, rules, skills, commands, hooks, subagents, output styles, MCP descriptions) | `rules-lawyer` |
| Auth, secrets, credentials or deploy tokens (CI secrets included), input from outside, tools an LLM can call, CI that runs on contributor input | `threat-modeller` |
| A changed API, schema, config, CLI, event or file format; migration or backfill | `contract-keeper` |
| Processes, daemons, containers, ports, installers, hooks, retries | `paranoid-sre` |
| Polling, background work, fan-out, paid APIs or LLM calls | `cost-hawk` |
| Something new people or agents will set up and use | `dx-advocate` |
| Claims about how a platform, library or tool behaves | `fact-checker` |
| A build-vs-buy question; prior art or existing tools | `outsider` |

When the core plus the signals exceed the size, drop first a core reviewer whose lens has nothing to work on in this artifact (`fact-checker` with no platform claims), then the signal with the least at stake.

Rules:

- **The user's picks win.** If they named a persona ("make one a grumpy senior dev"), include the matching agent and fill the rest yourself. If they named a lens no agent covers (accessibility, test coverage, product owner), write an **ad-hoc persona** with [persona-contract.md](references/persona-contract.md) and dispatch it as `general-purpose`.
- **No overlap.** If two picks would hunt the same thing for this artifact, keep one. On agent instructions, `rules-lawyer` owns the claim checks and the literal reading, so don't add `fact-checker` or `dx-advocate` for the same text; `dx-advocate` still fits the human docs around it. On agent instructions, `rules-lawyer` owns the text and `threat-modeller` owns attack paths beyond it; for a full code-level security scan, suggest `/claude-security` rather than a bigger panel.
- **One claim owner.** Exactly one reviewer checks technical claims: `rules-lawyer` on agent instructions, otherwise `fact-checker` if picked, otherwise `goal-auditor`. Every other reviewer's brief gets the "Lens boundaries" section from [brief-template.md](references/brief-template.md), so the lenses don't all re-check the same claims.
- **At most one ad-hoc persona per panel**, unless the user asked for more.

## Step 3: Print the roster, then dispatch

Show one line per reviewer, one line naming the claim owner, and one line for what you skipped. Then print the status line below and go straight on. Don't wait for approval; the user can interrupt.

    Panel running: <n> reviewers + filter. Expect about <a>–<b> min; I'll report once, when the triage is ready.

On a single-reviewer panel, say `1 reviewer, no filter` and "when its findings are ready" instead.

Take `<a>–<b>` from this table, not from memory: 1 reviewer 10–20; 2–3 reviewers 20–35; 4–6 reviewers 25–40. The slowest reviewer gates the filter. One measured run took 8.5–21.6 min per reviewer and 12 min for the filter.

```
Panel (4 + filter):
- goal-auditor: core for a plan; full context
- grumpy-senior: user asked for one; spec only, no repo history
- paranoid-sre: plan adds a daemon and a port; full repo
- fact-checker: 6 claims about Docker on macOS; spec + live docs
- noise-filter: final pass
Skipped: threat-modeller (no new trust boundary), cost-hawk (no steady-state work)
Claim owner: fact-checker
```

## Step 4: Brief each reviewer

Build every brief from [brief-template.md](references/brief-template.md). The agent files already carry each persona's lens, contract and boundaries; the brief supplies what changes per run. Non-negotiables:

- **Goal first, fixed.** State the goal and say "don't argue against the goal itself".
- **Choose a context level for each reviewer on purpose.** Vary it:
  - `outsider` gets the problem statement first and the artifact second;
  - `grumpy-senior` often does best with the artifact alone;
  - `fact-checker` and `goal-auditor` get the repo.
- **Draw the lens boundaries.** Fill the brief's "Lens boundaries" section for every reviewer except the claim owner, which gets "You own claim checks for this panel."
- **Pass the rules CLAUDE.md doesn't carry.** Reviewers load the session's CLAUDE.md chain like any subagent, so don't paste it. Pass the rules that bear on the artifact and live elsewhere (CONTRIBUTING, linked docs, another repo's CLAUDE.md), or name the files to read.
- **Give each reviewer a report path** under the session scratchpad, e.g. `<scratchpad>/panel-<slug>/<agent>.md`. The reviewer returns one line.
- **Use read-only snapshots.** If reviewers need a branch state, point them at `git show <ref>:<path>` or a separate read-only worktree. Never have a reviewer check out branches in a tree others are reading.

## Step 5: Dispatch and wait

Work through this checklist in order. If you have a task-list tool, copy the checklist into it. Don't skip an item because the run looks fine; the one recorded real panel broke items 3 and 6.

1. Send every reviewer in **one message**, in the background.
2. If any call is refused (a hook, a permission rule, a policy, an unknown agent type), stop and follow "If dispatch is blocked". Don't retry it.
3. As each reviewer finishes, note its one-line return and nothing else. Don't Read, Grep or quote its report, and don't tell the user about it. On a single-reviewer panel, the only exception is the title-and-severity Grep in Step 6.
4. When every reviewer has returned or failed, say one line: `Reviewers done (<k>/<n>); filter running, about 10–15 min.` Name any reviewer that failed. On a single-reviewer panel, skip this line.
5. Dispatch `noise-filter` (Step 6) with the report paths. You haven't read the reports, and you don't need to. On a single-reviewer panel, skip this item; there is no filter.
6. Read the triage, and only the triage (Step 7). On a single-reviewer panel there is no triage: follow Step 6's single-reviewer line instead. If the user asks about one finding, read that finding's section, not the whole report.

## If dispatch is blocked

A PreToolUse hook, a permission rule or a host policy can refuse `Agent` calls. The block is the host's decision; never work around it. Don't retry, don't re-dispatch as `general-purpose` or any other type, don't launch reviewers through Bash, `claude -p`, a workflow or a worker, and don't quietly do the review yourself.

Stop and tell the user:

    Panel blocked: <k> of <n> reviewer dispatches were refused by <a hook | a permission rule | policy>: "<first line of the refusal>".
    Options:
    (a) Allow subagent dispatch, or run from a session that allows it, and say "retry". I'll send the same briefs.
    (b) One reviewer inline: I review it here through the <lens> lens, using agents/<lens>.md as my checklist. It uses this conversation's context and gets no independent filter.
    (c) <only if the refusal names a sanctioned route: that route, e.g. "dispatch through the fleet">
    (d) Stop here.

Then wait. For (b), pick the lens the same way as the trivial row: `goal-auditor`, `rules-lawyer` (static, no followers) for agent instructions, or `outsider` for a decision.

If only some calls were refused, let the others finish, filter what you have, and mark the missing lenses in the panel line. Don't substitute another agent for them.

## Step 6: Filter

Dispatch `review-panel:noise-filter` with the goal, the report paths and a triage path. It merges duplicates, marks cross-confirmed findings, spot-checks evidence and drops noise. Don't do this pass yourself in the main context. The reports are long, and the filter's independence is the point. Pass the report paths in any order; `noise-filter` shuffles and logs its own read order.

On a single-reviewer panel (Step 0), skip the filter. Report its one-line return, then read only its finding titles and severities (`Grep` for `^### F|^- Severity:` in the report), not the whole report.

## Step 7: Present

On a single-reviewer panel, present the titles and severities from Step 6 instead. Otherwise, read the triage and give the user:

1. The overall verdict and the panel line (`reviewer · verdict`), and the triage's `## At a glance` block.
2. **Fix before merge**, with cross-confirmed findings first. Keep each to one or two lines with its location.
3. **Decisions for you**, through AskUserQuestion when there are any, each with its options.
4. Counts for "worth doing", "pre-existing" and "dropped", plus the triage file path for detail.

Don't apply fixes, post comments or publish anything until the user says so. Any review that's going to another person stays a draft until the user approves it.

## Follow-ups

- **"Run it again after the fixes."** Re-run only the reviewers who raised BLOCKs, with the previous triage attached. Don't re-run the whole panel.
- **"Add a `<persona>`."** Dispatch that one reviewer, then re-run `noise-filter` over all reports. Follow the Step 5 checklist; don't re-read the earlier reports.

Why these rules: [references/evidence.md](references/evidence.md).
