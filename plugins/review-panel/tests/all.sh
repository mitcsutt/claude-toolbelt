#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
# shellcheck source=../../../tests/lib/assert.sh
source "$ROOT/tests/lib/assert.sh"

PLUGIN="$ROOT/plugins/review-panel"
AGENTS="$PLUGIN/agents"
SKILL="$PLUGIN/skills/assemble-panel/SKILL.md"
REFS="$PLUGIN/skills/assemble-panel/references"

ROSTER="grumpy-senior paranoid-sre fact-checker dx-advocate threat-modeller goal-auditor outsider contract-keeper cost-hawk noise-filter"

fm() { # file key -> frontmatter value
  awk -v k="$2" 'NR==1&&/^---$/{f=1;next} f&&/^---$/{exit} f&&index($0,k":")==1{sub("^"k":[ ]*","");print;exit}' "$1"
}

# Promise: ships the ten persona agents.
n=0
for a in $ROSTER; do
  f="$AGENTS/$a.md"
  if [ -f "$f" ]; then ok=0; n=$((n + 1)); else ok=1; fi
  assert_true "$ok" "agent $a exists"
  [ -f "$f" ] || continue
  assert_eq "$a" "$(fm "$f" name)" "$a: frontmatter name matches filename"
  desc=$(fm "$f" description)
  [ -n "$desc" ]; assert_true "$?" "$a: has a description"

  # Promise: read-only reviewers that can't fan out or edit.
  tools=$(fm "$f" tools)
  echo "$tools" | grep -Eq '(^|[ ,])(Agent|Task|Edit|NotebookEdit)([ ,]|$)'
  assert_false "$?" "$a: tools exclude Agent/Task/Edit"
  [ -n "$tools" ]; assert_true "$?" "$a: declares an explicit tools allowlist"

  # Promise: never checks out branches, never prints secret values.
  grep -qi 'edit, commit, check out, switch branches' "$f"; assert_true "$?" "$a: forbids checkout in a shared tree"
  grep -qi 'never print secret values' "$f"; assert_true "$?" "$a: forbids printing secret values"
  grep -q "Don't spawn subagents" "$f"; assert_true "$?" "$a: forbids spawning subagents"

  # Promise: returns one line pointing at its report file.
  grep -q 'return exactly one line\|then return exactly one line' "$f"; assert_true "$?" "$a: returns one line"
done
assert_eq "10" "$n" "exactly ten roster agents present"

# No stray agent files beyond the roster.
extra=0
for f in "$AGENTS"/*.md; do
  b=$(basename "$f" .md)
  case " $ROSTER " in *" $b "*) ;; *) extra=1; echo "unexpected agent: $b" >&2 ;; esac
done
assert_eq "0" "$extra" "no agents outside the documented roster"

# Promise: reviewers report everything with severity + confidence; filtering is a separate pass.
for a in $ROSTER; do
  [ "$a" = noise-filter ] && continue
  f="$AGENTS/$a.md"
  grep -q 'Severity: BLOCK | SHOULD | COULD' "$f"; assert_true "$?" "$a: uses the shared severity scale"
  grep -q 'Confidence: high | medium | low' "$f"; assert_true "$?" "$a: reports confidence"
  grep -q 'Verdict: SHIP | SHIP WITH FIXES | DO NOT SHIP' "$f"; assert_true "$?" "$a: gives one verdict"
  grep -q 'separate pass filters' "$f"; assert_true "$?" "$a: reports everything, filter is separate"
done

# Promise: fact-checker uses the CONFIRMED / WRONG / UNVERIFIED ledger.
grep -q 'CONFIRMED' "$AGENTS/fact-checker.md" && grep -q 'WRONG' "$AGENTS/fact-checker.md" && grep -q 'UNVERIFIED' "$AGENTS/fact-checker.md"
assert_true "$?" "fact-checker marks claims CONFIRMED/WRONG/UNVERIFIED"

# Promise: grumpy-senior delivers a cut-down version.
grep -q '## Ship it on Friday' "$AGENTS/grumpy-senior.md"; assert_true "$?" "grumpy-senior delivers 'Ship it on Friday'"

# Promise: outsider writes its cold take before reading the artifact.
grep -q 'Before opening the artifact' "$AGENTS/outsider.md"; assert_true "$?" "outsider takes a cold read first"

# Promise: noise-filter merges, marks cross-confirmed, spot-checks, drops with reasons.
for s in 'cross-confirmed' 'Spot-check' '## Dropped' '## Decisions for you' '## Fix before merge'; do
  grep -q "$s" "$AGENTS/noise-filter.md"; assert_true "$?" "noise-filter: $s"
done

# Skill structure.
[ -f "$SKILL" ]; assert_true "$?" "SKILL.md exists"
assert_eq "assemble-panel" "$(fm "$SKILL" name)" "SKILL.md frontmatter name is 'assemble-panel'"

# Promise: sizes the panel (and can decline) before picking.
grep -q '^## Step 0: Size the panel' "$SKILL"; assert_true "$?" "skill sizes the panel first"
grep -qi "panel isn't worth it" "$SKILL"; assert_true "$?" "skill can decline a panel for trivial work"

# Promise: the routing table covers every roster agent, and names no agent that doesn't exist.
for a in $ROSTER; do
  grep -q "\`$a\`" "$SKILL"; assert_true "$?" "skill routes to $a"
done
missing=0
for a in $(grep -o '`[a-z]*-[a-z-]*`' "$SKILL" | tr -d '`' | sort -u); do
  case "$a" in assemble-panel|general-purpose|review-panel) continue ;; esac
  [ -f "$AGENTS/$a.md" ] || { missing=1; echo "skill names unknown agent: $a" >&2; }
done
assert_eq "0" "$missing" "skill names only agents that exist"

# Promise: prints the roster with reasons and what was skipped, then dispatches.
grep -q '^## Step 3: Print the roster' "$SKILL"; assert_true "$?" "skill prints the roster"
grep -q '^Skipped:' "$SKILL"; assert_true "$?" "roster example lists skipped reviewers"

# Promise: briefs open with a fixed goal and vary context.
grep -q "don't argue against the goal itself" "$REFS/brief-template.md"; assert_true "$?" "brief opens with the fixed goal"
grep -q '## Context you get' "$REFS/brief-template.md"; assert_true "$?" "brief sets a deliberate context level"
grep -q "Plugin agents don't load CLAUDE.md" "$SKILL"; assert_true "$?" "skill passes repo rules into briefs"

# Promise: parallel dispatch, then noise-filter.
grep -q 'in \*\*one message\*\*' "$SKILL"; assert_true "$?" "skill dispatches in parallel"
grep -q '^## Step 6: Filter' "$SKILL"; assert_true "$?" "skill runs the noise-filter pass"

# Promise: ad-hoc personas for uncovered lenses use the shared contract.
[ -f "$REFS/persona-contract.md" ]; assert_true "$?" "persona-contract reference exists"
grep -q 'Severity: BLOCK | SHOULD | COULD' "$REFS/persona-contract.md"; assert_true "$?" "ad-hoc personas use the shared contract"

# Promise: never publishes without the user's go-ahead.
grep -qi "Don't apply fixes, post comments or publish" "$SKILL"; assert_true "$?" "skill never posts without approval"

assert_summary
