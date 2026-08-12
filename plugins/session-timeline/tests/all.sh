#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
# shellcheck source=../../../tests/lib/assert.sh
source "$ROOT/tests/lib/assert.sh"

if ! command -v node >/dev/null 2>&1; then
  echo "(skipped — node not installed)"
  exit 0
fi

GEN="$ROOT/plugins/session-timeline/skills/session-timeline/scripts/generate-timeline.mjs"
FIXTURE="$HERE/fixtures/sample-session.jsonl"
OUT="$(mktemp -t session-timeline-test).html"
trap 'rm -f "$OUT"' EXIT

# Promise: "a self-contained HTML file" generated from a session transcript.
node "$GEN" --file "$FIXTURE" --output "$OUT" >/dev/null 2>&1
assert_true $? "generate-timeline.mjs runs against a fixture transcript"

if [ -s "$OUT" ]; then out_nonempty=0; else out_nonempty=1; fi
assert_true "$out_nonempty" "output file was written and is non-empty"

size=$(wc -c <"$OUT")
if [ "$size" -gt 2000 ]; then out_sized=0; else out_sized=1; fi
assert_true "$out_sized" "output HTML is non-trivial in size (got ${size} bytes)"

grep -q "<html" "$OUT"
assert_true $? "output contains an <html document"

! grep -Eq '<script[^>]+src="https?://' "$OUT"
assert_true $? "output has no remote <script src=...>"

! grep -Eq '<link[^>]+rel="stylesheet"[^>]+href="https?://' "$OUT"
assert_true $? "output has no remote stylesheet <link>"

assert_summary
