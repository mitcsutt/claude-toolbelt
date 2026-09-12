# agent-loop v2 — resilience, honest status, dashboard refresh, and the medic

**Date:** 2026-09-12
**Status:** approved by default (autonomous session — every decision below is
overridable; see §9 "Decisions made without you").
**Scope:** `plugins/agent-loop` only. Host-level memory hygiene (Docker VM,
turbo daemon) is documented, not engineered.

---

## 1. What is actually wrong

The handoff (`~/Downloads/agent-loop-memory-handoff.md`) framed this as "host
memory pressure → OOM → double harness → HALT". That chain is real, but three of
its links are plugin defects, not environment. Read in order of blast radius:

### 1.1 There is no harness-level lock
`run.sh` acquires nothing. The only mutex is `runtime/LOCK`, which the **tick
prompt asks the LLM to write** and to verify by PID (tick-prompt §2). A second
`bash run.sh` on the same `LOOP_DIR` starts instantly. That is the double-harness
race. The `concurrent loop harnesses detected` HALT quoted in the handoff is not
in this repo's `run.sh` — it was emitted by a tick that noticed the mess, or by a
different plugin version on that machine. Either way, exclusivity must be owned
by the harness, not delegated to a model.

### 1.2 Status is a substring grep over an LLM stream
`serve.py::event_verdict` returns `halted` when `"HALT:" in last_log` and `done`
when `"LOOP_DONE" in last_log`, where `last_log` is the last 4 KB of `run.log`.
`run.log` is the raw `stream-json` tee: every assistant message, every subagent
dispatch prompt, every tool input. A Worker prompt that says "if you cannot,
report `<<LOOP_HALT:…>>`" — or the orchestrator reasoning about §12 — puts
`HALT:` into the tail exactly at the hand-off between agents. That is the
"HALTED when it's just between agents" symptom. The same code path also derives
`stalled` from the LLM-written `LOCK` file plus a 45 s event-silence threshold.

### 1.3 The dashboard is a genuine memory/CPU hog
`_State.snapshot()` and `build_snapshot()` each call
`_read(events.jsonl)` + `tail_events_from_text` — **the whole file, parsed
twice, once per second, per connected SSE client, on its own thread**.
`events.jsonl` grows without bound (one line per tool call, for the whole run;
two harnesses for 18 h makes it tens of MB). Python objects for a multi-MB JSONL
parse are several × the file size, allocated and freed every second. This is the
most plausible reason `serve.py` is the recurring OOM victim, and it competes
with the tick for RAM. The incremental `tail_events(fp, offset)` exists and is
tested — the hot path just never uses it.

### 1.4 Secondary contributors
- `format_stream` forks 2–4 `jq` processes **per stream line**; `emit_event` forks
  another per event. Thousands of forks per tick. CPU, not RSS, but it hurts on a
  swap-bound box.
- `timeout $TICK_TIMEOUT` sends SIGTERM only; a wedged `claude` under memory
  pressure can ignore it. No `-k` grace kill.
- rc=124 (timeout), rc=137 (SIGKILL — the OOM signature), rc=143 (SIGTERM) and a
  garbage result all classify as the same `retry`, so the operator cannot tell
  "environment killed it" from "the model produced nothing".
- `100% (48/48)` in the header/dashboard counts **planned rows only**. With nine
  unplanned segments left, that reads as "finished".
- The dashboard is launched only by `/agent-loop`, unsupervised; when it dies
  nothing restarts it and nothing says so.
- Incidents reach a human only if a human is polling. The interactive session
  the user keeps open to "have an agent to talk to" costs ~435 MB of RAM and
  still can't hear the loop.

## 2. Goals and non-goals

**Goals**
1. One harness per `LOOP_DIR`, enforced by the harness, verified by PID + start
   time, with a heartbeat — never by `pgrep`.
2. A status model that cannot say HALTED or DONE unless the harness emitted a
   terminal lifecycle event, and that always shows *why* it says what it says.
3. A dashboard whose memory is bounded regardless of run length.
4. The dashboard is an opt-in sidecar of `run.sh` (supervised, auto-restarted),
   so it is available on every launch, not only via the skill.
5. Incidents are pushed: to a headless **medic** tick with a hard budget, and to
   any attached interactive session via a `Monitor` wake-up. The medic knows when
   to stop fixing and hand over to a human.
6. A redesigned dashboard: health first, phase timeline, honest progress,
   incidents panel, light/dark, low render cost.

**Non-goals**
- Fixing the host (Docker VM, `vercel.turbo-vsc`). Documented in the README
  operator notes and in the medic's escalation text.
- Changing the Planner/Scout/Worker/Evaluator model or the sprint-contract flow.
- A persistent daemon or launchd service.

## 3. Architecture

```
run.sh (owner)  ── harness.json lock + HEARTBEAT ──► runtime/
   │  spawns ── claude --print tick ── stream ──► format_stream ──► events.jsonl
   │  spawns ── serve.py --no-spawn (sidecar, supervised)   ▲          │
   │  spawns ── claude --print /agent-loop-medic (on incident, budgeted)
   │                                                        │          ▼
/agent-loop (interactive) ── Monitor(events.jsonl) ◄────────┴── incident / loop_end
```

Ownership rules:
- **run.sh owns exclusivity, liveness, and lifecycle events.** The tick prompt no
  longer manages a PID lock. serve.py never spawns a harness when one is alive.
- **events.jsonl is the single source of truth for status.** `run.log` is
  forensic only; nothing derives state from it.
- **serve.py is read-mostly.** It keeps an incremental, bounded event store and
  one shared snapshot; it verifies liveness by PID (`os.kill(pid, 0)` + `ps`
  command check) and heartbeat age.

## 4. Harness (`run.sh`, `lib/loop.sh`, `lib/events.sh`)

### 4.1 Exclusivity
- `runtime/harness.json` is the lock: `{pid, start_epoch, host, loop_dir,
  plugin_version}`. Acquired atomically — since 2.1.0 by `ln` of a finished private payload into `runtime/harness.json` (the file IS the lock; 2.0.x's `mkdir runtime/harness.lock.d` then write-json left a window where two starters both took over one dead harness) —
  (POSIX-atomic, works on bash 3.2, no `flock` on macOS).
- On startup, if the lock dir exists: read `harness.json`; `harness_alive PID`
  = `kill -0 PID` **and** `ps -o command= -p PID` contains `run.sh` (defeats PID
  reuse). Alive → exit code **3**, emit `incident kind=lock-conflict severity=warn
  detail="pid N alive since <iso>; refused second harness"` (an `incident`, not a
  `loop_end` — the events file belongs to the live harness, and a `loop_end` there
  would flip the healthy loop's dashboard to `stopped`), print the owner's PID and
  how to stop it. Dead →
  log "stale harness lock (pid N dead) — taking over", emit
  `incident kind=harness-crash` (the previous owner died without cleanup; the
  medic reconciles orphans), then acquire.
- `trap` on EXIT/INT/TERM: kill the in-flight tick (if any) and the sidecar,
  stop the heartbeat, emit `loop_end` with the right reason, remove the lock.

### 4.2 Heartbeat and per-tick liveness
- A background subshell writes the epoch to `runtime/HEARTBEAT` every
  `HB_INTERVAL` (10 s). "Harness alive" for observers = PID alive **and**
  heartbeat age < 3×`HB_INTERVAL`.
- The tick is launched in the background through a FIFO so the harness knows its
  PID (bash 3.2 cannot `wait` on process substitution):
  ```
  mkfifo "$FIFO"
  format_stream … < "$FIFO" > "$RESULT_FILE" &   fmt_pid=$!
  run_tick "${claude_cmd[@]}" <<<"$TICK_INPUT" > "$FIFO" 2>>"$RUNLOG" &   tick_pid=$!
  write runtime/tick.json {tick, pid: tick_pid, started_at, timeout_at}
  wait "$tick_pid"; rc=$?; wait "$fmt_pid"; out="$(cat "$RESULT_FILE")"
  ```
- `timeout -k 30 "$TICK_TIMEOUT"` (SIGKILL 30 s after SIGTERM).
- `format_stream` writes `runtime/last-activity` (epoch) on every stream line.
  Stall detection (harness-side): if a tick is in flight and last-activity is
  older than `STALL_S` (default 300) the harness emits `incident kind=tick-stalled`
  once per tick; the tick itself is left to `tick_timeout`.

### 4.3 Failure classification
`classify_cause <rc> <json> <sentinel>` → one of
`ok | timeout | killed | terminated | api_error | rate_limit | no_sentinel | crashed`
(124→timeout, 137→killed, 143→terminated, `is_error`+429→rate_limit, other
`is_error`→api_error, rc≠0 no result→crashed, rc=0 no sentinel→no_sentinel).
`tick_end` carries `cause` and `rc`. The console summary line says e.g.
`✗ t12 T34 · 30m00s · killed (SIGKILL — likely OS memory pressure)`.

### 4.4 Structured lifecycle events (new/changed)
| event | fields | emitted when |
| --- | --- | --- |
| `loop_start` | pid, host, plugin_version, resume(bool), dashboard_url? | after lock acquired |
| `tick_start` | tick, pid, timeout_at | unchanged + pid/timeout |
| `tick_end` | tick, verdict, cause, rc, dur, by_model | unchanged + cause/rc |
| `sleep` | tick, until, reason ∈ between-ticks\|backoff\|rate-limit\|memory | any harness sleep |
| `memory_pressure` | free_mb, swap_used_pct, action | pre-tick guard trips |
| `incident` | id, kind, severity ∈ warn\|error\|needs-human, detail, tick | see §6.2 |
| `medic_start` / `medic_end` | id, outcome ∈ resumed\|paused\|escalated\|noop, summary | medic run |
| `loop_end` | reason ∈ done\|halt\|paused\|rate-limit-exit\|error\|signal\|needs-human, detail, exit_code | every exit path of the lock owner |
Existing `role_start/role_end/handoff/tool/task_status/paused/plan_oversize`
are unchanged. `emit_event` gains a `seq` counter so consumers can detect gaps.

### 4.5 Memory guard (advisory, never halts)
`mem_headroom` (darwin: `vm_stat` free+inactive+speculative pages, `sysctl
vm.swapusage`; linux: `/proc/meminfo`) → `free_mb swap_used_pct`. Pure decision
function `mem_guard_action <free_mb> <swap_pct> <min_mb> <max_swap_pct>` →
`proceed|delay`. On `delay`: emit `memory_pressure`, `sleep MEM_BACKOFF` (60 s),
re-check up to `MEM_MAX_DELAYS` (5), then proceed regardless with a warning. Env:
`MEM_MIN_MB=1024`, `SWAP_MAX_PCT=90`. Emits `sleep reason=memory` so the dashboard
says "waiting for memory headroom" rather than "stalled".

### 4.6 Honest progress
`session_header` gains `<segments_done> <segments_total>`. When unplanned
segments remain it renders `seg 3/12 · 48/48 planned tasks`; the percent is the
segment-weighted estimate `done_segments/total_segments` (planned-task percent
inside the current segment is a second-order term and omitted). `LOOP_STATUS.md`
and the dashboard use the same numbers. `100%` appears only when every segment
is planned and every task is `[x]`/`[-]`.

### 4.7 Dashboard sidecar
- Config `Dashboard: auto | off` (template default `auto`); env `LOOP_DASHBOARD`
  overrides; `LOOP_DASHBOARD_OPEN=1` additionally runs `open`/`xdg-open` on the
  URL once.
- When on, run.sh spawns `python3 web/serve.py --no-spawn --loop-dir "$LOOP_DIR"
  --port "$(prev port from runtime/dashboard.json, else 0)"`, reads the
  `dashboard-started` line, `feed`s the URL, and records the pid.
- The heartbeat subshell also supervises it: dead → respawn on the same port
  (browser SSE reconnects), max `DASH_MAX_RESTARTS` (5) per hour, then emit
  `incident kind=dashboard-crashloop severity=warn` and stop trying.
- Killed on harness exit (the page keeps its last snapshot and shows
  "harness exited: <reason>" from the `loop_end` event it already received).
- `serve.py` launched standalone (via `/agent-loop` or by hand) keeps its
  Start/Resume buttons, but they refuse when `harness.json` names a live PID.

### 4.8 Medic hook (harness side)
Config `Medic: auto | notify | off` (template default `auto`), `Medic model:`
(blank → inherit). Budget env `MEDIC_MAX_PER_RUN=3`, `MEDIC_TIMEOUT=600`.
At the incident points in §6.2 the harness:
1. emits `incident`, writes `runtime/incident-<id>.json`;
2. `notify`/`auto`: best-effort desktop notification (`osascript -e 'display
   notification …'` on darwin, `notify-send` on linux) — one line, no markdown;
3. `auto` and budget remaining: emits `medic_start`, runs
   `timeout -k 30 $MEDIC_TIMEOUT claude --print --dangerously-skip-permissions
   [--model X] "/agent-loop-medic <id>"` with `LOOP_DIR` exported, never
   concurrently with a tick; reads `runtime/medic-<id>.json`;
4. outcome `resumed` → reset the relevant streak and continue; `paused` /
   `escalated` / no file → write `runtime/NEEDS_HUMAN.md` (incident summary +
   medic notes + exact resume command), emit `loop_end reason=needs-human`, exit
   **2**.
Budget exhausted or `off`: skip step 3 and go straight to step 4's escalation
path for error-severity incidents; `warn` incidents never stop the loop.

## 5. Dashboard server (`web/serve.py`)

- **EventStore**: incremental tail with offset; on `size < offset` (truncate /
  new run) reset. Keeps *all* lifecycle/incident/medic/task_status events (small)
  and the full event list only for the current tick; earlier ticks are folded to
  per-tick aggregates `{tick, verdict, cause, dur, tools, roles}`. Memory is
  O(current tick + number of ticks).
- **One snapshot thread** at 1 Hz; SSE handlers serve the cached bytes and send
  only when the hash changes. Zero parsing per client.
- `derive_status(store, liveness, now)` — pure, table-tested. Inputs: last
  `loop_start`/`loop_end`, last `tick_start`/`tick_end`, last `sleep`, PAUSE
  exists, harness `{pid_alive, heartbeat_age}`, tick `{pid_alive}`,
  `last_activity_age`. Output `{state, since, why, phase}`:

| state | condition | `why` example |
| --- | --- | --- |
| `running` | harness alive, tick in flight, activity age < STALL_S | "Worker (sonnet) on T34 · 3m12s · last activity 4s ago" |
| `between-ticks` | harness alive, last event `sleep between-ticks/backoff` | "next tick in 5s" / "backoff 8s after failed tick" |
| `rate-limited` | `sleep reason=rate-limit` | "5h window · resumes 10:20am" |
| `memory-wait` | `sleep reason=memory` | "1.2 GB free, swap 94% — retry in 60s" |
| `stalled` | harness alive, tick in flight, activity age ≥ STALL_S | "no activity 6m · tick killed in 24m" |
| `pausing` | PAUSE present, harness alive | "stops after tick 12" |
| `paused` | `loop_end reason=paused`, or PAUSE present and harness dead | |
| `done` | `loop_end reason=done` | |
| `halted` | `loop_end reason=halt\|error` | the detail text |
| `needs-human` | `loop_end reason=needs-human` or incident severity needs-human | medic summary |
| `crashed` | last lifecycle is `loop_start`/`tick_*` but harness pid dead or heartbeat stale > 3×HB | "harness pid 4812 gone · last heartbeat 9m ago" |
| `stopped` | `loop_end reason=signal` | |
| `idle` | no events | |

  Terminal states require a `loop_end`; **no code path reads `run.log`**.
- Snapshot adds `health: {harness:{alive,pid,heartbeat_age_s}, tick:{alive,pid,
  elapsed_s,timeout_s,activity_age_s}, dashboard:{pid,uptime_s}}`,
  `status:{state,since,why,phase}`, `incidents:[…]`, `progress.segments_done`,
  `ticks:[aggregates]`. `loop.status` stays as the state string for the tests
  that read it.
- Supervisor: `start/resume` check `harness.json` liveness first and return
  `{error:"harness pid N is alive"}`; `stop` writes PAUSE and SIGTERMs the pid
  from `harness.json` (not only its own child).
- Remove `detect_loop_status`, `event_verdict`, `loop_seems_active`'s run.log
  branch, `last_log`, `slice_current_tick`, `parse_current_activity` (dead after
  this change); update their tests.

## 6. Medic — `/agent-loop-medic`

### 6.1 Purpose
A bounded triage-and-repair agent. It exists so the loop can run unattended, and
it exists equally to *decide it cannot fix this* and stop cleanly. It never
touches product code.

### 6.2 Incident kinds (harness → medic)
| kind | raised by | default medic action |
| --- | --- | --- |
| `harness-crash` | stale lock at startup, or dashboard sees heartbeat dead | reconcile orphan `[~]`, clean half-written runtime files, verify tree clean → `resumed`. Raised at `error` only under `Medic: auto`; with `notify`/`off` it is a `warn` and the loop continues (tick-prompt §2 re-evaluates the orphan itself) |
| `tick-killed` | rc=137 | check memory snapshot; 1st: `resumed` with `sleep reason=memory` hint; 2nd consecutive: `paused` (env problem) |
| `tick-timeout` | rc=124 | 1st: `resumed`; 2nd same task: `paused` with the task named |
| `tick-stalled` | harness stall detector | warn only; no action (tick_timeout owns it) |
| `garbage-ticks` | 3 consecutive no-sentinel/crash | inspect last result/stderr; auth/CLI error → `escalated`; else `paused` |
| `no-progress` | NP_MAX guard | inspect plan for a task flipping `[~]`↔`[ ]`; `escalated` with the task id |
| `halt-sentinel` | tick printed `<<LOOP_HALT:…>>` | human decision by definition → `escalated` (summary only, no repair attempts) |
| `lock-conflict` | second harness refused | nothing to fix; `noop` |
| `git-divergence` | HEAD moved without a tick commit, or a `loop: start` commit that isn't ours | `escalated`; never resets history |
| `dashboard-crashloop` | sidecar restarts exhausted | warn only |

### 6.3 Budget and stop rules (HARD)
- Same `kind`+`detail` signature twice in a run → `escalated`.
- `MEDIC_MAX_PER_RUN` reached → harness stops calling it.
- Any action outside the allowlist → `escalated`, with the proposed action written
  in `NEEDS_HUMAN.md` for the human to run.
- Wall clock `MEDIC_TIMEOUT` → harness treats as `escalated`.

### 6.4 Allowed remediations (the complete list)
delete a stale `runtime/harness.json` **after** verifying its PID is dead; remove
stale `runtime/tick.json` / `sprint-*.json` / `worker-result.json`; flip an
orphaned `[~]` back to `[ ]` (never to `[x]`); `git checkout -- <path>` /
`git clean` **only** for paths inside the last sprint contract's `allow_list`;
restart the dashboard; write `LOOP_CLEANUP.md` / `NEEDS_HUMAN.md`; send a
desktop notification. **Forbidden:** editing any non-loop file, any `git
reset/rebase/push/branch -D`, starting a harness, editing `LOOP_CONFIG.md`,
touching `LOOP_PLAN.md` beyond the `[~]`→`[ ]` flip.

### 6.5 Output contract
`runtime/medic-<id>.json`:
`{id, kind, outcome: resumed|paused|escalated|noop, actions:[{what, evidence}],
summary, human_next_step}`. Plus a one-paragraph append to `LOOP_CLEANUP.md`
when outcome ≠ resumed.

### 6.6 Interactive mode
When invoked inside a human session (from `/agent-loop`'s Monitor wake-up, or
by hand as `/agent-loop-medic`), the same triage runs, but the skill explains
what it found, applies only allowlisted fixes, and asks before anything else. It
never spawns a harness; it prints the resume command.

## 7. Interactive skill `/agent-loop` (attach)
1. Find the loop dir (unchanged).
2. Report health from `runtime/harness.json` + `HEARTBEAT` + `tick.json` +
   the last `loop_end`. **Never `pgrep`/`ps | grep run.sh`** — the skill says so
   explicitly and why (matches the assistant's own tool calls).
3. Dashboard: if `runtime/dashboard.json` names a live PID (sidecar), print its
   URL; else background-launch `serve.py` as today.
4. Arm a **persistent `Monitor`** on `events.jsonl` filtering
   `incident|loop_end|medic_end|memory_pressure` (line-buffered), described as
   "agent-loop <run-id> incidents". On wake: for `severity=needs-human` or
   `loop_end` with a non-`done` reason, invoke `/agent-loop-medic <id>` in
   interactive mode and send a `PushNotification`; for `done`, offer
   `/agent-loop-postmortem`.
5. If no harness is alive, print the launch command; never start it.

## 8. Setup skill, tick prompt, templates, docs
- **Setup wizard** adds two questions: `Dashboard` (auto/off) and `Medic`
  (auto/notify/off, plus optional model). Launch print explains the sidecar URL
  and that the loop should run in its own terminal, not as a background task of
  an interactive session (RAM stacking + survives session close).
- **Tick prompt §2**: remove the PID-lock protocol. Keep crash recovery as "if any
  task is `[~]` at boot, a prior tick died: re-evaluate, never assume done".
  Remove `runtime/LOCK` from the artefact list. §14 no longer releases a lock.
- **`templates/LOOP_CONFIG.md`**: `Dashboard: auto`, `Medic: auto`,
  `Medic model:`.
- **README**: What-it-ships gains `/agent-loop-medic`; new "Operator notes"
  (liveness = harness.json + heartbeat, never pgrep; run the harness standalone;
  Docker VM / turbo daemon note; exit codes 0/1/2/3); Configuration documents
  the new env vars; event table.
- **Version**: `2.0.0` — the `LOCK` contract, config fields, and event schema
  change.

## 9. Decisions made without you (override any)
1. **Dashboard defaults to `auto`** (sidecar spawned by `run.sh`). Rationale: you
   asked for "the option each time"; opt-out is one config line.
2. **Medic defaults to `auto` with a budget of 3 per run.** Rationale: the loop's
   point is unattended operation; `notify` is one config line away.
3. **Sidecar dies with the harness** rather than lingering. The browser keeps the
   last state; `/agent-loop` relaunches for post-run review.
4. **Stall threshold 300 s**, not 45 s. Subagent phases are legitimately quiet.
5. **`LOCK` is removed from the tick prompt** rather than kept alongside the
   harness lock. Two locks with different owners is the bug class we're fixing.
6. **Progress percent becomes segment-weighted** when segments are unplanned.
7. **UI direction**: health-first, dense, system fonts, light/dark, no ambient
   gradients. Sections in reading order: status + why + health pills; tick phase
   timeline; roadmap; incidents; effort; log (collapsed).
8. **Exit codes**: 0 done/paused/rate-limit-exit, 1 halt/error, 2 needs-human,
   3 lock-conflict.

## 10. Testing
- `lib.test.sh`: `classify_cause` matrix; `mem_guard_action`; `harness_alive`
  with a live/dead PID; `session_header` segment form; `fmt` of the new summary.
- `run.e2e.test.sh` (mock `claude`): second harness exits 3 + `loop_end
  `incident kind=lock-conflict` (and no `loop_end`); stale lock is taken over +
  `incident harness-crash`;
  `loop_end reason=done` on the happy path; `sleep` events between ticks;
  `tick.json` written during a tick; mock `RC=137` → `tick_end cause=killed`;
  `Medic: off` → no medic spawn; `Medic: auto` with a mock medic script writing
  `medic-<id>.json outcome=resumed` → loop continues; `outcome=paused` → exit 2
  `Dashboard: off` → no sidecar; `Dashboard: auto` with the
  `LOOP_DASHBOARD_CMD` env override pointing at a stub that prints a
  `dashboard-started` line → URL fed + pid recorded + killed on exit. (The
  override exists for tests and for anyone who wants a different observer.)
- `serve.test.py`: `derive_status` full matrix, including **`run.log` containing
  `HALT:` and `LOOP_DONE` text produces `running`**; EventStore incremental +
  truncation + memory bound (aggregates only for old ticks); supervisor refuses
  start when lock is live.
- `web.contract.sh`: new element ids (`status`, `why`, `health`, `timeline`,
  `incidents`), `health`/`status` keys in `/api/state`.
- `medic.contract.sh` (new): SKILL.md has frontmatter name matching dir,
  the allowlist and forbidden sections, the output contract keys, the budget
  rules.
- `tick-prompt.contract.sh`: assert the PID-lock protocol is gone and the `[~]`
  recovery rule remains.
- `bash scripts/test-all.sh` green.

## 11. Rollout
1. Land on a branch; `bash scripts/test-all.sh`.
2. On the other machine: pull, confirm `plugin.json` says `2.0.0`, delete any
   stale `runtime/LOCK`, restart the harness in its own terminal.
3. Verify the handoff's open items independently (they are that repo's, not this
   plugin's): the `apps/internal` build after the race, and the S2 commit set vs
   `LOOP_PLAN.md` SHAs.

## 12. Loop-dir schema and migrations

**Problem.** A loop dir outlives plugin versions. 2.0 changed what lives under `runtime/` (harness-owned lock, no `runtime/LOCK`) and how status is derived, and the loop the user is running was started by 1.x. There was no way for the harness to know which layout a dir was written by, and no place to put a one-time repair.

**Schema, not semver.** The layout of a loop dir is versioned by one integer, `LOOP_SCHEMA` (in `lib/migrate.sh`), separate from the plugin semver. It bumps only when a file name, format or sentinel under `$LOOP_DIR` changes meaning. Prompt, UI and harness-internal changes never bump it. Current value: `2`.

**Stamp.** `runtime/schema` holds the integer. The harness is its only writer. Reading rule (`loop_schema_read`): the file's integer if present; else `1` if `runtime/tickseq` exists (a dir that ran before stamping); else the current schema (a new dir — stamped on first start, no migration).

**Runner.** `migrate_loop_dir <loop_dir> <runtime_dir> <events> <target> <legacy_live> <force> <plugin_version>` runs after the lock is held and after `loop_start`, before the stale-lock (`harness-crash`) handling. It prints exactly one token:

- `ok:<n>` — nothing to do (stamp written if missing).
- `migrated:<from>:<to>:<actions>` — ran each step `k→k+1` in order, writing the stamp and emitting `migration{from,to,actions,plugin_version}` after each.
- `blocked:<detail>` — from=1 and a 1.x harness looks live (see below) and `LOOP_MIGRATE_FORCE!=1`. Nothing touched.
- `newer:<n>` — the dir is newer than this plugin. Nothing touched.

`blocked` → `escalate migration-blocked <detail>`; `newer` → `escalate schema-newer …`. Both exit 2 and write `runtime/NEEDS_HUMAN.md` like every other escalation. No medic runs for either: the fix is a human action (pause the old harness / update the plugin).

**Migration 1→2.** Remove `runtime/LOCK` (the 1.x LLM-written lock; nothing reads it). Action token `legacy-lock-removed`, or `none`. `LOOP_CONFIG.md` is not edited — the 2.0 harness already defaults `Dashboard:`/`Medic:` to `auto` when the lines are absent.

**Legacy-liveness heuristic (`legacy_harness_live <loop_dir> [recent_s=120]`).** 1.x wrote no pid file, so this is evidence, not proof: returns live when `run.log` was modified less than `recent_s` seconds ago **and** its last timestamped harness line (`^YYYY-MM-DDTHH:MM:SSZ `) is not one of 1.x's clean-exit lines (`LOOP_DONE after`, `HALT:`, `PAUSE present;`, `re-run run.sh`, `NEEDS HUMAN:`). No timestamped line at all with a recent mtime counts as live (conservative). `run.sh` evaluates it into `LEGACY_LIVE` **before its first write to `run.log`**, otherwise it would read its own output. Override: `LOOP_MIGRATE_FORCE=1`.

**Observers.** `serve.py` keeps `migration` as a lifecycle event and adds `loop.schema` (int or null) and `loop.migration` (last migration event or null) to the snapshot; the dashboard footer shows `schema N`. `/agent-loop` attach reads `runtime/schema` and, when it is absent or behind, tells the user what the next launch will do and how to pause a 1.x harness first. The medic decision table gains rows for `migration-blocked` and `schema-newer` (interactive only, outcome `noop`, never force).

**Authoring rule.** Any change that alters what a loop dir contains bumps `LOOP_SCHEMA`, adds a `migrate_<k>_to_<k+1>` function with tests, and adds a one-paragraph note under “Upgrading” in the plugin README. The CLAUDE.md “Changing a plugin” checklist links that section.

## 13. Dashboard-first launch (revision of §4.7, §7, §9.1)

**What was wrong.** §9.1 made the dashboard a sidecar the harness owns, and §7 made `/agent-loop` an attach-only skill that starts a read-only observer and tells the user to launch the harness in a separate terminal. In use that meant: attach starts a dashboard that cannot start anything, the user opens a terminal, a second dashboard appears on a new port. The memory rationale ("a tick's RAM stacks on the session") was wrong: a tick is its own process and its memory is the same whichever parent it has. The one real requirement is survival — the loop must outlive the Claude session — and that is solved by detaching, not by terminals.

**The model.** Exactly one dashboard per loop dir, and it is the launcher.

1. `/agent-loop` ensures one dashboard exists: if `runtime/dashboard.json` names a live `serve.py` pid, print its URL; otherwise run `serve.py --detach` in the foreground. `--detach` re-launches the server in a new process session (`start_new_session=True`, stdio to `runtime/dashboard.out`), waits until `runtime/dashboard.json` names the child, prints the `dashboard-started` banner, and exits. A second `--detach` on a dir with a live dashboard prints the same banner (`reused: true`) and launches nothing.
2. The dashboard's ▶ Start / ⟳ Resume spawn `run.sh` as its child with `LOOP_DASHBOARD=off` in the environment and in a new process session, so a dashboard crash does not take the loop down. Both clear `runtime/PAUSE`; both refuse (409) while `harness.json` names a live harness. "Tell the agent to start it" is the same path: `POST <url>/api/resume` (or `/api/start` before the first tick).
3. A terminal launch (`bash run.sh`) still works. Before spawning a sidecar the harness checks `runtime/dashboard.json`: a live non-sidecar `serve.py` is **adopted** — its URL is announced and recorded in `loop_start`, nothing is spawned, nothing is supervised, and it is not killed on exit. Only when no dashboard is alive (or the recorded one is a dead/orphaned sidecar) does `Dashboard: auto` spawn and supervise a sidecar as in §4.7.
4. Monitor fallback: when the Monitor tool is unavailable, the attach skill arms a background Bash task `tail -n 0 -F events.jsonl | grep -m1 -E …` that exits on the first matching event (the harness re-invokes the session on exit) and is re-armed after handling.

**Unchanged.** The lock, heartbeat, liveness rule, tick classification, medic, migrations, and every event. Where the harness process is started never mattered to any of them.

**Helpers.** `serve.py`: `existing_dashboard(runtime_dir)`, `run_detached(loop_dir, runtime_dir, argv, launcher, timeout)`. `lib/harness.sh`: `dashboard_adoptable <runtime_dir>` → prints the URL and returns 0 iff `dashboard.json` names a live `serve.py` pid with `sidecar != true`.
