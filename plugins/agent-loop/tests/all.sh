#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
fail=0
for t in lib.test.sh events.test.sh run.e2e.test.sh tick-prompt.contract.sh setup.contract.sh postmortem.contract.sh; do
  echo "### $t"
  bash "$HERE/$t" || fail=1
done
echo "### serve.test.py"
if command -v python3 >/dev/null 2>&1; then
  python3 "$HERE/serve.test.py" || fail=1
else
  echo "(python tests skipped — python3 not installed)"
fi
echo "### web.contract.sh"
bash "$HERE/web.contract.sh" || fail=1
echo "### lint"
bash "$HERE/lint.sh"; rc=$?
if [[ "$rc" -eq 2 ]]; then
  echo "(lint skipped — shellcheck not installed)"
elif [[ "$rc" -ne 0 ]]; then
  fail=1
fi
exit "$fail"
