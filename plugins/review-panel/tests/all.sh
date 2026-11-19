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

ROSTER="grumpy-senior paranoid-sre fact-checker dx-advocate threat-modeller goal-auditor outsider contract-keeper cost-hawk rules-lawyer noise-filter"
HELPERS="instruction-follower"
REFS_RL="$PLUGIN/references/rules-lawyer"
RL="$AGENTS/rules-lawyer.md"

fm() { # file key -> frontmatter value
  awk -v k="$2" 'NR==1&&/^---$/{f=1;next} f&&/^---$/{exit} f&&index($0,k":")==1{sub("^"k":[ ]*","");print;exit}' "$1"
}

# Promise: ships the persona agents in the roster.
n=0; want=0
for a in $ROSTER; do
  want=$((want + 1))
  f="$AGENTS/$a.md"
  if [ -f "$f" ]; then ok=0; n=$((n + 1)); else ok=1; fi
  assert_true "$ok" "agent $a exists"
  [ -f "$f" ] || continue
  assert_eq "$a" "$(fm "$f" name)" "$a: frontmatter name matches filename"
  desc=$(fm "$f" description)
  [ -n "$desc" ]; assert_true "$?" "$a: has a description"

  # Promise: read-only reviewers that can't edit; only rules-lawyer may spawn, and only its follower.
  tools=$(fm "$f" tools)
  [ -n "$tools" ]; assert_true "$?" "$a: declares an explicit tools allowlist"
  echo "$tools" | grep -Eq '(^|[ ,])(Task|Edit|NotebookEdit)([ ,(]|$)'
  assert_false "$?" "$a: tools exclude Task/Edit/NotebookEdit"
  if [ "$a" = rules-lawyer ]; then
    grep -q 'Spawn only `review-panel:instruction-follower`' "$f"; assert_true "$?" "$a: may spawn only instruction-follower"
    grep -q 'at most 3 scenarios × 3 runs' "$f"; assert_true "$?" "$a: states the follower budget"
  else
    echo "$tools" | grep -Eq '(^|[ ,])Agent([ ,(]|$)'
    assert_false "$?" "$a: tools exclude Agent"
    grep -q "Don't spawn subagents" "$f"; assert_true "$?" "$a: forbids spawning subagents"
  fi

  # Promise: never checks out branches, never prints secret values.
  grep -qi 'edit, commit, check out, switch branches' "$f"; assert_true "$?" "$a: forbids checkout in a shared tree"
  grep -qi 'never print secret values' "$f"; assert_true "$?" "$a: forbids printing secret values"

  # Promise: returns one line pointing at its report file.
  grep -q 'return exactly one line\|then return exactly one line' "$f"; assert_true "$?" "$a: returns one line"
done
assert_eq "$want" "$n" "every roster agent present"

# No stray agent files beyond the roster and its helpers.
extra=0
for f in "$AGENTS"/*.md; do
  b=$(basename "$f" .md)
  case " $ROSTER $HELPERS " in *" $b "*) ;; *) extra=1; echo "unexpected agent: $b" >&2 ;; esac
done
assert_eq "0" "$extra" "no agents outside the documented roster and helpers"

# Promise: reviewers report everything with severity + confidence; filtering is a separate pass.
for a in $ROSTER; do
  [ "$a" = noise-filter ] && continue
  f="$AGENTS/$a.md"
  grep -q 'Severity: BLOCK | SHOULD | COULD' "$f"; assert_true "$?" "$a: uses the shared severity scale"
  grep -q 'Confidence: high | medium | low' "$f"; assert_true "$?" "$a: reports confidence"
  grep -q 'Verdict: SHIP | SHIP WITH FIXES | DO NOT SHIP' "$f"; assert_true "$?" "$a: gives one verdict"
  grep -q 'separate pass filters' "$f"; assert_true "$?" "$a: reports everything, filter is separate"
done

# Promise: rules-lawyer spawns no agent type other than its follower.
others=$(grep -o 'review-panel:[a-z-]*' "$RL" | sort -u | grep -v '^review-panel:instruction-follower$')
assert_eq "" "$others" "rules-lawyer names no other agent type to dispatch"
grep -q 'general-purpose' "$RL"; assert_false "$?" "rules-lawyer never mentions general-purpose"

# Promise: rules-lawyer picks a tier, maps what loads, keeps a claim ledger and a redline.
for s in 'T0, static only' 'T1, static and verify' 'T2, adds followers' '## Load map' '## Redline' '## Claim ledger' '## Follower results' 'CONFIRMED, WRONG or UNVERIFIED' 'Refute before you report'; do
  grep -q "$s" "$RL"; assert_true "$?" "rules-lawyer: $s"
done

# Promise: its checklists load on demand from the plugin, and every one it names exists (and vice versa).
grep -q '${CLAUDE_PLUGIN_ROOT}/references/rules-lawyer/' "$RL"; assert_true "$?" "rules-lawyer loads references via CLAUDE_PLUGIN_ROOT"
for r in $(grep -o '[a-z][a-z-]*\.md' "$RL" | sort -u); do
  if [ -f "$REFS_RL/$r" ]; then ok=0; else ok=1; fi
  assert_true "$ok" "rules-lawyer reference exists: $r"
done
for f in "$REFS_RL"/*.md; do
  grep -q "$(basename "$f")" "$RL"; assert_true "$?" "reference is reachable from rules-lawyer: $(basename "$f")"
done

# Promise: every evidence ID cited anywhere resolves to a row in evidence.md.
for id in $(cat "$RL" "$REFS_RL"/*.md | grep -oE '\bE[0-9]{1,2}\b' | sort -u); do
  grep -q "^| $id |" "$REFS_RL/evidence.md"; assert_true "$?" "evidence ID $id has a row"
done
grep -q '^## Checks not to make' "$REFS_RL/evidence.md"; assert_true "$?" "evidence.md lists checks not to make"

# Promise: platform facts carry a date and are re-checked live before a finding depends on them.
for f in "$REFS_RL"/context-files.md "$REFS_RL"/skills-and-commands.md "$REFS_RL"/hooks.md "$REFS_RL"/agents-and-tools.md; do
  grep -q 'on 2026-' "$f"; assert_true "$?" "$(basename "$f"): states when facts were checked"
  grep -q 'Before a finding depends on' "$f"; assert_true "$?" "$(basename "$f"): says to re-check live"
done

# Promise: instruction-follower is a read-only helper that loads no CLAUDE.md and runs nothing.
FOL="$AGENTS/instruction-follower.md"
assert_eq "instruction-follower" "$(fm "$FOL" name)" "instruction-follower: name matches filename"
assert_eq "Read, Grep, Glob" "$(fm "$FOL" tools)" "instruction-follower: read-only tools only"
assert_eq "true" "$(fm "$FOL" omitClaudeMd)" "instruction-follower: omits CLAUDE.md"
fm "$FOL" description | grep -q 'not for direct use'; assert_true "$?" "instruction-follower: says it is not for direct use"
grep -q 'run, edit and create nothing' "$FOL"; assert_true "$?" "instruction-follower: plans on paper only"

# Promise: two planted-defect evals grade rules-lawyer against a baseline; one carries a control.
for c in rules-lawyer-planted-defects rules-lawyer-platform-traps; do
  EVAL="$PLUGIN/evals/$c"
  if [ -f "$EVAL/case.yaml" ]; then ok=0; else ok=1; fi
  assert_true "$ok" "eval case exists: $c"
  grep -q 'scaffold_script: fixture.sh' "$EVAL/case.yaml" && [ -f "$EVAL/fixture.sh" ]; assert_true "$?" "$c: seeds its fixture from fixture.sh"
  grep -q 'focus: { source: file, path: AUDIT.md }' "$EVAL/case.yaml"; assert_true "$?" "$c: grades the written audit"
done
grep -q 'does NOT recommend' "$PLUGIN/evals/rules-lawyer-planted-defects/case.yaml"; assert_true "$?" "planted-defects eval has a control that must survive"

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
grep -q "Reviewers load the session's CLAUDE.md chain" "$SKILL"; assert_true "$?" "skill passes rules CLAUDE.md doesn't carry into briefs"

# Promise: parallel dispatch, then noise-filter.
grep -q 'in \*\*one message\*\*' "$SKILL"; assert_true "$?" "skill dispatches in parallel"
grep -q '^## Step 6: Filter' "$SKILL"; assert_true "$?" "skill runs the noise-filter pass"

# Promise: ad-hoc personas for uncovered lenses use the shared contract.
[ -f "$REFS/persona-contract.md" ]; assert_true "$?" "persona-contract reference exists"
grep -q 'Severity: BLOCK | SHOULD | COULD' "$REFS/persona-contract.md"; assert_true "$?" "ad-hoc personas use the shared contract"

# Promise: never publishes without the user's go-ahead.
grep -qi "Don't apply fixes, post comments or publish" "$SKILL"; assert_true "$?" "skill never posts without approval"

assert_summary
