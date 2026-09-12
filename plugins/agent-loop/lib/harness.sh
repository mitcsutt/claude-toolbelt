#!/usr/bin/env bash
# agent-loop harness primitives: exclusivity, liveness, failure classification,
# memory headroom, incidents, desktop notification. run.sh is the only writer of
# runtime/ state; everything here takes the directory it writes to as an explicit
# argument so the whole file is exercisable without a running loop
# (tests/harness.test.sh). Nothing in here reads run.log: loop state is derived
# from events.jsonl + PID liveness only.

# emit_event (incident_new narrates into events.jsonl). Path relative to this lib dir.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/events.sh"

# harness_alive <pid> -> 0 iff the pid is live AND its command line contains "run.sh".
#   The command check defeats PID reuse: a recycled pid belonging to some unrelated
#   process must not keep a dead harness's lock alive. Deliberately NOT pgrep/`ps |
#   grep run.sh` — those match the grep itself and any assistant tool call.
harness_alive() {
  local pid="${1:-}" cmd
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  cmd="$(ps -o command= -p "$pid" 2>/dev/null)"
  [[ "$cmd" == *run.sh* ]]
}

# _harness_write_json <runtime_dir> <pid> <loop_dir> <version>
#   The lock's payload: who owns this LOOP_DIR, since when, and on which host.
_harness_write_json() {
  local rt="$1" pid="$2" loop_dir="$3" ver="$4" host
  host="$(hostname 2>/dev/null || echo unknown)"
  jq -cn --argjson pid "$pid" --argjson start_epoch "$(date +%s)" \
     --arg host "$host" --arg loop_dir "$loop_dir" --arg plugin_version "$ver" \
     '{pid:$pid,start_epoch:$start_epoch,host:$host,loop_dir:$loop_dir,plugin_version:$plugin_version}' \
     > "$rt/harness.json" 2>/dev/null || true
}

# harness_lock_acquire <runtime_dir> <pid> <loop_dir> <version>
#   prints "acquired" | "takeover:<oldpid>" | "held:<pid>:<start_epoch>"
#   exit 0 for acquired/takeover, 1 for held.
#   `mkdir` is the atomic primitive (no flock on macOS): the winner of the mkdir
#   race owns the dir. A LOSER still has to decide whether the recorded owner is
#   really alive — a harness killed by the OOM killer leaves the dir behind.
#
#   Clearing a stale dir is itself a race: two harnesses starting against the same
#   dead owner would both delete it and both call themselves the new owner. So a
#   clear is never an acquisition — it only re-opens the mkdir race, and the loser
#   of that second race reports `held` by whoever won it. A dir we have already
#   cleared once is never cleared again: if its payload has not appeared yet we
#   wait for it, because a lock cleared twice is two harnesses, which is the exact
#   bug this whole mechanism exists to prevent.
harness_lock_acquire() {
  local rt="$1" pid="$2" loop_dir="$3" ver="$4"
  local lock="$rt/harness.lock.d"
  local oldpid="" curpid curstart cleared=0 attempt=0
  mkdir -p "$rt" 2>/dev/null || true
  while (( attempt < 6 )); do
    attempt=$(( attempt + 1 ))
    if mkdir "$lock" 2>/dev/null; then
      _harness_write_json "$rt" "$pid" "$loop_dir" "$ver"
      if (( cleared > 0 )); then printf 'takeover:%s' "${oldpid:-none}"; else printf 'acquired'; fi
      return 0
    fi
    # Someone holds the dir. Give the winner of a race we started a moment to
    # publish its payload before judging it.
    (( cleared > 0 )) && sleep 0.2
    curpid="$(jq -r '.pid // empty' "$rt/harness.json" 2>/dev/null)"
    curstart="$(jq -r '.start_epoch // 0' "$rt/harness.json" 2>/dev/null)"
    [[ "$curstart" =~ ^[0-9]+$ ]] || curstart=0
    if harness_alive "$curpid"; then
      printf 'held:%s:%s' "$curpid" "$curstart"
      return 1
    fi
    if (( cleared > 0 )) && [[ "$curpid" == "$oldpid" ]]; then
      continue    # our own race winner has not written harness.json yet — wait, do not clear
    fi
    oldpid="$curpid"
    rmdir "$lock" 2>/dev/null || rm -rf "$lock" 2>/dev/null || true
    cleared=$(( cleared + 1 ))
  done
  # Gave up waiting for a payload that never arrived: report the dir as held rather
  # than seizing it. The next run.sh invocation finds a genuinely stale lock.
  curpid="$(jq -r '.pid // empty' "$rt/harness.json" 2>/dev/null)"
  curstart="$(jq -r '.start_epoch // 0' "$rt/harness.json" 2>/dev/null)"
  [[ "$curstart" =~ ^[0-9]+$ ]] || curstart=0
  printf 'held:%s:%s' "$curpid" "$curstart"
  return 1
}

# harness_lock_release <runtime_dir> <pid>
#   Removes the lock dir + harness.json only when this pid is the recorded owner.
#   Always exits 0: release runs from the EXIT trap and must never mask the real
#   exit code, and a non-owner (or an already-released lock) is simply a no-op.
harness_lock_release() {
  local rt="$1" pid="$2" owner
  owner="$(jq -r '.pid // empty' "$rt/harness.json" 2>/dev/null)"
  if [[ -n "$owner" && "$owner" != "$pid" ]]; then
    return 0
  fi
  rm -f "$rt/harness.json" 2>/dev/null || true
  rmdir "$rt/harness.lock.d" 2>/dev/null || rm -rf "$rt/harness.lock.d" 2>/dev/null || true
  return 0
}

# heartbeat_loop <runtime_dir> <interval> [parent_pid]
#   Foreground infinite loop; run.sh backgrounds it. Observers treat the harness as
#   alive only when the pid is live AND this file is younger than 3x the interval,
#   so a wedged (but not exited) harness is still detectable.
#   With <parent_pid> the loop exits once that pid is gone. That is the SIGKILL case:
#   a harness killed by the OOM killer runs no EXIT trap, and a heartbeat that kept
#   ticking afterwards would tell every observer the dead loop is healthy.
heartbeat_loop() {
  local rt="$1" interval="${2:-10}" parent="${3:-0}"
  mkdir -p "$rt" 2>/dev/null || true
  while true; do
    date +%s > "$rt/HEARTBEAT" 2>/dev/null || true
    sleep "$interval"
    if [[ "$parent" =~ ^[0-9]+$ ]] && (( parent > 0 )); then
      kill -0 "$parent" 2>/dev/null || return 0
    fi
  done
}

# classify_cause <rc> <result_json> <sentinel> -> the cause token for tick_end.
#   `classify_result` (lib/loop.sh) answers "what should the loop do next";
#   this answers "why did the tick end", which is what an operator and the medic
#   need: rc=124/137/143 are environment kills, not model failures, and lumping
#   them into "retry" is exactly what made OOM invisible.
classify_cause() {
  local rc="${1:-0}" json="${2:-}" sentinel="${3:-NONE}" is_err status
  case "$rc" in
    124) printf 'timeout';    return ;;
    137) printf 'killed';     return ;;
    143) printf 'terminated'; return ;;
  esac
  is_err="$(printf '%s' "$json" | jq -r '.is_error // false' 2>/dev/null)"
  if [[ "$is_err" == "true" ]]; then
    status="$(printf '%s' "$json" | jq -r '.api_error_status // empty' 2>/dev/null)"
    if [[ "$status" == "429" ]]; then printf 'rate_limit'; else printf 'api_error'; fi
    return
  fi
  if [[ "$rc" != "0" ]]; then printf 'crashed'; return; fi
  if [[ "$sentinel" == "NONE" || -z "$sentinel" ]]; then printf 'no_sentinel'; return; fi
  printf 'ok'
}

# cause_human <cause> -> the one-clause gloss appended to the per-tick summary line.
cause_human() {
  case "${1:-}" in
    ok)          printf 'ok' ;;
    timeout)     printf 'timeout (tick exceeded its wall-clock cap)' ;;
    killed)      printf 'killed (SIGKILL — likely OS memory pressure)' ;;
    terminated)  printf 'terminated (SIGTERM)' ;;
    api_error)   printf 'api error' ;;
    rate_limit)  printf 'rate limited (429)' ;;
    no_sentinel) printf 'no sentinel in the tick result' ;;
    crashed)     printf 'crashed (no result payload)' ;;
    *)           printf '%s' "${1:-}" ;;
  esac
}

# mem_headroom -> "<free_mb> <swap_used_pct>"; "0 0" on an unknown platform.
#   darwin: free+inactive+speculative pages x page size, and vm.swapusage.
#   linux:  MemAvailable, and SwapTotal-SwapFree.
mem_headroom() {
  local os free_mb=0 swap_pct=0
  os="$(uname -s 2>/dev/null || echo unknown)"
  case "$os" in
    Darwin)
      free_mb="$(vm_stat 2>/dev/null | awk '
        /page size of/ { for (i = 1; i <= NF; i++) if ($i == "of") { ps = $(i + 1) + 0; break } }
        /^Pages free:/        { f = $3 + 0 }
        /^Pages inactive:/    { iv = $3 + 0 }
        /^Pages speculative:/ { sp = $3 + 0 }
        END { if (ps <= 0) ps = 4096; printf "%d", ((f + iv + sp) * ps) / 1048576 }')"
      swap_pct="$(sysctl -n vm.swapusage 2>/dev/null | awk '
        { t = 0; u = 0
          for (i = 1; i <= NF; i++) {
            if ($i == "total") t = $(i + 2) + 0
            if ($i == "used")  u = $(i + 2) + 0
          }
          if (t > 0) printf "%d", (u * 100) / t; else printf "0" }')"
      ;;
    Linux)
      free_mb="$(awk '/^MemAvailable:/ { printf "%d", $2 / 1024; found = 1; exit }
                      END { if (!found) printf "0" }' /proc/meminfo 2>/dev/null)"
      swap_pct="$(awk '/^SwapTotal:/ { t = $2 } /^SwapFree:/ { f = $2 }
                       END { if (t > 0) printf "%d", ((t - f) * 100) / t; else printf "0" }' \
                       /proc/meminfo 2>/dev/null)"
      ;;
  esac
  [[ "$free_mb" =~ ^[0-9]+$ ]] || free_mb=0
  [[ "$swap_pct" =~ ^[0-9]+$ ]] || swap_pct=0
  printf '%s %s' "$free_mb" "$swap_pct"
}

# mem_guard_action <free_mb> <swap_pct> <min_mb> <max_swap_pct> -> proceed|delay
#   Both conditions must hold: a low free figure alone is normal on a healthy box
#   (the OS caches aggressively); it only matters once the machine is also paging.
#   `>=` on the swap ceiling, so an explicit ceiling of 0 always trips (tests).
mem_guard_action() {
  local free_mb="${1:-0}" swap_pct="${2:-0}" min_mb="${3:-1024}" max_swap="${4:-90}"
  if (( free_mb < min_mb )) && (( swap_pct >= max_swap )); then
    printf 'delay'
  else
    printf 'proceed'
  fi
}

# write_tick_json <runtime_dir> <tick> <pid> <started_at> <timeout_s>
#   The in-flight tick's liveness record. Removed when the tick ends, so its mere
#   presence plus a live pid is what "a tick is running" means to an observer.
write_tick_json() {
  local rt="$1" tick="$2" pid="$3" started="$4" timeout_s="$5"
  [[ "$tick" =~ ^[0-9]+$ ]] || tick=0
  [[ "$pid" =~ ^[0-9]+$ ]] || pid=0
  [[ "$started" =~ ^[0-9]+$ ]] || started=0
  [[ "$timeout_s" =~ ^[0-9]+$ ]] || timeout_s=0
  jq -cn --argjson tick "$tick" --argjson pid "$pid" \
     --argjson started_at "$started" --argjson timeout_s "$timeout_s" \
     '{tick:$tick,pid:$pid,started_at:$started_at,timeout_s:$timeout_s}' \
     > "$rt/tick.json" 2>/dev/null || true
}

# incident_new <runtime_dir> <events_file> <kind> <severity> <detail> <tick>
#   Prints the new id (i-NNN), writes runtime/incident-<id>.json for the medic to
#   read, and narrates one `incident` event for the dashboard and any attached
#   Monitor. The counter is per-LOOP_DIR (persisted under runtime/), not per-run.
incident_new() {
  local rt="$1" ev="$2" kind="$3" sev="$4" detail="$5" tick="${6:-0}"
  local seqf="$rt/incidentseq" n id now
  [[ "$tick" =~ ^[0-9]+$ ]] || tick=0
  mkdir -p "$rt" 2>/dev/null || true
  n="$(cat "$seqf" 2>/dev/null)"; [[ "$n" =~ ^[0-9]+$ ]] || n=0
  n=$(( n + 1 ))
  printf '%s' "$n" > "$seqf" 2>/dev/null || true
  id="$(printf 'i-%03d' "$n")"
  now="$(date +%s)"
  jq -cn --arg id "$id" --arg kind "$kind" --arg severity "$sev" --arg detail "$detail" \
     --argjson tick "$tick" --argjson t "$now" \
     '{id:$id,kind:$kind,severity:$severity,detail:$detail,tick:$tick,t:$t}' \
     > "$rt/incident-$id.json" 2>/dev/null || true
  emit_event "$ev" incident id "$id" kind "$kind" severity "$sev" detail "$detail" tick "$tick"
  printf '%s' "$id"
}

# notify_desktop <title> <body>
#   Best-effort push to the human at the keyboard; never fails, never blocks the
#   loop. LOOP_NOTIFY=0 suppresses it (tests, and anyone running headless).
notify_desktop() {
  local title="${1:-agent-loop}" body="${2:-}"
  [[ "${LOOP_NOTIFY:-1}" == "0" ]] && return 0
  title="${title//\"/\'}"; title="${title//$'\n'/ }"
  body="${body//\"/\'}";  body="${body//$'\n'/ }"
  case "$(uname -s 2>/dev/null)" in
    Darwin) osascript -e "display notification \"$body\" with title \"$title\"" >/dev/null 2>&1 || true ;;
    Linux)  command -v notify-send >/dev/null 2>&1 && { notify-send "$title" "$body" >/dev/null 2>&1 || true; } ;;
  esac
  return 0
}
