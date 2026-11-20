#!/usr/bin/env bash
# Seeds ledger-api: commit 1 is 1.3.0, commit 2 is the 1.4.0 change under
# review, with planted DX defects and one sound control (SESSION_SECRET).
# Ground truth is in case.yaml.
set -euo pipefail
mkdir -p src test scripts docs

# --- Commit 1: ledger-api 1.3.0 -------------------------------------------

cat > README.md <<'EOF'
# ledger-api

Small HTTP service that records ledger entries.

## Requirements

- Node 18 or later

## Setup

1. `cp .env.example .env` and set `DATABASE_URL`.
2. `cp config.example.json config.json`
3. `./scripts/setup.sh` (pass `--no-seed` to skip the demo data)
4. `npm start`

## Configuration

`config.json` sets `port` (default 3000).

## Uninstall

`rm -rf node_modules dev.db`
EOF

cat > CONTRIBUTING.md <<'EOF'
# Contributing

Supported dev platforms: macOS and Linux.

1. Follow the setup in README.md.
2. Run `make test` before pushing.
EOF

cat > CLAUDE.md <<'EOF'
# ledger-api

- Every env var the code reads must be listed in `.env.example` with a comment.
- Run `make test` before pushing.
EOF

cat > .env.example <<'EOF'
# Postgres connection string for the ledger database
DATABASE_URL=postgres://localhost:5432/ledger
# Session signing key. Leave it empty: scripts/setup.sh generates it on first run.
SESSION_SECRET=
EOF

printf '18\n' > .nvmrc

cat > package.json <<'EOF'
{
  "name": "ledger-api",
  "version": "1.3.0",
  "private": true,
  "engines": { "node": ">=18" },
  "scripts": {
    "start": "node -r ./src/load-env.js src/server.js",
    "test": "node --test",
    "migrate": "node src/migrate.js"
  }
}
EOF

printf '{ "port": 8080 }\n' > config.example.json

cat > CHANGELOG.md <<'EOF'
## 1.3.0

- Add migrations script.
EOF

printf '.PHONY: test lint\n\ntest:\n\tnpm test\n\nlint:\n\tnode --check src/server.js\n' > Makefile

cat > src/load-env.js <<'EOF'
// Loads .env into process.env without overriding variables already set.
const fs = require('fs');

if (fs.existsSync('.env')) {
  for (const line of fs.readFileSync('.env', 'utf8').split('\n')) {
    const m = line.match(/^([A-Z][A-Z0-9_]*)=(.*)$/);
    if (m && process.env[m[1]] === undefined) process.env[m[1]] = m[2];
  }
}
EOF

cat > src/config.js <<'EOF'
const fs = require('fs');

function load() {
  const cfg = fs.existsSync('config.json') ? JSON.parse(fs.readFileSync('config.json', 'utf8')) : {};
  if (!process.env.DATABASE_URL) {
    throw new Error('DATABASE_URL is not set: copy .env.example to .env and set it');
  }
  return { dbUrl: process.env.DATABASE_URL, port: cfg.port ?? 3000 };
}

module.exports = { load };
EOF

cat > src/server.js <<'EOF'
const http = require('http');
const { load } = require('./config.js');

if (!process.env.SESSION_SECRET) {
  console.error('SESSION_SECRET is empty: run ./scripts/setup.sh to generate it');
  process.exit(1);
}

const cfg = load();
http.createServer((req, res) => res.end('ok')).listen(cfg.port, () => {
  console.log(`ledger-api listening on :${cfg.port}`);
});
EOF

cat > src/migrate.js <<'EOF'
console.log('migrations: none pending');
EOF

cat > test/config.test.js <<'EOF'
const test = require('node:test');
const assert = require('node:assert');

test('config exposes load()', () => {
  assert.equal(typeof require('../src/config.js').load, 'function');
});
EOF

# The SESSION_SECRET block (the control) is byte-identical in both commits.
write_setup() { # missing-env message, extra block
  {
    cat <<'EOF'
#!/bin/bash
set -euo pipefail

SEED=1
while [ $# -gt 0 ]; do
  case "$1" in
    --no-seed) SEED=0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ ! -f .env ]; then
EOF
    printf '%s\n' "$1"
    cat <<'EOF'
  exit 1
fi

if ! grep -q '^SESSION_SECRET=.' .env; then
  secret=$(node -e "console.log(require('crypto').randomBytes(32).toString('hex'))")
  { grep -v '^SESSION_SECRET=' .env || true; } > .env.tmp
  echo "SESSION_SECRET=$secret" >> .env.tmp
  mv .env.tmp .env
fi
EOF
    if [ -n "$2" ]; then printf '%s\n' "$2"; fi
    cat <<'EOF'

if [ "$SEED" = 1 ]; then
  node src/migrate.js
fi
echo "setup done"
EOF
  } > scripts/setup.sh
}
write_setup "  echo \"No .env found: run 'cp .env.example .env' first\" >&2" ""

git init -q -b main
git add -A
git -c user.name=fixture -c user.email=fixture@example.invalid commit -q -m "ledger-api 1.3.0"

# --- Commit 2: ledger-api 1.4.0 (the change under review) -----------------

cat > README.md <<'EOF'
# ledger-api

Small HTTP service that records ledger entries.

## Requirements

- Node 18 or later

## Setup

1. `cp .env.example .env` and set `DATABASE_URL`.
2. `cp config.example.json config.json`
3. `./scripts/setup.sh` (pass `--skip-seed` to skip the demo data)
4. `npm start`

## Configuration

`config.json` sets `listen_port` (default 3000).

## Nightly maintenance

See `docs/ci.md`.

## Uninstall

`rm -rf node_modules dev.db`
EOF

cat > CONTRIBUTING.md <<'EOF'
# Contributing

Supported dev platforms: macOS and Linux.

1. Follow the setup in README.md.
2. Run `make test-unit` before pushing.
EOF

cat > CLAUDE.md <<'EOF'
# ledger-api

- Every env var the code reads must be listed in `.env.example` with a comment.
- Run `make test-unit` before pushing.
- If a migration fails, ask the maintainer before retrying.
EOF

cat > docs/ci.md <<'EOF'
# Nightly maintenance

A cron job on the build host runs at 02:00:

    claude -p --permission-mode dontAsk "Run npm run migrate. If it fails, follow CLAUDE.md and fix it."

Nobody watches this run; read its log in the morning.
EOF

cat > package.json <<'EOF'
{
  "name": "ledger-api",
  "version": "1.4.0",
  "private": true,
  "engines": { "node": ">=18" },
  "scripts": {
    "start": "node --env-file=.env src/server.js",
    "test": "node --test",
    "migrate": "node src/migrate.js"
  }
}
EOF
rm src/load-env.js

printf '{ "listen_port": 8080 }\n' > config.example.json

cat > src/config.js <<'EOF'
const fs = require('fs');

function load() {
  const cfg = fs.existsSync('config.json') ? JSON.parse(fs.readFileSync('config.json', 'utf8')) : {};
  return { dbUrl: process.env.LEDGER_DB_URL ?? 'sqlite://./dev.db', port: cfg.listen_port ?? 3000 };
}

module.exports = { load };
EOF

cat > src/audit.js <<'EOF'
const timeoutMs = Number(process.env.LEDGER_AUDIT_TIMEOUT_MS ?? 5000);

function start() {
  setInterval(() => {}, timeoutMs).unref();
}

module.exports = { start };
EOF

cat > src/server.js <<'EOF'
const http = require('http');
const { load } = require('./config.js');

if (!process.env.SESSION_SECRET) {
  console.error('SESSION_SECRET is empty: run ./scripts/setup.sh to generate it');
  process.exit(1);
}

const cfg = load();
require('./audit.js').start();
http.createServer((req, res) => res.end('ok')).listen(cfg.port, () => {
  console.log(`ledger-api listening on :${cfg.port}`);
});
EOF

cat > CHANGELOG.md <<'EOF'
## 1.4.0

- Internal: tidy config loader and setup script.

## 1.3.0

- Add migrations script.
EOF

printf '.PHONY: test test-unit lint\n\ntest:\n\tnpm test\n\nlint:\n\tnode --check src/server.js\n' > Makefile

printf 'ledger\naudit\n' > services.txt

cat > scripts/pre-commit.sh <<'EOF'
#!/bin/sh
exec make lint
EOF

write_setup '  echo "config error" >&2' "$(cat <<'EOF'

mapfile -t SERVICES < services.txt
for s in "${SERVICES[@]}"; do
  echo "registering $s"
done

mkdir -p "$HOME/.config/ledger"
node -e "console.log('token=' + require('crypto').randomBytes(16).toString('hex'))" > "$HOME/.config/ledger/credentials"
ln -sf ../../scripts/pre-commit.sh .git/hooks/pre-commit
EOF
)"

git add -A
git -c user.name=fixture -c user.email=fixture@example.invalid commit -q -m "ledger-api 1.4.0"
git diff HEAD~1 HEAD > CHANGE.diff
