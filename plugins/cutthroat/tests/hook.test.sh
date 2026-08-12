#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
# shellcheck source=./assert.sh
source "$HERE/assert.sh"

HOOK="$ROOT/plugins/cutthroat/hooks/subagent-brief.mjs"
HOOKS_JSON="$ROOT/plugins/cutthroat/hooks/hooks.json"

[[ -f "$HOOK" ]]
assert_true $? "hook file exists"

# 1. Valid stdin produces well-formed output.
out=$(echo '{"agentType":"general-purpose"}' | node "$HOOK" 2>/dev/null)
rc=$?
assert_eq "0" "$rc" "exits 0 on valid stdin"

ev=$(printf '%s' "$out" | python3 -c "import json,sys;print(json.load(sys.stdin)['hookSpecificOutput']['hookEventName'])" 2>/dev/null)
assert_eq "SubagentStart" "$ev" "emits hookEventName SubagentStart"

len=$(printf '%s' "$out" | python3 -c "import json,sys;print(len(json.load(sys.stdin)['hookSpecificOutput']['additionalContext']))" 2>/dev/null)
assert_true "$([[ "${len:-0}" -gt 100 ]] && echo 0 || echo 1)" "additionalContext is a non-trivial brief"

# 2. Fail-open: malformed stdin must not crash.
out2=$(echo 'not json at all' | node "$HOOK" 2>/dev/null); rc2=$?
assert_eq "0" "$rc2" "exits 0 on malformed stdin"
ev2=$(printf '%s' "$out2" | python3 -c "import json,sys;print(json.load(sys.stdin)['hookSpecificOutput']['hookEventName'])" 2>/dev/null)
assert_eq "SubagentStart" "$ev2" "still emits the brief on malformed stdin"

# 3. Fail-open: empty stdin.
out3=$(printf '' | node "$HOOK" 2>/dev/null); rc3=$?
assert_eq "0" "$rc3" "exits 0 on empty stdin"

# 4. Kill switch.
out4=$(echo '{}' | CUTTHROAT_SUBAGENT=off node "$HOOK" 2>/dev/null); rc4=$?
assert_eq "0" "$rc4" "exits 0 when disabled"
assert_eq "" "$out4" "emits nothing when CUTTHROAT_SUBAGENT=off"

# 5. hooks.json contract.
python3 -c "import json;json.load(open('$HOOKS_JSON'))" 2>/dev/null
assert_true $? "hooks.json is valid JSON"

cmd=$(python3 -c "
import json
d=json.load(open('$HOOKS_JSON'))
print(d['hooks']['SubagentStart'][0]['hooks'][0]['command'])
" 2>/dev/null)
echo "$cmd" | grep -q 'CLAUDE_PLUGIN_ROOT'
assert_true $? "hooks.json uses CLAUDE_PLUGIN_ROOT"

assert_summary
