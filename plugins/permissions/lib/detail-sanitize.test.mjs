import { test } from "node:test";
import assert from "node:assert/strict";
import { sanitizeDetail } from "./detail-sanitize.mjs";

test("returns short single-line commands unchanged", () => {
  assert.equal(sanitizeDetail("git status"), "git status");
});

test("truncates long single-line commands to 200 chars with ellipsis", () => {
  const cmd = "grep -rn 'x' " + "a".repeat(300);
  const out = sanitizeDetail(cmd);
  assert.equal(out.length, 200 + 1); // 200 + "…"
  assert.ok(out.endsWith("…"));
  assert.ok(out.startsWith("grep -rn"));
});

test("strips heredoc body (<<EOF...EOF) and replaces with <<EOF …", () => {
  const cmd = `gh pr create --body "$(cat <<'EOF'
multi
line
body
EOF
)"`;
  const out = sanitizeDetail(cmd);
  assert.ok(out.includes("<<'EOF' …"));
  assert.ok(!out.includes("multi\nline"));
});

test("collapses multi-line bash scripts to first non-blank line + marker", () => {
  const cmd = `if [ -f x ]; then
  echo a
  echo b
fi`;
  const out = sanitizeDetail(cmd);
  assert.equal(out, "if [ -f x ]; then …(+3 lines)");
});

test("returns empty string for empty input", () => {
  assert.equal(sanitizeDetail(""), "");
  assert.equal(sanitizeDetail(undefined), "");
});
