# Loop Plan

Legend: [ ] pending · [~] in-progress · [x] done(+SHA) · [!] blocked · [-] skipped · [blocked-upstream]
Task line format (HARD, ONE line): `- [ ] T<n>: <one-sentence imperative> | depends_on: T<a>,T<b> | model: <alias?> | <class?>`.
Verbose detail goes in the detail doc / sprint contract — NEVER inline here, NEVER a `SCOUT done:` block.
Class flag governs the per-tick Evaluator (§10):
- `| mechanical` — ONLY when success is fully grep+pipeline verifiable (no judgement) AND the success criteria name the proving grep. Skips the per-tick Evaluator.
- `| complex` — high subjective risk (architecture, tricky logic, security/high-blast-radius). Per-tick Evaluator runs at the most-capable tier.
- no flag — ordinary non-mechanical work. Per-tick Evaluator runs at the standard tier. Default to this when unsure; `| complex` is opt-in. `mechanical` and `complex` are mutually exclusive.

Clone-family rows: a task that is a near-verbatim clone of an earlier one carries
`| clone_of: T<n>` and names its substitutions in the one-line description, e.g.
`- [ ] T<n>: Clone T<m> (<SourceName>) as <TargetName> — substitute {type, ticket, display name} | depends_on: T<m> | clone_of: T<m>`.
A `clone_of` task's Scout writes a LEAN contract (references the sibling file, lists only
substitutions) instead of re-embedding the source (§6).

## Segment A: <name>
- [ ] T1: ...
- [ ] T2: ... | depends_on: T1

## Segment B: <name>   (unplanned — filled by a PLAN tick)
