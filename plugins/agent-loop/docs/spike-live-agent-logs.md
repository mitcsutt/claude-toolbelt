# Spike: live per-role log streaming in the dashboard

**Request (dashboard item #5):** click a role tile — orchestrator / Planner / Scout /
Worker / Evaluator — and tail that role's live activity, the way you watch a Claude Code
session scroll. This is a research spike: findings + a verdict + a recommended next step.
No implementation.

## Current state — what we capture today

The harness runs one headless `claude` process per tick. Its stream-json output is the
**top-level orchestrator stream** only:

- `run.sh` pipes that stream into `format_stream` (`lib/loop.sh:56`), which tees every
  raw line to `run.log` and folds it into coarse events on `events.jsonl`.
- The orchestrator *dispatches* roles via the Agent tool. In its stream those dispatches
  appear only as `tool_use` blocks (name `Agent`/`Task`, with `subagent_type`, `model`,
  `description`). `format_stream` turns each into one `tool` event plus role
  `role_start`/`role_end`/`handoff` transitions keyed on the role label parsed from the
  description (`lib/loop.sh:72-106`).
- So a subagent collapses to **count events**: "Worker started, did N tools, ended." The
  Worker's own narration — the file reads, the edits, the reasoning text, the test output
  it saw — never appears. It lives inside that subagent process and is summarised back to
  the orchestrator as a single tool result.

In short: `run.log` = the orchestrator's monologue. There is **no per-subagent stdout** on
disk anywhere, and the dashboard's `/events` SSE channel (`web/serve.py:728`) only replays
the derived snapshot, which is built from those count events.

## The gap — the key unknown

The feature needs each role's *sub-stream*, not a tool count. The blocking question is:

> **Does the Agent/Task tool surface a child agent's stream-json sub-stream to the
> parent process at all?**

From the parent's stream-json, a dispatched agent is opaque: we see the `tool_use` that
launches it and the `tool_result` that comes back, and nothing in between. There is no
evidence today that the nested agent's per-step events are exposed to the orchestrator
process or written anywhere the harness can tail. **This is the likely blocker.** If the
sub-stream is not observable, no amount of dashboard work produces a live Worker tail —
there is nothing to tail.

(Two lesser unknowns ride on the first: even if a sub-stream exists, is it *interleaved*
into the parent stream-json, or emitted on a side channel? And is it real-time or only
flushed at the child's completion? A completion-only flush kills the "watch it live"
value even if the data exists.)

## Proposed approach — *if* the sub-stream turns out to be observable

The plumbing is straightforward and mirrors what already works for events:

1. **Per-role logfiles.** Extend `format_stream` so that, while a role span is active
   (`active_role` is set, `lib/loop.sh:91`), it also appends that role's sub-stream lines
   to `runtime/log-<role>.jsonl` (e.g. `runtime/log-Worker.jsonl`). The orchestrator's own
   lines go to `runtime/log-orchestrator.jsonl`. These are append-only and per-run, under
   the already-gitignored `runtime/`.
2. **New SSE endpoint.** Add `/events/<role>` to the `do_GET` dispatch in `web/serve.py`
   (alongside `/events`, `web/serve.py:728`). It tails `runtime/log-<role>.jsonl` from the
   client's last offset — reuse the `tail_events` offset machinery (`web/serve.py:307`) —
   and pushes new lines as `text/event-stream`.
3. **Click-to-tail panel.** In `dashboard.html`, make each role node in the pipeline tree
   clickable; clicking opens a panel that subscribes to `/events/<role>` and renders the
   incoming lines as a scrolling Claude-Code-style transcript.

Data flow if feasible:

```
claude (orchestrator stream-json)
  └─ Agent → Worker sub-stream  ──►  format_stream  ──►  runtime/log-Worker.jsonl
                                                              │  (tail by offset)
   dashboard click "Worker"  ──►  GET /events/Worker (SSE)  ──┘
```

Everything downstream of step 1 is mechanical and low-risk. **Step 1 is the entire bet.**

## Verdict

**Blocked on SDK/tool support — pending a one-shot observability check.**

The dashboard half (per-role files + `/events/<role>` + a tail panel) is feasible now and
small. But it is worthless without subagent sub-streams, and as of this spike there is no
evidence the Agent/Task tool exposes a child agent's stream to the parent in real time. The
honest classification is **blocked on SDK support** until that single fact is established;
if the check succeeds it drops to **feasible-now**.

Do **not** build the dashboard plumbing speculatively. The role-event contract already
shipping in this work (role tiles, model, active/done — items #1/#3) gives most of the
"which role is running now" legibility at near-zero cost; live tailing is the expensive
increment that hinges on the unknown.

## Recommended next step

Run a minimal experiment before any feature work: invoke a headless `claude --print
--output-format stream-json --verbose` tick that dispatches **one** trivial Agent subagent,
capture the full stream to a file, and grep it for the child's interior steps (its own
`tool_use`/`text` lines, not just the launching `Agent` tool_use and its `tool_result`).

- If the child's steps appear in the parent stream (interleaved, real-time) → unblocked,
  build the approach above; the only design choice left is attributing interleaved lines to
  the right `runtime/log-<role>.jsonl`.
- If only the `Agent` tool_use + final `tool_result` appear → confirmed blocked; record
  it here and revisit only if a future SDK/CLI flag exposes subagent streams (or, as a
  distant fallback, have each role write its own progress file the parent can tail — far
  more invasive, and out of scope for this spike).
