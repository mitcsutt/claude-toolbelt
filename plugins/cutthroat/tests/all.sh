#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
fail=0
for t in style.test.sh hook.test.sh; do
  if [[ -f "$HERE/$t" ]]; then
    echo "### $t"
    bash "$HERE/$t" || fail=1
  fi
done
exit "$fail"
