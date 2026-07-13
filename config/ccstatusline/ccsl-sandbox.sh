#!/bin/bash
# ccstatusline custom-command widget: prints the sandbox label
# ("sandbox" / "sandbox:auto" / nothing).
#
# Perf: this machine taxes every process launch (~0.2-0.3s of exec-scan
# latency), so the script minimises spawns — current_dir is read with a bash
# regex (not jq), and each settings file is read with a single jq (both fields
# at once), user-level only consulted when the project file didn't decide.
#
# ccstatusline passes the full Claude Code stdin JSON. Output is the widget text.

stdin_data=$(cat)

re_cur='"current_dir"[[:space:]]*:[[:space:]]*"([^"]*)"'
re_cwd='"cwd"[[:space:]]*:[[:space:]]*"([^"]*)"'
cur=""
[[ $stdin_data =~ $re_cur ]] && cur="${BASH_REMATCH[1]}"
[ -z "$cur" ] && [[ $stdin_data =~ $re_cwd ]] && cur="${BASH_REMATCH[1]}"

git_root=""
if [ -n "$cur" ] && cd "$cur" 2>/dev/null; then
    git_root=$(git -c core.useBuiltinFSMonitor=false rev-parse --show-toplevel 2>/dev/null)
fi

if [ -n "$git_root" ]; then
    proj="$git_root/.claude/settings.local.json"
else
    proj="$cur/.claude/settings.local.json"
fi
user="$HOME/.claude/settings.json"

enabled=""
auto=""
# One jq per file: emit both fields tab-separated ("" when absent).
read_sandbox() {
    local out
    out=$(jq -r '[(.sandbox.enabled // ""), (.sandbox.autoAllowBashIfSandboxed // "")] | @tsv' "$1" 2>/dev/null)
    enabled="${out%%$'\t'*}"
    auto="${out#*$'\t'}"
}

[ -f "$proj" ] && read_sandbox "$proj"
[ -z "$enabled" ] && [ -f "$user" ] && read_sandbox "$user"

if [ "$enabled" = "true" ]; then
    if [ "$auto" = "true" ]; then
        printf 'sandbox:auto'
    else
        printf 'sandbox'
    fi
fi
