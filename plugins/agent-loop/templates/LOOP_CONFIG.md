# Loop Config
Started: <ISO8601>
Goal: <one sentence>
Loop type: refactor | new-feature | test-sweep | custom
Granularity: single | segmented
TDD mode: none | tdd-per-task
Verification pipeline: lint tsc build test
Limits: tick_timeout=1800 scout_timeout=480 worker_timeout=1500 wrapup_timeout=300 eval_timeout=480 judge_timeout=360 planner_timeout=900 gate_cmd_timeout=600 worker_resume_max=1 worker_budget_usd=6 max_attempts=3
Blocker policy: continue-independent | halt
Branch: agent-loop-<topic>
Worktree: <absolute path — the harness refuses to run elsewhere>
Segment count: <N or 1>
Spec: docs/superpowers/specs/<file>.md
Plan: docs/superpowers/plans/<file>.md
# auto = run.sh spawns the dashboard as a supervised sidecar and prints its URL; off = don't.
Dashboard: auto
# auto = a budgeted headless medic tick triages incidents; notify = desktop notification only;
# off = neither (an error-severity incident then stops the loop for a human).
Medic: auto
# Model alias for the medic tick. Blank inherits your Claude default.
Medic model:

## Limits — one line, space-separated key=value, seconds unless noted
# Every phase has its own wall, so one slow phase no longer costs the whole tick.
# Defaults, all overridable above:
#   tick_timeout=1800        outer sanity cap for one tick; the figure the dashboard displays
#   scout_timeout=480        contract writing
#   worker_timeout=1500      the edit phase
#   wrapup_timeout=300       the out-of-time continuation that updates worker-result.json
#   eval_timeout=480         verdict
#   judge_timeout=360        failure-path decision
#   planner_timeout=900      PLAN tick / task split
#   reviewer_timeout=900     segment review
#   learner_timeout=180      learnings write-back
#   gate_cmd_timeout=600     per verification/render/fidelity command
#   worker_resume_max=1      resume attempts before the Judge chooses escalate vs split
#   worker_budget_usd=6      per-Worker-phase spend cap
#   max_attempts=3           hard cap on attempts for one task, resume or escalation included
# These are provisional, derived from one run's phase durations (spec 4.2) — after a
# segment, run `python3 -m runner.calibrate <run-dir>` and paste its suggested Limits:
# line here.

## Model tiers — THE ONLY PLACE THIS PLUGIN NAMES A MODEL
# Phases ask for a tier; this line resolves the tier to an alias. Swapping a
# model is a one-word edit here and nothing else changes.
Tiers: cheap=haiku standard=sonnet most-capable=opus
# Per-role tier preference. Blank = the phase's default from the runner
# (scout/worker/evaluator standard, judge/planner/reviewer most-capable,
# learner cheap). A Worker's second attempt is escalated one tier by the
# harness regardless of what is written here.
Planner tier: most-capable
Scout tier: standard
Worker tier: standard
# Evaluator tier is CLASS-GOVERNED: `| mechanical` skips it, `| complex` runs it
# at most-capable, anything else at standard. A value here is a ceiling for
# `| complex` reviews only — it does NOT force a tier onto default tasks.
Evaluator tier:

## Decision policy — what the Judge may settle without waking you
# autonomous (default): the Judge may widen constraints the Scout invented,
#   escalate a tier, split an oversized task, and choose a default for a
#   question the spec is silent on. Every such choice is appended to
#   LOOP_DECISIONS.md with its alternatives, so a human can reverse it later.
#   It still defers when the spec forbids the change, when the change touches a
#   forbidden path sourced from the plan or spec, and when two Judge decisions
#   on the same task have already failed.
# conservative: widen/escalate/split allowed; spec-silent defaults are deferred
#   to LOOP_CLEANUP.md for a human, as 2.x did.
Decision policy: autonomous

# Ignored by v3 — kept so a config written by 2.x still parses.
Orchestrator model:

## Render recipe (optional). Uncomment and fill to turn on the render gate.
# Without it nothing in the loop ever looks at the product: lint, tsc, build and jsdom
# all pass on a page that renders a blank screen. `ui_globs` is what makes the gate
# mandatory — a task touching those paths must carry a render_gate unless its plan row
# is tagged `| no-ui`. `{route}` and `{screenshot}` are substituted per task by the Scout.
# Render:
#   start: pnpm --filter @repo/webapp dev --port 5273
#   ready: http://127.0.0.1:5273/
#   command: pnpm --filter @repo/integration cypress run --spec {route}
#   ui_globs: apps/*/src/**/*.tsx packages/ui/**/*.tsx
#   reference: app-customers=docs/reference/app-customers.png
