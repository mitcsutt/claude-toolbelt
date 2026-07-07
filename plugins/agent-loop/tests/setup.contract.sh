#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/assert.sh"
F="$HERE/../skills/agent-loop-setup/SKILL.md"
has() { grep -qiF -e "$1" "$F"; assert_true $? "setup must mention: $1"; }
hasnt() { grep -qiF -e "$1" "$F"; assert_false $? "setup must NOT contain: $1"; }

has 'worktree'
has '--worktree'
has 'brainstorming'
has 'writing-plans'
has 'segment'
has 'Limits'
has 'tick_timeout'
hasnt 'max_total_cost_usd'
has 'Blocker policy'
has 'permission-advisor'
has 'run.sh'
has 'CLAUDE_PLUGIN_ROOT'
has 'timeout'
has 'coreutils'
# Persistent cross-run knowledge file: created if absent, committed, NOT per-run / NOT seeded
has 'KNOWLEDGE.md'
has 'cross-run'
has 'persistent'
hasnt '.zshrc'
hasnt 'ScheduleWakeup'

# Task 8: machine logs gitignored, ledgers dropped from seed git add
SETUP="$HERE/../skills/agent-loop-setup/SKILL.md"
for f in run.log events.jsonl LOOP_LOG.jsonl LOOP_USAGE.jsonl; do
  grep -q "$f" "$SETUP" || { echo "FAIL: setup gitignore must list $f"; exit 1; }
done
# ledger files must NOT be in the explicit git add list anymore
grep -qE 'git add .*LOOP_USAGE.jsonl' "$SETUP" && { echo "FAIL: LOOP_USAGE.jsonl still in git add list"; exit 1; } || true

TMPL="$HERE/../templates/LOOP_CONFIG.md"
# Plan 2 Task 1: Evaluator tier no longer flat-pinned to most-capable (defeated §10)
grep -qE '^Evaluator tier:[[:space:]]*most-capable[[:space:]]*$' "$TMPL"; assert_false $? "template must NOT flat-pin Evaluator tier: most-capable"
grep -qE '^Evaluator tier:' "$TMPL"; assert_true $? "Evaluator tier line still present (blank/knob)"

# Plan 4 Task 1: Invariants section present in knowledge + learnings templates
for invt in LOOP_KNOWLEDGE.md LOOP_LEARNINGS.md; do
  grep -q '## Invariants' "$HERE/../templates/$invt"; assert_true $? "$invt has ## Invariants section"
  grep -qi 'check:' "$HERE/../templates/$invt"; assert_true $? "$invt Invariants document the check: field"
done

# Plan 4 Task 6: setup inspects + classifies repo PostToolUse hooks
grep -qiE 'PostToolUse|posttooluse' "$F"; assert_true $? "setup must inspect repo PostToolUse hooks"
grep -qiE 'auto-?fix|revert|reformat' "$F"; assert_true $? "setup must classify auto-fix vs revert hooks"

# Plan 5 Task 1: clone_of row metadata documented in plan template
grep -q 'clone_of' "$HERE/../templates/LOOP_PLAN.md"; assert_true $? "plan template documents clone_of metadata"

# Plan 5 Task 4: setup warns on clone-heavy plans
grep -qiE 'clone-heavy|clone famil|near-verbatim|sweet spot.*independent' "$F"; assert_true $? "setup must warn about clone-heavy plans"

assert_summary
