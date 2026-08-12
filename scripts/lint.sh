#!/usr/bin/env bash
# Runs shellcheck over every tracked shell file.
#
# --severity=warning: gate on real warnings/errors. The info/style noise
# (SC1091 can't-follow-source from runtime-resolved paths, SC2016 intentional
# single-quoted prompt text, SC2181) is not actionable for this layout.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -n "$ROOT" ] || exit 1
cd "$ROOT" || exit 1

if ! command -v shellcheck >/dev/null 2>&1; then
  echo "shellcheck not installed — skipping lint (install: brew install shellcheck)"
  exit 2
fi

files=()
while IFS= read -r f; do
  [ -n "$f" ] && files+=("$f")
done < <(git ls-files -- '*.sh' 'plugins/agent-loop/tests/fixtures/claude')

if [ "${#files[@]}" -eq 0 ]; then
  echo "no shell files found" >&2
  exit 1
fi

shellcheck -x --severity=warning "${files[@]}" || exit 1
echo "shellcheck clean (${#files[@]} files)"
