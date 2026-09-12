---
name: agent-loop
description: Attach an interactive session to the current repo's agent-loop. Reports harness/tick health from the runtime files, reuses the sidecar dashboard or launches one, and arms a persistent incident monitor so this session is woken to run /agent-loop-medic when the loop needs a human. Never starts the harness. User-invoked only (never auto-run).
disable-model-invocation: true
---

# Attach to the agent-loop

Attach this session to a loop: say how healthy it is, hand over a dashboard URL, and arm a monitor that wakes you when the loop raises an incident or ends. The harness (`run.sh`) owns the loop; this skill only observes, never starts it.

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
s=$(cat "$R/schema" 2>/dev/null); [ -n "$s" ] && echo "schema $s" || { [ -f "$R/tickseq" ] && echo "schema 1 (pre-2.0 layout; run.sh migrates it on the next launch)"; }
```

Report in one block: harness alive/dead (pid, heartbeat age), tick in flight (tick number, elapsed vs `timeout_s`), the last `loop_end` reason and detail, any `NEEDS_HUMAN.md`. A dead harness whose last event is not a `loop_end` crashed — say so plainly. `run.log` is forensic only; never derive state from its text. Include the schema line: **schema 1** means the dir was started by a pre-2.0 plugin and `run.sh` will migrate it on the next launch (Step 5 says how to prepare). The migration is the harness's job, never yours: never write `runtime/schema` and never delete the old 1.x lock file. `migration` events in `events.jsonl` show when a past launch upgraded the dir.

## Step 3: Dashboard

- `runtime/dashboard.json` names a pid that passes `kill -0` (and `ps -o command= -p PID` contains `serve.py`) → print its `url`. When `run.sh` runs with `Dashboard: auto` this is the sidecar it already spawned; do not start a second one.
- Otherwise background-launch the observer (Bash tool, `run_in_background: true`), using `${CLAUDE_PLUGIN_ROOT}/web/serve.py` (confirm the file exists; if the variable is unset, locate the plugin source under the marketplace repo — never hand-type a version-keyed cache path):

  ```bash
  cd <Worktree> && LOOP_DIR=<loop-dir> python3 "${CLAUDE_PLUGIN_ROOT}/web/serve.py" --no-spawn
  ```

  It prints `{"type":"dashboard-started","url":"http://127.0.0.1:PORT", ...}` — surface the URL. Its Start/Resume buttons refuse while `harness.json` names a live pid, so it cannot double-start a loop.

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

## Step 5: If no harness is alive

Print the launch command and stop; never run it, in the foreground or the background:

```bash
cd <Worktree> && LOOP_DIR=<loop-dir> bash "${CLAUDE_PLUGIN_ROOT}/run.sh"
```

Tell the user to run it in its own terminal — not as a background task of this session (the tick's RAM stacks on the session's, and the loop should survive the session closing). If `runtime/NEEDS_HUMAN.md` exists, the resume command inside it takes precedence; the human clears the cause first.

If the dir is schema 1, say what the next launch will do before they run it: `run.sh` removes the dead 1.x lock file, stamps `runtime/schema`, and emits a `migration` event. It **refuses (exit 2, `runtime/NEEDS_HUMAN.md`, incident `migration-blocked`) when `run.log` was written in the last two minutes without ending in a 1.x exit line**, because that usually means the old harness is still running. The fix is to pause the old harness at a tick boundary (`touch <loop-dir>/runtime/PAUSE`, wait for its terminal to exit, delete the file), kill its dashboard, then relaunch. Only when the user confirms the old harness is dead is `LOOP_MIGRATE_FORCE=1` the answer — never suggest it first. Incident `schema-newer` means an older plugin was pointed at a newer dir: update the plugin on that machine.

Notes: requires `python3` (stdlib only) for the dashboard and `jq` for the health block. Exit codes of `run.sh`: 0 done/paused/rate-limit-exit · 1 halt/error · 2 needs-human · 3 lock-conflict.
