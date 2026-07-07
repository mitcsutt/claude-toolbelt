# Dashboard redesign — Agent roster + Activity feed

## Problem

The dashboard's top "pipeline" section renders **one node per tool call**, so a tick
with many tool calls overflows the page. Worse, the nodes are mislabelled: the
orchestrator's own Bash commands show up as fake "agents", and the real subagents
(Scout, Worker, Evaluator) show up labelled by their **model** ("Haiku", "Sonnet")
instead of their role.

### Root cause (data layer, not UI)

`lib/loop.sh:format_stream` builds a TSV row `[name, subagent_type, model, description]`
from each `tool_use` and reads it with `IFS=$'\t' read -r _tn _ts _tm _td`. **Tab is an
IFS-whitespace character, so bash collapses runs of tabs and drops empty fields.** The new
`Agent` tool carries no `subagent_type` (inputs are `description, model, prompt`); Bash
carries neither `subagent_type` nor `model`. Those empty columns vanish and every later
value shifts left:

- `Bash⇥⇥⇥Cat LOOP_PLAN.md` → role derived from `"Cat LOOP_PLAN.md"` → fake agent.
- `Agent⇥⇥haiku⇥Scout: recon…` → role=`"haiku"`, model=`"Scout: recon…"` → real agent
  mislabelled by model.

Python's `split('\t')` preserves empties, which is why an earlier offline replay predicted
clean roles while the live bash run produced the mess.

## Requirements (from the user)

The current UI conflates two different axes. Split them:

- **R1 — The cast.** Who is running and on what model: Orchestrator (sonnet) plus each
  dispatched subagent (Planner/Scout/Worker/Evaluator) with its model, and a clear
  **orchestrator-vs-subagent** distinction. Small, stable, bounded.
- **R2 — Live progress.** A readable, chronological feed of what's happening — the
  per-action detail the badges carried, but shaped as a **list**, height-capped with
  **internal scroll** and auto-scroll to newest, so it never eats the page.

## Design — two bounded panels

Top section (full width, under the hero) becomes two side-by-side panels:

**① Agents roster (R1)** — a list (not badges), ≤5 rows, orchestrator spine + indented
subagents:

```
●  Orchestrator   sonnet    running   ⚙ 9
✓  Scout          haiku     done      ⚙ 18   0:42
▶  Worker         sonnet    running   ⚙ 6    0:18   ← active (highlighted)
·  Evaluator      —         pending
```

Each row: status dot · role · model chip · tool count · elapsed (active row only).

**② Activity feed (R2)** — chronological, internally-scrolling, height-capped, auto-scroll,
each line tagged with the agent that performed it:

```
Worker   ✎ Edit  espn.provider.ts
Worker   ⚙ Bash  tsc --noEmit
Scout    ⚙ Read  ingest.types.ts
Orch     ⚙ Bash  Cat LOOP_PLAN.md
```

The active agent's current task is a slim header line above the panels (decision pending:
slim header vs folded into the highlighted roster row).

## Tasks

### Task 1 — Fix the field-collapse (lib/loop.sh)
- Change the jq projection from `… | @tsv` to `… | join("")` (US, 0x1f — a
  non-whitespace separator) and the reader from `IFS=$'\t'` to `IFS=$'\x1f'`.
- Acceptance: replaying the live `run.log` through the role logic yields only real roles
  (orchestrator + Scout/Worker/Evaluator), Bash calls produce no `role_start`, and Agent
  dispatches resolve role from the description token with the correct model.

### Task 2 — Carry an action label on tool events (lib/loop.sh)
- Add the tool description (the existing 4th column `_td`, e.g. "Cat LOOP_PLAN.md via
  bash", "Edit espn.provider.ts") to each `tool` event: `emit_event … desc "$_td"`.
- This feeds the Activity feed; without it the feed can only show role+tool-name.
- Acceptance: new `tool` events carry a non-empty `desc` when the tool_use had a
  description or file_path.

### Task 3 — serve.py: roster + activity derivations
- `derive_pipeline` already yields orchestrator + children (role/model/state/tools); reuse
  it as the **roster** (orchestrator row + child rows). Keep `active`/`since` for the
  highlighted row's elapsed.
- Add `derive_activity(events)` → last N (≈60) `tool` events as
  `[{role, name, desc, t}]`, oldest→newest, tagged with the now-correct role.
- Add `activity` to the snapshot dict.
- Acceptance: unit test — synthetic new-emitter events produce a roster with exactly the
  dispatched roles + correct models, and an activity list tagged with the right agent.

### Task 4 — dashboard.html: roster panel
- Render the roster as rows (orchestrator first/spine, subagents indented), status dot,
  model chip, tool count, elapsed on the active row. Bounded height. Stable morph keys
  (`roster-<role>`).

### Task 5 — dashboard.html: activity feed panel
- Render the feed as an internally-scrolling, height-capped list (`max-height` +
  `overflow:auto`), auto-scroll to newest on update, each row `agent · icon name · desc`.
  Stable keys (`act-<t>-<idx>`). Preserve user scroll-up (don't yank to bottom if the user
  has scrolled away).

### Task 6 — Layout + slim now-playing header
- Place roster (narrow, left) and activity feed (wider, right) in the top section.
- Slim header line: active agent · model · elapsed · current task. (Final placement per UX
  review.)

### Task 7 — Version bump + commit
- Bump `agent-loop` to 0.12.2; commit; rebase onto origin/master.

## Verification
- `python3 -c "ast.parse"` on serve.py; `bash -n lib/loop.sh`.
- serve.py unit tests for derive_pipeline (roster) + derive_activity.
- Replay the real `run.log` through the fixed field logic; confirm clean roles.
- Visual check in Chrome against the paused loop, then live on resume.
