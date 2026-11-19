# beta review: src/app.js
Verdict: DO NOT SHIP: retries never end.
## Findings
### F1 Unbounded retry
- Where: `src/app.js:46`
- What: No retry limit.
- Consequence: Requests hang forever when the API is down.
- Evidence: no counter anywhere in `src/app.js`.
- Fix: Add a max-attempts check.
- Severity: SHOULD
- Confidence: medium
### F2 Timeout parsed without radix
- Where: `src/config.js:3`
- What: `parseInt(process.env.TIMEOUT)` is unchanged by this diff and yields NaN when unset.
- Consequence: The timeout is NaN, so requests never time out.
- Evidence: `git show HEAD~1:src/config.js` has the same line.
- Fix: Default to 30000.
- Severity: SHOULD
- Confidence: medium
## Checked and fine
nothing else
