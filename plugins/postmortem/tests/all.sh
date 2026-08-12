#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
# shellcheck source=../../../tests/lib/assert.sh
source "$ROOT/tests/lib/assert.sh"

SKILL="$ROOT/plugins/postmortem/skills/postmortem/SKILL.md"

if [ -f "$SKILL" ]; then skill_exists=0; else skill_exists=1; fi
assert_true "$skill_exists" "SKILL.md exists"

# Promise: "The output has the 8 named sections... All 8. Not 7, not 12."
# Pull the numbered template out of the "Required sections" block.
block=$(awk '/^## Required sections/{f=1; next} /^## /{if (f) exit} f' "$SKILL")

count=$(printf '%s\n' "$block" | grep -cE '^[0-9]+\. \*\*[^*]+\*\*')
assert_eq "8" "$count" "SKILL.md's Required sections block defines exactly 8 sections"

# Each of the 8 must be named (a non-empty bold heading) in order 1..8.
named=1
i=1
while [ "$i" -le 8 ]; do
  printf '%s\n' "$block" | grep -qE "^${i}\. \*\*[^*]+\*\*" || named=0
  i=$((i + 1))
done
if [ "$named" -eq 1 ]; then all_named=0; else all_named=1; fi
assert_true "$all_named" "sections 1 through 8 are each named in the template"

assert_summary
