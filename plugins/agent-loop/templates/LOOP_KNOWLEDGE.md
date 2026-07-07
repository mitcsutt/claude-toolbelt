# Loop Knowledge (persistent, cross-run; repo-scoped; committed)

Durable, generalized patterns accumulated across all loop runs in this repo. NOT per-run and
NOT bulk-seeded into a run — the Scout reads task-relevant entries from here per task, and
`/agent-loop-postmortem` auto-promotes durable learnings here at loop close (slice-specific
ones are dropped). Created once and preserved across runs.

## Patterns (durable, cross-run; auto-promoted at loop close)

## Invariants (machine-checkable, cross-run; enforced by the §9 gate)

An invariant is a convention with an executable check. Unlike a Pattern (prose
guidance a Worker may ignore), an invariant is ENFORCED: the Scout copies any
task-relevant invariant into the sprint contract's `verification`, and the
parent-side §9 gate runs the check — a violation fails the tick. Format:

- <rule, one line> | check: <shell command that exits NON-ZERO when violated, scoped to the touched files>

Example:
- Test `describe` blocks use PascalCase component names | check: `! grep -rEn "describe\('[a-z]" <changed test files>`
