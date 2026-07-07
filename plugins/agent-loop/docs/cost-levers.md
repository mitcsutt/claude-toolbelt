# Cost levers — running loops cheaply

Durable guidance distilled from the live-loop cost analysis. The aim: spend the thinking
budget where it changes the outcome, and nowhere else. All the tuning lives in the
**model-tier fields of `LOOP_CONFIG.md`** (`Orchestrator model:` plus the per-role
`Planner tier:` / `Scout tier:` / `Worker tier:` / `Evaluator tier:` lines) — this doc is
how to set them.

## The finding

In the observed run, the **orchestrator spine ran on Opus on every tick** and accounted for
**~90% of total spend**. The spine only coordinates: it reads state, dispatches role
subagents, runs the verification pipeline, and records the outcome. None of that is
reasoning-heavy — the heavy thinking is already delegated to the Planner and Evaluator
subagents. Running the spine on a top-tier model was paying premium rates for clerical work
on *every* tick.

**Biggest lever, already pulled:** pin the spine to a standard model. The template now
defaults `Orchestrator model: sonnet` (`templates/LOOP_CONFIG.md`), so new loops are cheap
out of the box. Blank it to inherit your Claude default; name any alias to override. This
one change is most of the savings; everything below is incremental on top of it.

## Levers, ranked

1. **Standard orchestrator spine — DONE, template default.**
   *Mechanism:* the per-tick `claude` process runs at standard tier; heavy reasoning is
   delegated to subagents. *Field:* `Orchestrator model:` (`LOOP_CONFIG.md`).
   *Saving:* the headline ~90%-of-spend lever. Largest single win.

2. **Planner → standard for segment-internal plan ticks; most-capable for cross-segment
   design.** *Mechanism:* decomposing one already-decided segment into dependency-ordered
   tasks is mostly mechanical; the high-leverage reasoning is the cross-segment / spec-level
   architecture. Reserve most-capable for the latter. *Field:* `Planner tier:`.
   *Saving:* moderate — plan ticks are infrequent but expensive at top tier. A weak plan
   poisons downstream ticks, so do not undertier the architecture-shaping plan.

3. **Evaluator → standard on `mechanical`-tagged tasks; most-capable on TDD/logic.**
   *Mechanism:* a `mechanical` task's success is fully deterministic (grep + the
   verification pipeline is the sole gate), so review is overhead — it already skips the
   Evaluator per `tick-prompt.md` §10. Where an Evaluator *does* run, reserve the
   most-capable tier for the judgement cases (TDD, logic, workaround-spotting); the
   fake-test catch that justified Opus was on a TDD task, not a mechanical one.
   *Field:* `Evaluator tier:`. *Saving:* moderate, proportional to mechanical-task share.

4. **Re-dispatch to a Worker on truncation — never let the spine finish files.**
   *Mechanism:* if a Worker returns truncated/partial output, dispatch a *fresh Worker* to
   finish (reading the checkpoint), rather than having the orchestrator author the
   remaining files itself. This is enforced by `tick-prompt.md` §7 (the truncation rule).
   *Saving:* prevents the single biggest blowup observed — a ~$7.45 tick where an expensive
   Opus spine finished a cheaper Worker's files. Authoring belongs at the Worker tier.

5. **Reuse one Scout brief per segment instead of per task.**
   *Mechanism:* scouting is reconnaissance; when several tasks in a segment share context,
   one brief amortises across them rather than re-running recon per task. *Field:*
   `Scout tier:` (already `cheap` by default). *Saving:* small but free — fewer cheap-tier
   round-trips and less repeated context.

## When to spend Opus — rule of thumb

Reserve the most-capable tier for work where a wrong call is expensive and the judgement is
genuinely hard:

- **Yes (most-capable):** TDD tasks, non-trivial logic, architecture / cross-segment plan
  design, and workaround-spotting in review. A weak model here ships hidden defects or a
  poisoned plan.
- **No (standard / cheap):** mechanical edits, renames, import-path rewrites, config
  changes, formatting, and the coordination spine itself. Determinism is checkable by grep
  and the pipeline; a top-tier model adds cost, not safety.

If you cannot name a concrete way a cheaper tier would get it *wrong*, use the cheaper tier.

## Where to set this

All of it is config, no code changes — edit the model-tier block in your loop's
`LOOP_CONFIG.md`:

- `Orchestrator model:` — spine model alias (template default `sonnet`; blank = your Claude
  default).
- `Planner tier:` / `Scout tier:` / `Worker tier:` / `Evaluator tier:` — per-role tiers
  (`cheap` | `standard` | `most-capable`; blank/auto lets the role pick per task via
  `tick-prompt.md` §16). The harness injects the resolved tiers into the tick prompt as an
  authoritative block, so they bind regardless of how diligently the spine reads config.
