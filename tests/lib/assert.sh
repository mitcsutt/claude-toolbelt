#!/usr/bin/env bash
# Minimal assertion helpers. No external deps.
ASSERT_PASS=0
ASSERT_FAIL=0

assert_eq() { # expected actual message
  if [[ "$1" == "$2" ]]; then
    ASSERT_PASS=$((ASSERT_PASS + 1))
  else
    ASSERT_FAIL=$((ASSERT_FAIL + 1))
    echo "FAIL: $3" >&2
    echo "  expected: [$1]" >&2
    echo "  actual:   [$2]" >&2
  fi
}

assert_true() { # status(0=true) message
  if [[ "$1" -eq 0 ]]; then ASSERT_PASS=$((ASSERT_PASS + 1));
  else ASSERT_FAIL=$((ASSERT_FAIL + 1)); echo "FAIL (expected true): $2" >&2; fi
}

assert_false() { # status message
  if [[ "$1" -ne 0 ]]; then ASSERT_PASS=$((ASSERT_PASS + 1));
  else ASSERT_FAIL=$((ASSERT_FAIL + 1)); echo "FAIL (expected false): $2" >&2; fi
}

assert_summary() {
  echo "---"
  echo "PASS: $ASSERT_PASS  FAIL: $ASSERT_FAIL"
  [[ "$ASSERT_FAIL" -eq 0 ]]
}
