#!/usr/bin/env bash
# A decision (DECISION.md): build an in-house feature-flag service or adopt
# an existing one; two options, no decision yet. The user asks for a grumpy
# senior dev, so the panel is the decision core (outsider, grumpy-senior)
# with no ad-hoc persona.
set -euo pipefail

# --- case files -------------------------------------------------------------
cat > DECISION.md <<'EOF'
# Decision: feature flags

## Problem

We ship features behind `if (process.env.NEW_CHECKOUT)` checks. Turning a
feature on or off means a redeploy, nobody knows which flags are live, and
three dead flags are still in the code. We want to flip features at
runtime, per environment and for a percentage of users.

## Option A: build our own flag service

- A `feature_flags` table in our existing Postgres: name, environment,
  enabled, rollout percentage, updated_by.
- A small admin page to edit rows.
- A client SDK in our Node services that polls the table every 30 seconds
  and caches the flags in memory; `flags.isOn('new-checkout', user)`
  hashes the user id for percentage rollouts.
- Estimate: two engineers for three weeks, then ongoing upkeep.

## Option B: adopt an existing feature-flag product

- Use an existing hosted or open-source flag service with a Node SDK.
- Percentage rollouts, audit log and admin UI come with it.
- Estimate: one engineer for one week to wire up and migrate the env-var
  flags.
- Costs a subscription, or hosting if self-hosted, and adds a vendor.

## Constraints

- Flags must still work if the flag service is down (fall back to a
  default per flag).
- No user data leaves our network without a review.
- Fewer than 20 flags expected in the first year.

## Not decided

Which option. This document is for the review.
EOF
