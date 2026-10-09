#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"

if ! command -v node >/dev/null 2>&1; then
  echo "(skipped — node not installed)"
  exit 0
fi

cd "$ROOT" || exit 1

# Collect test files from lib/, bin/, hooks/; unmatched globs stay literal and are filtered out.
files=()
for f in plugins/permissions/lib/*.test.mjs \
         plugins/permissions/bin/*.test.mjs \
         plugins/permissions/hooks/*.test.mjs; do
  [ -e "$f" ] && files+=("$f")
done

if [ "${#files[@]}" -eq 0 ]; then
  echo "(no node tests found)"
  exit 0
fi

node --test "${files[@]}"
