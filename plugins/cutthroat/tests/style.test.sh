#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
# shellcheck source=./assert.sh
source "$HERE/assert.sh"

STYLE="$ROOT/plugins/cutthroat/output-styles/cutthroat.md"

[[ -f "$STYLE" ]]
assert_true $? "output style file exists"

fm=$(awk 'NR==1 && /^---$/{f=1;next} f && /^---$/{exit} f{print}' "$STYLE")

echo "$fm" | grep -qE '^name:[[:space:]]*cutthroat[[:space:]]*$'
assert_true $? "frontmatter declares name: cutthroat"

echo "$fm" | grep -qE '^keep-coding-instructions:[[:space:]]*true[[:space:]]*$'
assert_true $? "keep-coding-instructions is true (guards against the exo defect)"

echo "$fm" | grep -q 'force-for-plugin'
assert_false $? "force-for-plugin is absent (activation is the settings key)"

echo "$fm" | grep -qE '^description:[[:space:]]*\S'
assert_true $? "frontmatter has a non-empty description"

# The four load-bearing guarantees must be present in the body.
grep -qi 'never govern\|does not govern' "$STYLE"
assert_true $? "body contains the scope carve-out"

grep -qi 'Economy applies to the report, never the work' "$STYLE"
assert_true $? "body contains the economy-vs-work guarantee"

grep -qi 'asking is not chatter' "$STYLE"
assert_true $? "body contains the clarifying-question guard"

grep -qi 'my-voice' "$STYLE"
assert_true $? "body delimits my-voice ownership"

assert_summary
