#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
if ! command -v shellcheck >/dev/null 2>&1; then
  echo "shellcheck not installed — skipping lint (install: brew install shellcheck)"
  exit 2
fi
# --severity=warning: gate on real warnings/errors. The info/style noise
# (SC1091 can't-follow-source from runtime-resolved paths, SC2016 intentional
# single-quoted prompt text, SC2181) isn't actionable for this layout.
shellcheck -x --severity=warning "$HERE/../run.sh" "$HERE/../lib/loop.sh" "$HERE"/*.sh "$HERE/fixtures/claude"
echo "shellcheck clean"
