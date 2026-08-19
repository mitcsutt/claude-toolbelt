#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
# shellcheck source=../../../tests/lib/assert.sh
source "$ROOT/tests/lib/assert.sh"

SKILL="$ROOT/plugins/orchestrate/skills/orchestrate/SKILL.md"

if [ -f "$SKILL" ]; then skill_exists=0; else skill_exists=1; fi
assert_true "$skill_exists" "SKILL.md exists"

# Frontmatter name matches the skill directory.
sname=$(awk '/^name:/{print $2; exit}' "$SKILL")
assert_eq "orchestrate" "$sname" "SKILL.md frontmatter name is 'orchestrate'"

# Promise: decide whether to orchestrate at all before delegating.
grep -q '^## Step 0' "$SKILL"; assert_true "$?" "ships the 'decide whether to orchestrate' gate (Step 0)"

# Promise: route each slice to the cheapest capable tier.
grep -qi 'cheapest capable tier' "$SKILL"; assert_true "$?" "documents routing to the cheapest capable tier"

# Promise: every delegated prompt is a structured handoff packet.
grep -qi '## Handoff packet' "$SKILL"; assert_true "$?" "documents the handoff packet"

# Promise: compact returns that stay traceable to a source.
grep -qi '## Compact returns' "$SKILL"; assert_true "$?" "requires compact, traceable returns"

# Promise: vet worker output before acting on it.
grep -qi 'Vet, don' "$SKILL"; assert_true "$?" "requires vetting worker output before acting"

# Promise: a verification gate before claiming done.
grep -qi 'Verification gate' "$SKILL"; assert_true "$?" "ships a verification gate"

assert_summary
