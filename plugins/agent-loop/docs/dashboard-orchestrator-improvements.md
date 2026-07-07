# Dashboard + Orchestrator Improvements — implementation spec

Derived from live-loop feedback. Goal: make the dashboard's role model legible, repair the
quota/narrative/pause/selection rough edges, and make the orchestrator spine cheap by default.
Each section cites the responsible code. Implementers own disjoint files (see "Work split").

## Cross-cutting contract: ROLE EVENTS (harness → dashboard)

Today every dispatched role is `subagent_type:"claude"`, so `lib/loop.sh` records `role="claude"`
for Planner/Worker/Evaluator alike (only the Scout's `Explore` recon is labelled). Role *intent*
must enter the event stream. Contract:

- **Role tokens (canonical):** `Planner`, `Scout`, `Worker`, `Evaluator`. The always-running
  per-tick process is `orchestrator` (implicit parent).
- **Emission mechanism (no SDK change):** the orchestrator already dispatches roles via the Agent
  tool. It MUST prefix each dispatched subagent's `description` with the role token + ": "
  (e.g. `Worker: implement T9 …`, `Evaluator: review T9 …`, `Scout: recon for T9 …`,
  `Planner: expand Segment 3`). `tick-prompt.md` is updated to mandate this.
- **`lib/loop.sh` (format_stream, ~L92-94):** add `(.input.description//"")` as a 4th TSV column.
  Derive `role_label`: if description matches `^(Planner|Scout|Worker|Evaluator)\b` → that token;
  elif `subagent_type=="Explore"` → `Scout`; else → `subagent_type` (fallback). Track `active_role`
  and emit `role_start`/`role_end`/`handoff` keyed on **`role_label`** (not raw subagent_type), and
  include both `role=<role_label>` and `subagent=<subagent_type>` and `model=<model>` on `role_start`.
- **`web/serve.py` (`derive_pipeline`, ~L377):** consume `role_start`/`role_end`/`handoff` with the
  `role` field; expose a tree: a persistent `orchestrator` node whose `children` are the dispatched
  role spans (role, model, status active|done). Backwards-compatible if `role` is an old value.
- **`web/dashboard.html` (pipeline render, ~L315-329):** render `orchestrator` as a persistent
  parent node with role children nested beneath it (tree, not a flat belt-separated chain). Each
  child shows role + model + active/done. Generic "claude" should now be rare (only true fallbacks).

---

## A. Orchestrator default → standard spine (paired with the world-cup manual fix)

Problem: `Orchestrator model:` defaults blank → inherits the user's Claude default (Opus), so the
coordination spine runs Opus on **every** tick (~90% of observed cost) though it only coordinates +
runs the gate. Fix (new loops cheap by default, power-user escape hatch preserved):

- **`templates/LOOP_CONFIG.md` (L23):** change `Orchestrator model:` → `Orchestrator model: sonnet`.
  Update the comment block (L17-22) to state the spine now defaults to a standard model (Sonnet);
  blank it to inherit your Claude default; name any alias to override. Keep "no model hardcoded in
  plugin *code*" — the alias lives in user-editable config/template only.
- **`skills/agent-loop-setup/SKILL.md`:** in the config-wizard step that documents model tiers, note
  the orchestrator now defaults to a standard (Sonnet) spine and why (cost; heavy reasoning is
  delegated to most-capable Planner/Evaluator subagents). Do NOT make `run.sh` hardcode a default
  (it already carries `--fallback-model sonnet`); the template default is the mechanism.

## B. Cost-lever guidance (document; implement the safe tick rule)

From the cost analysis (Opus = 90% of spend): in `tick-prompt.md`, add two rules + document the rest
in this doc's "Cost levers" appendix:
- **Truncation rule:** if a dispatched Worker returns truncated/partial output, the orchestrator must
  **re-dispatch to a Worker subagent**, NOT finish the files itself on the (expensive) spine — the
  single biggest blowup in the live run ($7.45 tick where Opus spine finished a Sonnet Worker's files).
- **Mechanical-task rule:** for tasks tagged `mechanical`, the Evaluator/review pass may use a standard
  tier rather than most-capable (the fake-test catch that justified Opus was on a TDD task, not a
  mechanical one). Honour the task's `model:` tag for the Worker as today.

---

## Dashboard items (own `web/serve.py` + `web/dashboard.html` together — they share the snapshot)

### #4 Quota panel shows "—" forever (BUG → repurpose)
`parse_quota` (serve.py ~L514/209) reads `runtime/plan-usage.json` `.utilization`, which is only
written near the warn threshold (`run.sh` ~L186) — i.e. effectively never. The file that DOES exist
is `runtime/ratelimit.json` (written from `rate_limit_event`, see `lib/loop.sh:70-71`) with
`resetsAt`/`rateLimitType` (no utilization). **Repurpose:** read `ratelimit.json`; show
`rateLimitType` + a live countdown to `resetsAt` when present, else a neutral "within limits".
Remove the dead `.utilization` path.

### #3 Narrative too coarse (enrich from data already on disk)
`_narrative` (serve.py ~L558) emits only `tick/mode/cost/dur`. The per-model split already exists on
each `tick_end` event as `by_model` (see `run.sh:165-169`) and in `LOOP_USAGE.jsonl`. Plumb per-tick
`by_model` into the narrative entries; in `dashboard.html` (`renderNarrative` ~L362) make each row
expandable to show the model split (e.g. `opus $1.40 · sonnet $0.30 · haiku $0.06`). The
per-*subagent* split is enabled by the role-event contract above (nice-to-have; wire if cheap).

### #6 Pause UI implies immediate (add transitional state)
Pause writes `runtime/PAUSE`, honoured at the next tick boundary (`run.sh` ~L125). Add a `pausing`
verdict in serve.py (`event_verdict`/snapshot ~L440): when the PAUSE file exists AND the loop still
seems active (`pause_exists` && `loop_seems_active`), report `pausing`. In `dashboard.html`
(`renderHero` ~L311 + pause button handler) show `⏸ PAUSING… (finishing current tick)` until status
flips to `paused`.

### #2 Roadmap → collapsible segments
`renderRoadmap` (dashboard.html ~L331) shows a flat track + only the current segment's tasks at the
bottom. Rewrite each segment as a `<details>`/`<summary>` with its own task list (data already in
`d.plan` filtered by `t.segment`; segment meta in `roadmap()` serve.py ~L178). Default-open the
`current` segment. Add "Expand all / Collapse all" controls at the top.

### #7 "Since you last looked" — remove
`sinceLastLook` (dashboard.html ~L381) diffs a localStorage snapshot on every 1/s SSE render, so it
updates constantly and means nothing. Remove it (markup + JS + any CSS).

### #8 Text selection unselects immediately — SUPERSEDED by the morph render (below)
Original plan was a selection-pause guard in `render()`. That was a hack (it freezes updates while a
selection is held). Replaced by the rendering-architecture rewrite below; the hack was removed.

### Rendering architecture (Approach A) — morph + client-owned view-state
Root cause of BOTH the selection loss and the open/closed snap-back (segment reopens, narrative
closes, any roadmap closes on the next tick): every SSE tick replaced panel `innerHTML` wholesale
(recreating nodes → losing selection/focus/scroll/`<details open>`), AND open/closed state was
derived from the snapshot each render (so a tick re-imposed it). Fix — vanilla JS, zero deps:
- **`morphInner(el, html)`** in `web/dashboard.html`: parse fresh HTML into a `<template>`, then
  reconcile the live container's children in place — keyed by `id`/`data-key`, positional for
  unkeyed — patching text/attrs and reusing matched nodes so node identity (and thus browser UI
  state) survives. Every render-path `innerHTML =` is replaced with `morphInner`.
- **Client-owned view-state**: `ui.open` (a `Set` persisted in `sessionStorage`) is the single
  source of truth for which `<details>` are open; a delegated `toggle` listener keeps it in sync;
  render emits `open` from `ui.open`, never from the snapshot. "Default-open current segment" is a
  one-time seed on first load. Stable keys: segments `seg-<i>`, narrative `narr-<tick>`, tasks
  `task-<id>`, roles `role-<name>`.
- Verified in a real browser (open/close persists across re-render; selection survives; node
  identity preserved). The selection-pause hack is deleted.

## #5 Live per-agent log streaming — RESEARCH SPIKE (doc only, do not implement)
No subagent stdout is captured today; `run.log` holds only the top-level orchestrator stream and
subagent internals collapse to `tool` count events. Implementing click-to-tail per role would need
per-role logfiles + a new `/events/<role>` SSE channel AND a way to capture subagent sub-streams,
which the Agent tool may not surface. Write findings + a recommended approach (or "blocked on SDK
support") to `docs/spike-live-agent-logs.md`. No code.

---

## Work split (disjoint files → parallel-safe)
- **Sub 1 (harness + config):** `lib/loop.sh` (role extraction), `tick-prompt.md` (role-token mandate
  + cost rules §B), `templates/LOOP_CONFIG.md`, `skills/agent-loop-setup/SKILL.md`. (§ROLE-emit, A, B)
- **Sub 2 (dashboard):** `web/serve.py` + `web/dashboard.html` — items #1(consume+render tree), #2, #3,
  #4, #6, #7, #8. Binds to the ROLE EVENT contract above.
- **Sub 3 (docs):** `docs/spike-live-agent-logs.md` (#5) + a "Cost levers" appendix to this file.

## Verification (each sub + integrator)
- `python3 -m py_compile web/serve.py` exits 0; `bash -n lib/loop.sh run.sh` exits 0.
- Smoke: run `LOOP_DIR=<a real loop dir> python3 web/serve.py --no-spawn` on a free port against the
  world-cup loop's `events.jsonl`; curl the snapshot endpoint; assert valid JSON + no traceback.
- Manual review of `dashboard.html` render functions for the new tree/segments/selection guard.
