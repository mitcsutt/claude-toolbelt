#!/usr/bin/env bash
# Drift tests for the repo-local /idea skill (.claude/skills/idea) vs the idea issue form.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# shellcheck source=lib/assert.sh
source "$HERE/lib/assert.sh"

TEMPLATE="$ROOT/.github/ISSUE_TEMPLATE/idea.yml"
SKILL="$ROOT/.claude/skills/idea/SKILL.md"
REF="$ROOT/.claude/skills/idea/references/research-section.md"

# Promise: the idea issue form exists and parses as YAML with the form keys.
yaml_check='
doc = YAML.respond_to?(:safe_load_file) ? YAML.safe_load_file(ARGV[0]) : YAML.load_file(ARGV[0])
ok = doc["name"] == "Idea" && doc["title"] == "idea: " && doc["labels"] == ["idea"] &&
     doc["description"].is_a?(String) && doc["body"].is_a?(Array) &&
     doc["body"].any? { |b| b["type"] == "textarea" && b["id"] == "problem" && b["validations"]["required"] == true } &&
     doc["body"].any? { |b| b["type"] == "dropdown" && b["attributes"]["options"] == ["Logged only", "Researched"] } &&
     doc["body"].any? { |b| b["type"] == "checkboxes" }
exit(ok ? 0 : 1)'
if command -v ruby >/dev/null 2>&1; then
  ruby -ryaml -e "$yaml_check" "$TEMPLATE"; assert_true "$?" "idea.yml parses and has the required form keys"
else
  echo "SKIP: ruby not installed - YAML parse assertion not run (idea.yml structure unchecked)"
fi

# Promise: blank issues are not disabled by a config.yml here (template set is unchanged in that respect).
if [ -f "$ROOT/.github/ISSUE_TEMPLATE/config.yml" ]; then
  ! grep -q 'blank_issues_enabled: *false' "$ROOT/.github/ISSUE_TEMPLATE/config.yml"
  assert_true "$?" "config.yml does not disable blank issues"
fi

# Promise: skill headings cannot drift from the form's field labels.
labels=$(sed -n 's/^      label: //p' "$TEMPLATE")
n=0
while IFS= read -r l; do
  [ -n "$l" ] || continue
  n=$((n + 1))
  grep -qxF "### $l" "$SKILL"; assert_true "$?" "SKILL.md has heading '### $l' from idea.yml"
done <<EOF2
$labels
EOF2
[ "$n" -ge 7 ]; assert_true "$?" "extracted all 7 form labels (got $n)"

# Promise: dropdown values and checkbox text match what the skill writes.
for v in "Logged only" "Researched"; do
  grep -qF "\`$v\`" "$SKILL"; assert_true "$?" "SKILL.md names research status '$v'"
done
cb=$(sed -n 's/^        - label: //p' "$TEMPLATE")
grep -qF -- "- [x] $cb" "$SKILL"; assert_true "$?" "SKILL.md writes the checkbox text from idea.yml"

# Promise: skill frontmatter is valid.
sname=$(awk '/^name:/{print $2; exit}' "$SKILL")
assert_eq "idea" "$sname" "SKILL.md frontmatter name is 'idea'"
head -1 "$SKILL" | grep -qx -- '---'; assert_true "$?" "SKILL.md opens frontmatter"
grep -q '^description: Use when' "$SKILL"; assert_true "$?" "SKILL.md description states when to trigger"

# Promise: two modes, dedupe, approval before filing, label, redaction, URL back.
grep -q 'Log only' "$SKILL" && grep -q 'Log + research' "$SKILL"; assert_true "$?" "skill defines both modes"
grep -qi 'default when ambiguous' "$SKILL"; assert_true "$?" "ambiguous requests default to log only"
grep -q 'gh issue list .*--label idea .*--search' "$SKILL"; assert_true "$?" "skill dedupes via gh issue list --search"
grep -qi 'explicit yes' "$SKILL"; assert_true "$?" "skill waits for approval before filing"
grep -q 'gh issue create .*--label idea' "$SKILL"; assert_true "$?" "skill files with --label idea"
grep -q 'verbatim' "$SKILL"; assert_true "$?" "problem statement kept verbatim"
grep -q '\[redacted\]' "$SKILL"; assert_true "$?" "skill redacts secrets"
grep -qi 'issue URL' "$SKILL"; assert_true "$?" "skill returns the issue URL"
grep -q 'WebSearch' "$SKILL" && grep -q 'WebFetch' "$SKILL"; assert_true "$?" "research tools allowed"
grep -q 'Bash(gh issue create' "$SKILL"; assert_false "$?" "gh issue create is not pre-approved"
grep -q 'references/research-section.md' "$SKILL"; assert_true "$?" "skill links the research template"
[ -f "$REF" ]; assert_true "$?" "research-section.md exists"
grep -q '^### Research' "$REF"; assert_true "$?" "research template has the Research heading"

assert_summary
