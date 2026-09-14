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

# limits <Limits: line>  — rewrite the whole config so there is exactly one
# `Limits:` line and the judge's own keys are present.
limits() {
  cat > "$WT/$LD/LOOP_CONFIG.md" <<EOF
Worktree: $WT
Verification pipeline: lint
Tiers: cheap=tier-cheap standard=tier-standard most-capable=tier-big
Dashboard: off
Medic: off
Decision policy: ${2:-autonomous}
Limits: $1
EOF
}

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

# ------------- Worker timeout: the runner wraps up and judges, it does not escalate
setup_case
contract_for 001 T1
reply 002 '{"status":"partial","summary":"ran out of time"}'
printf '60\n' > "$STUB/002.sleep"
# 003 is the wrap-up on the killed session; 004 is the Judge it is handed to.
reply 003 '{"status":"partial","summary":"checkpoint written"}'
reply 004 '{"decision":"defer","classification":"capability","rationale":"one phase budget was not enough and the checkpoint shows no progress","instruction":"","alternatives":["resume the session"],"reversal":"clear the [!] on T1","changes":{}}'
sed -i.bak 's/worker_timeout=30/worker_timeout=2/' "$WT/$LD/LOOP_CONFIG.md"
rm -f "$WT/$LD/LOOP_CONFIG.md.bak"
( cd "$WT" && MEDIC_MAX_PER_RUN=0 bash "$RUN" >/dev/null 2>&1 )
rc=$?
# Spec §8: a timeout the runner resolves is an EVENT, not an incident. v2 woke a
# human here; v3 runs the wrap-up, keeps the checkpoint and asks the Judge.
assert_eq "" "$(ev incident .kind | head -1)" "a resolved Worker timeout raises no incident"
assert_eq "wrapup" "$(jq -r 'select(.type=="resume") | .kind' "$WT/$LD/events.jsonl" | head -1)" \
  "the timeout is recorded as a wrap-up resume event"
assert_eq "worker-wrapup" "$(jq -r 'select(.type=="role_start" and .role=="worker-wrapup") | .role' "$WT/$LD/events.jsonl" | head -1)" \
  "the wrap-up phase actually ran"
assert_eq "defer" "$(ev decision .decision | tail -1)" "the Judge decided what happens next"
assert_eq "1" "$rc" "a deferral with nothing else eligible halts rather than needing a human"
grep -q '"by_model"' "$WT/$LD/events.jsonl"; assert_true $? "tick_end still carries by_model after a kill"
assert_eq "1" "$(jq -r 'select(.type=="tick_end") | (.by_model | length)' "$WT/$LD/events.jsonl")" \
  "the killed tick attributes the spend it did incur"

# ------------ NEEDS_WORK goes to the Judge, which re-dispatches at the SAME tier
setup_case
contract_for 001 T1
side 002 "printf 'export const parse = () => 1\n' > \"$WT/src/a.ts\""
reply 002 '{"status":"complete","summary":"first try"}'
reply 003 '{"verdict":"NEEDS_WORK","findings":[],"views":[],"summary":"too thin"}'
reply 004 '{"decision":"retry","classification":"capability","rationale":"one more go at the same tier","instruction":"flesh the parser out","alternatives":["escalate"],"reversal":"none","changes":{}}'
side 005 "printf 'export const parse = () => 2\n' > \"$WT/src/a.ts\""
reply 005 '{"status":"complete","summary":"second try"}'
reply 006 '{"verdict":"PASS","findings":[],"views":[],"summary":"good now"}'
reply 007 '{"patterns":[],"log":"needed two goes","invariants":[]}'
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "the re-dispatch reaches done"
assert_eq "tier-big" "$(jq -r 'select(.type=="role_start" and .role=="judge") | .model' "$WT/$LD/events.jsonl" | head -1)" \
  "the Judge runs at the most-capable tier (tier-big in this config)"
assert_eq "tier-standard" "$(jq -r 'select(.type=="role_start" and .role=="Worker") | .model' "$WT/$LD/events.jsonl" | head -1)" \
  "the first Worker runs at the configured tier"
assert_eq "tier-standard" "$(jq -r 'select(.type=="role_start" and .role=="Worker") | .model' "$WT/$LD/events.jsonl" | tail -1)" \
  "the second Worker runs at the SAME tier — the Judge named none, and there is no ladder in code"
assert_eq "retry" "$(ev decision .decision | head -1)" "the re-dispatch is the Judge's retry decision"
assert_eq "capability" "$(ev decision .classification | head -1)" "and it carries the Judge's own §7 classification"
assert_eq "2" "$(jq -r '.attempts | length' "$WT/$LD/runtime/task-T1.json")" "both attempts are recorded"
assert_eq "standard standard" "$(jq -r '[.attempts[].tier] | join(" ")' "$WT/$LD/runtime/task-T1.json")" \
  "both attempts record the same dispatched tier"
assert_eq "retry" "$(jq -r '.attempts[0].judge.decision' "$WT/$LD/runtime/task-T1.json")" \
  "the decision is recorded on the attempt it closed"
grep -q "flesh the parser out" "$WT/$LD/LOOP_DECISIONS.md"; assert_true $? \
  "LOOP_DECISIONS.md records the instruction, not only that one was applied"
grep -q "JUDGE" "$WT/$LD/runtime/sprint-T1.json"; assert_true $? \
  "and the instruction reaches the next Worker through the contract"

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

# ------------------------------------------------------------------
# A scout-sourced constraint blocks the gate; the Judge widens it and the retry
# passes. This is the 2026-09-11 T60 halt, decided without waking anyone.
# ------------------------------------------------------------------
setup_case "- [ ] T1: Create both source files"
limits "tick_timeout=120 scout_timeout=30 worker_timeout=30 wrapup_timeout=20 eval_timeout=30 judge_timeout=30 learner_timeout=30 gate_cmd_timeout=30 worker_resume_max=0 max_attempts=3"
# 001 scout: a contract whose own gate needs a file its own `forbidden` blocks.
# The contract is VALID -- every path it names is inside `allow_list`. What
# blocks it is the Scout's own `forbidden` entry, which no plan or spec line
# supports, so the Worker obeys it and the gate then fails for exactly that.
side 001 "cat > \"\$RUNTIME_DIR/sprint-T1.json\" <<'JSON'
{\"task\":\"T1\",\"success_criteria\":[\"src/a.ts exists\",\"src/b.ts exists\"],
 \"allow_list\":[\"src/**\"],
 \"forbidden\":[{\"path\":\"src/b.ts\",\"source\":\"scout\"}],
 \"verification\":[\"test -f src/b.ts\"],
 \"evaluator_must_read\":[],\"evaluator_must_view\":[],
 \"estimated_diff_lines\":10,\"scout_notes\":\"\",\"relevant_learnings\":[]}
JSON"
reply 001 '{"contract_path":"sprint-T1.json","notes":"ok"}'
side 002 "printf 'export const a = 1\n' > \"$WT/src/a.ts\""
reply 002 '{"status":"complete","summary":"a only; b is forbidden"}'
reply 003 '{"decision":"widen","classification":"self-imposed","rationale":"the forbidden entry for src/b.ts is scout-sourced and no plan line supports it","instruction":"create src/b.ts as well","alternatives":["defer to a human, as the 2026-09-11 run did"],"reversal":"re-add the forbidden entry to runtime/sprint-T1.json","changes":{"allow_list_add":["src/b.ts"],"forbidden_remove":["src/b.ts"],"default_choice":"","blocks":[],"sub_rows":[],"tier":"","extend_cap_s":0}}'
side 004 "printf 'export const b = 2\n' > \"$WT/src/b.ts\""
reply 004 '{"status":"complete","summary":"both files now"}'
reply 005 '{"verdict":"PASS","findings":[{"criterion":"src/b.ts exists","met":true,"evidence":"gate"}],"views":[],"summary":"ok"}'
reply 006 '{"patterns":[],"log":"widened once","invariants":[]}'
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "judge-widen scenario exits 0"
grep -q '— widen (self-imposed)' "$WT/$LD/LOOP_DECISIONS.md"; assert_true $? \
  "LOOP_DECISIONS.md records the widen with its classification"
grep -q '"src/b.ts"' "$WT/$LD/runtime/sprint-T1.json"; assert_true $? \
  "the contract's allow_list was widened on disk"
grep -q '^- \[x\] T1' "$WT/$LD/LOOP_PLAN.md"; assert_true $? \
  "T1 completed after the widen"
grep -q '\[!\]' "$WT/$LD/LOOP_PLAN.md"; assert_false $? \
  "nothing was marked [!] — the loop answered a question its own files answered"
assert_eq "widen" "$(ev decision .decision | head -1)" "the decision event names the widen"
assert_eq "self-imposed" "$(ev decision .classification | head -1)" \
  "and carries the Judge's §7 classification"
isfile "$WT/$LD/runtime/NEEDS_HUMAN.md"; assert_false $? "no human was needed"

# ------------------------------------------------------------------
# The Worker overruns, the wrap-up checkpoints, the resume budget is zero, and
# the Judge splits the task into two numeric sub-tasks that both complete.
# ------------------------------------------------------------------
setup_case "- [ ] T1: Build both halves"
limits "tick_timeout=180 scout_timeout=30 worker_timeout=2 wrapup_timeout=20 eval_timeout=30 judge_timeout=30 learner_timeout=30 gate_cmd_timeout=30 worker_resume_max=0 max_attempts=3"
split_contract() {   # $1 = script number, $2 = task id, $3 = file its gate wants
  side "$1" "cat > \"\$RUNTIME_DIR/sprint-$2.json\" <<'JSON'
{\"task\":\"$2\",\"success_criteria\":[\"$3 exists\"],\"allow_list\":[\"src/**\"],
 \"forbidden\":[],\"verification\":[\"test -f $3\"],
 \"evaluator_must_read\":[],\"evaluator_must_view\":[],
 \"estimated_diff_lines\":10,\"scout_notes\":\"\",\"relevant_learnings\":[]}
JSON"
  reply "$1" "{\"contract_path\":\"sprint-$2.json\",\"notes\":\"ok\"}"
}
finish_task() {      # $1 = scout script number, $2 = task id, $3 = file to create
  split_contract "$1" "$2" "$3"
  n2=$(printf '%03d' $((10#$1 + 1)))
  n3=$(printf '%03d' $((10#$1 + 2)))
  n4=$(printf '%03d' $((10#$1 + 3)))
  side "$n2" "printf 'done\n' > \"$WT/$3\""
  reply "$n2" '{"status":"complete","summary":"ok"}'
  reply "$n3" '{"verdict":"PASS","findings":[],"views":[],"summary":"ok"}'
  reply "$n4" '{"patterns":[],"log":"ok","invariants":[]}'
}
split_contract 001 T1 src/both
printf '20\n' > "$STUB/002.sleep"                       # the Worker overruns
side 003 "printf '{\"task\":\"T1\",\"status\":\"partial\",\"files_touched\":[],\"summary\":\"half\",\"checkpoint\":\"src/a then src/b\",\"next_steps\":[\"src/a\",\"src/b\"]}' > \"\$RUNTIME_DIR/worker-result.json\""
reply 003 '{"status":"partial","summary":"out of time"}'
reply 004 '{"decision":"split","classification":"capability","rationale":"the wrap-up checkpoint shows two independently verifiable halves and the resume budget is spent","instruction":"","alternatives":["escalate to the next tier"],"reversal":"revert the split commit and restore T1","changes":{"allow_list_add":[],"forbidden_remove":[],"default_choice":"","blocks":[],"sub_rows":["- [ ] Build the first half","- [ ] Build the second half"],"tier":"","extend_cap_s":0}}'
# The harness allocates the ids: T1 is the only row, so the halves are T2 and T3.
finish_task 005 T2 src/a
finish_task 009 T3 src/b
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "judge-split scenario exits 0"
grep -q 'split→T2,T3' "$WT/$LD/LOOP_PLAN.md"; assert_true $? \
  "the parent row records the split into numeric ids"
grep -q '| split_of: T1' "$WT/$LD/LOOP_PLAN.md"; assert_true $? \
  "each sub row carries | split_of: T1"
grep -q 'T1a' "$WT/$LD/LOOP_PLAN.md"; assert_false $? \
  "no lettered ids anywhere: serve.py matches \\bT\\d+\\b"
grep -q '^- \[-\] T1' "$WT/$LD/LOOP_PLAN.md"; assert_true $? \
  "the parent task carries the [-] glyph"
git -C "$WT" log --pretty=%B > "$STUB/log.txt"
grep -q 'loop: split T1 → T2,T3' "$STUB/log.txt"; assert_true $? \
  "the split commit subject is exact"
grep -q 'Loop-Status: skipped' "$STUB/log.txt"; assert_true $? \
  "the split commit carries Loop-Status: skipped"
assert_eq "T1" "$(ev split .task | head -1)" "a split event was emitted for T1"
assert_eq "T2 T3" "$(jq -r 'select(.type=="split") | (.into | join(" "))' "$WT/$LD/events.jsonl")" \
  "and it names both new ids"
assert_eq "wrapup" "$(jq -r 'select(.type=="resume") | .kind' "$WT/$LD/events.jsonl" | head -1)" \
  "the overrun produced a wrap-up, not an incident"
grep -q '^- \[x\] T2' "$WT/$LD/LOOP_PLAN.md"; assert_true $? "the first half completed"
grep -q '^- \[x\] T3' "$WT/$LD/LOOP_PLAN.md"; assert_true $? "the second half completed"

# --------------------------- RENDER + FIDELITY: the loop sees what it built
# Plan B's own file table adds no e2e scenario, so the render gate and the
# fidelity check would never run through the harness. These two do.
setup_case
# A contract with a render gate that writes a real PNG, and a fidelity pair the
# Worker will honestly port. `printf` writes the PNG magic bytes; bash 3.2
# understands the octal escapes.
side 001 "cat > \"\$RUNTIME_DIR/sprint-T1.json\" <<'JSON'
{\"task\":\"T1\",\"success_criteria\":[\"src/a.ts exports parse\"],
 \"allow_list\":[\"src/a.ts\"],\"forbidden\":[],\"verification\":[\"true\"],
 \"render_gate\":{\"commands\":[\"mkdir -p shots && printf '\\\\211PNG\\\\r\\\\n\\\\032\\\\n' > shots/home.png\"],
                \"screenshots\":[{\"name\":\"home\",\"path\":\"shots/home.png\"}]},
 \"fidelity_source\":[{\"from\":\"src/ref.ts\",\"to\":\"src/a.ts\",\"min_similarity\":0.6}],
 \"evaluator_must_read\":[],\"evaluator_must_view\":[],
 \"estimated_diff_lines\":5,\"scout_notes\":\"inline\",\"relevant_learnings\":[]}
JSON"
reply 001 '{"contract_path":"sprint-T1.json","notes":"ok"}'
# The reference and the port: identical but for one line, so the ratio clears 0.6.
printf 'export const parse = () => 1\nexport const other = () => 2\nexport const third = () => 3\n' > "$WT/src/ref.ts"
side 002 "printf 'export const parse = () => 1\nexport const other = () => 2\nexport const third = () => 9\n' > \"$WT/src/a.ts\""
reply 002 '{"status":"complete","summary":"ported it"}'
reply 003 '{"verdict":"PASS","findings":[],"views":[{"name":"home","observation":"the page renders"}],"summary":"criteria met"}'
reply 004 '{"patterns":[],"log":"T1 rendered","invariants":[]}'
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "a task with a render gate and a fidelity pair reaches done"
isfile "$WT/$LD/artifacts/T1/home.png"; assert_true $? \
  "the screenshot is archived under artifacts/<T>/"
assert_eq "home" "$(ev artifact .name | head -1)" "an artifact event names the screenshot"
assert_eq "T1" "$(ev artifact .task | head -1)" "and the task it belongs to"
head -c 8 "$WT/$LD/artifacts/T1/home.png" | od -An -c | grep -q 'P   N   G'
assert_true $? "the archived file is the PNG the render command wrote"
isfile "$WT/$LD/runtime/fidelity-T1.json"; assert_true $? "the fidelity record is written"
assert_eq "true" "$(jq -r '.checks[0].ok' "$WT/$LD/runtime/fidelity-T1.json")" \
  "an honest port passes its threshold"
isfile "$WT/$LD/runtime/gate-render-T1-1.txt"; assert_true $? \
  "the render command's output is captured under its own render- tag"
grep -q '^- \[x\] T1:' "$WT/$LD/LOOP_PLAN.md"; assert_true $? "the row is marked [x]"
# The counterpart to the skip assertion in the failing scenario below: when the
# visual checks pass, the Evaluator does run, and it is handed the screenshots.
assert_eq "1" "$(jq -rs '[.[] | select(.type=="role_start" and .role=="Evaluator")] | length' "$WT/$LD/events.jsonl")" \
  "a green render runs the Evaluator"
# artifacts/ is gitignored, so the screenshot is not a stray the SANDBOX reverts
# and not a file the work commit swallows.
git -C "$WT" status --porcelain > "$STUB/porcelain.txt"
grep -q 'artifacts/' "$STUB/porcelain.txt"; assert_false $? \
  "the archived screenshot is not left as an untracked stray"

# ---------------- a six-line "copy" fails the fidelity check and reaches the Judge
setup_case
side 001 "cat > \"\$RUNTIME_DIR/sprint-T1.json\" <<'JSON'
{\"task\":\"T1\",\"success_criteria\":[\"src/a.ts exports parse\"],
 \"allow_list\":[\"src/a.ts\"],\"forbidden\":[],\"verification\":[\"true\"],
 \"fidelity_source\":[{\"from\":\"src/ref.ts\",\"to\":\"src/a.ts\",\"min_similarity\":0.6}],
 \"evaluator_must_read\":[],\"evaluator_must_view\":[],
 \"estimated_diff_lines\":5,\"scout_notes\":\"inline\",\"relevant_learnings\":[]}
JSON"
reply 001 '{"contract_path":"sprint-T1.json","notes":"ok"}'
printf 'export const parse = () => 1\nexport const other = () => 2\nexport const third = () => 3\n' > "$WT/src/ref.ts"
# w5 §5, verbatim in shape: the "copy" is a note saying it was copied.
side 002 "printf '// TODO(app-regression): copied from src/ref.ts\n' > \"$WT/src/a.ts\""
reply 002 '{"status":"complete","summary":"copied it"}'
# 003 is the Judge: the Evaluator is SKIPPED on a visual failure, exactly as it
# is on a failed verification command.
reply 003 '{"decision":"defer","classification":"capability","rationale":"the target does not resemble the reference","instruction":"","alternatives":["retry with the real file"],"reversal":"clear the [!] on T1","changes":{}}'
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 )
assert_eq "1" "$?" "a failed fidelity check does not reach done"
assert_eq "false" "$(jq -r '.checks[0].ok' "$WT/$LD/runtime/fidelity-T1.json")" \
  "the six-line comment fails its threshold"
assert_eq "defer" "$(ev decision .decision | tail -1)" "the failure reached the Judge"
assert_eq "fidelity" "$(jq -r '.attempts[0].outcome' "$WT/$LD/runtime/task-T1.json" | cut -d: -f1)" \
  "the attempt records its own failure kind, not gate"
assert_eq "0" "$(jq -rs '[.[] | select(.type=="role_start" and .role=="Evaluator")] | length' "$WT/$LD/events.jsonl")" \
  "the Evaluator is skipped on a visual failure"
grep -q '^- \[!\] T1:' "$WT/$LD/LOOP_PLAN.md"; assert_true $? "the row is marked blocked"

assert_summary
