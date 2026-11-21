#!/usr/bin/env bash
# Seeds a small Claude Code plugin marketplace with a problem statement
# (docs/PROBLEM.md) and a design that meets it (docs/DESIGN.md, codename
# "Kestrel"). Components A-D rebuild Claude Code built-ins that shipped in
# September 2026; component E, a zero-model-call hook harness, is the control.
# Ground truth checked against the Claude Code 2.1.329 docs on 2026-11-20.
# The codename appears only in DESIGN.md and the "Kestrel" code comments, and
# no file names a built-in the graders look for (tests/all.sh checks both).
set -euo pipefail

mkdir -p docs tools .claude/skills/audit-instructions .claude-plugin plugins/notify/.claude-plugin plugins/notify/hooks tests/fixtures .github/workflows

cat > docs/PROBLEM.md <<'EOF'
# What we need

We maintain a marketplace of Claude Code plugins used by six developers. Every developer runs Claude Code 2.1.300 or later (pinned in our onboarding doc).

1. Before each weekly release, find out whether each plugin's skills and agents actually change Claude's behaviour compared with not having the plugin, on a few realistic tasks. Run by hand; model calls are fine; keep it under $5 a run.
2. Contributors also use other coding agents. Write the project's agent instructions once, in one file, and have every agent, Claude Code included, read them.
3. Find instruction text that has gone stale: references to files or commands that no longer exist, and files that contradict each other.
4. Each developer wants to know which skills cost the most context and how often they are actually used on their machine.
5. Catch hook regressions on every push. Hard constraints: CI has no API key and must make zero model calls; under 30 seconds; it must assert the exit code and the stdout JSON of each `plugins/*/hooks/*.mjs` given fixture event JSON on stdin; no new third-party dependencies (Node 20 and bash are available).
EOF

cat > docs/DESIGN.md <<'EOF'
# Kestrel: release kit

Kestrel is five small components, one per need in PROBLEM.md. Everything lives in this repo, so there is nothing to install.

## A. Eval harness (`tools/eval-harness.sh`)

For each task in `tools/tasks/`, run `claude -p --plugin-dir plugins/notify` three times and plain `claude -p` three times. A `claude -p` judge prompt then scores each output from 1 to 10, and the script writes the scores to `eval.csv`.

## B. Instruction sync (`tools/sync-agents.mjs`)

Claude Code only reads CLAUDE.md, so Kestrel copies AGENTS.md to CLAUDE.md at the start of every session. It is registered as a SessionStart hook in `.claude/settings.json`. Contributors edit AGENTS.md only.

## C. Instruction audit (`.claude/skills/audit-instructions/SKILL.md`)

A skill that greps AGENTS.md and every skill for backticked paths that don't exist, and lists rules that contradict each other.

## D. Skill usage report (`tools/skill-usage.mjs`)

Reads `~/.claude/projects/*/*.jsonl`, counts `Skill` tool calls per skill, and estimates each skill's context cost as its description length divided by 4.

## E. Hook fixture harness (`tests/hooks.sh`)

Run by `.github/workflows/ci.yml` on every push. Pipes each `tests/fixtures/<hook>.<case>.json` into `node plugins/<p>/hooks/<hook>.mjs`, and compares the exit code and stdout with `<hook>.<case>.expected.json` using `node -e`. Exits 1 on any mismatch.
EOF

cat > AGENTS.md <<'EOF'
# Project rules

- Hooks are `.mjs`, run as `node "${CLAUDE_PLUGIN_ROOT}/hooks/<file>.mjs"`.
- Every plugin has `.claude-plugin/plugin.json`; bump its version on every change.
- Run `bash tests/hooks.sh` before pushing.
- Keep plugins dependency-free: Node 20 built-ins only.
EOF

cat > .claude/settings.json <<'EOF'
{
  "hooks": {
    "SessionStart": [
      { "hooks": [ { "type": "command", "command": "node tools/sync-agents.mjs" } ] }
    ]
  }
}
EOF

cat > tools/sync-agents.mjs <<'EOF'
// Kestrel B: keep CLAUDE.md in step with AGENTS.md.
import { copyFileSync, existsSync } from 'node:fs';

if (existsSync('AGENTS.md')) {
  copyFileSync('AGENTS.md', 'CLAUDE.md');
}
EOF

cat > tools/eval-harness.sh <<'EOF'
#!/usr/bin/env bash
# Kestrel A: with/without-plugin runs, then a judge prompt per output.
set -euo pipefail
echo "task,arm,run,score" > eval.csv
for task in tools/tasks/*.md; do
  for run in 1 2 3; do
    for arm in with without; do
      if [ "$arm" = with ]; then flags=(--plugin-dir plugins/notify); else flags=(); fi
      out=$(claude -p "${flags[@]}" "$(cat "$task")")
      score=$(claude -p "Score this answer to the task from 1 to 10. Reply with the number only. Task: $(cat "$task") Answer: $out")
      echo "$(basename "$task"),$arm,$run,$score" >> eval.csv
    done
  done
done
EOF

cat > .claude/skills/audit-instructions/SKILL.md <<'EOF'
---
name: audit-instructions
description: Use when asked to audit agent instructions for stale paths or contradictions.
---

1. Grep AGENTS.md and every `SKILL.md` under `.claude/skills/` and `plugins/` for backticked paths, and list each one that doesn't exist.
2. Read the files and list pairs of rules that contradict each other, quoting both.
3. Report both lists with `path:line`, and change nothing.
EOF

cat > tools/skill-usage.mjs <<'EOF'
// Kestrel D: count Skill tool calls per skill across local transcripts.
import { readdirSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

const root = join(homedir(), '.claude', 'projects');
const counts = {};
for (const project of readdirSync(root)) {
  for (const file of readdirSync(join(root, project)).filter((f) => f.endsWith('.jsonl'))) {
    for (const line of readFileSync(join(root, project, file), 'utf8').split('\n')) {
      if (!line.includes('"name":"Skill"')) continue;
      const m = line.match(/"skill":"([^"]+)"/);
      if (m) counts[m[1]] = (counts[m[1]] || 0) + 1;
    }
  }
}
// Context cost estimate: description length / 4 tokens (descriptions read separately).
for (const [skill, n] of Object.entries(counts).sort((a, b) => b[1] - a[1])) {
  console.log(`${skill}\t${n} calls`);
}
EOF

cat > .claude-plugin/marketplace.json <<'EOF'
{"name":"kit","owner":{"name":"fixture"},"plugins":[{"name":"notify","source":"./plugins/notify"}]}
EOF

cat > plugins/notify/.claude-plugin/plugin.json <<'EOF'
{"name":"notify","version":"0.1.0","description":"Quiet-hours reminder at session start."}
EOF

cat > plugins/notify/hooks/hooks.json <<'EOF'
{
  "hooks": {
    "SessionStart": [
      { "hooks": [ { "type": "command", "command": "node \"${CLAUDE_PLUGIN_ROOT}/hooks/quiet-hours.mjs\"" } ] }
    ]
  }
}
EOF

cat > plugins/notify/hooks/quiet-hours.mjs <<'EOF'
import { readFileSync } from 'node:fs';

const raw = readFileSync(0, 'utf8');
const event = raw.trim() ? JSON.parse(raw) : {};
if (event.source === 'startup') {
  const additionalContext = 'notify: quiet hours are 22:00-07:00';
  process.stdout.write(JSON.stringify({ hookSpecificOutput: { hookEventName: 'SessionStart', additionalContext } }));
}
process.exit(0);
EOF

cat > tests/fixtures/quiet-hours.startup.json <<'EOF'
{"hook_event_name":"SessionStart","source":"startup"}
EOF

cat > tests/fixtures/quiet-hours.startup.expected.json <<'EOF'
{"exit":0,"stdout":{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"notify: quiet hours are 22:00-07:00"}}}
EOF

cat > tests/fixtures/quiet-hours.resume.json <<'EOF'
{"hook_event_name":"SessionStart","source":"resume"}
EOF

cat > tests/fixtures/quiet-hours.resume.expected.json <<'EOF'
{"exit":0,"stdout":null}
EOF

cat > tests/hooks.sh <<'EOF'
#!/usr/bin/env bash
# Kestrel E: hook fixture harness. No model calls, no API key, no dependencies.
# For each tests/fixtures/<hook>.<case>.json, pipe it into the hook and compare
# the exit code and stdout JSON with <hook>.<case>.expected.json
# ({"exit": <n>, "stdout": <JSON or null for no output>}).
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0
for input in tests/fixtures/*.json; do
  case "$input" in *.expected.json) continue ;; esac
  name=$(basename "$input" .json)
  hook=${name%%.*}
  script=""
  for s in plugins/*/hooks/"$hook".mjs; do [ -f "$s" ] && script=$s; done
  if [ -z "$script" ]; then echo "FAIL $name: no plugins/*/hooks/$hook.mjs"; fail=1; continue; fi
  out=$(node "$script" < "$input")
  code=$?
  if node -e '
const [file, code, out] = process.argv.slice(1);
const want = JSON.parse(require("fs").readFileSync(file, "utf8"));
const got = out.trim() === "" ? null : JSON.parse(out);
process.exit(Number(code) === want.exit && JSON.stringify(got) === JSON.stringify(want.stdout) ? 0 : 1);
' "tests/fixtures/$name.expected.json" "$code" "$out"; then
    echo "ok   $name"
  else
    echo "FAIL $name (exit $code, stdout: $out)"
    fail=1
  fi
done
exit "$fail"
EOF

cat > .github/workflows/ci.yml <<'EOF'
name: ci
on: push
jobs:
  hooks:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: bash tests/hooks.sh
EOF

git init -q -b main
git add -A
git -c user.name=fixture -c user.email=fixture@example.invalid commit -q -m "kit: initial"
