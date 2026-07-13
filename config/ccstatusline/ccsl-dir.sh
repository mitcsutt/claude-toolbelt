#!/bin/bash
# ccstatusline custom-command widget: combined repo-root / worktree identity.
#
#   main repo -> "<repo-dir>"            (linked to the repo root)
#   worktree  -> "⎇ <worktree-name>"     (icon prefix, linked to the worktree)
#
# The name is an OSC 8 terminal hyperlink; control/cmd-click opens the folder.
# The link scheme is set by $CCSL_EDITOR (see below) and defaults to file://.
# Prints nothing outside a git repo, so the widget + its separators collapse.
#
# Perf: this machine taxes every process launch (~0.2-0.3s of exec-scan
# latency), so the script keeps to ONE external spawn — current_dir is read
# with a bash regex (not jq), the toplevel + git-dir come from a single
# git rev-parse, and basenames use ${var##*/} (not basename).
#
# Requires preserveColors:true — ccstatusline strips escape sequences (colour +
# OSC 8) from custom-command output otherwise, so this script emits its own
# colour (brightBlue in the colorLevel:2 palette).

stdin_data=$(cat)

re_cur='"current_dir"[[:space:]]*:[[:space:]]*"([^"]*)"'
re_cwd='"cwd"[[:space:]]*:[[:space:]]*"([^"]*)"'
cur=""
[[ $stdin_data =~ $re_cur ]] && cur="${BASH_REMATCH[1]}"
[ -z "$cur" ] && [[ $stdin_data =~ $re_cwd ]] && cur="${BASH_REMATCH[1]}"
[ -z "$cur" ] && exit 0
cd "$cur" 2>/dev/null || exit 0

# Single git call: line 1 = --show-toplevel, line 2 = --git-dir.
git_out=$(git -c core.useBuiltinFSMonitor=false rev-parse --show-toplevel --git-dir 2>/dev/null)
[ -z "$git_out" ] && exit 0
toplevel="${git_out%%$'\n'*}"
git_dir="${git_out#*$'\n'}"
[ -z "$toplevel" ] && exit 0

# Worktree git-dir lives under .../worktrees/<name>; else it's the main repo.
case "$git_dir" in
    *"/worktrees/"*)
        wt="${git_dir%/}"
        name="${wt##*/}"
        prefix="⎇ " ;;
    *)
        name="${toplevel##*/}"
        prefix="" ;;
esac

esc=$'\033'
st="$esc\\"                         # OSC 8 string terminator (ESC \)
blue=$'\033[38;5;111m'              # brightBlue in the colorLevel:2 palette
reset=$'\033[0m'

# Link target. Scheme is chosen by $CCSL_EDITOR so a shared checkout stays
# editor-neutral; set it in your personal env (e.g. ~/.claude settings "env",
# not this repo) to override. Encode spaces so the URI stays intact.
#   unset / file  -> file://<abs-path>            (OS default, universal)
#   vscode        -> vscode://file/<abs-path>/     (opens folder in VS Code)
#   cursor        -> cursor://file/<abs-path>/     (opens folder in Cursor)
enc="${toplevel// /%20}"
case "${CCSL_EDITOR:-file}" in
    vscode) uri="vscode://file${enc}/" ;;
    cursor) uri="cursor://file${enc}/" ;;
    *)      uri="file://${enc}" ;;
esac

# colour + optional icon, then the name as an OSC 8 hyperlink to the folder.
printf '%s%s%s]8;;%s%s%s%s]8;;%s%s' \
    "$blue" "$prefix" "$esc" "$uri" "$st" "$name" "$esc" "$st" "$reset"
