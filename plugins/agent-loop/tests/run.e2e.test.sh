#!/usr/bin/env bash
# End-to-end: drive run.sh against the scripted `claude` stub and assert on the
# artefacts a real operator, the dashboard and the skills read off disk.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
# shellcheck source=../../../tests/lib/assert.sh
source "$ROOT/tests/lib/assert.sh"
RUN="$HERE/../run.sh"

if ! command -v python3 >/dev/null 2>&1; then
  echo "(run.e2e skipped — python3 not installed; the harness is a python program)"
  exit 0
fi

# `[[ ... ]]` followed by `assert_* $?` trips SC2319, so predicates are real commands.
isfile() { [ -f "$1" ]; }
isdir()  { [ -d "$1" ]; }
isint()  { case "${1:-}" in '' | *[!0-9]*) return 1 ;; *) return 0 ;; esac }
has()    { case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac }

LD=".claude/loop/test-run"

# reply <NNN> <json-document>
#   Writes $STUB/NNN.jsonl: one stream-json result line whose text ends in the
#   fenced JSON block the harness parses, with a usage rollup attached.
reply() {
  python3 - "$STUB/$1.jsonl" "$2" <<'PY'
import json
import sys
path, doc = sys.argv[1], sys.argv[2]
record = {"type": "result", "subtype": "success", "is_error": False,
          "result": "ok\n```json\n%s\n```" % doc,
          "modelUsage": {"stub-model": {"costUSD": 0.01, "inputTokens": 10,
                                        "outputTokens": 5,
                                        "cacheReadInputTokens": 0,
                                        "cacheCreationInputTokens": 0}}}
with open(path, "w") as handle:
    handle.write(json.dumps(record) + "\n")
PY
}

# side <NNN> <shell body>
side() { printf '%s\n' "$2" > "$STUB/$1.sh"; }

# contract_for <NNN> <task-id>
#   A side-effect script that writes a valid sprint contract for the task.
contract_for() {
  side "$1" "cat > \"\$RUNTIME_DIR/sprint-$2.json\" <<'JSON'
{\"task\":\"$2\",\"success_criteria\":[\"src/a.ts exports parse\"],
 \"allow_list\":[\"src/a.ts\"],\"forbidden\":[],\"verification\":[\"true\"],
 \"evaluator_must_read\":[],\"evaluator_must_view\":[],
 \"estimated_diff_lines\":5,\"scout_notes\":\"inline\",\"relevant_learnings\":[]}
JSON"
  reply "$1" "{\"contract_path\":\"sprint-$2.json\",\"notes\":\"ok\"}"
}

# setup_case [plan-body]
setup_case() {
  WT="$(mktemp -d)"
  (
    cd "$WT" || exit 1
    git init -q
    git config user.email loop@example.com
    git config user.name Loop
    git config commit.gpgsign false
    mkdir -p src
    printf 'export const a = 0\n' > src/a.ts
    git add -A
    git commit -q -m seed
  ) >/dev/null 2>&1
  mkdir -p "$WT/$LD/runtime"
  # OUTSIDE the worktree. The stub is a stand-in for the `claude` binary, and a
  # real one does not live in the user's repo -- but more concretely, the stub
  # keeps its invocation counter in `.n`, and anything untracked inside the
  # worktree is a stray the SANDBOX reverts after every Worker phase (spec
  # §6.4). With the stub inside, the counter is deleted mid-run and every later
  # phase replays the first scripted reply.
  STUB="$(mktemp -d)"
  mkdir -p "$STUB"
  printf '%s\n' 'runtime/' 'artifacts/' > "$WT/$LD/.gitignore"
  cat > "$WT/$LD/LOOP_CONFIG.md" <<EOF
Worktree: $WT
Verification pipeline: lint
Tiers: cheap=tier-cheap standard=tier-standard most-capable=tier-big
Dashboard: off
Medic: off
Limits: tick_timeout=120 scout_timeout=30 worker_timeout=30 eval_timeout=30 learner_timeout=30 gate_cmd_timeout=30
EOF
  printf '%s\n' "${1:-- [ ] T1: Add the parser}" > "$WT/$LD/LOOP_PLAN.md"
  export LOOP_DIR="$LD" STUB_SCRIPT="$STUB"
  export PATH="$HERE/fixtures:$PATH"
  export LOOP_NOTIFY=0 AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99
  export LOOP_DASHBOARD=off
  unset LOOP_DASHBOARD_CMD MOCK_MEDIC_SCRIPT STUB_LOG STUB_DELAY 2>/dev/null || true
}

ev() { jq -r "select(.type==\"$1\") | ${2:-.type}" "$WT/$LD/events.jsonl"; }

# ---------------------------------------------------------------- happy path
setup_case
contract_for 001 T1
side 002 "printf 'export const parse = () => 1\n' > \"$WT/src/a.ts\""
reply 002 '{"status":"complete","summary":"did it"}'
reply 003 '{"verdict":"PASS","findings":[],"views":[],"summary":"criteria met"}'
reply 004 '{"patterns":[],"log":"T1 was straightforward","invariants":[]}'
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "happy path exits 0 once the plan is finished"
assert_eq "done" "$(ev loop_end .reason | tail -1)" "loop_end reason=done"
assert_eq "loop_start" "$(head -1 "$WT/$LD/events.jsonl" | jq -r .type)" "loop_start is first"
assert_eq "loop_end" "$(tail -1 "$WT/$LD/events.jsonl" | jq -r .type)" "loop_end is last"
assert_eq "T1" "$(ev task_status .id | head -1)" "task_status names the task"
assert_eq "continue" "$(ev tick_end .verdict | head -1)" "tick_end verdict=continue"
grep -q '"by_model"' "$WT/$LD/events.jsonl"; assert_true $? "tick_end carries by_model"
grep -q '^- \[x\] T1:' "$WT/$LD/LOOP_PLAN.md"; assert_true $? "the row is marked [x]"
grep -q 'done(+' "$WT/$LD/LOOP_PLAN.md"; assert_true $? "the row carries the sha"
# Captured first, then matched: `git log | grep -q` exits 141 under `pipefail`
# because grep closes the pipe on its first match and git takes the SIGPIPE --
# so the assertion would fail precisely when the trailer IS present. And the
# work commit is not HEAD: `flush_plan` puts a checkpoint commit on top of it.
ALL_BODIES="$( cd "$WT" && git log --format=%B )"
ALL_SUBJECTS="$( cd "$WT" && git log --format=%s )"
case "$ALL_BODIES" in *"$(printf '\nLoop-Status: done\n')"*) true ;; *) false ;; esac
assert_true $? "the work commit carries Loop-Status: done"
case "$ALL_SUBJECTS" in *T1*) true ;; *) false ;; esac
assert_true $? "the subject names the task"
assert_eq "1" "$(wc -l < "$WT/$LD/LOOP_USAGE.jsonl" | tr -d ' ')" "the ledger has one row"
assert_eq "execute" "$(jq -r .mode "$WT/$LD/LOOP_USAGE.jsonl")" "the ledger records the mode"
grep -q '── loop' "$WT/$LD/LOOP_STATUS.md"; assert_true $? "LOOP_STATUS has the session header"
grep -q '^✓ t' "$WT/$LD/LOOP_STATUS.md"; assert_true $? "LOOP_STATUS has a per-tick line"
isfile "$WT/$LD/harness.log"; assert_true $? "harness.log is written"
isfile "$WT/$LD/run.log"; assert_false $? "run.log is gone"
isfile "$WT/$LD/runtime/harness.json"; assert_false $? "the lock is released on exit"
isfile "$WT/$LD/runtime/tick.json"; assert_false $? "tick.json is removed after the tick"
isint "$(cat "$WT/$LD/runtime/HEARTBEAT" 2>/dev/null)"; assert_true $? "HEARTBEAT holds an epoch"
assert_eq "3" "$(cat "$WT/$LD/runtime/schema")" "a fresh dir is stamped schema 3"
isdir "$WT/$LD/artifacts"; assert_true $? "artifacts/ exists"
isfile "$WT/$LD/LOOP_DECISIONS.md"; assert_true $? "LOOP_DECISIONS.md exists"
assert_eq "1" "$(jq -r .attempt "$WT/$LD/runtime/task-T1.json")" "task-T1.json records one attempt"
assert_eq "pass" "$(jq -r '.attempts[0].outcome' "$WT/$LD/runtime/task-T1.json")" "the attempt outcome is pass"
assert_eq "LEARN:done" "$(jq -r .phase "$WT/$LD/runtime/task-T1.json")" "the recorded phase ends at LEARN:done"
isfile "$WT/$LD/runtime/attempts-T1.json"; assert_false $? "nothing writes the old attempts file"

# ------------------------------------------------------------- lock conflict
setup_case
contract_for 001 T1
printf '30\n' > "$STUB/001.sleep"
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 ) &
first=$!
tries=0
while [ "$tries" -lt 200 ]; do
  [ -f "$WT/$LD/runtime/harness.json" ] && break
  sleep 0.1
  tries=$((tries + 1))
done
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 )
assert_eq "3" "$?" "a second harness on a live LOOP_DIR exits 3"
assert_eq "lock-conflict" "$(ev incident .kind | head -1)" "the refusal is an incident"
assert_eq "warn" "$(ev incident .severity | head -1)" "a refused starter is warn severity"
assert_eq "0" "$(ev loop_end .reason | grep -c 'lock-conflict')" "no loop_end in the live loop's stream"
kill "$first" 2>/dev/null
wait "$first" 2>/dev/null

# ------------------------------------------------------------------ takeover
setup_case "- [x] T1: already done"
sleep 0.01 & deadpid=$!
wait "$deadpid"
printf '{"pid":%s,"start_epoch":1,"host":"x","loop_dir":"x","plugin_version":"2.1.0"}' \
  "$deadpid" > "$WT/$LD/runtime/harness.json"
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "a stale lock is taken over and the run completes"
grep -q 'taking over' "$WT/$LD/harness.log"; assert_true $? "the takeover is logged"
assert_eq "harness-crash" "$(ev incident .kind | head -1)" "the takeover raises harness-crash"
assert_eq "warn" "$(ev incident .severity | head -1)" "harness-crash is warn with Medic: off"

# --------------------------------------------------- PAUSE between two phases
setup_case
contract_for 001 T1
side 001 "cat > \"\$RUNTIME_DIR/sprint-T1.json\" <<'JSON'
{\"task\":\"T1\",\"success_criteria\":[\"src/a.ts exports parse\"],
 \"allow_list\":[\"src/a.ts\"],\"forbidden\":[],\"verification\":[\"true\"],
 \"evaluator_must_read\":[],\"evaluator_must_view\":[],
 \"estimated_diff_lines\":5,\"scout_notes\":\"inline\",\"relevant_learnings\":[]}
JSON
touch \"\$RUNTIME_DIR/PAUSE\""
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "PAUSE between phases exits 0"
assert_eq "paused" "$(ev loop_end .reason | tail -1)" "loop_end reason=paused"
assert_eq "T1" "$(jq -r .next_task "$WT/$LD/runtime/CHECKPOINT.json")" "the checkpoint names the next task"
assert_eq "1" "$(cat "$STUB/.n")" "the Worker never ran — PAUSE landed after the Scout"
assert_eq "1" "$(ev paused .tick | wc -l | tr -d ' ')" "one paused event"

# -------------------------------------------- STOP kills the phase in flight
setup_case
side 001 "touch \"\$RUNTIME_DIR/STOP\""
reply 001 '{"contract_path":"none","notes":"never parsed"}'
printf '60\n' > "$STUB/001.sleep"
started="$(date +%s)"
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 )
rc=$?
elapsed=$(( $(date +%s) - started ))
assert_eq "0" "$rc" "STOP exits 0"
assert_eq "paused" "$(ev loop_end .reason | tail -1)" "STOP reports loop_end reason=paused"
under() { [ "$1" -lt "$2" ]; }
under "$elapsed" 40; assert_true $? "STOP killed the phase instead of waiting it out (${elapsed}s)"
isfile "$WT/$LD/runtime/CHECKPOINT.json"; assert_true $? "STOP writes a checkpoint"

# ---------------------------------- Worker timeout still reports its spend
setup_case
contract_for 001 T1
reply 002 '{"status":"partial","summary":"ran out of time"}'
printf '60\n' > "$STUB/002.sleep"
sed -i.bak 's/worker_timeout=30/worker_timeout=2/' "$WT/$LD/LOOP_CONFIG.md"
rm -f "$WT/$LD/LOOP_CONFIG.md.bak"
( cd "$WT" && MEDIC_MAX_PER_RUN=0 bash "$RUN" >/dev/null 2>&1 )
assert_eq "2" "$?" "a Worker timeout with no medic ends in needs-human"
assert_eq "phase-timeout" "$(ev incident .kind | head -1)" "the timeout is an incident"
grep -q '"by_model"' "$WT/$LD/events.jsonl"; assert_true $? "tick_end still carries by_model after a kill"
assert_eq "1" "$(jq -r 'select(.type=="tick_end") | (.by_model | length)' "$WT/$LD/events.jsonl")" \
  "the killed tick attributes the spend it did incur"
isfile "$WT/$LD/runtime/NEEDS_HUMAN.md"; assert_true $? "NEEDS_HUMAN.md is written"

# ------------------------ NEEDS_WORK re-dispatches at the SAME tier
setup_case
contract_for 001 T1
side 002 "printf 'export const parse = () => 1\n' > \"$WT/src/a.ts\""
reply 002 '{"status":"complete","summary":"first try"}'
reply 003 '{"verdict":"NEEDS_WORK","findings":[],"views":[],"summary":"too thin"}'
side 004 "printf 'export const parse = () => 2\n' > \"$WT/src/a.ts\""
reply 004 '{"status":"complete","summary":"second try"}'
reply 005 '{"verdict":"PASS","findings":[],"views":[],"summary":"good now"}'
reply 006 '{"patterns":[],"log":"needed two goes","invariants":[]}'
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "the re-dispatch reaches done"
assert_eq "tier-standard" "$(jq -r 'select(.type=="role_start" and .role=="Worker") | .model' "$WT/$LD/events.jsonl" | head -1)" \
  "the first Worker runs at the configured tier"
assert_eq "tier-standard" "$(jq -r 'select(.type=="role_start" and .role=="Worker") | .model' "$WT/$LD/events.jsonl" | tail -1)" \
  "the second Worker runs at the SAME tier — there is no ladder in code"
assert_eq "retry" "$(ev decision .decision | head -1)" "the re-dispatch is recorded as a retry decision"
assert_eq "2" "$(jq -r '.attempts | length' "$WT/$LD/runtime/task-T1.json")" "both attempts are recorded"
assert_eq "standard standard" "$(jq -r '[.attempts[].tier] | join(" ")' "$WT/$LD/runtime/task-T1.json")" \
  "both attempts record the same dispatched tier"

# ------------------------------------------------- needs-human on a newer dir
setup_case "- [x] T1: already done"
printf '99' > "$WT/$LD/runtime/schema"
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 )
assert_eq "2" "$?" "a loop dir newer than the plugin exits 2"
assert_eq "needs-human" "$(ev loop_end .reason | tail -1)" "loop_end reason=needs-human"
assert_eq "schema-newer" "$(ev incident .kind | head -1)" "schema-newer incident"
grep -q 'update the plugin' "$WT/$LD/runtime/NEEDS_HUMAN.md"; assert_true $? "NEEDS_HUMAN says to update the plugin"
grep -q 'run.sh' "$WT/$LD/runtime/NEEDS_HUMAN.md"; assert_true $? "NEEDS_HUMAN carries the resume command"
assert_eq "99" "$(cat "$WT/$LD/runtime/schema")" "the stamp is untouched"

# ------------------------------------------------------------ migration 2 -> 3
setup_case "- [x] T1: already done"
printf '2' > "$WT/$LD/runtime/schema"
rm -f "$WT/$LD/.gitignore"
printf '%s' '{"task":"T1","forbidden":["apps/frontend/**"],"allow_list":["src/a.ts"]}' \
  > "$WT/$LD/runtime/sprint-T1.json"
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "a schema-2 dir is migrated and the run completes"
assert_eq "scout" "$(jq -r '.forbidden[0].source' "$WT/$LD/runtime/sprint-T1.json")" \
  "a 2.x string forbidden entry gains scout provenance"
assert_eq "apps/frontend/**" "$(jq -r '.forbidden[0].path' "$WT/$LD/runtime/sprint-T1.json")" \
  "the forbidden path itself survives"
assert_eq "null" "$(jq -r '.render_gate' "$WT/$LD/runtime/sprint-T1.json")" \
  "missing v3 contract fields get their defaults"
assert_eq "3" "$(cat "$WT/$LD/runtime/schema")" "runtime/schema is stamped 3"
assert_eq "2" "$(ev migration .from)" "the migration event records from=2"
assert_eq "3" "$(ev migration .to)" "the migration event records to=3"
isdir "$WT/$LD/artifacts"; assert_true $? "artifacts/ is created"
grep -q 'artifacts/' "$WT/$LD/.gitignore"; assert_true $? "artifacts/ is gitignored"
grep -q 'schema 2 -> 3' "$WT/$LD/harness.log"; assert_true $? "the migration is logged"

# ----------------------------------------------------- halt on a stuck plan
setup_case "$(printf '%s\n' '## Segment A' 'Reviewed: aaa' '- [!] T1: blocked by hand' '- [ ] T2: waits on it | depends_on: T1')"
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 )
assert_eq "1" "$?" "a plan with work left but nothing runnable exits 1"
assert_eq "halt" "$(ev loop_end .reason | tail -1)" "loop_end reason=halt"
assert_eq "1" "$(ev loop_end .exit_code | tail -1)" "loop_end carries exit_code 1"
assert_eq "0" "$(cat "$STUB/.n" 2>/dev/null || echo 0)" "no phase ran"

# -------------------------------------------------------- sidecar adoption
setup_case "- [x] T1: already done"
export LOOP_DASHBOARD=auto
export LOOP_DASHBOARD_CMD="$HERE/fixtures/dashboard-stub"
bash -c 'exec -a serve.py sleep 60' &
dashfake=$!
sleep 0.2
printf '{"pid":%s,"port":7,"url":"http://127.0.0.1:7","sidecar":false}' "$dashfake" \
  > "$WT/$LD/runtime/dashboard.json"
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "the adopting run completes"
grep -q 'dashboard http://127.0.0.1:7 (adopted)' "$WT/$LD/harness.log"; assert_true $? "the existing URL is announced as adopted"
isfile "$WT/$LD/runtime/sidecar.pid"; assert_false $? "no sidecar is spawned when one is adopted"
assert_eq "http://127.0.0.1:7" "$(ev loop_start .dashboard_url | tail -1)" "loop_start carries the adopted URL"
kill -0 "$dashfake" 2>/dev/null; assert_true $? "the standalone dashboard outlives the harness"
assert_eq "$dashfake" "$(jq -r .pid "$WT/$LD/runtime/dashboard.json")" "dashboard.json is untouched"
kill "$dashfake" 2>/dev/null
wait "$dashfake" 2>/dev/null

assert_summary
