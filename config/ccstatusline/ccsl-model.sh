#!/bin/bash
# ccstatusline custom-command widget: model name, with a Bedrock prefix.
#
# Mirrors the built-in model widget (prints .model.display_name), but when
# CLAUDE_CODE_USE_BEDROCK=1 it prepends an AWS glyph (Nerd Font U+F270) in AWS
# "Smile" orange (#FF9900) to flag a Bedrock-routed session.
#
# Perf: this machine taxes every process launch (~0.2-0.3s of exec-scan
# latency), so the script spawns NOTHING external — the model name is read with
# a bash regex instead of jq, and all escapes use $'...' builtins.
#
# Requires preserveColors:true — icon and name use different colours, so this
# script emits its own escapes.

stdin_data=$(cat)

re='"display_name"[[:space:]]*:[[:space:]]*"([^"]*)"'
model="Claude"
[[ $stdin_data =~ $re ]] && model="${BASH_REMATCH[1]}"

white=$'\033[38;5;188m'             # matches the built-in model widget's white
aws=$'\033[38;2;255;153;0m'         # AWS Smile orange #FF9900 (truecolor)
reset=$'\033[0m'

if [ "$CLAUDE_CODE_USE_BEDROCK" = "1" ]; then
    icon=$'\xef\x89\xb0'            # U+F270 (Nerd Font AWS)
    printf '%s%s%s %s%s' "$aws" "$icon" "$white" "$model" "$reset"
else
    printf '%s%s%s' "$white" "$model" "$reset"
fi
