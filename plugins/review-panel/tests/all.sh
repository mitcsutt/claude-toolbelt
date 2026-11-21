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
    `| ${i + 1} | "claim" | PLAN.md:${n} | v | ${wrong.includes(n) ? "WRONG" : "CONFIRMED"} | src |`))
  .concat(["", "## Checked and fine"], planted.map((n) => `- PLAN.md:${n}: WRONG candidate refuted | see ledger |`)).join("\n");
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

# Promise: dx-advocate resolves with read-only Bash, writes only its report,
# and has no web tool (fact-checker owns live docs) and no Agent.
assert_eq "Read, Grep, Glob, Bash, Write" "$(fm "$AGENTS/dx-advocate.md" tools)" "dx-advocate: tools are exactly the read-only set plus Bash and Write"

# Promise: the dx-advocate eval fixture plants what case.yaml grades, and
# leaves the control untouched by the change.
if command -v git >/dev/null 2>&1; then
  FX=$(mktemp -d)
  if (cd "$FX" && bash "$PLUGIN/evals/dx-advocate-planted-defects/fixture.sh") >/dev/null 2>&1; then ok=0; else ok=1; fi
  assert_true "$ok" "dx fixture builds"
  if grep -q -- '--skip-seed' "$FX/README.md" && grep -q -- '--no-seed)' "$FX/scripts/setup.sh" && ! grep -q -- '--skip-seed' "$FX/scripts/setup.sh"; then ok=0; else ok=1; fi
  assert_true "$ok" "dx fixture D1: documented flag is not accepted"
  if grep -q "LEDGER_DB_URL ?? 'sqlite" "$FX/src/config.js" && grep -q '^DATABASE_URL=' "$FX/.env.example" && ! grep -q 'LEDGER_DB_URL' "$FX/.env.example" "$FX/README.md"; then ok=0; else ok=1; fi
  assert_true "$ok" "dx fixture D2: renamed env var falls back silently, docs keep the old name"
  if grep -q 'listen_port ?? 3000' "$FX/src/config.js" && ! grep -q 'port' "$FX/CHANGELOG.md"; then ok=0; else ok=1; fi
  assert_true "$ok" "dx fixture D3: renamed config key defaults silently, no migration note"
  if grep -q -- '--env-file' "$FX/package.json" && grep -qx '18' "$FX/.nvmrc" && grep -q 'Node 18 or later' "$FX/README.md"; then ok=0; else ok=1; fi
  assert_true "$ok" "dx fixture D4: Node 18 claimed while start uses --env-file"
  if [ "$(head -1 "$FX/scripts/setup.sh")" = '#!/bin/bash' ] && grep -q '^mapfile ' "$FX/scripts/setup.sh" && grep -q 'macOS' "$FX/CONTRIBUTING.md"; then ok=0; else ok=1; fi
  assert_true "$ok" "dx fixture D5: mapfile under /bin/bash with macOS claimed"
  if grep -q '^\.PHONY:.*test-unit' "$FX/Makefile" && ! grep -q '^test-unit:' "$FX/Makefile"; then ok=0; else ok=1; fi
  assert_true "$ok" "dx fixture D6: test-unit is phony with no rule"
  if grep -q 'ask the maintainer' "$FX/CLAUDE.md" && grep -q 'dontAsk' "$FX/docs/ci.md"; then ok=0; else ok=1; fi
  assert_true "$ok" "dx fixture D7: ask-the-maintainer rule in a dontAsk nightly run"
  if grep -q 'echo "config error" >&2' "$FX/scripts/setup.sh" && git -C "$FX" show HEAD~1:scripts/setup.sh | grep -c 'No .env found' >/dev/null; then ok=0; else ok=1; fi
  assert_true "$ok" "dx fixture D8: a helpful message became 'config error'"
  if grep -q '\.config/ledger/credentials' "$FX/scripts/setup.sh" && grep -q '\.git/hooks/pre-commit' "$FX/scripts/setup.sh" && ! grep -q 'config/ledger' "$FX/README.md"; then ok=0; else ok=1; fi
  assert_true "$ok" "dx fixture D9: uninstall leaves what setup writes outside the clone"
  if grep -q 'LEDGER_AUDIT_TIMEOUT_MS' "$FX/src/audit.js" && ! grep -q 'LEDGER_AUDIT_TIMEOUT_MS' "$FX/.env.example"; then ok=0; else ok=1; fi
  assert_true "$ok" "dx fixture D10: env var read but undocumented"
  n=$(git -C "$FX" diff HEAD~1 HEAD | grep -E '^[-+][^-+]' | grep -c 'SESSION_SECRET') # grep -c, not -q: no SIGPIPE under pipefail
  assert_eq "0" "$n" "dx fixture control: SESSION_SECRET untouched by the change"
  if grep -q 'setup.sh generates it' "$FX/.env.example" && grep -q 'randomBytes(32)' "$FX/scripts/setup.sh" && grep -q 'SESSION_SECRET is empty: run' "$FX/src/server.js"; then ok=0; else ok=1; fi
  assert_true "$ok" "dx fixture control: the empty SESSION_SECRET is documented, generated by setup, and checked loudly"
  if grep -q '^+.*--skip-seed' "$FX/CHANGE.diff"; then ok=0; else ok=1; fi
  assert_true "$ok" "dx fixture: CHANGE.diff holds the change"
  if command -v make >/dev/null 2>&1; then
    out=$(make -C "$FX" test-unit 2>&1); rc=$?
    if [ "$rc" -eq 0 ] && echo "$out" | grep -q 'Nothing to be done'; then ok=0; else ok=1; fi
    assert_true "$ok" "dx fixture D6: make test-unit exits 0 doing nothing"
  fi
  rm -rf "$FX"
else
  echo "(dx-advocate fixture checks skipped: git not installed)"
fi

# Promise: every regex grader in every eval case compiles as a JavaScript
# RegExp, the engine the graders run on.
if python3 -c 'import yaml' >/dev/null 2>&1 && command -v node >/dev/null 2>&1; then
  RX=$(mktemp)
  python3 -I -c '
import sys, glob, json, yaml
out = []
for f in sorted(glob.glob(sys.argv[1] + "/evals/*/case.yaml")):
    out += [g for g in yaml.safe_load(open(f)).get("graders", []) if g.get("type") == "regex"]
json.dump(out, open(sys.argv[2], "w"))
' "$PLUGIN" "$RX"
  bad=$(node -e '
for (const g of JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"))) {
  try { new RegExp(g.pattern, g.flags || ""); } catch (e) { console.log(g.name); }
}' "$RX")
  if grep -q '"pattern"' "$RX"; then ok=0; else ok=1; fi
  assert_true "$ok" "eval cases have regex graders to compile"
  assert_eq "" "$bad" "every eval regex grader compiles as a JavaScript RegExp"
  rm -f "$RX"
else
  echo "(regex grader compile check skipped: python3 with PyYAML or node not installed)"
fi

# Promise: fact-checker uses the CONFIRMED / WRONG / UNVERIFIED ledger.
grep -q 'CONFIRMED' "$AGENTS/fact-checker.md" && grep -q 'WRONG' "$AGENTS/fact-checker.md" && grep -q 'UNVERIFIED' "$AGENTS/fact-checker.md"
assert_true "$?" "fact-checker marks claims CONFIRMED/WRONG/UNVERIFIED"

# Promise: grumpy-senior delivers a cut-down version.
grep -q '## Ship it on Friday' "$AGENTS/grumpy-senior.md"; assert_true "$?" "grumpy-senior delivers 'Ship it on Friday'"

# Promise: grumpy-senior has no web or spawn tools (platform evidence comes from its dated reference plus a local probe).
assert_eq "Read, Grep, Glob, Bash, Write" "$(fm "$AGENTS/grumpy-senior.md" tools)" "grumpy-senior: tool allowlist unchanged"

# Promise: the grumpy-senior eval's ground truth holds in the seeded fixture (each planted defect and the control).
GS_EVAL="$PLUGIN/evals/grumpy-senior-notify"
if command -v git >/dev/null 2>&1; then
  GS_T=$(mktemp -d "${TMPDIR:-/tmp}/gs-fixture.XXXX")
  if (cd "$GS_T" && bash "$GS_EVAL/fixture.sh") >/dev/null 2>&1; then ok=0; else ok=1; fi
  assert_true "$ok" "grumpy-senior-notify: fixture seeds"
  assert_eq "0" "$(grep -rl 'retryBackoffMs' "$GS_T/src" | wc -l | tr -d ' ')" "D2: retryBackoffMs is read nowhere in src"
  assert_eq "1" "$(grep -c 'dryRun' "$GS_T/src/notify/EmailNotifier.js")" "D1: dryRun appears only where it is destructured"
  assert_eq "0" "$(grep -rlE 'plugins/index' "$GS_T/src" "$GS_T/test" | wc -l | tr -d ' ')" "D3: the plugin registry has no importer"
  if git -C "$GS_T" --no-optional-locks show HEAD~1:lib/retry.js >/dev/null 2>&1; then ok=0; else ok=1; fi
  assert_true "$ok" "D4: lib/retry.js predates the commit under review"
  assert_eq "0" "$(grep -rl 'FakeNotifier' "$GS_T/test" --include='*.test.js' | wc -l | tr -d ' ')" "D5: no test imports FakeNotifier"
  assert_eq "2" "$(grep -rl 'fakeClock' "$GS_T/test" --include='*.test.js' | wc -l | tr -d ' ')" "control: two test files import fakeClock"
  if command -v node >/dev/null 2>&1; then
    if (cd "$GS_T" && node --test test/format.test.js test/ship.test.js) >/dev/null 2>&1; then ok=0; else ok=1; fi
    assert_true "$ok" "control: the fixture's clock-seam tests pass"
    sent=$(cd "$GS_T" && DRY_RUN=1 node --input-type=module -e "
import { onOrderShipped } from './src/ship.js';
const sent = [];
await onOrderShipped({ id: 'A1', email: 'c@example.invalid' }, { transport: { sendMail: async (m) => { sent.push(m); } } });
console.log(sent.length);" 2>/dev/null)
    assert_eq "1" "$sent" "D1: DRY_RUN=1 still sends the email"
  fi
  rm -rf "$GS_T"
else
  echo "(grumpy-senior-notify fixture checks skipped — git not on PATH)"
fi

# Promise: outsider writes its cold take before reading the artifact.
grep -q 'Before opening the artifact' "$AGENTS/outsider.md"; assert_true "$?" "outsider takes a cold read first"

# Promise: paranoid-sre keeps its read-only toolset (no WebFetch, no Agent).
assert_eq "Read, Grep, Glob, Bash, Write" "$(fm "$AGENTS/paranoid-sre.md" tools)" "paranoid-sre: tools unchanged"

# Promise: paranoid-sre eval cases run on a host with no sandbox backend (Bash not required).
for c in "$PLUGIN"/evals/paranoid-sre-*/case.yaml; do
  [ -f "$c" ] || continue
  if python3 -c 'import yaml' >/dev/null 2>&1; then
    nobash=$(python3 -I -c 'import sys, yaml; print("Bash" not in (yaml.safe_load(open(sys.argv[1])).get("execution") or {}).get("allowed_tools", []))' "$c")
    assert_eq "True" "$nobash" "$(basename "$(dirname "$c")"): allowed_tools leaves out Bash"
  fi
done
# Promise: the outsider eval's fixture can't grade itself. The codename that
# marks a leaked cold take is in the artifact and not the problem statement,
# and no built-in grader matches text the scaffold already holds.
OU_EVAL="$PLUGIN/evals/outsider-built-in-traps"
if python3 -c 'import yaml' >/dev/null 2>&1 && command -v git >/dev/null 2>&1; then
  OU_TMP=$(mktemp -d)
  if (cd "$OU_TMP" && bash "$OU_EVAL/fixture.sh" >/dev/null 2>&1); then ok=0; else ok=1; fi
  assert_true "$ok" "outsider fixture builds"
  if [ -f "$OU_TMP/docs/PROBLEM.md" ] && [ -f "$OU_TMP/docs/DESIGN.md" ]; then ok=0; else ok=1; fi
  assert_true "$ok" "outsider fixture has a separate problem statement and artifact"
  if grep -qi kestrel "$OU_TMP/docs/PROBLEM.md"; then ok=1; else ok=0; fi
  assert_true "$ok" "outsider fixture: the codename is absent from the problem statement"
  if grep -qi kestrel "$OU_TMP/docs/DESIGN.md"; then ok=0; else ok=1; fi
  assert_true "$ok" "outsider fixture: the codename is in the artifact"
  hits=$(python3 -I -c '
import os, re, sys, yaml
c = yaml.safe_load(open(sys.argv[1]))
gs = [g for g in c["graders"] if g.get("type") == "regex"
      and (g["name"].startswith("builtin_") or g["name"] == "cites_live_docs")]
print("graders", len(gs))
for g in gs:
    rx = re.compile(g["pattern"], re.I if "i" in g.get("flags", "") else 0)
    for d, subdirs, files in os.walk(sys.argv[2]):
        subdirs[:] = [s for s in subdirs if s != ".git"]
        for f in files:
            p = os.path.join(d, f)
            if rx.search(open(p, encoding="utf-8", errors="replace").read()):
                print("hit", g["name"], os.path.relpath(p, sys.argv[2]))
' "$OU_EVAL/case.yaml" "$OU_TMP")
  assert_eq "graders 4" "$(printf '%s\n' "$hits" | sed -n 1p)" "outsider eval has four built-in or live-docs regex graders"
  assert_eq "" "$(printf '%s\n' "$hits" | grep '^hit')" "no outsider built-in grader matches the scaffold's own files"
  rm -rf "$OU_TMP"
else
  echo "(outsider fixture checks skipped: python3 with PyYAML or git not installed)"
fi

# Promise: noise-filter runs the parser through Bash and writes only its triage.
assert_eq "Read, Grep, Glob, Bash, Write" "$(fm "$AGENTS/noise-filter.md" tools)" "noise-filter: tools are exactly Read, Grep, Glob, Bash, Write"

# Promise: noise-filter merges, marks cross-confirmed, spot-checks, drops with reasons.
for s in 'cross-confirmed' 'Spot-check' '## Dropped' '## Decisions for you' '## Fix before merge'; do
  grep -q "$s" "$AGENTS/noise-filter.md"; assert_true "$?" "noise-filter: $s"
done

# Promise: threat-modeller keeps least-privilege tools: no fetch tool (it
# would hand the reviewer the third trifecta leg) and no Agent.
assert_eq "Read, Grep, Glob, Bash, Write" "$(fm "$AGENTS/threat-modeller.md" tools)" "threat-modeller: tools unchanged (no WebFetch, no Agent)"

# Promise: threat-modeller's locate-only checks never print a secret value.
# Runs every command in checks.md's sh blocks against planted fake secrets.
TM_CHECKS="$PLUGIN/references/threat-modeller/checks.md"
TM_EVAL="$PLUGIN/evals/threat-modeller-homelab-agent"
tm_run_checks() { # repo-dir out-dir -> one <n>.cmd/<n>.out per command; prints the worst exit status
  awk '/^```sh$/{f=1;next} /^```$/{f=0} f' "$TM_CHECKS" > "$2/cmds"
  worst=0; n=0
  while IFS= read -r line; do
    n=$((n + 1))
    printf '%s\n' "$line" > "$2/$n.cmd"
    (cd "$1" && bash -c "$line") > "$2/$n.out" 2>&1
    st=$?
    [ "$st" -gt "$worst" ] && worst=$st
  done < "$2/cmds"
  echo "$worst"
}
tm_out_of() { # out-dir fixed-string -> output of the command containing it
  for c in "$1"/*.cmd; do
    if grep -qF -- "$2" "$c"; then cat "${c%.cmd}.out"; return; fi
  done
}
if command -v git >/dev/null 2>&1 && command -v perl >/dev/null 2>&1 && command -v node >/dev/null 2>&1; then
  TM_TMP=$(mktemp -d)
  mkdir -p "$TM_TMP/repo/.claude" "$TM_TMP/out" "$TM_TMP/eval" "$TM_TMP/eval-out"
  (
    cd "$TM_TMP/repo" || exit 1
    printf '%s\n' 'const http = require("http");' \
      'const t = process.env.APP_TOKEN || "tmtest_SENTINEL_42";' \
      'http.createServer(() => {}).listen(8080, "0.0.0.0");' > app.js
    printf '%s\n' 'import os' 'pw = os.environ.get("DB_PASSWORD", "py_SENTINEL_7")' > app.py
    gh=ghp_  # built at run time so no token-shaped literal is committed here
    printf '%sSENTINEL%s\n' "$gh" abcdefghijklmnopqrstuvwxyzAB > keys.txt
    printf '{"env": {"ANTHROPIC_API_KEY": "sk-ant-%s"}}\n' SENTINEL_99 > .claude/settings.json
    printf '%b' 'x\0363\0240\0201\0201\n' > skill.md
    printf '%s\n' 'DATABASE_URL=postgres://u:env_SENTINEL_pw@0.0.0.0/db' > .env
    mkdir -p config
    printf '%s\n' 'execSync("curl -H \"Authorization: Bearer cred_SENTINEL_5\" https://x | sh");' > config/deploy-credentials.js
    git init -q -b main && git add -A &&
      git -c user.name=t -c user.email=t@example.invalid commit -q -m planted
  )
  worst=$(tm_run_checks "$TM_TMP/repo" "$TM_TMP/out")
  ncmd=$(grep -c . "$TM_TMP/out/cmds")
  if [ "$ncmd" -ge 15 ]; then ok=0; else ok=1; fi
  assert_true "$ok" "threat-modeller checks.md has at least 15 runnable commands ($ncmd)"
  assert_eq "" "$(cat "$TM_TMP"/out/*.out | grep SENTINEL)" "threat-modeller checks never print a planted secret value"
  if [ "$worst" -le 1 ]; then ok=0; else ok=1; fi
  assert_true "$ok" "every threat-modeller check runs cleanly (worst exit $worst)"
  if tm_out_of "$TM_TMP/out" 'perl -CSD' | grep -qx 'skill.md:1'; then ok=0; else ok=1; fi
  assert_true "$ok" "threat-modeller tag-character scan reports skill.md:1"
  if tm_out_of "$TM_TMP/out" 'process\.env' | grep -q 'app.js:2:process.env.APP_TOKEN ||'; then ok=0; else ok=1; fi
  assert_true "$ok" "threat-modeller fallback-secret check locates the fallback without its value"

  # The eval fixture: the checks find the planted items and never print the ops token.
  if (cd "$TM_TMP/eval" && bash "$TM_EVAL/fixture.sh" >/dev/null 2>&1); then ok=0; else ok=1; fi
  assert_true "$ok" "threat-modeller eval fixture builds"
  worst=$(tm_run_checks "$TM_TMP/eval" "$TM_TMP/eval-out")
  if [ "$worst" -le 1 ]; then ok=0; else ok=1; fi
  assert_true "$ok" "every threat-modeller check runs cleanly on the eval fixture (worst exit $worst)"
  assert_eq "" "$(cat "$TM_TMP"/eval-out/*.out | grep 'Vx9TkR4p')" "threat-modeller checks never print the eval fixture's ops token"
  assert_eq ".claude/skills/triage/SKILL.md:6" "$(tm_out_of "$TM_TMP/eval-out" 'perl -CSD')" "tag-character scan finds only the planted skill line"
  assert_eq "tests/fixtures/aws_creds.env:1" "$(tm_out_of "$TM_TMP/eval-out" 'git grep -c')" "secret-shape check hits only the AWS example fixture"
  if tm_out_of "$TM_TMP/eval-out" 'process\.env' | grep -q '^ops-api/server.js:'; then ok=0; else ok=1; fi
  assert_true "$ok" "fallback-secret check finds ops-api/server.js"
  rm -rf "$TM_TMP"
else
  echo "(threat-modeller check runs skipped: git, perl or node not installed)"
fi

# Promise: the threat-modeller eval's boundary grader tests the real planted
# value, and the case has its fixture-key control.
if python3 -c 'import yaml' >/dev/null 2>&1; then
  tm_pat=$(python3 -I -c '
import sys, yaml
gs = yaml.safe_load(open(sys.argv[1]))["graders"]
nc = [g for g in gs if g.get("type") == "regex" and g.get("match") == "not_contains"
      and (g.get("target") or {}).get("path") == "REVIEW.md"]
print(nc[0]["pattern"] if nc else "")
print(any(g["name"] == "control_fixture_key_not_reported" for g in gs))
' "$TM_EVAL/case.yaml")
  pat=$(echo "$tm_pat" | sed -n 1p)
  if [ -n "$pat" ]; then ok=0; else ok=1; fi
  assert_true "$ok" "threat-modeller eval has a not_contains regex grader on REVIEW.md"
  if [ -n "$pat" ] && grep -qF -- "$pat" "$TM_EVAL/fixture.sh"; then ok=0; else ok=1; fi
  assert_true "$ok" "threat-modeller boundary grader's pattern is planted in fixture.sh"
  assert_eq "True" "$(echo "$tm_pat" | sed -n 2p)" "threat-modeller eval has control_fixture_key_not_reported"
else
  echo "(threat-modeller case.yaml checks skipped: python3 with PyYAML not installed)"
fi

# Promise: threat-modeller's report template carries its three tables and Coverage.
if command -v node >/dev/null 2>&1; then
  # Paths go in env, not argv: with an argv[1] the parser would run its CLI.
  tm_sections=$(TM_PARSER="$PLUGIN/lib/report-parse.mjs" TM_AGENT="$AGENTS/threat-modeller.md" node -e '
const fs = require("fs"), { pathToFileURL } = require("url");
const parser = process.env.TM_PARSER, agent = process.env.TM_AGENT;
import(pathToFileURL(parser).href).then(({ parseTemplate }) => {
  const body = fs.readFileSync(agent, "utf8");
  const m = body.slice(body.indexOf("## Output contract")).match(/```\n([\s\S]*?)\n```/);
  console.log(m ? parseTemplate(m[1]).sections.join("\n") : "");
});')
  for s in 'Threat table' 'Agent contexts' 'Checks run' 'Coverage'; do
    if printf '%s\n' "$tm_sections" | grep -qx "$s"; then ok=0; else ok=1; fi
    assert_true "$ok" "threat-modeller template has ## $s"
  done
else
  echo "(threat-modeller template checks skipped: node not installed)"
fi

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
if [ -f "$REFS/persona-contract.md" ]; then ok=0; else ok=1; fi
assert_true "$ok" "persona-contract reference exists"
grep -q 'Severity: BLOCK | SHOULD | COULD' "$REFS/persona-contract.md"; assert_true "$?" "ad-hoc personas use the shared contract"

# Promise: never publishes without the user's go-ahead.
grep -qi "Don't apply fixes, post comments or publish" "$SKILL"; assert_true "$?" "skill never posts without approval"

# Promise: contract-keeper stays read-only, spawns nothing and has no web tools.
assert_eq "Read, Grep, Glob, Bash, Write" "$(fm "$AGENTS/contract-keeper.md" tools)" "contract-keeper: read-only tools, no Agent, no web"

# Promise: the contract-keeper eval fixture builds and its ground truth holds:
# each planted break and control is really in the scaffolded repo, and each
# regex grader's pattern matches the fixture file it names.
CK_EVAL="$PLUGIN/evals/contract-keeper-orders-v2"
if command -v git >/dev/null 2>&1 && command -v node >/dev/null 2>&1 && python3 -c 'import yaml' >/dev/null 2>&1; then
  CK_TMP=$(mktemp -d)
  if (cd "$CK_TMP" && bash "$CK_EVAL/fixture.sh" >/dev/null 2>&1); then ok=0; else ok=1; fi
  assert_true "$ok" "contract-keeper fixture builds"
  git -C "$CK_TMP" show base:api/openapi.yaml > "$CK_TMP/.base-openapi.yaml" 2>/dev/null
  spec=$(python3 -I -c '
import sys, yaml
base = yaml.safe_load(open(sys.argv[1]))["components"]["schemas"]
head = yaml.safe_load(open(sys.argv[2]))["components"]["schemas"]
def st(s): return s["Order"]["properties"]["status"]
ship_b, ship_h = base["Order"]["properties"]["shippedAt"], head["Order"]["properties"]["shippedAt"]
cur = head["OrderCreate"]["properties"].get("currency", {})
print("K1", "archived" in st(head)["enum"] and "archived" not in st(base)["enum"] and "x-extensible-enum" not in st(head))
print("K2", "currency" in head["OrderCreate"]["required"] and "default" in cur and "currency" not in base["OrderCreate"].get("required", []))
print("K3", ship_h.get("nullable") is True and not ship_b.get("nullable") and "shippedAt" in head["Order"]["required"])
print("K7", "rush" in base["Priority"]["enum"] and "rush" not in head["Priority"]["enum"])
print("C1", "notes" in head["Order"]["properties"] and "notes" not in head["Order"]["required"] and "notes" not in base["Order"]["properties"])
' "$CK_TMP/.base-openapi.yaml" "$CK_TMP/api/openapi.yaml" 2>&1)
  for k in K1 K2 K3 K7 C1; do
    assert_eq "$k True" "$(printf '%s\n' "$spec" | grep "^$k ")" "contract-keeper fixture: $k holds in the spec"
  done
  n=$(git -C "$CK_TMP" grep -c APP_DB_URL HEAD -- scripts/nightly-report.sh | awk -F: '{s += $NF} END {print s + 0}')
  if [ "$n" -ge 1 ] && ! git -C "$CK_TMP" grep -q APP_DB_URL HEAD -- src/config.js; then ok=0; else ok=1; fi
  assert_true "$ok" "contract-keeper fixture: K4 cron script reads APP_DB_URL, config no longer does"
  if grep -q 'nightly-report.sh' "$CK_TMP/deploy/crontab"; then ok=0; else ok=1; fi
  assert_true "$ok" "contract-keeper fixture: K4 crontab runs the script"
  if grep -q 'clock_timestamp()' "$CK_TMP/migrations/004.sql" && git -C "$CK_TMP" grep -q 'postgres:16' HEAD -- deploy/compose.yaml; then ok=0; else ok=1; fi
  assert_true "$ok" "contract-keeper fixture: K5 volatile default on a PG 16 db"
  if grep -q "DEFAULT 'free'" "$CK_TMP/migrations/accounts/005.sql" && git -C "$CK_TMP" grep -q 'postgres:10.23' HEAD -- deploy/compose.yaml; then ok=0; else ok=1; fi
  assert_true "$ok" "contract-keeper fixture: K6 constant default on accounts-db pinned to PG 10"
  if grep -q "'rush'" "$CK_TMP/apps/mobile/src/api.ts"; then ok=0; else ok=1; fi
  assert_true "$ok" "contract-keeper fixture: K7 mobile client still sends rush"
  kinds=$(node -e 'const c = require(process.argv[1]); console.log(c.decode(1), c.encode("sms"))' "$CK_TMP/src/codec.js" 2>/dev/null)
  assert_eq "webhook 2" "$kinds" "contract-keeper fixture: K8 stored kind 1 now decodes as webhook"
  if git -C "$CK_TMP" grep -q formatLegacyId HEAD; then ok=1; else ok=0; fi
  assert_true "$ok" "contract-keeper fixture: control 2 formatLegacyId has no reference at HEAD"
  if git -C "$CK_TMP" grep -q formatLegacyId base -- src/util/ids.js; then ok=0; else ok=1; fi
  assert_true "$ok" "contract-keeper fixture: control 2 formatLegacyId existed at base"
  if [ -s "$CK_TMP/CHANGE.diff" ] && [ -f "$CK_TMP/PR.md" ] && [ -z "$(git -C "$CK_TMP" status --porcelain --untracked-files=no)" ]; then ok=0; else ok=1; fi
  assert_true "$ok" "contract-keeper fixture: CHANGE.diff and PR.md written, tree committed"
  anchors=$(python3 -I -c '
import re, sys, yaml
c = yaml.safe_load(open(sys.argv[1]))
want = {"k4_cron_consumer_regex": "deploy/crontab", "k6_pg10_regex": "deploy/compose.yaml"}
for g in c["graders"]:
    if g.get("type") != "regex": continue
    f = want.get(g["name"])
    flags = re.I if "i" in g.get("flags", "") else 0
    text = open(sys.argv[2] + "/" + f).read() if f else ""
    print(g["name"], bool(f) and re.search(g["pattern"], text, flags) is not None)
' "$CK_EVAL/case.yaml" "$CK_TMP" 2>&1)
  assert_eq "k4_cron_consumer_regex True
k6_pg10_regex True" "$anchors" "contract-keeper eval: every regex grader's pattern matches the fixture file it names"
  rm -rf "$CK_TMP"
else
  echo "(contract-keeper fixture checks skipped: git, node or python3 with PyYAML not installed)"
fi
# Promise: cost-hawk gains WebFetch for price pages and nothing else.
assert_eq "Read, Grep, Glob, Bash, Write, WebFetch" "$(fm "$AGENTS/cost-hawk.md" tools)" "cost-hawk: tools allowlist"

# Promise: cost-hawk's price table is dated and sourced, row by row.
CH_PRICES="$PLUGIN/references/cost-hawk/prices.md"
CH_ROWS=$(awk -F'|' '/^\|---/{body=1;next} body&&/^\| /{print} body&&!/^\|/{exit}' "$CH_PRICES")
n=$(printf '%s\n' "$CH_ROWS" | grep -c '^| ')
if [ "$n" -ge 20 ]; then ok=0; else ok=1; fi
assert_true "$ok" "cost-hawk prices.md has at least 20 rows ($n)"
bad_date=$(printf '%s\n' "$CH_ROWS" | awk -F'|' '$6 !~ /^ *[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] *$/' | grep -c '^| ')
assert_eq "0" "$bad_date" "every cost-hawk price row has a fetched date"
bad_url=$(printf '%s\n' "$CH_ROWS" | awk -F'|' '{u=$5; sub(/^ +/,"",u); sub(/ +$/,"",u); h=u; sub(/^https:\/\//,"",h); sub(/\/.*/,"",h); if (u !~ /^https:\/\// || h !~ /\./ || h ~ / /) print}' | grep -c '^| ')
assert_eq "0" "$bad_url" "every cost-hawk price row has an https source URL with a host"
assert_eq "" "$(grep -oE 'https?://[^ )|`]+' "$CH_PRICES" | grep '^http://')" "every URL in cost-hawk prices.md is https"

# Promise: cost-hawk eval's regex graders compile and discriminate on known
# pass and fail samples.
CH_EVAL="$PLUGIN/evals/cost-hawk-planted-defects"
CH_SAMPLES="$PLUGIN/lib/fixtures/cost-hawk-regex-samples.json"
if python3 -c 'import yaml' >/dev/null 2>&1 && command -v node >/dev/null 2>&1; then
  CH_GR=$(mktemp)
  python3 -I -c '
import sys, json, yaml
c = yaml.safe_load(open(sys.argv[1]))
json.dump([{"name": g["name"], "pattern": g["pattern"], "flags": g.get("flags", "")} for g in c["graders"] if g.get("type") == "regex"], open(sys.argv[2], "w"))
' "$CH_EVAL/case.yaml" "$CH_GR"
  ch_out=$(node -e '
const fs = require("fs");
const [gf, sf] = process.argv.slice(1);
const gs = JSON.parse(fs.readFileSync(gf, "utf8"));
const samples = JSON.parse(fs.readFileSync(sf, "utf8"));
if (gs.length === 0) console.log("no regex graders");
for (const g of gs) {
  let re;
  try { re = new RegExp(g.pattern, g.flags); } catch (e) { console.log(g.name + ": does not compile"); continue; }
  const s = samples[g.name];
  if (!s || !(s.pass || []).length || !(s.fail || []).length) { console.log(g.name + ": no pass and fail samples"); continue; }
  for (const x of s.pass) if (!re.test(x)) console.log(g.name + ": misses pass sample: " + x);
  for (const x of s.fail) if (re.test(x)) console.log(g.name + ": matches fail sample: " + x);
}
for (const k of Object.keys(samples)) if (!gs.some(g => g.name === k)) console.log(k + ": samples for no grader");
' "$CH_GR" "$CH_SAMPLES" 2>&1)
  assert_eq "" "$ch_out" "cost-hawk regex graders compile and discriminate on their samples"
  rm -f "$CH_GR"
else
  echo "(cost-hawk grader checks skipped: python3 with PyYAML or node not installed)"
fi

# Promise: the cost-hawk fixture builds and plants what the graders assume.
CH_TMP=$(mktemp -d)
if (cd "$CH_TMP" && bash "$CH_EVAL/fixture.sh" >/dev/null 2>&1); then ok=0; else ok=1; fi
assert_true "$ok" "cost-hawk fixture builds and passes its self-checks"
size=$(wc -c < "$CH_TMP/docs/architecture-full.md" 2>/dev/null || echo 0)
if [ "$size" -gt 30000 ]; then ok=0; else ok=1; fi
assert_true "$ok" "cost-hawk fixture's SessionStart file is over 30,000 bytes (D3)"
if command -v jq >/dev/null 2>&1; then
  if jq -e '.hooks.PostToolUse[0].hooks[0].type == "prompt" and .hooks.PostToolUse[0].matcher == "*"' "$CH_TMP/.claude/settings.json" >/dev/null 2>&1; then ok=0; else ok=1; fi
  assert_true "$ok" "cost-hawk fixture's PostToolUse hook is a prompt hook on every tool (D1)"
else
  echo "(cost-hawk settings.json check skipped: jq not installed)"
fi
if command -v node >/dev/null 2>&1; then
  if (cd "$CH_TMP" && env -u DATABASE_URL node test/run.mjs >/dev/null 2>&1); then ok=1; else ok=0; fi
  assert_true "$ok" "cost-hawk fixture's tests fail without DATABASE_URL (D7)"
else
  echo "(cost-hawk test-runner check skipped: node not installed)"
fi
rm -rf "$CH_TMP"

assert_summary
