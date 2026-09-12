#!/usr/bin/env bash
# agent-loop loop-dir schema. Pure helpers plus one runner; sourced by lib/loop.sh.
#
# LOOP_SCHEMA versions the LAYOUT of a loop dir (file names, formats, sentinel
# semantics). It is not the plugin version: prompt, UI and harness-internal changes
# never bump it. runtime/schema holds the stamp and the harness is its only writer.
# A dir with no stamp but a tick counter predates stamping (schema 1); a dir with
# neither is new and is stamped with the current value on first start.
# shellcheck disable=SC2034  # read by run.sh and the tests, not by this file.
LOOP_SCHEMA=2

# loop_schema_read <runtime_dir> <current> -> prints the dir's schema.
loop_schema_read() {
  local rt="$1" cur="$2" n
  n="$(cat "$rt/schema" 2>/dev/null)"
  if [[ "$n" =~ ^[0-9]+$ ]]; then printf '%s' "$n"
  elif [[ -f "$rt/tickseq" ]]; then printf '1'
  else printf '%s' "$cur"
  fi
}

# _mig_mtime <path> -> epoch seconds (0 when unreadable). BSD stat first, then GNU.
_mig_mtime() {
  stat -f %m "$1" 2>/dev/null || stat -c %Y "$1" 2>/dev/null || echo 0
}

# legacy_harness_live <loop_dir> [recent_s=120] -> 0 iff a pre-2.0 harness looks alive.
#   1.x wrote no pid file, so this is evidence, not proof: run.log modified within
#   recent_s AND its last timestamped harness line is not one of 1.x's clean-exit
#   lines. Raw stream lines carry no leading timestamp and are ignored, so a subagent
#   that merely mentions LOOP_DONE cannot make a live harness look stopped.
#   The caller MUST evaluate this before writing anything to run.log itself.
legacy_harness_live() {
  local ld="$1" recent="${2:-120}" age last rl
  rl="$ld/run.log"   # separate statement: bash 3.2 expands all `local` args before assigning
  [[ -f "$rl" ]] || return 1
  age=$(( $(date +%s) - $(_mig_mtime "$rl") ))
  (( age < recent )) || return 1   # a 0s window means nothing is recent (test: recent_s=0)
  last="$(grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z ' "$rl" 2>/dev/null | tail -1)"
  case "$last" in
    *"LOOP_DONE after"*|*"HALT:"*|*"PAUSE present;"*|*"re-run run.sh"*|*"NEEDS HUMAN:"*) return 1 ;;
  esac
  return 0
}

# migrate_1_to_2 <loop_dir> <runtime_dir> -> prints comma-joined action tokens, or "none".
#   1.x -> 2.0: the tick prompt no longer writes runtime/LOCK and nothing reads it; the
#   harness owns the mutex (runtime/harness.lock.d). LOOP_CONFIG is left alone — the
#   harness already defaults an absent `Dashboard:`/`Medic:` line to `auto`.
migrate_1_to_2() {
  local rt="$2" actions=""
  if [[ -e "$rt/LOCK" ]]; then
    rm -f "$rt/LOCK" 2>/dev/null && actions="legacy-lock-removed"
  fi
  printf '%s' "${actions:-none}"
}

# migrate_loop_dir <loop_dir> <runtime_dir> <events> <target> <legacy_live 0|1> <force 0|1> <plugin_version>
#   Prints exactly one token:
#     ok:<n>                    nothing to do (a missing stamp is written)
#     migrated:<from>:<to>:<a>  ran every step from..to; <a> = actions of the last step
#     blocked:<detail>          from=1, a 1.x harness looks live, not forced; nothing touched
#     newer:<n>                 the dir is newer than this plugin; nothing touched
#   Each step writes the stamp and emits `migration` before the next runs, so an
#   interrupted multi-step upgrade resumes from the step it reached.
migrate_loop_dir() {
  local ld="$1" rt="$2" ev="$3" target="$4" legacy_live="$5" force="$6" ver="${7:-}"
  local from cur acts="none"
  from="$(loop_schema_read "$rt" "$target")"
  if (( from > target )); then printf 'newer:%s' "$from"; return 0; fi
  if (( from == target )); then
    [[ -f "$rt/schema" ]] || printf '%s' "$target" > "$rt/schema" 2>/dev/null || true
    printf 'ok:%s' "$target"; return 0
  fi
  if (( from == 1 )) && [[ "$legacy_live" == "1" && "$force" != "1" ]]; then
    printf 'blocked:%s' "run.log was written in the last 2 minutes and its last harness line is not a 1.x exit line, so the pre-2.0 harness may still be running. Pause it (touch $rt/PAUSE and wait for its terminal to exit), kill its dashboard, then re-run; or re-run with LOOP_MIGRATE_FORCE=1 if you are certain it is dead"
    return 0
  fi
  cur="$from"
  while (( cur < target )); do
    case "$cur" in
      1) acts="$(migrate_1_to_2 "$ld" "$rt")" ;;
      *) acts="none" ;;
    esac
    cur=$(( cur + 1 ))
    printf '%s' "$cur" > "$rt/schema" 2>/dev/null || true
    emit_event "$ev" migration from "$(( cur - 1 ))" to "$cur" actions "$acts" plugin_version "$ver"
  done
  printf 'migrated:%s:%s:%s' "$from" "$target" "$acts"
}
