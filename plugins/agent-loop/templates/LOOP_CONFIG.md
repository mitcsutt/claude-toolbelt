# Loop Config
Started: <ISO8601>
Goal: <one sentence>
Loop type: refactor | new-feature | test-sweep | custom
Granularity: single | segmented
TDD mode: none | tdd-per-task
Verification pipeline: lint tsc build test
Limits: tick_timeout=1200
Blocker policy: continue-independent | halt
Branch: agent-loop-<topic>
Worktree: <absolute path — the harness refuses to run elsewhere>
Segment count: <N or 1>
Spec: docs/superpowers/specs/<file>.md
Plan: docs/superpowers/plans/<file>.md

## Model tiers (optional). Blank = let each role pick the least-powerful capable model.
# The orchestrator (the per-tick process) is a coordination + verification spine: it
# reads state, runs the verification pipeline, and dispatches the thinking-heavy roles
# below as subagents that choose their own tier. A standard tier is plenty for the
# spine — the heavy reasoning is delegated — so it now defaults to a standard model
# (Sonnet) to keep every tick cheap. Blank `Orchestrator model:` to inherit your Claude
# default instead, or name any model alias to override. No model name is hardcoded in
# the plugin; the alias you put here is the only place a concrete model is named.
Orchestrator model: sonnet
# Per-role tier preferences honoured by the tick (see tick-prompt §16). Tiers, not
# model names: cheap | standard | most-capable. Blank/auto lets the role pick per task.
Planner tier: most-capable
Scout tier: cheap
Worker tier: standard
# Evaluator tier is CLASS-GOVERNED by tick-prompt §10 (mechanical→skip,
# complex→most-capable, default→standard). Leave blank to use that split.
# If you set a tier here it becomes the ceiling for `| complex` Evaluator
# work only — it does NOT force most-capable onto default/mechanical tasks.
Evaluator tier:
