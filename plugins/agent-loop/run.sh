#!/usr/bin/env bash
set -uo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$PLUGIN_ROOT/lib/loop.sh"

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

# Ensure the base dir + runtime subdir exist before anything writes into them.
mkdir -p "$RUNTIME_DIR"
# Each tick is a fresh subprocess; export the base dir so the tick prompt can
# resolve all artefact paths relative to it. LOOP_KNOWLEDGE is exported too so the
# tick's Scout can read the persistent cross-run knowledge file.
export LOOP_DIR RUNTIME_DIR LOOP_KNOWLEDGE EVENTS

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$RUNLOG" >&2; }
die() { log "HALT: $*"; exit 1; }
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
run_tick() {
  if [[ ${#TIMEOUT_CMD[@]} -gt 0 ]]; then
    "${TIMEOUT_CMD[@]}" "$TICK_TIMEOUT" "$@"
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

iter=0
fail_streak=0
rl_streak=0
no_progress=0
rem_prev="$(remaining_tasks "$PLAN_PATH")"
loop_start="$(date +%s)"   # wall-clock anchor for elapsed/ETA (resets on a fresh run.sh)
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
    exit 0
  fi

  iter=$((iter + 1))
  gtick="$(tick_seq_next "$TICKSEQ")"   # continuous across resumes; the displayed/emitted tick #
  emit_event "$EVENTS" tick_start tick "$gtick"
  log "tick $gtick starting"
  t0="$(date +%s)"
  head0="$(git rev-parse HEAD 2>/dev/null || echo none)"
  : > "$RL_FILE"
  # Orchestrator model (optional). The tick process is a coordination + verification
  # spine: it reads state, runs the verification pipeline, and dispatches the
  # thinking-heavy roles (Planner, Worker, Evaluator) as subagents that pick their own
  # tier (tick-prompt §16). A standard-tier model is plenty for the spine — the heavy
  # reasoning is delegated. Blank ORCHESTRATOR_MODEL inherits the user's default; a
  # non-empty alias pins it. Built as an array so the empty case is safe under set -u.
  claude_cmd=(claude --print --dangerously-skip-permissions
              --output-format stream-json --verbose --fallback-model sonnet)
  [[ -n "$ORCHESTRATOR_MODEL" ]] && claude_cmd+=(--model "$ORCHESTRATOR_MODEL")
  # SC2094: both writers append-only to $RUNLOG (claude stderr + raw stream); no read, safe to interleave.
  # shellcheck disable=SC2094
  out="$(run_tick "${claude_cmd[@]}" \
          <<<"$TICK_INPUT" 2>>"$RUNLOG" \
        | format_stream "$RUNLOG" "$RL_FILE" "$gtick" "$EVENTS")"
  rc="${PIPESTATUS[0]}"
  t1="$(date +%s)"

  text="$(printf '%s' "$out" | jq -r '.result // ""' 2>/dev/null)"
  mode="$(detect_mode "$text")"
  sent_out="$(parse_sentinel "$text")"
  sentinel="$(printf '%s' "$sent_out" | head -1)"

  if printf '%s' "$out" | jq -e . >/dev/null 2>&1; then
    append_usage "$LEDGER" "$iter" "$mode" "$out" "$((t1 - t0))"
  fi

  verdict="$(classify_result "$rc" "$out" "$sentinel")"

  # Per-tick close-out event with a per-model usage rollup (the dashboard aggregates these).
  bm="$(printf '%s' "$out" | jq -c '(.modelUsage // {}) | with_entries(.value |= {
          cost_usd:(.costUSD//0), input_tokens:(.inputTokens//0), output_tokens:(.outputTokens//0),
          cache_read_tokens:(.cacheReadInputTokens//0), cache_creation_tokens:(.cacheCreationInputTokens//0)})' 2>/dev/null)"
  [[ -z "$bm" || "$bm" == "null" ]] && bm='{}'
  emit_event "$EVENTS" tick_end tick "$gtick" verdict "$verdict" dur "$((t1 - t0))" by_model "$bm"

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
  header="$(session_header "$done" "$total" "$(( $(date +%s) - loop_start ))" "$eta" "$plan")"
  disp="$verdict"; [[ "$verdict" == continue && "$mode" == plan ]] && disp=plan
  [[ "$verdict" == continue && "$mode" == review ]] && disp=review
  summary="$(tick_line "$disp" "$gtick" "$task" "$((t1 - t0))" "$gates" "$sha" "$done" "$total")"
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

  case "$verdict" in
    done)
      log "LOOP_DONE after $gtick ticks"
      [[ "${AGENT_LOOP_SKIP_POSTMORTEM:-0}" == "1" ]] || \
        claude --print --dangerously-skip-permissions "/agent-loop-postmortem" >>"$RUNLOG" 2>&1 || true
      exit 0 ;;
    halt)
      die "tick requested halt: $(printf '%s' "$sent_out" | sed -n 2p)" ;;
    continue)
      fail_streak=0
      rl_streak=0
      rem_now="$(remaining_tasks "$PLAN_PATH")"
      if [[ "$mode" == "plan" || "$mode" == "review" || "$rem_now" != "$rem_prev" ]]; then
        no_progress=0
      else
        no_progress=$((no_progress + 1))
        if [[ "$no_progress" -ge "$NP_MAX" ]]; then
          die "no progress in $NP_MAX consecutive execute ticks (remaining stuck at $rem_now) — possible loop"
        fi
      fi
      rem_prev="$rem_now"
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
          exit 0 ;;
      esac
      # 2) Transient 429 with no usable window info: short capped backoff.
      if is_rate_limit "$out"; then
        rl_streak=$((rl_streak + 1))
        if [[ "$rl_streak" -ge "$RL_MAX_STRIKES" ]]; then
          log "rate-limited ${rl_streak}x with no reset info — state saved; re-run run.sh later to resume"
          exit 0
        fi
        log "tick $gtick rate-limited (429); waiting ${RL_BACKOFF}s (rl streak $rl_streak)"
        sleep "$RL_BACKOFF"
      else
        # 3) Genuine failure/garbage tick.
        fail_streak=$((fail_streak + 1))
        if [[ "$fail_streak" -ge 3 ]]; then
          die "3 consecutive failed/garbage ticks; aborting for human review"
        fi
        d="$(backoff_delay "$((fail_streak - 1))")"
        log "tick $gtick failed (rc=$rc); backoff ${d}s (streak $fail_streak)"
        sleep "$d"
      fi
      ;;
  esac
done
