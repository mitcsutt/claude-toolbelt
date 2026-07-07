# agent-loop

An autonomous coding loop for long-running, multi-task work (refactors, new features, test sweeps).

A bash harness (`run.sh`) drives a `while` loop that re-invokes a **fresh headless `claude --print` tick per task**. Each tick reads `tick-prompt.md`, picks one task, does the work, commits, and exits. Because every tick is a brand-new process, the loop gets an **OS-level context reset between tasks** — no growing session, no compaction drift.

Contrast with v1: v1 ran one long-lived in-session agent that self-scheduled with `ScheduleWakeup` and accumulated context; v2 replaces that with a stateless-per-task bash orchestrator.

## Requirements

- **superpowers** (required) — the loop delegates planning and TDD to
  `superpowers:brainstorming`, `superpowers:writing-plans`, and
  `superpowers:test-driven-development`. Install it before running a loop.
- **postmortem** (required for loop close-out) — `/agent-loop-postmortem` wraps
  `/postmortem`. Bundled in claude-toolbelt; install it from the same marketplace.
- **permission-advisor** (recommended) — an advisory pre-dispatch permission check
  used during setup. Bundled in claude-toolbelt; setup degrades gracefully without it.
- **python3** (for the dashboard) and **coreutils** (`gtimeout` for the per-tick
  timeout on macOS) — see Safety posture.

## Lifecycle

```
/agent-loop-setup        interactive, run once — config wizard + brainstorm + plan + permission gate + scaffold
   │
run.sh                   bash while-loop — worktree guard, tick_timeout cap, dispatches one tick per task
   │
fresh tick per task      PLAN: Planner (most-capable, writing-plans) │ EXECUTE: Scout (recon) → Worker (edits) → Evaluator (grades diff)
                         + parent-side verify (the tick re-runs the verification pipeline itself; never trusts the Worker)
   │
/agent-loop-postmortem   run once at the end — aggregates artefacts and delegates to /postmortem
```

A tick is either a **PLAN tick** (a most-capable-tier **Planner** subagent expands a mapped-but-unplanned segment into tasks via `superpowers:writing-plans`, writing them to disk) or an **EXECUTE tick** (Scout → Worker → Evaluator on the next dependency-eligible task). The orchestrator process itself is a coordination + verification spine — its model is set by `Orchestrator model:` in `LOOP_CONFIG.md` (blank inherits your default; a standard tier suffices since the thinking-heavy roles are delegated to subagents).

## Artefacts

All loop artefacts live under a single per-run base dir, `.claude/loop/<run-id>/` (where `<run-id>` is `<date>-<topic>`, the same slug the postmortem uses). `.claude/` **is** tracked in the target repos where loops run, so the **durable artefacts under `$LOOP_DIR/` are committed/tracked** — nested per run, with full file + trailer history preserved. Only the transient `$LOOP_DIR/runtime/` subdir is ignored (via a nested `$LOOP_DIR/.gitignore` containing `runtime/`). Tracking the durable artefacts keeps real content in the per-tick commits, so the `Loop-Status:`/`Loop-Verification:`/`Loop-Files:` trailers those commits carry survive `git log --grep` for the postmortem's audit trail (no `--allow-empty` hack needed). The committed retrospective is the postmortem under `docs/postmortems/`. The harness exports the base dir as `$LOOP_DIR` and the launch command passes it.

Durable artefacts (created by setup, updated by ticks) under `$LOOP_DIR/`:

- `LOOP_CONFIG.md` — loop configuration (goal, type, limits, blocker policy, granularity, worktree, spec/plan refs).
- `LOOP_PLAN.md` — the task list. Checkbox legend: `[ ]` pending · `[~]` in-progress · `[x]` done (+SHA) · `[!]` blocked · `[-]` skipped · `[blocked-upstream]` (a dependency is blocked).
- `LOOP_LEARNINGS.md` — per-run notes a future tick should know (APIs, conventions, gotchas). Starts empty each run; never seeded from prior runs.
- `LOOP_LOG.jsonl` — append-only structured tick-event log.
- `LOOP_USAGE.jsonl` — append-only per-tick usage/cost ledger. Each row carries a `by_model` map (distilled from the stream's `modelUsage`) attributing cost + tokens to each model the tick touched — orchestrator and every dispatched subagent — so per-tier spend is visible without an external metrics backend.
- `LOOP_CLEANUP.md` — manual follow-up tasks and decisions that need a human.
- `run.log` — harness log (full per-tick stream + timestamped events).
- `LOOP_STATUS.md` — overwritten each tick: the session header plus the last 10 per-tick summary lines. Glance at it (or `cat`/watch it) from another window to see progress without tailing the log.

Ephemeral runtime files under `$LOOP_DIR/runtime/`:

- `LOCK` — single-tick mutex (`{pid, started_at, task_id}`); stale-PID recovery on the next tick.
- `PAUSE` — touch to stop the loop after the in-flight tick finishes.
- `sprint-<TASK>.json` — the Scout's sprint contract for the current task (allow_list, forbidden, verification, success_criteria, inlined scout_notes).
- `worker-result.json` — the Worker's checkpointed result (written before deep work, so truncation never loses signal).

Persistent cross-run knowledge — `.claude/loop/KNOWLEDGE.md` (the **sibling** of the per-run `$LOOP_DIR`, NOT inside it):

- `.claude/loop/KNOWLEDGE.md` — **persistent cross-run loop knowledge.** Repo-scoped (lives in the repo), committed (under `.claude/loop/`, outside any per-run dir, so it is not caught by the nested `$LOOP_DIR/.gitignore`'s `runtime/`). Holds only durable, generalized patterns accumulated across all loop runs. It is **never bulk-seeded into a run**: the Scout reads only task-relevant entries from it per task (the same relevance gate that keeps contracts lean), and `/agent-loop-postmortem` **auto-promotes** durable learnings from the run's `LOOP_LEARNINGS.md` into it at loop close — no human gate — classifying each `## Patterns` entry durable-vs-slice-specific (durable → generalize + dedup + merge; slice-specific → drop). **Known limitation:** KNOWLEDGE.md is committed on the loop branch; if a loop branch never merges to master, the next loop (branched from master) won't see its knowledge. Persistence is realised once the loop branch merges.

## Launch

`/agent-loop-setup` prints the resolved command. It is, with `${CLAUDE_PLUGIN_ROOT}` expanded and `<run-id>` the `<date>-<topic>` slug:

```bash
cd <worktree> && LOOP_DIR=.claude/loop/<run-id> bash "${CLAUDE_PLUGIN_ROOT}/run.sh"
```

To pause: `touch .claude/loop/<run-id>/runtime/PAUSE`. To resume: delete that `PAUSE` file and re-run the start command. Tick numbers stay **continuous** across resumes (never restart at 1), and a pause writes a resume checkpoint (`runtime/CHECKPOINT.json`).

### Live dashboard

Run **`/agent-loop`** in Claude Code to background-launch the browser dashboard (zero ongoing tokens; your session stays free), or `python3 "${CLAUDE_PLUGIN_ROOT}/web/serve.py"` for the same thing headed. It needs `python3` only (stdlib; no pip, no build step).

The dashboard is **event-driven**: the harness appends one JSON line per event to `$LOOP_DIR/events.jsonl`, and the server tails that file and streams cheap snapshots over SSE — it does **not** scrape the multi-megabyte `run.log`. It shows, in real time:

- a **status verdict** (running / stalled / rate-limited / paused / done / halted / stopped / idle), derived from event recency;
- the live **role pipeline** — discovered from the event stream, not a fixed list: the orchestrator spine plus each subagent it hands off to, labelled with whatever identifier the stream carries (so new models or a different provider need no plugin change), with the active actor highlighted only while the loop is actually running;
- **effort-first usage** — tokens and effort by model, per-task and burn rates, with cost secondary;
- the full **roadmap**, including ghosted future segments still to be planned.

Launching it never auto-starts the loop — it opens at the loop's current state with ▶ Start / ⟳ Resume / ⏸ Pause / ■ Stop controls. Add `--no-spawn` for a pure read-only observer.

## Config

Set in `LOOP_CONFIG.md` (under `.claude/loop/<run-id>/`):

- **Limits** (one line): `tick_timeout` (per-tick seconds — rabbit-hole kill for a single stuck tick). There is **no cost/iteration/wall-clock budget.** The subscription usage window is the only ceiling.
- **Blocker policy**: `continue-independent` (default — on a blocker, mark dependents `[blocked-upstream]` and keep working unblocked tasks) or `halt` (stop the loop).
- **Granularity**: `single` (whole plan up front) or `segmented` (segments mapped in config, each expanded by its own PLAN tick).

## Progress & usage

- Ticks run with `--output-format stream-json`. The terminal shows a **glanceable** view, not the raw trace:
  - While a tick runs, a single self-updating heartbeat line (spinner · current activity · tool count · elapsed) proves liveness.
  - When a tick finishes, one permanent summary line scrolls into history: `✓ t3 T26 · 3m12s · lint✓ tsc✓ build✓ test✓ · → a1b2c3   62% (26/42)` (verdict glyph, task, duration, verification gates from the commit's `Loop-Verification` trailer, short SHA, and **percentage-first** progress).
  - A session header prints each tick: `── loop · 62% ███████░░░░░ 26/42 · ⏱ 1h18m · ~45m left · 5h 90% ↺2h12m ──` (task percent + bar, count, elapsed, rough ETA from average tick time, and the usage-window utilization). The `5h 90%` segment is the 5-hour rate-limit window utilization Claude reports on `rate_limit_event` lines, with time-to-reset (`↺`); it's labelled to distinguish it from the leading task percentage, and is omitted until the stream reports it (Claude only includes `utilization` near the warning threshold). No dollar figure — `cost_usd` is raw list-price and is never summed or projected.
  - The full per-tool/per-text trace still tees to `$LOOP_DIR/run.log` (`tail -f`), and `LOOP_VERBOSE=1` restores it to the terminal in place of the heartbeat. Piped/non-TTY runs stay quiet (header + summary lines only).
- The harness reads the `rate_limit_event` from each tick. When the usage window is exhausted it **auto-waits until the reset** (`resetsAt`) and resumes; if the reset is further out than `MAX_WAIT` (default 6h, e.g. a weekly window) it logs the reset time and **exits cleanly** so you can re-run `run.sh` later. Resume is safe at any point: work is committed per task, with stale-lock recovery and the Worker's checkpoint file.
- The loop also self-terminates on `LOOP_DONE`, a tick `LOOP_HALT`, 3 consecutive failed/garbage ticks, or 3 consecutive `CONTINUE` ticks with no drop in remaining tasks (no-progress guard).

## Safety posture

- Ticks run headless with `--dangerously-skip-permissions`, inside the OS sandbox, confined to a git **worktree**. `run.sh` enforces a worktree guard: it refuses to run if `pwd` does not equal the configured `Worktree:`.
- The per-tick wall-clock cap (`tick_timeout`) requires `timeout` or `gtimeout` on PATH. If neither is present the harness still runs but logs a warning and the cap is **disabled** — a stuck tick can run unbounded. Install coreutils (`brew install coreutils` provides `gtimeout` on macOS) to restore it.
- After the Worker returns, the tick reverts any changed path outside the Scout's `allow_list`, then runs parent-side verification. Only a clean parent-side run (plus an Evaluator PASS) commits.

## Dynamic model selection

The governing rule is **use the least powerful model that can handle each role/task**. Tier is matched to complexity (cheap for read-only scouting and mechanical edits, most-capable for evaluation/judgement), resolved to the cheapest capable model alias at dispatch, honouring any tier overrides in `LOOP_CONFIG.md`. On a reasoning-gap failure the tick **escalates one tier** on re-dispatch rather than retrying the same model unchanged.

The plugin hardcodes **no** model names — everything is expressed in tiers (`cheap | standard | most-capable`), and the only place a concrete model alias is ever named is the value you write into `LOOP_CONFIG.md`. The split:

- **Orchestrator** (the per-tick process) is a coordination + verification spine: it reads state, runs the verification pipeline, dispatches the role subagents, and does the bookkeeping. It needs no top-tier reasoning. Set `Orchestrator model:` in `LOOP_CONFIG.md` (or the `LOOP_ORCHESTRATOR_MODEL` env var for a one-off) to run it at a standard tier; blank inherits your Claude default.
- **Planner** (PLAN ticks) and **Evaluator** (review) ride the **most-capable** tier — plan decomposition and the workaround-vs-legit-pass judgement are the highest-leverage reasoning in the loop, so the thinking budget is concentrated there rather than spread across the mechanical spine.
- The **Evaluator** returns `PASS | NEEDS_WORK | BLOCKER`; `BLOCKER` means the diff only "passes" via a workaround/spec-deviation. Keeping that subtle call on the most-capable Evaluator is what lets the orchestrator safely run cheaper — it just routes on the verdict.

## Tests

```bash
bash tests/all.sh
```

Runs `lib.test.sh`, `run.e2e.test.sh`, and the `*.contract.sh` suites, then `lint.sh` (shellcheck; skipped non-fatally if shellcheck is absent). Note: `lib.test.sh` and `run.e2e.test.sh` use `mktemp -d`; if the sandbox blocks it, run with the sandbox disabled.

## Design docs

- Spec: `docs/superpowers/specs/2026-06-02-agent-loop-v2-bash-orchestrated-design.md`
- Plan: `docs/superpowers/plans/2026-06-02-agent-loop-v2-bash-orchestrated.md`
