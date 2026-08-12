#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
# shellcheck source=../../../tests/lib/assert.sh
source "$ROOT/tests/lib/assert.sh"

MCP="$ROOT/plugins/find-docs/.mcp.json"

jq -e . "$MCP" >/dev/null 2>&1
assert_true $? ".mcp.json is valid JSON"

# Promise: "Context7 for version-specific library docs, Exa for web/code search."
jq -e '.mcpServers.context7' "$MCP" >/dev/null 2>&1
assert_true $? ".mcp.json declares the context7 server"

jq -e '.mcpServers.exa' "$MCP" >/dev/null 2>&1
assert_true $? ".mcp.json declares the exa server"

assert_summary
