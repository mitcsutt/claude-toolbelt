---
name: agent-loop-postmortem
description: This skill should be used to close out an autonomous coding loop and produce a retrospective. Aggregates loop artefacts (LOOP_CONFIG.md, LOOP_PLAN.md, LOOP_USAGE.jsonl, LOOP_CLEANUP.md, Loop-Status commit trailers) into a dense structured context, then delegates to /postmortem to write the document. Hard dependency on /postmortem. Triggers when all LOOP_PLAN tasks are [x]/[-], after a halt-class blocker, or when the user says "loop postmortem", "wrap up the loop", "close the loop", "finish the loop", "loop retrospective", "/agent-loop-postmortem".
---

# Agent Loop Postmortem

Thin wrapper around `/postmortem`. Aggregates loop artefacts → injects as dense context → `/postmortem` writes the retrospective.

## When to invoke

- Invoked by the bash harness `run.sh` once the loop reaches `<<LOOP_DONE>>` (the harness calls `/agent-loop-postmortem` after the final tick)
- Manually by the user to close out a loop early
- After a halt-class blocker, to capture state before resume/abandon

## Preconditions

All loop artefacts live under the per-run base dir `$LOOP_DIR` (`.claude/loop/<run-id>/`), exported by the harness; durable files sit directly under it, ephemeral runtime files under `$LOOP_DIR/runtime/`. Read `LOOP_DIR` from your environment; every artefact path below is relative to it. If `LOOP_DIR` is unset (e.g. a manual invocation), locate the most recent `.claude/loop/<run-id>/` dir containing a `LOOP_CONFIG.md`.

1. `$LOOP_DIR/LOOP_CONFIG.md` exists.
2. `$LOOP_DIR/LOOP_USAGE.jsonl` exists (may be empty if the harness crashed before any tick produced parseable JSON).
3. `/postmortem` skill resolvable (hard dependency — declared in `agent-loop` plugin).

If `/postmortem` is unresolvable: halt and surface "install the postmortem plugin" — there is no useful fallback for the wrapper without the underlying skill.

## Edge cases

- **Empty `$LOOP_DIR/LOOP_USAGE.jsonl`** — valid. Zero cost/tokens everywhere. Do NOT fabricate data. The postmortem will be skinny — that's correct.
- **Malformed JSONL lines** — skip the line, count it under a synthetic `parse-errors: N` field in the structured context. Don't halt.
- **No loop branch / detached HEAD** — `git log --grep="Loop-Status"` returns empty. Note "no Loop-Status commits found" in the context block; continue.
- **`$LOOP_DIR/LOOP_CLEANUP.md` missing** (not just empty) — note in context as "no cleanup file"; continue.
- **Single-granularity loop** — `Granularity: single`, one segment. Skip the per-segment breakdown (there is nothing to split); report the flat task counts.
- **Missing `Started:` timestamp in `$LOOP_DIR/LOOP_CONFIG.md`** — set `Elapsed: unknown` rather than crashing. Surface in postmortem.
- **Postmortem file already exists** at `$LOOP_DIR/POSTMORTEM.md` — ask the user whether to overwrite or write `-v2`. Never silently overwrite — prior analysis matters.

## Process

### Step 1: Aggregate loop artefacts

Read all of:

- **`$LOOP_DIR/LOOP_CONFIG.md`** — goal, loop type, granularity (`single` / `segmented`), segment count, TDD mode, verification pipeline, limits (per-tick `tick_timeout`), branch, worktree, start timestamp
- **`$LOOP_DIR/LOOP_PLAN.md`** — final state of every task with its marker (`[ ]` pending · `[~]` in-progress · `[x]` done(+SHA) · `[!]` blocked · `[-]` skipped · `[blocked-upstream]`), grouped under `## Segment <id>` headers
- **`$LOOP_DIR/LOOP_USAGE.jsonl`** — the harness's per-tick usage ledger, one JSON object per line. Parse line-by-line. Each record is:

  ```json
  {"tick":1,"mode":"plan|execute|review","cost_usd":0.42,"input_tokens":49,"output_tokens":12585,"duration_s":87,
   "by_model":{"<model-id>":{"cost_usd":..,"input_tokens":..,"output_tokens":..,"cache_read_tokens":..,"cache_creation_tokens":..}}}
  ```

  **CRITICAL:** the top-level `input_tokens`/`output_tokens` are the **orchestrator (parent) process only** — every subagent's usage (Planner, Scout, Worker, Evaluator) lives exclusively in `by_model`. Summing the top-level fields undercounts the run by ~350× and reports orchestrator-only output. The true billed surface is the sum across `by_model.*` of `input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens`. `cost_usd` (top-level) IS the full-tick cost and may be summed for the cost figure.

  This is the source of truth for tokens and timing — task *outcomes* live in the commit trailers, not here. `cost_usd` is a raw API-list-price estimate recorded per tick (it is not summed by the harness and does not map to subscription billing). Empty if the loop crashed before any tick produced parseable JSON (expected for an early halt).
- **`$LOOP_DIR/LOOP_CLEANUP.md`** — the human-decision queue: each `[!]` task's reason and the specific decision a person must make to unblock it, plus any permission/scope gaps left unresolved
- **`$LOOP_DIR/LOOP_LOG.jsonl`** (supplementary) — the tick event log, one JSON event per line (tick-start, blocked, recovery, etc.). Commit trailers are the primary outcome record; skim this only for events that left no commit (e.g. a halted tick, a stale-lock recovery). Empty/absent is fine.
- **`git log --grep="Loop-Status"` for the loop branch** — full structured commit history. Parse trailers:

```
Loop-Status: done | skipped | halted | reviewed
Loop-Verification: lint=pass tsc=pass build=pass test=pass
Loop-Files: path/a, path/b, ...
```

### Step 2: Derive summary metrics

Compute:

**Task outcomes (from `$LOOP_DIR/LOOP_PLAN.md`):**

- **Total tasks**, **Done** (`[x]`), **Skipped** (`[-]`), **Blocked** (`[!]`), **Blocked-upstream** (`[blocked-upstream]`), **Pending** (`[ ]` — non-zero only if the loop halted before completion)

**Usage rollup (from `$LOOP_DIR/LOOP_USAGE.jsonl`):**

- **Total ticks** (line count), and the split of **plan ticks** vs **execute ticks** vs **review ticks** (the `mode` field)
- **Evaluator invocation count** — count `role_start` events with `role: "Evaluator"` in `$LOOP_DIR/events.jsonl`, split into per-task vs per-segment, and the per-model tier each ran on. Report this **separately from the review-tick count**: the per-task Evaluator runs *inside* execute ticks, so the review-tick count (the `mode:"review"` ledger rows) badly understates real judgement cost. In the source run there were 30 Evaluator dispatches across only 10 review ticks. Flag if every Evaluator ran on the most-capable tier (a sign the flat `Evaluator tier` config defeated the per-class split — see Plan 2).
- **Total billed tokens** = sum across every record's `by_model.*` of `(input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens)`. This is the meaningful usage figure. **Report the cache-read share** (`sum(cache_read_tokens) / total billed`) explicitly — in the source run it was 93%, i.e. cost is dominated by re-reading fixed context, not by output. **Canonicalize model ids before grouping**: strip the `us.anthropic.`/`anthropic.` region prefix, the `[1m]` window suffix, and the `-v1:0` version suffix, or the same model double-lists (e.g. `claude-sonnet-4-6` vs `us.anthropic.claude-sonnet-4-6`).
- **Per-model cost/token table** — one row per canonical model: cost_usd, the four token fields, and % of total cost. (Source run: sonnet ~$89 / 66%, opus ~$41 / 31%, haiku ~$4 / 3%.)
- **Total cost** = sum of top-level `cost_usd` (already full-tick; informational, not subscription spend).
- **Tokens per task** = total billed ÷ Done count (the unit economics of the run)
- **Slowest ticks** — top 3 by `duration_s`, each with its `tick`, `mode`, and token count
- **Time elapsed** = (last commit timestamp) − (`Started:` timestamp from `$LOOP_DIR/LOOP_CONFIG.md`)

**Completion records (from `Loop-Status` commit trailers):**

- **Loop-Status distribution** — counts of `done` / `skipped` / `halted` / `reviewed`
- **Verification pass rate** — across `Loop-Verification` trailers, how many ticks had every stage `pass`
- Reconcile against the plan markers; note any divergence (e.g. a `[x]` task with no matching `Loop-Status: done` commit). Note: `reviewed` commits are per-segment closeout reviews, not task completions — they do not correspond to any single task marker, so exclude them from the per-task reconciliation.

**Segments (only when `Granularity: segmented`):**

- Per-segment done/blocked breakdown — for each `## Segment <id>` header in `$LOOP_DIR/LOOP_PLAN.md`, the count of `[x]` / `[-]` / `[!]` / `[blocked-upstream]` / `[ ]` tasks beneath it. Flag any segment left wholly unplanned (a PLAN-tick placeholder the loop never reached).

**Blocked work (the human-decision queue):**

- For each `[!]` and `[blocked-upstream]` task: task ID, segment, and the decision a person must make (from `$LOOP_DIR/LOOP_CLEANUP.md`)

### Step 3: Build the structured context block

Format as a single markdown block that `/postmortem` will receive as input:

```markdown
## Loop Postmortem Input — <topic>

### Config
- **Started:** <ISO>
- **Ended:** <ISO>
- **Elapsed:** <human-readable>
- **Goal:** <goal from CONFIG>
- **Loop type:** <type>
- **Granularity:** single | segmented (<N> segments)
- **TDD mode:** <mode>
- **Verification pipeline:** <list>
- **Branch:** <branch>
- **Worktree:** <absolute path>

### Outcomes
- Total tasks: <N>
- Done (`[x]`): <N>
- Skipped (`[-]`): <N>
- Blocked (`[!]`): <N>
- Blocked-upstream (`[blocked-upstream]`): <N>
- Pending (`[ ]`): <N>
- Loop-Status trailers: done <N> · skipped <N> · halted <N> · reviewed <N>
- Verification pass rate: <N> of <K> ticks all-pass

### Usage (from LOOP_USAGE.jsonl)
- Ticks: <N> total (<P> plan · <E> execute · <R> review)
- Evaluator dispatches: <N> total (<per-task> per-task · <per-segment> per-segment), tiers: <model:count …>
- Total cost: $<X> · Total tokens: <in>+<out>
- Cost per task: $<X> (total ÷ done)
- Slowest ticks: tick <n> (<mode>, <s>s, $<X>) · tick <n> (…) · tick <n> (…)

### Segment breakdown (omit when Granularity: single)
For each `## Segment <id>` — done / skipped / blocked / blocked-upstream / pending counts. Flag any unplanned segment the loop never reached.

### Blocked work — human-decision queue
For each `[!]` / `[blocked-upstream]` task — task ID, segment, and the specific decision a person must make (verbatim from `LOOP_CLEANUP.md`).

### Cleanup follow-ups (from LOOP_CLEANUP.md)
- [ ] <verbatim from file, one per line>

### Sample commits
- Last 5 entries from `git log --grep="Loop-Status: done"` (sha + subject + Loop-Verification trailer).
```

### Step 4: Promote durable learnings into persistent cross-run knowledge (AUTOMATIC)

This step runs **automatically with no human approval** — loops run unattended overnight, so there is no gate. It is the close-time counterpart to the per-task read the Scout does at runtime: it harvests the run's `$LOOP_DIR/LOOP_LEARNINGS.md` so durable knowledge survives the run instead of dying with it.

Read this run's `$LOOP_DIR/LOOP_LEARNINGS.md` `## Patterns` digest. For **each** entry, classify it durable-vs-slice-specific using the classifier below, then **merge the durable (generalized, deduped) ones** into the persistent knowledge file at `$LOOP_KNOWLEDGE` (default `.claude/loop/KNOWLEDGE.md` — repo-scoped, committed, the SIBLING of the per-run `$LOOP_DIR`, not inside it). **Slice-specific entries are dropped** — they die with the run and are never written to `$LOOP_KNOWLEDGE`. The run's own `$LOOP_DIR/LOOP_LEARNINGS.md` is left unchanged.

```
PROMOTE (durable) — true independent of the feature slice:
  • build/test/lint/dev commands, env/host/port quirks
  • monorepo conventions (e.g. "don't modify apps/frontend")
  • cross-cutting test-infra gotchas (e.g. "ui-web Button v2 needs createLink in the router mock")
  • reusable wiring mechanisms — but GENERALIZE the wording (strip the specific feature noun)
  • tooling / version facts

DROP (slice-specific) — names a feature/entity/field that won't recur:
  • "billing form submit already wired"
  • "legacy parity source = …/frontend billing"
  • specific field names / formulas / component states

SIGNAL: domain noun (billing, contact, this form)? → slice → DROP.
        repo/tooling/test-infra/convention?        → durable → PROMOTE.
        borderline (slice-derived but reusable)     → PROMOTE, generalized.

DEDUP-ON-PROMOTE: compare against existing KNOWLEDGE.md entries; merge near-duplicates, never append restatements.
```

**Idempotency.** Re-running the postmortem must NOT duplicate entries. Before appending any promoted entry, compare it against the existing `## Patterns` entries in `$LOOP_KNOWLEDGE` (same rule, different wording counts as a duplicate); merge rather than append. A second close on the same run is therefore a no-op against `$LOOP_KNOWLEDGE`.

**Mechanics.** If `$LOOP_KNOWLEDGE` does not exist, create it with a `# Loop Knowledge` header and a `## Patterns (durable, cross-run; auto-promoted at loop close)` section, then append under that section (the harness `run.sh` normally seeds it on first run, so this is belt-and-braces). Append promoted entries (verbatim where durable, generalized where borderline) under the `## Patterns` section. If any entries were promoted, stage and commit only this file:

```bash
git add "$LOOP_KNOWLEDGE"   # resolves to .claude/loop/KNOWLEDGE.md — sibling of $LOOP_DIR, NOT under the gitignored runtime/
git commit -m "loop: promote durable learnings into KNOWLEDGE.md"
```

If nothing was promoted (every `## Patterns` entry was slice-specific, or all were already present), make no commit. This step never touches `$LOOP_DIR/LOOP_LEARNINGS.md` and never blocks on a human.

**Invariants promote too.** In addition to `## Patterns`, read this run's `## Invariants` section. A durable invariant (a machine-checkable rule with a `check` that generalises across the repo, not a slice-specific one) is promoted into `$LOOP_KNOWLEDGE`'s `## Invariants` section verbatim (rule + check), deduped against existing entries. This is how a convention learned the hard way in one run (e.g. PascalCase `describe`) becomes a gate-enforced invariant on every future run, instead of re-teaching the digest each time.

### Step 5: Invoke `/postmortem` with the structured context

Pass the block above as the input. Set the topic to `<branch-name>` (which is `agent-loop-<topic>`).

`/postmortem` will:
- See the input is dense (no thin-context branch)
- Skip its "ask follow-up questions" phase (the structured input is sufficient)
- Write the retrospective to **`$LOOP_DIR/POSTMORTEM.md`** (and any companion HTML alongside it in `$LOOP_DIR/`). Pass this explicit path to `/postmortem` as its output target. Keeping it inside `$LOOP_DIR` (whose log artefacts are now gitignored — see setup) means the postmortem is local provenance, not PR noise on the feature branch.
- Commit the file

### Step 6: Surface the limitation caveat

In your final message to the user, include this caveat (it's IMPORTANT and easy to forget):

> **What was NOT verified:** Each tick's verification pipeline graded structural correctness against the diff (lint, tsc, build, existing tests — recorded in the `Loop-Verification` trailers). It did NOT verify behavioural correctness of the running application. A subtle visual regression or runtime behaviour change outside the verified surface could exist. Spot-check before merging.

This text MUST appear in the postmortem's `Limitations` section. If `/postmortem` doesn't include it by default, append it manually after the file is written.

### Step 7: Final user message

```
Loop closed.

Outcome:    <N done> / <K total> tasks  (<B blocked>)
Cost:       $<total> · <N> ticks · $<per-task>/task
Elapsed:    <human-readable>
Postmortem: $LOOP_DIR/POSTMORTEM.md

Manual cleanup: <N items> in LOOP_CLEANUP.md (human-decision queue)
Next: review the postmortem, then merge / squash / push.
```

## Common rationalisations to refuse

| Thought | Reality |
|---------|---------|
| "Skip /postmortem, just emit summary inline" | The file IS the deliverable. /postmortem writes it. Don't inline. |
| "Loop was short — skip the structured aggregation" | Run the aggregation. Even short loops surface signal. |
| "Blocked tasks were minor — skip the human-decision queue" | List them. The `[!]` / `[blocked-upstream]` queue is the whole point — it's what a human acts on next. |
| "Cost rollup is noise — skip LOOP_USAGE.jsonl" | Report it. Cost-per-task and the slowest ticks are the loop's unit economics; future runs are tuned from them. |
| "Cleanup file is empty, omit the section" | Show empty section (`_No items._`). Postmortem contract requires all sections. |
| "Skip the limitation caveat — user knows" | Always surface. The caveat is precisely the thing humans forget. |
| "Resume the loop instead of running postmortem" | If user asked for postmortem, run it. Resume is re-running `run.sh` — separate action. |
| "Postmortem file already exists — silently overwrite, my new one is better" | Ask the user. Overwriting silently loses prior analysis. Suggest `-v2` suffix. |

## What this skill does NOT do

- Does NOT merge the loop branch.
- Does NOT push to remote. (The Step 4 KNOWLEDGE.md promotion commits locally on the loop branch only; cross-run persistence is realised once that branch merges to master — see the README's known limitation.)
- Does NOT delete `$LOOP_DIR/runtime/` runtime artefacts (the user may want them for forensics).
- Does NOT auto-clean `@deprecated` files (those are in `LOOP_CLEANUP.md` for human action).

The boundary is clear: this skill captures the retrospective. Branch management is the user's call.
