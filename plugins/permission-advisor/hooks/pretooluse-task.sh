#!/bin/bash
# PreToolUse hook on Task tool — advisory reminder to run /permission-advisor.
#
# Contract:
#   - Receives JSON on stdin: { tool_name, tool_input, ... }
#   - Writes advisory line to stderr (surfaced to model as system message)
#   - ALWAYS exits 0 — non-blocking by design (the skill is advisory)
#
# The hook itself does NOT run the permission check. It surfaces a reminder so
# the model considers invoking /permission-advisor before the dispatch. The
# real check happens inside the skill, where the model has the tools to read
# settings.json and emit a structured report.
set -euo pipefail

input=$(cat 2>/dev/null || true)
[ -z "$input" ] && exit 0

# Extract subagent prompt preview (first 80 chars) for the reminder.
preview=$(echo "$input" | jq -r '.tool_input.prompt // .tool_input.description // empty' 2>/dev/null | head -c 80 | tr '\n' ' ')

# Detect a few high-signal bash command hints from the prompt that suggest the
# subagent will need broader permissions than the default allow-list.
hint=""
if echo "$preview" | grep -qiE '\b(rm|rmdir|delete|clean|chmod|chown|sudo)\b'; then
  hint=" (preview mentions destructive ops — broader allow rules may be required)"
fi

# Emit the reminder. Stderr surfaces to the model on PreToolUse.
echo "[permission-advisor] About to dispatch subagent${hint}. Consider invoking /permission-advisor first to surface any allow-list gaps. Advisory only — this hook does not block." >&2

# Always exit 0 — advisory, never blocks.
exit 0
