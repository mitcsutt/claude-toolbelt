#!/usr/bin/env bash
# agent-loop event substrate. Pure helpers; the only side effect is appending a JSONL line.
# The harness "narrates" what it parses into $LOOP_DIR/events.jsonl; the dashboard tails it.

# emit_event <events_file> <type> [key value]...
#   Appends one compact JSON object: {t:<now>, type:<type>, <key>:<value>...}.
#   Value typing: an integer-looking value -> JSON number; a value starting with '{' or '['
#   -> raw JSON (e.g. by_model); anything else -> JSON string. An empty <events_file> is a
#   silent no-op so headless runs that don't want events never error.
emit_event() {
  local f="$1" type="$2"; shift 2 || true
  [[ -z "$f" ]] && return 0
  local jqargs=(--argjson t "$(date +%s)" --arg type "$type")
  local filter='{t:$t,type:$type'
  local k v
  while [[ $# -ge 2 ]]; do
    k="$1"; v="$2"; shift 2
    if [[ "$v" =~ ^-?[0-9]+$ || "$v" == \{* || "$v" == \[* ]]; then
      jqargs+=(--argjson "v_$k" "$v")
    else
      jqargs+=(--arg "v_$k" "$v")
    fi
    filter+=",$k:\$v_$k"
  done
  filter+='}'
  jq -cn "${jqargs[@]}" "$filter" >> "$f" 2>/dev/null || true
}

# tick_seq_next <seqfile> -> increment the persisted counter, print the new value.
#   Missing/empty/garbage file starts the sequence at 1. The seqfile lives under
#   $LOOP_DIR/runtime/ which persists on disk across run.sh invocations (it is gitignored,
#   not deleted), so tick numbers stay CONTINUOUS across resume — they never reset to 1.
tick_seq_next() {
  local f="$1" n
  n="$(cat "$f" 2>/dev/null)"; [[ "$n" =~ ^[0-9]+$ ]] || n=0
  n=$(( n + 1 ))
  printf '%s' "$n" > "$f" 2>/dev/null || true
  printf '%s' "$n"
}
