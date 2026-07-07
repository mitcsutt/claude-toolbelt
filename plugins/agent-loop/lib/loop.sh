#!/usr/bin/env bash
# agent-loop v2 harness library. Pure functions; side effects only via explicit file args.

# Event emission helpers (emit_event). Path resolves relative to this lib dir.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/events.sh"

# parse_sentinel <text> -> prints "DONE"|"CONTINUE"|"HALT"|"NONE"; for HALT, reason on line 2.
parse_sentinel() {
  local text="$1"
  if [[ "$text" == *"<<LOOP_DONE>>"* ]]; then
    echo "DONE"
  elif [[ "$text" =~ \<\<LOOP_HALT:([^>]*)\>\> ]]; then
    echo "HALT"
    echo "${BASH_REMATCH[1]}"
  elif [[ "$text" == *"<<LOOP_CONTINUE>>"* ]]; then
    echo "CONTINUE"
  else
    echo "NONE"
  fi
}

# detect_mode <result_text> -> "plan"|"review"|"execute"
# Reads the mode token the tick prints near the start of its output (tick-prompt §0/§3).
# First match wins, case-insensitive; absence of a token means an execute tick.
detect_mode() {
  local text="$1"
  if printf '%s' "$text" | grep -qi 'PLAN TICK'; then
    echo plan
  elif printf '%s' "$text" | grep -qi 'REVIEW TICK'; then
    echo review
  else
    echo execute
  fi
}

# extract_result -> read a stream-json stream on stdin, print the LAST type:"result" line (empty if none)
extract_result() {
  local line out=""
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" ]] && continue
    if [[ "$(printf '%s' "$line" | jq -r '.type // empty' 2>/dev/null)" == "result" ]]; then
      out="$line"
    fi
  done
  printf '%s' "$out"
}

# format_stream <runlog> <ratelimit_file> <tick> : reads JSONL stream on stdin.
#   - tees raw stream to <runlog>
#   - writes the last rate_limit_event's rate_limit_info to <ratelimit_file>
#   - emits ONLY the final type:"result" line to stdout
#   - stderr feed depends on mode:
#       LOOP_VERBOSE=1        -> per-tool/per-text forensic feed (the original behaviour)
#       default + TTY (fd 2)  -> a single mutating heartbeat line (\r ... \033[K), cleared at end
#       default + non-TTY     -> silent (the full trace still lands in <runlog>)
format_stream() {
  local runlog="$1" rlfile="$2" tick="${3:-?}" events="${4:-}" line typ result=""
  local verbose=0 tty=0 tools=0 frame=0 activity="booting" start now
  local active_role="" etools=0
  [[ "${LOOP_VERBOSE:-0}" == "1" ]] && verbose=1
  [[ -t 2 ]] && tty=1
  start="$(date +%s)"
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" ]] && continue
    printf '%s\n' "$line" >> "$runlog"
    typ="$(printf '%s' "$line" | jq -r '.type // empty' 2>/dev/null)"
    case "$typ" in
      result)
        result="$line" ;;
      rate_limit_event)
        printf '%s' "$line" | jq -c '.rate_limit_info // empty' > "$rlfile" 2>/dev/null || true ;;
      assistant)
        if [[ -n "$events" ]]; then
          # One jq pass per assistant message: one row per tool_use with fields
          # (name, subagent_type, model, description, action). @tsv keeps the row
          # newline/tab-safe — data tabs/newlines are backslash-escaped, so the only
          # real tabs are the field separators — and we then translate those separator
          # tabs to US (0x1f) so `read` does NOT collapse empty columns. Tab is an
          # IFS-whitespace char, so `IFS=$'\t' read` silently drops empty fields; the
          # Agent tool carries no subagent_type and Bash carries neither subagent_type
          # nor model, so those empties used to shift every later value left — role was
          # read off the wrong column, every Bash became a fake "agent", and every
          # subagent showed its model name ("haiku"/"sonnet") as its role. US is
          # non-whitespace, so empty fields are preserved and columns stay aligned.
          local _tn _ts _tm _td _ta _role
          while IFS=$'\x1f' read -r _tn _ts _tm _td _ta; do
            [[ -z "$_tn$_ts" ]] && continue
            etools=$(( etools + 1 ))
            # Derive the canonical role label (ROLE EVENTS contract): a description that opens
            # with a role token wins; else the Explore recon maps to Scout; else fall back to
            # the raw subagent_type. role_start/role_end/handoff are keyed on this label, not
            # on the (usually "claude") subagent_type. Match the natural phrasings too
            # ("Plan…", "Evaluate…", "Re-evaluate…") — not just the strict canonical tokens —
            # so a dispatch that reads "Evaluate T2 diff" still lands in the Evaluator lane
            # instead of leaking through as the raw agent type ("claude").
            if   [[ "$_td" =~ ^[Pp]lan ]];              then _role="Planner"
            elif [[ "$_td" =~ ^[Ss]cout ]];             then _role="Scout"
            elif [[ "$_td" =~ ^[Ww]orker ]];            then _role="Worker"
            elif [[ "$_td" =~ ^([Rr]e-?)?[Ee]valuat ]]; then _role="Evaluator"
            elif [[ "$_ts" == "Explore" ]];             then _role="Scout"
            else _role="$_ts"
            fi
            if [[ -n "$_role" && "$_role" != "$active_role" ]]; then
              if [[ -n "$active_role" ]]; then
                emit_event "$events" role_end role "$active_role"
                emit_event "$events" handoff from "$active_role" to "$_role"
              fi
              active_role="$_role"
              # Carry the dispatch description (desc) so the dashboard's now-playing card can
              # show what the active agent is actually doing, not just its role + model.
              if [[ -n "$_tm" ]]; then
                emit_event "$events" role_start role "$_role" subagent "$_ts" model "$_tm" desc "$_td"
              else
                emit_event "$events" role_start role "$_role" subagent "$_ts" desc "$_td"
              fi
            fi
            # Tool events carry an action label (the tool's description, or the file it
            # touched) so the dashboard's activity feed can show what happened, tagged
            # with the role that did it.
            emit_event "$events" tool role "${active_role:-orchestrator}" name "$_tn" count "$etools" desc "$_ta"
          done < <(printf '%s' "$line" | jq -r '
            (.message.content // [])[] | select(.type=="tool_use")
            | [(.name//""),(.input.subagent_type//""),(.input.model//""),
               (.input.description//""),
               (.input.description // .input.file_path // .input.path // "")] | @tsv' 2>/dev/null | tr '\t' '\037')
        fi
        if (( verbose )); then
          printf '%s' "$line" | jq -r --arg t "$tick" '
            (.message.content // [])[]
            | if .type=="tool_use" then
                "  [" + $t + "] ⚙ " + (.name // "tool")
                + ( if   (.input.subagent_type // "") != "" then "(" + (.input.subagent_type|tostring) + ")"
                    elif (.input.file_path // "")     != "" then " " + (.input.file_path|tostring)
                    elif (.input.command // "")       != "" then " " + ((.input.command|tostring)[0:60])
                    else "" end )
              elif .type=="text" and ((.text // "")|length) > 0 then
                "  [" + $t + "] › " + ((.text|split("\n")[0])[0:100])
              else empty end
          ' >&2 2>/dev/null || true
        elif (( tty )); then
          # quiet + TTY: collapse the message to a count + last activity, redraw one line
          local meta tc act
          meta="$(printf '%s' "$line" | jq -r '
            (.message.content // []) as $c
            | ($c | map(select(.type=="tool_use")) | length) as $tc
            | ([ $c[]
                 | if .type=="tool_use" then (.input.subagent_type // .name // "tool")
                   elif .type=="text" and ((.text//"")|length)>0 then ((.text|split("\n")[0])[0:40])
                   else empty end ] | last // "") as $a
            | "\($tc)\t\($a)"
          ' 2>/dev/null || true)"
          tc="${meta%%$'\t'*}"; act="${meta#*$'\t'}"
          [[ "$tc" =~ ^[0-9]+$ ]] && tools=$(( tools + tc ))
          [[ -n "$act" ]] && activity="$act"
          frame=$(( frame + 1 ))
          now="$(date +%s)"
          printf '\r%s\033[K' "$(live_line "$frame" "$tick" "$activity" "$tools" "$(( now - start ))")" >&2
        fi
        ;;
    esac
  done
  # clear the heartbeat line so the permanent summary (printed by run.sh) starts clean
  (( verbose == 0 && tty == 1 )) && printf '\r\033[K' >&2
  printf '%s' "$result"
}

# append_usage <ledger> <tick> <mode> <claude_result_json> <duration_s>
# Records per-tick usage for the postmortem only. cost_usd is raw/informational —
# it is NEVER summed, projected, or used as a control input.
append_usage() {
  local ledger="$1" tick="$2" mode="$3" json="$4" dur="$5"
  local cost in out by_model
  cost="$(printf '%s' "$json" | jq -r '.total_cost_usd // 0')"
  in="$(printf '%s' "$json" | jq -r '.usage.input_tokens // 0')"
  out="$(printf '%s' "$json" | jq -r '.usage.output_tokens // 0')"
  # Per-model attribution. The result event carries `modelUsage`, keyed by model id,
  # with each model's own token + cost figures — the orchestrator and every dispatched
  # subagent (Planner/Scout/Worker/Evaluator) roll up here. Distil it to a compact
  # `by_model` map so the ledger records which tier actually spent the budget, with no
  # dependency on an external metrics backend. Empty `{}` when the field is absent.
  by_model="$(printf '%s' "$json" | jq -c '
    (.modelUsage // {}) | with_entries(.value |= {
      cost_usd: (.costUSD // 0),
      input_tokens: (.inputTokens // 0),
      output_tokens: (.outputTokens // 0),
      cache_read_tokens: (.cacheReadInputTokens // 0),
      cache_creation_tokens: (.cacheCreationInputTokens // 0)
    })' 2>/dev/null)"
  [[ -z "$by_model" || "$by_model" == "null" ]] && by_model='{}'
  jq -cn \
    --argjson tick "$tick" --arg mode "$mode" \
    --argjson cost "$cost" --argjson in "$in" --argjson out "$out" --argjson dur "$dur" \
    --argjson by_model "$by_model" \
    '{tick:$tick,mode:$mode,cost_usd:$cost,input_tokens:$in,output_tokens:$out,duration_s:$dur,by_model:$by_model}' \
    >> "$ledger"
}

# remaining_tasks <plan> -> count of pending "[ ]" task lines
remaining_tasks() {
  local plan="$1"
  [[ -f "$plan" ]] || { echo 0; return; }
  grep -cE '^[[:space:]]*- \[ \] ' "$plan" || true
}

# --- status rendering (pure string builders; the ANSI/IO wiring lives in run.sh) ---

# total_tasks <plan> -> count of all task lines, any "[.]" state
total_tasks() {
  local plan="$1"
  [[ -f "$plan" ]] || { echo 0; return; }
  grep -cE '^[[:space:]]*- \[.\] ' "$plan" || true
}

# done_tasks <plan> -> count of completed "[x]" task lines
done_tasks() {
  local plan="$1"
  [[ -f "$plan" ]] || { echo 0; return; }
  grep -cE '^[[:space:]]*- \[x\] ' "$plan" || true
}

# pct <done> <total> -> integer percent 0..100, rounded to nearest (0 when total is 0)
pct() {
  local done="$1" total="$2"
  (( total > 0 )) && echo $(( (done * 100 + total / 2) / total )) || echo 0
}

# fmt_dur <secs> -> "45s" | "3m12s" | "1h18m"
fmt_dur() {
  local s="${1:-0}"
  if (( s < 60 )); then
    printf '%ds' "$s"
  elif (( s < 3600 )); then
    printf '%dm%02ds' $(( s / 60 )) $(( s % 60 ))
  else
    printf '%dh%02dm' $(( s / 3600 )) $(( (s % 3600) / 60 ))
  fi
}

# plan_usage <rate_limit_info_json> <now_epoch> -> "5h 90% ↺2h12m" | "" (empty when no utilization)
#   Reads the usage-window utilization Claude reports on rate_limit_event lines. The stream only
#   carries .utilization near the warning threshold, so this is empty until the window is loaded
#   (graceful, like the statusline). resetsAt -> time-to-reset; rateLimitType -> the 5h/weekly label.
plan_usage() {
  local rl="$1" now="${2:-0}" util resets typ pct label left
  [[ -z "$rl" || "$rl" == "null" ]] && return
  printf '%s' "$rl" | jq -e '.utilization' >/dev/null 2>&1 || return
  util="$(printf '%s' "$rl" | jq -r '.utilization')"
  resets="$(printf '%s' "$rl" | jq -r '.resetsAt // empty')"
  typ="$(printf '%s' "$rl" | jq -r '.rateLimitType // ""')"
  pct="$(awk -v u="$util" 'BEGIN { printf "%d", u * 100 + 0.5 }')"
  case "$typ" in five_hour) label="5h" ;; weekly) label="wk" ;; *) label="quota" ;; esac
  if [[ "$resets" =~ ^[0-9]+$ ]] && (( resets > now )); then
    left=" ↺$(fmt_dur $(( resets - now )))"
  else
    left=""
  fi
  printf '%s %d%%%s' "$label" "$pct" "$left"
}

# usage_avg_dur <ledger> -> mean duration_s, integer (0 when empty)
usage_avg_dur() {
  local ledger="$1"
  [[ -f "$ledger" ]] || { echo 0; return; }
  jq -s 'if length == 0 then 0 else (map(.duration_s // 0) | add / length | floor) end' "$ledger" 2>/dev/null || echo 0
}

# eta_secs <remaining> <avg_dur> -> remaining * avg_dur
eta_secs() {
  echo $(( ${1:-0} * ${2:-0} ))
}

# progress_bar <done> <total> <width> -> "████████░░░░"
progress_bar() {
  local done="$1" total="$2" width="$3" filled i out=""
  (( total > 0 )) && filled=$(( done * width / total )) || filled=0
  (( filled > width )) && filled=$width
  for (( i = 0; i < width; i++ )); do
    (( i < filled )) && out+="█" || out+="░"
  done
  printf '%s' "$out"
}

# gates_compact <verification_line> -> "lint✓ tsc✓ build✓ test✓" (✗ for non-pass); empty in -> empty out
# input e.g.: "Loop-Verification: lint=pass tsc=pass build=pass test=fail"
gates_compact() {
  local line="$1" tok key val out=""
  line="${line#*Loop-Verification:}"
  for tok in $line; do
    [[ "$tok" == *=* ]] || continue
    key="${tok%%=*}"; val="${tok#*=}"
    [[ "$val" == "pass" ]] && out+="${key}✓ " || out+="${key}✗ "
  done
  printf '%s' "${out% }"
}

# _verdict_glyph <verdict> -> single glyph (done/continue ✓, plan ✦, skip ⏭, halt/fail ✗, retry ↻)
_verdict_glyph() {
  case "$1" in
    done|continue) printf '✓' ;;
    plan)          printf '✦' ;;
    review)        printf '◆' ;;
    skip)          printf '⏭' ;;
    retry)         printf '↻' ;;
    *)             printf '✗' ;;
  esac
}

# live_line <frame> <tick> <activity> <tools> <elapsed_s>
#   -> "⠹ t3 worker · 15 tools · 2m01s"   (mutating heartbeat content; run.sh wraps with \r)
live_line() {
  local frame="${1:-0}" tick="$2" activity="$3" tools="${4:-0}" elapsed="${5:-0}" sp
  # index a multibyte spinner glyph via a bash array of chars
  local -a frames=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧)
  sp="${frames[$(( frame % 8 ))]}"
  printf '%s t%s %s · %s tools · %s' "$sp" "$tick" "$activity" "$tools" "$(fmt_dur "$elapsed")"
}

# pause_line <frame> <reset_epoch> <now>
#   -> "⠹ ⏸ usage limit · resuming in 57m00s (10:20am)"  (mutating; run.sh wraps with \r)
#   Spinner reuses live_line's braille set; countdown via fmt_dur; reset shown in friendly
#   local time (the durable absolute UTC reset is already in run.sh's one-time log line).
pause_line() {
  local frame="${1:-0}" reset="${2:-0}" now="${3:-0}" sp when
  local -a frames=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧)
  sp="${frames[$(( frame % 8 ))]}"
  when="$(date -r "$reset" "+%l:%M%p" 2>/dev/null | tr '[:upper:]' '[:lower:]' | sed 's/^ *//')"
  printf '%s ⏸ usage limit · resuming in %s (%s)' "$sp" "$(fmt_dur $(( reset - now )))" "$when"
}

# pause_countdown <reset_epoch> <refresh_s> <status_file> <header> <tail_block>
#   Until now >= reset_epoch: rewrite <status_file> as
#       <header>\n\n<pause_line banner>\n\n<tail_block>
#   and, on a TTY (fd 2), redraw "\r<banner>\033[K" in place (same idiom as format_stream).
#   Sleeps <refresh_s> between redraws, clamped to the remaining time. Clears the TTY line on
#   exit and returns 0. Presentation only — caller decides whether to wait at all.
pause_countdown() {
  local reset="$1" refresh="${2:-30}" sf="$3" header="$4" tail="$5"
  local now frame=0 banner nap tty=0
  [[ -t 2 ]] && tty=1
  while now="$(date +%s)"; (( now < reset )); do
    banner="$(pause_line "$frame" "$reset" "$now")"
    { printf '%s\n\n%s\n\n%s\n' "$header" "$banner" "$tail"; } > "$sf" 2>/dev/null || true
    (( tty )) && printf '\r%s\033[K' "$banner" >&2
    frame=$(( frame + 1 ))
    nap=$(( reset - now )); (( nap > refresh )) && nap="$refresh"; (( nap < 1 )) && nap=1
    sleep "$nap"
  done
  (( tty )) && printf '\r\033[K' >&2
  return 0
}

# tick_line <verdict> <tick> <task> <dur_s> <gates> <sha> <done> <total>
#   -> "✓ t3 T26 · 3m12s · lint✓ tsc✓ build✓ test✓ · → a1b2c3   62% (26/42)"
#   no sha -> "(no commit)" in place of gates+sha. Percentage leads the progress chunk.
tick_line() {
  local verdict="$1" tick="$2" task="$3" dur="$4" gates="$5" sha="$6" done="$7" total="$8"
  local glyph mid p
  glyph="$(_verdict_glyph "$verdict")"
  p="$(pct "$done" "$total")"
  if [[ -n "$sha" ]]; then
    mid="${gates:+$gates · }→ ${sha}"
  else
    mid="(no commit)"
  fi
  printf '%s t%s %s · %s · %s   %d%% (%d/%d)' \
    "$glyph" "$tick" "${task:-?}" "$(fmt_dur "$dur")" "$mid" "$p" "$done" "$total"
}

# session_header <done> <total> <elapsed_s> <eta_s> <plan_str>
#   -> "── loop · 62% ████████░░░░ 26/42 · ⏱ 1h18m · ~45m left · 5h 90% ↺2h12m ──"
#   <plan_str> is the pre-rendered plan_usage segment; when empty the quota segment is omitted.
session_header() {
  local done="$1" total="$2" elapsed="$3" eta="$4" plan="$5"
  printf '── loop · %d%% %s %d/%d · ⏱ %s · ~%s left%s ──' \
    "$(pct "$done" "$total")" "$(progress_bar "$done" "$total" 12)" \
    "$done" "$total" "$(fmt_dur "$elapsed")" "$(fmt_dur "$eta")" "${plan:+ · $plan}"
}

# ratelimit_action <rate_limit_info_json> <now_epoch> <max_wait_s>
#   prints: ok | "wait <seconds>" | exit
#   Any "allowed*" status (incl. "allowed_warning", emitted near the threshold but still
#   serving requests) is ok; only genuinely throttled statuses (rejected/blocked) wait/exit.
ratelimit_action() {
  local rl="$1" now="$2" maxw="$3" status resets wait
  [[ -z "$rl" || "$rl" == "null" ]] && { echo "ok"; return; }
  printf '%s' "$rl" | jq -e . >/dev/null 2>&1 || { echo "ok"; return; }
  status="$(printf '%s' "$rl" | jq -r '.status // "allowed"' 2>/dev/null)"
  [[ "$status" == allowed* ]] && { echo "ok"; return; }
  resets="$(printf '%s' "$rl" | jq -r '.resetsAt // empty' 2>/dev/null)"
  if ! [[ "$resets" =~ ^[0-9]+$ ]]; then echo "exit"; return; fi
  wait=$(( resets - now + 60 ))
  (( wait < 5 )) && wait=5
  if (( wait > maxw )); then echo "exit"; return; fi
  echo "wait $wait"
}

# worktree_guard <expected_abs> <cwd_abs> -> 0 if equal, else 1
worktree_guard() {
  [[ "$1" == "$2" ]]
}

# plan_oversize_chars <plan> -> prints char count; exit 0 if over ~8k-token budget.
# The plan is re-primed into every subagent dispatch, so size is the dominant
# cache-read lever. Advisory only — the caller warns, never halts.
PLAN_CHAR_BUDGET="${PLAN_CHAR_BUDGET:-32000}"   # ~8k tokens at ~4 chars/token
plan_oversize_chars() {
  local plan="$1" n
  [[ -f "$plan" ]] || { echo 0; return 1; }
  n="$(wc -c < "$plan" | tr -d ' ')"
  echo "$n"
  [[ "$n" -gt "$PLAN_CHAR_BUDGET" ]]
}

# is_rate_limit <claude_json> -> 0 if a rate-limit/429, else 1
is_rate_limit() {
  local status
  status="$(printf '%s' "$1" | jq -r '(.api_error_status // empty)' 2>/dev/null)"
  [[ "$status" == "429" ]]
}

# backoff_delay <attempt> -> seconds: 2^(attempt+1), capped at 300
backoff_delay() {
  local attempt="$1" d
  d=$(( 2 ** (attempt + 1) ))
  (( d > 300 )) && d=300
  echo "$d"
}

# classify_result <rc> <claude_json> <sentinel> -> done|continue|halt|retry
classify_result() {
  local rc="$1" json="$2" sentinel="$3"
  case "$sentinel" in
    DONE) echo "done"; return ;;
    HALT) echo "halt"; return ;;
    CONTINUE)
      # a clean continue still requires a non-error invocation
      if [[ "$rc" -eq 0 ]] && [[ "$(printf '%s' "$json" | jq -r '.is_error // false')" != "true" ]]; then
        echo "continue"; return
      fi
      ;;
  esac
  # No usable sentinel, or error/timeout/crash -> retry (with backoff)
  echo "retry"
}

# tier_directives_block <config-path>
# Render an authoritative tier-override block the harness appends to the tick prompt, built
# from the per-role `<Role> tier:` lines in LOOP_CONFIG.md. The orchestrator is told to read
# the config (tick-prompt §1), but a cheaper spine model is less reliable at that step, so the
# explicit overrides can be silently missed. Injecting them directly makes them bind
# deterministically regardless of how diligently the spine reads files. Emits nothing when no
# role tier is set — every role then falls back to the §16 complexity heuristic.
tier_directives_block() {
  local cfg_path="$1" role val list=""
  for role in Planner Scout Worker Evaluator; do
    val="$(grep -E "^$role tier:" "$cfg_path" 2>/dev/null | head -1 | sed -E "s/^$role tier:[[:space:]]*//")"
    [[ -z "$val" ]] && continue
    if [[ "$role" == "Evaluator" ]]; then
      list+="- ${role}: ${val} (complex-class ceiling only; §10 governs mechanical/default)"$'\n'
    else
      list+="- ${role}: ${val}"$'\n'
    fi
  done
  [[ -z "$list" ]] && return 0
  printf '%s\n' \
    '' '---' '' \
    '## 16a. Resolved tier directives (injected by the harness — AUTHORITATIVE)' '' \
    'The harness has already read the per-role tier overrides from `LOOP_CONFIG.md` for you;' \
    'the list below is authoritative. When you dispatch a listed role via the Agent tool, use' \
    'the tier given here — resolve it to the cheapest capable model alias (§16) and pass that' \
    'as the `model` parameter. Do not downgrade or second-guess it. A role not listed has no' \
    'override; choose its tier with the §16 complexity heuristic. Reasoning-gap escalation' \
    '(§7, §11) still applies on top of these.' \
    ''
  printf '%s' "$list"
}
