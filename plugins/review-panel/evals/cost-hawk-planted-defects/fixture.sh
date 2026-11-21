#!/usr/bin/env bash
# Seeds a review-bot rollout plan (docs/plan.md) and its repo config with
# eight planted cost defects and one control (the nightly R2 backup). The
# ground truth for each, priced as of 2026-11-19, is in case.yaml (one grader
# per plant). Nothing here is ever executed by the eval: it is review material.
set -euo pipefail

fail() { echo "fixture self-check failed: $*" >&2; exit 1; }

mkdir -p docs .claude/hooks .claude/skills/summarize .claude/agents .github/workflows scripts ops test

cat > docs/plan.md <<'EOF'
# Review bot rollout

## Context

- 20 developers.
- Private GitHub repository on the Team plan.
- API-key (Console) billing for every Claude call.
- Interactive Claude Code sessions run on Opus 5.5 at about 150k tokens of context.
- 22 working days a month.

## 1. Tool-call auditing

Every tool call is checked by a PostToolUse prompt hook on Opus for accuracy.
Sessions average 200 tool calls; 20 sessions a day. Each check is ~3k input
and 100 output tokens. Hooks run locally, so this adds little cost.

## 2. Architecture context

A SessionStart hook prints docs/architecture-full.md (40 KB) into every
session, including the review bot's headless runs: 40 KB ≈ 10k tokens added
to every session, which we accept.

## 3. PR reviewers

`.claude/agents/pr-reviewer.md` runs as 12 reviewer subagents on Opus per PR,
40 PRs a week. Last month's transcripts: per reviewer about 15 requests, 1.8M
cache-read tokens, 150k 5-minute cache-write tokens and 15k output tokens;
each request's prompt is between 110k and 140k tokens. Proposal: switch
reviewers to Haiku 5.5, which is 40× cheaper per token, cutting this bill by
about 97%.

## 4. Issue triage

scripts/triage.sh classifies issues with Sonnet 5.5 and a 30k-token rubric
marked for caching; ~500 calls per working day, one every 1–3 minutes.
Because the rubric is cached, each call pays about $0.003 for it.

## 5. CI watcher

ops/poll.timer runs a headless Claude check of CI every minute on the home
server; each run is ~3 requests with a ~25k-token prefix on Sonnet 5.5 and
~1.5k output tokens.

## 6. Green-tests gate

A Stop hook refuses to let Claude finish until `npm test` passes. It also
applies to the review-bot CI job. Claude Code caps Stop-hook continuations at
8, so the worst case is 8 × ~$0.15 = $1.20 per run.

## 7. Summaries

The summarize skill is pinned to Haiku. It triggers about 20 times per
session, so summaries cost ~95% less than on Opus.

## 8. CI

ci.yml lints on every push to any branch, about 30 pushes a day; the lint
takes ~40 seconds.

## 9. Backups

ops/backup.sh tars the 2 GB data directory nightly and uploads it to a
Cloudflare R2 bucket used only for this, keeping 30 copies; once a month we
test a restore by downloading one copy. We're a bit worried about 60 GB of
storage and restore egress.
EOF

cat > .claude/settings.json <<'EOF'
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "prompt",
            "model": "claude-opus-5-5",
            "prompt": "Check this tool call and its result for mistakes: $ARGUMENTS"
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "cat \"$CLAUDE_PROJECT_DIR/docs/architecture-full.md\"" }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/require-green.sh\"" }
        ]
      }
    ]
  }
}
EOF

cat > .claude/hooks/require-green.sh <<'EOF'
#!/usr/bin/env bash
# Refuse to let Claude stop until the tests pass.
if npm test --silent >/dev/null 2>&1; then exit 0; fi
echo "Tests are failing. Fix them and run npm test again before finishing." >&2
exit 2
EOF

cat > package.json <<'EOF'
{"name":"acme-app","private":true,"scripts":{"test":"node test/run.mjs"}}
EOF

cat > test/run.mjs <<'EOF'
if (!process.env.DATABASE_URL) { console.error('DATABASE_URL not set'); process.exit(1); }
console.log('ok');
EOF

for i in $(seq 1 480); do
  printf 'Section %03d: services, queues, data flow and ownership notes for the platform.\n' "$i"
done > docs/architecture-full.md

cat > .claude/agents/pr-reviewer.md <<'EOF'
---
name: pr-reviewer
description: Reviews a PR diff for correctness.
model: opus
tools: Read, Grep, Glob
---

Review the PR diff you are given for correctness bugs and report each with file and line.
EOF

cat > .claude/skills/summarize/SKILL.md <<'EOF'
---
name: summarize
description: Use whenever the conversation needs a summary of progress so far.
model: haiku
---

Summarize the conversation so far in five bullets.
EOF

cat > .github/workflows/ci.yml <<'EOF'
name: ci
on: push
jobs:
  lint:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
        node: [20, 22, 24]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: ${{ matrix.node }}
      - run: npm ci
      - run: npx eslint .
EOF

cat > .github/workflows/review-bot.yml <<'EOF'
name: review-bot
on: pull_request
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: claude -p "Review this PR and fix failing tests"
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
EOF

cat > scripts/triage.sh <<'EOF'
#!/usr/bin/env bash
# Classify one issue (title and body on stdin) with Sonnet 5.5.
set -euo pipefail
SYSTEM="Now: $(date +%s.%N)"$'\n'"$(cat docs/triage-rubric.md)"
ISSUE="$(cat)"
BODY=$(jq -n --arg system "$SYSTEM" --arg issue "$ISSUE" '{
  model: "claude-sonnet-5-5",
  max_tokens: 200,
  system: [{type: "text", text: $system, cache_control: {type: "ephemeral"}}],
  messages: [{role: "user", content: $issue}]
}')
curl -s https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d "$BODY"
EOF

cat > docs/triage-rubric.md <<'EOF'
(30k-token rubric, abridged for this fixture)
EOF

cat > ops/poll.timer <<'EOF'
[Unit]
Description=Check CI for acme-app every minute

[Timer]
OnUnitActiveSec=1min
AccuracySec=1us

[Install]
WantedBy=timers.target
EOF

cat > ops/poll.service <<'EOF'
[Unit]
Description=Headless Claude CI check for acme-app

[Service]
Type=oneshot
WorkingDirectory=/srv/acme-app
ExecStart=/usr/local/bin/claude -p "Check CI status for acme-app and summarize anything failing"
EOF

cat > ops/crontab <<'EOF'
15 3 * * * /srv/acme-app/ops/backup.sh
EOF

cat > ops/backup.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
tar czf "/tmp/data-$(date +%F).tgz" /srv/acme-app/data
rclone copy "/tmp/data-$(date +%F).tgz" r2:acme-backups/
rclone delete --min-age 30d r2:acme-backups/
rm -f /tmp/data-*.tgz
EOF

# Self-checks: the graders' ground truth depends on these.
test "$(wc -c < docs/architecture-full.md)" -gt 30000 || fail "architecture-full.md must exceed the 10,000-character hook cap by far"
grep -q '"type": "prompt"' .claude/settings.json || fail "PostToolUse hook must be a prompt hook"
grep -q '^AccuracySec=1us$' ops/poll.timer || fail "poll.timer must set AccuracySec=1us"
