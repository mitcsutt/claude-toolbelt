#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
fail=0
for t in run.e2e.test.sh setup.contract.sh medic.contract.sh postmortem.contract.sh; do
  echo "### $t"
  bash "$HERE/$t" || fail=1
done
echo "### runner unit tests"
if command -v python3 >/dev/null 2>&1; then
  ( cd "$HERE/.." && python3 -m unittest discover -s tests/runner -p 'test_*.py' ) || fail=1
else
  echo "(python tests skipped — python3 not installed)"
fi
echo "### serve.test.py"
if command -v python3 >/dev/null 2>&1; then
  python3 "$HERE/serve.test.py" || fail=1
else
  echo "(python tests skipped — python3 not installed)"
fi
echo "### web.contract.sh"
bash "$HERE/web.contract.sh" || fail=1
exit "$fail"
