#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"

if ! command -v node >/dev/null 2>&1; then
  echo "(skipped — node not installed)"
  exit 0
fi

cd "$ROOT" || exit 1
node --test plugins/permissions/lib/*.test.mjs
