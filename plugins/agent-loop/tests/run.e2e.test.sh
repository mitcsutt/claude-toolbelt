#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
# shellcheck source=../../../tests/lib/assert.sh
source "$ROOT/tests/lib/assert.sh"
RUN="$HERE/../run.sh"

# `[[ ... ]]` followed by `assert_* $?` trips SC2319 (that $? is a condition's, not a
# command's), so file/format predicates go through real commands.
isdir()  { [[ -d "$1" ]]; }
isfile() { [[ -f "$1" ]]; }
isint()  { [[ "$1" =~ ^[0-9]+$ ]]; }

# Per-run base dir, relative to the worktree (run.sh runs with cwd=$WT and
# resolves LOOP_DIR relative to it). $WT/$LD is the absolute base dir for assertions.
LD=".claude/loop/test-run"

# setup_case [medic-mode]
#   Fresh worktree + loop dir. The sidecar is OFF and desktop notifications are
#   suppressed for every case except the two that test them; the medic defaults to
#   `off` so a case that raises an error-severity incident escalates deterministically
#   instead of shelling out to the mock.
setup_case() {
  WT="$(mktemp -d)"
  export LOOP_DIR="$LD"
  export LOOP_DASHBOARD=off
  export LOOP_NOTIFY=0
  unset MOCK_MEDIC_SCRIPT LOOP_DASHBOARD_CMD MEDIC_MAX_PER_RUN 2>/dev/null || true
  mkdir -p "$WT/$LD/runtime"
  printf '%s\n' '- [ ] T1' '- [ ] T2' '- [ ] T3' > "$WT/$LD/LOOP_PLAN.md"
  cat > "$WT/$LD/LOOP_CONFIG.md" <<EOF
Worktree: $WT
Limits: tick_timeout=600
Medic: ${1:-off}
EOF
  echo 'dummy tick' > "$WT/tick.md"; export TICK_PROMPT="$WT/tick.md"
  export MOCK_CLAUDE_STATE="$WT/.mockstate"
  export PATH="$HERE/fixtures:$PATH"   # mock claude wins
  : > "$WT/.mockstate"
}

# write_medic_script <path> <outcome>
#   Stands in for the /agent-loop-medic tick: writes the runtime/medic-<id>.json the
#   harness reads back. The id comes off the prompt argument the harness appends
#   (MEDIC_INCIDENT_ID is the fallback).
write_medic_script() {
  cat > "$1" <<EOF
#!/usr/bin/env bash
id=""
for a in "\$@"; do case "\$a" in *agent-loop-medic*) id="\${a##* }" ;; esac; done
[[ -n "\$id" ]] || id="\${MEDIC_INCIDENT_ID:-}"
jq -cn --arg id "\$id" --arg o "$2" \\
  '{id:\$id,kind:"mock",outcome:\$o,actions:[],summary:("mock medic " + \$o),human_next_step:"look at the box"}' \\
  > "\$RUNTIME_DIR/medic-\$id.json"
printf 'MEDIC_OUTCOME: %s\n' "$2"
EOF
}

# Happy path: three ticks, last is DONE (static plan; NP_MAX=99 so no-progress guard won't fire)
setup_case
export MOCK_CLAUDE_SCRIPT="$WT/script"
cat > "$WT/script" <<'EOF'
{"type":"result","subtype":"success","is_error":false,"result":"did T1 <<LOOP_CONTINUE>>","usage":{"input_tokens":10,"output_tokens":5},"total_cost_usd":0.10}
===
{"type":"result","subtype":"success","is_error":false,"result":"did T2 <<LOOP_CONTINUE>>","usage":{"input_tokens":10,"output_tokens":5},"total_cost_usd":0.10}
===
{"type":"result","subtype":"success","is_error":false,"result":"all done <<LOOP_DONE>>","usage":{"input_tokens":10,"output_tokens":5},"total_cost_usd":0.10}
EOF
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99 bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "happy path exits 0 on DONE"
assert_eq "3" "$(wc -l < "$WT/$LD/LOOP_USAGE.jsonl" | tr -d ' ')" "ledger has 3 ticks"
assert_eq "5" "$(tail -1 "$WT/$LD/LOOP_USAGE.jsonl" | jq -r '.output_tokens')" "ledger records tokens"
assert_eq "null" "$(tail -1 "$WT/$LD/LOOP_USAGE.jsonl" | jq -r '.cumulative_cost_usd')" "no cumulative cost field"
# nothing is written to the worktree root — all artefacts live under $LOOP_DIR
assert_eq "0" "$(find "$WT" -maxdepth 1 -name 'LOOP_*' | wc -l | tr -d ' ')" "no LOOP_* files at the worktree root"
ls "$WT/run.log" >/dev/null 2>&1; assert_false $? "no run.log at the worktree root"
# glanceable status: header + per-tick summary land in the runlog and the status file
grep -q "── loop" "$WT/$LD/run.log"; assert_true $? "run.log shows session header"
grep -q "0/3" "$WT/$LD/run.log"; assert_true $? "run.log shows progress count (no plan edits -> 0/3)"
grep -q "%" "$WT/$LD/run.log"; assert_true $? "run.log shows percentage"
ls "$WT/$LD/LOOP_STATUS.md" >/dev/null 2>&1; assert_true $? "LOOP_STATUS.md is written"
grep -q "^✓ t" "$WT/$LD/LOOP_STATUS.md"; assert_true $? "status file contains per-tick summary lines (not just header)"

# events.jsonl is written with boundary events, and tick numbers are continuous.
assert_eq "1" "$(jq -rc 'select(.type=="tick_start")' "$WT/$LD/events.jsonl" | head -1 | wc -l | tr -d ' ')" "tick_start emitted"
grep -q '"type":"tick_end"' "$WT/$LD/events.jsonl"; assert_true $? "tick_end emitted"
FIRST_TICK="$(jq -r 'select(.type=="tick_start") | .tick' "$WT/$LD/events.jsonl" | head -1)"
assert_eq "1" "$FIRST_TICK" "first run starts at tick 1"
# the tickseq counter persists under runtime/ and reflects the ticks just run (3 -> DONE)
SEQ_BEFORE="$(cat "$WT/$LD/runtime/tickseq")"
assert_eq "3" "$SEQ_BEFORE" "tickseq persisted after first run (3 ticks)"

# --- v2 lifecycle: the harness brackets the run and cleans up after itself ---
assert_eq "loop_start" "$(head -1 "$WT/$LD/events.jsonl" | jq -r .type)" "loop_start is the first event"
assert_eq "0" "$(head -1 "$WT/$LD/events.jsonl" | jq -r .resume)" "a first run is not a resume"
assert_eq "loop_end" "$(tail -1 "$WT/$LD/events.jsonl" | jq -r .type)" "loop_end is the last event"
assert_eq "done" "$(tail -1 "$WT/$LD/events.jsonl" | jq -r .reason)" "loop_end reason=done"
assert_eq "0" "$(tail -1 "$WT/$LD/events.jsonl" | jq -r .exit_code)" "loop_end carries exit_code 0"
assert_eq "ok" "$(jq -r 'select(.type=="tick_end") | .cause' "$WT/$LD/events.jsonl" | head -1)" "tick_end carries cause=ok"
assert_eq "number" "$(jq -r 'select(.type=="tick_start") | .pid|type' "$WT/$LD/events.jsonl" | head -1)" "tick_start carries the tick pid"
assert_eq "between-ticks" "$(jq -r 'select(.type=="sleep") | .reason' "$WT/$LD/events.jsonl" | head -1)" "between-tick sleep is narrated"
isdir "$WT/$LD/runtime/harness.lock.d"; assert_false $? "lock dir is released on exit"
isfile "$WT/$LD/runtime/harness.json"; assert_false $? "harness.json is removed on exit"
isfile "$WT/$LD/runtime/tick.json"; assert_false $? "tick.json is removed after the tick"
isint "$(cat "$WT/$LD/runtime/HEARTBEAT" 2>/dev/null)"; assert_true $? "HEARTBEAT holds an epoch"

# A second run.sh invocation continues numbering (does NOT reset to 1). Run exactly one
# tick to DONE and assert its emitted tick_start.tick == SEQ_BEFORE + 1.
export MOCK_CLAUDE_SCRIPT="$WT/script2"
cat > "$WT/script2" <<'EOF'
{"type":"result","subtype":"success","is_error":false,"result":"resumed done <<LOOP_DONE>>","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0.01}
EOF
: > "$WT/.mockstate"
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99 bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "second run exits 0 on DONE"
RESUME_TICK="$(jq -r 'select(.type=="tick_start") | .tick' "$WT/$LD/events.jsonl" | tail -1)"
assert_eq "$((SEQ_BEFORE + 1))" "$RESUME_TICK" "second run continues numbering (tick = SEQ_BEFORE + 1, no reset to 1)"
assert_eq "1" "$(jq -r 'select(.type=="loop_start") | .resume' "$WT/$LD/events.jsonl" | tail -1)" "second run reports resume=1"

# --- lock conflict: a second harness on the same LOOP_DIR refuses to start ---
setup_case
export MOCK_CLAUDE_SCRIPT="$WT/script"
cat > "$WT/script" <<'EOF'
SLEEP=3
{"type":"result","subtype":"success","is_error":false,"result":"held the lock <<LOOP_DONE>>","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0}
EOF
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99 bash "$RUN" >/dev/null 2>&1 ) &
first=$!
tries=0
while (( tries < 100 )); do
  [[ -d "$WT/$LD/runtime/harness.lock.d" ]] && break
  sleep 0.1; tries=$(( tries + 1 ))
done
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 bash "$RUN" >/dev/null 2>&1 )
assert_eq "3" "$?" "a second harness on a live LOOP_DIR exits 3"
assert_eq "lock-conflict" "$(jq -r 'select(.type=="incident") | .kind' "$WT/$LD/events.jsonl" | head -1)" \
  "the refusal is recorded as an incident on the live loop"
assert_eq "warn" "$(jq -r 'select(.type=="incident") | .severity' "$WT/$LD/events.jsonl" | head -1)" \
  "a refused starter is warn severity — it changes nothing about the running loop"
# The events file belongs to the loop that is STILL RUNNING: a loop_end here would
# make every observer render that healthy loop as terminal.
assert_eq "0" "$(jq -rc 'select(.type=="loop_end" and .reason=="lock-conflict")' "$WT/$LD/events.jsonl" | wc -l | tr -d ' ')" \
  "no loop_end is written into the live loop's event stream"
grep -q "another harness already owns" "$WT/$LD/run.log"; assert_true $? "run.log names the owning pid"
wait "$first"
assert_eq "0" "$?" "the harness that owns the lock still finishes normally"

# --- stale lock: a dead owner is taken over, and the crash is an incident ---
setup_case auto
write_medic_script "$WT/medic.sh" resumed
export MOCK_MEDIC_SCRIPT="$WT/medic.sh"
export MOCK_CLAUDE_SCRIPT="$WT/script"
cat > "$WT/script" <<'EOF'
{"type":"result","subtype":"success","is_error":false,"result":"recovered <<LOOP_DONE>>","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0}
EOF
sleep 0.01 & deadpid=$!; wait $deadpid
mkdir -p "$WT/$LD/runtime/harness.lock.d"
printf '{"pid":%s,"start_epoch":1,"host":"x","loop_dir":"x","plugin_version":"1.0.0"}' "$deadpid" \
  > "$WT/$LD/runtime/harness.json"
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99 bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "a stale lock is taken over and the loop reaches DONE"
grep -q "stale harness lock" "$WT/$LD/run.log"; assert_true $? "takeover is logged"
assert_eq "harness-crash" "$(jq -r 'select(.type=="incident") | .kind' "$WT/$LD/events.jsonl" | head -1)" \
  "takeover raises a harness-crash incident"
assert_eq "resumed" "$(jq -r 'select(.type=="medic_end") | .outcome' "$WT/$LD/events.jsonl" | head -1)" \
  "the medic clears the harness-crash incident"

# --- a SIGKILLed tick is an incident, not just a retry ---
setup_case auto
write_medic_script "$WT/medic.sh" resumed
export MOCK_MEDIC_SCRIPT="$WT/medic.sh"
export MOCK_CLAUDE_SCRIPT="$WT/script"
cat > "$WT/script" <<'EOF'
RC=137
===
{"type":"result","subtype":"success","is_error":false,"result":"recovered <<LOOP_DONE>>","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0}
EOF
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99 bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "a resumed medic lets the loop carry on to DONE"
assert_eq "killed" "$(jq -r 'select(.type=="tick_end") | .cause' "$WT/$LD/events.jsonl" | head -1)" "rc=137 -> cause=killed"
assert_eq "137" "$(jq -r 'select(.type=="tick_end") | .rc' "$WT/$LD/events.jsonl" | head -1)" "tick_end carries rc"
assert_eq "tick-killed" "$(jq -r 'select(.type=="incident") | .kind' "$WT/$LD/events.jsonl" | head -1)" "tick-killed incident raised"
grep -q '"type":"medic_start"' "$WT/$LD/events.jsonl"; assert_true $? "the medic is dispatched"
assert_eq "resumed" "$(jq -r 'select(.type=="medic_end") | .outcome' "$WT/$LD/events.jsonl" | head -1)" "medic_end records the outcome"
grep -q "SIGKILL" "$WT/$LD/run.log"; assert_true $? "the tick summary spells out the cause"

# --- a medic that cannot fix it stops the loop for a human ---
setup_case auto
write_medic_script "$WT/medic.sh" paused
export MOCK_MEDIC_SCRIPT="$WT/medic.sh"
export MOCK_CLAUDE_SCRIPT="$WT/script"
cat > "$WT/script" <<'EOF'
RC=137
===
{"type":"result","subtype":"success","is_error":false,"result":"never reached <<LOOP_DONE>>","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0}
EOF
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99 bash "$RUN" >/dev/null 2>&1 )
assert_eq "2" "$?" "medic outcome=paused exits 2 (needs human)"
ls "$WT/$LD/runtime/NEEDS_HUMAN.md" >/dev/null 2>&1; assert_true $? "NEEDS_HUMAN.md is written"
grep -q "mock medic paused" "$WT/$LD/runtime/NEEDS_HUMAN.md"; assert_true $? "NEEDS_HUMAN.md carries the medic summary"
grep -q "run.sh" "$WT/$LD/runtime/NEEDS_HUMAN.md"; assert_true $? "NEEDS_HUMAN.md carries the resume command"
assert_eq "needs-human" "$(jq -r 'select(.type=="loop_end") | .reason' "$WT/$LD/events.jsonl" | head -1)" "loop_end reason=needs-human"
assert_eq "needs-human" "$(jq -r 'select(.type=="incident") | .severity' "$WT/$LD/events.jsonl" | tail -1)" "the escalation is itself an incident"

# --- Medic: off — an error incident escalates straight away, no medic spawn ---
setup_case off
export MOCK_CLAUDE_SCRIPT="$WT/script"
cat > "$WT/script" <<'EOF'
RC=137
===
{"type":"result","subtype":"success","is_error":false,"result":"never reached <<LOOP_DONE>>","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0}
EOF
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99 bash "$RUN" >/dev/null 2>&1 )
assert_eq "2" "$?" "Medic: off + error incident exits 2"
ls "$WT/$LD/runtime/NEEDS_HUMAN.md" >/dev/null 2>&1; assert_true $? "Medic: off still writes NEEDS_HUMAN.md"
grep -q '"type":"medic_start"' "$WT/$LD/events.jsonl"; assert_false $? "Medic: off never dispatches a medic"

# --- the medic budget is a hard ceiling ---
setup_case auto
write_medic_script "$WT/medic.sh" resumed
export MOCK_MEDIC_SCRIPT="$WT/medic.sh"
export MOCK_CLAUDE_SCRIPT="$WT/script"
cat > "$WT/script" <<'EOF'
RC=137
===
RC=137
===
{"type":"result","subtype":"success","is_error":false,"result":"never reached <<LOOP_DONE>>","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0}
EOF
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99 MEDIC_MAX_PER_RUN=1 bash "$RUN" >/dev/null 2>&1 )
assert_eq "2" "$?" "the incident after the budget runs out escalates"
assert_eq "1" "$(grep -c '"type":"medic_start"' "$WT/$LD/events.jsonl" | tr -d ' ')" "MEDIC_MAX_PER_RUN=1 dispatches exactly one medic"
grep -q "medic budget exhausted" "$WT/$LD/run.log"; assert_true $? "the exhausted budget is logged"

# --- halt sentinel: notified and recorded, but still exit 1 (not needs-human) ---
setup_case notify
export MOCK_CLAUDE_SCRIPT="$WT/script"
cat > "$WT/script" <<'EOF'
{"type":"result","subtype":"success","is_error":false,"result":"cannot proceed <<LOOP_HALT:blocked on T2>>","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0}
EOF
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99 bash "$RUN" >/dev/null 2>&1 )
assert_eq "1" "$?" "a halt sentinel exits 1"
assert_eq "halt" "$(jq -r 'select(.type=="loop_end") | .reason' "$WT/$LD/events.jsonl" | head -1)" "loop_end reason=halt"
assert_eq "halt-sentinel" "$(jq -r 'select(.type=="incident") | .kind' "$WT/$LD/events.jsonl" | head -1)" "halt-sentinel incident raised"
assert_eq "blocked on T2" "$(jq -r 'select(.type=="incident") | .detail' "$WT/$LD/events.jsonl" | head -1)" "the halt reason is the incident detail"
grep -q '"type":"medic_start"' "$WT/$LD/events.jsonl"; assert_false $? "Medic: notify does not dispatch a medic"

# --- dashboard sidecar: spawned, announced, supervised, killed with the harness ---
setup_case off
export LOOP_DASHBOARD=auto
export LOOP_DASHBOARD_CMD="$HERE/fixtures/dashboard-stub"
export MOCK_CLAUDE_SCRIPT="$WT/script"
cat > "$WT/script" <<'EOF'
SLEEP=2
{"type":"result","subtype":"success","is_error":false,"result":"done <<LOOP_DONE>>","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0}
EOF
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99 bash "$RUN" >/dev/null 2>&1 ) &
runpid=$!
stubpid=""; tries=0
while (( tries < 100 )); do
  stubpid="$(cat "$WT/$LD/runtime/sidecar.pid" 2>/dev/null)"
  [[ -n "$stubpid" ]] && break
  sleep 0.1; tries=$(( tries + 1 ))
done
wait "$runpid"
assert_eq "0" "$?" "the sidecar case still reaches DONE"
isint "$stubpid"; assert_true $? "the harness records the sidecar pid ($stubpid)"
grep -q "dashboard http://127.0.0.1:1" "$WT/$LD/run.log"; assert_true $? "the sidecar URL is fed to the operator"
assert_eq "http://127.0.0.1:1" "$(jq -r 'select(.type=="loop_start") | .dashboard_url' "$WT/$LD/events.jsonl" | head -1)" \
  "loop_start carries the dashboard url"
kill -0 "$stubpid" 2>/dev/null; assert_false $? "the sidecar dies with the harness"
isfile "$WT/$LD/runtime/sidecar.pid"; assert_false $? "sidecar.pid is cleaned up on exit"

# --- SIGKILLed harness: no EXIT trap runs, so the background loops must self-terminate ---
# This is the OOM case. An orphaned supervisor would keep respawning a dashboard and an
# orphaned heartbeat would keep telling observers the dead loop is healthy.
setup_case off
export LOOP_DASHBOARD=auto
export LOOP_DASHBOARD_CMD="$HERE/fixtures/dashboard-stub"
export MOCK_CLAUDE_SCRIPT="$WT/script"
cat > "$WT/script" <<'EOF'
SLEEP=15
{"type":"result","subtype":"success","is_error":false,"result":"never reached <<LOOP_DONE>>","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0}
EOF
# The whole launch/kill/reap runs with stderr muted: bash reports a background job
# that died by signal ("Killed: 9") on its own stderr, and that is not a test failure.
{
  ( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99 HB_INTERVAL=1 bash "$RUN" >/dev/null 2>&1 ) &
  runpid=$!
  hpid=""; stubpid=""; tickpid=""; tries=0
  while (( tries < 150 )); do
    hpid="$(jq -r '.pid // empty' "$WT/$LD/runtime/harness.json" 2>/dev/null)"
    stubpid="$(cat "$WT/$LD/runtime/sidecar.pid" 2>/dev/null)"
    tickpid="$(jq -r '.pid // empty' "$WT/$LD/runtime/tick.json" 2>/dev/null)"
    [[ -n "$hpid" && -n "$stubpid" && -n "$tickpid" ]] && break
    sleep 0.1; tries=$(( tries + 1 ))
  done
  kill -9 "$hpid" 2>/dev/null
  wait "$runpid"        # reap it: kill -0 on an unreaped zombie still succeeds
} 2>/dev/null
isint "$hpid"; assert_true $? "harness.json records the harness pid ($hpid)"
hb1="$(cat "$WT/$LD/runtime/HEARTBEAT" 2>/dev/null)"
sleep 3
kill -0 "$stubpid" 2>/dev/null; assert_false $? "the supervisor takes the sidecar down with the dead harness"
hb2="$(cat "$WT/$LD/runtime/HEARTBEAT" 2>/dev/null)"
sleep 2
assert_eq "$hb2" "$(cat "$WT/$LD/runtime/HEARTBEAT" 2>/dev/null)" "the heartbeat stops advancing once the harness is gone"
isint "$hb1"; assert_true $? "the heartbeat had been running before the kill"
assert_eq "0" "$(jq -rc 'select(.type=="loop_end")' "$WT/$LD/events.jsonl" | wc -l | tr -d ' ')" \
  "a SIGKILLed harness writes no loop_end (that is what the crashed state is for)"
kill "$tickpid" 2>/dev/null   # the orphaned tick wrapper; its mock `sleep` expires on its own

# --- Dashboard: off — nothing is spawned ---
setup_case off
export MOCK_CLAUDE_SCRIPT="$WT/script"
cat > "$WT/script" <<'EOF'
{"type":"result","subtype":"success","is_error":false,"result":"done <<LOOP_DONE>>","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0}
EOF
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99 bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "Dashboard: off reaches DONE"
grep -q "dashboard" "$WT/$LD/run.log"; assert_false $? "no dashboard line in run.log"
isfile "$WT/$LD/runtime/sidecar.pid"; assert_false $? "no sidecar.pid"
assert_eq "null" "$(jq -r 'select(.type=="loop_start") | .dashboard_url' "$WT/$LD/events.jsonl" | head -1)" \
  "loop_start carries no dashboard url"

# --- memory guard: delays the tick, narrates the wait, never halts ---
setup_case off
export MOCK_CLAUDE_SCRIPT="$WT/script"
cat > "$WT/script" <<'EOF'
{"type":"result","subtype":"success","is_error":false,"result":"done <<LOOP_DONE>>","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0}
EOF
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99 \
  MEM_MIN_MB=999999999 SWAP_MAX_PCT=0 MEM_MAX_DELAYS=1 MEM_BACKOFF=0 bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "the memory guard is advisory — the loop still reaches DONE"
assert_eq "delay" "$(jq -r 'select(.type=="memory_pressure") | .action' "$WT/$LD/events.jsonl" | head -1)" \
  "memory_pressure action=delay when there is no headroom"
assert_eq "memory" "$(jq -r 'select(.type=="sleep") | .reason' "$WT/$LD/events.jsonl" | head -1)" \
  "the wait is narrated as sleep reason=memory"
assert_eq "proceed" "$(jq -r 'select(.type=="memory_pressure") | .action' "$WT/$LD/events.jsonl" | tail -1)" \
  "after MEM_MAX_DELAYS the guard proceeds anyway"

# Usage limit, far-future reset -> clean exit 0 (manual resume), state saved
setup_case
export MOCK_CLAUDE_SCRIPT="$WT/script"
cat > "$WT/script" <<'EOF'
{"type":"rate_limit_event","rate_limit_info":{"status":"rejected","resetsAt":9999999999,"rateLimitType":"weekly"}}
{"type":"result","subtype":"success","is_error":true,"api_error_status":429,"result":"","usage":{"input_tokens":0,"output_tokens":0},"total_cost_usd":0}
EOF
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99 bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "usage-limit far-future reset -> clean exit 0"
grep -q "usage window resets" "$WT/$LD/run.log"; assert_true $? "run.log tells user to resume later"
assert_eq "rate-limit-exit" "$(jq -r 'select(.type=="loop_end") | .reason' "$WT/$LD/events.jsonl" | head -1)" \
  "loop_end reason=rate-limit-exit"

# Usage limit, reset already passed -> brief 5s wait then resume to DONE
setup_case
export MOCK_CLAUDE_SCRIPT="$WT/script"
cat > "$WT/script" <<'EOF'
{"type":"rate_limit_event","rate_limit_info":{"status":"rejected","resetsAt":1}}
{"type":"result","subtype":"success","is_error":true,"api_error_status":429,"result":"","usage":{"input_tokens":0,"output_tokens":0},"total_cost_usd":0}
===
{"type":"result","subtype":"success","is_error":false,"result":"recovered <<LOOP_DONE>>","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0.01}
EOF
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99 bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "usage-limit (reset passed) waits briefly then resumes to DONE"
grep -q "sleeping" "$WT/$LD/run.log"; assert_true $? "run.log records the wait"
assert_eq "rate-limit" "$(jq -r 'select(.type=="sleep") | .reason' "$WT/$LD/events.jsonl" | head -1)" \
  "the usage-window wait is narrated as sleep reason=rate-limit"

# No-progress guard: CONTINUE forever without dropping remaining count -> needs a human
setup_case off
export MOCK_CLAUDE_SCRIPT="$WT/script"
cat > "$WT/script" <<'EOF'
{"type":"result","subtype":"success","is_error":false,"result":"spin <<LOOP_CONTINUE>>","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0}
===
{"type":"result","subtype":"success","is_error":false,"result":"spin <<LOOP_CONTINUE>>","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0}
===
{"type":"result","subtype":"success","is_error":false,"result":"spin <<LOOP_CONTINUE>>","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0}
===
{"type":"result","subtype":"success","is_error":false,"result":"spin <<LOOP_CONTINUE>>","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0}
EOF
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 bash "$RUN" >/dev/null 2>&1 )
assert_eq "2" "$?" "no-progress escalates to a human (exit 2)"
grep -q "no progress" "$WT/$LD/run.log"; assert_true $? "run.log records no-progress halt"
assert_eq "no-progress" "$(jq -r 'select(.type=="incident") | .kind' "$WT/$LD/events.jsonl" | head -1)" "no-progress incident raised"
ls "$WT/$LD/runtime/NEEDS_HUMAN.md" >/dev/null 2>&1; assert_true $? "no-progress writes NEEDS_HUMAN.md"

# Garbage ticks: three sentinel-less results in a row -> needs a human
setup_case off
export MOCK_CLAUDE_SCRIPT="$WT/script"
cat > "$WT/script" <<'EOF'
{"type":"result","subtype":"success","is_error":false,"result":"junk","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0}
===
{"type":"result","subtype":"success","is_error":false,"result":"junk","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0}
===
{"type":"result","subtype":"success","is_error":false,"result":"junk","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0}
EOF
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99 bash "$RUN" >/dev/null 2>&1 )
assert_eq "2" "$?" "three garbage ticks escalate to a human (exit 2)"
assert_eq "no_sentinel" "$(jq -r 'select(.type=="tick_end") | .cause' "$WT/$LD/events.jsonl" | head -1)" "a sentinel-less tick is cause=no_sentinel"
assert_eq "garbage-ticks" "$(jq -r 'select(.type=="incident") | .kind' "$WT/$LD/events.jsonl" | head -1)" "garbage-ticks incident raised"
ls "$WT/$LD/runtime/NEEDS_HUMAN.md" >/dev/null 2>&1; assert_true $? "garbage ticks write NEEDS_HUMAN.md"
assert_eq "backoff" "$(jq -r 'select(.type=="sleep") | .reason' "$WT/$LD/events.jsonl" | head -1)" "failed-tick backoff is narrated"

# Worktree guard: running outside the configured worktree aborts
setup_case
export MOCK_CLAUDE_SCRIPT="$WT/script"
echo '{"type":"result","subtype":"success","is_error":false,"result":"x <<LOOP_DONE>>","usage":{"input_tokens":0,"output_tokens":0},"total_cost_usd":0}' > "$WT/script"
( cd /tmp && AGENT_LOOP_SKIP_POSTMORTEM=1 CONFIG_PATH="$WT/$LD/LOOP_CONFIG.md" bash "$RUN" >/dev/null 2>&1 )
assert_true "$([[ $? -ne 0 ]] && echo 0 || echo 1)" "aborts when cwd != worktree"

# Transient 429 with NO rate_limit_event -> short backoff (RL_BACKOFF=0) then resume to DONE
setup_case
export MOCK_CLAUDE_SCRIPT="$WT/script"
cat > "$WT/script" <<'EOF'
{"type":"result","subtype":"success","is_error":true,"api_error_status":429,"result":"","usage":{"input_tokens":0,"output_tokens":0},"total_cost_usd":0}
===
{"type":"result","subtype":"success","is_error":false,"result":"recovered <<LOOP_DONE>>","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0.01}
EOF
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 RL_BACKOFF=0 NP_MAX=99 bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "transient 429 (no window info) backs off and resumes to DONE"
assert_eq "rate_limit" "$(jq -r 'select(.type=="tick_end") | .cause' "$WT/$LD/events.jsonl" | head -1)" "a 429 result is cause=rate_limit"

# Plan-oversize guard: oversize plan emits plan_oversize event but does NOT halt the loop
setup_case
export MOCK_CLAUDE_SCRIPT="$WT/script"
cat > "$WT/script" <<'EOF'
{"type":"result","subtype":"success","is_error":false,"result":"did T1 <<LOOP_CONTINUE>>","usage":{"input_tokens":10,"output_tokens":5},"total_cost_usd":0.10}
===
{"type":"result","subtype":"success","is_error":false,"result":"did T2 <<LOOP_CONTINUE>>","usage":{"input_tokens":10,"output_tokens":5},"total_cost_usd":0.10}
===
{"type":"result","subtype":"success","is_error":false,"result":"all done <<LOOP_DONE>>","usage":{"input_tokens":10,"output_tokens":5},"total_cost_usd":0.10}
EOF
# Pad the plan well over the 32000-char budget while keeping the task list intact
{ printf '\n<!-- '; printf 'x%.0s' $(seq 1 40000); printf ' -->\n'; } >> "$WT/$LD/LOOP_PLAN.md"
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99 bash "$RUN" >/dev/null 2>&1 )
rc=$?
grep -q '"type":"plan_oversize"' "$WT/$LD/events.jsonl"; assert_true $? "oversize plan emits plan_oversize event"
assert_eq "0" "$rc" "oversize plan does not halt the loop"

# Stale lock + Medic: off -> harness-crash is a WARN (no medic to run, next tick reconciles [~]); loop continues to DONE
setup_case off
export MOCK_CLAUDE_SCRIPT="$WT/script"
echo '{"type":"result","subtype":"success","is_error":false,"result":"x <<LOOP_DONE>>","usage":{"input_tokens":0,"output_tokens":0},"total_cost_usd":0}' > "$WT/script"
mkdir -p "$WT/$LD/runtime/harness.lock.d"
sleep 0.01 & _dead=$!; wait "$_dead"
printf '{"pid":%s,"start_epoch":1,"host":"x","loop_dir":"%s","plugin_version":"2.0.0"}' "$_dead" "$LD" > "$WT/$LD/runtime/harness.json"
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99 bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "stale lock with Medic off: loop continues and exits 0 on DONE"
assert_eq "warn" "$(jq -r 'select(.type=="incident" and .kind=="harness-crash") | .severity' "$WT/$LD/events.jsonl" | head -1)" "harness-crash is warn-severity when no medic runs"
ls "$WT/$LD/runtime/NEEDS_HUMAN.md" >/dev/null 2>&1; assert_false $? "no NEEDS_HUMAN.md for a warn-level harness-crash"

# --- schema migration: a 1.x dir (tickseq, LLM LOCK, quiet run.log) is migrated in place ---
setup_case
export MOCK_CLAUDE_SCRIPT="$WT/script"
cat > "$WT/script" <<'EOF'
{"type":"result","subtype":"success","is_error":false,"result":"post-upgrade <<LOOP_DONE>>","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0}
EOF
printf '41' > "$WT/$LD/runtime/tickseq"
printf '4242' > "$WT/$LD/runtime/LOCK"
printf '%s\n' "2026-01-01T00:00:00Z tick 41 starting" "2026-01-01T00:20:00Z LOOP_DONE after 41 ticks" > "$WT/$LD/run.log"
touch -t 202001010000 "$WT/$LD/run.log"
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99 bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "1.x dir: migrated and the loop reaches DONE"
assert_eq "2" "$(cat "$WT/$LD/runtime/schema" 2>/dev/null)" "1.x dir: runtime/schema stamped 2"
ls "$WT/$LD/runtime/LOCK" >/dev/null 2>&1; assert_false $? "1.x dir: legacy runtime/LOCK removed"
assert_eq "1" "$(jq -r 'select(.type=="migration") | .from' "$WT/$LD/events.jsonl")" "1.x dir: migration event from=1"
assert_eq "legacy-lock-removed" "$(jq -r 'select(.type=="migration") | .actions' "$WT/$LD/events.jsonl")" "1.x dir: migration event actions"
assert_eq "loop_start" "$(jq -r 'select(.type=="loop_start" or .type=="migration") | .type' "$WT/$LD/events.jsonl" | head -1)" "1.x dir: loop_start precedes migration"
assert_eq "42" "$(jq -r 'select(.type=="tick_end") | .tick' "$WT/$LD/events.jsonl" | head -1)" "1.x dir: tick numbering continues from the old counter"
grep -q "loop dir migrated: schema 1 -> 2" "$WT/$LD/run.log"; assert_true $? "1.x dir: migration is logged"

# --- schema migration: a 1.x harness that looks live blocks the start (exit 2, nothing touched) ---
setup_case
export MOCK_CLAUDE_SCRIPT="$WT/script"
cat > "$WT/script" <<'EOF'
{"type":"result","subtype":"success","is_error":false,"result":"should not run <<LOOP_DONE>>","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0}
EOF
printf '41' > "$WT/$LD/runtime/tickseq"
printf '4242' > "$WT/$LD/runtime/LOCK"
printf '%s\n' "2026-01-01T00:00:00Z tick 41 starting" '{"type":"assistant","text":"working"}' > "$WT/$LD/run.log"
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99 bash "$RUN" >/dev/null 2>&1 )
assert_eq "2" "$?" "live 1.x evidence: exit 2"
assert_eq "migration-blocked" "$(jq -r 'select(.type=="incident") | .kind' "$WT/$LD/events.jsonl" | head -1)" "live 1.x evidence: migration-blocked incident"
assert_eq "needs-human" "$(jq -r 'select(.type=="loop_end") | .reason' "$WT/$LD/events.jsonl" | tail -1)" "live 1.x evidence: loop_end needs-human"
grep -q "LOOP_MIGRATE_FORCE" "$WT/$LD/runtime/NEEDS_HUMAN.md"; assert_true $? "live 1.x evidence: NEEDS_HUMAN names the override"
grep -q "PAUSE" "$WT/$LD/runtime/NEEDS_HUMAN.md"; assert_true $? "live 1.x evidence: NEEDS_HUMAN says how to pause the old harness"
ls "$WT/$LD/runtime/LOCK" >/dev/null 2>&1; assert_true $? "live 1.x evidence: legacy LOCK untouched"
ls "$WT/$LD/runtime/schema" >/dev/null 2>&1; assert_false $? "live 1.x evidence: no stamp written"
assert_eq "0" "$(jq -r 'select(.type=="tick_start") | .tick' "$WT/$LD/events.jsonl" | wc -l | tr -d ' ')" "live 1.x evidence: no tick ran"
ls "$WT/$LD/runtime/harness.lock.d" >/dev/null 2>&1; assert_false $? "live 1.x evidence: our lock released on exit"

# --- schema migration: LOOP_MIGRATE_FORCE=1 overrides the live-1.x guard ---
( cd "$WT" && LOOP_MIGRATE_FORCE=1 AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99 bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "forced: migrates and reaches DONE"
assert_eq "2" "$(cat "$WT/$LD/runtime/schema" 2>/dev/null)" "forced: stamped 2"
ls "$WT/$LD/runtime/LOCK" >/dev/null 2>&1; assert_false $? "forced: legacy LOCK removed"

# --- schema migration: a dir newer than the plugin is refused ---
setup_case
export MOCK_CLAUDE_SCRIPT="$WT/script"
cat > "$WT/script" <<'EOF'
{"type":"result","subtype":"success","is_error":false,"result":"should not run <<LOOP_DONE>>","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0}
EOF
printf '3' > "$WT/$LD/runtime/tickseq"
printf '99' > "$WT/$LD/runtime/schema"
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99 bash "$RUN" >/dev/null 2>&1 )
assert_eq "2" "$?" "newer schema: exit 2"
assert_eq "schema-newer" "$(jq -r 'select(.type=="incident") | .kind' "$WT/$LD/events.jsonl" | head -1)" "newer schema: schema-newer incident"
assert_eq "99" "$(cat "$WT/$LD/runtime/schema")" "newer schema: stamp untouched"
grep -q "update the plugin" "$WT/$LD/runtime/NEEDS_HUMAN.md"; assert_true $? "newer schema: NEEDS_HUMAN says to update the plugin"

# --- schema migration: a fresh dir is stamped with no migration event ---
setup_case
export MOCK_CLAUDE_SCRIPT="$WT/script"
cat > "$WT/script" <<'EOF'
{"type":"result","subtype":"success","is_error":false,"result":"fresh <<LOOP_DONE>>","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0}
EOF
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99 bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "fresh dir: runs to DONE"
assert_eq "2" "$(cat "$WT/$LD/runtime/schema" 2>/dev/null)" "fresh dir: stamped 2"
assert_eq "0" "$(grep -c '"type":"migration"' "$WT/$LD/events.jsonl")" "fresh dir: no migration event"

# --- one dashboard per loop dir: a live standalone dashboard is adopted, never doubled ---
setup_case
export LOOP_DASHBOARD=auto
export LOOP_DASHBOARD_CMD="$HERE/fixtures/dashboard-stub"
export MOCK_CLAUDE_SCRIPT="$WT/script"
cat > "$WT/script" <<'EOF'
{"type":"result","subtype":"success","is_error":false,"result":"adopted <<LOOP_DONE>>","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0}
EOF
bash -c 'exec -a serve.py sleep 60' & dashfake=$!
sleep 0.2
printf '{"pid":%s,"port":7,"url":"http://127.0.0.1:7","sidecar":false}' "$dashfake" > "$WT/$LD/runtime/dashboard.json"
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99 bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "adopt: loop reaches DONE"
grep -q "dashboard http://127.0.0.1:7 (adopted)" "$WT/$LD/run.log"; assert_true $? "adopt: the existing URL is announced as adopted"
ls "$WT/$LD/runtime/sidecar.pid" >/dev/null 2>&1; assert_false $? "adopt: no sidecar spawned"
assert_eq "http://127.0.0.1:7" "$(jq -r 'select(.type=="loop_start") | .dashboard_url' "$WT/$LD/events.jsonl" | tail -1)" "adopt: loop_start carries the adopted URL"
kill -0 "$dashfake" 2>/dev/null; assert_true $? "adopt: the standalone dashboard outlives the harness"
assert_eq "$dashfake" "$(jq -r .pid "$WT/$LD/runtime/dashboard.json")" "adopt: dashboard.json untouched"
kill "$dashfake" 2>/dev/null; wait "$dashfake" 2>/dev/null

# --- adoption ignores LOOP_DASHBOARD=off (the Supervisor sets it when IT spawns the harness) ---
setup_case
export LOOP_DASHBOARD=off
export MOCK_CLAUDE_SCRIPT="$WT/script"
cat > "$WT/script" <<'EOF'
{"type":"result","subtype":"success","is_error":false,"result":"adopted-off <<LOOP_DONE>>","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0}
EOF
bash -c 'exec -a serve.py sleep 60' & dashfake=$!
sleep 0.2
printf '{"pid":%s,"port":8,"url":"http://127.0.0.1:8","sidecar":false}' "$dashfake" > "$WT/$LD/runtime/dashboard.json"
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99 bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "adopt+off: loop reaches DONE"
assert_eq "http://127.0.0.1:8" "$(jq -r 'select(.type=="loop_start") | .dashboard_url' "$WT/$LD/events.jsonl" | tail -1)" "adopt+off: loop_start still names the parent dashboard"
ls "$WT/$LD/runtime/sidecar.pid" >/dev/null 2>&1; assert_false $? "adopt+off: nothing spawned"
kill "$dashfake" 2>/dev/null; wait "$dashfake" 2>/dev/null

# --- an orphaned sidecar record (sidecar:true) is NOT adopted: the spawn path owns it ---
setup_case
export LOOP_DASHBOARD=auto
export LOOP_DASHBOARD_CMD="$HERE/fixtures/dashboard-stub"
export MOCK_CLAUDE_SCRIPT="$WT/script"
cat > "$WT/script" <<'EOF'
{"type":"result","subtype":"success","is_error":false,"result":"respawn <<LOOP_DONE>>","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0}
EOF
bash -c 'exec -a serve.py sleep 60' & dashfake=$!
sleep 0.2
printf '{"pid":%s,"port":9,"url":"http://127.0.0.1:9","sidecar":true}' "$dashfake" > "$WT/$LD/runtime/dashboard.json"
( cd "$WT" && AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99 bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "orphan sidecar: loop reaches DONE"
grep -q "(adopted)" "$WT/$LD/run.log"; assert_false $? "orphan sidecar: not adopted"
grep -q "dashboard http://127.0.0.1:1" "$WT/$LD/run.log"; assert_true $? "orphan sidecar: a fresh sidecar is spawned instead"
kill "$dashfake" 2>/dev/null; wait "$dashfake" 2>/dev/null

assert_summary
