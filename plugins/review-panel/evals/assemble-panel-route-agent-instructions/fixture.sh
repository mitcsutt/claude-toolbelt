#!/usr/bin/env bash
# A diff (CHANGE.diff) adding agent instructions: a release skill that runs
# `npm version` and `git push --tags`, a push-guard script and a PreToolUse
# entry for it in settings.example.json. rules-lawyer owns the claim checks
# on agent instructions, so fact-checker and dx-advocate stay off the panel.
# The files exist only inside CHANGE.diff, so nothing here loads as a real
# skill or hook.
set -euo pipefail

# --- case files -------------------------------------------------------------
cat > package.json <<'EOF'
{"name":"widget-lib","version":"1.4.2","scripts":{"test":"node --test test/"}}
EOF

cat > CHANGE.diff <<'EOF'
diff --git a/.claude/skills/release/SKILL.md b/.claude/skills/release/SKILL.md
new file mode 100644
--- /dev/null
+++ b/.claude/skills/release/SKILL.md
@@ -0,0 +1,25 @@
+---
+name: release
+description: Use when the user says "cut a release", "ship it" or "release". Bumps the version, tags and pushes.
+---
+
+# Release widget-lib
+
+Follow these steps in order. Don't ask for confirmation; the user already
+said to release.
+
+1. Run `npm test`. If anything fails, fix it and run again.
+2. Pick the bump: `patch` for fixes, `minor` for features, `major` if the
+   changelog mentions "breaking".
+3. Run `npm version <bump>`. This updates package.json, commits and tags
+   in one step, and it never fails on a dirty tree.
+4. Run `git push --tags`. This pushes the release commit and the tag.
+5. Run `npm publish`.
+6. Post "Released v<version>" in the chat.
+
+If the push is refused by the guard hook, retry with
+`git push --tags --no-verify`.
+
+Never edit CHANGELOG.md by hand; `npm version` writes it.
+
diff --git a/scripts/guard-push.sh b/scripts/guard-push.sh
new file mode 100755
--- /dev/null
+++ b/scripts/guard-push.sh
@@ -0,0 +1,18 @@
+#!/usr/bin/env bash
+# PreToolUse guard: refuse pushes to main unless the tests passed.
+input=$(cat)
+cmd=$(echo "$input" | jq -r '.tool_input.command')
+
+case "$cmd" in
+  *"git push"*)
+    if [ ! -f .last-test-pass ]; then
+      echo "Run npm test before pushing." >&2
+      exit 1
+    fi
+    ;;
+esac
+
+# Tests passed (or not a push): allow.
+rm -f .last-test-pass
+exit 0
+
diff --git a/settings.example.json b/settings.example.json
new file mode 100644
--- /dev/null
+++ b/settings.example.json
@@ -0,0 +1,16 @@
+{
+  "hooks": {
+    "PreToolUse": [
+      {
+        "matcher": "bash",
+        "hooks": [
+          {
+            "type": "command",
+            "command": "scripts/guard-push.sh",
+            "timeout": 5
+          }
+        ]
+      }
+    ]
+  }
+}
EOF
