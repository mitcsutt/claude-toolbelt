#!/usr/bin/env bash
# Everything that must be green before a change lands.
# Deliberately does NOT run `claude plugin eval` — evals cost money and are
# run manually. See docs/testing.md.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -n "$ROOT" ] || exit 1
cd "$ROOT" || exit 1

fail=0

echo "### validate"
bash scripts/validate.sh || fail=1

echo "### lint"
bash scripts/lint.sh; rc=$?
if [ "$rc" -eq 2 ]; then
  echo "(lint skipped — shellcheck not installed)"
elif [ "$rc" -ne 0 ]; then
  fail=1
fi

for suite in plugins/*/tests/all.sh; do
  [ -f "$suite" ] || continue
  echo "### $suite"
  bash "$suite" || fail=1
done

echo "### node --test"
node_files=()
while IFS= read -r f; do
  [ -n "$f" ] && node_files+=("$f")
done < <(git ls-files -- '*.test.mjs')
if [ "${#node_files[@]}" -eq 0 ]; then
  echo "(no .test.mjs files)"
elif command -v node >/dev/null 2>&1; then
  node --test "${node_files[@]}" || fail=1
else
  echo "(skipped — node not installed)"
fi

echo
if [ "$fail" -ne 0 ]; then echo "test-all: FAILED" >&2; exit 1; fi
echo "test-all: PASS"
