#!/bin/bash
# ccstatusline custom-command widget.
#
# ccstatusline passes the full Claude Code stdin JSON (plus terminal_width) to
# custom commands, so this does two jobs:
#   1. Prints the sandbox label ("sandbox" / "sandbox:auto" / nothing), matching
#      the old statusline.sh behaviour.
#   2. Writes ~/.claude/.context-window.json as the fast-path cache consumed by
#      the guardrails plugin's context-warn.sh hook (which has a transcript
#      fallback if this is stale/absent).
#
# Output on stdout is the widget text; the cache write is a side effect.

stdin_data=$(cat)

# --- Context % + fast-path cache for context-warn.sh (#30202) ---
ctx_used=$(echo "$stdin_data" | jq -r '
    try (
        if (.context_window.remaining_percentage // null) != null then
            100 - (.context_window.remaining_percentage | floor)
        elif (.context_window.context_window_size // 0) > 0 then
            (((.context_window.current_usage.input_tokens // 0) +
              (.context_window.current_usage.cache_creation_input_tokens // 0) +
              (.context_window.current_usage.cache_read_input_tokens // 0)) * 100 /
             .context_window.context_window_size) | floor
        else "" end
    ) catch ""' 2>/dev/null)

if [ -n "$ctx_used" ] && [ "$ctx_used" != "null" ]; then
    session_id=$(echo "$stdin_data" | jq -r '.session_id // ""' 2>/dev/null)
    if [ -n "$session_id" ]; then
        printf '{"pct":%s,"ts":%s,"session":"%s"}\n' \
            "$ctx_used" "$(date +%s)" "$session_id" \
            > "$HOME/.claude/.context-window.json" 2>/dev/null || true
    fi
fi

# --- Sandbox label ---
current_dir=$(echo "$stdin_data" | jq -r '.workspace.current_dir // .cwd // ""' 2>/dev/null)

git_root=""
if [ -n "$current_dir" ] && cd "$current_dir" 2>/dev/null; then
    git_root=$(git -c core.useBuiltinFSMonitor=false rev-parse --show-toplevel 2>/dev/null)
fi

if [ -n "$git_root" ]; then
    project_settings="$git_root/.claude/settings.local.json"
else
    project_settings="$current_dir/.claude/settings.local.json"
fi
user_settings="$HOME/.claude/settings.json"

sandbox_enabled=""
sandbox_auto_allow=""

if [ -f "$project_settings" ]; then
    sandbox_enabled=$(jq -r '.sandbox.enabled // empty' "$project_settings" 2>/dev/null)
    sandbox_auto_allow=$(jq -r '.sandbox.autoAllowBashIfSandboxed // empty' "$project_settings" 2>/dev/null)
fi

if [ -z "$sandbox_enabled" ] && [ -f "$user_settings" ]; then
    sandbox_enabled=$(jq -r '.sandbox.enabled // empty' "$user_settings" 2>/dev/null)
    sandbox_auto_allow=$(jq -r '.sandbox.autoAllowBashIfSandboxed // empty' "$user_settings" 2>/dev/null)
fi

if [ "$sandbox_enabled" = "true" ]; then
    if [ "$sandbox_auto_allow" = "true" ]; then
        printf 'sandbox:auto'
    else
        printf 'sandbox'
    fi
fi
