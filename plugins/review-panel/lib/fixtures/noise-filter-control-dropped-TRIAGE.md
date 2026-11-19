# Panel triage: CHANGE.diff
Panel: grumpy-senior · DO NOT SHIP, dx-advocate · SHIP WITH FIXES, goal-auditor · SHIP WITH FIXES, paranoid-sre · DO NOT SHIP, threat-modeller · DO NOT SHIP
Overall: DO NOT SHIP: refill is unclamped.
Tier: T1 (Bash works) · read order paranoid-sre, threat-modeller, dx-advocate, grumpy-senior, goal-auditor (seed 11) · findings 15 · spot-checks 7 (holds 4 / wrong 3 / not checked 0)
## At a glance
- `src/limiter.js:39` refill is no longer clamped to capacity: restore `Math.min`.
- Decide: keep in-memory buckets or move to a shared store.
## Fix before merge
### T1 Refill no longer clamped to capacity  [cross-confirmed: dx-advocate, goal-auditor, paranoid-sre]
- Sources: paranoid-sre F1, goal-auditor F1, dx-advocate F1 (same cause: the change removed the Math.min clamp on refill; the README sentence is right and the code is wrong)
- Where `src/limiter.js:38-39` / What tokens grow without bound while idle / Consequence an idle key bursts far past capacity
- Evidence (checked: holds): CHANGE.diff removes `-    b.tokens = Math.min(this.capacity, b.tokens + refill);`
- Fix: restore the clamp; README "Burst is capped at capacity." stays as it is.
- Severity: BLOCK (SHOULD, BLOCK, COULD → BLOCK: highest source; the goal forbids weakening the limiter)
## Decisions for you
### D1 In-memory buckets or a shared store
- Sources: grumpy-senior F1
- Options: keep the per-process Map (the goal) · move buckets to Redis (grumpy-senior)
## Worth doing
### W1 Test reset
- Sources: goal-auditor F3
- `test/limiter.test.js`: drain a key, reset it, assert `take()` succeeds. BLOCK → SHOULD: a missing test can ship and be added after.
## Pre-existing
### P1 Unset RATE turns the limiter off
- Sources: paranoid-sre F2
- `src/config.js:4` `parseInt(undefined)` is NaN and `NaN < 1` is false; CHANGE.diff never touches this line.
## Dropped
| sources | finding | failed check: reason |
|---|---|---|
| grumpy-senior F2 | admin route has no auth | evidence: `src/server.js:12` checks `x-admin` against `config.adminToken`; the real hole is T2, a different cause |
| paranoid-sre F3 | parseInt("08") is 0 | evidence: `node -e 'console.log(parseInt("08"))'` prints 8 |
| goal-auditor F2 | Date.now() read twice | evidence: `src/limiter.js:53` is a comment; the file calls `Date.now()` once, at line 35 |
| dx-advocate F2, dx-advocate F3, dx-advocate F4 | `let` never reassigned | nit: no behaviour change |
| grumpy-senior F3 | rename `rate` | nit: the unit is in the comment on the same line |
| paranoid-sre F4 | rebase; CI is red | unspecific: no failing check named, no evidence |
| threat-modeller F1 | empty admin token | unsupported: hedged, low confidence |
## Coverage
Every Fix item and every evidence drop was spot-checked.
