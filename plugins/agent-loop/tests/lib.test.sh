#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
# shellcheck source=../../../tests/lib/assert.sh
source "$ROOT/tests/lib/assert.sh"
source "$HERE/../lib/loop.sh"

# parse_sentinel echoes one of: DONE | CONTINUE | HALT | NONE
# and, for HALT, the reason on a second line.
assert_eq "DONE"     "$(parse_sentinel 'work done <<LOOP_DONE>> bye' | head -1)"     "done sentinel"
assert_eq "CONTINUE" "$(parse_sentinel 'more to do <<LOOP_CONTINUE>>' | head -1)"     "continue sentinel"
assert_eq "HALT"     "$(parse_sentinel 'stuck <<LOOP_HALT:T3 blocked>>' | head -1)"   "halt sentinel"
assert_eq "T3 blocked" "$(parse_sentinel 'stuck <<LOOP_HALT:T3 blocked>>' | sed -n 2p)" "halt reason"
assert_eq "NONE"     "$(parse_sentinel 'no marker here' | head -1)"                   "no sentinel"
# DONE wins if both present (completion is terminal)
assert_eq "DONE"     "$(parse_sentinel '<<LOOP_CONTINUE>> then <<LOOP_DONE>>' | head -1)" "done precedence"

# --- usage ledger (tokens + duration; raw cost recorded, never summed) ---
TMP="$(mktemp -d)"; LEDGER="$TMP/LOOP_USAGE.jsonl"
SAMPLE_JSON='{"result":"x <<LOOP_CONTINUE>>","total_cost_usd":0.05,"usage":{"input_tokens":1000,"output_tokens":200}}'
append_usage "$LEDGER" 1 execute "$SAMPLE_JSON" 12
assert_eq "1" "$(wc -l < "$LEDGER" | tr -d ' ')" "ledger has 1 line"
assert_eq "0.05" "$(tail -1 "$LEDGER" | jq -r '.cost_usd')" "raw cost recorded"
assert_eq "1000" "$(tail -1 "$LEDGER" | jq -r '.input_tokens')" "input tokens recorded"
assert_eq "200" "$(tail -1 "$LEDGER" | jq -r '.output_tokens')" "output tokens recorded"
assert_eq "12" "$(tail -1 "$LEDGER" | jq -r '.duration_s')" "duration recorded"
assert_eq "null" "$(tail -1 "$LEDGER" | jq -r '.cumulative_cost_usd')" "no cumulative field"
rm -rf "$TMP"

# --- remaining_tasks (feeds the no-progress guard) ---
TMP="$(mktemp -d)"; PLAN="$TMP/LOOP_PLAN.md"
printf '%s\n' '- [x] T1' '- [ ] T2' '- [ ] T3' '- [~] T4' '- [-] T5' > "$PLAN"
assert_eq "2" "$(remaining_tasks "$PLAN")" "counts only [ ] pending"
# total_tasks counts every state; done_tasks counts only [x]
assert_eq "5" "$(total_tasks "$PLAN")" "total_tasks counts all task lines"
assert_eq "1" "$(done_tasks "$PLAN")"  "done_tasks counts only [x]"
rm -rf "$TMP"
assert_eq "0" "$(total_tasks /no/such/plan)" "total_tasks missing plan -> 0"

# --- status render: pure string builders (percentage-first) ---
assert_eq "0"  "$(pct 0 0)"   "pct guards divide-by-zero"
assert_eq "62" "$(pct 26 42)" "pct floors"
assert_eq "100" "$(pct 42 42)" "pct full"

assert_eq "45s"    "$(fmt_dur 45)"    "fmt_dur seconds"
assert_eq "3m12s"  "$(fmt_dur 192)"   "fmt_dur minutes zero-pads seconds"
assert_eq "1h12m"  "$(fmt_dur 4332)"  "fmt_dur hours zero-pads minutes"

assert_eq "100" "$(eta_secs 5 20)" "eta_secs = remaining * avg"

# plan_usage: 5-hour window utilization from a rate_limit_event's rate_limit_info
assert_eq "5h 90% ↺2h12m" "$(plan_usage '{"utilization":0.9,"resetsAt":17920,"rateLimitType":"five_hour"}' 10000)" "plan_usage 5h with reset countdown"
assert_eq "5h 83%"        "$(plan_usage '{"utilization":0.83,"resetsAt":1,"rateLimitType":"five_hour"}' 10000)"  "plan_usage drops ↺ when reset passed"
assert_eq "wk 50%"        "$(plan_usage '{"utilization":0.5,"rateLimitType":"weekly"}' 10000)"                   "plan_usage weekly label, no reset"
assert_eq ""              "$(plan_usage '{"status":"allowed","resetsAt":17920}' 10000)"                          "plan_usage empty when no utilization"
assert_eq ""              "$(plan_usage '' 10000)"                                                              "plan_usage empty input -> empty"

# progress_bar: 12-wide, 26/42 -> 7 filled (26*12/42=7)
assert_eq "███████░░░░░" "$(progress_bar 26 42 12)" "progress_bar fills floor(done/total*width)"
assert_eq "░░░░"         "$(progress_bar 0 0 4)"    "progress_bar empty when total 0"
assert_eq "████"         "$(progress_bar 9 4 4)"    "progress_bar clamps to width"

assert_eq "lint✓ tsc✓ build✓ test✓" "$(gates_compact 'Loop-Verification: lint=pass tsc=pass build=pass test=pass')" "gates all pass"
assert_eq "lint✓ test✗"             "$(gates_compact 'Loop-Verification: lint=pass test=fail')"                     "gates mark non-pass"
assert_eq ""                        "$(gates_compact '')"                                                          "gates empty in -> empty out"

# usage_avg_dur over a ledger (feeds the ETA)
TMP="$(mktemp -d)"; L="$TMP/LOOP_USAGE.jsonl"
printf '%s\n' \
  '{"tick":1,"input_tokens":1000,"output_tokens":200,"duration_s":10}' \
  '{"tick":2,"input_tokens":2000,"output_tokens":300,"duration_s":20}' > "$L"
assert_eq "15" "$(usage_avg_dur "$L")"  "usage_avg_dur means duration_s"
assert_eq "0"  "$(usage_avg_dur /no/such)" "usage_avg_dur missing ledger -> 0"
rm -rf "$TMP"

# live_line: spinner frame, tick, activity, tools, elapsed
assert_eq "⠹ t3 worker · 15 tools · 2m01s" "$(live_line 2 3 worker 15 121)" "live_line heartbeat content"

# tick_line: with a commit -> gates + sha + percentage(count); without -> (no commit)
assert_eq "✓ t3 T26 · 3m12s · lint✓ test✓ · → a1b2c3   62% (26/42)" \
  "$(tick_line continue 3 T26 192 'lint✓ test✓' a1b2c3 26 42)" "tick_line with commit, percentage-first"
assert_eq "✗ t4 T27 · 45s · (no commit)   62% (26/42)" \
  "$(tick_line halt 4 T27 45 '' '' 26 42)" "tick_line no-commit path, halt glyph"
assert_eq "✦ t1 ? · 30s · (no commit)   0% (0/8)" \
  "$(tick_line plan 1 '' 30 '' '' 0 8)" "tick_line plan glyph + empty task"
assert_eq "◆ t5 ? · 30s · → a1b2c3   38% (3/8)" \
  "$(tick_line review 5 '' 30 '' a1b2c3 3 8)" "tick_line review glyph"

# session_header: percentage leads; plan segment appended when non-empty, omitted when empty
assert_eq "── loop · 62% ███████░░░░░ 26/42 · ⏱ 1h12m · ~45m00s left · 5h 90% ↺2h12m ──" \
  "$(session_header 26 42 4332 2700 '5h 90% ↺2h12m')" "session_header with plan segment"
assert_eq "── loop · 62% ███████░░░░░ 26/42 · ⏱ 1h12m · ~45m00s left ──" \
  "$(session_header 26 42 4332 2700 '')" "session_header omits empty plan segment"

# --- worktree guard ---
worktree_guard "/a/b/wt" "/a/b/wt"; assert_true  $? "cwd matches worktree"
worktree_guard "/a/b/wt" "/a/b/other"; assert_false $? "cwd differs from worktree"

# --- rate limit detection ---
is_rate_limit '{"is_error":true,"api_error_status":429}'; assert_true $? "429 is rate limit"
is_rate_limit '{"is_error":true,"api_error_status":500}'; assert_false $? "500 not rate limit"
is_rate_limit '{"is_error":false}'; assert_false $? "success not rate limit"

# --- backoff (exponential, capped at 300) ---
assert_eq "2"   "$(backoff_delay 0)" "first backoff"
assert_eq "4"   "$(backoff_delay 1)" "second backoff"
assert_eq "300" "$(backoff_delay 20)" "backoff capped"

# --- classify_result <rc> <json> <sentinel> -> done|continue|halt|retry ---
assert_eq "done"     "$(classify_result 0 '{"is_error":false}' DONE)"     "done"
assert_eq "continue" "$(classify_result 0 '{"is_error":false}' CONTINUE)" "continue"
assert_eq "halt"     "$(classify_result 0 '{"is_error":false}' HALT)"     "explicit halt"
assert_eq "retry"    "$(classify_result 124 '{}' NONE)"                    "timeout -> retry"
assert_eq "retry"    "$(classify_result 0 '{"is_error":true,"api_error_status":429}' NONE)" "rate limit -> retry"
assert_eq "retry"    "$(classify_result 1 '{"is_error":false}' NONE)"      "crash/garbage -> retry"

# --- extract_result: isolates the final type:"result" line from a stream ---
STREAM='{"type":"system","subtype":"init"}
{"type":"assistant","message":{"content":[{"type":"text","text":"hi"}]}}
{"type":"rate_limit_event","rate_limit_info":{"status":"allowed","resetsAt":1780405800}}
{"type":"result","subtype":"success","is_error":false,"result":"ok <<LOOP_CONTINUE>>","usage":{"input_tokens":3,"output_tokens":4},"total_cost_usd":0.01}'
RES="$(printf '%s\n' "$STREAM" | extract_result)"
assert_eq "result" "$(printf '%s' "$RES" | jq -r '.type')" "extract_result returns the result line"
assert_eq "ok <<LOOP_CONTINUE>>" "$(printf '%s' "$RES" | jq -r '.result')" "extract_result preserves result text"
assert_eq "" "$(printf '%s\n' '{"type":"system"}' | extract_result)" "extract_result empty when no result line"
# extract_result must handle a final line with NO trailing newline (the result line often is last)
assert_eq "result" "$(printf '%s' '{"type":"result","result":"x"}' | extract_result | jq -r '.type')" "extract_result handles missing trailing newline"

# --- detect_mode <result_text> -> plan|review|execute ---
assert_eq "plan"    "$(detect_mode 'blah PLAN TICK blah')"     "detect_mode finds PLAN TICK"
assert_eq "review"  "$(detect_mode 'REVIEW TICK here')"        "detect_mode finds REVIEW TICK"
assert_eq "execute" "$(detect_mode 'EXECUTE TICK doing work')" "detect_mode defaults to execute"
assert_eq "execute" "$(detect_mode 'no marker at all')"        "detect_mode no marker -> execute"
assert_eq "plan"    "$(detect_mode 'plan tick lowercase')"     "detect_mode is case-insensitive"

# --- format_stream: result passthrough + rate-limit capture + raw log ---
# (passthrough is mode-independent; verbose feed surfaces the forensic trace)
TMP="$(mktemp -d)"; RLOG="$TMP/run.log"; RLF="$TMP/ratelimit.json"
RES="$(LOOP_VERBOSE=1 format_stream "$RLOG" "$RLF" 1 < "$HERE/fixtures/stream-sample.jsonl" 2>"$TMP/feed.txt")"
assert_eq "result" "$(printf '%s' "$RES" | jq -r '.type')" "format_stream emits the result line on stdout"
assert_eq "allowed" "$(jq -r '.status' "$RLF")" "format_stream captures rate_limit_info"
assert_eq "1780405800" "$(jq -r '.resetsAt' "$RLF")" "format_stream captures resetsAt"
assert_eq "9" "$(wc -l < "$RLOG" | tr -d ' ')" "format_stream tees all raw lines to run.log"
grep -q "Worker" "$TMP/feed.txt"; assert_true $? "verbose feed surfaces Agent(Worker) dispatch"
grep -q "billing.ts" "$TMP/feed.txt"; assert_true $? "verbose feed surfaces edited file"
grep -q "Done." "$TMP/feed.txt"; assert_true $? "verbose feed surfaces assistant text first line"
grep -q "thinking_tokens" "$TMP/feed.txt" && feed_noise=1 || feed_noise=0
assert_eq "0" "$feed_noise" "verbose feed filters system noise"
rm -rf "$TMP"

# default mode + non-TTY (stderr redirected): feed is silent, passthrough unchanged
TMP="$(mktemp -d)"; RLOG="$TMP/run.log"; RLF="$TMP/ratelimit.json"
RES="$(format_stream "$RLOG" "$RLF" 1 < "$HERE/fixtures/stream-sample.jsonl" 2>"$TMP/feed.txt")"
assert_eq "result" "$(printf '%s' "$RES" | jq -r '.type')" "quiet mode still emits the result line"
assert_eq "9" "$(wc -l < "$RLOG" | tr -d ' ')" "quiet mode still tees raw lines to run.log"
assert_eq "0" "$(wc -c < "$TMP/feed.txt" | tr -d ' ')" "quiet non-TTY feed is silent"
rm -rf "$TMP"
# format_stream must handle a final result line with NO trailing newline
TMP="$(mktemp -d)"
RES="$(printf '%s' '{"type":"system","subtype":"init"}
{"type":"result","subtype":"success","result":"z"}' | format_stream "$TMP/r.log" "$TMP/rl.json" 1 2>/dev/null)"
assert_eq "z" "$(printf '%s' "$RES" | jq -r '.result')" "format_stream handles missing trailing newline"
rm -rf "$TMP"

# --- format_stream event emission (4th arg = events file) ---
# Reuses the existing fixture: an Agent(Worker) dispatch, then Bash + Edit tools.
TMP="$(mktemp -d)"; RLOG="$TMP/run.log"; RLF="$TMP/rl.json"; EV="$TMP/events.jsonl"
RES="$(format_stream "$RLOG" "$RLF" 7 "$EV" < "$HERE/fixtures/stream-sample.jsonl" 2>/dev/null)"
assert_eq "result" "$(printf '%s' "$RES" | jq -r '.type')" "passthrough still emits result line"
# a Worker role_start is emitted on the Agent(subagent_type=Worker) dispatch
assert_eq "1" "$(jq -rc 'select(.type=="role_start" and .role=="Worker")' "$EV" | wc -l | tr -d ' ')" "role_start Worker emitted once"
# every tool_use becomes a tool event; the Edit lands with cumulative count 3 (Agent,Bash,Edit)
assert_eq "Edit" "$(jq -r 'select(.type=="tool" and .count==3) | .name' "$EV")" "third tool is Edit with cumulative count"
# tools after the dispatch are attributed to the active Worker role
assert_eq "Worker" "$(jq -r 'select(.type=="tool" and .name=="Edit") | .role' "$EV")" "tool attributed to active role"
# omitting the 4th arg keeps the old behaviour: no events file written
RES2="$(format_stream "$TMP/r2.log" "$TMP/rl2.json" 7 < "$HERE/fixtures/stream-sample.jsonl" 2>/dev/null)"
assert_eq "result" "$(printf '%s' "$RES2" | jq -r '.type')" "3-arg call still works (no events)"
rm -rf "$TMP"

# --- ratelimit_action <rate_limit_info_json> <now_epoch> <max_wait_s> -> ok | "wait N" | exit ---
assert_eq "ok"   "$(ratelimit_action '{"status":"allowed","resetsAt":2000}' 1000 21600)" "allowed -> ok"
assert_eq "ok"   "$(ratelimit_action '{"status":"allowed_warning","resetsAt":2000,"utilization":0.9}' 1000 21600)" "allowed_warning (near limit but still allowed) -> ok"
assert_eq "ok"   "$(ratelimit_action '' 1000 21600)"                                     "empty info -> ok"
assert_eq "wait 560" "$(ratelimit_action '{"status":"rejected","resetsAt":1500}' 1000 21600)" "blocked within cap -> wait"
assert_eq "exit" "$(ratelimit_action '{"status":"rejected","resetsAt":100000}' 1000 21600)" "blocked beyond cap -> exit"
assert_eq "exit" "$(ratelimit_action '{"status":"rejected"}' 1000 21600)"                   "blocked no reset -> exit"
assert_eq "wait 5" "$(ratelimit_action '{"status":"blocked","resetsAt":900}' 1000 21600)"   "reset passed -> brief wait"
assert_eq "ok" "$(ratelimit_action 'not json' 1000 21600)" "malformed info -> ok (fail-open)"

# --- tier_directives_block <config-path>: injected authoritative tier block ---
TMP="$(mktemp -d)"; CFG="$TMP/LOOP_CONFIG.md"
printf '%s\n' 'Worktree: /x' 'Planner tier: most-capable' 'Scout tier: cheap' 'Worker tier: standard' 'Evaluator tier: most-capable' > "$CFG"
BLK="$(tier_directives_block "$CFG")"
assert_eq "0" "$([[ "$BLK" == *'## 16a. Resolved tier directives'* ]] && echo 0 || echo 1)" "block has the 16a header"
assert_eq "0" "$([[ "$BLK" == *'AUTHORITATIVE'* ]] && echo 0 || echo 1)" "block marked authoritative"
assert_eq "0" "$([[ "$BLK" == *'- Worker: standard'* ]] && echo 0 || echo 1)" "block lists Worker tier"
assert_eq "0" "$([[ "$BLK" == *'- Evaluator: most-capable (complex-class ceiling only'* ]] && echo 0 || echo 1)" "block lists Evaluator tier with ceiling qualifier"
# blank/absent tiers are omitted (those roles fall back to the §16 heuristic)
printf '%s\n' 'Worktree: /x' 'Planner tier: most-capable' 'Worker tier:' > "$CFG"
BLK="$(tier_directives_block "$CFG")"
assert_eq "0" "$([[ "$BLK" == *'- Planner: most-capable'* ]] && echo 0 || echo 1)" "set tier kept"
assert_eq "0" "$([[ "$BLK" != *'Worker'* ]] && echo 0 || echo 1)" "blank tier omitted"
# no tiers at all -> empty block (prompt left untouched)
printf '%s\n' 'Worktree: /x' 'Limits: tick_timeout=600' > "$CFG"
assert_eq "" "$(tier_directives_block "$CFG")" "no tiers -> empty block"
rm -rf "$TMP"

# --- pause_line: spinner + countdown + friendly reset time ---
# friendly local time is computed the same way the helper does, so the assertion
# is self-consistent regardless of the test machine's clock/TZ.
ftime() { date -r "$1" "+%l:%M%p" | tr '[:upper:]' '[:lower:]' | sed 's/^ *//'; }

assert_eq "⠋ ⏸ usage limit · resuming in 45s ($(ftime 1045))" \
  "$(pause_line 0 1045 1000)" "pause_line <60s band, frame 0 spinner"
assert_eq "⠙ ⏸ usage limit · resuming in 57m00s ($(ftime 4420))" \
  "$(pause_line 1 4420 1000)" "pause_line <1h band, frame 1 spinner"
assert_eq "⠋ ⏸ usage limit · resuming in 1h01m ($(ftime 4661))" \
  "$(pause_line 8 4661 1000)" "pause_line >=1h band, frame wraps 8->0"

# --- pause_countdown: rewrites STATUS_FILE with live banner, returns at reset ---
# 2s window, 1s refresh; fd 2 is not a TTY under the harness so it runs file-only (no \r spam).
pc_tmp="$(mktemp)"
pause_countdown "$(( $(date +%s) + 2 ))" 1 "$pc_tmp" "HEADERLINE" "TAILLINE"
assert_true $? "pause_countdown returns 0 at reset"
grep -q "HEADERLINE" "$pc_tmp"; assert_true $? "status file keeps the session header"
grep -q "resuming in" "$pc_tmp"; assert_true $? "status file shows a live countdown banner"
grep -q "TAILLINE" "$pc_tmp"; assert_true $? "status file preserves the recent-tick tail"
rm -f "$pc_tmp"

# --- Plan 2: tier_directives_block — Evaluator is a complex-class ceiling, not a flat floor ---
CFGE="$(mktemp)"; printf 'Planner tier: most-capable\nEvaluator tier: most-capable\n' > "$CFGE"
OUTE="$(tier_directives_block "$CFGE")"
printf '%s' "$OUTE" | grep -qE '^- Planner: most-capable$'; assert_true $? "planner stays a flat override"
printf '%s' "$OUTE" | grep -qiE '^- Evaluator: most-capable .*ceiling'; assert_true $? "evaluator line carries ceiling qualifier"
rm -f "$CFGE"
CFGB="$(mktemp)"; printf 'Scout tier: cheap\nEvaluator tier:\n' > "$CFGB"
OUTB="$(tier_directives_block "$CFGB")"
printf '%s' "$OUTB" | grep -qE '^- Evaluator:'; assert_false $? "blank Evaluator emits no line"
rm -f "$CFGB"

# --- Plan 3: plan_oversize_chars — advisory plan-size budget ---
posmall="$(mktemp)"; printf 'x%.0s' $(seq 1 100) > "$posmall"
pobig="$(mktemp)"; printf 'x%.0s' $(seq 1 40000) > "$pobig"
plan_oversize_chars "$posmall" >/dev/null; assert_false $? "small plan not flagged oversize"
plan_oversize_chars "$pobig" >/dev/null; assert_true $? "big plan flagged oversize"
assert_eq "40000" "$(plan_oversize_chars "$pobig")" "char count is exact"
rm -f "$posmall" "$pobig"

assert_summary
