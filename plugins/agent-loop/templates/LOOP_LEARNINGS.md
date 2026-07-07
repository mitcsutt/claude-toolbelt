# Loop Learnings

## Patterns (curated — read this first; keep concise; HARD cap ≈ 2KB / ~20 rules, prune when exceeded)
<general reusable facts about this codebase discovered during the loop>

## Invariants (machine-checkable, cross-run; enforced by the §9 gate)

An invariant is a convention with an executable check. Unlike a Pattern (prose
guidance a Worker may ignore), an invariant is ENFORCED: the Scout copies any
task-relevant invariant into the sprint contract's `verification`, and the
parent-side §9 gate runs the check — a violation fails the tick. Format:

- <rule, one line> | check: <shell command that exits NON-ZERO when violated, scoped to the touched files>

Example:
- Test `describe` blocks use PascalCase component names | check: `! grep -rEn "describe\('[a-z]" <changed test files>`

## Log (append-only, one block per task)
