#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
# shellcheck source=../../../tests/lib/assert.sh
source "$ROOT/tests/lib/assert.sh"

HOOKS_JSON="$ROOT/plugins/cutthroat/hooks/hooks.json"

# Structural contract only. Whether the gate actually changes the model's
# final message is graded by evals/final-message-restates-open-asks, which
# costs money per run and is never part of this suite.

python3 -c "import json;json.load(open('$HOOKS_JSON'))" 2>/dev/null
assert_true $? "hooks.json is valid JSON"

field() { # json-path-expr -> value, empty on any failure
  python3 -c "
import json,sys
try:
    d=json.load(open(sys.argv[1]))
    sys.stdout.write(str($1))
except Exception:
    pass
" "$HOOKS_JSON" 2>/dev/null
}

STOP="d['hooks']['Stop'][0]['hooks'][0]"

assert_eq "prompt" "$(field "${STOP}['type']")" "Stop hook is a prompt hook"
assert_eq "30" "$(field "${STOP}['timeout']")" "Stop hook declares a 30s timeout"

prompt=$(field "${STOP}['prompt']")
plen=${#prompt}
assert_true "$([[ "$plen" -gt 500 ]] && echo 0 || echo 1)" "Stop prompt is a non-trivial ruleset"

printf '%s' "$prompt" | grep -q '\$ARGUMENTS'
assert_true $? "prompt interpolates the hook input via \$ARGUMENTS"

# Without this the hook could block up to Claude Code's 8-continuation cap.
printf '%s' "$prompt" | grep -q 'stop_hook_active is true'
assert_true $? "prompt short-circuits on stop_hook_active (one retry max)"

printf '%s' "$prompt" | grep -q 'last_assistant_message'
assert_true $? "prompt reads last_assistant_message, not the transcript"

printf '%s' "$prompt" | grep -q 'Waiting on you'
assert_true $? "block reason names the required 'Waiting on you' section"

for phrase in "waiting on, blocked on" "bare labels" "as discussed"; do
  printf '%s' "$prompt" | grep -q "$phrase"
  assert_true $? "prompt covers the failure mode: $phrase"
done

# The carve-out: the agent's own in-flight work is a status update, not a user ask.
printf '%s' "$prompt" | grep -q 'in-flight or background work'
assert_true $? "prompt exempts the agent's own in-flight/background work from rule 3(a)"

# Hardened JSON contract against fenced/prose replies that fail validation.
printf '%s' "$prompt" | grep -q 'no code fences'
assert_true $? "prompt forbids code fences in the JSON response"

# The subagent brief must survive the consolidation.
assert_eq "1" "$(field "int(bool(d['hooks'].get('SubagentStart')))")" \
  "SubagentStart brief still registered alongside the Stop gate"

assert_summary
