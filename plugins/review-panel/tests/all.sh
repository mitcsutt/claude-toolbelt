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
# Spawn policy as data: the reviewers allowed the Agent tool, and the one
# agent type each may dispatch.
SPAWNERS="rules-lawyer"
spawns() { case "$1" in rules-lawyer) echo instruction-follower ;; esac; }
is_spawner() { case " $SPAWNERS " in *" $1 "*) return 0 ;; esac; return 1; }

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
  if [ -n "$desc" ]; then ok=0; else ok=1; fi
  assert_true "$ok" "$a: has a description"

  # Promise: read-only reviewers that can't edit; only SPAWNERS may spawn, and only their follower.
  tools=$(fm "$f" tools)
  if [ -n "$tools" ]; then ok=0; else ok=1; fi
  assert_true "$ok" "$a: declares an explicit tools allowlist"
  echo "$tools" | grep -Eq '(^|[ ,])(Task|Edit|NotebookEdit)([ ,(]|$)'
  assert_false "$?" "$a: tools exclude Task/Edit/NotebookEdit"
  if is_spawner "$a"; then
    echo "$tools" | grep -Eq '(^|[ ,])Agent([ ,(]|$)'
    assert_true "$?" "$a: tools include Agent, as a listed spawner"
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

# Promise: a spawner dispatches no agent type other than its follower.
# The platform doesn't enforce this (Agent(type) lists are ignored inside
# subagents) and the eval trace doesn't show a subagent's own Agent calls, so
# the boundary is prompt-enforced and covered by eval review. This checks only
# that every agent type a spawner names is its follower.
for a in $SPAWNERS; do
  f="$AGENTS/$a.md"
  others=$(grep -o 'review-panel:[a-z-]*' "$f" | sort -u | grep -v "^review-panel:$(spawns "$a")\$")
  assert_eq "" "$others" "$a names no agent type to dispatch but $(spawns "$a")"
  grep -q 'general-purpose' "$f"; assert_false "$?" "$a never mentions general-purpose"
done

# Promise: each reviewer's references load on demand from the plugin, every
# one it names exists, and every file in its directory is reachable.
REFROOT="$PLUGIN/references"
for d in "$REFROOT"/*/; do
  name=$(basename "$d")
  [ "$name" = shared ] && continue
  f="$AGENTS/$name.md"
  if [ -f "$f" ]; then ok=0; else ok=1; fi
  assert_true "$ok" "references/$name/ belongs to an agent"
  [ -f "$f" ] || continue
  grep -q "\${CLAUDE_PLUGIN_ROOT}/references/$name/" "$f"; assert_true "$?" "$name loads references via CLAUDE_PLUGIN_ROOT"
  # Full paths must resolve; a bare file name means one in its own directory.
  for p in $(grep -oE '\$\{CLAUDE_PLUGIN_ROOT\}/references/[a-z-]+/[a-z-]+\.md' "$f" | sort -u); do
    if [ -f "$REFROOT/${p#*/references/}" ]; then ok=0; else ok=1; fi
    assert_true "$ok" "$name reference exists: ${p#*/references/}"
  done
  for r in $(sed -E 's#\$\{CLAUDE_PLUGIN_ROOT\}/[a-z/-]+\.md##g' "$f" | grep -oE '`[a-z][a-z-]*\.md`' | tr -d '`' | sort -u); do
    if [ -f "$d$r" ]; then ok=0; else ok=1; fi
    assert_true "$ok" "$name reference exists: $name/$r"
  done
  for r in "$d"*.md; do
    b=$(basename "$r")
    others=$(find "$d" -name '*.md' ! -name "$b")
    # shellcheck disable=SC2086 # $others is a list of plain .md paths
    if grep -q "$b" "$f" $others; then ok=0; else ok=1; fi
    assert_true "$ok" "reference is reachable from $name: $b"
  done
done
for r in "$REFROOT"/shared/*.md; do
  b=$(basename "$r")
  if grep -q "\${CLAUDE_PLUGIN_ROOT}/references/shared/$b" "$AGENTS"/*.md; then ok=0; else ok=1; fi
  assert_true "$ok" "shared reference is loaded by an agent: $b"
done

# Promise: every evidence ID cited in an agent or reference resolves to
# exactly one row across all evidence files.
ID_RE='\b(E|FC|TM|GA|NF|SR|CK|CH|DX|OU|GS|AP)[0-9]{1,2}\b'
for id in $(cat "$AGENTS"/*.md "$REFROOT"/*/*.md | grep -oE "$ID_RE" | sort -u); do
  rows=$(cat "$REFROOT"/*/evidence.md | grep -c "^| $id |")
  assert_eq "1" "$rows" "evidence ID $id has exactly one row"
done

# Promise: instruction-follower is a read-only helper that loads no CLAUDE.md.
FOL="$AGENTS/instruction-follower.md"
assert_eq "instruction-follower" "$(fm "$FOL" name)" "instruction-follower: name matches filename"
assert_eq "Read, Grep, Glob" "$(fm "$FOL" tools)" "instruction-follower: read-only tools only"
assert_eq "true" "$(fm "$FOL" omitClaudeMd)" "instruction-follower: omits CLAUDE.md"

# Promise: every eval case seeds its fixture, grades the report file its prompt
# names, targets every regex grader, and has a control grader or says why not.
for EVAL in "$PLUGIN"/evals/*/; do
  c=$(basename "$EVAL")
  if [ -f "$EVAL/case.yaml" ]; then ok=0; else ok=1; fi
  assert_true "$ok" "eval case exists: $c"
  [ -f "$EVAL/case.yaml" ] || continue
  if python3 -c 'import yaml' >/dev/null 2>&1; then
    facts=$(python3 -I -c '
import sys, yaml
c = yaml.safe_load(open(sys.argv[1]))
gs = c.get("graders", [])
llm = [g for g in gs if g.get("type") == "llm"]
paths = {(g.get("focus") or {}).get("path") for g in llm}
shaped = all((g.get("focus") or {}).get("source") == "file" for g in llm)
prompt = (c.get("execution") or {}).get("prompt", "")
p = next(iter(paths)) if len(paths) == 1 else None
print(c.get("context", {}).get("scaffold_script"))
print(not llm or (shaped and p is not None and p in prompt))
print(all(g.get("target") for g in gs if g.get("type") == "regex"))
print(any(g["name"].startswith("control_") for g in gs) or "no control" in c.get("description", ""))
' "$EVAL/case.yaml")
    assert_eq "fixture.sh" "$(echo "$facts" | sed -n 1p)" "$c: seeds its fixture from fixture.sh"
    if [ -f "$EVAL/fixture.sh" ]; then ok=0; else ok=1; fi
    assert_true "$ok" "$c: fixture.sh exists"
    assert_eq "True" "$(echo "$facts" | sed -n 2p)" "$c: every llm grader grades the report file the prompt names"
    assert_eq "True" "$(echo "$facts" | sed -n 3p)" "$c: every regex grader has a target"
    assert_eq "True" "$(echo "$facts" | sed -n 4p)" "$c: has a control grader or says 'no control'"
  else
    echo "($c case.yaml checks skipped — python3 with PyYAML not installed)"
  fi
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
if [ -f "$SKILL" ]; then ok=0; else ok=1; fi
assert_true "$ok" "SKILL.md exists"
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
