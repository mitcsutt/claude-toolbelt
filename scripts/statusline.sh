#!/bin/bash
# Two-line statusline - concise, quick to scan
#
# Line 1: [Model] folder | branch | sandbox
# Line 2: bar ctx%  ↑session_plan%  plan%·timeleft  duration  cache%
#
# Context % uses Claude Code's pre-calculated remaining_percentage,
# which accounts for compaction reserves. 100% = compaction fires.
# Plan usage shows the Claude.ai 5-hour rolling session limit
# (subscribers only; gracefully omitted when data is unavailable).
# ↑session_plan% is the slice of the 5-hour budget used this session;
# it resets to 0 when /clear starts a new session.

# Read stdin (Claude Code passes JSON data via stdin)
stdin_data=$(cat)

# Single jq call - extract all values at once
IFS=$'\t' read -r current_dir model_name cost duration_ms ctx_used cache_pct five_hour_pct five_hour_resets_at < <(
    echo "$stdin_data" | jq -r '[
        .workspace.current_dir // "unknown",
        .model.display_name // "Unknown",
        (try (.cost.total_cost_usd // 0 | . * 10000 | round / 10000) catch 0),
        (.cost.total_duration_ms // 0),
        (try (
            if (.context_window.remaining_percentage // null) != null then
                100 - (.context_window.remaining_percentage | floor)
            elif (.context_window.context_window_size // 0) > 0 then
                (((.context_window.current_usage.input_tokens // 0) +
                  (.context_window.current_usage.cache_creation_input_tokens // 0) +
                  (.context_window.current_usage.cache_read_input_tokens // 0)) * 100 /
                 .context_window.context_window_size) | floor
            else "null" end
        ) catch "null"),
        (try (
            (.context_window.current_usage // {}) |
            if (.input_tokens // 0) + (.cache_read_input_tokens // 0) > 0 then
                ((.cache_read_input_tokens // 0) * 100 /
                 ((.input_tokens // 0) + (.cache_read_input_tokens // 0))) | floor
            else 0 end
        ) catch 0),
        (.rate_limits.five_hour.used_percentage // ""),
        (.rate_limits.five_hour.resets_at // "")
    ] | @tsv'
)

# Bash-level fallback: if jq crashed entirely, extract fields individually
if [ -z "$current_dir" ] && [ -z "$model_name" ]; then
    current_dir=$(echo "$stdin_data" | jq -r '.workspace.current_dir // .cwd // "unknown"' 2>/dev/null)
    model_name=$(echo "$stdin_data" | jq -r '.model.display_name // "Unknown"' 2>/dev/null)
    cost=$(echo "$stdin_data" | jq -r '(.cost.total_cost_usd // 0)' 2>/dev/null)
    duration_ms=$(echo "$stdin_data" | jq -r '(.cost.total_duration_ms // 0)' 2>/dev/null)
    ctx_used=""
    cache_pct="0"
    five_hour_pct=""
    five_hour_resets_at=""
    : "${current_dir:=unknown}"
    : "${model_name:=Unknown}"
    : "${cost:=0}"
    : "${duration_ms:=0}"
fi

# Cache context-window % for hooks that don't get it in stdin (#30202).
# context-warn.sh in plugins/guardrails reads this. Skip silently on any failure.
if [ -n "$ctx_used" ] && [ "$ctx_used" != "null" ]; then
    session_id_for_cache=$(echo "$stdin_data" | jq -r '.session_id // ""' 2>/dev/null)
    if [ -n "$session_id_for_cache" ]; then
        printf '{"pct":%s,"ts":%s,"session":"%s"}\n' \
            "$ctx_used" "$(date +%s)" "$session_id_for_cache" \
            > "$HOME/.claude/.context-window.json" 2>/dev/null || true
    fi
fi

# Git info
if cd "$current_dir" 2>/dev/null; then
    git_branch=$(git -c core.useBuiltinFSMonitor=false branch --show-current 2>/dev/null)
    git_root=$(git -c core.useBuiltinFSMonitor=false rev-parse --show-toplevel 2>/dev/null)
fi

# Build folder display (repo name or basename)
if [ -n "$git_root" ]; then
    repo_name=$(basename "$git_root")
    if [ "$current_dir" = "$git_root" ]; then
        folder_name="$repo_name"
    else
        folder_name=$(basename "$current_dir")
    fi
else
    folder_name=$(basename "$current_dir")
fi

# Sandbox mode detection - project settings take priority over user settings
sandbox_label=""
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
        sandbox_label="sandbox:auto"
    else
        sandbox_label="sandbox"
    fi
fi

# Compact 6-char progress bar for context usage
progress_bar=""
bar_width=6

if [ -n "$ctx_used" ] && [ "$ctx_used" != "null" ]; then
    filled=$((ctx_used * bar_width / 100))
    empty=$((bar_width - filled))

    if [ "$ctx_used" -lt 50 ]; then
        bar_color='\033[32m'  # Green
    elif [ "$ctx_used" -lt 80 ]; then
        bar_color='\033[33m'  # Yellow
    else
        bar_color='\033[31m'  # Red
    fi

    progress_bar="${bar_color}"
    for ((i=0; i<filled; i++)); do
        progress_bar="${progress_bar}▪"
    done
    progress_bar="${progress_bar}\033[2m"
    for ((i=0; i<empty; i++)); do
        progress_bar="${progress_bar}▫"
    done
    progress_bar="${progress_bar}\033[0m"

    ctx_pct="${ctx_used}%"
else
    ctx_pct=""
fi

# Session time (human-readable, compact)
session_time=""
if [ "$duration_ms" -gt 0 ] 2>/dev/null; then
    total_sec=$((duration_ms / 1000))
    hours=$((total_sec / 3600))
    minutes=$(((total_sec % 3600) / 60))
    seconds=$((total_sec % 60))
    if [ "$hours" -gt 0 ]; then
        session_time="${hours}h${minutes}m"
    elif [ "$minutes" -gt 0 ]; then
        session_time="${minutes}m${seconds}s"
    else
        session_time="${seconds}s"
    fi
fi

# Plan usage display (Claude.ai 5-hour rolling session limit)
# Available for subscribers after the first API response in a session.
plan_display=""
plan_color=""
if [ -n "$five_hour_pct" ] && [ -n "$five_hour_resets_at" ]; then
    pct_int=$(printf '%.0f' "$five_hour_pct" 2>/dev/null)
    now_epoch=$(date +%s)
    secs_left=$(( five_hour_resets_at - now_epoch ))
    if [ "$secs_left" -gt 0 ]; then
        hrs_left=$(( secs_left / 3600 ))
        mins_left=$(( (secs_left % 3600) / 60 ))
        if [ "$hrs_left" -gt 0 ]; then
            time_left="${hrs_left}h${mins_left}m"
        else
            time_left="${mins_left}m"
        fi
        plan_display="${pct_int}% · ${time_left}"
    else
        plan_display="${pct_int}%"
    fi
elif [ -n "$five_hour_pct" ]; then
    pct_int=$(printf '%.0f' "$five_hour_pct" 2>/dev/null)
    plan_display="${pct_int}%"
fi

# Determine plan color based on usage percentage
if [ -n "$pct_int" ]; then
    if [ "$pct_int" -ge 90 ]; then
        plan_color='\033[1;31m'   # Bold red  (90–100%)
    elif [ "$pct_int" -ge 80 ]; then
        plan_color='\033[38;5;208m' # Orange    (80–89%)
    elif [ "$pct_int" -ge 60 ]; then
        plan_color='\033[33m'     # Yellow    (60–79%)
    else
        plan_color='\033[32m'     # Green     (0–59%)
    fi
fi

# Session plan % — slice of the 5-hour budget used since last /clear.
# Detects a new session when cost drops back toward zero.
# Persists the baseline plan% in a temp file between statusline calls.
session_plan_display=""
session_plan_color=""
SESSION_BASELINE_FILE="$HOME/.claude/.statusline_session_baseline"

if [ -n "$five_hour_pct" ]; then
    stored_baseline=""
    stored_last_cost="0"
    if [ -f "$SESSION_BASELINE_FILE" ]; then
        stored_baseline=$(jq -r '.plan_baseline // ""' "$SESSION_BASELINE_FILE" 2>/dev/null)
        stored_last_cost=$(jq -r '.last_cost // "0"' "$SESSION_BASELINE_FILE" 2>/dev/null)
    fi

    # New session: cost dropped from meaningful value back to near-zero
    is_new_session=$(awk -v c="${cost:-0}" -v lc="${stored_last_cost:-0}" \
        'BEGIN { print (c < 0.0001 && lc > 0.001) ? "1" : "0" }')

    if [ "$is_new_session" = "1" ] || [ -z "$stored_baseline" ]; then
        stored_baseline="$five_hour_pct"
    fi

    # Persist updated state
    printf '{"plan_baseline":%.4f,"last_cost":%.6f}\n' \
        "${stored_baseline:-0}" "${cost:-0}" > "$SESSION_BASELINE_FILE" 2>/dev/null

    # Delta = how much of the 5-hour budget this session has consumed
    session_delta=$(awk -v curr="$five_hour_pct" -v base="${stored_baseline:-0}" 'BEGIN {
        d = curr - base
        if (d < 0) d = 0
        printf "%d", d + 0.5
    }')

    if [ -n "$session_delta" ]; then
        session_plan_display="↑${session_delta}%"
        if [ "$session_delta" -ge 25 ]; then
            session_plan_color='\033[1;31m'     # Bold red
        elif [ "$session_delta" -ge 15 ]; then
            session_plan_color='\033[38;5;208m' # Orange
        elif [ "$session_delta" -ge 8 ]; then
            session_plan_color='\033[33m'       # Yellow
        else
            session_plan_color='\033[32m'       # Green
        fi
    fi
fi

# Separator
SEP='\033[2m│\033[0m'

# Short model name (drop "Claude X.Y " prefix)
short_model=$(echo "$model_name" | sed -E 's/Claude [0-9.]+ //; s/^Claude //')

# LINE 1: [Model] folder | branch | sandbox
line1=$(printf '\033[37m[%s]\033[0m' "$short_model")
line1="$line1 $(printf '\033[94m%s\033[0m' "$folder_name")"
if [ -n "$git_branch" ]; then
    line1="$line1 $(printf '%b \033[96m%s\033[0m' "$SEP" "$git_branch")"
fi
if [ -n "$sandbox_label" ]; then
    line1="$line1 $(printf '%b \033[33m%s\033[0m' "$SEP" "$sandbox_label")"
fi

# LINE 2: bar ctx%  ↑session_plan%  plan%·timeleft  time  cache%
line2=""
if [ -n "$progress_bar" ]; then
    line2=$(printf '%b' "$progress_bar")
fi
if [ -n "$ctx_pct" ]; then
    if [ -n "$line2" ]; then
        line2="$line2 $(printf '\033[37m%s\033[0m' "$ctx_pct")"
    else
        line2=$(printf '\033[37m%s\033[0m' "$ctx_pct")
    fi
fi
if [ -n "$session_plan_display" ]; then
    line2="$line2 $(printf '%b '"${session_plan_color}"'%s\033[0m' "$SEP" "$session_plan_display")"
fi
if [ -n "$plan_display" ]; then
    line2="$line2 $(printf "${plan_color}"'%s\033[0m' " $plan_display")"
fi
if [ -n "$session_time" ]; then
    line2="$line2 $(printf '%b \033[36m%s\033[0m' "$SEP" "$session_time")"
fi
if [ "${cache_pct:-0}" -gt 0 ] 2>/dev/null; then
    line2="$line2 $(printf '\033[2m↻%s%%\033[0m' "$cache_pct")"
fi

printf '%b\n\n%b' "$line1" "$line2"
