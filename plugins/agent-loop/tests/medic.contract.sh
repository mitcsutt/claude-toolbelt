#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
# shellcheck source=../../../tests/lib/assert.sh
source "$ROOT/tests/lib/assert.sh"
F="$HERE/../skills/agent-loop-medic/SKILL.md"
has() { grep -qF -e "$1" "$F"; assert_true $? "medic must mention: $1"; }
hasnt() { grep -qF -e "$1" "$F"; assert_false $? "medic must NOT contain: $1"; }

# Frontmatter name matches the directory
rc=0; [ "$(awk '/^name:/{print $2; exit}' "$F")" = "agent-loop-medic" ] || rc=1; assert_true "$rc" "frontmatter name is agent-loop-medic"
grep -q '^disable-model-invocation: true' "$F"; assert_false $? "medic must be model-invocable (no disable-model-invocation)"

# Required sections
for h in 'Allowed remediations' 'Forbidden' 'Budget' 'Output contract' 'Decision table' 'Headless mode' 'Interactive mode' 'Liveness rule'; do
  grep -qE "^#+ .*$h" "$F"; assert_true $? "medic has a '$h' section"
done

# Output contract keys
has 'medic-<id>.json'
for k in '"id"' '"kind"' '"outcome"' '"actions"' '"summary"' '"human_next_step"'; do has "$k"; done
has 'MEDIC_OUTCOME: <outcome>'
has 'LOOP_CLEANUP.md'

# The four outcomes
for o in resumed paused escalated noop; do
  grep -qE "\`$o\`" "$F"; assert_true $? "outcome \`$o\` appears"
done
has 'resumed | paused | escalated | noop'

# Every incident kind from spec §6.2 has a row
for k in harness-crash tick-killed tick-timeout tick-stalled garbage-ticks no-progress halt-sentinel lock-conflict git-divergence dashboard-crashloop migration-blocked schema-newer; do
  grep -qE "^\| \`$k\` \|" "$F"; assert_true $? "decision table has a row for $k"
done

# Budget rules (spec §6.3)
has 'twice in a run'
has 'MEDIC_MAX_PER_RUN'
has 'MEDIC_TIMEOUT'
has 'outside the allowlist'

# Allowlist (spec §6.4) and forbidden list
has 'harness.lock.d'
has 'never to `[x]`'
has 'allow_list'
has 'NEEDS_HUMAN.md'
has 'git reset'
has 'starting a harness'
has 'editing `LOOP_CONFIG.md`'

# Liveness sentence, verbatim, and pgrep only inside it
has 'Never `pgrep`/`ps | grep run.sh` — those match your own tool calls.'
n_pgrep="$(grep -c 'pgrep' "$F")"; n_never="$(grep -c 'Never `pgrep`' "$F")"
rc=0; [ "$n_pgrep" -eq "$n_never" ] || rc=1; assert_true "$rc" "pgrep appears only in the 'Never' sentence ($n_pgrep vs $n_never)"

# AskUserQuestion appears only in a NEVER sentence
n_ask="$(grep -c 'AskUserQuestion' "$F")"; n_never_ask="$(grep -c 'NEVER call `AskUserQuestion`' "$F")"
[ "$n_ask" -gt 0 ] && [ "$n_ask" -eq "$n_never_ask" ]; assert_true $? "AskUserQuestion appears only in a NEVER sentence ($n_ask vs $n_never_ask)"
has 'EnterPlanMode'

# Never edits product code, never starts a harness
has 'never touches product code'
hasnt 'runtime/LOCK'
hasnt 'run.log`-derived'

# The two schema kinds are human jobs: never force a migration, never edit the stamp.
hasnt 'LOOP_MIGRATE_FORCE=1'
has 'runtime/schema'

assert_summary
