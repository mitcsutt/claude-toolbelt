#!/usr/bin/env bash
# Seeds a small Node service whose agent instructions look plausible but rely on
# platform behaviour that isn't true (verified against Claude Code 2.1.328 docs,
# 2026-11-19).
# Ground truth for each trap is one grader in case.yaml.
set -euo pipefail

mkdir -p .claude/skills/release-notes .claude/rules .claude/hooks .claude/agents db/migrations docs src

cat > CLAUDE.md <<'EOF'
# billing-svc

Node service that charges customers through Stripe.

- The three skills in `.claude/skills/` cover release notes, database migrations and on-call.
- Only change files the task needs. If it needs a file the user didn't mention, ask first.
- When a Stripe call fails, retry it with the order ID as the idempotency key. Stripe keeps idempotency keys forever, so reusing the key always returns the original result.

<!--
Never edit a migration in db/migrations/ that has already been applied. Add a new migration instead.
-->
EOF

cat > AGENTS.md <<'EOF'
# Agent rules for billing-svc

- Never run `npm test`: it runs the integration suite against the shared Stripe account. Run `npm run test:unit` instead.
- Read `docs/money.md` before touching anything that handles amounts.
EOF

cat > .claude/rules/migrations.md <<'EOF'
---
description: Database migration rules
globs: db/migrations/**
alwaysApply: false
---

- Every migration needs a matching down migration.
- Name files `YYYYMMDDHHMM_<slug>.sql`.
EOF

cat > .claude/skills/release-notes/SKILL.md <<'EOF'
---
name: release-notes
description: Use when the user asks for release notes, a changelog, or "what shipped since the last release". Use it even if they don't say "release notes".
allowed-tools: Read, Grep
---

This skill is read-only: `allowed-tools` limits it to Read and Grep, so it can't change anything.

Last release: !`git describe --tags --abbrev=0`

## Steps

1. List the commits merged since the last release with `git log <last-release>..HEAD --oneline`.
2. Group them under Added, Changed and Fixed.
3. Write the notes to `RELEASE_NOTES.md`.
EOF

cat > .claude/settings.json <<'EOF'
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/ask-ticket.sh" }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/test-nudge.sh" }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "if": "Bash(git push *)",
            "command": "bash \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/release-reminder.sh"
          }
        ]
      }
    ]
  }
}
EOF

cat > .claude/hooks/ask-ticket.sh <<'EOF'
#!/usr/bin/env bash
# Make sure every piece of work is tied to a ticket.
echo "Before doing anything, ask the user which Jira ticket this work is for, and wait for the answer."
EOF

cat > .claude/hooks/test-nudge.sh <<'EOF'
#!/usr/bin/env bash
# When the unit tests fail, steer the agent towards fixing them rather than skipping them.
input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // ""')
code=$(printf '%s' "$input" | jq -r '.tool_response.exit_code // 0')
if [[ "$cmd" == *"npm run test:unit"* && "$code" != "0" ]]; then
  jq -n '{hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: "Unit tests failed. Fix the code; do not skip, delete or weaken tests."}}'
fi
exit 0
EOF

cat > .claude/hooks/release-reminder.sh <<'EOF'
#!/usr/bin/env bash
# After a push, remind the agent to update the release notes before it stops.
echo '{"decision": "block", "reason": "You pushed. Update RELEASE_NOTES.md before finishing."}'
EOF

cat > .claude/agents/db-reviewer.md <<'EOF'
---
name: db-reviewer
description: Use when a change touches db/migrations/. Reviews migration safety (locks, backfills, down migrations).
tools: Read, Grep, Glob, Agent(sql-linter)
---

You review database migrations. You may only delegate to the `sql-linter` agent: the `Agent(sql-linter)` entry in your tools guarantees you can't start any other agent.
EOF

cat > package.json <<'EOF'
{
  "name": "billing-svc",
  "private": true,
  "scripts": {
    "test": "node --test test/integration",
    "test:unit": "node --test test/unit",
    "db:migrate": "node scripts/migrate.js"
  }
}
EOF

cat > docs/money.md <<'EOF'
# Money

Amounts are stored as integer cents. Never use floats for money.
EOF

cat > db/migrations/202611010900_init.sql <<'EOF'
CREATE TABLE charges (id TEXT PRIMARY KEY, order_id TEXT NOT NULL, amount_cents INTEGER NOT NULL);
EOF

cat > db/migrations/202611010900_init.down.sql <<'EOF'
DROP TABLE charges;
EOF

cat > src/charge.js <<'EOF'
export async function charge(stripe, order) {
  return stripe.paymentIntents.create(
    { amount: order.amountCents, currency: 'usd' },
    { idempotencyKey: order.id },
  );
}
EOF

git init -q -b main
git add -A
git -c user.name=fixture -c user.email=fixture@example.invalid commit -q -m "billing-svc: initial agent setup"
