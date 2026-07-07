#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/assert.sh"
F="$HERE/../skills/agent-loop-postmortem/SKILL.md"
has() { grep -qiF -e "$1" "$F"; assert_true $? "postmortem must mention: $1"; }
hasnt() { grep -qiF -e "$1" "$F"; assert_false $? "postmortem must NOT contain: $1"; }
has 'LOOP_USAGE.jsonl'
has 'Loop-Status'
has 'blocked'
has 'segment'
# Persistent cross-run knowledge: automatic promote/classify/dedup step at loop close
has 'KNOWLEDGE.md'
has 'PROMOTE'
has 'DROP'
has 'DEDUP'
has 'slice-specific'
has 'AUTOMATIC'
# Task 5: by_model billed surface rollup
has 'by_model'
has 'cache_read'
has 'parent) process only'
# Task 6: Evaluator invocation count
has 'Evaluator invocation'
# Task 7: postmortem output relocated to $LOOP_DIR
has 'LOOP_DIR/POSTMORTEM.md'
hasnt 'docs/postmortems'
# Plan 3 Task 5: learnings digest must stay within the 2KB cap (inert until fixture present)
LEARN="$HERE/../tests/fixtures/LOOP_LEARNINGS.sample.md"
if [ -f "$LEARN" ]; then
  digest="$(awk '/^## Patterns/{f=1;next} /^## /{f=0} f' "$LEARN")"
  bytes="$(printf '%s' "$digest" | wc -c | tr -d ' ')"
  [ "$bytes" -le 2048 ] || echo "WARN: digest is ${bytes}B (> 2KB cap) — §13 cap not holding in practice"
fi
# Plan 4 Task 7: Step 4 promotes durable invariants into KNOWLEDGE
grep -qiE 'promote.*invariant|invariants.*KNOWLEDGE|## Invariants' "$F"; assert_true $? "Step 4 must promote durable invariants into KNOWLEDGE.md"
assert_summary
