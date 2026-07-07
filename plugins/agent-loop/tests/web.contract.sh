#!/usr/bin/env bash
# Smoke test: server serves /, /api/state is valid JSON, control toggles PAUSE.
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
printf '# Loop Config\nWorktree: %s\n' "$tmp" > "$tmp/LOOP_CONFIG.md"
printf '# Loop Plan\n## Segment A: x\n- [ ] T1: a\n' > "$tmp/LOOP_PLAN.md"

# --no-spawn: observe only, never launch a real claude loop in the test.
# -u: unbuffered stdout, so the startup JSON line lands in $tmp/out promptly
# even though stdout is redirected to a file (fully buffered by default).
LOOP_DIR="$tmp" python3 -u "$ROOT/web/serve.py" --loop-dir "$tmp" --no-spawn >"$tmp/out" 2>"$tmp/err" &
srv=$!
trap 'kill "$srv" 2>/dev/null' EXIT

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

# 1) / serves HTML (dashboard.html present after Task 9; tolerate empty pre-Task-9 by checking 200)
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

# 4) Task 8 — dashboard markup carries the contract element ids
curl -s "$url/" -o "$tmp/page.html"
grep -q 'id="verdict"'  "$tmp/page.html"; assert_true $? "hero verdict element present"
grep -q 'id="nowLine"'  "$tmp/page.html"; assert_true $? "NOW line element present"
grep -q 'id="tickNo"'   "$tmp/page.html"; assert_true $? "continuous tick element present"
grep -q 'id="roster"'   "$tmp/page.html"; assert_true $? "roster (pipeline) strip present"
grep -q 'id="roadmap"'  "$tmp/page.html"; assert_true $? "roadmap track present"

# 5) Task 8 — snapshot exposes the new contract shape
curl -s "$url/api/state" -o "$tmp/state.json"
if command -v jq >/dev/null 2>&1; then
  jq -e 'has("roadmap") and has("pipeline")' "$tmp/state.json" >/dev/null; assert_true $? "snapshot has roadmap+pipeline"
  jq -e 'has("loop") and (.loop|has("tick"))' "$tmp/state.json" >/dev/null; assert_true $? "snapshot loop has tick"
else
  python3 -c 'import sys,json; d=json.load(open(sys.argv[1])); sys.exit(0 if ("roadmap" in d and "pipeline" in d) else 1)' "$tmp/state.json"
  assert_true $? "snapshot has roadmap+pipeline"
  python3 -c 'import sys,json; d=json.load(open(sys.argv[1])); sys.exit(0 if ("loop" in d and "tick" in d["loop"]) else 1)' "$tmp/state.json"
  assert_true $? "snapshot loop has tick"
fi

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
