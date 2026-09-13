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
# R3: `Decision policy:`, `Tiers:` and the Limits line are plan D's to write here.

assert_summary
