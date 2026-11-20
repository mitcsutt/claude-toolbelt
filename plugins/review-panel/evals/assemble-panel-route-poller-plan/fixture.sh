#!/usr/bin/env bash
# A plan (PLAN.md) for a background job that calls an existing paid LLM
# client every 60 s and adds a validated config key. Signals: polling and
# paid calls (cost-hawk), a changed config format (contract-keeper). No new
# credentials, no outside input, no claims about outside platforms.
set -euo pipefail

# --- case files -------------------------------------------------------------
mkdir -p src
cat > PLAN.md <<'EOF'
# Plan: digest-bot

## Goal

Post a short digest of new support tickets to the team channel, so nobody
has to scroll the ticket queue to see what came in. Done means a digest
appears within about a minute of new tickets arriving, and the existing
ticket workflow is unchanged.

## Design

A new background job, `digest-bot`, runs inside the existing worker
process. Every 60 seconds it:

1. Reads tickets created since its last run from the existing `tickets`
   table (the job stores its high-water mark in a new `digest_state` row).
2. If there are none, does nothing until the next tick.
3. Otherwise calls `summarise()` from `src/llm.js` once with all new
   tickets, and writes the result to the existing `digests` table, which
   the channel integration already posts from.

`summarise()` is the existing paid LLM client. It reuses the existing
client and key; no new credentials.

## Configuration

Add one key to `config.yaml`:

    digest:
      interval_seconds: 60

`src/config.js` validates the file at startup. It gains a rule that
`digest.interval_seconds` is an integer between 30 and 3600, defaulting to
60 when the key is absent.

## Rollout

1. Ship the job disabled (`interval_seconds: 0` means off).
2. Turn it on in staging for a week and read the digests.
3. Turn it on in production.

## Out of scope

- Changing how the channel integration posts.
- Summarising ticket replies, only new tickets.

## Tests

- Unit test: no new tickets means no `summarise()` call.
- Unit test: config validation accepts 60 and rejects 10 and "abc".
- Integration test: three tickets produce one digest row.

## Open questions

- Should a digest with more than 50 tickets be split?
EOF

cat > src/llm.js <<'EOF'
'use strict';
// Paid LLM client, shared by every feature that summarises text.
const client = require('./vendor-client');

async function summarise(texts) {
  return client.complete({ prompt: texts.join('\n---\n'), maxTokens: 400 });
}

module.exports = { summarise };
EOF

cat > src/config.js <<'EOF'
'use strict';
// Loads and validates config.yaml once at startup.
const fs = require('fs');
const yaml = require('yaml');

const config = yaml.parse(fs.readFileSync('config.yaml', 'utf8'));
if (!config.db || !config.db.url) throw new Error('config: db.url is required');

module.exports = config;
EOF

cat > config.yaml <<'EOF'
db:
  url: postgres://localhost/support
channel:
  name: support-team
EOF
