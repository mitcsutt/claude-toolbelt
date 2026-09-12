# agent-loop

An autonomous coding loop for long-running, multi-task work (refactors, new features, test sweeps).

A bash harness (`run.sh`) drives a `while` loop that re-invokes a **fresh headless `claude --print` tick per task**. Each tick reads `tick-prompt.md`, picks one task, does the work, commits, and exits. Because every tick is a brand-new process, the loop gets an **OS-level context reset between tasks** — no growing session, no compaction drift.

Contrast with v1: v1 ran one long-lived in-session agent that self-scheduled with `ScheduleWakeup` and accumulated context; v2 replaces that with a stateless-per-task bash orchestrator.

## What it ships

| Component | Kind | What |
| --- | --- | --- |
| `/agent-loop-setup` | skill | Interactive, one-time bootstrap — config wizard, brainstorm, plan, permission gate, scaffold. |
| `/agent-loop` | skill | Attach: health from the runtime files, dashboard URL (reuses the sidecar), arms a persistent incident monitor that wakes the session to run the medic. Never starts the harness. |
| `/agent-loop-medic` | skill | Bounded triage-and-repair on an incident — headless under `run.sh` or interactive from `/agent-loop`. Allowlisted fixes only, hard budget, then hands over to a human. |
| `/agent-loop-postmortem` | skill | Wraps `/postmortem` to close out a loop, aggregating artefacts into a retrospective. |
| `run.sh` | bash harness | Owns exclusivity (`harness.json` lock) and liveness (heartbeat), drives the per-tick `while` loop, classifies tick failures, guards memory, supervises the dashboard sidecar, and spawns the medic. |
| `web/serve.py` | dashboard server | Stdlib-only Python server; incremental bounded tail of `events.jsonl`, one shared snapshot over SSE, status derived from events + PID liveness (never `run.log`). |

### Lifecycle

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

### Artefacts

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

- `harness.json` — the harness lock itself (`{pid, start_epoch, host, loop_dir, plugin_version}`). It is created with `ln` from a fully written private file, so the lock never exists without its owner; a dead owner is cleared by rename, which only one starter can win. One harness per `LOOP_DIR`; a second exits 3. There is no tick-level lock file any more — `runtime/LOCK` was removed in 2.0.0, and the 2.0.x `harness.lock.d/` dir is gone in 2.1.0 (a leftover one is ignored and tidied on release).
- `HEARTBEAT` — epoch seconds, rewritten every `HB_INTERVAL` (10 s) while the harness runs.
- `tick.json` — `{tick, pid, started_at, timeout_s}` while a tick is in flight; removed after.
- `last-activity` — epoch of the last stream line seen from the tick (stall detection).
- `dashboard.json` — `{pid, port, url, sidecar}` written by `serve.py`.
- `incident-<id>.json` / `medic-<id>.json` — one pair per incident: what the harness saw, what the medic did.
- `NEEDS_HUMAN.md` — written by the harness when it stops for a human: incident summary, medic notes, the exact resume command.
- `PAUSE` — touch to stop the loop after the in-flight tick finishes.
- `sprint-<TASK>.json` — the Scout's sprint contract for the current task (allow_list, forbidden, verification, success_criteria, inlined scout_notes).
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

The dashboard needs `python3` only (stdlib; no pip). It is **event-driven** and read-mostly: the server keeps an incremental, bounded store over `$LOOP_DIR/events.jsonl` (full detail for the current tick, per-tick aggregates for earlier ticks) and one shared snapshot thread streams to every SSE client — memory stays flat however long the run. It never reads `run.log`. It shows: a health-first **status** with the *why* (harness pid + heartbeat age, tick pid + elapsed vs timeout, dashboard uptime), the tick **phase timeline**, honest **progress** (segment-weighted while segments are unplanned — `100%` appears only when every segment is planned and every task is `[x]`/`[-]`), the **incidents** panel with each medic outcome, effort-first **usage**, and the roadmap.

Launched standalone (`python3 "${CLAUDE_PLUGIN_ROOT}/web/serve.py"`, with or without `--detach`) it has ▶ Start / ⟳ Resume / ⏸ Pause / ■ Stop; `--no-spawn` makes it a pure observer whose buttons only write/remove `runtime/PAUSE`.

### Attach: `/agent-loop`

Run **`/agent-loop`** in a Claude Code session on the repo. It reports harness/tick health from `runtime/harness.json` + `HEARTBEAT` + `tick.json` and the last `loop_end`, ensures one detached dashboard and prints its URL, and arms a **persistent `Monitor`** on `events.jsonl` filtered to `incident | loop_end | medic_end | memory_pressure`. That session is then woken on an incident instead of polling: a `needs-human` incident or a non-`done` `loop_end` runs `/agent-loop-medic` interactively and sends a push notification; `done` offers `/agent-loop-postmortem`. With no harness alive it asks one question — start from here (through the dashboard's `/api/start`, recommended) or a terminal command — with the tradeoff for each; ▶ Start in the dashboard always works too. It never runs `run.sh` itself. Without the Monitor tool it arms a background `tail -F | grep -m1` fallback that wakes the session on the first matching event.

### Medic and hand-over

On an incident the harness writes `runtime/incident-<id>.json`, emits an `incident` event, sends a desktop notification (`Medic: auto|notify`), and — with `Medic: auto` and budget left (`MEDIC_MAX_PER_RUN`, default 3) — runs `/agent-loop-medic <id>` headless, never concurrently with a tick. The medic reads the incident, the lifecycle events, and the runtime files, applies only the fixes on its allowlist (stale lock dir after verifying the pid is dead, stale runtime files, an orphaned `[~]`→`[ ]` flip, `git checkout --`/`git clean` inside the last sprint contract's `allow_list`, a dashboard restart, notes), and returns `resumed | paused | escalated | noop`. It escalates on the second occurrence of the same signature, on anything outside the allowlist, and on a halt sentinel or git divergence by definition. `resumed` → the loop continues. `paused`/`escalated`/no answer within `MEDIC_TIMEOUT` → the harness writes `runtime/NEEDS_HUMAN.md` (incident, medic notes, the exact resume command), emits `loop_end reason=needs-human`, and exits **2**. The medic never edits product code and never starts a harness.

### Exit codes

`0` done / paused / rate-limit-exit · `1` halt / error · `2` needs-human (read `runtime/NEEDS_HUMAN.md`, fix, re-run) · `3` lock-conflict (another harness owns this `LOOP_DIR`; its pid is printed — stop it first). Two of the exit-2 kinds come from the schema check, not from a tick: `migration-blocked` and `schema-newer` — see [Upgrading](#upgrading-loop-dir-schema).

### Progress & usage

- Ticks run with `--output-format stream-json`. The terminal shows a **glanceable** view, not the raw trace:
  - While a tick runs, a single self-updating heartbeat line (spinner · current activity · tool count · elapsed) proves liveness.
  - When a tick finishes, one permanent summary line scrolls into history: `✓ t3 T26 · 3m12s · lint✓ tsc✓ build✓ test✓ · → a1b2c3   62% (26/42)` (verdict glyph, task, duration, verification gates from the commit's `Loop-Verification` trailer, short SHA, and **percentage-first** progress).
  - A session header prints each tick: `── loop · 62% ███████░░░░░ 26/42 · ⏱ 1h18m · ~45m left · 5h 90% ↺2h12m ──` (task percent + bar, count, elapsed, rough ETA from average tick time, and the usage-window utilization). The `5h 90%` segment is the 5-hour rate-limit window utilization Claude reports on `rate_limit_event` lines, with time-to-reset (`↺`); it's labelled to distinguish it from the leading task percentage, and is omitted until the stream reports it (Claude only includes `utilization` near the warning threshold). No dollar figure — `cost_usd` is raw list-price and is never summed or projected.
  - The full per-tool/per-text trace still tees to `$LOOP_DIR/run.log` (`tail -f`), and `LOOP_VERBOSE=1` restores it to the terminal in place of the heartbeat. Piped/non-TTY runs stay quiet (header + summary lines only).
- The harness reads the `rate_limit_event` from each tick. When the usage window is exhausted it **auto-waits until the reset** (`resetsAt`) and resumes; if the reset is further out than `MAX_WAIT` (default 6h, e.g. a weekly window) it logs the reset time and **exits cleanly** so you can re-run `run.sh` later. Resume is safe at any point: work is committed per task, an orphaned `[~]` is re-evaluated by the next tick (or reset by the medic), and the Worker's checkpoint file survives.
- The loop also self-terminates on `LOOP_DONE`, a tick `LOOP_HALT`, 3 consecutive failed/garbage ticks, or 3 consecutive `CONTINUE` ticks with no drop in remaining tasks (no-progress guard) — each of these raises an incident first, so the medic gets a look before the harness stops.
- Each `tick_end` carries a `cause` (`ok | timeout | killed | terminated | api_error | rate_limit | no_sentinel | crashed`), so the summary line distinguishes `killed (SIGKILL — likely OS memory pressure)` from a tick that produced nothing. A pre-tick memory guard delays (never halts) while free RAM is below `MEM_MIN_MB` and swap above `SWAP_MAX_PCT`, emitting `sleep reason=memory` so the dashboard says "waiting for memory headroom" rather than "stalled".

## Configuration

### Config

Set in `LOOP_CONFIG.md` (under `.claude/loop/<run-id>/`):

- **Limits** (one line): `tick_timeout` (per-tick seconds — rabbit-hole kill for a single stuck tick). There is **no cost/iteration/wall-clock budget.** The subscription usage window is the only ceiling.
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
| `LOOP_DASHBOARD_OPEN` | unset | `1` → `open`/`xdg-open` the URL once. |
| `LOOP_DASHBOARD_CMD` | serve.py | Full sidecar command override (tests, or a different observer). |
| `LOOP_MEDIC_CMD` | claude | Full medic command override; default `claude --print --dangerously-skip-permissions [--model M] "/agent-loop-medic <id>"`. |
| `LOOP_ORCHESTRATOR_MODEL` | config | One-off override of `Orchestrator model:`. |
| `LOOP_MIGRATE_FORCE` | unset | `1` → run a pending schema migration even though `run.log` looks like a pre-2.0 harness is still writing it. Only after you have confirmed that harness is dead. |
| `LOOP_VERBOSE` | unset | `1` → raw stream to the terminal instead of the heartbeat line. |
| `MAX_WAIT` | `6h` | Longest rate-limit reset the harness will wait for before exiting cleanly. |

### Events (`events.jsonl`)

One JSON object per line, every one carrying `t` (epoch), `seq` (monotonic per harness process) and `type`. `events.jsonl` is the single source of truth for status; `run.log` is forensic only.

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
| `paused`, `role_start`, `role_end`, `handoff`, `tool`, `task_status`, `plan_oversize` | unchanged | as before |

Incident kinds: `harness-crash`, `tick-killed`, `tick-timeout`, `tick-stalled`, `garbage-ticks`, `no-progress`, `halt-sentinel`, `lock-conflict`, `git-divergence`, `dashboard-crashloop`, `migration-blocked`, `schema-newer`. The medic's per-kind action and outcome table lives in `skills/agent-loop-medic/SKILL.md`.

### Operator notes

- **Liveness is `runtime/harness.json` + `HEARTBEAT`, never `pgrep`.** The harness is alive when the pid in `harness.json` passes `kill -0` **and** `ps -o command= -p PID` mentions `run.sh`, **and** `HEARTBEAT` is younger than 3×`HB_INTERVAL`. `pgrep run.sh` / `ps | grep run.sh` from inside a Claude session matches the session's own tool calls and reports a phantom harness — that is how the 1.x "concurrent loop harnesses" false alarms happened. The skills, the dashboard and the medic all use the pid test.
- **One harness per `LOOP_DIR`, enforced by the harness.** In 1.x the only mutex was a `runtime/LOCK` file the tick prompt asked the model to write and check by pid; a second `bash run.sh` started instantly and two harnesses raced on the same plan. The lock now lives in the harness (`runtime/harness.json`, hard-linked into place from a finished payload so a lock can never be read half-written; 2.0.x's `mkdir harness.lock.d` + write-json had a window in which two starters could both take over one dead harness — found by the concurrent-takeover test flaking on CI), a second launch exits 3 with the owner's pid, and a stale lock from a crashed harness is taken over with an `incident kind=harness-crash` so the medic reconciles orphans (with `Medic: notify|off` it is a warning and the loop continues — the next tick re-evaluates an orphaned `[~]` itself).
- **Status cannot lie.** 1.x derived `halted`/`done` by grepping the last 4 KB of `run.log` for `HALT:`/`LOOP_DONE` — text that legitimately appears in a Worker prompt or the orchestrator's reasoning, which produced "HALTED" during an ordinary hand-off between agents. Terminal states now require a `loop_end` event; nothing reads `run.log` for state.
- **One dashboard per loop dir, and it is the launcher.** The 2.0 first cut made the dashboard a sidecar the harness owned and told operators to launch from a separate terminal — which produced a second dashboard on a new port every time. Retracted, along with its rationale: a tick's RAM is its own whichever parent it has. What matters is that the loop outlives the Claude session, and `serve.py --detach` (new process session) gives that without terminals. The harness adopts a live dashboard; the dashboard spawns the harness in its own session; neither takes the other down.
- **Host memory hogs.** A repeated `tick-killed` (rc=137) is the OS killing the tick for RAM, not the loop misbehaving. On a developer Mac the usual residents are a Docker / `Virtualization.framework` VM and the `vercel.turbo-vsc` daemon (multiple GB each) plus idle interactive Claude sessions (~400 MB each). The memory guard delays ticks while headroom is low but cannot create headroom; the medic names these in `human_next_step`.
- **Why the dashboard used to be a hog, and no longer is.** 1.x `serve.py` re-read and re-parsed the whole `events.jsonl` (tens of MB after a long run) once per second, per connected browser, on its own thread — several × the file size in Python objects churned every second, which is the most plausible reason it was the recurring OOM victim and competed with the tick for RAM. 2.0 keeps an incremental tail with an offset, folds finished ticks to aggregates, and serves one cached snapshot to every client. Memory is O(current tick + number of ticks).

### Upgrading (loop-dir schema)

A loop dir outlives plugin versions, so its **layout** carries its own integer version: `LOOP_SCHEMA` in `lib/migrate.sh`, stamped in `runtime/schema`. The stamp is independent of the plugin semver — it bumps only when a file name, format or sentinel under the loop dir changes meaning. Prompt, UI and harness-internal changes never bump it. Nothing but the harness writes the stamp.

Every launch checks the stamp right after `loop_start`. A dir with no stamp and a `runtime/tickseq` was started by a pre-2.0 plugin (schema 1); a dir with neither is new and is stamped without a migration. An older dir is migrated in place, one step at a time, each step writing the stamp and a `migration` event, so an interrupted upgrade resumes where it stopped. Migrations never edit `LOOP_CONFIG.md` or `LOOP_PLAN.md`. Two refusals exit 2 with `runtime/NEEDS_HUMAN.md`, and no medic runs for either because the fix is a human action:

- `migration-blocked` — the dir is schema 1 and `run.log` was written in the last two minutes without ending in a 1.x exit line, which usually means the old harness is still running (1.x wrote no pid file, so this is the only evidence there is). Pause it at a tick boundary (`touch <loop-dir>/runtime/PAUSE`, wait for its terminal to exit, delete the file), kill its dashboard, relaunch. `LOOP_MIGRATE_FORCE=1` skips the guard once you have confirmed it is dead.
- `schema-newer` — an older plugin was pointed at a newer dir. Update the plugin on that machine.

| step | what changes on disk | why |
| --- | --- | --- |
| 1 → 2 | `runtime/LOCK` removed (`actions=legacy-lock-removed`, or `none`) | 1.x asked the tick prompt to write and check a pid lock; 2.0's mutex is the harness's own lock (`runtime/harness.json` since 2.1.0), and nothing reads the old file. Tick numbering, plan, config, learnings and the usage ledger carry over unchanged; ticks recorded before the migration show no `cause` because 1.x did not record one. |

**Authoring rule.** A change that alters anything a loop dir contains — a file name, a format, a sentinel's meaning, a runtime file another component reads — bumps `LOOP_SCHEMA`, adds `migrate_<k>_to_<k+1>` in `lib/migrate.sh` with a case in `migrate_loop_dir`, adds cases to `tests/migrate.test.sh`, and adds a row to the table above. A change that does not touch the dir's contents does not bump it, however large.

### Safety posture

- Ticks run headless with `--dangerously-skip-permissions`, inside the OS sandbox, confined to a git **worktree**. `run.sh` enforces a worktree guard: it refuses to run if `pwd` does not equal the configured `Worktree:`.
- The per-tick wall-clock cap (`tick_timeout`) requires `timeout` or `gtimeout` on PATH. If neither is present the harness still runs but logs a warning and the cap is **disabled** — a stuck tick can run unbounded. Install coreutils (`brew install coreutils` provides `gtimeout` on macOS) to restore it.
- After the Worker returns, the tick reverts any changed path outside the Scout's `allow_list`, then runs parent-side verification. Only a clean parent-side run (plus an Evaluator PASS) commits.

### Dynamic model selection

The governing rule is **use the least powerful model that can handle each role/task**. Tier is matched to complexity (cheap for read-only scouting and mechanical edits, most-capable for evaluation/judgement), resolved to the cheapest capable model alias at dispatch, honouring any tier overrides in `LOOP_CONFIG.md`. On a reasoning-gap failure the tick **escalates one tier** on re-dispatch rather than retrying the same model unchanged.

The plugin hardcodes **no** model names — everything is expressed in tiers (`cheap | standard | most-capable`), and the only place a concrete model alias is ever named is the value you write into `LOOP_CONFIG.md`. The split:

- **Orchestrator** (the per-tick process) is a coordination + verification spine: it reads state, runs the verification pipeline, dispatches the role subagents, and does the bookkeeping. It needs no top-tier reasoning. Set `Orchestrator model:` in `LOOP_CONFIG.md` (or the `LOOP_ORCHESTRATOR_MODEL` env var for a one-off) to run it at a standard tier; blank inherits your Claude default.
- **Planner** (PLAN ticks) and **Evaluator** (review) ride the **most-capable** tier — plan decomposition and the workaround-vs-legit-pass judgement are the highest-leverage reasoning in the loop, so the thinking budget is concentrated there rather than spread across the mechanical spine.
- The **Evaluator** returns `PASS | NEEDS_WORK | BLOCKER`; `BLOCKER` means the diff only "passes" via a workaround/spec-deviation. Keeping that subtle call on the most-capable Evaluator is what lets the orchestrator safely run cheaper — it just routes on the verdict.

## Tests

```bash
bash tests/all.sh
```

Runs `lib.test.sh`, `harness.test.sh`, `events.test.sh`, `run.e2e.test.sh`, the `tick-prompt.contract.sh`, `setup.contract.sh`, `postmortem.contract.sh`, and `medic.contract.sh` suites, `serve.test.py` (skipped non-fatally if `python3` is absent), and `web.contract.sh`. The contract suites pin the skills' promises: the medic's allowlist, forbidden list, outcomes and output keys; the attach skill's Monitor command and "never `pgrep`" rule; the tick prompt's lock-free boot. Linting is repo-wide via `scripts/lint.sh`, run once by `scripts/test-all.sh`, not per-plugin. Note: `lib.test.sh` and `run.e2e.test.sh` use `mktemp -d`; if the sandbox blocks it, run with the sandbox disabled.

