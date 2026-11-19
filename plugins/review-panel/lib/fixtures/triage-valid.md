# Panel triage: src/app.js
Panel: alpha · SHIP WITH FIXES, beta · DO NOT SHIP, gamma · SHIP WITH FIXES
Overall: SHIP WITH FIXES: cap the retry loop.
Tier: T1 (Bash works) · read order beta, gamma, alpha (seed 7) · findings 5 · spot-checks 3 (holds 3 / wrong 0 / not checked 0)
## At a glance
- `src/app.js:41-46` retry loop never stops: cap it at 5.
- `src/app.js:44` the cap check reads the old counter.
## Fix before merge
### T1 Retry loop never stops  [cross-confirmed: alpha, beta]
- Sources: alpha F1, beta F1 (same cause: the while loop has no attempt counter)
- Where `src/app.js:41-46` / What no cap / Consequence a dead upstream pins a CPU
- Evidence (checked: holds): `src/app.js:42` reads `while (true) {`
- Fix: cap at 5 attempts.
- Severity: BLOCK (BLOCK, SHOULD → BLOCK: highest source)
### T2 Cap check reads the old counter
- Sources: new (spot-check of alpha F1)
- Where `src/app.js:44` / What compares `attempt` before incrementing it
- Evidence (checked: holds): `src/app.js:44` reads `if (attempt > max)`
- Fix: increment first.
- Severity: SHOULD (new → SHOULD: off by one, no outage)
## Decisions for you
## Worth doing
### W1 Test the retry cap
- Sources: gamma F1
- `test/app.test.js`: stub a failing upstream and assert 5 calls.
## Pre-existing
### P1 Timeout is NaN when unset
- Sources: beta F2
- `src/config.js:3`, unchanged in this diff: requests never time out.
## Dropped
| sources | finding | failed check: reason |
|---|---|---|
| alpha F2 | rename `x` | nit: no consequence |
## Coverage
none: every Fix item was spot-checked.
