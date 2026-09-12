#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
# shellcheck source=../../../tests/lib/assert.sh
source "$ROOT/tests/lib/assert.sh"
source "$HERE/../lib/events.sh"
source "$HERE/../lib/migrate.sh"

# `[[ ... ]]` followed by `assert_* $?` trips SC2319, so predicates go through commands.
isfile() { [[ -f "$1" ]]; }
nofile() { [[ ! -e "$1" ]]; }

TMP="$(mktemp -d)"
mk() { rm -rf "$TMP/ld"; mkdir -p "$TMP/ld/runtime"; LD="$TMP/ld"; RT="$LD/runtime"; EV="$LD/events.jsonl"; : > "$EV"; }
old_log() { printf '%s\n' "$@" > "$LD/run.log"; touch -t 202001010000 "$LD/run.log"; }
new_log() { printf '%s\n' "$@" > "$LD/run.log"; }

# --- LOOP_SCHEMA is the current layout version ---
assert_eq "2" "$LOOP_SCHEMA" "LOOP_SCHEMA is 2"

# --- loop_schema_read: stamp > tickseq-implies-1 > fresh-implies-current ---
mk; assert_eq "2" "$(loop_schema_read "$RT" 2)" "fresh dir reads as current"
mk; printf '7' > "$RT/tickseq"; assert_eq "1" "$(loop_schema_read "$RT" 2)" "tickseq without stamp reads as 1"
mk; printf '3' > "$RT/schema"; printf '7' > "$RT/tickseq"; assert_eq "3" "$(loop_schema_read "$RT" 2)" "stamp wins"
mk; printf 'junk' > "$RT/schema"; assert_eq "2" "$(loop_schema_read "$RT" 2)" "garbage stamp on a fresh dir reads as current"
mk; printf 'junk' > "$RT/schema"; printf '1' > "$RT/tickseq"; assert_eq "1" "$(loop_schema_read "$RT" 2)" "garbage stamp with tickseq reads as 1"

# --- legacy_harness_live: recent run.log whose last harness line is not an exit ---
mk; legacy_harness_live "$LD"; assert_false $? "no run.log -> not live"
mk; old_log "2026-01-01T00:00:00Z tick 3 starting"; legacy_harness_live "$LD"; assert_false $? "old mtime -> not live"
mk; new_log "2026-01-01T00:00:00Z tick 3 starting" "2026-01-01T00:01:00Z LOOP_DONE after 3 ticks"; legacy_harness_live "$LD"; assert_false $? "recent + LOOP_DONE -> not live"
mk; new_log "2026-01-01T00:00:00Z HALT: boom"; legacy_harness_live "$LD"; assert_false $? "recent + HALT -> not live"
mk; new_log "2026-01-01T00:00:00Z PAUSE present; writing checkpoint and exiting cleanly"; legacy_harness_live "$LD"; assert_false $? "recent + PAUSE exit -> not live"
mk; new_log "2026-01-01T00:00:00Z state saved on disk; re-run run.sh after your usage window resets to resume"; legacy_harness_live "$LD"; assert_false $? "recent + rate-limit exit -> not live"
mk; new_log "2026-01-01T00:00:00Z tick 3 starting" '{"type":"assistant","text":"LOOP_DONE mentioned by a subagent"}'; legacy_harness_live "$LD"; assert_true $? "recent + last harness line is a tick start -> live (stream text ignored)"
mk; new_log '{"type":"assistant"}'; legacy_harness_live "$LD"; assert_true $? "recent + no timestamped line -> live (conservative)"
mk; new_log "2026-01-01T00:00:00Z tick 3 starting"; legacy_harness_live "$LD" 0; assert_false $? "recent_s=0 -> not live"

# --- migrate_1_to_2: removes the LLM-written lock, reports what it did ---
mk; printf '123' > "$RT/LOCK"; assert_eq "legacy-lock-removed" "$(migrate_1_to_2 "$LD" "$RT")" "1->2 removes runtime/LOCK"
nofile "$RT/LOCK"; assert_true $? "LOCK is gone"
mk; assert_eq "none" "$(migrate_1_to_2 "$LD" "$RT")" "1->2 with nothing to do -> none"

# --- migrate_loop_dir: fresh dir is stamped, no event ---
mk; assert_eq "ok:2" "$(migrate_loop_dir "$LD" "$RT" "$EV" 2 0 0 2.0.0)" "fresh -> ok:2"
assert_eq "2" "$(cat "$RT/schema")" "fresh dir stamped"
assert_eq "0" "$(grep -c '"type":"migration"' "$EV")" "no migration event on a fresh dir"

# --- migrate_loop_dir: schema 1 with stale LOCK migrates, stamps, emits ---
mk; printf '9' > "$RT/tickseq"; printf '1' > "$RT/LOCK"
assert_eq "migrated:1:2:legacy-lock-removed" "$(migrate_loop_dir "$LD" "$RT" "$EV" 2 0 0 2.0.0)" "1 -> 2 migrated"
assert_eq "2" "$(cat "$RT/schema")" "stamp is 2 after migration"
nofile "$RT/LOCK"; assert_true $? "legacy LOCK removed by the runner"
assert_eq "1" "$(jq -r 'select(.type=="migration") | .from' "$EV")" "migration event from=1 (number)"
assert_eq "2" "$(jq -r 'select(.type=="migration") | .to' "$EV")" "migration event to=2"
assert_eq "legacy-lock-removed" "$(jq -r 'select(.type=="migration") | .actions' "$EV")" "migration event actions"
assert_eq "2.0.0" "$(jq -r 'select(.type=="migration") | .plugin_version' "$EV")" "migration event plugin_version"
# idempotent
assert_eq "ok:2" "$(migrate_loop_dir "$LD" "$RT" "$EV" 2 0 0 2.0.0)" "second run -> ok:2"
assert_eq "1" "$(grep -c '"type":"migration"' "$EV")" "second run emits nothing"

# --- migrate_loop_dir: blocked when a 1.x harness looks live, unless forced ---
mk; printf '9' > "$RT/tickseq"; printf '1' > "$RT/LOCK"
out="$(migrate_loop_dir "$LD" "$RT" "$EV" 2 1 0 2.0.0)"
case "$out" in blocked:*) assert_true 0 "legacy live -> blocked" ;; *) assert_true 1 "legacy live -> blocked (got $out)" ;; esac
case "$out" in *LOOP_MIGRATE_FORCE*) assert_true 0 "blocked detail names the override" ;; *) assert_true 1 "blocked detail names the override" ;; esac
isfile "$RT/LOCK"; assert_true $? "blocked leaves LOCK alone"
nofile "$RT/schema"; assert_true $? "blocked writes no stamp"
assert_eq "0" "$(grep -c '"type":"migration"' "$EV")" "blocked emits nothing"
assert_eq "migrated:1:2:legacy-lock-removed" "$(migrate_loop_dir "$LD" "$RT" "$EV" 2 1 1 2.0.0)" "forced -> migrated"

# --- migrate_loop_dir: a dir newer than the plugin is refused untouched ---
mk; printf '3' > "$RT/schema"; printf '9' > "$RT/tickseq"
assert_eq "newer:3" "$(migrate_loop_dir "$LD" "$RT" "$EV" 2 0 0 2.0.0)" "schema 3 on a schema-2 plugin -> newer:3"
assert_eq "3" "$(cat "$RT/schema")" "newer leaves the stamp alone"

rm -rf "$TMP"
assert_summary
