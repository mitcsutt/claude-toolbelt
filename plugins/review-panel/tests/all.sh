#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
# shellcheck source=../../../tests/lib/assert.sh
source "$ROOT/tests/lib/assert.sh"

PLUGIN="$ROOT/plugins/review-panel"
AGENTS="$PLUGIN/agents"
SKILL="$PLUGIN/skills/assemble-panel/SKILL.md"
REFS="$PLUGIN/skills/assemble-panel/references"

ROSTER="grumpy-senior paranoid-sre fact-checker dx-advocate threat-modeller goal-auditor outsider contract-keeper cost-hawk rules-lawyer noise-filter"
HELPERS="instruction-follower"
# Spawn policy as data: the reviewers allowed the Agent tool, and the one
# agent type each may dispatch.
SPAWNERS="rules-lawyer"
spawns() { case "$1" in rules-lawyer) echo instruction-follower ;; esac; }
is_spawner() { case " $SPAWNERS " in *" $1 "*) return 0 ;; esac; return 1; }

fm() { # file key -> frontmatter value
  awk -v k="$2" 'NR==1&&/^---$/{f=1;next} f&&/^---$/{exit} f&&index($0,k":")==1{sub("^"k":[ ]*","");print;exit}' "$1"
}

# Promise: ships the persona agents in the roster.
n=0; want=0
for a in $ROSTER; do
  want=$((want + 1))
  f="$AGENTS/$a.md"
  if [ -f "$f" ]; then ok=0; n=$((n + 1)); else ok=1; fi
  assert_true "$ok" "agent $a exists"
  [ -f "$f" ] || continue
  assert_eq "$a" "$(fm "$f" name)" "$a: frontmatter name matches filename"
  desc=$(fm "$f" description)
  if [ -n "$desc" ]; then ok=0; else ok=1; fi
  assert_true "$ok" "$a: has a description"

  # Promise: read-only reviewers that can't edit; only SPAWNERS may spawn, and only their follower.
  tools=$(fm "$f" tools)
  if [ -n "$tools" ]; then ok=0; else ok=1; fi
  assert_true "$ok" "$a: declares an explicit tools allowlist"
  echo "$tools" | grep -Eq '(^|[ ,])(Task|Edit|NotebookEdit)([ ,(]|$)'
  assert_false "$?" "$a: tools exclude Task/Edit/NotebookEdit"
  if is_spawner "$a"; then
    echo "$tools" | grep -Eq '(^|[ ,])Agent([ ,(]|$)'
    assert_true "$?" "$a: tools include Agent, as a listed spawner"
  else
    echo "$tools" | grep -Eq '(^|[ ,])Agent([ ,(]|$)'
    assert_false "$?" "$a: tools exclude Agent"
    grep -q "Don't spawn subagents" "$f"; assert_true "$?" "$a: forbids spawning subagents"
  fi

  # Promise: never checks out branches, never prints secret values.
  grep -qi 'edit, commit, check out, switch branches' "$f"; assert_true "$?" "$a: forbids checkout in a shared tree"
  grep -qi 'never print secret values' "$f"; assert_true "$?" "$a: forbids printing secret values"

  # Promise: returns one line pointing at its report file.
  grep -q 'return exactly one line\|then return exactly one line' "$f"; assert_true "$?" "$a: returns one line"
done
assert_eq "$want" "$n" "every roster agent present"

# Promise: fact-checker keeps a read-and-fetch tool set: no Agent, no MCP tools.
assert_eq "Read, Grep, Glob, Bash, Write, WebFetch, WebSearch" "$(fm "$AGENTS/fact-checker.md" tools)" "fact-checker: tools allowlist (no MCP, no Agent)"

# No stray agent files beyond the roster and its helpers.
extra=0
for f in "$AGENTS"/*.md; do
  b=$(basename "$f" .md)
  case " $ROSTER $HELPERS " in *" $b "*) ;; *) extra=1; echo "unexpected agent: $b" >&2 ;; esac
done
assert_eq "0" "$extra" "no agents outside the documented roster and helpers"

# Promise: reviewers report everything with severity + confidence; filtering is a separate pass.
for a in $ROSTER; do
  [ "$a" = noise-filter ] && continue
  f="$AGENTS/$a.md"
  grep -q 'Severity: BLOCK | SHOULD | COULD' "$f"; assert_true "$?" "$a: uses the shared severity scale"
  grep -q 'Confidence: high | medium | low' "$f"; assert_true "$?" "$a: reports confidence"
  grep -q 'Verdict: SHIP | SHIP WITH FIXES | DO NOT SHIP' "$f"; assert_true "$?" "$a: gives one verdict"
  grep -q 'separate pass filters' "$f"; assert_true "$?" "$a: reports everything, filter is separate"
done

# Promise: a spawner dispatches no agent type other than its follower.
# The platform doesn't enforce this (Agent(type) lists are ignored inside
# subagents) and the eval trace doesn't show a subagent's own Agent calls, so
# the boundary is prompt-enforced and covered by eval review. This checks only
# that every agent type a spawner names is its follower.
for a in $SPAWNERS; do
  f="$AGENTS/$a.md"
  others=$(grep -o 'review-panel:[a-z-]*' "$f" | sort -u | grep -v "^review-panel:$(spawns "$a")\$")
  assert_eq "" "$others" "$a names no agent type to dispatch but $(spawns "$a")"
  grep -q 'general-purpose' "$f"; assert_false "$?" "$a never mentions general-purpose"
done

# Promise: each reviewer's references load on demand from the plugin, every
# one it names exists, and every file in its directory is reachable.
REFROOT="$PLUGIN/references"
for d in "$REFROOT"/*/; do
  name=$(basename "$d")
  [ "$name" = shared ] && continue
  f="$AGENTS/$name.md"
  if [ -f "$f" ]; then ok=0; else ok=1; fi
  assert_true "$ok" "references/$name/ belongs to an agent"
  [ -f "$f" ] || continue
  grep -q "\${CLAUDE_PLUGIN_ROOT}/references/$name/" "$f"; assert_true "$?" "$name loads references via CLAUDE_PLUGIN_ROOT"
  # Full paths must resolve; a bare file name means one in its own directory.
  for p in $(grep -oE '\$\{CLAUDE_PLUGIN_ROOT\}/references/[a-z-]+/[a-z-]+\.md' "$f" | sort -u); do
    if [ -f "$REFROOT/${p#*/references/}" ]; then ok=0; else ok=1; fi
    assert_true "$ok" "$name reference exists: ${p#*/references/}"
  done
  for r in $(sed -E 's#\$\{CLAUDE_PLUGIN_ROOT\}/[a-z/-]+\.md##g' "$f" | grep -oE '`[a-z][a-z-]*\.md`' | tr -d '`' | sort -u); do
    if [ -f "$d$r" ]; then ok=0; else ok=1; fi
    assert_true "$ok" "$name reference exists: $name/$r"
  done
  for r in "$d"*.md; do
    b=$(basename "$r")
    others=$(find "$d" -name '*.md' ! -name "$b")
    # shellcheck disable=SC2086 # $others is a list of plain .md paths
    if grep -q "$b" "$f" $others; then ok=0; else ok=1; fi
    assert_true "$ok" "reference is reachable from $name: $b"
  done
done
for r in "$REFROOT"/shared/*.md; do
  b=$(basename "$r")
  if grep -q "\${CLAUDE_PLUGIN_ROOT}/references/shared/$b" "$AGENTS"/*.md; then ok=0; else ok=1; fi
  assert_true "$ok" "shared reference is loaded by an agent: $b"
done

# Promise: every evidence ID cited in an agent, a skill or a reference
# resolves to exactly one row across all evidence files.
ID_RE='\b(E|FC|TM|GA|NF|SR|CK|CH|DX|OU|GS|AP)[0-9]{1,2}\b'
for id in $(cat "$AGENTS"/*.md "$REFROOT"/*/*.md "$PLUGIN"/skills/*/SKILL.md "$PLUGIN"/skills/*/references/*.md | grep -oE "$ID_RE" | sort -u); do
  rows=$(cat "$REFROOT"/*/evidence.md "$PLUGIN"/skills/*/references/evidence.md | grep -c "^| $id |")
  assert_eq "1" "$rows" "evidence ID $id has exactly one row"
done

# Promise: instruction-follower is a read-only helper that loads no CLAUDE.md.
FOL="$AGENTS/instruction-follower.md"
assert_eq "instruction-follower" "$(fm "$FOL" name)" "instruction-follower: name matches filename"
assert_eq "Read, Grep, Glob" "$(fm "$FOL" tools)" "instruction-follower: read-only tools only"
assert_eq "true" "$(fm "$FOL" omitClaudeMd)" "instruction-follower: omits CLAUDE.md"

# Promise: every eval case seeds its fixture, grades the report file its prompt
# names, targets every regex grader, and has a control grader or says why not.
for EVAL in "$PLUGIN"/evals/*/; do
  c=$(basename "$EVAL")
  [ "$c" = results ] && continue # claude plugin eval output, gitignored
  if [ -f "$EVAL/case.yaml" ]; then ok=0; else ok=1; fi
  assert_true "$ok" "eval case exists: $c"
  [ -f "$EVAL/case.yaml" ] || continue
  if python3 -c 'import yaml' >/dev/null 2>&1; then
    facts=$(python3 -I -c '
import sys, yaml
c = yaml.safe_load(open(sys.argv[1]))
gs = c.get("graders", [])
llm = [g for g in gs if g.get("type") == "llm"]
paths = {(g.get("focus") or {}).get("path") for g in llm}
shaped = all((g.get("focus") or {}).get("source") == "file" for g in llm)
prompt = (c.get("execution") or {}).get("prompt", "")
p = next(iter(paths)) if len(paths) == 1 else None
print(c.get("context", {}).get("scaffold_script"))
print(not llm or (shaped and p is not None and p in prompt))
print(all(g.get("target") for g in gs if g.get("type") == "regex"))
print(any(g["name"].startswith("control_") for g in gs) or "no control" in c.get("description", ""))
files = [g["target"].get("path") for g in gs if g.get("type") == "regex" and isinstance(g.get("target"), dict) and g["target"].get("source") == "file"]
print(all(f and f in prompt for f in files))
' "$EVAL/case.yaml")
    assert_eq "fixture.sh" "$(echo "$facts" | sed -n 1p)" "$c: seeds its fixture from fixture.sh"
    if [ -f "$EVAL/fixture.sh" ]; then ok=0; else ok=1; fi
    assert_true "$ok" "$c: fixture.sh exists"
    assert_eq "True" "$(echo "$facts" | sed -n 2p)" "$c: every llm grader grades the report file the prompt names"
    assert_eq "True" "$(echo "$facts" | sed -n 3p)" "$c: every regex grader has a target"
    assert_eq "True" "$(echo "$facts" | sed -n 4p)" "$c: has a control grader or says 'no control'"
    assert_eq "True" "$(echo "$facts" | sed -n 5p)" "$c: every regex grader on a file grades a path the prompt names"
  else
    echo "($c case.yaml checks skipped — python3 with PyYAML not installed)"
  fi
done

# Promise: the assemble-panel eval cases run without Agent (fixture hooks
# don't load under plugin eval) and grade the roster the skill prints. Each
# case's last_message graders pass on a golden final message, fail on wrong
# ones, name only real agents, and compile in JavaScript; exactly one case
# is the control.
AP_GOLDEN="$PLUGIN/lib/fixtures/assemble-panel"
AP_EVALS=$(find "$PLUGIN/evals" -mindepth 1 -maxdepth 1 -type d -name 'assemble-panel-*' | sort)
AP_COUNT=$(printf '%s\n' "$AP_EVALS" | grep -c .)
if [ "$AP_COUNT" -gt 0 ]; then ok=0; else ok=1; fi
assert_true "$ok" "assemble-panel eval cases exist"
AP_JS='
const fs = require("fs");
const [graderFile, goldenDir, caseName, agentsDir] = process.argv.slice(1);
const graders = JSON.parse(fs.readFileSync(graderFile, "utf8"));
const read = (n) => fs.readFileSync(`${goldenDir}/${n}.md`, "utf8");
const grade = (g, text) => {
  const n = (text.match(new RegExp(g.pattern, (g.flags || "") + "g")) || []).length;
  const m = g.match || "contains";
  return m === "contains" ? n > 0 : m === "not_contains" ? n === 0 : n === Number(m.slice(6));
};
const out = [];
for (const g of graders) {
  try { new RegExp(g.pattern, g.flags || ""); } catch (e) { out.push(`bad-regex ${g.name}`); continue; }
  if (g.target !== "last_message") continue;
  const golden = read(caseName);
  if (!grade(g, golden)) out.push(`golden-fails ${g.name}`);
  const roster = g.pattern.startsWith("Panel \\(");
  const agent = (g.pattern.match(/`\?([a-z][a-z-]*)\\b$/) || [])[1];
  if (agent && !fs.existsSync(`${agentsDir}/${agent}.md`)) out.push(`unknown-agent ${g.name} ${agent}`);
  if (agent && g.match === "not_contains") {
    const bad = golden.replace(/(Panel \(\d+ \+ filter\)[^\n]*\n)/, `$1- ${agent}: added by mistake\n`);
    if (grade(g, bad)) out.push(`not-discriminating ${g.name}`);
  }
  if (roster && (g.match || "contains") === "contains" && caseName !== "route-typo-control"
      && grade(g, read("route-typo-control"))) out.push(`passes-without-roster ${g.name}`);
  if (g.name.startsWith("control_") && grade(g, read("route-poller-plan"))) out.push(`control-passes-on-a-panel ${g.name}`);
}
console.log(out.join("\n"));'
if python3 -c 'import yaml' >/dev/null 2>&1 && command -v node >/dev/null 2>&1; then
  controls=0
  for EVAL in $AP_EVALS; do
    c=$(basename "$EVAL")
    short=${c#assemble-panel-}
    GR_JSON=$(mktemp)
    facts=$(python3 -I -c '
import sys, json, yaml
c = yaml.safe_load(open(sys.argv[1]))
gs = c.get("graders", [])
tools = (c.get("execution") or {}).get("allowed_tools") or []
print("Skill" in tools and "Agent" not in tools)
print(sum(1 for g in gs if g["name"].startswith("control_")))
json.dump([g for g in gs if g.get("type") == "regex"], open(sys.argv[2], "w"))
' "$EVAL/case.yaml" "$GR_JSON")
    assert_eq "True" "$(echo "$facts" | sed -n 1p)" "$c: allowed_tools include Skill and withhold Agent"
    controls=$((controls + $(echo "$facts" | sed -n 2p)))
    if [ -f "$AP_GOLDEN/$short.md" ]; then ok=0; else ok=1; fi
    assert_true "$ok" "$c: has a golden final message"
    if [ "$ok" -eq 0 ]; then
      problems=$(node -e "$AP_JS" "$GR_JSON" "$AP_GOLDEN" "$short" "$AGENTS" 2>&1)
      assert_eq "" "$problems" "$c: graders compile, pass on the golden, fail on wrong answers, name real agents"
    fi
    rm -f "$GR_JSON"

    AP_TMP=$(mktemp -d)
    if (cd "$AP_TMP" && bash "$EVAL/fixture.sh" >/dev/null 2>&1); then ok=0; else ok=1; fi
    assert_true "$ok" "$c: fixture builds"
    if [ "$c" = assemble-panel-blocked-add-persona ]; then
      if (cd "$AP_TMP" && node "$PLUGIN/lib/report-parse.mjs" findings .panel/digest/goal-auditor.md .panel/digest/grumpy-senior.md .panel/digest/fact-checker.md .panel/digest/contract-keeper.md >/dev/null 2>&1); then ok=0; else ok=1; fi
      assert_true "$ok" "$c: the earlier panel's reports parse with no problems"
    fi
    rm -rf "$AP_TMP"
  done
  assert_eq "1" "$controls" "exactly one assemble-panel case has a control_ grader"
else
  echo "(assemble-panel case checks skipped: python3 with PyYAML or node not installed)"
fi

# Promise: noise-filter's eval fixture builds, its five reports parse, and
# the golden triage accounts for every finding (check-triage).
NF_EVAL="$PLUGIN/evals/noise-filter-planted-triage"
PARSER="$PLUGIN/lib/report-parse.mjs"
GOLDEN="$PLUGIN/lib/fixtures/noise-filter-golden-TRIAGE.md"
CTRL_DROPPED="$PLUGIN/lib/fixtures/noise-filter-control-dropped-TRIAGE.md"
if command -v git >/dev/null 2>&1 && command -v node >/dev/null 2>&1; then
  NF_TMP=$(mktemp -d)
  if (cd "$NF_TMP" && bash "$NF_EVAL/fixture.sh" >/dev/null 2>&1); then ok=0; else ok=1; fi
  assert_true "$ok" "noise-filter fixture builds and passes its self-checks"
  rows=$(cd "$NF_TMP" && node "$PARSER" findings reports/*.md 2>/dev/null)
  st=$?
  assert_eq "0" "$st" "noise-filter fixture reports parse with no problems"
  assert_eq "15" "$(printf '%s\n' "$rows" | grep -c '^{')" "noise-filter fixture has 15 findings"
  if (cd "$NF_TMP" && node "$PARSER" check-triage "$GOLDEN" reports/*.md >/dev/null 2>&1); then ok=0; else ok=1; fi
  assert_true "$ok" "check-triage passes on the golden triage against the fixture's reports"
  rm -rf "$NF_TMP"
else
  echo "(noise-filter fixture checks skipped: git or node not installed)"
fi

# Promise: the noise-filter eval's regex graders discriminate on a known
# answer: every file grader passes on the golden triage, and the control
# grader fails when the control finding is dropped.
GRADE_JS='
const fs = require("fs");
const [graders, file] = process.argv.slice(1);
const text = fs.readFileSync(file, "utf8");
for (const g of JSON.parse(fs.readFileSync(graders, "utf8"))) {
  if (!g.target || g.target.source !== "file") continue;
  const n = (text.match(new RegExp(g.pattern, (g.flags || "") + "g")) || []).length;
  const m = g.match || "contains";
  const pass = m === "contains" ? n > 0 : m === "not_contains" ? n === 0
    : m.startsWith("count:") ? n === Number(m.slice(6)) : false;
  console.log(g.name + (pass ? " pass" : " fail"));
}'
if python3 -c 'import yaml' >/dev/null 2>&1 && command -v node >/dev/null 2>&1; then
  GR_JSON=$(mktemp)
  python3 -I -c '
import sys, json, yaml
c = yaml.safe_load(open(sys.argv[1]))
json.dump([g for g in c["graders"] if g.get("type") == "regex"], open(sys.argv[2], "w"))
' "$NF_EVAL/case.yaml" "$GR_JSON"
  golden=$(node -e "$GRADE_JS" "$GR_JSON" "$GOLDEN")
  if [ -n "$golden" ]; then ok=0; else ok=1; fi
  assert_true "$ok" "noise-filter eval has file-target regex graders"
  assert_eq "" "$(printf '%s\n' "$golden" | grep ' fail$')" "every noise-filter file grader passes on the golden triage"
  dropped=$(node -e "$GRADE_JS" "$GR_JSON" "$CTRL_DROPPED")
  if printf '%s\n' "$dropped" | grep -qx 'control_hedged_block_survives fail'; then ok=0; else ok=1; fi
  assert_true "$ok" "control grader fails when the control finding is dropped"
  rm -f "$GR_JSON"
else
  echo "(noise-filter grader checks skipped: python3 with PyYAML or node not installed)"
fi

# Promise: goal-auditor runs read-only checks and its revert check through
# Bash, writes only its report, and has no web or Agent tool.
assert_eq "Read, Grep, Glob, Bash, Write" "$(fm "$AGENTS/goal-auditor.md" tools)" "goal-auditor: tools are exactly the read-only set plus Bash and Write"

# Promise: goal-auditor's fail-before/pass-after recipe tells a covering test
# from a vacuous one, copies DEPS and passes EXTRA_ENV to both sides without
# the block being edited, and leaves the repo it reads untouched.
GA_TMP=$(mktemp -d)
awk '/^# revert-check v1/{f=1} f&&/^```/{exit} f{print}' "$PLUGIN/references/goal-auditor/revert-check.md" > "$GA_TMP/recipe.sh"
if [ -s "$GA_TMP/recipe.sh" ]; then ok=0; else ok=1; fi
assert_true "$ok" "goal-auditor: revert-check.md carries the recipe block"
ga_snap() { find .git -type f -exec cksum {} + | sort; git ls-files -z | xargs -0 cksum | sort; }
ga_want="before test/f.sh exit=1
before test/vac.sh exit=0
after test/f.sh exit=0
after test/vac.sh exit=0"
for mode in commit WORKTREE; do
  res=$(
    d="$GA_TMP/$mode"; mkdir -p "$d/src" "$d/test" && cd "$d" || exit 1
    git init -q -b main
    printf 'deps/\n' > .gitignore
    printf 'f() { echo bad; }\ng() { echo same; }\n' > src/lib.sh
    printf '. ./src/lib.sh\n[ "$(g)" = same ]\n' > test/g.sh
    git add -A && git -c user.name=t -c user.email=t@example.invalid commit -q -m base
    printf 'f() { echo ok; }\ng() { echo same; }\n' > src/lib.sh
    mkdir deps && printf 'h() { echo "$WANT"; }\n' > deps/h.sh
    printf '. ./src/lib.sh\n. ./deps/h.sh\n[ "$(f)" = "$(h)" ]\n' > test/f.sh
    printf '. ./src/lib.sh\n[ "$(g)" = same ]\n' > test/vac.sh
    fix=WORKTREE
    if [ "$mode" = commit ]; then
      git checkout -q -b fix && git add -A && git -c user.name=t -c user.email=t@example.invalid commit -q -m fix
      fix=fix
    fi
    snap=$(ga_snap)
    out=$(BASE=main FIX="$fix" TESTS="test/f.sh test/vac.sh" RUN=sh DEPS=deps EXTRA_ENV=WANT=ok TMPDIR="$GA_TMP" bash "$GA_TMP/recipe.sh" | grep ' exit=')
    if [ "$out" = "$ga_want" ]; then echo 0; else echo 1; fi
    if [ "$snap" = "$(ga_snap)" ]; then echo 0; else echo 1; fi
  ) 2>/dev/null
  assert_eq "0" "$(echo "$res" | sed -n 1p)" "goal-auditor: recipe ($mode), given DEPS and EXTRA_ENV, fails the covering test before the fix, passes it after, and passes the vacuous test both times"
  assert_eq "0" "$(echo "$res" | sed -n 2p)" "goal-auditor: recipe ($mode) leaves .git and tracked files unchanged"
done
rm -rf "$GA_TMP"
# Promise: each fact-checker eval grader names the PLAN.md line that holds its planted claim.
FC_EVAL="$PLUGIN/evals/fact-checker-planted-claims"
if python3 -c 'import yaml' >/dev/null 2>&1; then
  tmp=$(mktemp -d)
  (cd "$tmp" && bash "$FC_EVAL/fixture.sh") >/dev/null 2>&1
  bad=$(python3 -I - "$FC_EVAL/case.yaml" "$tmp/PLAN.md" <<'PY'
import re, sys, yaml
case = yaml.safe_load(open(sys.argv[1]))
lines = open(sys.argv[2]).read().split("\n")
keys = {"p1": "MappingProxyType", "p2": "PYTHONUTF8", "p3": "AGENTS.md",
        "p4": "experimental-strip-types", "p5": "ERR_REQUIRE_ESM", "p6": "cp -n",
        "control_c1": "print_counts", "control_c2": "Agent(explorer)"}
seen = set()
for g in case["graders"]:
    key = next((k for k in keys if g["name"].startswith(k + "_")), None)
    if key is None:
        continue
    seen.add(key)
    m = re.search(r"PLAN\\?\.md:(\d+)", g.get("pattern", "") + g.get("criteria", ""))
    n = int(m.group(1)) if m else 0
    if not (0 < n <= len(lines)) or keys[key] not in lines[n - 1]:
        print("%s: line %d lacks %r" % (g["name"], n, keys[key]))
for k in sorted(set(keys) - seen):
    print("no grader for " + k)
PY
)
  rm -rf "$tmp"
  assert_eq "" "$bad" "fact-checker eval: graders point at their planted lines"
else
  echo "(fact-checker eval line-map check skipped — python3 with PyYAML not installed)"
fi

# Promise: the fact-checker eval's verdict graders discriminate by line: on a
# ledger marking every planted line WRONG and the controls CONFIRMED, every
# regex grader passes; flipping one planted line to CONFIRMED fails exactly
# that line's grader.
FC_DISC_JS='
const fs = require("fs");
const gs = JSON.parse(fs.readFileSync(process.argv[1], "utf8")).filter((g) => g.target && g.target.source === "file");
const lineOf = (g) => Number((g.pattern.match(/PLAN\\\.md:(\d+)/) || [])[1]);
const planted = gs.map(lineOf);
const ledger = (wrong) => ["| # | claim (quoted) | where | version | verdict | source |", "|---|---|---|---|---|---|"]
  .concat([14, 15, 16, 17, 18, 19, 20, 21].map((n, i) =>
    `| ${i + 1} | "claim" | PLAN.md:${n} | v | ${wrong.includes(n) ? "WRONG" : "CONFIRMED"} | src |`)).join("\n");
const passes = (g, text) => new RegExp(g.pattern, g.flags || "").test(text);
const out = [];
if (gs.length !== 6) out.push(`expected 6 file regex graders, got ${gs.length}`);
const golden = ledger(planted);
for (const g of gs) if (!passes(g, golden)) out.push(`${g.name} fails on the golden ledger`);
for (const flip of planted) {
  const text = ledger(planted.filter((n) => n !== flip));
  for (const g of gs) {
    const want = lineOf(g) !== flip;
    if (passes(g, text) !== want) out.push(`${g.name} ${want ? "fails" : "passes"} with PLAN.md:${flip} CONFIRMED`);
  }
}
console.log(out.join("\n"));'
if python3 -c 'import yaml' >/dev/null 2>&1 && command -v node >/dev/null 2>&1; then
  GR_JSON=$(mktemp)
  python3 -I -c '
import sys, json, yaml
c = yaml.safe_load(open(sys.argv[1]))
json.dump([g for g in c["graders"] if g.get("type") == "regex"], open(sys.argv[2], "w"))
' "$FC_EVAL/case.yaml" "$GR_JSON"
  assert_eq "" "$(node -e "$FC_DISC_JS" "$GR_JSON" 2>&1)" "fact-checker eval: each verdict grader passes only when its line is WRONG"
  rm -f "$GR_JSON"
else
  echo "(fact-checker grader checks skipped: python3 with PyYAML or node not installed)"
fi

# Promise: fact-checker uses the CONFIRMED / WRONG / UNVERIFIED ledger.
grep -q 'CONFIRMED' "$AGENTS/fact-checker.md" && grep -q 'WRONG' "$AGENTS/fact-checker.md" && grep -q 'UNVERIFIED' "$AGENTS/fact-checker.md"
assert_true "$?" "fact-checker marks claims CONFIRMED/WRONG/UNVERIFIED"

# Promise: grumpy-senior delivers a cut-down version.
grep -q '## Ship it on Friday' "$AGENTS/grumpy-senior.md"; assert_true "$?" "grumpy-senior delivers 'Ship it on Friday'"

# Promise: outsider writes its cold take before reading the artifact.
grep -q 'Before opening the artifact' "$AGENTS/outsider.md"; assert_true "$?" "outsider takes a cold read first"

# Promise: noise-filter runs the parser through Bash and writes only its triage.
assert_eq "Read, Grep, Glob, Bash, Write" "$(fm "$AGENTS/noise-filter.md" tools)" "noise-filter: tools are exactly Read, Grep, Glob, Bash, Write"

# Promise: noise-filter merges, marks cross-confirmed, spot-checks, drops with reasons.
for s in 'cross-confirmed' 'Spot-check' '## Dropped' '## Decisions for you' '## Fix before merge'; do
  grep -q "$s" "$AGENTS/noise-filter.md"; assert_true "$?" "noise-filter: $s"
done

# Skill structure.
if [ -f "$SKILL" ]; then ok=0; else ok=1; fi
assert_true "$ok" "SKILL.md exists"
assert_eq "assemble-panel" "$(fm "$SKILL" name)" "SKILL.md frontmatter name is 'assemble-panel'"

# Promise: sizes the panel (and can decline) before picking.
grep -q '^## Step 0: Size the panel' "$SKILL"; assert_true "$?" "skill sizes the panel first"
grep -qi "panel isn't worth it" "$SKILL"; assert_true "$?" "skill can decline a panel for trivial work"

# Promise: the routing table covers every roster agent, and names no agent that doesn't exist.
for a in $ROSTER; do
  grep -q "\`$a\`" "$SKILL"; assert_true "$?" "skill routes to $a"
done
missing=0
for a in $(grep -o '`[a-z]*-[a-z-]*`' "$SKILL" | tr -d '`' | sort -u); do
  case "$a" in assemble-panel|general-purpose|review-panel) continue ;; esac
  [ -f "$AGENTS/$a.md" ] || { missing=1; echo "skill names unknown agent: $a" >&2; }
done
assert_eq "0" "$missing" "skill names only agents that exist"

# Promise: prints the roster with reasons and what was skipped, then dispatches.
grep -q '^## Step 3: Print the roster' "$SKILL"; assert_true "$?" "skill prints the roster"
grep -q '^Skipped:' "$SKILL"; assert_true "$?" "roster example lists skipped reviewers"

# Promise: briefs open with a fixed goal and vary context.
grep -q "don't argue against the goal itself" "$REFS/brief-template.md"; assert_true "$?" "brief opens with the fixed goal"
grep -q '## Context you get' "$REFS/brief-template.md"; assert_true "$?" "brief sets a deliberate context level"
grep -q "Reviewers load the session's CLAUDE.md chain" "$SKILL"; assert_true "$?" "skill passes rules CLAUDE.md doesn't carry into briefs"

# Promise: parallel dispatch, then noise-filter.
grep -q 'in \*\*one message\*\*' "$SKILL"; assert_true "$?" "skill dispatches in parallel"
grep -q '^## Step 6: Filter' "$SKILL"; assert_true "$?" "skill runs the noise-filter pass"

# Promise: ad-hoc personas for uncovered lenses use the shared contract.
[ -f "$REFS/persona-contract.md" ]; assert_true "$?" "persona-contract reference exists"
grep -q 'Severity: BLOCK | SHOULD | COULD' "$REFS/persona-contract.md"; assert_true "$?" "ad-hoc personas use the shared contract"

# Promise: never publishes without the user's go-ahead.
grep -qi "Don't apply fixes, post comments or publish" "$SKILL"; assert_true "$?" "skill never posts without approval"

assert_summary
