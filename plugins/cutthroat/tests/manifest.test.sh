#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
# shellcheck source=../../../tests/lib/assert.sh
source "$ROOT/tests/lib/assert.sh"

PLUGIN_JSON="$ROOT/plugins/cutthroat/.claude-plugin/plugin.json"
MARKET_JSON="$ROOT/.claude-plugin/marketplace.json"

python3 -c "import json,sys; json.load(open('$PLUGIN_JSON'))" 2>/dev/null
assert_true $? "plugin.json is valid JSON"

name=$(python3 -c "import json;print(json.load(open('$PLUGIN_JSON')).get('name',''))" 2>/dev/null)
assert_eq "cutthroat" "$name" "plugin.json name is cutthroat"

for field in version description author; do
  present=$(python3 -c "import json;print('yes' if '$field' in json.load(open('$PLUGIN_JSON')) else 'no')" 2>/dev/null)
  assert_eq "yes" "$present" "plugin.json has $field"
done

python3 -c "import json,sys; json.load(open('$MARKET_JSON'))" 2>/dev/null
assert_true $? "marketplace.json is valid JSON"

found=$(python3 -c "
import json
d=json.load(open('$MARKET_JSON'))
print('yes' if any(p.get('name')=='cutthroat' for p in d['plugins']) else 'no')
" 2>/dev/null)
assert_eq "yes" "$found" "marketplace.json lists cutthroat"

src=$(python3 -c "
import json
d=json.load(open('$MARKET_JSON'))
print(next((p.get('source','') for p in d['plugins'] if p.get('name')=='cutthroat'),''))
" 2>/dev/null)
assert_eq "./plugins/cutthroat" "$src" "marketplace source path is correct"

assert_summary
