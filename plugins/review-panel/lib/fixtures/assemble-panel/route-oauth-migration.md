Panel (5 + filter):
1. `goal-auditor`: core for a PR; full repo
2. `paranoid-sre`: core for a PR; migration order and rollback
3. `threat-modeller`: OAuth callback, session cookie, client secret
4. `contract-keeper`: drops users.password_reset_token
5. `fact-checker`: GitHub OAuth API behaviour
- `noise-filter`: final pass
Skipped: cost-hawk (no steady-state work), dx-advocate (nothing new to set up)
Claim owner: fact-checker

Panel running: 5 reviewers + filter. Expect about 25–40 min; I'll report once, when the triage is ready.
