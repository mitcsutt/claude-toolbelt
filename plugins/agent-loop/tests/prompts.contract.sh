#!/usr/bin/env bash
# Prompt contracts for the v3 phase runner.
#
# These are role briefs, not a spine: each prompt states its JSON output shape
# verbatim (the harness parses the last fenced json block), forbids the
# interactive tools a headless phase cannot use, and carries the invariants for
# its own role. The two universal rules are what keep the 41 KB orchestrator
# prompt from growing back: every phase returns JSON, and no <<LOOP_*>> sentinel
# exists any more — the harness decides continue/done/halt, not a model.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
# shellcheck source=../../../tests/lib/assert.sh
source "$ROOT/tests/lib/assert.sh"
D="$HERE/../runner/prompts"

[ -d "$D" ]; assert_true $? "runner/prompts/ exists"
if [ ! -d "$D" ]; then assert_summary; exit 1; fi

# has FILE STRING — the file must contain STRING literally.
has() { grep -qF -e "$2" "$D/$1"; assert_true $? "$1 must contain: $2"; }
# hasnt FILE STRING
hasnt() { grep -qF -e "$2" "$D/$1"; assert_false $? "$1 must NOT contain: $2"; }
# hasre FILE REGEX MSG
hasre() { grep -qiE "$2" "$D/$1"; assert_true $? "$1: $3"; }

# Every role the runner dispatches has a brief.
for f in scout worker worker_wrapup worker_resume evaluator judge planner reviewer learner; do
  [ -f "$D/$f.md" ]; assert_true $? "runner/prompts/$f.md exists"
done

# --- universal rules -------------------------------------------------------
# This suite does not enforce a closed set of {{placeholder}} names: plan B adds
# {{render_recipe}} to scout.md and plan C adds judge-only placeholders to
# judge.md (interfaces doc "Cross-plan notes"), neither of which is in the
# interfaces doc's base placeholder list. Both are expected and allowed; only
# the per-file assertions below (e.g. the render_recipe check under scout)
# require any specific placeholder to be present.
for p in "$D"/*.md; do
  n="$(basename "$p")"
  # A phase returns JSON in one fenced block; the harness parses the last one.
  grep -qF '```json' "$p"; assert_true $? "$n states its output as a fenced json block"
  # Non-interactivity: a headless phase cannot ask anyone anything.
  grep -qF 'AskUserQuestion' "$p"; assert_true $? "$n forbids AskUserQuestion by name"
  grep -qF 'EnterPlanMode' "$p"; assert_true $? "$n forbids EnterPlanMode by name"
  # The shipped wording is 'never invoke any tool that waits on a person — it
  # will hang this process forever', which is better than 'never ask': it says
  # what goes wrong, not just what is banned.
  grep -qiE 'never (ask|prompt)|no (interactive|clarif)|cannot ask|waits on a person|nobody is there' "$p"
  assert_true $? "$n carries the non-interactivity sentence"
  # Sentinel-free control: the harness decides, not the model.
  grep -qF '<<LOOP_' "$p"; assert_false $? "$n contains no <<LOOP_*>> sentinel"
  # No model name anywhere in the plugin except the Tiers: config line. A bare
  # alias only — a full model id like claude-sonnet-4-6 is documentation, not a
  # choice — which is the same rule setup.contract.sh enforces plugin-wide.
  grep -qE '(^|[^-[:alnum:]])(haiku|sonnet|opus)([^-[:alnum:]]|$)' "$p"
  assert_false $? "$n names no model"
done

# --- scout -----------------------------------------------------------------
has scout.md '"contract_path"'
has scout.md '"notes"'
has scout.md 'sprint-'
has scout.md 'allow_list'
has scout.md 'forbidden'
has scout.md '"source"'
# scout.md puts it as "Say which it is — a constraint with no provenance cannot
# be reasoned about", which states the consequence as well as the rule.
hasre scout.md 'every *(entry|item) *in *`?forbidden|forbidden.*carr(y|ies).*source|no provenance' \
  'every forbidden entry must carry a source'
hasre scout.md 'plan|spec' 'source values distinguish plan/spec from scout'
has scout.md 'evaluator_must_read'
has scout.md 'evaluator_must_view'
has scout.md 'render_gate'
has scout.md 'fidelity_source'
# plan B's addition beyond the interfaces doc's base placeholder list.
has scout.md '{{render_recipe}}'

# --- worker ----------------------------------------------------------------
has worker.md 'worker-result.json'
has worker.md '"status"'
has worker.md 'complete'
has worker.md 'partial'
has worker.md '"summary"'
has worker.md 'allow_list'
# Containment: the harness commits and the harness owns the plan.
hasre worker.md 'never (run )?`?git commit|do not commit|never commit' \
  'the Worker never commits'
hasre worker.md 'never.*LOOP_PLAN|do not (edit|touch).*LOOP_PLAN' \
  'the Worker never edits LOOP_PLAN.md'
hasre worker.md 'checkpoint' 'the Worker keeps a checkpoint current'
# The Judge picks the tier of every re-attempt from the checkpoint (spec 4.2,
# 11 item 4) — the Worker is never told about tiers or asked to escalate itself.
grep -qiE '\btiers?\b|escalat' "$D/worker.md"
assert_false $? "worker.md does not mention tiers or escalation"

# --- worker wrap-up / resume ----------------------------------------------
has worker_wrapup.md 'worker-result.json'
hasre worker_wrapup.md 'out of time|no new work|do not start' \
  'wrap-up forbids starting new work'
has worker_resume.md 'checkpoint'
has worker_resume.md '{{minutes_left}}'

# --- evaluator -------------------------------------------------------------
has evaluator.md '"verdict"'
has evaluator.md 'PASS'
has evaluator.md 'NEEDS_WORK'
has evaluator.md 'BLOCKER'
has evaluator.md '"findings"'
has evaluator.md '"criterion"'
has evaluator.md '"evidence"'
has evaluator.md '"views"'
has evaluator.md '"observation"'
hasre evaluator.md 'one (entry|observation).*(per|for each).*(screenshot|must_view)|each.*evaluator_must_view' \
  'one views entry per screenshot is mandatory'
hasre evaluator.md 'read|open' 'the evaluator is told to actually read its must-read files'

# --- judge -----------------------------------------------------------------
# judge.md carries placeholders of its own beyond the interfaces doc's base
# list (plan C); this suite does not enumerate them, so they are allowed by
# default — see the "universal rules" comment above.
has judge.md '"decision"'
for d in retry escalate widen resume split defer halt; do
  grep -qF "$d" "$D/judge.md"; assert_true $? "judge.md lists the $d decision"
done
has judge.md '"classification"'
for c in self-imposed spec-answered capability open; do
  grep -qF "$c" "$D/judge.md"; assert_true $? "judge.md lists the $c classification"
done
has judge.md '"rationale"'
has judge.md 'allow_list_add'
has judge.md 'forbidden_remove'
has judge.md 'LOOP_DECISIONS.md'
hasre judge.md 'scout-sourced|source.*scout' 'only scout-sourced constraints may be widened'

# --- planner / reviewer / learner -----------------------------------------
has planner.md '"tasks_added"'
has planner.md 'LOOP_PLAN.md'
has reviewer.md '"findings"'
has reviewer.md '"severity"'
for s in nit should-fix must-fix; do
  grep -qF "$s" "$D/reviewer.md"; assert_true $? "reviewer.md lists the $s severity"
done
has reviewer.md 'follow_up_row'
has learner.md '"patterns"'
has learner.md '"invariants"'
has learner.md '"log"'
has learner.md 'evidence'
hasre learner.md 'gate-|gate output' 'a pattern must cite a gate output file'

assert_summary
