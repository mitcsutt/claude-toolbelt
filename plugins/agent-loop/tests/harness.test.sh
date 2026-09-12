#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
# shellcheck source=../../../tests/lib/assert.sh
source "$ROOT/tests/lib/assert.sh"
source "$HERE/../lib/events.sh"
source "$HERE/../lib/harness.sh"

# `[[ ... ]]` followed by `assert_* $?` trips SC2319 (that $? is a condition's, not a
# command's), so file/format predicates go through real commands.
isdir()  { [[ -d "$1" ]]; }
isfile() { [[ -f "$1" ]]; }
isint()  { [[ "$1" =~ ^[0-9]+$ ]]; }

# --- classify_cause: rc + result json + sentinel -> one cause token ---
assert_eq "ok"          "$(classify_cause 0 '{"is_error":false}' CONTINUE)"  "rc0+sentinel -> ok"
assert_eq "timeout"     "$(classify_cause 124 '' NONE)"                      "124 -> timeout"
assert_eq "killed"      "$(classify_cause 137 '' NONE)"                      "137 -> killed"
assert_eq "terminated"  "$(classify_cause 143 '' NONE)"                      "143 -> terminated"
assert_eq "rate_limit"  "$(classify_cause 1 '{"is_error":true,"api_error_status":429}' NONE)" "429 -> rate_limit"
assert_eq "api_error"   "$(classify_cause 1 '{"is_error":true,"api_error_status":500}' NONE)" "500 -> api_error"
assert_eq "no_sentinel" "$(classify_cause 0 '{"is_error":false,"result":"hi"}' NONE)" "rc0 no sentinel -> no_sentinel"
assert_eq "crashed"     "$(classify_cause 1 '' NONE)"                        "rc1 empty -> crashed"

# --- cause_human: operator-readable gloss (the tick summary appends it) ---
case "$(cause_human killed)" in *SIGKILL*) assert_true 0 "cause_human killed names SIGKILL" ;;
  *) assert_true 1 "cause_human killed names SIGKILL" ;; esac
assert_eq "ok" "$(cause_human ok)" "cause_human ok"

# --- memory guard: pure decision + platform probe ---
assert_eq "proceed" "$(mem_guard_action 4000 20 1024 90)" "plenty -> proceed"
assert_eq "proceed" "$(mem_guard_action 500 20 1024 90)"  "low free but low swap -> proceed"
assert_eq "delay"   "$(mem_guard_action 500 95 1024 90)"  "low free AND high swap -> delay"
assert_eq "delay"   "$(mem_guard_action 500 90 1024 90)"  "swap exactly at the ceiling -> delay"
mh="$(mem_headroom)"
two_ints() { [[ "$1" =~ ^[0-9]+\ [0-9]+$ ]]; }
two_ints "$mh"; assert_true $? "mem_headroom prints two ints ($mh)"

# --- lock: mkdir-atomic acquire, PID-verified takeover, owner-only release ---
TMP="$(mktemp -d)"; RT="$TMP/runtime"; mkdir -p "$RT"
assert_eq "acquired" "$(harness_lock_acquire "$RT" $$ "$TMP" 2.0.0)" "first acquire"
assert_eq "$$" "$(jq -r .pid "$RT/harness.json")" "harness.json records pid"
assert_eq "2.0.0" "$(jq -r .plugin_version "$RT/harness.json")" "harness.json records version"
isdir "$RT/harness.lock.d"; assert_true $? "acquire creates the lock dir"
# a live owner must LOOK like run.sh to `ps -o command=`: fake one with exec -a
bash -c 'exec -a run.sh sleep 30' & fake=$!
sleep 0.2
harness_alive "$fake"; assert_true $? "exec -a run.sh process counts as a live harness"
printf '{"pid":%s,"start_epoch":1,"host":"x","loop_dir":"%s","plugin_version":"2.0.0"}' "$fake" "$TMP" > "$RT/harness.json"
out="$(harness_lock_acquire "$RT" 99999 "$TMP" 2.0.0)"; rc=$?
held_by() { [[ "$1" == "held:$2:"* ]]; }
held_by "$out" "$fake"; assert_true $? "second acquire reports held by live owner ($out)"
assert_eq "1" "$rc" "held -> exit 1"
kill "$fake" 2>/dev/null; wait "$fake" 2>/dev/null
# stale takeover: fake a dead pid in harness.json
sleep 0.01 & dead=$!; wait $dead
printf '{"pid":%s,"start_epoch":1,"host":"x","loop_dir":"%s","plugin_version":"2.0.0"}' "$dead" "$TMP" > "$RT/harness.json"
out="$(harness_lock_acquire "$RT" $$ "$TMP" 2.0.0)"
assert_eq "takeover:$dead" "$out" "dead owner -> takeover"
assert_eq "$$" "$(jq -r .pid "$RT/harness.json")" "takeover rewrites harness.json to the new owner"
harness_lock_release "$RT" $$
isdir "$RT/harness.lock.d"; assert_false $? "release removes lock dir"
harness_lock_release "$RT" 12345; assert_true $? "release by non-owner is a no-op that exits 0"
# harness_alive: this test shell is not run.sh
harness_alive $$; assert_false $? "a live pid whose command lacks run.sh is not a harness"
harness_alive 999999; assert_false $? "dead pid -> not alive"
harness_alive ""; assert_false $? "empty pid -> not alive"

# --- two harnesses racing for ONE stale lock: exactly one wins ---
# Clearing a stale lock must not be an acquisition, or a crashed harness's leftover
# dir lets every starter declare itself the owner at once.
RT2="$TMP/rt2"; mkdir -p "$RT2/harness.lock.d"
sleep 0.01 & dead2=$!; wait $dead2
printf '{"pid":%s,"start_epoch":1,"host":"x","loop_dir":"%s","plugin_version":"2.0.0"}' "$dead2" "$TMP" > "$RT2/harness.json"
# both contenders must LOOK like run.sh, so whichever wins is a live harness to the loser
bash -c 'exec -a run.sh sleep 30' & fa=$!
bash -c 'exec -a run.sh sleep 30' & fb=$!
sleep 0.2
( harness_lock_acquire "$RT2" "$fa" "$TMP" 2.0.0 > "$TMP/race-a" ) & ra=$!
( harness_lock_acquire "$RT2" "$fb" "$TMP" 2.0.0 > "$TMP/race-b" ) & rb=$!
wait $ra; wait $rb
RACE="$(printf '%s\n%s\n' "$(cat "$TMP/race-a")" "$(cat "$TMP/race-b")")"
assert_eq "1" "$(printf '%s\n' "$RACE" | grep -c '^takeover:' | tr -d ' ')" "concurrent stale takeover: exactly one takeover [$RACE]"
assert_eq "1" "$(printf '%s\n' "$RACE" | grep -c '^held:' | tr -d ' ')" "concurrent stale takeover: the loser reports held [$RACE]"
assert_eq "$(jq -r .pid "$RT2/harness.json")" "$(printf '%s\n' "$RACE" | grep '^held:' | cut -d: -f2)" \
  "the loser names the winner recorded in harness.json"
kill "$fa" "$fb" 2>/dev/null; wait "$fa" 2>/dev/null; wait "$fb" 2>/dev/null

# --- heartbeat_loop stops when its harness is gone (SIGKILL leaves no EXIT trap) ---
RT3="$TMP/rt3"; mkdir -p "$RT3"
sleep 5 & hbparent=$!
( heartbeat_loop "$RT3" 1 "$hbparent" ) & hb2=$!
sleep 0.3
kill -9 "$hbparent" 2>/dev/null; wait "$hbparent" 2>/dev/null
sleep 1.6
kill -0 "$hb2" 2>/dev/null; assert_false $? "heartbeat_loop exits once its parent pid is gone"
kill "$hb2" 2>/dev/null; wait "$hb2" 2>/dev/null

# --- incident_new: numbered id, sidecar json file, and one incident event ---
EV="$TMP/events.jsonl"
id="$(incident_new "$RT" "$EV" tick-killed error "rc=137" 7)"
assert_eq "i-001" "$id" "first incident id"
assert_eq "tick-killed" "$(jq -r .kind "$RT/incident-$id.json")" "incident file written"
assert_eq "error" "$(jq -r .severity "$RT/incident-$id.json")" "incident severity written"
assert_eq "7" "$(jq -r .tick "$RT/incident-$id.json")" "incident tick written"
assert_eq "incident" "$(tail -1 "$EV" | jq -r .type)" "incident event emitted"
assert_eq "i-001" "$(tail -1 "$EV" | jq -r .id)" "incident event carries the id"
assert_eq "i-002" "$(incident_new "$RT" "$EV" tick-timeout error "rc=124" 8)" "ids increment"

# --- write_tick_json: the per-tick liveness sidecar the dashboard reads ---
write_tick_json "$RT" 3 4242 100 1800
assert_eq "4242" "$(jq -r .pid "$RT/tick.json")" "tick.json pid"
assert_eq "3"    "$(jq -r .tick "$RT/tick.json")" "tick.json tick"
assert_eq "1800" "$(jq -r .timeout_s "$RT/tick.json")" "tick.json timeout_s"
assert_eq "number" "$(jq -r '.started_at|type' "$RT/tick.json")" "tick.json started_at is a number"

# --- heartbeat_loop: one epoch line per interval, restartable ---
( heartbeat_loop "$RT" 1 ) & hb=$!
sleep 0.4
kill "$hb" 2>/dev/null; wait "$hb" 2>/dev/null
isint "$(cat "$RT/HEARTBEAT" 2>/dev/null)"; assert_true $? "heartbeat_loop writes an epoch"

# --- notify_desktop: best-effort, never fails, suppressible ---
LOOP_NOTIFY=0 notify_desktop "t" "b"; assert_true $? "notify_desktop is a no-op under LOOP_NOTIFY=0"

rm -rf "$TMP"
assert_summary
