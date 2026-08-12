#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
# shellcheck source=../../../tests/lib/assert.sh
source "$ROOT/tests/lib/assert.sh"
source "$HERE/../lib/events.sh"

# --- emit_event: appends one typed JSON line; numbers and JSON blobs keep their type ---
TMP="$(mktemp -d)"; EV="$TMP/events.jsonl"
emit_event "$EV" role_start role Worker model opus
emit_event "$EV" tool role Worker name Edit count 3
emit_event "$EV" tick_end tick 7 verdict continue dur 611 by_model '{"opus":{"cost_usd":1.5}}'
assert_eq "3" "$(wc -l < "$EV" | tr -d ' ')" "three event lines appended"
assert_eq "role_start" "$(sed -n 1p "$EV" | jq -r '.type')" "type recorded"
assert_eq "Worker"     "$(sed -n 1p "$EV" | jq -r '.role')" "string field recorded"
assert_eq "number"     "$(sed -n 2p "$EV" | jq -r '.count|type')" "numeric field is a JSON number"
assert_eq "7"          "$(sed -n 3p "$EV" | jq -r '.tick')" "tick number recorded"
assert_eq "1.5"        "$(sed -n 3p "$EV" | jq -r '.by_model.opus.cost_usd')" "json blob kept as object"
assert_eq "number"     "$(sed -n 1p "$EV" | jq -r '.t|type')" "t timestamp is a number"
# empty events-file path is a no-op (headless-safe; never errors)
emit_event "" tick_start tick 1; assert_true $? "empty path is a silent no-op"

# --- tick_seq_next: persistent, monotonic, continuous across 'runs' ---
SEQ="$TMP/tickseq"
assert_eq "1" "$(tick_seq_next "$SEQ")" "first tick is 1"
assert_eq "2" "$(tick_seq_next "$SEQ")" "increments"
# simulate a fresh run.sh invocation: same file, must continue (not reset)
assert_eq "3" "$(tick_seq_next "$SEQ")" "continues across resume"
assert_eq "3" "$(cat "$SEQ")" "counter persisted to disk"
# garbage/missing file starts cleanly at 1
echo "garbage" > "$SEQ"; assert_eq "1" "$(tick_seq_next "$SEQ")" "garbage counter resets to 1"
rm -rf "$TMP"

assert_summary
