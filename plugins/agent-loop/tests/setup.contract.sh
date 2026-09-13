#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
# shellcheck source=../../../tests/lib/assert.sh
source "$ROOT/tests/lib/assert.sh"
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
has 'permissions-advisor'
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

# Plan 6 (resilience) Task 4: wizard gains Dashboard + Medic questions; launch print explains
# the sidecar, the own-terminal rule, and /agent-loop attach; the LLM lock and the old
# version gate are gone.
has 'Dashboard'
grep -qE '^1?[0-9]\. \*\*Dashboard\*\*' "$F"; assert_true $? "setup wizard has a numbered Dashboard question"
has 'Medic'
grep -qE '^1?[0-9]\. \*\*Medic\*\*' "$F"; assert_true $? "setup wizard has a numbered Medic question"
has 'Medic model'
has 'own terminal'
has 'not as a background task'
has 'NEEDS_HUMAN.md'
has 'To attach:'
grep -qF -e 'runtime/LOCK' -e 'LOCK,' "$F"; assert_false $? "setup must NOT mention the removed runtime/LOCK"
hasnt '0.11.0'

# The /agent-loop attach skill: liveness from runtime files, Monitor armed, never starts the harness.
A="$HERE/../skills/agent-loop/SKILL.md"
ahas() { grep -qF -e "$1" "$A"; assert_true $? "attach skill must mention: $1"; }
ahas 'runtime/harness.json'
ahas 'HEARTBEAT'
ahas 'tick.json'
ahas 'runtime/dashboard.json'
ahas 'Never `pgrep`/`ps | grep run.sh` — those match your own tool calls.'
n_pgrep="$(grep -c 'pgrep' "$A")"; n_never="$(grep -c 'Never `pgrep`' "$A")"
[ "$n_pgrep" -eq "$n_never" ]; assert_true $? "attach skill: pgrep appears only in the 'Never' sentence ($n_pgrep vs $n_never)"
ahas 'Monitor'
ahas 'persistent: true'
ahas 'agent-loop <run-id> incidents'
ahas 'tail -n 0 -F "<loop-dir>/events.jsonl" | grep -E --line-buffered '"'"'"type":"(incident|loop_end|medic_end|memory_pressure)"'"'"''
ahas '/agent-loop-medic'
ahas 'PushNotification'
ahas '/agent-loop-postmortem'
ahas 'never run it'
grep -q '^disable-model-invocation: true' "$A"; assert_true $? "attach skill keeps disable-model-invocation: true"
grep -qF 'runtime/LOCK' "$A"; assert_false $? "attach skill must not mention runtime/LOCK"
# Schema stamp: attach reads it and previews the upgrade; the harness (not the skill) migrates.
ahas 'runtime/schema'
ahas 'LOOP_MIGRATE_FORCE'
ahas 'schema 1'
# Dashboard-first (spec §13): attach ensures ONE detached dashboard and it is the launcher;
# the read-only observer and the background-launch are gone; Monitor has a background-Bash fallback.
ahas '--detach'
ahas 'reused'
ahas '/api/resume'
ahas '/api/start'
ahas 'survives'
ahas 'adopt'
ahas 'AskUserQuestion'
ahas 'Recommended'
ahas 'Give me a terminal command'
grep -qF -- '--no-spawn' "$A"; assert_false $? "attach skill must not start a read-only observer"
grep -qF 'run_in_background: true' "$A"; assert_true $? "attach skill keeps run_in_background for the monitor fallback only"
grep -qF 'RAM stacks' "$A"; assert_false $? "attach skill drops the retracted RAM rationale"
# setup: dashboard-first launch print
has '/agent-loop'
has 'Start'
has '--detach'
grep -qF 'RAM adds to the session' "$F"; assert_false $? "setup drops the retracted RAM rationale"
grep -qF 'migrate' "$A"; assert_true $? "attach skill explains that run.sh migrates on the next launch"

# v3: Stop-now vs Pause, the decisions ledger, and harness.log instead of run.log.
ahas 'runtime/STOP'
ahas 'phase boundary'
ahas 'LOOP_DECISIONS.md'
ahas 'harness.log'
grep -qiE 'PAUSE.*(finish|end of).*(tick|phase)' "$A"; assert_true $? "attach skill contrasts PAUSE with STOP"
# NB the backticks: the skill writes it as `run.log`, so the un-backticked
# needle in plan D never matched and the assertion could not fail.
grep -qF 'run.log` is forensic' "$A"; assert_false $? "attach skill no longer points at run.log"
# Liveness is unchanged by v3 — run.sh still execs the runner, so the ps match holds.
ahas 'ps -o command= -p'

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

# Plan B Task 8: the wizard asks for a render recipe, writes the Render: block, and warns
# when the repo has UI files but the user declined one.
has 'render recipe'
grep -qE '^1?[0-9]\. \*\*Render recipe\*\*' "$F"; assert_true $? "setup wizard has a numbered Render recipe question"
has 'ui_globs'
has 'screenshot'
has '{route}'
has 'cypress'
has 'playwright'
grep -qiE 'no render recipe|without a recipe' "$F"; assert_true $? "setup warns when a UI repo has no recipe"
grep -qF 'artifacts/' "$F"; assert_true $? "setup gitignores the artifacts dir"

# The template ships the block commented out, with every sub-key documented.
grep -qE '^# Render:' "$TMPL"; assert_true $? "template ships a commented Render: block"
for k in start ready command ui_globs reference; do
  grep -qE "^#[[:space:]]+$k:" "$TMPL"; assert_true $? "template documents the Render sub-key $k"
done
grep -qE '^Render:' "$TMPL"; assert_false $? "template must not ship an ACTIVE Render: block"
# --- v3: tiers, decision policy, per-phase limits, new row tags ---
has 'Tiers:'
has 'cheap='
has 'most-capable='
has 'Decision policy'
has 'autonomous'
has 'conservative'
has 'LOOP_DECISIONS.md'
grep -qE '^1?[0-9]+\. \*\*Decision policy\*\*' "$F"; assert_true $? "setup wizard has a numbered Decision policy question"
grep -qE '^1?[0-9]+\. \*\*Tiers\*\*' "$F"; assert_true $? "setup wizard has a numbered Tiers question"
# The orchestrator-model EXPLANATION is gone: v3 has no LLM spine to explain.
# (Plan D's own blanket `orchestrator (model|...)` grep cannot work: the
# replacement paragraph has to name the key to say it is inert. These pin the
# thing that actually matters — the only surviving mention is the compat note.)
grep -qiE 'orchestrator \(the per-tick|coordination \+ verification spine' "$F"
assert_false $? "setup drops the v2 orchestrator-model rationale"
grep -qF '**No orchestrator.**' "$F"; assert_true $? "setup states plainly that v3 has no orchestrator"
grep -qiE 'orchestrator model.*(inert|ignored)' "$F"; assert_true $? "Orchestrator model: survives only as an inert 2.x compat key"
grep -qF 'tick-prompt' "$F"; assert_false $? "setup no longer points at tick-prompt.md"
# Per-phase budgets replace the single tick wall.
has 'worker_resume_max'
has 'per-phase'

# Template: tiers map, decision policy, the spec 4.2 limit defaults, no orchestrator prose.
grep -qE '^Tiers: cheap=haiku standard=sonnet most-capable=opus$' "$TMPL"; assert_true $? "template ships the default tier map"
grep -qE '^Decision policy: autonomous$' "$TMPL"; assert_true $? "template defaults to Decision policy: autonomous"
for k in tick_timeout=1800 scout_timeout=480 worker_timeout=1500 wrapup_timeout=300 eval_timeout=480 judge_timeout=360 planner_timeout=900 gate_cmd_timeout=600 worker_resume_max=1 worker_budget_usd=6 max_attempts=3; do
  grep -qF "$k" "$TMPL"; assert_true $? "template Limits carries $k"
done
grep -qiE '^# The orchestrator' "$TMPL"; assert_false $? "template drops the orchestrator-model paragraph"
grep -qE '^Orchestrator model:' "$TMPL"; assert_true $? "Orchestrator model: kept as an inert key so 2.x configs parse"
# Exactly one place in the plugin names a model, and it is the Tiers line.
# A bare alias only: `claude-sonnet-4-6` inside a full model id is an example of
# what the usage canonicalizer normalizes, not a model this plugin chooses, so a
# hyphen on either side disqualifies the match.
ALIAS_RE='(^|[^-[:alnum:]])(haiku|sonnet|opus)([^-[:alnum:]]|$)'
# Resolve the plugin root: `$HERE/..` leaves "/tests/.." in every path below it,
# which the /tests/ filter would then strip, silently matching nothing.
PLUGIN="$(cd "$HERE/.." && pwd)"
n_alias="$(grep -rlE "$ALIAS_RE" "$PLUGIN" --include='*.md' --include='*.py' --include='*.sh' --include='*.json' | grep -v '/tests/' | wc -l | tr -d ' ')"
[ "$n_alias" -eq 1 ]; assert_true $? "only templates/LOOP_CONFIG.md names a model alias (got $n_alias files)"

# Plan template: the four new row tags.
PLANT="$HERE/../templates/LOOP_PLAN.md"
for tag in 'no-ui' 'copy_of' 'blocked_by' 'split_of'; do
  grep -qF "$tag" "$PLANT"; assert_true $? "plan template documents the $tag row tag"
done

assert_summary
