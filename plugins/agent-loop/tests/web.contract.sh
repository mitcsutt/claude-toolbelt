#!/usr/bin/env bash
# Smoke test: server serves /, /api/state matches the snapshot contract,
# control toggles PAUSE, and start refuses to race a live harness.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "(web.contract.sh skipped — python3 not installed)"; exit 0
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "(web.contract.sh skipped — curl not installed)"; exit 0
fi

tmp="$(mktemp -d)"
mkdir -p "$tmp/runtime"
printf '# Loop Config\nWorktree: %s\nDashboard: auto\nMedic: notify\n' "$tmp" > "$tmp/LOOP_CONFIG.md"
printf '# Loop Plan\n## Segment A: x\n- [ ] T1: a\n' > "$tmp/LOOP_PLAN.md"

# --no-spawn: observe only, never launch a real claude loop in the test.
# -u: unbuffered stdout, so the startup JSON line lands in $tmp/out promptly
# even though stdout is redirected to a file (fully buffered by default).
LOOP_DIR="$tmp" python3 -u "$ROOT/web/serve.py" --loop-dir "$tmp" --no-spawn >"$tmp/out" 2>"$tmp/err" &
srv=$!
fake=""
det=""
cleanup() {
  kill "$srv" 2>/dev/null
  [[ -n "$fake" ]] && kill "$fake" 2>/dev/null
  [[ -n "${det:-}" ]] && kill "$det" 2>/dev/null
  return 0
}
trap cleanup EXIT

# Wait for the startup JSON (which carries the chosen port) to appear.
url=""
for _ in $(seq 1 50); do
  url="$(grep -o '"url": *"[^"]*"' "$tmp/out" 2>/dev/null | head -1 | sed 's/.*"\(http[^"]*\)"/\1/')"
  [[ -n "$url" ]] && break
  sleep 0.1
done
[[ -n "$url" ]] || { echo "FAIL: server did not print a URL"; cat "$tmp/err"; exit 1; }

fail=0
# assert_true RC MSG — record a pass/fail line; flip $fail on non-zero RC.
assert_true() {
  if [[ "$1" -eq 0 ]]; then echo "ok: $2"; else echo "FAIL: $2"; fail=1; fi
}

# 1) / serves HTML
code="$(curl -s -o /dev/null -w '%{http_code}' "$url/")"
[[ "$code" == "200" ]] || { echo "FAIL: GET / => $code"; fail=1; }

# 2) /api/state is valid JSON with the expected top-level keys
state="$(curl -s "$url/api/state")"
echo "$state" | python3 -c 'import sys,json; d=json.load(sys.stdin); assert "progress" in d and "current" in d and "loop" in d; print("state-ok")' \
  || { echo "FAIL: /api/state not valid/complete: $state"; fail=1; }

# 3) POST /api/pause creates runtime/PAUSE; /api/resume removes it
curl -s -X POST "$url/api/pause" >/dev/null
[[ -f "$tmp/runtime/PAUSE" ]] || { echo "FAIL: pause did not create PAUSE"; fail=1; }
curl -s -X POST "$url/api/resume" >/dev/null
[[ ! -f "$tmp/runtime/PAUSE" ]] || { echo "FAIL: resume did not remove PAUSE"; fail=1; }

# 4) dashboard markup carries the contract element ids
curl -s "$url/" -o "$tmp/page.html"
for id in status why health timeline roadmap incidents usage log \
          btnStart btnPause btnResume btnStop tickNo; do
  grep -q "id=\"$id\"" "$tmp/page.html"; assert_true $? "element id=\"$id\" present"
done

# 5) the snapshot contract: honest status, health, segments, incidents, no narrative
curl -s "$url/api/state" -o "$tmp/state.json"
python3 - "$tmp/state.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
checks = [
    ("now is an int epoch", isinstance(d.get("now"), int) and d["now"] > 1_600_000_000),
    ("status.state is a string", isinstance(d.get("status", {}).get("state"), str)),
    ("status.resume_cmd runnable", str(d.get("status", {}).get("resume_cmd", "")).startswith("cd ")
     and "run.sh" in d["status"]["resume_cmd"]),
    ("health.tick.stall_s is an int", isinstance(d.get("health", {}).get("tick", {}).get("stall_s"), int)),
    ("health.dashboard.alive is a bool", isinstance(d.get("health", {}).get("dashboard", {}).get("alive"), bool)),
    ("status has why+since+phase", all(k in d.get("status", {}) for k in ("why", "since", "phase"))),
    ("loop.status mirrors status.state", d.get("loop", {}).get("status") == d["status"]["state"]),
    ("health.harness present", "harness" in d.get("health", {})),
    ("health.tick present", "tick" in d.get("health", {})),
    ("health.dashboard present", "dashboard" in d.get("health", {})),
    ("progress.segments_done present", "segments_done" in d.get("progress", {})),
    ("progress.pct_basis present", "pct_basis" in d.get("progress", {})),
    ("incidents is a list", isinstance(d.get("incidents"), list)),
    ("ticks is a list", isinstance(d.get("ticks"), list)),
    ("config.dashboard parsed", d.get("config", {}).get("dashboard") == "auto"),
    ("config.medic parsed", d.get("config", {}).get("medic") == "notify"),
    ("narrative removed", "narrative" not in d),
]
bad = [name for name, ok in checks if not ok]
for name, ok in checks:
    print(("ok: " if ok else "FAIL: ") + name)
sys.exit(1 if bad else 0)
PY
assert_true $? "snapshot matches the contract"
# loop-dir schema surfaces in the snapshot (null on a dir that never ticked; never a crash)
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert "schema" in d["loop"] and "migration" in d["loop"], d["loop"]' "$tmp/state.json"
assert_true $? "/api/state carries loop.schema and loop.migration"

# 6) an idle loop dir with no events reports idle, never done/halted
python3 -c 'import sys,json; d=json.load(open(sys.argv[1])); sys.exit(0 if d["status"]["state"]=="idle" else 1)' "$tmp/state.json"
assert_true $? "no events -> idle (not done/halted)"

# 7) start refuses to race a live harness (HTTP 409).
# `exec -a run.sh` makes the stub's argv[0] read as run.sh, which is exactly
# what liveness checks (`ps -o command=` must contain run.sh) — a plain $$
# would be rejected as PID reuse and the guard would not trip.
bash -c 'exec -a run.sh sleep 30' &
fake=$!
sleep 0.3
printf '{"pid":%s,"start_epoch":1,"host":"t","loop_dir":"%s","plugin_version":"2.0.0"}\n' \
  "$fake" "$tmp" > "$tmp/runtime/harness.json"
code="$(curl -s -o "$tmp/start.json" -w '%{http_code}' -X POST "$url/api/start")"
ok409=0; [[ "$code" == "409" ]] || ok409=1; assert_true "$ok409" "POST /api/start over a live harness => 409 (got $code)"
grep -q "is alive" "$tmp/start.json"; assert_true $? "409 body names the live harness pid"
kill "$fake" 2>/dev/null; wait "$fake" 2>/dev/null
fake=""
rm -f "$tmp/runtime/harness.json"

# 8) the live server reports itself alive, on the STALL_S it was started with
python3 - "$tmp/state.json" <<'PY'
import json, os, sys
d = json.load(open(sys.argv[1]))
dash = d["health"]["dashboard"]
ok = dash["alive"] is True and dash["pid"] > 0 and d["health"]["tick"]["stall_s"] == 300
print(("ok: " if ok else "FAIL: ") + "dashboard reports itself alive; stall_s=%s" %
      d["health"]["tick"]["stall_s"])
sys.exit(0 if ok else 1)
PY
assert_true $? "health.dashboard.alive true for the serving process"

# 10a) --detach over a live dashboard reuses it: same url, same pid, nothing launched
( cd "$tmp" && LOOP_DIR="$tmp" python3 "$ROOT/web/serve.py" --loop-dir "$tmp" --detach > "$tmp/detach-a.out" 2>&1 )
python3 - "$tmp/detach-a.out" "$url" "$srv" <<'PY'
import json, sys
b = json.loads(open(sys.argv[1]).read().strip().splitlines()[-1])
ok = b["type"] == "dashboard-started" and b["url"] == sys.argv[2] and b["reused"] is True and int(b["pid"]) == int(sys.argv[3])
print(("ok: " if ok else "FAIL: ") + "--detach reuses the live dashboard (%s)" % b)
sys.exit(0 if ok else 1)
PY
assert_true $? "--detach over a live dashboard reuses it"

# 10b) --detach on a quiet loop dir launches a real server in its own session and returns
tmp2="$(mktemp -d)"; mkdir -p "$tmp2/runtime"
printf '# Loop Config\nWorktree: %s\n' "$tmp2" > "$tmp2/LOOP_CONFIG.md"
printf '# Loop Plan\n- [ ] T1: a\n' > "$tmp2/LOOP_PLAN.md"
( cd "$tmp2" && LOOP_DIR="$tmp2" python3 "$ROOT/web/serve.py" --loop-dir "$tmp2" --no-spawn --detach > "$tmp2/detach.out" 2>&1 )
rc=$?
det="$(python3 -c 'import json,sys; print(json.loads(open(sys.argv[1]).read().strip().splitlines()[-1]).get("pid",""))' "$tmp2/detach.out" 2>/dev/null)"
url2="$(python3 -c 'import json,sys; print(json.loads(open(sys.argv[1]).read().strip().splitlines()[-1]).get("url",""))' "$tmp2/detach.out" 2>/dev/null)"
okdet=0; [[ "$rc" -eq 0 && -n "$det" && -n "$url2" ]] || okdet=1; assert_true "$okdet" "--detach returns 0 with a pid and url (rc=$rc pid=$det url=$url2)"
kill -0 "$det" 2>/dev/null; assert_true $? "the detached server is alive after --detach returned"
code="$(curl -s -o /dev/null -w '%{http_code}' "$url2/api/state")"
ok200=0; [[ "$code" == "200" ]] || ok200=1; assert_true "$ok200" "the detached server serves /api/state (got $code)"
[[ "$(ps -o pgid= -p "$det" | tr -d ' ')" != "$(ps -o pgid= -p $$ | tr -d ' ')" ]]; assert_true $? "the detached server is in its own process group"
kill "$det" 2>/dev/null; wait "$det" 2>/dev/null
rm -rf "$tmp2"

# 9) runtime/dashboard.json records the sidecar flag (false without --sidecar)
python3 -c 'import sys,json; d=json.load(open(sys.argv[1])); sys.exit(0 if d.get("sidecar") is False and d.get("pid") else 1)' \
  "$tmp/runtime/dashboard.json"
assert_true $? "dashboard.json records pid + sidecar:false"

# --- Plan 1: real billed surface from a synthetic 3-model ledger ---
if command -v jq >/dev/null 2>&1; then
  p1fix="$(mktemp)"
  cat > "$p1fix" <<'JSONL'
{"tick":1,"mode":"review","cost_usd":4.70,"duration_s":1090,"by_model":{"claude-sonnet-4-6":{"cost_usd":3.6,"input_tokens":134,"output_tokens":29878,"cache_read_tokens":7214525,"cache_creation_tokens":220543},"us.anthropic.claude-sonnet-4-6":{"cost_usd":1.0,"input_tokens":1,"output_tokens":1,"cache_read_tokens":1000,"cache_creation_tokens":0},"claude-opus-4-8[1m]":{"cost_usd":0.8,"input_tokens":30274,"output_tokens":3028,"cache_read_tokens":281679,"cache_creation_tokens":69214}}}
JSONL
  p1out="$(cd "$ROOT/web" && python3 -c "import serve,json; print(json.dumps(serve.usage_effort(open('$p1fix').read(), 1)))")"
  p1models="$(printf '%s' "$p1out" | jq '.by_model | length')"
  [ "$p1models" = "2" ] || { echo "FAIL: region-variant sonnet should collapse to 2 models, got $p1models"; fail=1; }
  p1pct="$(printf '%s' "$p1out" | jq '.cache_read_pct')"
  [ "$p1pct" -ge 90 ] 2>/dev/null || { echo "FAIL: cache_read_pct=$p1pct < 90"; fail=1; }
  rm -f "$p1fix"
fi

[[ "$fail" -eq 0 ]] && echo "web.contract.sh: PASS"
exit "$fail"
