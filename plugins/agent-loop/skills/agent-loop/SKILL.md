---
name: agent-loop
description: Attach an interactive session to the current repo's agent-loop. Reports harness/tick health from the runtime files, ensures exactly one detached dashboard (reused or launched with --detach; it survives this session), arms an incident monitor so this session is woken to run /agent-loop-medic when the loop needs a human, and — when no harness is alive — asks whether to start the loop through the dashboard or print a terminal command. Never runs run.sh itself. User-invoked only (never auto-run).
disable-model-invocation: true
---

# Attach to the agent-loop

Attach this session to a loop: say how healthy it is, make sure there is exactly one dashboard and hand over its URL, arm a monitor that wakes you when the loop raises an incident or ends, and — if nothing is running — let the user choose how to start it (from here through the dashboard, or a terminal command). The harness (`run.sh`) owns the loop and its lock; this skill launches it only through the dashboard's endpoint, never by running `run.sh`.

## Step 1: Find the loop dir

Run `ls -dt .claude/loop/*/ 2>/dev/null | head -1` from the repo (or worktree) root.

- None → tell the user to run `/agent-loop-setup` first and stop.
- Several → list them (`ls -dt .claude/loop/*/`) and ask which to attach to.

Set `LOOP_DIR` to the chosen dir and `<run-id>` to its basename. Read `Worktree:` from `$LOOP_DIR/LOOP_CONFIG.md`.

## Step 2: Report health from the runtime files

Judge liveness by `runtime/harness.json` pid + `kill -0` + `ps -o command= -p PID` containing `run.sh`, and `HEARTBEAT` age. Never `pgrep`/`ps | grep run.sh` — those match your own tool calls.

```bash
R="$LOOP_DIR/runtime"
pid=$(jq -r .pid "$R/harness.json" 2>/dev/null)
if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && ps -o command= -p "$pid" | grep -q 'run.sh'; then echo "harness pid $pid alive"; else echo "harness not running"; fi
[ -f "$R/HEARTBEAT" ] && echo "heartbeat age $(( $(date +%s) - $(cat "$R/HEARTBEAT") ))s"      # alive means < 30s
[ -f "$R/tick.json" ] && cat "$R/tick.json"                                                   # a tick is in flight
grep '"type":"loop_end"' "$LOOP_DIR/events.jsonl" | tail -1                                   # last exit, if any
grep '"type":"incident"' "$LOOP_DIR/events.jsonl" | tail -3                                   # recent incidents
[ -f "$R/NEEDS_HUMAN.md" ] && cat "$R/NEEDS_HUMAN.md"
[ -f "$LOOP_DIR/LOOP_DECISIONS.md" ] && tail -20 "$LOOP_DIR/LOOP_DECISIONS.md"                # autonomous Judge decisions
tail -12 "$LOOP_DIR/harness.log" 2>/dev/null                                                  # the harness's own lines
s=$(cat "$R/schema" 2>/dev/null); [ -n "$s" ] && echo "schema $s" || { [ -f "$R/tickseq" ] && echo "schema 1 (pre-2.0 layout; run.sh migrates it on the next launch)"; }
```

Report in one block: harness alive/dead (pid, heartbeat age), tick in flight (tick number, elapsed vs `timeout_s`), the last `loop_end` reason and detail, any `NEEDS_HUMAN.md`. A dead harness whose last event is not a `loop_end` crashed — say so plainly. `harness.log` is forensic only; never derive state from its text — a phase's full transcript lives in the session JSONL its `phase_end` event names, not in the loop dir. (A loop dir last written by 2.x has a `run.log` instead; same rule.) Also report the tail of `$LOOP_DIR/LOOP_DECISIONS.md`: under `Decision policy: autonomous` the Judge widens Scout-invented constraints, escalates tiers, splits oversized tasks and picks a default where the spec is silent, and every one of those lands here with its alternatives. These are decisions a human **ratifies or reverses after the fact**, not blockers — say how many are new since the last attach, and offer to walk them. `LOOP_CLEANUP.md` remains the queue of things the Judge *refused* to decide. Include the schema line: **schema 1** means the dir was started by a pre-2.0 plugin and `run.sh` will migrate it on the next launch (Step 5 says how to prepare). The migration is the harness's job, never yours: never write `runtime/schema` and never delete the old 1.x lock file. `migration` events in `events.jsonl` show when a past launch upgraded the dir.

## Step 3: Ensure exactly one dashboard — it is the launcher

- If `runtime/dashboard.json` names a pid that passes `kill -0` and whose `ps -o command= -p PID` contains `serve.py`, print its `url`. That is the loop's dashboard whoever started it (a sidecar of a running harness, or a detached server from an earlier attach). Never start a second one.
- Otherwise run this in the **foreground** (it returns within ~10 s), using `${CLAUDE_PLUGIN_ROOT}/web/serve.py` (confirm the file exists; if the variable is unset, locate the plugin source under the marketplace repo — never hand-type a version-keyed cache path):

  ```bash
  cd <Worktree> && LOOP_DIR=<loop-dir> python3 "${CLAUDE_PLUGIN_ROOT}/web/serve.py" --detach
  ```

  It prints `{"type":"dashboard-started","url":"http://127.0.0.1:PORT","pid":N,"reused":false}` — surface the URL. `--detach` launches the server in its own process session, so it **survives this Claude session closing**; a second `--detach` on the same loop dir prints the same banner with `"reused": true` and launches nothing. Never start it as a read-only observer here: this dashboard's ▶ Start / ⟳ Resume must be able to launch the harness. A harness later launched from a terminal **adopts** this dashboard (same URL) instead of spawning a sidecar.
- `{"type":"dashboard-failed", …}` means the child never registered; show its `log_tail` and stop.

### Stopping it: Pause vs Stop now

Two sentinels, both read by the harness at every phase boundary; neither kills anything mid-write.

- **Pause** (`⏸` in the dashboard, `POST /api/pause`, or `touch <loop-dir>/runtime/PAUSE`) — the harness finishes the **phase** in flight, writes `runtime/CHECKPOINT.json`, emits `paused`, exits 0.
- **Stop now** (`Stop now` in the dashboard, or `POST /api/stop`) — writes `runtime/STOP`. The harness SIGTERMs the phase in flight (a Worker gets its wrap-up continuation first, so its checkpoint is current), then behaves exactly as Pause. This is the one to reach for when a phase is burning budget on the wrong thing: it lands in minutes, not at the end of the tick.

`/api/stop` refuses with HTTP 409 (`no live harness to stop`) when nothing is running — a sentinel nobody reads would only stop the *next* launch. The dashboard shows `PAUSING` for both sentinels. Resume (`⟳`, `POST /api/resume`, or deleting the file) removes **both** sentinels before relaunching; if you stop the loop by hand, delete `runtime/STOP` as well as `runtime/PAUSE` or the next launch exits immediately.

## Step 4: Arm the incident monitor

Arm one persistent `Monitor` so this session is woken on incidents instead of polling:

```bash
tail -n 0 -F "<loop-dir>/events.jsonl" | grep -E --line-buffered '"type":"(incident|loop_end|medic_end|memory_pressure)"'
```

with `persistent: true` and description `agent-loop <run-id> incidents` (substitute the real dir and run-id). Arm it once; if a monitor with that description is already running in this session, do not arm another.

**On wake**, parse the JSON line and act by type:

- `incident` with `severity=needs-human`, or `loop_end` with any `reason` other than `done` → invoke `/agent-loop-medic <id>` in interactive mode (for a `loop_end`, pass the newest `runtime/incident-*.json` id, or no id if there is none) and send a `PushNotification` with the kind and detail. The medic explains what it found, applies only allowlisted fixes, asks before anything else, and prints the resume command.
- `loop_end` with `reason=done` → say so and offer `/agent-loop-postmortem`.
- `incident` with `severity=warn|error` → one line to the user (the harness's own medic is handling it; wait for `medic_end`).
- `medic_end` → one line: `medic <id> → <outcome>: <summary>`. `outcome` ∈ `paused|escalated` also triggers the needs-human path above (the harness is about to exit 2).
- `memory_pressure` → one line: free MB, swap %, action. No action from you.

Never restart the harness from a wake-up. Never grep the process table to check whether it is still there — Step 2's test is the only liveness test.

**No Monitor tool in this build?** Arm the portable fallback instead — a background Bash task that exits on the first matching event, which re-invokes you:

```bash
tail -n 0 -F "<loop-dir>/events.jsonl" | grep -m1 -E --line-buffered '"type":"(incident|loop_end|medic_end|memory_pressure)"'
```

Run it with `run_in_background: true`. When it fires, handle the event exactly as above, then re-arm. Tell the user which mode you armed (Monitor, or the tail fallback) so they know what wakes you.

## Step 5: If no harness is alive — dealer's choice

Ask **one** `AskUserQuestion` (header `Launch`), never assume. Two options, each with its one-line tradeoff:

1. **Start it from here (Recommended)** — "One dashboard, nothing to juggle. The loop runs under the detached dashboard, not this session, so closing Claude changes nothing. You watch it in the dashboard; this session stays free to discuss it."
2. **Give me a terminal command** — "You see the harness's live stream in that window and the loop does not depend on the dashboard process (also the only option without `python3`). With the dashboard alive the harness adopts it, so the URL is unchanged."

Mention in the question text that **▶ Start / ⟳ Resume in the dashboard** is always available too and does the same thing as option 1.

**Option 1 — do exactly this and nothing else:**

```bash
curl -s -X POST "<url>/api/resume"      # /api/start if runtime/tickseq does not exist yet
```

Both endpoints delete `runtime/PAUSE` and `runtime/STOP` and spawn `run.sh` as a child of the detached dashboard — not of this session — with the sidecar disabled, so there is still exactly one dashboard. Both refuse with HTTP 409 while `runtime/harness.json` names a live harness; a 409 body names the live pid — report it, do not retry. Confirm within ~5 s that `runtime/harness.json` appeared and its pid passes the Step 2 liveness test, and report the tick number from the first `tick_start`.

**Option 2 — print the launch command; never run it yourself, in the foreground or the background of this session:**

```bash
cd <Worktree> && LOOP_DIR=<loop-dir> bash "${CLAUDE_PLUGIN_ROOT}/run.sh"
```

With the Step 3 dashboard alive the harness adopts it (same URL). Without one it spawns its own sidecar (`Dashboard: auto`).

If `runtime/NEEDS_HUMAN.md` exists, the human clears the cause first; the resume command inside it is the terminal form of option 2.

If the dir is schema 1, say what the next launch will do before they press Start or ask you to start it: `run.sh` removes the dead 1.x lock file, stamps `runtime/schema`, and emits a `migration` event. It **refuses (exit 2, `runtime/NEEDS_HUMAN.md`, incident `migration-blocked`) when `run.log` was written in the last two minutes without ending in a 1.x exit line**, because that usually means the old harness is still running. The fix is to pause the old harness at a tick boundary (`touch <loop-dir>/runtime/PAUSE`, wait for its terminal to exit, delete the file), kill its dashboard, then relaunch. Only when the user confirms the old harness is dead is `LOOP_MIGRATE_FORCE=1` the answer — never suggest it first. Incident `schema-newer` means an older plugin was pointed at a newer dir: update the plugin on that machine.

Notes: requires `python3` (stdlib only) for the dashboard and `jq` for the health block. Exit codes of `run.sh`: 0 done/paused/rate-limit-exit · 1 halt/error · 2 needs-human · 3 lock-conflict.
