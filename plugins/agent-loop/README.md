# agent-loop

An autonomous coding loop for long-running, multi-task work (refactors, new features, test sweeps).

A Python harness (`runner/run.py`, entered through the `run.sh` shim) owns the loop's state machine and runs one **phase** at a time as its own `claude -p` process: SELECT → SCOUT → VALIDATE → WORK → SANDBOX → GATE → RENDER → FIDELITY → EVALUATE → COMMIT → LEARN, with a JUDGE phase on any failure path. The harness is the only thing that reads the plan, picks tasks, edits checkboxes, runs commands, enforces the allow-list and commits. Each model does exactly one thing from a small role brief and returns a JSON document the harness validates.

Why phases rather than one tick-shaped prompt: `--model` per phase is a command-line fact rather than a request inside a prompt; each phase has its own timeout, turn cap, budget and usage record, flushed even when it is killed; a killed Worker is `--resume`-able from its session id; and PAUSE/STOP land between phases, so a stop takes minutes rather than the rest of a 30-minute tick.

Contrast with v2: v2's spine was a 300-line prompt that re-read the plan every tick and cost 31% of one run's spend while enforcing tier directives as prose. v3 deletes it. Contrast with v1: v1 ran one long-lived in-session agent that self-scheduled and accumulated context.

## What it ships

| Component | Kind | What |
| --- | --- | --- |
| `/agent-loop-setup` | skill | Interactive, one-time bootstrap — config wizard, brainstorm, plan, permission gate, scaffold. |
| `/agent-loop` | skill | Attach: health from the runtime files, dashboard URL (reuses the sidecar), arms a persistent incident monitor that wakes the session to run the medic. Never starts the harness. |
| `/agent-loop-medic` | skill | Bounded triage-and-repair on an incident — headless under `run.sh` or interactive from `/agent-loop`. Allowlisted fixes only, hard budget, then hands over to a human. |
| `/agent-loop-postmortem` | skill | Wraps `/postmortem` to close out a loop, aggregating artefacts into a retrospective. |
| `runner/` | python harness | One process per loop. Owns the lock, heartbeat, events, PAUSE/STOP, phase dispatch, the gate, git, the Judge and the dashboard sidecar. Python 3.9 stdlib only. |
| `runner/prompts/*.md` | role briefs | One per phase (scout, worker, worker_wrapup, worker_resume, evaluator, judge, planner, reviewer, learner). Each states its JSON output shape and forbids interactive tools. |
| `run.sh` | shim | `exec python3 runner/run.py "$@"` — kept so the dashboard's launcher and every `ps … run.sh` liveness check still work. |
| `web/serve.py` | dashboard server | Stdlib-only Python server; incremental bounded tail of `events.jsonl`, one shared snapshot over SSE, status derived from events + PID liveness (never the log). Serves the render gate's screenshots at `/api/artifact`. |

### Lifecycle

```
/agent-loop-setup        interactive, run once — config wizard + brainstorm + plan + permission gate + scaffold
   │
runner/run.py            one process — worktree guard, lock, heartbeat, plan state, phase dispatch, git
   │
one phase per step       SELECT → SCOUT → VALIDATE → WORK → SANDBOX → GATE → RENDER → FIDELITY →
                         EVALUATE → COMMIT → LEARN, with JUDGE on any failure path
   │
/agent-loop-postmortem   run once at the end — aggregates artefacts and delegates to /postmortem
```

Every box except the first and last is the harness itself. A phase is one `claude -p` process with one job, its own `--model` (resolved from the `Tiers:` line), its own timeout, turn cap and budget, and its own row in the usage ledger. SELECT, VALIDATE, SANDBOX, GATE, FIDELITY and COMMIT involve no model at all — they are Python. A **PLAN tick** dispatches the Planner instead, to expand a mapped-but-unplanned segment into tasks.

The harness never trusts a phase's self-report: GATE re-runs the verification commands itself, SANDBOX reverts anything the Worker touched outside its `allow_list`, RENDER runs the repo's render recipe and archives the screenshots, and FIDELITY hard-fails a `| copy_of:` task whose output is not actually similar to its source.

### Artefacts

All loop artefacts live under a single per-run base dir, `.claude/loop/<run-id>/` (where `<run-id>` is `<date>-<topic>`, the same slug the postmortem uses). `.claude/` **is** tracked in the target repos where loops run, so the **durable artefacts under `$LOOP_DIR/` are committed/tracked** — nested per run, with full file + trailer history preserved. Only the transient `$LOOP_DIR/runtime/` subdir is ignored (via a nested `$LOOP_DIR/.gitignore` containing `runtime/`). Tracking the durable artefacts keeps real content in the per-tick commits, so the `Loop-Status:`/`Loop-Verification:`/`Loop-Files:` trailers those commits carry survive `git log --grep` for the postmortem's audit trail (no `--allow-empty` hack needed). The committed retrospective is the postmortem under `docs/postmortems/`. The harness exports the base dir as `$LOOP_DIR` and the launch command passes it.

Durable artefacts (created by setup, updated by ticks) under `$LOOP_DIR/`:

- `LOOP_CONFIG.md` — loop configuration (goal, type, limits, blocker policy, granularity, worktree, spec/plan refs).
- `LOOP_PLAN.md` — the task list. Checkbox legend: `[ ]` pending · `[~]` in-progress · `[x]` done (+SHA) · `[!]` blocked · `[-]` skipped · `[blocked-upstream]` (a dependency is blocked).
- `LOOP_LEARNINGS.md` — per-run notes a future tick should know (APIs, conventions, gotchas). Starts empty each run; never seeded from prior runs.
- `LOOP_LOG.jsonl` — append-only structured tick-event log.
- `LOOP_USAGE.jsonl` — append-only per-tick usage/cost ledger. Each row carries a `by_model` map (distilled from the stream's `modelUsage`) attributing cost + tokens to each model the tick touched — orchestrator and every dispatched subagent — so per-tier spend is visible without an external metrics backend.
- `LOOP_CLEANUP.md` — manual follow-up tasks and decisions that need a human — the queue of things the Judge **refused** to settle.
- `LOOP_DECISIONS.md` — the audit trail of everything the Judge settled *without* a human under `Decision policy: autonomous`: widened constraints, tier escalations, task splits, and defaults chosen where the spec was silent, each with the alternatives it rejected. A ratify-or-reverse queue, not a failure list.
- `artifacts/<T>/<name>.png` — the screenshots the render gate produced for task `<T>`, and the evidence the Evaluator was required to observe. Durable, and gitignored inside the run dir only so the SANDBOX revert cannot delete them mid-tick.
- `harness.log` — the harness's own narration, rotated at 10 MB × 3. Gitignored. **`run.log` is gone**: v3 does not tee a per-tick stream (one v2 run left a 105 MB file nobody read). A phase's full transcript lives in `~/.claude/projects/<encoded cwd>/<session-id>.jsonl`, and its `phase_end` event records the path.
- `LOOP_STATUS.md` — overwritten each tick: the session header plus the last 10 per-tick summary lines. Glance at it (or `cat`/watch it) from another window to see progress without tailing the log.

Ephemeral runtime files under `$LOOP_DIR/runtime/`:

- `harness.json` — the harness lock itself (`{pid, start_epoch, host, loop_dir, plugin_version}`). It is created with `ln` from a fully written private file, so the lock never exists without its owner; a dead owner is cleared by rename, which only one starter can win. One harness per `LOOP_DIR`; a second exits 3. There is no tick-level lock file any more — `runtime/LOCK` was removed in 2.0.0, and the 2.0.x `harness.lock.d/` dir is gone in 2.1.0 (a leftover one is ignored and tidied on release).
- `HEARTBEAT` — epoch seconds, rewritten every `HB_INTERVAL` (10 s) while the harness runs.
- `tick.json` — `{tick, pid, started_at, timeout_s}` while a tick is in flight; removed after.
- `last-activity` — epoch of the last stream line seen from the tick (stall detection).
- `dashboard.json` — `{pid, port, url, sidecar}` written by `serve.py`.
- `incident-<id>.json` / `medic-<id>.json` — one pair per incident: what the harness saw, what the medic did.
- `NEEDS_HUMAN.md` — written by the harness when it stops for a human: incident summary, medic notes, the exact resume command.
- `PAUSE` — touch to stop the loop after the **phase** in flight finishes.
- `STOP` — touch (or press **Stop now**) to SIGTERM the phase in flight first, so the stop lands in minutes rather than at the end of the tick. Otherwise identical to PAUSE.
- `sprint-<T>.json` — the Scout's sprint contract for the current task (allow_list, forbidden with provenance, verification, success_criteria, render_gate, fidelity_source, evaluator_must_read, evaluator_must_view).
- `task-<T>.json` — the per-task attempt/state record (spec §14): the phase it reached and `attempts[].n/.tier/.outcome/.judge`. This is what lets a dir stopped mid-task resume rather than restart.
- `gate-<T>-<n>.txt` — the captured output of gate command `<n>` for task `<T>`; what the Learner must cite and the Judge reads.
- `CHECKPOINT.json` — written when a PAUSE or STOP lands, so the next launch continues rather than restarting.
- `worker-result.json` — the Worker's checkpointed result (written before deep work, so truncation never loses signal).

Persistent cross-run knowledge — `.claude/loop/KNOWLEDGE.md` (the **sibling** of the per-run `$LOOP_DIR`, NOT inside it):

- `.claude/loop/KNOWLEDGE.md` — **persistent cross-run loop knowledge.** Repo-scoped (lives in the repo), committed (under `.claude/loop/`, outside any per-run dir, so it is not caught by the nested `$LOOP_DIR/.gitignore`'s `runtime/`). Holds only durable, generalized patterns accumulated across all loop runs. It is **never bulk-seeded into a run**: the Scout reads only task-relevant entries from it per task (the same relevance gate that keeps contracts lean), and `/agent-loop-postmortem` **auto-promotes** durable learnings from the run's `LOOP_LEARNINGS.md` into it at loop close — no human gate — classifying each `## Patterns` entry durable-vs-slice-specific (durable → generalize + dedup + merge; slice-specific → drop). **Known limitation:** KNOWLEDGE.md is committed on the loop branch; if a loop branch never merges to master, the next loop (branched from master) won't see its knowledge. Persistence is realised once the loop branch merges.

## Install

```text
/plugin marketplace add mitcsutt/claude-toolbelt
/plugin install agent-loop@claude-toolbelt
```

### Requirements

- **superpowers** (required) — the loop delegates planning and TDD to
  `superpowers:brainstorming`, `superpowers:writing-plans`, and
  `superpowers:test-driven-development`. Install it before running a loop.
- **postmortem** (required for loop close-out) — `/agent-loop-postmortem` wraps
  `/postmortem`. Bundled in claude-toolbelt; install it from the same marketplace.
- **permissions** plugin (recommended) — provides `/permissions-advisor`, an advisory
  pre-dispatch permission check used during setup. Available in claude-toolbelt; setup
  degrades gracefully without it.
- **python3** (for the dashboard) and **coreutils** (`gtimeout` for the per-tick
  timeout on macOS) — see Configuration.

## Usage

`/agent-loop-setup` prints the resolved command. It is, with `${CLAUDE_PLUGIN_ROOT}` expanded and `<run-id>` the `<date>-<topic>` slug:

```bash
cd <worktree> && LOOP_DIR=.claude/loop/<run-id> bash "${CLAUDE_PLUGIN_ROOT}/run.sh"
```

To pause: `touch .claude/loop/<run-id>/runtime/PAUSE`. To resume: delete that `PAUSE` file and re-run the start command. Tick numbers stay **continuous** across resumes (never restart at 1), and a pause writes a resume checkpoint (`runtime/CHECKPOINT.json`).

The usual way to start is **dashboard-first**: `/agent-loop` in a Claude Code session ensures one detached dashboard for the loop dir and prints its URL; press **▶ Start** there (or ask the session, which POSTs `/api/start`). The harness runs under that dashboard, not under any session, so closing Claude changes nothing. The command above is the terminal alternative; with a dashboard alive the harness adopts it.

### Dashboard: exactly one per loop dir

Three ways to get there, one result. `/agent-loop` runs `web/serve.py --detach`, which reuses a live dashboard (`"reused": true`) or launches one in its own process session and returns its URL. That dashboard's ▶ Start / ⟳ Resume spawn `run.sh` (child of the dashboard, `LOOP_DASHBOARD=off`, own session), and refuse with 409 while `runtime/harness.json` names a live harness. A terminal `bash run.sh` **adopts** a live non-sidecar dashboard (`◉ dashboard <url> (adopted)`, recorded in `loop_start`, never supervised or killed by the harness); only with none alive does `Dashboard: auto` spawn a supervised sidecar, restarted on the same port up to `DASH_MAX_RESTARTS` and killed when the harness exits. `Dashboard: off` (or `LOOP_DASHBOARD=off`) disables that spawn only; adoption always happens. `LOOP_DASHBOARD_OPEN=1` opens the URL once.

The dashboard needs `python3` only (stdlib; no pip). It is **event-driven** and read-mostly: the server keeps an incremental, bounded store over `$LOOP_DIR/events.jsonl` (full detail for the current tick, per-tick aggregates for earlier ticks) and one shared snapshot thread streams to every SSE client — memory stays flat however long the run. It never reads the harness's log. It shows: a health-first **status** with the *why* (harness pid + heartbeat age, tick pid + elapsed vs timeout, dashboard uptime), the tick **phase timeline**, honest **progress** (segment-weighted while segments are unplanned — `100%` appears only when every segment is planned and every task is `[x]`/`[-]`), the **incidents** panel with each medic outcome, effort-first **usage**, and the roadmap.

Launched standalone (`python3 "${CLAUDE_PLUGIN_ROOT}/web/serve.py"`, with or without `--detach`) it has ▶ Start / ⟳ Resume / ⏸ Pause / ■ Stop; `--no-spawn` makes it a pure observer whose buttons only write/remove `runtime/PAUSE`.

### Attach: `/agent-loop`

Run **`/agent-loop`** in a Claude Code session on the repo. It reports harness/tick health from `runtime/harness.json` + `HEARTBEAT` + `tick.json` and the last `loop_end`, ensures one detached dashboard and prints its URL, and arms a **persistent `Monitor`** on `events.jsonl` filtered to `incident | loop_end | medic_end | memory_pressure`. That session is then woken on an incident instead of polling: a `needs-human` incident or a non-`done` `loop_end` runs `/agent-loop-medic` interactively and sends a push notification; `done` offers `/agent-loop-postmortem`. With no harness alive it asks one question — start from here (through the dashboard's `/api/start`, recommended) or a terminal command — with the tradeoff for each; ▶ Start in the dashboard always works too. It never runs `run.sh` itself. Without the Monitor tool it arms a background `tail -F | grep -m1` fallback that wakes the session on the first matching event.

### Medic and hand-over

On an incident the harness writes `runtime/incident-<id>.json`, emits an `incident` event, sends a desktop notification (`Medic: auto|notify`), and — with `Medic: auto` and budget left (`MEDIC_MAX_PER_RUN`, default 3) — runs `/agent-loop-medic <id>` headless, never concurrently with a tick. The medic reads the incident, the lifecycle events, and the runtime files, applies only the fixes on its allowlist (stale lock dir after verifying the pid is dead, stale runtime files, an orphaned `[~]`→`[ ]` flip, `git checkout --`/`git clean` inside the last sprint contract's `allow_list`, a dashboard restart, notes), and returns `resumed | paused | escalated | noop`. It escalates on the second occurrence of the same signature, on anything outside the allowlist, and on a halt sentinel or git divergence by definition. `resumed` → the loop continues. `paused`/`escalated`/no answer within `MEDIC_TIMEOUT` → the harness writes `runtime/NEEDS_HUMAN.md` (incident, medic notes, the exact resume command), emits `loop_end reason=needs-human`, and exits **2**. The medic never edits product code and never starts a harness.

### Exit codes

`0` done / paused / rate-limit-exit · `1` halt / error · `2` needs-human (read `runtime/NEEDS_HUMAN.md`, fix, re-run) · `3` lock-conflict (another harness owns this `LOOP_DIR`; its pid is printed — stop it first). Two of the exit-2 kinds come from the schema check, not from a tick: `migration-blocked` and `schema-newer` — see [Upgrading](#upgrading-loop-dir-schema).

### Progress & usage

- Phases run with `--output-format stream-json`. The terminal shows a **glanceable** view, not the raw trace:
  - While a tick runs, a single self-updating heartbeat line (spinner · current activity · tool count · elapsed) proves liveness.
  - When a tick finishes, one permanent summary line scrolls into history: `✓ t3 T26 · 3m12s · lint✓ tsc✓ build✓ test✓ · → a1b2c3   62% (26/42)` (verdict glyph, task, duration, verification gates from the commit's `Loop-Verification` trailer, short SHA, and **percentage-first** progress).
  - A session header prints each tick: `── loop · 62% ███████░░░░░ 26/42 · ⏱ 1h18m · ~45m left · 5h 90% ↺2h12m ──` (task percent + bar, count, elapsed, rough ETA from average tick time, and the usage-window utilization). The `5h 90%` segment is the 5-hour rate-limit window utilization Claude reports on `rate_limit_event` lines, with time-to-reset (`↺`); it's labelled to distinguish it from the leading task percentage, and is omitted until the stream reports it (Claude only includes `utilization` near the warning threshold). No dollar figure — `cost_usd` is raw list-price and is never summed or projected.
  - There is no file copy of the raw trace and no verbose terminal mode: each phase's full transcript stays in its own session JSONL, whose path its `phase_end` event records. Piped/non-TTY runs stay quiet (header + summary lines only).
- The harness reads the `rate_limit_event` from each tick. When the usage window is exhausted it **auto-waits until the reset** (`resetsAt`) and resumes; if the reset is further out than `MAX_WAIT` (default 6h, e.g. a weekly window) it logs the reset time and **exits cleanly** so you can re-run `run.sh` later. Resume is safe at any point: work is committed per task, an orphaned `[~]` is re-evaluated by the next tick (or reset by the medic), and the Worker's checkpoint file survives.
- The loop also self-terminates on `LOOP_DONE`, a tick `LOOP_HALT`, 3 consecutive failed/garbage ticks, or 3 consecutive `CONTINUE` ticks with no drop in remaining tasks (no-progress guard) — each of these raises an incident first, so the medic gets a look before the harness stops.
- Each `tick_end` carries a `cause` (`ok | timeout | killed | terminated | api_error | rate_limit | no_sentinel | crashed`), so the summary line distinguishes `killed (SIGKILL — likely OS memory pressure)` from a tick that produced nothing. A pre-tick memory guard delays (never halts) while free RAM is below `MEM_MIN_MB` and swap above `SWAP_MAX_PCT`, emitting `sleep reason=memory` so the dashboard says "waiting for memory headroom" rather than "stalled".

## Configuration

### Config

Set in `LOOP_CONFIG.md` (under `.claude/loop/<run-id>/`):

- **Limits** (one line, space-separated `key=value`, seconds): `tick_timeout=1800` (outer sanity cap per tick, and the figure the dashboard displays) · `scout_timeout=480` · `worker_timeout=1500` · `wrapup_timeout=300` · `eval_timeout=480` · `judge_timeout=360` · `planner_timeout=900` · `reviewer_timeout=900` · `learner_timeout=180` · `gate_cmd_timeout=600` (per verification/render/fidelity command) · `worker_resume_max=1` · `worker_budget_usd=6` · `max_attempts=3` (hard cap on attempts for one task, resumes and escalations included). Each phase has its own wall; the tick has no single one. There is still **no cost/iteration/wall-clock budget for the run** — the subscription usage window is the only ceiling, and the harness auto-waits on it. After a segment, `python3 -m runner.calibrate <run-dir>` suggests a `Limits:` line from that segment's own phase durations.
- **Tiers** (one line): `cheap=<alias> standard=<alias> most-capable=<alias>`. The only place this plugin names a model. Phases request a tier; the harness resolves it to `--model` at dispatch. A Worker's second attempt escalates one tier, enforced by the harness.
- **Decision policy**: `autonomous` (default — before marking `[!]`, halting or raising needs-human, a Judge phase may widen a Scout-invented constraint, escalate a tier, split a task, or pick a default where the spec is silent, appending every choice to `LOOP_DECISIONS.md` with its alternatives) or `conservative` (widen/escalate/split allowed; spec-silent defaults deferred to `LOOP_CLEANUP.md`).
- **Render** (optional block): `start` / `ready` / `command` / `ui_globs` / `reference` — how a headless phase can *see* the product. `ui_globs` is what makes the gate mandatory: a task whose `allow_list` touches one of those globs must carry a `render_gate` unless its plan row is tagged `| no-ui`.
- **Blocker policy**: `continue-independent` (default — on a blocker, mark dependents `[blocked-upstream]` and keep working unblocked tasks) or `halt` (stop the loop).
- **Granularity**: `single` (whole plan up front) or `segmented` (segments mapped in config, each expanded by its own PLAN tick).
- **Dashboard**: `auto` (default — `run.sh` spawns and supervises the sidecar) or `off`.
- **Medic**: `auto` (default — notify + headless `/agent-loop-medic`, budgeted) · `notify` (desktop notification only; error incidents go straight to `NEEDS_HUMAN.md`) · `off`. **Medic model**: alias or blank (inherit).

### Env knobs (`run.sh`)

| Variable | Default | Effect |
| --- | --- | --- |
| `HB_INTERVAL` | `10` | Heartbeat and supervisor period, seconds. Observers treat the harness as dead when `HEARTBEAT` is older than 3×. |
| `STALL_S` | `300` | No stream activity for this long during a tick → `incident kind=tick-stalled` (warn; `tick_timeout` still owns the kill). |
| `MEM_MIN_MB` / `SWAP_MAX_PCT` | `1024` / `90` | Pre-tick memory guard: delay when free MB is below **and** swap % is above. |
| `MEM_BACKOFF` / `MEM_MAX_DELAYS` | `60` / `5` | Seconds per delay, and how many before proceeding regardless with a warning. |
| `MEDIC_MAX_PER_RUN` | `3` | Medic invocations per harness run; beyond it error incidents escalate directly. |
| `MEDIC_TIMEOUT` | `600` | Wall clock per medic run; overrun counts as `escalated`. |
| `DASH_MAX_RESTARTS` | `5` | Sidecar respawns per hour before `dashboard-crashloop`. |
| `LOOP_DASHBOARD` | config | `auto`/`off`, overrides the `Dashboard:` line for one launch. |
| `LOOP_DASHBOARD_CMD` | serve.py | Full sidecar command override (tests, or a different observer). |
| `LOOP_MEDIC_CMD` | claude | Full medic command override; default `claude --print --dangerously-skip-permissions [--model M] "/agent-loop-medic <id>"`. |
| `LOOP_MIGRATE_FORCE` | unset | `1` → run a pending schema migration even though `run.log` looks like a pre-2.0 harness is still writing it. Only after you have confirmed that harness is dead. |
| `LOOP_NOTIFY` | `1` | `0` → suppress desktop notifications on an incident. |
| `MAX_WAIT` | `6h` | Longest rate-limit reset the harness will wait for before exiting cleanly. |

### Events (`events.jsonl`)

One JSON object per line, every one carrying `t` (epoch), `seq` (monotonic per harness process) and `type`. `events.jsonl` is the single source of truth for status; `harness.log` is forensic only.

| type | fields | when |
| --- | --- | --- |
| `loop_start` | `pid`, `host`, `plugin_version`, `resume`, `dashboard_url?` | after the lock is acquired |
| `tick_start` | `tick`, `pid`, `timeout_at` | a tick is launched |
| `tick_end` | `tick`, `verdict`, `cause`, `rc`, `dur`, `by_model` | a tick returns |
| `sleep` | `tick`, `until`, `reason` ∈ `between-ticks\|backoff\|rate-limit\|memory` | any harness sleep |
| `memory_pressure` | `free_mb`, `swap_used_pct`, `action` | the pre-tick guard runs |
| `incident` | `id`, `kind`, `severity` ∈ `warn\|error\|needs-human`, `detail`, `tick` | see the medic section |
| `medic_start` / `medic_end` | `id` / `id`, `outcome`, `summary` | around a medic run |
| `migration` | `from`, `to`, `actions`, `plugin_version` | once per schema step when a launch upgrades an older loop dir (after `loop_start`, before the first tick) |
| `loop_end` | `reason` ∈ `done\|halt\|paused\|rate-limit-exit\|error\|signal\|needs-human`, `detail`, `exit_code` | every exit path of the lock owner (a refused second harness raises `incident kind=lock-conflict` instead) |
| `phase_end` | `phase`, `rc`, `dur`, `session_id`, `transcript` | every phase returns, including a killed one |
| `artifact` | `tick`, `task`, `name`, `path` | the render gate archives a screenshot |
| `decision` | `task`, `decision`, `classification` | the Judge settles something without a human |
| `resume` | `task`, `attempt` | a timed-out Worker is continued from its session |
| `split` | `task`, `into` | an oversized task becomes sub-rows |
| `render_app_start` | `pid`, `cmd` | the render recipe's app is started (once per run) |
| `paused`, `role_start`, `role_end`, `handoff`, `tool`, `task_status`, `plan_oversize` | unchanged | as before |

None of `phase_end`, `artifact`, `decision`, `resume`, `split` or `render_app_start` is a lifecycle type, so none of them can change the derived status — they are per-tick or per-run detail the dashboard renders, never evidence that the loop is alive.

Incident kinds: `harness-crash`, `tick-killed`, `tick-timeout`, `tick-stalled`, `garbage-ticks`, `no-progress`, `halt-sentinel`, `lock-conflict`, `git-divergence`, `dashboard-crashloop`, `migration-blocked`, `schema-newer`. The medic's per-kind action and outcome table lives in `skills/agent-loop-medic/SKILL.md`.

### Operator notes

- **Liveness is `runtime/harness.json` + `HEARTBEAT`, never `pgrep`.** The harness is alive when the pid in `harness.json` passes `kill -0` **and** `ps -o command= -p PID` mentions `run.sh`, **and** `HEARTBEAT` is younger than 3×`HB_INTERVAL`. `pgrep run.sh` / `ps | grep run.sh` from inside a Claude session matches the session's own tool calls and reports a phantom harness — that is how the 1.x "concurrent loop harnesses" false alarms happened. The skills, the dashboard and the medic all use the pid test.
- **One harness per `LOOP_DIR`, enforced by the harness.** In 1.x the only mutex was a `runtime/LOCK` file the tick prompt asked the model to write and check by pid; a second `bash run.sh` started instantly and two harnesses raced on the same plan. The lock now lives in the harness (`runtime/harness.json`, hard-linked into place from a finished payload so a lock can never be read half-written; 2.0.x's `mkdir harness.lock.d` + write-json had a window in which two starters could both take over one dead harness — found by the concurrent-takeover test flaking on CI), a second launch exits 3 with the owner's pid, and a stale lock from a crashed harness is taken over with an `incident kind=harness-crash` so the medic reconciles orphans (with `Medic: notify|off` it is a warning and the loop continues — the next tick re-evaluates an orphaned `[~]` itself).
- **Status cannot lie.** 1.x derived `halted`/`done` by grepping the last 4 KB of `run.log` for `HALT:`/`LOOP_DONE` — text that legitimately appears in a Worker prompt or the orchestrator's reasoning, which produced "HALTED" during an ordinary hand-off between agents. Terminal states now require a `loop_end` event; nothing reads the harness's log for state. (`run.log` no longer exists at all — see Artefacts.)
- **One dashboard per loop dir, and it is the launcher.** The 2.0 first cut made the dashboard a sidecar the harness owned and told operators to launch from a separate terminal — which produced a second dashboard on a new port every time. Retracted, along with its rationale: a tick's RAM is its own whichever parent it has. What matters is that the loop outlives the Claude session, and `serve.py --detach` (new process session) gives that without terminals. The harness adopts a live dashboard; the dashboard spawns the harness in its own session; neither takes the other down.
- **Host memory hogs.** A repeated `tick-killed` (rc=137) is the OS killing the tick for RAM, not the loop misbehaving. On a developer Mac the usual residents are a Docker / `Virtualization.framework` VM and the `vercel.turbo-vsc` daemon (multiple GB each) plus idle interactive Claude sessions (~400 MB each). The memory guard delays ticks while headroom is low but cannot create headroom; the medic names these in `human_next_step`.
- **Why the dashboard used to be a hog, and no longer is.** 1.x `serve.py` re-read and re-parsed the whole `events.jsonl` (tens of MB after a long run) once per second, per connected browser, on its own thread — several × the file size in Python objects churned every second, which is the most plausible reason it was the recurring OOM victim and competed with the tick for RAM. 2.0 keeps an incremental tail with an offset, folds finished ticks to aggregates, and serves one cached snapshot to every client. Memory is O(current tick + number of ticks).

- **Pause vs Stop now.** Two sentinels, both read by the harness at every phase boundary; neither kills anything mid-write. **Pause** (`⏸`, `POST /api/pause`, or `touch <loop-dir>/runtime/PAUSE`) lets the harness finish the **phase** in flight, write `runtime/CHECKPOINT.json`, emit `paused` and exit 0. **Stop now** (the dashboard button, or `POST /api/stop`) writes `runtime/STOP`, which makes the harness SIGTERM the phase in flight first — a Worker gets its wrap-up continuation so its checkpoint is current — and then behave exactly as Pause. Reach for Stop now when a phase is burning budget on the wrong thing: it lands in minutes rather than at the end of the tick. `/api/stop` refuses with HTTP 409 when no harness is alive, because a sentinel nobody reads would only stop the *next* launch. Resume deletes **both** sentinels; if you stop the loop by hand, delete `runtime/STOP` as well as `runtime/PAUSE` or the next launch exits immediately.

**Upgrading a machine that is mid-run.** Land v3 with the suite green and no loop running on the machine you built it on. On the loop machine: pull, then pause the running 2.1 harness at a tick boundary (`touch <loop-dir>/runtime/PAUSE`, wait for its terminal to exit, delete the file), and relaunch — `run.sh` migrates the dir to schema 3 on the way up. The dashboard needs no restart; it re-reads the dir. For the first real v3 run set `Decision policy: conservative` for one segment and read `LOOP_DECISIONS.md` before flipping to `autonomous` — the ledger is the thing you are learning to trust, and a segment is enough to see whether its entries are sane. When the run closes, have the postmortem compare $/task, wall-clock/task, human touches and needs-human count against the last 2.x run on the same repo; those four numbers are what v3 claims to move.

### Upgrading (loop-dir schema)

A loop dir outlives plugin versions, so its **layout** carries its own integer version: `LOOP_SCHEMA` in `runner/migrate.py`, stamped in `runtime/schema`. The stamp is independent of the plugin semver — it bumps only when a file name, format or sentinel under the loop dir changes meaning. Prompt, UI and harness-internal changes never bump it. Nothing but the harness writes the stamp.

Every launch checks the stamp right after `loop_start`. A dir with no stamp and a `runtime/tickseq` was started by a pre-2.0 plugin (schema 1); a dir with neither is new and is stamped without a migration. An older dir is migrated in place, one step at a time, each step writing the stamp and a `migration` event, so an interrupted upgrade resumes where it stopped. Migrations never edit `LOOP_CONFIG.md` or `LOOP_PLAN.md`. Two refusals exit 2 with `runtime/NEEDS_HUMAN.md`, and no medic runs for either because the fix is a human action:

- `migration-blocked` — the dir is schema 1 and `run.log` was written in the last two minutes without ending in a 1.x exit line, which usually means the old harness is still running (1.x wrote no pid file, so this is the only evidence there is). Pause it at a tick boundary (`touch <loop-dir>/runtime/PAUSE`, wait for its terminal to exit, delete the file), kill its dashboard, relaunch. `LOOP_MIGRATE_FORCE=1` skips the guard once you have confirmed it is dead.
- `schema-newer` — an older plugin was pointed at a newer dir. Update the plugin on that machine.

| step | what changes on disk | why |
| --- | --- | --- |
| 1 → 2 | `runtime/LOCK` removed (`actions=legacy-lock-removed`, or `none`) | 1.x asked the tick prompt to write and check a pid lock; 2.0's mutex is the harness's own lock (`runtime/harness.json` since 2.1.0), and nothing reads the old file. Tick numbering, plan, config, learnings and the usage ledger carry over unchanged; ticks recorded before the migration show no `cause` because 1.x did not record one. |
| 2 → 3 | `artifacts/` and `LOOP_DECISIONS.md` created; `runtime/STOP` becomes a sentinel the harness reads; `harness.log` replaces `run.log` as the harness's own log; `runtime/task-<T>.json` replaces `attempts-<T>.json` as the per-task attempt/state record; contracts gain `forbidden[].source`, `render_gate`, `fidelity_source`, `evaluator_must_read`, `evaluator_must_view` | v3 runs phases rather than ticks, so a stop needs a second sentinel and screenshots need somewhere durable to live. Migration creates the two empty files and leaves everything else untouched: a v2 `LOOP_PLAN.md`, `LOOP_CONFIG.md`, learnings and usage ledger all keep working, and an existing `run.log` is left in place (the dashboard falls back to it). Contracts written by v2 are re-validated and re-scouted per task; none is rewritten in place. **A dir stopped mid-task upgrades and resumes**: boot reads the `[~]` task's `task-<T>.json` and continues from its recorded phase, and a task with no state file (a 2.x dir, or a crash before the first write) goes to the Judge with `failure="boot-reconcile"` (spec §14) — no human step is required. |

**Authoring rule.** A change that alters anything a loop dir contains — a file name, a format, a sentinel's meaning, a runtime file another component reads — bumps `LOOP_SCHEMA`, adds `migrate_<k>_to_<k+1>` in `runner/migrate.py` with a case in `migrate_loop_dir`, adds cases to `tests/runner/test_migrate.py`, and adds a row to the table above. A change that does not touch the dir's contents does not bump it, however large.

### Safety posture

- Phases run headless with `--dangerously-skip-permissions`, inside the OS sandbox, confined to a git **worktree**. `run.sh` enforces a worktree guard: it refuses to run if `pwd` does not equal the configured `Worktree:`.
- Every phase has its own wall-clock cap, enforced by the harness in Python — there is no dependency on `timeout`/`gtimeout` for it any more. `tick_timeout` survives as an outer sanity cap and as the figure the dashboard displays.
- After the Worker returns, SANDBOX reverts any changed path outside the Scout's `allow_list` — both halves of a rename, honouring the protected loop-dir paths and the files that were already dirty before the phase started — and only then does GATE run. Only a clean gate (plus an Evaluator PASS, with one observation per screenshot it was told to view) commits.
- The Judge may widen a constraint, but only one the **Scout** invented: `forbidden` entries carry a `source`, and a `plan`- or `spec`-sourced entry is never removable by a model. That is the difference between the loop unblocking itself and the loop overruling you.

**Deferred: concurrency and async evaluation.** The Evaluator stays a synchronous phase in v3, on the critical path of every tick — see spec §15 for why (moving it off the path is unsafe across a dependency edge until every task a later one depends on has been *evaluated*, not merely committed) and the edge-case ledger a future implementation must honour. Not designed or scheduled; recorded so it is not re-litigated from scratch.

### Dynamic model selection

The governing rule is unchanged — **use the least powerful model that can handle each phase** — but in v3 it is enforced in code, not asked for in a prompt. Each phase is dispatched with an explicit `--model`, resolved from the `Tiers:` line at launch time. The plugin hardcodes **no** model names; `Tiers:` is the only place an alias ever appears, which makes swapping in a new frontier model a one-word edit.

Defaults, and why the leverage sits where it does:

- **Scout — standard, not cheap.** The contract is the highest-leverage document in a tick: it fixes the allow-list, the success criteria, the render gate and the fidelity check. In the analysed run the three defective contracts were all written by the cheap tier, and one of them invented a `forbidden` entry that then blocked three Workers.
- **Worker — standard, escalated one tier on the second attempt by the harness.** v2 asked the model to escalate itself and it retried on the same tier 6 times out of 7.
- **Evaluator — class-governed.** `| mechanical` skips it, `| complex` runs it most-capable, everything else standard. A tier set in `LOOP_CONFIG.md` is a ceiling for `| complex` only.
- **Judge, Planner, Reviewer — most-capable.** These are the only phases making a judgement call, and the Judge runs only on failure paths.
- **Learner — cheap.** It summarises evidence it is handed.

The Evaluator still returns `PASS | NEEDS_WORK | BLOCKER`, where `BLOCKER` means the diff only "passes" via a workaround or a spec deviation — but it now also owes one observation per screenshot it was told to view, and the harness rejects a verdict that is missing one. That, rather than policing tool calls, is what stopped both v2 Evaluators reading zero reference files on a copy task.

## Tests

```bash
bash tests/all.sh
```

Runs `run.e2e.test.sh` (against a scripted stub `claude` that can time a phase out, return malformed JSON, or answer a `--resume`), the four prompt/skill contract suites (`prompts.contract.sh`, `setup.contract.sh`, `medic.contract.sh`, `postmortem.contract.sh`), the runner unit tests (`python3 -m unittest discover -s tests/runner` — plan grammar round-trip against serve.py's regexes, contract validation, the stream parser, budgets and wrap-up, Judge decisions under both policies, the fidelity ratio, SANDBOX revert, commit trailers), `serve.test.py`, and `web.contract.sh`. Python suites are skipped non-fatally when `python3` is absent.

The contract suites pin promises, not implementations: the medic's allowlist and outcomes; the attach skill's Monitor command, its "never `pgrep`" rule and the Pause-vs-Stop-now distinction; each role brief's JSON output shape, its non-interactivity rule, and the absence of any `<<LOOP_*>>` sentinel. Linting is repo-wide via `scripts/lint.sh`, run once by `scripts/test-all.sh`, not per-plugin. Note: the shell suites use `mktemp -d`; if the sandbox blocks it, run with the sandbox disabled.
