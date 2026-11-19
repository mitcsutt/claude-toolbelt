# alpha review: src/app.js
Verdict: SHIP WITH FIXES: the retry loop never stops.
## Findings
### F1 Retry loop has no cap
- Where: `src/app.js:41-43`
- What: The loop retries forever.
- Consequence: A dead upstream pins a CPU.
- Evidence: `src/app.js:42` reads `while (true) {`.
- Fix: Cap at 5 attempts.
- Severity: BLOCK
- Confidence: high
### F2 Variable name
- Where: `./src/app.js:90`
- What: `x` is a poor name.
- Consequence: none at runtime.
- Evidence: read the line.
- Fix: Rename to `attempts`.
- Severity: COULD
- Confidence: high
## Checked and fine
the config loader
