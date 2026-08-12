#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
# shellcheck source=../../../tests/lib/assert.sh
source "$ROOT/tests/lib/assert.sh"
RUN="$HERE/../run.sh"

# Per-run base dir, relative to the worktree (run.sh runs with cwd=$WT and
# resolves LOOP_DIR relative to it). $WT/$LD is the absolute base dir for assertions.
LD=".claude/loop/test-run"

setup_case() {
  WT="$(mktemp -d)"
  export LOOP_DIR="$LD"
  mkdir -p "$WT/$LD/runtime"
  printf '%s\n' '- [ ] T1' '- [ ] T2' '- [ ] T3' > "$WT/$LD/LOOP_PLAN.md"
  cat > "$WT/$LD/LOOP_CONFIG.md" <<EOF
Worktree: $WT
Limits: tick_timeout=600
EOF
  echo 'dummy tick' > "$WT/tick.md"; export TICK_PROMPT="$WT/tick.md"
  export MOCK_CLAUDE_STATE="$WT/.mockstate"
  export PATH="$HERE/fixtures:$PATH"   # mock claude wins
  : > "$WT/.mockstate"
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

# No-progress guard: CONTINUE forever without dropping remaining count -> halt
setup_case
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
rc=$?
assert_true "$([[ $rc -ne 0 ]] && echo 0 || echo 1)" "no-progress halt exits non-zero"
grep -q "no progress" "$WT/$LD/run.log"; assert_true $? "run.log records no-progress halt"

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

assert_summary
