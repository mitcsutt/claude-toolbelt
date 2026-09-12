#!/usr/bin/env bash
set -uo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$PLUGIN_ROOT/lib/loop.sh"      # sources lib/events.sh + lib/harness.sh
# The harness stamps its own version into the lock and into loop_start so an
# operator (and the medic) can tell which contract a running loop was started on.
PLUGIN_VERSION="$(jq -r '.version // "0.0.0"' "$PLUGIN_ROOT/.claude-plugin/plugin.json" 2>/dev/null || echo 0.0.0)"

# Single per-run base dir. All loop artefacts live under here; nothing is written
# to the worktree root. The setup skill creates it and the launch command passes
# LOOP_DIR explicitly (`.claude/loop/<run-id>`); the default below is a fallback.
LOOP_DIR="${LOOP_DIR:-.claude/loop/run}"
RUNTIME_DIR="$LOOP_DIR/runtime"   # ephemeral lock/sprint/worker/ratelimit files
# Event substrate: the harness narrates tick boundaries + commits into events.jsonl (the
# dashboard tails it). TICKSEQ persists the continuous tick counter under runtime/ so tick
# numbers stay continuous across resume (runtime/ survives run.sh re-invocations).
EVENTS="${EVENTS:-$LOOP_DIR/events.jsonl}"
TICKSEQ="$RUNTIME_DIR/tickseq"

CONFIG_PATH="${CONFIG_PATH:-$LOOP_DIR/LOOP_CONFIG.md}"
PLAN_PATH="${PLAN_PATH:-$LOOP_DIR/LOOP_PLAN.md}"
LEDGER="${LEDGER:-$LOOP_DIR/LOOP_USAGE.jsonl}"
RUNLOG="${RUNLOG:-$LOOP_DIR/run.log}"
# Legacy-harness evidence is read HERE, before this process writes a single line to
# run.log: the check looks at run.log's mtime and last harness line, and would
# otherwise see its own output. Consumed by the schema migration below.
LEGACY_LIVE=0
legacy_harness_live "$LOOP_DIR" 120 && LEGACY_LIVE=1
STATUS_FILE="${STATUS_FILE:-$LOOP_DIR/LOOP_STATUS.md}"
# Persistent, repo-scoped cross-run knowledge. Resolves to the SIBLING of the per-run
# $LOOP_DIR (i.e. `.claude/loop/KNOWLEDGE.md`), so it lives outside any per-run dir and is
# NOT caught by the nested `$LOOP_DIR/.gitignore` (`runtime/`) — it is committed/tracked.
# Survives across loop runs; the Scout reads it per task, the postmortem auto-promotes
# durable learnings into it at loop close. Never per-run, never bulk-seeded.
LOOP_KNOWLEDGE="${LOOP_KNOWLEDGE:-$(dirname "$LOOP_DIR")/KNOWLEDGE.md}"
TICK_PROMPT="${TICK_PROMPT:-$PLUGIN_ROOT/tick-prompt.md}"
PAUSE_BETWEEN="${PAUSE_BETWEEN:-5}"
RL_BACKOFF="${RL_BACKOFF:-60}"        # seconds to wait out a rate limit
RL_MAX_STRIKES="${RL_MAX_STRIKES:-5}" # consecutive rate-limits before graceful halt

# --- v2 resilience knobs -------------------------------------------------------
HB_INTERVAL="${HB_INTERVAL:-10}"            # heartbeat write period (s)
TICK_POLL="${TICK_POLL:-1}"                 # in-flight tick poll period (s)
STALL_S="${STALL_S:-300}"                   # no stream activity for this long -> tick-stalled
MEM_MIN_MB="${MEM_MIN_MB:-1024}"            # pre-tick free-memory floor
SWAP_MAX_PCT="${SWAP_MAX_PCT:-90}"          # pre-tick swap-utilisation ceiling
MEM_BACKOFF="${MEM_BACKOFF:-60}"            # wait between memory re-checks (s)
MEM_MAX_DELAYS="${MEM_MAX_DELAYS:-5}"       # then proceed anyway (the guard is advisory)
MEDIC_MAX_PER_RUN="${MEDIC_MAX_PER_RUN:-3}" # hard budget: medic invocations per run.sh
MEDIC_TIMEOUT="${MEDIC_TIMEOUT:-600}"       # wall-clock cap on one medic (s)
DASH_MAX_RESTARTS="${DASH_MAX_RESTARTS:-5}" # sidecar respawns before giving up

# Ensure the base dir + runtime subdir exist before anything writes into them.
mkdir -p "$RUNTIME_DIR"
# Each tick is a fresh subprocess; export the base dir so the tick prompt can
# resolve all artefact paths relative to it. LOOP_KNOWLEDGE is exported too so the
# tick's Scout can read the persistent cross-run knowledge file.
export LOOP_DIR RUNTIME_DIR LOOP_KNOWLEDGE EVENTS

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$RUNLOG" >&2; }
die() { EXIT_REASON="error"; EXIT_DETAIL="$*"; log "HALT: $*"; exit 1; }
# feed: a pretty status line (no timestamp) for the human; mirrored into the runlog.
feed() { printf '%s\n' "$*" | tee -a "$RUNLOG" >&2; }

cfg() { grep -E "^$1:" "$CONFIG_PATH" | head -1 | sed -E "s/^$1:[[:space:]]*//"; }

# Resolve a timeout command (GNU `timeout` or macOS-homebrew `gtimeout`).
# When neither is present, run without a wrapper so the loop still works.
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_CMD=(timeout)
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_CMD=(gtimeout)
else
  TIMEOUT_CMD=()
fi
# -k 30: SIGKILL 30s after the SIGTERM. A `claude` wedged under memory pressure can
# ignore SIGTERM entirely, which is how a "timed out" tick used to keep holding RAM.
run_tick() {
  if [[ ${#TIMEOUT_CMD[@]} -gt 0 ]]; then
    "${TIMEOUT_CMD[@]}" -k 30 "$TICK_TIMEOUT" "$@"
  else
    "$@"
  fi
}
run_medic_cmd() {
  if [[ ${#TIMEOUT_CMD[@]} -gt 0 ]]; then
    "${TIMEOUT_CMD[@]}" -k 30 "$MEDIC_TIMEOUT" "$@"
  else
    "$@"
  fi
}

[[ -f "$CONFIG_PATH" ]] || die "no $CONFIG_PATH found"
WORKTREE="$(cfg Worktree)"
TICK_TIMEOUT="$(cfg Limits | tr ' ' '\n' | grep -E '^tick_timeout=' | head -1 | cut -d= -f2)"
TICK_TIMEOUT="${TICK_TIMEOUT:-1200}"
# Optional orchestrator model alias from LOOP_CONFIG (`Orchestrator model:`). Blank =
# inherit the user's Claude default. The plugin hardcodes no model name; whatever alias
# the human writes in config is the only place a concrete model is named (env override
# wins for one-off runs). See the launch block below for the rationale.
ORCHESTRATOR_MODEL="${LOOP_ORCHESTRATOR_MODEL:-$(cfg 'Orchestrator model')}"
# Sidecar dashboard + medic policy. Config is the durable answer, env is the one-off
# override. Both default to the template's `auto`, which is also the right behaviour
# for a config written before v2 (no such line -> full v2 behaviour).
DASH_MODE="${LOOP_DASHBOARD:-$(cfg Dashboard)}"; DASH_MODE="${DASH_MODE:-auto}"
MEDIC_MODE="$(cfg Medic)"; MEDIC_MODE="${MEDIC_MODE:-auto}"
MEDIC_MODEL="${MEDIC_MODEL:-$(cfg 'Medic model')}"
# Compose the tick prompt once: the static template plus an authoritative tier-override block
# derived from LOOP_CONFIG. The orchestrator is told to read the config itself (§1), but a
# cheaper spine model is less reliable at that step — injecting the resolved tiers here makes
# the per-role overrides bind deterministically. Empty block (no tiers configured) leaves the
# prompt untouched, so every role falls back to the §16 heuristic. Fed via here-string below.
TICK_INPUT="$(cat "$TICK_PROMPT")"
TIER_BLOCK="$(tier_directives_block "$CONFIG_PATH")"
[[ -n "$TIER_BLOCK" ]] && TICK_INPUT+=$'\n'"$TIER_BLOCK"
MAX_WAIT="${MAX_WAIT:-21600}"          # cap on a single usage-limit sleep (s); beyond this, clean-exit. 6h default.
PAUSE_REFRESH="${PAUSE_REFRESH:-30}"   # usage-limit countdown redraw interval (s); env-overridable
NP_MAX="${NP_MAX:-3}"                   # consecutive no-progress execute ticks before halting
RL_FILE="$RUNTIME_DIR/ratelimit.json"  # format_stream writes the tick's last rate_limit_info here; cleared each tick
ACT_FILE="$RUNTIME_DIR/last-activity"  # format_stream stamps every stream line; the stall detector reads it
FIFO="$RUNTIME_DIR/tick.fifo"          # tick stdout -> format_stream (bash 3.2 cannot wait on a process substitution)
RESULT_FILE="$RUNTIME_DIR/tick-result.json"

cwd_real="$(pwd -P)"
wt_real="$(cd "$WORKTREE" 2>/dev/null && pwd -P || echo "$WORKTREE")"
if ! worktree_guard "$wt_real" "$cwd_real"; then
  die "cwd '$cwd_real' != configured Worktree '$wt_real'"
fi

# Ensure the persistent knowledge file exists so the Scout's read never fails. Its parent
# dir (`.claude/loop/`) already exists (it is the parent of $LOOP_DIR), but mkdir -p is
# belt-and-braces. Seed a header-only file if absent; an existing file is left untouched.
if [[ ! -f "$LOOP_KNOWLEDGE" ]]; then
  mkdir -p "$(dirname "$LOOP_KNOWLEDGE")" 2>/dev/null || true
  {
    printf '%s\n' '# Loop Knowledge (persistent, cross-run; repo-scoped; committed)' ''
    printf '%s\n' '## Patterns (durable, cross-run; auto-promoted at loop close)' ''
  } > "$LOOP_KNOWLEDGE" 2>/dev/null || true
fi

if [[ ${#TIMEOUT_CMD[@]} -eq 0 ]]; then
  log "WARNING: no 'timeout'/'gtimeout' on PATH — per-tick wall-clock cap is DISABLED; a stuck tick can run unbounded. Install coreutils (brew install coreutils) to restore it."
fi

# --- process-wide state the traps and incident handlers read -------------------
EXIT_REASON=""      # every deliberate exit path sets this; loop_end carries it
EXIT_DETAIL=""
TICK_PID=""         # in-flight tick (the `timeout` wrapper), cleared when it ends
FMT_PID=""          # in-flight format_stream reader
HB_PID=""           # heartbeat writer
SUP_PID=""          # sidecar supervisor
DASH_URL=""
HARNESS_PID=$$    # the background loops watch this: a SIGKILLed harness runs no trap
gtick=0             # the tick number an incident is attributed to; 0 = before the first tick
medic_count=0       # medic invocations this run (MEDIC_MAX_PER_RUN is a hard ceiling)
HI_ID=""            # last incident id raised by handle_incident
HI_OUTCOME=""       # last medic outcome, when one ran
HI_MEDIC_RAN=0

# --- exclusivity ---------------------------------------------------------------
# The harness owns the mutex. Nothing else — not the tick prompt, not the dashboard —
# may declare a loop live: `runtime/harness.json` + `kill -0` + a `run.sh` command
# check is the only liveness test in the system.
LOCK_OUT="$(harness_lock_acquire "$RUNTIME_DIR" $$ "$LOOP_DIR" "$PLUGIN_VERSION")"
PENDING_CRASH_PID=""
case "$LOCK_OUT" in
  held:*)
    _owner="${LOCK_OUT#held:}"; _ostart="${_owner#*:}"; _owner="${_owner%%:*}"
    _since="$(date -u -r "$_ostart" +%FT%TZ 2>/dev/null || echo "$_ostart")"
    log "another harness already owns $LOOP_DIR: pid $_owner (since $_since). Stop it with 'kill $_owner', or use a different LOOP_DIR."
    # An incident, never loop_end: this events file belongs to the loop that is still
    # RUNNING, and a loop_end in it would make every observer call that healthy loop
    # terminal. The refused starter is a warn-level fact about the live loop, so it
    # shows up in the dashboard's incidents panel and nowhere else.
    incident_new "$RUNTIME_DIR" "$EVENTS" lock-conflict warn \
      "pid $_owner alive since $_since; refused second harness" 0 >/dev/null
    exit 3 ;;
  takeover:*)
    PENDING_CRASH_PID="${LOCK_OUT#takeover:}"
    log "stale harness lock (pid $PENDING_CRASH_PID dead) — taking over" ;;
esac

# --- sidecar dashboard ---------------------------------------------------------
# start_sidecar -> spawns the observer, records its pid, prints the URL it announced.
# Reuses the previous port so an open browser tab's SSE reconnect lands on the same
# origin after a respawn.
start_sidecar() {
  local prev_port url="" waited
  local dash_cmd=()
  prev_port="$(jq -r '.port // 0' "$RUNTIME_DIR/dashboard.json" 2>/dev/null || echo 0)"
  [[ "$prev_port" =~ ^[0-9]+$ ]] || prev_port=0
  if [[ -n "${LOOP_DASHBOARD_CMD:-}" ]]; then
    # Word-split the override deliberately: it is a command line, not a filename.
    # shellcheck disable=SC2206
    dash_cmd=($LOOP_DASHBOARD_CMD)
  else
    dash_cmd=(python3 "$PLUGIN_ROOT/web/serve.py" --sidecar --no-spawn
              --loop-dir "$LOOP_DIR" --port "$prev_port")
  fi
  : > "$RUNTIME_DIR/dashboard.out"
  { exec "${dash_cmd[@]}"; } </dev/null >>"$RUNTIME_DIR/dashboard.out" 2>&1 &
  printf '%s' "$!" > "$RUNTIME_DIR/sidecar.pid"
  # The observer announces itself with a one-line JSON banner; wait up to 5s for it.
  waited=0
  while (( waited < 10 )); do
    url="$(grep -h 'dashboard-started' "$RUNTIME_DIR/dashboard.out" 2>/dev/null \
           | head -1 | jq -r '.url // empty' 2>/dev/null)"
    [[ -n "$url" ]] && break
    sleep 0.5
    waited=$(( waited + 1 ))
  done
  printf '%s' "$url"
}

stop_sidecar() {
  local p
  p="$(cat "$RUNTIME_DIR/sidecar.pid" 2>/dev/null)"
  [[ "$p" =~ ^[0-9]+$ ]] && kill "$p" 2>/dev/null
  rm -f "$RUNTIME_DIR/sidecar.pid" 2>/dev/null || true
  return 0
}

# sidecar_supervise: background watchdog. A dead observer is a warn-level fact, never
# a reason to stop the loop, so the crash-loop ceiling ends in one warn incident and
# silence rather than an endless respawn storm.
sidecar_supervise() {
  local p n
  # The parent check is the whole point of the loop's shape: when the harness is
  # SIGKILLed (the OOM case) no EXIT trap runs, and an orphaned supervisor would
  # keep respawning a dashboard for a loop that no longer exists. Notice, take the
  # sidecar down, and go.
  while kill -0 "$HARNESS_PID" 2>/dev/null; do
    sleep "$HB_INTERVAL"
    kill -0 "$HARNESS_PID" 2>/dev/null || break
    p="$(cat "$RUNTIME_DIR/sidecar.pid" 2>/dev/null)"
    if [[ "$p" =~ ^[0-9]+$ ]] && kill -0 "$p" 2>/dev/null; then continue; fi
    n="$(cat "$RUNTIME_DIR/dash-restarts" 2>/dev/null)"; [[ "$n" =~ ^[0-9]+$ ]] || n=0
    n=$(( n + 1 )); printf '%s' "$n" > "$RUNTIME_DIR/dash-restarts" 2>/dev/null || true
    if (( n > DASH_MAX_RESTARTS )); then
      incident_new "$RUNTIME_DIR" "$EVENTS" dashboard-crashloop warn \
        "sidecar died $n times — not restarting" "$(cat "$TICKSEQ" 2>/dev/null || echo 0)" >/dev/null
      return 0
    fi
    start_sidecar >/dev/null
  done
  stop_sidecar
  return 0
}

# --- teardown ------------------------------------------------------------------
# One exit path for everything: kill what we spawned, narrate why we stopped, drop
# the lock. loop_end is the ONLY thing that makes a state terminal for an observer,
# so it must be emitted on every exit — including a signal.
on_exit() {
  local rc=$?
  trap - EXIT
  [[ -n "$TICK_PID" ]] && kill "$TICK_PID" 2>/dev/null
  [[ -n "$FMT_PID" ]] && kill "$FMT_PID" 2>/dev/null
  [[ -n "$SUP_PID" ]] && kill "$SUP_PID" 2>/dev/null
  stop_sidecar
  [[ -n "$HB_PID" ]] && kill "$HB_PID" 2>/dev/null
  emit_event "$EVENTS" loop_end reason "${EXIT_REASON:-error}" detail "${EXIT_DETAIL:-}" exit_code "$rc"
  harness_lock_release "$RUNTIME_DIR" $$
  rm -f "$RUNTIME_DIR/tick.json" "$FIFO" 2>/dev/null || true
  exit "$rc"
}
trap 'on_exit' EXIT
trap 'EXIT_REASON="signal"; exit 130' INT TERM

# --- incidents and the medic ---------------------------------------------------
# escalate: the loop cannot continue and no automation is going to fix it. Leave the
# human one file that says what broke and the exact command to resume.
escalate() {
  local kind="$1" detail="$2" id="${3:-}"
  local summary="" next=""
  local mf="$RUNTIME_DIR/medic-$id.json"
  if [[ -n "$id" && -f "$mf" ]]; then
    summary="$(jq -r '.summary // ""' "$mf" 2>/dev/null)"
    next="$(jq -r '.human_next_step // ""' "$mf" 2>/dev/null)"
  fi
  {
    printf '%s\n\n' '# agent-loop needs a human'
    printf -- '- incident: %s (%s)\n' "${id:-none}" "$kind"
    printf -- '- detail: %s\n' "$detail"
    printf -- '- tick: %s\n' "$gtick"
    printf -- '- when: %s\n' "$(date -u +%FT%TZ)"
    [[ -n "$summary" ]] && printf -- '- medic summary: %s\n' "$summary"
    [[ -n "$next" ]] && printf -- '- medic next step: %s\n' "$next"
    printf '\n%s\n\n' '## Resume'
    printf '    cd %s && LOOP_DIR=%s bash "%s"\n' "$WORKTREE" "$LOOP_DIR" "$PLUGIN_ROOT/run.sh"
  } > "$RUNTIME_DIR/NEEDS_HUMAN.md" 2>/dev/null || true
  incident_new "$RUNTIME_DIR" "$EVENTS" "$kind" needs-human "$detail" "$gtick" >/dev/null
  notify_desktop "agent-loop needs you" "$kind: $detail"
  log "NEEDS HUMAN: $kind — $detail (see $RUNTIME_DIR/NEEDS_HUMAN.md)"
  EXIT_REASON="needs-human"
  EXIT_DETAIL="$kind: $detail"
  exit 2
}

# run_medic <id> -> 0 iff the medic reported `resumed`.
# Runs only between ticks, never alongside one, and only inside its own timeout.
run_medic() {
  local id="$1" outcome summary
  local mf="$RUNTIME_DIR/medic-$id.json"
  local mcmd=()
  HI_MEDIC_RAN=1
  medic_count=$(( medic_count + 1 ))
  rm -f "$mf" 2>/dev/null || true
  emit_event "$EVENTS" medic_start id "$id"
  log "medic $id starting ($medic_count/$MEDIC_MAX_PER_RUN this run)"
  if [[ -n "${LOOP_MEDIC_CMD:-}" ]]; then
    # shellcheck disable=SC2206
    mcmd=($LOOP_MEDIC_CMD)
  else
    mcmd=(claude --print --dangerously-skip-permissions)
    [[ -n "$MEDIC_MODEL" ]] && mcmd+=(--model "$MEDIC_MODEL")
  fi
  mcmd+=("/agent-loop-medic $id")
  # LOOP_DIR/RUNTIME_DIR are already exported; the id also travels as an env var so a
  # LOOP_MEDIC_CMD override does not have to parse it back out of the prompt.
  export MEDIC_INCIDENT_ID="$id"
  run_medic_cmd "${mcmd[@]}" </dev/null >>"$RUNLOG" 2>&1 || true
  unset MEDIC_INCIDENT_ID
  # No file, an unparseable file, or a timeout all mean the same thing: the medic did
  # not conclude it fixed anything, so treat it as an escalation, never as success.
  outcome="$(jq -r '.outcome // empty' "$mf" 2>/dev/null)"
  case "$outcome" in resumed|paused|escalated|noop) ;; *) outcome=escalated ;; esac
  summary="$(jq -r '.summary // ""' "$mf" 2>/dev/null)"
  emit_event "$EVENTS" medic_end id "$id" outcome "$outcome" summary "$summary"
  HI_OUTCOME="$outcome"
  feed "◍ medic $id → $outcome${summary:+ · $summary}"
  [[ "$outcome" == "resumed" ]]
}

# handle_incident <kind> <severity> <detail> -> 0 = the loop may continue.
# warn never stops the loop. error runs the medic when the budget allows and the
# policy is `auto`; anything short of a `resumed` verdict is the caller's cue to
# escalate. Sets HI_ID / HI_OUTCOME / HI_MEDIC_RAN for the caller.
handle_incident() {
  local kind="$1" sev="$2" detail="$3"
  HI_OUTCOME=""; HI_MEDIC_RAN=0
  HI_ID="$(incident_new "$RUNTIME_DIR" "$EVENTS" "$kind" "$sev" "$detail" "$gtick")"
  feed "⚠ incident $HI_ID · $kind · $detail"
  case "$MEDIC_MODE" in
    auto|notify) notify_desktop "agent-loop: $kind" "$detail" ;;
  esac
  [[ "$sev" == "warn" ]] && return 0
  if [[ "$MEDIC_MODE" == "auto" ]] && (( medic_count < MEDIC_MAX_PER_RUN )); then
    run_medic "$HI_ID" && return 0
    return 1
  fi
  [[ "$MEDIC_MODE" == "auto" ]] && log "medic budget exhausted ($MEDIC_MAX_PER_RUN) — escalating $kind"
  return 1
}

# --- observability start-up ----------------------------------------------------
heartbeat_loop "$RUNTIME_DIR" "$HB_INTERVAL" "$HARNESS_PID" &
HB_PID=$!

# One dashboard per loop dir. A live standalone dashboard (the one /agent-loop opened,
# or the one whose Start button spawned us) is ADOPTED: announce its URL, spawn nothing,
# supervise nothing, and leave it running when we exit — it outlives any one harness.
# Adoption ignores Dashboard/LOOP_DASHBOARD: those only decide whether to spawn.
DASH_ADOPTED=0
if _adopt_url="$(dashboard_adoptable "$RUNTIME_DIR")"; then
  DASH_URL="$_adopt_url"; DASH_ADOPTED=1
  feed "◉ dashboard $DASH_URL (adopted)"
elif [[ "$DASH_MODE" == "auto" ]]; then
  DASH_URL="$(start_sidecar)"
  if [[ -n "$DASH_URL" ]]; then
    feed "◉ dashboard $DASH_URL"
  else
    log "dashboard sidecar did not announce a URL within 5s — see $RUNTIME_DIR/dashboard.out"
  fi
  sidecar_supervise &
  SUP_PID=$!
fi
export DASH_ADOPTED
if [[ -n "$DASH_URL" && "${LOOP_DASHBOARD_OPEN:-0}" == "1" ]]; then
  case "$(uname -s 2>/dev/null)" in
    Darwin) open "$DASH_URL" >/dev/null 2>&1 || true ;;
    Linux)  xdg-open "$DASH_URL" >/dev/null 2>&1 || true ;;
  esac
fi

# resume = this LOOP_DIR has already run at least one tick (the counter persists).
resume=0
_seq0="$(cat "$TICKSEQ" 2>/dev/null)"
[[ "$_seq0" =~ ^[0-9]+$ ]] && (( _seq0 > 0 )) && resume=1
if [[ -n "$DASH_URL" ]]; then
  emit_event "$EVENTS" loop_start pid $$ host "$(hostname 2>/dev/null || echo unknown)" \
    plugin_version "$PLUGIN_VERSION" resume "$resume" dashboard_url "$DASH_URL"
else
  emit_event "$EVENTS" loop_start pid $$ host "$(hostname 2>/dev/null || echo unknown)" \
    plugin_version "$PLUGIN_VERSION" resume "$resume"
fi

# --- loop-dir schema ------------------------------------------------------------
# runtime/schema is the layout stamp and the harness is its only writer. A dir from
# before stamping (tick counter, no stamp) is schema 1 and is migrated in place, once,
# with a `migration` event marking the boundary in the timeline. Nothing here touches
# LOOP_CONFIG or LOOP_PLAN. A refusal is a human job, never the medic's: pause the
# old harness, or update the plugin — so both go straight to escalate (exit 2).
MIG_OUT="$(migrate_loop_dir "$LOOP_DIR" "$RUNTIME_DIR" "$EVENTS" "$LOOP_SCHEMA" \
  "$LEGACY_LIVE" "${LOOP_MIGRATE_FORCE:-0}" "$PLUGIN_VERSION")"
case "$MIG_OUT" in
  migrated:*)
    _m="${MIG_OUT#migrated:}"
    log "loop dir migrated: schema ${_m%%:*} -> $LOOP_SCHEMA (${_m##*:})"
    feed "⇡ schema ${_m%%:*} → $LOOP_SCHEMA · ${_m##*:}" ;;
  blocked:*)
    escalate migration-blocked "${MIG_OUT#blocked:}" ;;
  newer:*)
    escalate schema-newer "this loop dir is schema ${MIG_OUT#newer:} but plugin v$PLUGIN_VERSION only knows schema $LOOP_SCHEMA — update the plugin on this machine, then re-run" ;;
esac

# A stale lock means the previous harness died mid-tick: some task is probably [~]
# and some runtime file half-written. That is a repair job, and it is the medic's.
# With `Medic: auto` the medic reconciles orphans and only a failed repair escalates.
# Without a medic there is nothing to run and nothing a human must do before the next
# tick — tick-prompt §2 re-evaluates an orphaned `[~]` itself — so the crash is recorded
# as a warning and the loop simply continues. Escalating here would make every relaunch
# after an OOM kill exit 2 for no reason other than acknowledgement.
if [[ -n "$PENDING_CRASH_PID" ]]; then
  if [[ "$MEDIC_MODE" == "auto" ]]; then
    handle_incident harness-crash error "previous harness pid $PENDING_CRASH_PID died without cleanup" \
      || escalate harness-crash "previous harness pid $PENDING_CRASH_PID died without cleanup" "$HI_ID"
  else
    handle_incident harness-crash warn "previous harness pid $PENDING_CRASH_PID died without cleanup; next tick re-evaluates any [~] task" || true
  fi
fi

iter=0
fail_streak=0
rl_streak=0
no_progress=0
rem_prev="$(remaining_tasks "$PLAN_PATH")"
loop_start_epoch="$(date +%s)"   # wall-clock anchor for elapsed/ETA (resets on a fresh run.sh)
tick_lines=()              # permanent per-tick summary lines, tailed into $STATUS_FILE

while true; do
  if [[ -f "$RUNTIME_DIR/PAUSE" ]]; then
    log "PAUSE present; writing checkpoint and exiting cleanly"
    nexttask="$(grep -oE 'T[0-9]+' <(grep -E '^[[:space:]]*- \[[ ~]\] ' "$PLAN_PATH" 2>/dev/null | head -1) | head -1)"
    seg="$(grep -E '^## ' "$PLAN_PATH" 2>/dev/null | tail -1 | sed -E 's/^##[[:space:]]*//')"
    jq -cn --argjson t "$(date +%s)" --arg task "${nexttask:-?}" --arg seg "${seg:-?}" \
       --argjson tick "$(cat "$TICKSEQ" 2>/dev/null || echo 0)" \
       '{t:$t, stopped_after_tick:$tick, next_task:$task, segment:$seg, note:"resume: re-run the launch command or click Resume"}' \
       > "$RUNTIME_DIR/CHECKPOINT.json" 2>/dev/null || true
    emit_event "$EVENTS" paused tick "$(cat "$TICKSEQ" 2>/dev/null || echo 0)"
    EXIT_REASON="paused"
    exit 0
  fi

  iter=$((iter + 1))
  gtick="$(tick_seq_next "$TICKSEQ")"   # continuous across resumes; the displayed/emitted tick #

  # --- pre-tick memory guard (advisory; never halts) ---------------------------
  # A tick is the single biggest memory consumer on the box. Starting one while the
  # machine is already paging is how ticks get SIGKILLed halfway through a sprint.
  # Wait a few rounds for headroom, then go anyway: refusing to work is worse than
  # risking a retry, and the memory_pressure/sleep events make the wait legible
  # ("waiting for memory headroom", not "stalled").
  mem_delays=0
  while true; do
    mem_now="$(mem_headroom)"
    free_mb="${mem_now%% *}"; swap_pct="${mem_now##* }"
    if [[ "$(mem_guard_action "$free_mb" "$swap_pct" "$MEM_MIN_MB" "$SWAP_MAX_PCT")" == "proceed" ]]; then
      break
    fi
    if (( mem_delays >= MEM_MAX_DELAYS )); then
      emit_event "$EVENTS" memory_pressure free_mb "$free_mb" swap_used_pct "$swap_pct" action proceed
      feed "⚠ low memory headroom (${free_mb}MB free, swap ${swap_pct}%) — starting tick $gtick anyway after $mem_delays delays"
      break
    fi
    emit_event "$EVENTS" memory_pressure free_mb "$free_mb" swap_used_pct "$swap_pct" action delay
    mem_delays=$(( mem_delays + 1 ))
    emit_event "$EVENTS" sleep tick "$gtick" until "$(( $(date +%s) + MEM_BACKOFF ))" reason memory
    feed "◌ waiting for memory headroom (${free_mb}MB free, swap ${swap_pct}%) — retry in ${MEM_BACKOFF}s"
    sleep "$MEM_BACKOFF"
  done

  log "tick $gtick starting"
  t0="$(date +%s)"
  head0="$(git rev-parse HEAD 2>/dev/null || echo none)"
  : > "$RL_FILE"
  date +%s > "$ACT_FILE"
  # Orchestrator model (optional). The tick process is a coordination + verification
  # spine: it reads state, runs the verification pipeline, and dispatches the
  # thinking-heavy roles (Planner, Worker, Evaluator) as subagents that pick their own
  # tier (tick-prompt §16). A standard-tier model is plenty for the spine — the heavy
  # reasoning is delegated. Blank ORCHESTRATOR_MODEL inherits the user's default; a
  # non-empty alias pins it. Built as an array so the empty case is safe under set -u.
  claude_cmd=(claude --print --dangerously-skip-permissions
              --output-format stream-json --verbose --fallback-model sonnet)
  [[ -n "$ORCHESTRATOR_MODEL" ]] && claude_cmd+=(--model "$ORCHESTRATOR_MODEL")

  # Launch through a FIFO rather than a pipeline: bash 3.2 cannot `wait` on a process
  # substitution, and the harness needs the tick's real PID — to record it in
  # tick.json for observers, to poll it for stalls, and to kill it from the EXIT trap.
  rm -f "$FIFO"; mkfifo "$FIFO" 2>/dev/null || true
  : > "$RESULT_FILE"
  { format_stream "$RUNLOG" "$RL_FILE" "$gtick" "$EVENTS" "$ACT_FILE"; } \
    < "$FIFO" > "$RESULT_FILE" &
  FMT_PID=$!
  # SC2094: both writers append-only to $RUNLOG (claude stderr + raw stream); no read, safe to interleave.
  # shellcheck disable=SC2094
  { run_tick "${claude_cmd[@]}"; } <<<"$TICK_INPUT" > "$FIFO" 2>>"$RUNLOG" &
  TICK_PID=$!
  write_tick_json "$RUNTIME_DIR" "$gtick" "$TICK_PID" "$t0" "$TICK_TIMEOUT"
  emit_event "$EVENTS" tick_start tick "$gtick" pid "$TICK_PID" timeout_at "$(( t0 + TICK_TIMEOUT ))"

  # Poll instead of blocking in `wait`: the stall detector has to run WHILE the tick
  # runs. A tick that has emitted nothing for STALL_S is wedged — the tick cannot
  # report that about itself, and tick_timeout is too blunt to be the only answer.
  stall_flagged=0
  polls=0
  stall_every=$(( HB_INTERVAL )); (( stall_every < 1 )) && stall_every=1
  while kill -0 "$TICK_PID" 2>/dev/null; do
    sleep "$TICK_POLL"
    polls=$(( polls + 1 ))
    if (( stall_flagged == 0 && polls % stall_every == 0 )); then
      last_act="$(cat "$ACT_FILE" 2>/dev/null)"; [[ "$last_act" =~ ^[0-9]+$ ]] || last_act="$t0"
      quiet=$(( $(date +%s) - last_act ))
      if (( quiet > STALL_S )); then
        stall_flagged=1
        handle_incident tick-stalled warn "no stream activity for ${quiet}s" || true
      fi
    fi
  done
  wait "$TICK_PID"; rc=$?
  TICK_PID=""
  wait "$FMT_PID" 2>/dev/null
  FMT_PID=""
  out="$(cat "$RESULT_FILE" 2>/dev/null)"
  rm -f "$RUNTIME_DIR/tick.json" "$FIFO" 2>/dev/null || true
  t1="$(date +%s)"

  text="$(printf '%s' "$out" | jq -r '.result // ""' 2>/dev/null)"
  mode="$(detect_mode "$text")"
  sent_out="$(parse_sentinel "$text")"
  sentinel="$(printf '%s' "$sent_out" | head -1)"

  if printf '%s' "$out" | jq -e . >/dev/null 2>&1; then
    append_usage "$LEDGER" "$iter" "$mode" "$out" "$((t1 - t0))"
  fi

  verdict="$(classify_result "$rc" "$out" "$sentinel")"
  # Why the tick ended, as distinct from what to do next: 137 (OOM kill) and a model
  # that simply produced no sentinel are both "retry", and telling them apart is the
  # whole point of the incident machinery below.
  cause="$(classify_cause "$rc" "$out" "$sentinel")"

  # Per-tick close-out event with a per-model usage rollup (the dashboard aggregates these).
  bm="$(printf '%s' "$out" | jq -c '(.modelUsage // {}) | with_entries(.value |= {
          cost_usd:(.costUSD//0), input_tokens:(.inputTokens//0), output_tokens:(.outputTokens//0),
          cache_read_tokens:(.cacheReadInputTokens//0), cache_creation_tokens:(.cacheCreationInputTokens//0)})' 2>/dev/null)"
  [[ -z "$bm" || "$bm" == "null" ]] && bm='{}'
  emit_event "$EVENTS" tick_end tick "$gtick" verdict "$verdict" cause "$cause" rc "$rc" \
    dur "$((t1 - t0))" by_model "$bm"

  # --- glanceable status: session header + permanent per-tick summary ---
  head1="$(git rev-parse HEAD 2>/dev/null || echo none)"
  if [[ "$head1" != "$head0" && "$head1" != none ]]; then
    sha="${head1:0:7}"
    gates="$(gates_compact "$(git log -1 --format=%b 2>/dev/null | grep -i '^Loop-Verification:' | head -1)")"
    task="$(git log -1 --format=%s 2>/dev/null | grep -oE 'T[0-9]+' | head -1)"
    emit_event "$EVENTS" task_status id "${task:-?}" status "done" sha "$sha"
  else
    sha=""; gates=""; task=""
  fi
  total="$(total_tasks "$PLAN_PATH")"
  done="$(done_tasks "$PLAN_PATH")"
  avg="$(usage_avg_dur "$LEDGER")"
  eta="$(eta_secs "$(remaining_tasks "$PLAN_PATH")" "$avg")"
  # persist the last usage-window utilization Claude reported (only present near the warning threshold)
  if [[ -s "$RL_FILE" ]] && jq -e '.utilization' "$RL_FILE" >/dev/null 2>&1; then
    cp "$RL_FILE" "$RUNTIME_DIR/plan-usage.json" 2>/dev/null || true
  fi
  plan="$(plan_usage "$(cat "$RUNTIME_DIR/plan-usage.json" 2>/dev/null || true)" "$(date +%s)")"
  # Segment-weighted header while any segment is still unplanned: "48/48 tasks" is not
  # progress when nine segments have yet to be written.
  seg_total="$(segments_total "$PLAN_PATH")"
  seg_done="$(segments_done "$PLAN_PATH")"
  if [[ "$(segments_unplanned "$PLAN_PATH")" -gt 0 ]]; then
    header="$(session_header "$done" "$total" "$(( $(date +%s) - loop_start_epoch ))" "$eta" "$plan" "$seg_done" "$seg_total")"
  else
    header="$(session_header "$done" "$total" "$(( $(date +%s) - loop_start_epoch ))" "$eta" "$plan")"
  fi
  disp="$verdict"; [[ "$verdict" == continue && "$mode" == plan ]] && disp=plan
  [[ "$verdict" == continue && "$mode" == review ]] && disp=review
  summary="$(tick_line "$disp" "$gtick" "$task" "$((t1 - t0))" "$gates" "$sha" "$done" "$total" "$cause")"
  tick_lines+=("$summary")
  feed "$header"
  feed "$summary"
  # tail the last 10 lines: positive offset clamped to 0 (negative offset errors under set -u when <10)
  status_start=$(( ${#tick_lines[@]} > 10 ? ${#tick_lines[@]} - 10 : 0 ))
  { printf '%s\n\n' "$header"; printf '%s\n' "${tick_lines[@]:status_start}"; } > "$STATUS_FILE" 2>/dev/null || true

  # Plan-size guard (advisory): the plan is re-primed into every subagent dispatch,
  # so unchecked growth is the dominant cache-read cost. Warn, never halt.
  if plan_chars="$(plan_oversize_chars "$PLAN_PATH")" && [[ "$plan_chars" -gt "$PLAN_CHAR_BUDGET" ]]; then
    emit_event "$EVENTS" plan_oversize tick "$gtick" chars "$plan_chars" budget "$PLAN_CHAR_BUDGET"
    feed "⚠ plan is ${plan_chars}c (> ${PLAN_CHAR_BUDGET}c budget) — rows may be carrying recon; trim to one-liners"
  fi

  # --- environment kills: raise before deciding what to do next -----------------
  # These are not model failures. Classifying them as incidents is what lets the
  # medic (or a human) see "the OS killed the tick" instead of "the model misbehaved".
  case "$cause" in
    killed)
      handle_incident tick-killed error "rc=$rc" \
        || escalate tick-killed "rc=$rc (tick $gtick killed by SIGKILL)" "$HI_ID" ;;
    timeout)
      handle_incident tick-timeout error "rc=$rc after ${TICK_TIMEOUT}s" \
        || escalate tick-timeout "rc=$rc (tick $gtick exceeded ${TICK_TIMEOUT}s)" "$HI_ID" ;;
  esac

  case "$verdict" in
    done)
      log "LOOP_DONE after $gtick ticks"
      [[ "${AGENT_LOOP_SKIP_POSTMORTEM:-0}" == "1" ]] || \
        claude --print --dangerously-skip-permissions "/agent-loop-postmortem" </dev/null >>"$RUNLOG" 2>&1 || true
      EXIT_REASON="done"
      exit 0 ;;
    halt)
      # A halt sentinel is a human decision by construction: the tick has already
      # concluded it cannot proceed. Record it, notify, let a medic summarise if one
      # is configured — but the exit code stays 1 (halt), not 2 (needs-human), unless
      # a medic actually ran and failed to clear it.
      halt_reason="$(printf '%s' "$sent_out" | sed -n 2p)"
      handle_incident halt-sentinel error "$halt_reason" || true
      if (( HI_MEDIC_RAN == 1 )) && [[ "$HI_OUTCOME" != "resumed" ]]; then
        escalate halt-sentinel "$halt_reason" "$HI_ID"
      fi
      EXIT_REASON="halt"
      EXIT_DETAIL="$halt_reason"
      log "HALT: tick requested halt: $halt_reason"
      exit 1 ;;
    continue)
      fail_streak=0
      rl_streak=0
      rem_now="$(remaining_tasks "$PLAN_PATH")"
      if [[ "$mode" == "plan" || "$mode" == "review" || "$rem_now" != "$rem_prev" ]]; then
        no_progress=0
      else
        no_progress=$((no_progress + 1))
        if [[ "$no_progress" -ge "$NP_MAX" ]]; then
          np_detail="no progress in $NP_MAX consecutive execute ticks (remaining stuck at $rem_now)"
          log "$np_detail — possible loop"
          handle_incident no-progress error "$np_detail" || escalate no-progress "$np_detail" "$HI_ID"
          no_progress=0
        fi
      fi
      rem_prev="$rem_now"
      emit_event "$EVENTS" sleep tick "$gtick" until "$(( $(date +%s) + PAUSE_BETWEEN ))" reason between-ticks
      sleep "$PAUSE_BETWEEN" ;;
    retry)
      # 1) Usage-window throttle (verified via rate_limit_event): wait until reset, or clean-exit.
      #    By design there is no aggregate wait ceiling — the usage window IS the only ceiling, so
      #    repeated within-MAX_WAIT resets keep waiting+resuming (a polite poll, min 5s, never a busy-spin).
      rl_info="$(cat "$RL_FILE" 2>/dev/null || true)"
      action="$(ratelimit_action "$rl_info" "$(date +%s)" "$MAX_WAIT")"
      case "$action" in
        wait\ *)
          secs="${action#wait }"
          reset_epoch=$(( $(date +%s) + secs ))
          log "usage limit hit — sleeping ${secs}s until window reset ($(date -u -r "$reset_epoch" +%FT%TZ)), then resuming"
          emit_event "$EVENTS" sleep tick "$gtick" until "$reset_epoch" reason rate-limit
          # Live countdown: rewrite STATUS_FILE + redraw the TTY heartbeat until the window
          # resets, instead of a silent blocking sleep. $header / $status_start / $tick_lines
          # are in scope from the status block above.
          pause_countdown "$reset_epoch" "$PAUSE_REFRESH" "$STATUS_FILE" "$header" \
            "$(printf '%s\n' "${tick_lines[@]:status_start}")"
          feed "▶ resumed · running tick $((gtick + 1))"
          log "usage window reset — resuming tick $((gtick + 1))"
          continue ;;
        exit)
          log "usage limit hit — raw rate_limit_info: ${rl_info:-<none>}"
          log "state saved on disk; re-run run.sh after your usage window resets to resume"
          EXIT_REASON="rate-limit-exit"
          exit 0 ;;
      esac
      # 2) Transient 429 with no usable window info: short capped backoff.
      if is_rate_limit "$out"; then
        rl_streak=$((rl_streak + 1))
        if [[ "$rl_streak" -ge "$RL_MAX_STRIKES" ]]; then
          log "rate-limited ${rl_streak}x with no reset info — state saved; re-run run.sh later to resume"
          EXIT_REASON="rate-limit-exit"
          exit 0
        fi
        log "tick $gtick rate-limited (429); waiting ${RL_BACKOFF}s (rl streak $rl_streak)"
        emit_event "$EVENTS" sleep tick "$gtick" until "$(( $(date +%s) + RL_BACKOFF ))" reason rate-limit
        sleep "$RL_BACKOFF"
      else
        # 3) Genuine failure/garbage tick.
        fail_streak=$((fail_streak + 1))
        if [[ "$fail_streak" -ge 3 ]]; then
          gt_detail="3 consecutive failed/garbage ticks (last cause: $cause, rc=$rc)"
          log "$gt_detail"
          handle_incident garbage-ticks error "$gt_detail" || escalate garbage-ticks "$gt_detail" "$HI_ID"
          fail_streak=0
        fi
        d="$(backoff_delay "$((fail_streak - 1))")"
        (( d < 1 )) && d=1
        log "tick $gtick failed (rc=$rc, cause=$cause); backoff ${d}s (streak $fail_streak)"
        emit_event "$EVENTS" sleep tick "$gtick" until "$(( $(date +%s) + d ))" reason backoff
        sleep "$d"
      fi
      ;;
  esac
done
