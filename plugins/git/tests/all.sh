#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
# shellcheck source=../../../tests/lib/assert.sh
source "$ROOT/tests/lib/assert.sh"

SKILL="$ROOT/plugins/git/skills/gh-pending-review/SKILL.md"

if [ -f "$SKILL" ]; then skill_exists=0; else skill_exists=1; fi
assert_true "$skill_exists" "gh-pending-review SKILL.md exists"

# Skill dir name must match its frontmatter name (also enforced by validate.sh).
sname=$(awk '/^name:/{print $2; exit}' "$SKILL")
assert_eq "gh-pending-review" "$sname" "SKILL.md frontmatter name is 'gh-pending-review'"

# Promise: append via the GraphQL addPullRequestReviewThread mutation.
grep -q 'addPullRequestReviewThread' "$SKILL"; assert_true "$?" "documents the addPullRequestReviewThread GraphQL mutation"

# Promise: detect the pending-review case first.
grep -qi 'Check for existing pending review' "$SKILL"; assert_true "$?" "checks for an existing pending review first"

# Promise: REST fallback when no pending review exists.
grep -qi 'No pending review' "$SKILL"; assert_true "$?" "documents the REST fallback path"

# Promise: never create a new review when one is pending (the original bug).
grep -qi 'Never create a new review' "$SKILL"; assert_true "$?" "warns never to create a new review when one is pending"

assert_summary
