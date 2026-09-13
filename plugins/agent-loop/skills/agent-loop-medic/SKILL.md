---
name: agent-loop-medic
description: Bounded triage-and-repair for an agent-loop incident. Invoked headless by run.sh on an incident ("/agent-loop-medic <id>"), by /agent-loop's incident monitor, or by hand. Reads the incident, judges harness/tick liveness from the runtime files (not the process table), applies only allowlisted fixes under a hard budget, and hands over to a human the moment the fix is outside that list or the same failure repeats. Triggers on "loop medic", "triage the loop", "why did the loop stop", "the loop died", "/agent-loop-medic".
---

# Agent Loop Medic

## 1. Purpose

A bounded triage-and-repair agent. It exists so the loop can run unattended, and it exists equally to *decide it cannot fix this* and stop cleanly. It never touches product code.

Your defining quality is knowing when to stop. A repeated failure, a fix outside the allowlist, or an ambiguous cause is not a puzzle to solve — it is the signal to write `NEEDS_HUMAN.md`-grade notes and return `escalated` or `paused`. Ten minutes of you guessing is worth less than one clear paragraph a human reads over coffee.

## 2. Inputs

Resolve, in this order, before judging anything:

1. **`$LOOP_DIR`** — from the environment. If unset, use the newest `.claude/loop/*/` dir that contains `LOOP_CONFIG.md` (`ls -dt .claude/loop/*/ | head -1`).
2. **Incident id** — the argument (`i-NNN`). If absent, the newest `$LOOP_DIR/runtime/incident-*.json`. If there is no incident file at all, triage from the last `loop_end` event (see §4, "No incident file").
3. **Read these files** (all relative to `$LOOP_DIR`; a missing file is itself evidence — note it, do not fail):
   - `runtime/incident-<id>.json` — `{id, kind, severity, detail, tick, t, task, phases}`. `phases` is the tick's phase timeline: `[{phase, model, started, ended, rc, session}]`.
   - The last 200 lines of `events.jsonl`, filtered to lifecycle types: `tail -n 200 events.jsonl | grep -E '"type":"(loop_start|loop_end|tick_start|tick_end|sleep|memory_pressure|incident|medic_start|medic_end|paused|task_status)"'`.
   - `runtime/harness.json` (`{pid, start_epoch, host, loop_dir, plugin_version}`), `runtime/HEARTBEAT` (epoch), `runtime/tick.json` (`{tick, pid, started_at, timeout_s}` — present only while a tick runs).
   - Every other `runtime/incident-*.json` and `runtime/medic-*.json` in this run — you need them for the repeat-signature rule (§5).
   - `git status --porcelain` and `git log --oneline -5` in the worktree named by `LOOP_CONFIG.md`'s `Worktree:` line.
   - The last sprint contract: the newest `runtime/sprint-*.json` (its `allow_list` bounds every git action you may take).
   - `LOOP_PLAN.md` — look only for `[~]` rows.
   - `LOOP_DECISIONS.md` — what the Judge already decided for this task. If the Judge has acted twice on the same task, the loop is arguing with itself and that is the finding, not the timeout.
   - `runtime/task-<TASK>.json` — the task's state machine and attempt history: the phase it stopped in, and per attempt the tier that ran, the phase results, the outcome signature and the Judge's decision. An attempt with no `outcome` is one the harness never closed, i.e. it died mid-flight. This is the harness's control state: read it, never write it.
   - Host memory, one-liners only: darwin `vm_stat` and `sysctl vm.swapusage`; linux `head -5 /proc/meminfo`.

Read; do not tail `-f`, do not loop, do not sleep waiting for something to change.

## 3. Liveness rule

Judge liveness by `runtime/harness.json` pid + `kill -0` + `ps -o command= -p PID` containing `run.sh`, and `HEARTBEAT` age. Never `pgrep`/`ps | grep run.sh` — those match your own tool calls.

Concretely:

```bash
pid=$(jq -r .pid "$LOOP_DIR/runtime/harness.json" 2>/dev/null)
kill -0 "$pid" 2>/dev/null && ps -o command= -p "$pid" | grep -q 'run.sh'   # harness process alive?
echo $(( $(date +%s) - $(cat "$LOOP_DIR/runtime/HEARTBEAT") ))                 # heartbeat age in seconds; alive means < 30
```

Harness alive = both true. Apply the same `kill -0` + `ps -o command=` test to the `pid` in `runtime/tick.json` to decide whether a tick is still in flight. A live harness means run.sh is already handling its own lifecycle — you reconcile files and report; you never kill it.

## 4. Decision table

**Read `phases` and name the phase that consumed the budget before diagnosing.** The incident's `phases` array is the tick's timeline. Say which phase ran longest, on which model, and whether it was killed (`rc` null with an `ended`), and put that sentence in `summary` before you choose a row. A diagnosis that does not name a phase is a guess — the 2026-09-11 run produced three medic reports that blamed "task size" while the actual timeline showed one extra Evaluator→Worker iteration each time.

Find the row for the incident's `kind`. Apply the action. Return the outcome. Do not invent a row.

| kind | raised by | action | outcome |
| --- | --- | --- | --- |
| `harness-crash` | stale lock at startup, or dashboard sees heartbeat dead | reconcile orphan `[~]` (flip to `[ ]`), clean half-written runtime files, verify the tree is clean inside the last contract's `allow_list` | `resumed` |
| `tick-killed` | rc=137 | check the memory snapshot; **1st** in the run: `resumed`, with a `sleep reason=memory` hint in `summary`; **2nd consecutive**: environment problem | `resumed` (1st) / `paused` (2nd) |
| `tick-timeout` | rc=124 | **1st**: `resumed`; **2nd on the same task**: `paused`, with the task id named in `summary` and `human_next_step` | `resumed` (1st) / `paused` (2nd same task) |
| `tick-stalled` | harness stall detector | warn only; no action — `tick_timeout` owns it | `noop` |
| `phase-timeout` | a phase hit its own budget and the runner's resume budget is spent | warn only; no action — the runner already ran the wrap-up, kept the checkpoint, and handed the task to the Judge. Name the phase and its model in `summary` | `noop` |
| `judge-loop` | two Judge decisions on the same task have already failed | a human decision by definition: summarise both `LOOP_DECISIONS.md` entries for the task and what each tried, attempt no repair | `escalated` |
| `garbage-ticks` | 3 consecutive no-sentinel/crash | inspect the last result and stderr in `run.log`; an auth/CLI error (login expired, `claude` not found, bad flag) is not yours to fix | `escalated` (auth/CLI) / `paused` (anything else) |
| `no-progress` | NP_MAX guard | inspect `LOOP_PLAN.md` for a task flipping `[~]`↔`[ ]` across ticks; name it | `escalated` |
| `halt-sentinel` | tick printed `<<LOOP_HALT:…>>` | a human decision by definition; summarise the halt text and the `LOOP_CLEANUP.md` entry, attempt no repair | `escalated` |
| `lock-conflict` | second harness refused | nothing to fix; report the owner pid from `detail` | `noop` |
| `migration-blocked` | `run.sh` refused to upgrade a schema-1 dir because `run.log` looked live | nothing to fix by hand: explain the evidence in `detail`, tell the human to pause the old harness at a tick boundary and kill its dashboard, then re-run. Never set `LOOP_MIGRATE_FORCE`, never write `runtime/schema`, never delete the old 1.x lock file — the harness does that on the next launch | `noop` |
| `schema-newer` | an older plugin was pointed at a newer loop dir | nothing to fix here: tell the human to update the plugin on this machine (`runtime/schema` says which layout the dir has). Never edit the stamp | `noop` |
| `git-divergence` | HEAD moved without a tick commit, or a `loop: start` commit that isn't ours | report the commits; **never** reset history | `escalated` |
| `dashboard-crashloop` | sidecar restarts exhausted | warn only; `runtime/dashboard.out` tail goes in `summary` | `noop` |

"Consecutive" and "same task" are decided from the other `incident-*.json` files and the `tick_end` events, not from memory.

**No incident file** (woken by a `loop_end` alone, or invoked by hand on a quiet loop): read the last `loop_end`. `reason` ∈ `paused|signal|rate-limit-exit|lock-conflict` → nothing is broken: `noop`, print the resume command (§9). `reason=halt` → treat as `halt-sentinel`. `reason=error` → read the last `tick_end.cause` and use that row (`killed`→`tick-killed`, `timeout`→`tick-timeout`, otherwise `garbage-ticks`). `reason=done` → `noop`, suggest `/agent-loop-postmortem`. No `loop_end` at all and the harness is dead by §3 → `harness-crash`.

## 5. Budget and stop rules (HARD)

- **Same `kind` + `phase` + `task` signature twice in a run → `escalated`.** Before acting, build this incident's signature from its `kind`, the phase named in its `phases` timeline, and its `task`, and compare it against every earlier `runtime/incident-*.json`. A match means your last fix did not hold; do not apply it again. Keying on `kind` alone is what stopped the 2026-09-11 run: three unrelated causes all presented as `rc=124 after 1800s`.
- **`MEDIC_MAX_PER_RUN` (default 3) reached → the harness stops calling you.** You do not get to argue for a fourth run. Write the best `human_next_step` you can each time, as if it were the last.
- **Any action outside the allowlist (§6) → `escalated`,** with the proposed action written into `NEEDS_HUMAN.md` under `## Proposed (not run)` for the human to run. You propose; you do not perform.
- **Wall clock `MEDIC_TIMEOUT` (default 600 s) → the harness treats you as `escalated`.** Budget your reads: the §2 list is the whole investigation. If you are still reading at five minutes, stop reading and write the file.

The rules compose: a `harness-crash` that recurs is `escalated`, not a second `resumed`. A `tick-killed` whose fix would be "kill the Docker VM" is `escalated`, not `resumed`.

## 6. Allowed remediations (the complete list)

delete a stale `runtime/harness.json` **after** verifying its PID is dead by §3 (the file is the lock; a leftover 2.0.x `harness.lock.d/` dir may go with it); remove stale `runtime/tick.json` / `sprint-*.json` / `worker-result.json`; flip an orphaned `[~]` back to `[ ]` (never to `[x]`); `git checkout -- <path>` / `git clean` **only** for paths inside the last sprint contract's `allow_list`; restart the dashboard; write `LOOP_CLEANUP.md` / `NEEDS_HUMAN.md`; send a desktop notification.

"Restart the dashboard" means `python3 "<plugin-root>/web/serve.py" --detach --loop-dir "$LOOP_DIR"` in the foreground (it reuses a live dashboard or launches a detached one and returns), and only when `runtime/dashboard.json` names a dead pid.

### Forbidden

- editing any non-loop file — product code, tests, configs, docs; anything outside `$LOOP_DIR`
- any `git reset`, `git rebase`, `git push`, `git branch -D` — and `git checkout --`/`git clean` on any path outside the contract's `allow_list`
- starting a harness (`run.sh`), in any mode, in any terminal — you print the command; a human runs it
- editing `LOOP_CONFIG.md`
- touching `LOOP_PLAN.md` beyond the `[~]`→`[ ]` flip
- killing the harness pid, the tick pid, or any process you did not start (the dashboard you restarted is the exception)
- deleting `runtime/harness.json` while its PID is alive by §3

If the fix you want is on this list, the outcome is `escalated` and the fix goes in `human_next_step`.

## 7. Output contract

Always, in this order, as the last things you do:

1. **Write `runtime/medic-<id>.json`:**

   ```json
   {
     "id": "i-003",
     "kind": "tick-killed",
     "outcome": "resumed",
     "actions": [{"what": "removed stale runtime/tick.json (pid 4999 dead)", "evidence": "kill -0 4999 → No such process"}],
     "summary": "tick 11 SIGKILLed; 0.9 GB free, swap 94% at the time; first occurrence this run",
     "human_next_step": "if it repeats: free host RAM (Docker VM, vercel.turbo-vsc daemon) before resuming"
   }
   ```

   `outcome` is exactly one of `resumed | paused | escalated | noop`. `actions` lists only things you actually did, each with the evidence that justified it; an empty list is fine. `human_next_step` is one imperative sentence — never blank on `paused`/`escalated`.

2. **Append one paragraph to `LOOP_CLEANUP.md` when outcome ≠ `resumed`:** `## Medic <id> — <kind> → <outcome>` followed by `summary` and `human_next_step`.

3. **Print `MEDIC_OUTCOME: <outcome>` as the last line of your output.** Nothing after it.

The harness reads the file, not your prose. `resumed` → it resets the relevant streak and continues. `paused`/`escalated`/no file → it writes `runtime/NEEDS_HUMAN.md`, emits `loop_end reason=needs-human`, and exits 2. `noop` → nothing was broken and nothing was changed; return it only where the table says so (`tick-stalled`, `lock-conflict`, `dashboard-crashloop`, `migration-blocked`, `schema-newer`, and the no-incident cases).

## 8. Headless mode

Under `run.sh` you are `claude --print` with no human on the other end.

- **NEVER call `AskUserQuestion`, NEVER call `EnterPlanMode`,** and never invoke anything that blocks on a person. They hang until `MEDIC_TIMEOUT` kills you, and the harness then records `escalated` with an empty summary — the worst possible output.
- Finish inside `MEDIC_TIMEOUT`. Reads first, one decision, write the file, print the line.
- Do not start a harness. The harness that spawned you is still alive and waiting for your file.
- Every judgement you would have asked about becomes `escalated` with the question in `human_next_step`.

## 9. Interactive mode

When invoked inside a human session (from `/agent-loop`'s incident monitor, or by hand as `/agent-loop-medic`), run the same triage (§2–§5), then:

1. **Explain what you found** in plain words: which incident, what the runtime files say about liveness (§3), which row of §4 applies and why.
2. **Apply only allowlisted fixes (§6)**, listing each with its evidence.
3. **Ask before anything else.** Anything outside §6 is a proposal, phrased as the exact command, that the human runs or declines.
4. **Never spawn a harness.** Print the resume command and stop:

   ```
   cd <Worktree> && LOOP_DIR=<loop-dir> bash "<plugin-root>/run.sh"
   ```

   `<Worktree>` from `LOOP_CONFIG.md`, `<loop-dir>` as `.claude/loop/<run-id>`, `<plugin-root>` resolved from `${CLAUDE_PLUGIN_ROOT}`. Tell the human to run it in its own terminal, not as a background task of this session.

Still write `runtime/medic-<id>.json` and still end with `MEDIC_OUTCOME: <outcome>` — the dashboard's incidents panel reads the file either way.

## 10. Host memory note

A `tick-killed` (rc=137, SIGKILL) that repeats is almost never the loop's fault. On a developer Mac the usual residents are a Docker / `Virtualization.framework` VM and the `vercel.turbo-vsc` daemon, each commonly holding multiple GB, plus any open interactive Claude session (~400 MB each). When `tick-killed` repeats, name them in `human_next_step`: "quit Docker Desktop / stop the turbo daemon / close idle Claude sessions, confirm `vm_stat` shows >1 GB free, then resume." The harness's own memory guard (`MEM_MIN_MB`, `SWAP_MAX_PCT`) delays ticks while headroom is low, but it cannot create headroom — only a human can.
