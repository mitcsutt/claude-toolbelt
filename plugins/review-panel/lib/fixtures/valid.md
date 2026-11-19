# goal-auditor review: plans/rollout.md
Verdict: SHIP WITH FIXES: one test passes with or without the fix.
Tier: T1 (default)
## Goal scorecard
outcome · met · evidence
## Findings
### F1 Test passes without the fix
- Where: `src/retry.ts:42`
- What: The new test asserts only that no exception is thrown.
- Consequence: Reverting the fix leaves the suite green.
- Evidence: ran `git stash; npm test` and got
  14 passing
```
## not a heading: inside a code fence
### F9 not a finding
```
- Fix: Assert the retry count.
- Severity: SHOULD
- Confidence: high
### F2 Rollback not described
- Where: plan section "Rollout"
- What: No rollback step.
- Consequence: A bad deploy stays up until someone writes one.
- Evidence: read plans/rollout.md end to end.
- Fix: Add the revert command.
- Severity: COULD
- Confidence: medium
## Coverage
none
## Checked and fine
the migration order
