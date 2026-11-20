#!/usr/bin/env bash
# Seeds "ledgerd", a small billing service whose scripts, unit, compose file,
# cron file and code carry planted failure modes (P1-P10) and one sound control
# script (scripts/setup-dirs.sh). Ground truth: case.yaml, one grader per defect.
set -euo pipefail

mkdir -p scripts deploy src app/bin migrations

echo "1.4.0" > VERSION

cat > README.md <<'EOF'
# ledgerd

Invoices customers daily and charges them through our payment provider (PSP).

Production runs on Debian 12 VMs (systemd, Debian cron, host timezone Europe/London). Staging runs the same image under Docker Compose; `deploy/staging.env` (not committed) holds `DATABASE_URL` and `POSTGRES_PASSWORD`.

## How it runs

- `ledgerd.service` runs the API. `ledgerd` exits with status 1 at startup if it can't reach Postgres, and exits when its connection drops. `Restart=always` means it always comes back after a database blip.
- Staging: if the app goes unhealthy, Compose restarts it, and it waits for the database to be ready before starting.
- Two dispatcher processes run from the same working directory, `/var/lib/ledgerd`, and each calls `saveState()` after every job.
- Charge jobs: the queue redelivers a failed job up to 3 more times (`QUEUE_MAX_RETRIES`).
- Leases are 30 s. A worker checks `still_mine()` before every write and stops when it returns False. The lease store expires a claim after its TTL.
- `billing-run` invoices the previous day. It is not safe to run twice for the same day.
- Cron skips the 01:30 billing run on the spring-forward Sunday (01:00 jumps to 02:00), so `dst-catchup.sh` runs it at 03:00 that day.
- `rates.mjs` runs every minute from cron. Each refresh must finish inside its minute; if the rates API is slow, that minute should just be skipped.
- CI runs `scripts/setup-dirs.sh` before tests. Parallel CI jobs on the same runner may run it at the same time.

## Deploy

`scripts/deploy.sh` installs, migrates, then restarts. Upgrades: bump `VERSION` and re-run `scripts/install.sh`; `ledgerd-current` then points at the new version. Backups: `scripts/backup.sh`, nightly.
EOF

cat > scripts/install.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PREFIX="${PREFIX:-/opt/ledgerd}"
VER="$(cat VERSION)"
install -d "$PREFIX/share/ledgerd-$VER"
cp -R app src scripts migrations "$PREFIX/share/ledgerd-$VER/"
ln -s "$PREFIX/share/ledgerd-$VER" "$PREFIX/share/ledgerd-current"
echo "installed ledgerd $VER"
EOF

cat > scripts/deploy.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

migrate_db() { psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/0007_rates.sql; }
restart_app() { sudo systemctl restart ledgerd; }

deploy() {
  bash scripts/install.sh
  migrate_db
  restart_app
}

if deploy; then
  echo "deploy ok"
else
  echo "deploy FAILED" >&2
  exit 1
fi
EOF

cat > scripts/backup.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
BACKUP_DIR=/var/backups/ledgerd

upload() { rclone copy "$1" offsite:ledgerd-backups/; }
prune() { ls -1t "$BACKUP_DIR"/*.sql.gz | tail -n +4 | xargs -r rm --; }

backup() {
  local out=$(pg_dump "$DATABASE_URL")
  local f="$BACKUP_DIR/ledgerd-$(date +%F).sql.gz"
  printf '%s\n' "$out" | gzip > "$f"
  upload "$f"
  prune
}

backup
EOF

cat > scripts/dst-catchup.sh <<'EOF'
#!/usr/bin/env bash
# Cron skips the 01:30 billing run on the spring-forward Sunday (last Sunday of March, 01:00 -> 02:00 in Europe/London), so run it here on that day only.
set -euo pipefail
if [ "$(date +%m)" = 03 ] && [ "$(date +%u)" = 7 ] && [ "$(date +%d)" -ge 25 ]; then
  exec "$(dirname "$0")/../app/bin/billing-run"
fi
EOF

cat > scripts/setup-dirs.sh <<'EOF'
#!/usr/bin/env bash
# Creates the directories CI needs. Parallel CI jobs on one runner may run this at the same time.
set -euo pipefail
PREFIX="${PREFIX:?set PREFIX}"
install -d "$PREFIX"
exec 9>"$PREFIX/.setup.lock"
flock 9
install -d -m 0755 "$PREFIX/bin" "$PREFIX/share" "$PREFIX/var/log"
EOF

cat > deploy/ledgerd.service <<'EOF'
[Unit]
Description=ledgerd API
After=network-online.target
Wants=network-online.target

[Service]
User=ledgerd
EnvironmentFile=/etc/ledgerd/env
ExecStart=/opt/ledgerd/share/ledgerd-current/app/bin/ledgerd
Restart=always

[Install]
WantedBy=multi-user.target
EOF

cat > deploy/compose.yaml <<'EOF'
services:
  db:
    image: postgres:16
    env_file: staging.env
  app:
    image: ledgerd:1.4.0
    env_file: staging.env
    restart: always
    depends_on: [db]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 10s
      retries: 3
EOF

cat > deploy/ledgerd.cron <<'EOF'
# /etc/cron.d/ledgerd (host timezone Europe/London)
SHELL=/bin/bash
30 1 * * * ledgerd /opt/ledgerd/share/ledgerd-current/app/bin/billing-run
0 3 * * * ledgerd /opt/ledgerd/share/ledgerd-current/scripts/dst-catchup.sh
* * * * * ledgerd /usr/bin/node /opt/ledgerd/share/ledgerd-current/src/rates.mjs
EOF

cat > app/bin/ledgerd <<'EOF'
#!/usr/bin/env bash
exec /usr/bin/python3 "$(dirname "$0")/../../src/main.py"
EOF

cat > app/bin/billing-run <<'EOF'
#!/usr/bin/env bash
echo "billing-run: invoices the previous day (placeholder)"
EOF

cat > src/main.py <<'EOF'
import logging
import os
import sys

import psycopg

from ledgerd_http import serve

log = logging.getLogger("ledgerd")


def main():
    try:
        db = psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=2)
    except psycopg.OperationalError:
        log.error("database unreachable")
        sys.exit(1)
    serve(db)  # returns when the database connection drops
    log.error("database connection lost")
    sys.exit(1)


if __name__ == "__main__":
    main()
EOF

cat > src/state.js <<'EOF'
const fs = require('fs');

function saveState(state) {
  fs.writeFileSync('state.json.tmp', JSON.stringify(state));
  fs.renameSync('state.json.tmp', 'state.json');
}

function loadState() {
  return JSON.parse(fs.readFileSync('state.json', 'utf8'));
}

module.exports = { saveState, loadState };
EOF

cat > src/charge.py <<'EOF'
import time

import requests

PSP_URL = "https://psp.example.com/v1/charges"
QUEUE_MAX_RETRIES = 3  # the queue redelivers a failed job up to 3 more times


def charge(job):
    for attempt in range(4):
        try:
            r = requests.post(PSP_URL, json={"customer": job["customer"], "amount": job["amount"]})
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            time.sleep(1)
    raise RuntimeError("charge failed after 4 attempts")


def handle(job):  # the queue worker calls this for each charge job
    return charge(job)
EOF

cat > src/lease.py <<'EOF'
import time

LEASE_SECONDS = 30


class Lease:
    def __init__(self, store, name):
        self.store = store
        self.name = name
        self.acquired = None

    def acquire(self):
        if self.store.try_claim(self.name, ttl=LEASE_SECONDS):
            self.acquired = time.time()
            return True
        return False

    def still_mine(self):
        return time.time() - self.acquired < LEASE_SECONDS
EOF

cat > src/rates.mjs <<'EOF'
// Run by cron every minute.
import { saveRates } from './store.mjs';

const res = await fetch('https://rates.example.com/latest.json');
if (!res.ok) throw new Error(`rates: HTTP ${res.status}`);
await saveRates(await res.json());
EOF

cat > src/store.mjs <<'EOF'
export async function saveRates(rates) { /* upserts into the rates table */ }
EOF

cat > migrations/0007_rates.sql <<'EOF'
CREATE TABLE IF NOT EXISTS rates (currency text PRIMARY KEY, rate numeric NOT NULL, fetched_at timestamptz NOT NULL);
EOF

chmod +x app/bin/* scripts/*.sh
