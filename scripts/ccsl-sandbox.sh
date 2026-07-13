#!/bin/bash
# ccstatusline custom-command widget: prints the sandbox label
# ("sandbox" / "sandbox:auto" / nothing), matching the old statusline.sh.
#
# ccstatusline passes the full Claude Code stdin JSON (plus terminal_width).
# Output on stdout is the widget text.

stdin_data=$(cat)

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
