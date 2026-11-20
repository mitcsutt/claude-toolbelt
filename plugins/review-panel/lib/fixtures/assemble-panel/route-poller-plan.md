Panel (4 + filter):
- goal-auditor: core for a plan; full context
- grumpy-senior: core for a plan; PLAN.md only
- cost-hawk: a 60 s poll that calls a paid LLM; PLAN.md + src/llm.js
- contract-keeper: new config key that src/config.js validates; full repo
- noise-filter: final pass
Skipped:
- fact-checker: no claims about outside platforms in PLAN.md
- outsider: no build-vs-buy question
- threat-modeller: no credential, auth or outside input in PLAN.md
Claim owner: goal-auditor

    Panel running: 4 reviewers + filter. Expect about 25–40 min; I'll report once, when the triage is ready.
