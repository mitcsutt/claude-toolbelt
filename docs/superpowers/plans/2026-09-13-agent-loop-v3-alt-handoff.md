# agent-loop v3 — handoff from the parallel design session

**Status:** input for comparison, NOT a competing proposal.
**Written:** 2026-09-13, by session `claude-toolbelt-bd` [5414bd].
**Incumbent:** `docs/superpowers/specs/2026-09-13-agent-loop-v3-phase-runner-design.md`
(branch `agent-loop-v3`, commits `7410811`, `e18b278`, `eaf3c5e`).

## How to read this

Two sessions worked the same problem in parallel without knowing about each
other. The incumbent spec is evidence-backed (built from the 2026-09-11
`internal-app` run extract) and is the one awaiting Mitch's approval. This
document records what the *other* thread concluded and — more importantly —
**seven decisions Mitch made in that conversation that the incumbent spec was
written without.** Those decisions are the payload; the design reasoning is
context.

**Honesty bound:** the author of this document read the incumbent spec's
section headings only, not its body. Nothing here asserts what the incumbent
says. Section 4 is phrased as questions to check, not as gaps.

## 0. Operational warning (act on this first)

The extract at `~/Downloads/EXTRACT/` contains a **live impersonation JWT** at
`04-transcripts/3b42506f-….jsonl` ~seq 390, and it is also inside the SQLite
index. Rotate the credential and scrub it before the extract is copied
anywhere. Any agent querying the index should be instructed not to echo
credential material.

Separately, flagged by the run analysis and independent of either design: the
stall warnings the medic cited are a **truncate/read race** between
`lib/loop.sh:75` and `run.sh:524`. That is a live bug in 2.x, worth fixing
regardless of which v3 lands.

## 1. Where the two threads independently agree

Convergence from different starting evidence is the strongest signal in this
document. Both threads concluded:

- **One bounded child process per phase**, not Agent-tool subagents. Reasoning
  matched almost exactly: the Agent tool cannot be killed mid-dispatch, so a
  single wall-clock cap around the whole tick is the only enforcement point,
  and it lands on the pipeline instead of the phase.
- **No cache cost to splitting.** Subagents never inherit the parent's
  conversation — each dispatch is already a cold prime, documented in the
  tick prompt itself ("the plan file is re-primed into EVERY subsequent
  subagent dispatch… ~100× over a run"). The supposed warm-context tradeoff
  does not exist.
- **The harness owns the gate, the commit, and the plan**; models own
  judgement only. The spine must not be able to author code.
- **`LOOP_SCHEMA` 3**, with `run.sh` preserved as a shim so the dashboard,
  skills and `ps`-based liveness checks keep working.
- **The supervisor must outlive the supervised.** Today a rabbit-holing Worker
  kills the orchestrator that was supposed to police it.

## 2. Mitch's decisions from the parallel session (the payload)

These were made interactively and are not in the incumbent spec. Treat them as
constraints on whatever design lands, overridable only by him.

1. **Mid-run migration must work.** Offered a clean break (schema 3 for new
   runs only, refuse mid-run dirs via the existing `migration-blocked`
   incident) and he explicitly chose the harder option: a schema-2 dir stopped
   mid-task must upgrade and resume.

2. **Migration steps become instruction sets, not bash.** His clarification,
   verbatim in intent: the migration system was *always* meant as agent
   instructions, and it "turned out to look more mechanical than that." He
   authorised a pivot back. The agreed split:
   - **Framework stays mechanical** — `migrate_loop_dir` is correct as bash:
     stamping, step ordering, resuming an interrupted multi-step upgrade,
     blocking a live 1.x harness, emitting `migration` events.
   - **Steps may delegate to an agent.** `migrate_1_to_2` is mechanical only
     because it turned out trivial (it deletes a stale lock file), which is how
     the drift happened — the first real migration never demanded thought.
   - **Pattern to reuse: the medic.** Bounded budget, allowlisted actions, JSON
     verdict, escalation to `NEEDS_HUMAN.md` when evidence is ambiguous or a
     fix repeats. This is what makes decision 1 tractable: reconstructing step
     state from a plan, commit trailers and half-written runtime files is
     judgement work, and mechanical bash has no "I can't tell, ask a human"
     state that isn't just another hardcoded branch.

3. **Partial Worker output is KEPT on overrun.** A Worker that blows its cap
   with half the files written leaves its checkpoint; the retry reads it and
   finishes the remaining files. Rejected: discarding and restarting from a
   clean tree. Rationale — it honours checkpoint-write-first, which the prompt
   already mandates so that truncation never equals lost signal, and the
   parent-side gate is already the thing that catches a half-applied edit.

4. **Retry policy is judged, not laddered.** A timeout does not imply a
   reasoning gap. The spine reads the checkpoint: steady file-by-file progress
   → same tier with an extended cap; no progress or repeated self-reported dead
   ends → escalate the tier per §16. Rejected: a fixed
   extend-then-escalate-then-block ladder, and "always escalate, never extend."
   Consequence: the decision rule must be explicit in the prompt or it becomes
   a coin flip.

5. **Per-role caps live in the existing `Limits:` config line.** It already
   parses `key=value` pairs (`run.sh:96`), so this needs no new parsing model:
   `Limits: tick_timeout=… scout_timeout=… worker_timeout=… eval_timeout=…`.
   The spine keeps a `tick_timeout` as an outer backstop, raised, since it must
   now contain the longest role plus gate and commit — but it stops being the
   binding constraint. Noted and unresolved: the verification-gate commands
   want their own cap; a hung test run is currently caught only by the spine's
   cap.

6. **Cap defaults are calibrated from real data, not guessed.** He rejected
   both my proposed numbers and a local-data calibration in favour of the live
   run's evidence. Resolution agreed: ship provisional defaults **plus a
   documented calibration procedure** derived from `role_start`/`role_end`
   events, so the data is the first calibration input rather than a blocker,
   and the procedure works for anyone installing the plugin. No `TBD` in the
   spec.

7. **Concurrency and async evaluation are DEFERRED** — see §3. He asked for it
   to be recorded as a future enhancement and wants it validated against a live
   run's evidence before it is designed.

## 3. The deferred enhancement: concurrency + async evaluation

Recorded here because Mitch asked for it to be marked somewhere. **It still
needs a permanent home in whichever spec lands** — this file is gitignored.

**The idea.** Commit a task on a green deterministic gate and let the loop move
on; evaluate asynchronously, possibly in parallel with the next task's Worker.
Rationale: reviewers do not belong in the critical path — you do not block a
commit on code review, you block the merge. Segment close-out is the merge
gate.

**Why it was deferred.** Mitch raised the propagation hazard and was right. The
danger is not "evaluation is async," it is **async across a dependency edge**,
in three escalating forms: blast radius grows with lag; a later Scout reads the
unevaluated code as the reference pattern and propagates a flawed approach; and
a `BLOCKER` verdict means revert, which stops being mechanical once descendants
have landed. The `clone_of` family is the worst case — T4 builds the template,
T5–T7 copy it verbatim, so one late verdict can mean four wrong
implementations.

**The rule that would make it safe.** *A task may not start until every task it
transitively depends on has been evaluated, not merely committed.* Evaluation
becomes a dependency-graph constraint rather than a pipeline stage, and the
graph already exists in `depends_on` / `clone_of`. Deep chains correctly
re-serialize (depth is where propagation lives); wide graphs still parallelize —
the clone family becomes "evaluate T4 first, then T5–T7 concurrently," which is
exactly the edge that demanded serialization.

**Edge-case ledger.** Six mechanical, one escalates:

| # | Edge case | Resolution |
|---|---|---|
| 1 | Undeclared coupling — a Scout reads an unevaluated file with no `depends_on` edge | Discovered edges: every commit carries `Loop-Files:`; compute the file set touched by unevaluated commits and make any contract that hits it inherit an eval dependency |
| 2 | Revert conflict with an independent sibling that touched a shared file | Attempt `git revert`; on conflict → `[!]` + `LOOP_CLEANUP.md`. **The one that escalates**, correctly |
| 3 | Segment review fires over pending verdicts | Segment reviewable only when every task is committed *and* evaluated — needs a new glyph (`[v]` committed-awaiting-eval vs `[x]` accepted) |
| 4 | Follow-up insertion mutates the graph at runtime, the first time the plan changes outside a PLAN tick | Insert as eligible, rewrite dependents' `depends_on` through it. Wants its own tests; a bug here corrupts the run |
| 5 | Unbounded evaluation debt — the unevaluated file set grows until everything inherits an eval dependency and the loop silently re-serializes, many tasks deep in a possibly-wrong direction | Hard cap of N unevaluated tasks (N=2); drain at the cap. **Non-optional, and must be built in from the first commit, not retrofitted** |
| 6 | Crash with evaluations outstanding | Eval state in the per-task file; drain the queue before leasing new work. New medic row |
| 7 | `<<LOOP_DONE>>` fires with evaluations in flight, postmortem runs over ungraded work | Done requires an empty eval queue. Trivial, and trivially forgotten |

**Decision criterion Mitch set.** Gate it on evidence: `role_start`/`role_end`
plus `tick_end.dur` will say how much of a real run's wall clock sat in
serialized-but-independent work. ~10% → not worth seven edge cases. ~35%, as in
the documented clone run → worth it.

**Note for the incumbent:** this rule is the same machinery as "don't lease two
tasks that conflict." If the incumbent plans parallel execution at all, it pays
for this anyway, and the two should be one work item rather than two.

## 4. Questions to check the incumbent against

Phrased as questions because the author has not read the incumbent's body.

1. **Is a *step* the unit of retry, or still a tick?** The parallel thread's
   central claim is that a timeout must never discard completed work. Its
   answer was a durable per-task state file (`runtime/task-<ID>.json`: step,
   attempt history, artefact paths, per-attempt tier/duration/outcome), with
   the plan file remaining the graph. Rejected alternatives: one
   `runtime/state.json` for all tasks (whole-file rewrite per transition, write
   contention once concurrency lands), and deriving state by replaying the
   event log (slower, more fragile, and it puts control state into the stream
   the dashboard reads — a dashboard query should never be able to confuse the
   loop). Does §4.1 "Tick lifecycle" make a phase individually resumable, or
   does a killed phase restart the tick?

2. **Does it handle a partial Worker diff on overrun?** §6 is "Worker resume
   and split (G2)" and may already cover it; decision 3 above is the policy
   Mitch chose.

3. **Does it shrink the spine's per-tick prime?** `tick-prompt.md` is 40KB and
   primed on *every* tick, and much of it is instructions for roles the spine
   never executes (§6 Scout, §7 Worker, §10 Evaluator). Moving those to
   per-role files read only by the child that needs them is a measurable cost
   reduction independent of the timeout fix, and it makes each role contract an
   independently testable artefact.

4. **Does it fix role-event fragility?** `lib/loop.sh:106-112` infers the role
   by regex on the dispatch description (does it start with "Scout"/"Worker"/
   "Evaluat"?) and silently falls through to the raw subagent type, usually
   `"claude"`. One subprocess per phase makes role events exact rather than
   sniffed — and every cap-calibration number depends on those events being
   right.

5. **Does its migration step handle mid-run dirs, and is it an instruction set
   or bash?** Decisions 1 and 2 above.

6. **Has it avoided the plan-format ripple?** While async evaluation is
   deferred, evaluation stays synchronous, so **no plan-format change is
   needed** — no new glyph, no `total_tasks`/`done_tasks` regex changes, no ETA
   or dashboard progress math. That entire ripple was async eval's cost. If the
   incumbent changes the plan format for another reason, that is a real cost
   worth re-examining.

## 5. Where the incumbent is ahead

Stated plainly so the comparison is fair. From its headings and the analysis
summary, the incumbent has material the parallel thread never considered:

- **Python stdlib runner** rather than bash. Better call — macOS ships bash
  3.2 (no `mapfile`, no `declare -A`), and a per-task state machine with
  per-phase subprocesses is exactly where that bites.
- **Judge phase + `Decision policy: autonomous|conservative` +
  `LOOP_DECISIONS.md`** (§7). The parallel thread had no answer for the finding
  that all three `LOOP_CLEANUP` deferrals were decidable from disk.
- **Render gate and fidelity diff** (§5.3, §5.4), against the finding that no
  tick ever rendered the app before a human did and gates were only
  lint/tsc/jsdom/grep.
- **Guaranteed reads and views** (§5.2), against the finding that the "copy
  Rise layouts" task passed by adding a `TODO(copied from)` comment while both
  Evaluators — one of them opus — read zero reference files.
- **Learner phase and learnings-with-evidence** (§9).
- **§11 "Decisions made without Mitch"** as an explicit override surface. Good
  practice; decisions 1–7 above should be merged into it.

## 6. The premise correction

For the record, because it undercuts part of the parallel thread's reasoning
and the other agent should not over-weight it:

That thread diagnosed the problem as **phase starvation** — the Evaluator runs
last and gets whatever minutes remain. The run evidence says otherwise:
timeouts were **one extra iteration inside a 30-minute tick already running at
85–94% of the wall clock.** The tick is chronically at its ceiling. Starvation
is real and per-phase caps still fix it, but it is a symptom; the disease is
that the tick has no headroom at all. Cap sizing reasoned from the starvation
model — including the provisional 600/2700/900/1800/1800 figures — should be
re-derived from the actual distribution, which is decision 6's calibration
procedure.

The incumbent's diagnosis is the better-grounded one. Where the two conflict,
prefer the evidence.
