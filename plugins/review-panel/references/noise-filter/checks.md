# noise-filter checks

Verified on 2026-11-19; re-check live before a decision depends on a volatile fact (platform behaviour, tool versions).

How to judge each finding, where it goes, and what not to do. Evidence IDs resolve in `evidence.md` (NF) and `${CLAUDE_PLUGIN_ROOT}/references/shared/evidence.md` (E).

## The six checks

Answer yes, no or n/a for every finding, one finding at a time, against its evidence. Don't write the answers into kept items; a Dropped row names the check that failed.

1. **Evidence holds.** The cited line, output or source says what the finding claims. "No" needs a quote of what is actually there (E32). If the line is wrong but the flaw is real elsewhere, answer yes and re-locate it (NF10). No evidence at all → "no (unsupported)".
2. **In goal.** It is about how well the work reaches the fixed goal. A finding that argues against the goal itself is a "no".
3. **Caused by this change.** "No" when the flaw reads the same before the change; cite the unchanged line (NF9).
4. **Consequence.** The Consequence field names something a user, operator or maintainer would feel. Read the field, not the label: a "docs" or "nit" label can hide a code bug, so for a doc-versus-code mismatch, check which side is wrong (NF8).
5. **Specific.** It names a location and a concrete fix, or the specific missing test or doc line.
6. **Self-solving.** Asked only of COULD/SHOULD items with no correctness, security or data consequence: would a capable implementer handle it unprompted? No evidence exists either way, so keep the question narrow (R9 §3 #10).

## Placement

Apply in order; the first rule that fires decides.

1. Evidence fails → Dropped, `evidence: <quote of what is actually there>`; no evidence at all → Dropped, `unsupported: <what is missing>`.
2. Not in goal → Decisions if it is a real trade-off the author must call, otherwise Dropped, `out of goal: <clause>`.
3. Not caused by the change but real → Pre-existing.
4. No consequence → Dropped, `nit: <clause>`.
5. Not specific → Dropped, `unspecific: <clause>` (covers "rebase", "CI is red", "add more tests").
6. Self-solving → Dropped, `self-solving: <clause>`.
7. Otherwise kept: BLOCK → Fix; reviewers disagree on what to do → Decisions; SHOULD/COULD → Worth doing.

The drop reasons are exactly `evidence`, `unsupported`, `out of goal`, `nit`, `unspecific` and `self-solving`, each followed by `:` in the Dropped table's third cell. Nothing else counts as a reason, and `check-triage` rejects any other. P2's "absorbed" was really a merge (NF13): a finding absorbed into another goes on that item's Sources line with a same-cause clause.

Several findings dropped for the same reason and the same pattern (one `let` per line, say) go in one Dropped row that lists every key (NF7). Dropped rows stay in the triage with their reasons (NF6).

## Merging

Merge only after the six checks, so the number of reviewers behind an item can't sway them (E39). `merges` proposes groups at the same path within 5 lines (claude-security's window, NF11); every group is only a candidate, because location alone is wrong most of the time (E40). Merge when the underlying cause is the same, and write `(same cause: <clause>)` on the Sources line. A doc that is the symptom of a code bug merges with the code finding across files, with the same clause (NF8). Same place, different cause: keep them apart, even when one of them is dropped.

## Ranking and agreement

Within a section, BLOCK first, then items with more distinct reviewers. Tag `[cross-confirmed: <reviewers>]` exactly when two or more distinct reviewers are among the Sources. Agreement orders the list; it never keeps or drops anything (E37). A single-reviewer finding is the normal case: in P2, 6 of the 31 Fix and Worth-doing items had a single source (E41).

## Re-grade

1. Start from the highest severity among the item's sources, as claude-security's merge does (NF11).
2. Lower it without new evidence only by naming the `review-method.md` severity anchor it fails.
3. Raise it only when a spot-check exposed a consequence no source stated, and quote that consequence.

Don't take the median of the sources. claude-security's median caps 3 verifiers, each with a different refutation lens, re-judging the same code path; panel sources are different lenses that saw different consequences. In P2, the phase-gate item came from six findings by five reviewers, four graded COULD and two SHOULD; the filter's own re-run showed the gate could never pass, and it went to BLOCK. A median would have left it at COULD (NF13). Never drop or rank on a model-assigned severity or score (E38). Write each change as `Severity: <final> (<as reported> → <final>: <clause>)`.

## Conflicts

When one reviewer says cut it and another says it's load-bearing, it goes to Decisions with both sides and who argued each. Never smooth it over.

## Checks not to make

1. Don't drop a finding because only one reviewer raised it. (E37)
2. Don't drop or downgrade a finding because of the reviewer's low Confidence or hedged wording. (E39)
3. Don't filter, rank or cut on a model-assigned severity or usefulness score. (E38)
4. Don't take a "nit", "docs" or "stale" label at face value. Read the Consequence field, and for a doc-versus-code mismatch check which side is wrong. (NF8)
5. Don't refute with a defence you didn't open and read. (E32)
6. Don't drop a finding because its line number is wrong. Re-locate it. (NF10)
7. Don't merge on location alone, and don't let a wrongly worded duplicate turn a real finding into "cross-confirmed". (E40)
8. Don't treat agreement between Claude personas as proof. It orders the list; the evidence check decides. (E39, E37)
9. Don't drop real pre-existing or out-of-goal problems silently. Pre-existing gets its own section; out-of-goal trade-offs go to Decisions. (NF9)
10. Don't apply "self-solving" to a correctness, security or data finding. (doctrine, no evidence; R9 §3 #10)
