#!/usr/bin/env bash
# Repo-wide structural invariants. Implemented once, looped over all plugins.
# Behavioural assertions belong in plugins/<name>/tests/all.sh, not here.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

FAILED=0
fail() { echo "FAIL: $*" >&2; FAILED=1; }
ok()   { echo "  ok: $*"; }

MARKET=".claude-plugin/marketplace.json"

# Canonical level-2 heading order (R4). A case statement rather than a
# delimited string: no IFS juggling, no unquoted word-splitting, bash 3.2 safe.
canon_index() {
  case "$1" in
    "Why")           echo 1 ;;
    "What it ships") echo 2 ;;
    "Install")       echo 3 ;;
    "Usage")         echo 4 ;;
    "Configuration") echo 5 ;;
    "Tests")         echo 6 ;;
    "Related")       echo 7 ;;
    *)               echo 0 ;;
  esac
}
# Files permitted to be mode 755 (R9). Everything else tracked must be 644.
EXEC_OK="config/ccstatusline/ccsl-dir.sh
config/ccstatusline/ccsl-model.sh
config/ccstatusline/ccsl-sandbox.sh
plugins/agent-loop/run.sh
plugins/agent-loop/tests/fixtures/claude
scripts/lint.sh
scripts/test-all.sh
scripts/validate.sh"

echo "== official manifest validation =="
if command -v claude >/dev/null 2>&1; then
  claude plugin validate --strict "$MARKET" >/dev/null 2>&1 \
    || fail "claude plugin validate --strict $MARKET"
  for d in plugins/*/; do
    claude plugin validate --strict "$d" >/dev/null 2>&1 \
      || fail "claude plugin validate --strict $d"
  done
  ok "claude plugin validate --strict"
else
  echo "  (skipped — claude CLI not on PATH)"
fi

echo "== marketplace entries =="
# R2: entries carry only name, source, category. plugin.json is authoritative.
while IFS= read -r extra; do
  [ -n "$extra" ] && fail "marketplace entry has non-permitted key '$extra' (R2: plugin.json is authoritative)"
done < <(jq -r '.plugins[] | keys[] | select(. != "name" and . != "source" and . != "category")' "$MARKET")

# Every entry resolves to a real plugin directory.
while IFS= read -r line; do
  name="${line%%	*}"; src="${line##*	}"
  [ "$src" = "./plugins/$name" ] || fail "$name: marketplace source is '$src', expected './plugins/$name' (R1)"
  [ -d "plugins/$name" ] || fail "$name: marketplace entry has no directory plugins/$name"
done < <(jq -r '.plugins[] | "\(.name)\t\(.source)"' "$MARKET")
ok "marketplace entries"

echo "== per-plugin =="
for d in plugins/*/; do
  d="${d%/}"                      # strip the trailing slash once, up front
  name="$(basename "$d")"

  # R1: directory name == plugin.json name == marketplace entry name.
  pj="$d/.claude-plugin/plugin.json"
  if [ ! -f "$pj" ]; then
    fail "$name: missing $pj"
    continue
  fi
  jname="$(jq -r '.name // ""' "$pj")"
  [ "$jname" = "$name" ] || fail "$name: plugin.json name is '$jname', expected '$name' (R1)"
  jq -e --arg n "$name" '.plugins[] | select(.name == $n)' "$MARKET" >/dev/null \
    || fail "$name: no marketplace entry (R1)"

  # R4: README exists, titled correctly, headings drawn from the canonical
  # set and in canonical order. Required: Install and Tests.
  readme="$d/README.md"
  if [ ! -f "$readme" ]; then
    fail "$name: missing README.md (R4)"
  else
    head -n1 "$readme" | grep -qx "# $name" \
      || fail "$name: README first line must be '# $name' (R4)"
    grep -q '^## Install$' "$readme" || fail "$name: README missing '## Install' (R4)"
    grep -q '^## Tests$'   "$readme" || fail "$name: README missing '## Tests' (R4)"
    last=0
    while IFS= read -r h; do
      idx="$(canon_index "$h")"
      if [ "$idx" -eq 0 ]; then
        fail "$name: README has non-canonical heading '## $h' (R4)"
      elif [ "$idx" -lt "$last" ]; then
        fail "$name: README heading '## $h' is out of canonical order (R4)"
      else
        last="$idx"
      fi
    done < <(grep '^## ' "$readme" | sed 's/^## //')
  fi

  # R5: a test entrypoint exists.
  [ -f "$d/tests/all.sh" ] || fail "$name: missing tests/all.sh (R5)"

  # R6: no plugin-local copy of the shared assertion helper.
  [ -f "$d/tests/assert.sh" ] && fail "$name: has a local tests/assert.sh; source tests/lib/assert.sh (R6)"

  # SKILL.md frontmatter name must match its directory.
  for s in "$d"/skills/*/SKILL.md; do
    [ -f "$s" ] || continue
    sdir="$(basename "$(dirname "$s")")"
    sname="$(awk '/^name:/{print $2; exit}' "$s")"
    [ "$sdir" = "$sname" ] || fail "$name: $s declares name '$sname' but lives in '$sdir'"
  done
done
ok "per-plugin checks complete"

echo "== file modes (R9) =="
while IFS= read -r line; do
  mode="${line%% *}"; path="${line#* }"
  case "$mode" in
    100755)
      case "$path" in
        */tests/all.sh) ;;
        *) printf '%s\n' "$EXEC_OK" | grep -qxF "$path" \
             || fail "$path is mode 755 but is not on the executable allowlist (R9)" ;;
      esac ;;
  esac
done < <(git ls-files -s | awk '{print $1, $4}')
ok "file modes"

if [ "$FAILED" -ne 0 ]; then
  echo; echo "validate: FAILED" >&2; exit 1
fi
echo; echo "validate: all invariants hold"
