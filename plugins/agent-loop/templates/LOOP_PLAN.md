# Loop Plan

Legend: [ ] pending · [~] in-progress · [x] done(+SHA) · [!] blocked · [-] skipped · [blocked-upstream]
Task line format (HARD, ONE line): `- [ ] T<n>: <one-sentence imperative> | depends_on: T<a>,T<b> | model: <alias?> | <class?>`.
Verbose detail goes in the detail doc / sprint contract — NEVER inline here, NEVER a `SCOUT done:` block.
Class flag governs the per-task Evaluator phase:
- `| mechanical` — ONLY when success is fully grep+pipeline verifiable (no judgement) AND the success criteria name the proving grep. Skips the per-tick Evaluator.
- `| complex` — high subjective risk (architecture, tricky logic, security/high-blast-radius). Per-tick Evaluator runs at the most-capable tier.
- no flag — ordinary non-mechanical work. Per-tick Evaluator runs at the standard tier. Default to this when unsure; `| complex` is opt-in. `mechanical` and `complex` are mutually exclusive.

Clone-family rows: a task that is a near-verbatim clone of an earlier one carries
`| clone_of: T<n>` and names its substitutions in the one-line description, e.g.
`- [ ] T<n>: Clone T<m> (<SourceName>) as <TargetName> — substitute {type, ticket, display name} | depends_on: T<m> | clone_of: T<m>`.
A `clone_of` task's Scout writes a LEAN contract (references the sibling file, lists only
substitutions) instead of re-embedding the source.

Verification tags (read by the harness when it validates the Scout's contract):
- `| no-ui` — this task cannot change what the product looks like, so no render
  gate is required even though its files match the repo's UI globs. Opt-out
  only: without it, a task whose `allow_list` touches a UI path MUST carry a
  `render_gate` and the tick fails if the screenshots are not produced.
- `| copy_of: T<n>` — this task copies/ports/replicates T<n>'s artefact. Forces
  a `fidelity_source` pair in the contract: the harness computes a normalized
  line-similarity ratio between source and target and hard-fails below
  `min_similarity`. A "copy" that produces a six-line comment does not pass.
- `| blocked_by: T<a>,T<b>` — a semantic block the Judge discovered, distinct
  from `depends_on:` (which is planned order). SELECT treats the named tasks as
  blocked-upstream until this one closes.
- `| split_of: T<n>` — this row was inserted by `Plan.split` when a Worker
  overran its resumes; T<n> is the parent task it was split from. Sub-task ids
  are the next free numeric ids (`T74`, `T75`, …), never a lettered suffix like
  `T60a` — `serve.py`'s id regex `\bT\d+\b` does not match one.

## Segment A: <name>
- [ ] T1: ...
- [ ] T2: ... | depends_on: T1

## Segment B: <name>   (unplanned — filled by a PLAN tick)
