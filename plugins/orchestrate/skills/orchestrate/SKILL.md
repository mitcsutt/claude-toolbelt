---
name: orchestrate
description: Use when the user asks you to act as an orchestrator, run an expensive or frontier model while minimising token usage, "manage a team of agents", "delegate this efficiently", "fan this out cheaply", "you're expensive, delegate", or when a token-heavy task has independent slices worth spreading across cheaper subagents.
---

# Orchestrate

You are running as an expensive top-level model. Your tokens are the costly resource. Spend them on judgment; push token-heavy bounded work to cheaper subagents. Success = thorough result at low orchestrator-token cost.

**Core principle:** the expensive model owns the *decision layer* — decompose, weigh tradeoffs, reconcile conflicting reports, integrate, final-review. Everything token-heavy and boundable — repo greps, log/test-output reduction, file inventories, mechanical edits, doc scans, narrow slices — goes to a cheaper worker. Worker context windows are separate from yours; that isolation is itself the saving.

## Step 0 — Decide whether to orchestrate at all

Orchestration trades coordination overhead for parallelism. It only pays when the work genuinely splits — you win by spending *more* tokens in parallel, so only do it on work that's both token-heavy and parallelisable.

**Orchestrate when:** the task has independent slices, large search/scan surface, long logs, or repetitive edits — and the slices don't depend on each other's output mid-flight.

**Stay single-threaded when:** the work is small, tightly coupled, needs shared evolving context, or is judgment-delicate throughout. Most bug fixes and small edits are here. Delegating them costs more (coordination + handoff) than it saves. Say so in one line and just do the work.

## Route to the cheapest capable tier

You already know your own ecosystem — which models, subagents, and skills you can dispatch, and how to dispatch them. Use that knowledge; this skill deliberately does **not** name specific models or tools, because the roster changes. The durable rule is tier-by-task-character:

- **Cheapest capable tier** — token-heavy, boundable, low-judgment work: greps, inventories, doc scans, log/test-output reduction, mechanical repetitive edits, summarising raw output.
- **A mid capable tier** — bounded reasoning slices: focused implementation, narrow debugging, writing tests.
- **Your own (expensive) tier** — the decision layer only: decomposition, tradeoffs, reconciling conflicting reports, integration, final review. Rarely a delegate target.

Match the tier to the task's **judgment demand, not its size** — a large mechanical job is still cheap-tier; a small delicate call stays with you. If your ecosystem lets you pick the tier per dispatch, do so; if workers are pre-defined at fixed tiers, pick the worker whose tier fits. The dispatch and parallelism mechanics are whatever your harness provides — use them.

**Stay the hub.** Unless your ecosystem explicitly supports nested orchestration (a worker fanning out its own team), you remain the single orchestrator — don't delegate the orchestration itself.

## Handoff packet (every delegated prompt)

Write each worker prompt as if the worker has **no useful chat context** — it inherits none of this conversation. A vague task produces duplicated or wrong work. Every packet includes:

1. **Objective** — one concrete goal.
2. **Scope** — in-scope files/surfaces AND explicit out-of-scope ("do not touch X").
3. **Evidence format** — exactly what to return (see below).
4. **Verification** — the command(s) that prove the work, and success criteria.
5. **Stop conditions** — when to halt and report rather than push on.

## Compact returns — with traceable references

Workers return **only** what you need to decide *and to re-trace*: their conclusion, plus file paths with line references, the exact commands run, URLs / PR / ticket links, and doc locations that back each finding — along with failures and open uncertainties. **No raw logs, no prose narration, no re-pasted file bodies** — reduction is the worker's job.

This is a balance, not "return nothing": the worker's resettable context is the token win, so you don't want it all back — but you must not lose **provenance**. Every non-trivial finding carries a pointer back to its source, so you (or a later worker) can verify or follow up **without re-running the original work**. A finding with no source reference is a lead you cannot check — send it back for the anchor.

## Hard stop conditions (put these in every packet)

A worker must stop and report — not improvise — when:

- Live code contradicts an assumption the packet was built on.
- A verification command fails twice after a reasonable retry.
- The work needs files outside its declared scope.
- It cannot produce concrete evidence for a claim.

## Parallel width — by independence and cost, not a fixed number

Parallelise freely when slices are genuinely independent and cheap. Don't cap at an arbitrary count. Two real constraints set the width:

1. **Cost** — many concurrent workers at your most expensive tier burn budget fast. Widen at cheap tiers; be sparing at expensive ones.
2. **Independence** — never parallelise workers whose findings depend on each other. They duplicate work, miss each other's context, or conflict. Coupled work is serial (or one worker); only genuinely independent slices go wide.

When unsure whether two slices are independent, assume they're coupled and serialise — a wrong parallel split costs more than a wrong serial one. Batch into waves when integrating between rounds helps; wave size is whatever independence + budget allow.

**Single writer per file** — never let two workers edit the same file concurrently, regardless of how they coordinate. Partition surfaces up front.

## Coordination depends on your ecosystem

How workers share state is ecosystem-specific — don't assume either way. If you have a shared channel or agent-team message bus (some harnesses provide one), use it for live coordination. If you don't, workers coordinate only through you and through shared files — then you must pass any cross-worker context yourself. Check what your environment actually offers before designing the coordination.

**An idle signal is not a delivered result.** Under an async teammate/bus model, a worker going idle does not mean its report reached you. Two rules that prevent the most common stall: (1) instruct each worker to deliver its final report *explicitly* (via the send-message primitive), not by simply going quiet; (2) when a worker first signals idle, proactively pull its output rather than waiting for a push. Where dispatch is async, sequential dispatches still run concurrently — don't assume you failed to parallelise just because you didn't batch them into one message.

## Don't block on the slowest worker

Integrate completed workers as they return. Do **not** gate the deliverable on the slowest worker still running when the finished workers already answer the core question — present the interim findings, name what's still pending, and let the user decide whether to wait. For any worker whose single step may run long (minutes), require an interim ETA update in its packet so you can choose to proceed rather than long-poll. A session that ends still holding all its findings unsynthesised has wasted the work.

## Vet, don't trust

Worker output is a **lead, not a fact.** Before any high-impact action (writing code, creating a PR, a user-facing conclusion): follow the returned references — reopen the cited files, confirm the line numbers, review the final diff yourself. When multiple workers return competing answers, judge them — pick the correct one, or the cheaper path when quality is equal — rather than averaging or trusting the last.

## Verification gate (non-negotiable, per the user's rules)

Before claiming done, report checkable scope + evidence — never assert completion without it. Emit:

- **Workers dispatched:** count, tiers used, what each covered.
- **What functions now:** one concrete verified statement + the command output proving it.
- **Unverified:** anything not confirmed, labelled — do not omit or paper over.

## Common mistakes

| Mistake | Fix |
| --- | --- |
| Orchestrating small/coupled work | Step 0 — most fixes stay single-threaded. |
| Token-heavy scan/reduction on your own expensive tier | Route it to the cheapest capable tier. |
| Vague handoff → duplicated/wrong work | Full packet: objective, scope, evidence, verification, stop conditions. |
| Worker returns raw logs | Mandate compact returns; reduction is the worker's job. |
| Finding with no source pointer | Send it back — you can't verify a lead you can't trace. |
| Trusting worker claims | Follow the references: reopen cited files, review diffs, before acting. |
| Wide parallelism at the expensive tier, or across coupled work | Widen only on independent, cheap slices; serialise coupled work. |
| Two workers editing the same file | Partition surfaces; single writer per file. |
| Assuming there's no shared bus (or that there is one) | Check your ecosystem's coordination model first. |
| Treating a worker's idle signal as its result | Require explicit report delivery; pull the worker's output on first idle. |
| Blocking the deliverable on the slowest worker | Synthesize on partial — integrate finished workers, present interim findings, note what's pending. |
| Delegating the orchestration | You stay the hub unless nested orchestration is explicitly supported. |

## Quick reference

| Task character | Tier | Notes |
| --- | --- | --- |
| Grep / scan / inventory / summarise output | cheapest capable | high volume, low judgment |
| Bounded code slice / narrow debug / tests | mid capable | one clear objective each |
| Decompose / reconcile / integrate / review | you (expensive) | keep local |
| Known-shape deterministic pipeline | mixed | use your harness's scripted-orchestration mechanism, if any |
