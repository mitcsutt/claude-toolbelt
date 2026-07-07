#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/assert.sh"
F="$HERE/../tick-prompt.md"

has() { grep -qF "$1" "$F"; assert_true $? "must contain: $1"; }
hasnt() { grep -qF "$1" "$F"; assert_false $? "must NOT contain: $1"; }

# Sentinels (exact)
has '<<LOOP_DONE>>'; has '<<LOOP_CONTINUE>>'; has '<<LOOP_HALT:'
# Mode selection
has 'PLAN TICK'; has 'EXECUTE TICK'
has 'REVIEW TICK'
has 'cumulative segment diff'
has 'Reviewed:'
# Segment-review follow-ups land in the segment being reviewed, never a later/unplanned one
has 'to the segment being reviewed'
has 'Follow-ups (from segment <X> review)'
has 'Never write a follow-up into a later or not-yet-planned segment'
hasnt 'follow-up task to the appropriate later segment'
# PLAN TICK is gated on pending work being drained first, so review-born follow-ups run before the next segment is planned
has 'no dependency-eligible `[ ]` task remains in any already-planned segment'
# Role pipeline
has 'Scout'; has 'Worker'; has 'Evaluator'
# Hard rules
has 'parent-side'; has 'never trust'
has 'checkpoint'
has 'blocked-upstream'
has 'workaround'
# Mechanical task class: deterministic tasks skip the Evaluator (§10)
has 'mechanical'
has 'skip the Evaluator'
# Tiered per-task Evaluator: complex -> most-capable tier, default non-mechanical -> standard tier (§10)
has 'complex'
has 'standard tier'
has 'opt-in'
# Worker containment: never commits / marks plan / picks next task; self-commit is detected and reset (§7/§8)
has 'Worker scope boundary'
has 'NEVER'
has 'self-commit'
has 'reset --soft'
has 'least powerful'
# Worker is steered to batch edits on multi-file mechanical rewrites
has 'batch'
# §16 must reference the harness-injected authoritative tier block (§16a)
has 'Resolved tier directives'
has '16a'
has 'LOOP_LEARNINGS'
# Learnings digest is capped and boot reads only the digest
has 'Patterns digest'
has '2KB'
# Non-interactivity (item #3): these tools must be explicitly forbidden, never invoked
has 'AskUserQuestion'
has 'EnterPlanMode'
# Must NOT instruct the tick to actually ask the user
hasnt 'ask the user'
hasnt 'ask the human'
# Loop-Status trailers the postmortem reads (done/skipped/halted distribution)
has 'Loop-Status: skipped'
has 'Loop-Status: halted'
has 'Loop-Status: reviewed'
has 'relevant_learnings'
# Scout also reads the persistent cross-run knowledge source (not just this run's LOOP_LEARNINGS)
has 'LOOP_KNOWLEDGE'
has 'KNOWLEDGE.md'
has 'cross-run'

# Plan 2 Task 3: §16a must carve out the Evaluator to §10's per-class logic
grep -qiE 'Evaluator.*(§10|class).*govern' "$F"; assert_true $? "§16a carve-out: Evaluator tier is §10 class-governed"

# Plan 3 Task 1: one-line plan rows + recon forbidden in the plan
grep -qiE 'one-line|single line|2-3 sentence|NEVER write.*recon|recon.*(only in|lives only).*sprint' "$F"; assert_true $? "§3 must constrain plan rows + forbid recon in the plan"
grep -qiE 'detail doc|docs/superpowers/plans|sprint contract' "$F"; assert_true $? "§3 must point verbose detail to detail doc / sprint contract"

# Plan 3 Task 2: §11 stamping must be in-place, no description rewrite
grep -qiE 'edit in place|in-place|flip the checkbox|do not (rewrite|duplicate|re-?append) the (task )?description' "$F"; assert_true $? "§11 must mandate in-place checkbox+SHA edit, no description rewrite"

# Plan 4 Task 2: §6 Scout promotes task-relevant invariants into contract verification
grep -qiE 'invariant' "$F"; assert_true $? "§6 must mention invariants"
grep -qiE 'invariant.*verification|append.*check.*verification|verification.*invariant' "$F"; assert_true $? "§6 puts invariant checks into contract verification"

# Plan 4 Task 3: §9 gate treats a failed invariant check as a gate failure
grep -qiE 'invariant check.*fail|a failed invariant|invariant.*non-zero|treat.*invariant.*as a failure' "$F"; assert_true $? "§9 must treat a failed invariant check as a gate failure"

# Plan 4 Task 4: §7 write-through-on-revert primitive
grep -qiE 'revert.*hook|hook.*revert|write-through|re-?grep.*after.*edit|verify.*edit.*persisted' "$F"; assert_true $? "§7 must define the write-through-on-revert primitive"

# Plan 4 Task 5: §10a forbids verify-only follow-ups for gated invariants
grep -qiE 'do not.*(verify-only|follow-up).*invariant|invariant.*already (gated|enforced)' "$F"; assert_true $? "§10a must forbid verify-only follow-ups for gated invariants"

# Plan 5 Task 2: §3 Planner detects clone-families
grep -qiE 'clone[- ]?famil|clone_of|near-verbatim clone' "$F"; assert_true $? "§3 must instruct the Planner to detect clone-families"
grep -qiE 'template task|parameteris|first.*template.*then.*clone' "$F"; assert_true $? "§3 must define the template-task-then-clones shape"

# Plan 5 Task 3: §6 lean clone reference contract
grep -qiE 'clone_of.*reference|reference the sibling|do not (re-?embed|inline).*sibling|substitution' "$F"; assert_true $? "§6 must define the lean clone reference contract"

assert_summary
