#!/usr/bin/env bash
# PR 42 (PR.md, CHANGE.diff): GitHub OAuth login. An OAuth callback that
# exchanges the code, sets a session cookie and reads a client secret from
# the environment, plus a migration that adds a table and drops a column.
# High stakes (auth, an irreversible drop), over 50 changed lines, so the
# panel needs the high-stakes band with threat-modeller and contract-keeper.
set -euo pipefail

# --- case files -------------------------------------------------------------
cat > PR.md <<'EOF'
# PR 42: Add GitHub OAuth login

## Why

Users keep forgetting passwords, and password resets are our top support
ticket. Let people sign in with GitHub instead.

## What

- New route `/auth/github/callback` that finishes the OAuth flow and logs
  the user in.
- New table `oauth_identities` linking a GitHub account to a user.
- Drops `users.password_reset_token`: resets go away with this PR, since
  every user can now sign in with GitHub.

## Rollout

Merge, run the migration, deploy. `GITHUB_CLIENT_ID` and
`GITHUB_CLIENT_SECRET` are already set in production.

## Testing

Logged in locally with my own GitHub account.
EOF

cat > CHANGE.diff <<'EOF'
diff --git a/src/auth/oauth-callback.js b/src/auth/oauth-callback.js
new file mode 100644
--- /dev/null
+++ b/src/auth/oauth-callback.js
@@ -0,0 +1,62 @@
+'use strict';
+// GET /auth/github/callback?code=...&state=...
+// Finishes the GitHub OAuth flow and logs the user in.
+
+const db = require('../db');
+const sessions = require('../sessions');
+
+const CLIENT_ID = process.env.GITHUB_CLIENT_ID;
+const CLIENT_SECRET = process.env.GITHUB_CLIENT_SECRET;
+
+async function exchangeCode(code) {
+  const res = await fetch('https://github.com/login/oauth/access_token', {
+    method: 'POST',
+    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
+    body: JSON.stringify({ client_id: CLIENT_ID, client_secret: CLIENT_SECRET, code }),
+  });
+  const body = await res.json();
+  console.log('github token response', body);
+  return body.access_token;
+}
+
+async function githubUser(token) {
+  const res = await fetch('https://api.github.com/user', {
+    headers: { Authorization: `Bearer ${token}` },
+  });
+  return res.json();
+}
+
+async function findOrCreateUser(gh) {
+  const existing = await db.one(
+    'SELECT user_id FROM oauth_identities WHERE provider = $1 AND provider_id = $2',
+    ['github', String(gh.id)],
+  );
+  if (existing) return existing.user_id;
+
+  // Link to an existing account with the same email, or create one.
+  let user = await db.one('SELECT id FROM users WHERE email = $1', [gh.email]);
+  if (!user) {
+    user = await db.one('INSERT INTO users (email, name) VALUES ($1, $2) RETURNING id',
+      [gh.email, gh.name]);
+  }
+  await db.none(
+    'INSERT INTO oauth_identities (user_id, provider, provider_id) VALUES ($1, $2, $3)',
+    [user.id, 'github', String(gh.id)],
+  );
+  return user.id;
+}
+
+module.exports = async function oauthCallback(req, res) {
+  const { code } = req.query;
+  if (!code) return res.status(400).send('missing code');
+
+  const token = await exchangeCode(code);
+  const gh = await githubUser(token);
+  const userId = await findOrCreateUser(gh);
+
+  const sid = await sessions.create(userId);
+  res.cookie('sid', sid, { maxAge: 30 * 24 * 3600 * 1000 });
+  res.redirect(req.query.next || '/');
+};
+
+// state is accepted but not checked yet; see follow-up ticket.
+
diff --git a/migrations/0042_oauth_identities.sql b/migrations/0042_oauth_identities.sql
new file mode 100644
--- /dev/null
+++ b/migrations/0042_oauth_identities.sql
@@ -0,0 +1,14 @@
+-- 0042: GitHub OAuth identities; password resets removed.
+BEGIN;
+
+CREATE TABLE oauth_identities (
+  user_id     integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
+  provider    text    NOT NULL,
+  provider_id text    NOT NULL,
+  created_at  timestamptz NOT NULL DEFAULT now(),
+  PRIMARY KEY (provider, provider_id)
+);
+
+ALTER TABLE users DROP COLUMN password_reset_token;
+
+COMMIT;
EOF
