#!/usr/bin/env bash
# Seeds a small orders service: a base commit (tagged `base`) and one change
# commit that breaks eight consumers and carries two harmless changes
# (controls). Ground truth for each planted break is a grader in case.yaml and
# a check in tests/all.sh; the rules behind them are in
# references/contract-keeper/ (verified 2026-11-19).
set -euo pipefail

command -v git >/dev/null 2>&1 || { echo "fixture needs git" >&2; exit 1; }
commit() { git -c user.name=fixture -c user.email=fixture@example.invalid commit -qam "$1"; }

git init -q -b main
mkdir -p api src/clients src/util apps/mobile/src deploy scripts migrations/accounts

# ---------------------------------------------------------------- base state

cat > package.json <<'EOF'
{"name":"orders-svc","private":true,"version":"1.4.0"}
EOF

cat > README.md <<'EOF'
# orders-svc

Orders service. The `events` table holds ~400M rows; `users` ~30M.

- Deployed with `deploy/compose.yaml`; the app reads its settings from `.env`.
- Nightly report via cron (`deploy/crontab`).
- Mobile app releases lag the API by weeks, and old builds stay installed.
- Migrations in `migrations/` run on `db`; `migrations/accounts/` run on `accounts-db`.
EOF

cat > api/openapi.yaml <<'EOF'
openapi: 3.0.3
info:
  title: orders-svc
  version: 1.4.0
paths:
  /orders:
    post:
      operationId: createOrder
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: '#/components/schemas/OrderCreate' }
      responses:
        '201':
          description: Created
          content:
            application/json:
              schema: { $ref: '#/components/schemas/Order' }
  /orders/{id}:
    parameters:
      - { name: id, in: path, required: true, schema: { type: string } }
    get:
      operationId: getOrder
      responses:
        '200':
          description: The order
          content:
            application/json:
              schema: { $ref: '#/components/schemas/Order' }
    put:
      operationId: updateOrder
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: '#/components/schemas/Order' }
      responses:
        '200':
          description: Updated
          content:
            application/json:
              schema: { $ref: '#/components/schemas/Order' }
components:
  schemas:
    Priority:
      type: string
      enum: [standard, rush]
    OrderCreate:
      type: object
      required: [items]
      properties:
        items:
          type: array
          items: { type: string }
        priority: { $ref: '#/components/schemas/Priority' }
    Order:
      type: object
      required: [id, status, total, shippedAt]
      properties:
        id: { type: string }
        status:
          type: string
          enum: [pending, paid, shipped]
        priority: { $ref: '#/components/schemas/Priority' }
        shippedAt:
          type: string
          format: date-time
          description: estimated or actual ship date, always set
        total: { type: number }
EOF

cat > src/clients/orders.ts <<'EOF'
// Web client for orders-svc. Used by the admin dashboard.
import { Order } from './types';

const BASE = '/api';

export async function createOrder(items: string[]): Promise<Order> {
  const res = await fetch(`${BASE}/orders`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ items }),
  });
  return res.json();
}

export function statusLabel(o: Order): string {
  switch (o.status) {
    case 'pending':
      return 'Awaiting payment';
    case 'paid':
      return 'Paid';
    case 'shipped':
      return 'Shipped';
    default:
      throw new Error('unknown status ' + o.status);
  }
}

export function etaLabel(o: Order): string {
  return new Date(o.shippedAt).toISOString();
}

export function render(o: Order): string {
  const { id, status, total } = o;
  return `${id} ${statusLabel(o)} ${status === 'paid' ? total.toFixed(2) : ''}`;
}
EOF

cat > src/clients/types.ts <<'EOF'
export interface Order {
  id: string;
  status: string;
  priority?: string;
  shippedAt: string;
  total: number;
}
EOF

cat > apps/mobile/src/api.ts <<'EOF'
// Mobile app API calls. Ships through the app stores.
export const PRIORITIES = ['standard', 'rush'] as const;
export type Priority = (typeof PRIORITIES)[number];

export async function placeOrder(items: string[], priority: Priority) {
  // Rush button sends 'rush'; the default button sends 'standard'.
  const res = await fetch('https://orders.example.invalid/orders', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ items, priority }),
  });
  if (!res.ok) throw new Error('order failed: ' + res.status);
  return res.json();
}
EOF

cat > src/config.js <<'EOF'
module.exports = { dbUrl: process.env.APP_DB_URL };
EOF

cat > .env.example <<'EOF'
APP_DB_URL=postgres://app@db/orders
EOF

cat > deploy/compose.yaml <<'EOF'
services:
  db:
    # orders, events, jobs
    image: postgres:16.4
  accounts-db:
    # users
    image: postgres:10.23
  app:
    build: ..
    env_file: ../.env
    depends_on: [db, accounts-db]
EOF

cat > deploy/crontab <<'EOF'
15 2 * * * cd /srv/orders && . ./.env && scripts/nightly-report.sh
EOF

cat > scripts/nightly-report.sh <<'EOF'
#!/bin/sh
# Nightly order count, mailed by cron.
set -eu
psql "$APP_DB_URL" -c 'select count(*) from orders'
EOF

cat > src/codec.js <<'EOF'
// Notification kind <-> int, stored in jobs.kind (int).
const KINDS = ['email', 'sms', 'push'];
exports.encode = (k) => KINDS.indexOf(k);
exports.decode = (i) => KINDS[i];
EOF

cat > migrations/003.sql <<'EOF'
CREATE TABLE jobs (id bigserial primary key, kind int not null);
EOF

cat > src/util/ids.js <<'EOF'
exports.formatId = (n) => 'ord_' + String(n).padStart(8, '0');
exports.formatLegacyId = (n) => 'ORD-' + n;
EOF

cat > src/server.js <<'EOF'
const http = require('http');
const { formatId } = require('./util/ids');
const config = require('./config');

http.createServer((req, res) => {
  res.setHeader('content-type', 'application/json');
  res.end(JSON.stringify({ id: formatId(1), db: Boolean(config.dbUrl) }));
}).listen(8080);
EOF

chmod +x scripts/nightly-report.sh
git add -A
commit "orders-svc 1.4.0"
git tag base

# ------------------------------------------------------------- change commit

swap_line() { # file old new: replace the one line equal to old (new may span lines)
  local f="$1" tmp="$1.tmp.$$"
  OLD="$2" NEW="$3" awk '$0 == ENVIRON["OLD"] { print ENVIRON["NEW"]; hit++; next } { print } END { if (hit != 1) exit 3 }' "$f" > "$tmp"
  mv "$tmp" "$f"
}

# K1 response enum value added; K3 became nullable; control 1 optional field.
swap_line api/openapi.yaml '          enum: [pending, paid, shipped]' '          enum: [pending, paid, shipped, archived]'
swap_line api/openapi.yaml '          description: estimated or actual ship date, always set' '          nullable: true
          description: null when no estimate exists yet'
swap_line api/openapi.yaml '        total: { type: number }' '        total: { type: number }
        notes: { type: string }'
# K2 required request property with a default.
swap_line api/openapi.yaml '      required: [items]' '      required: [items, currency]'
swap_line api/openapi.yaml '          items: { type: string }' '          items: { type: string }
        currency: { type: string, default: USD }'
# K7 request enum value removed (rush -> express).
swap_line api/openapi.yaml '      enum: [standard, rush]' '      enum: [standard, express]'
swap_line api/openapi.yaml '  version: 1.4.0' '  version: 2.0.0'

# K4 env var renamed; the cron script still reads the old name.
cat > src/config.js <<'EOF'
module.exports = { dbUrl: process.env.DATABASE_URL };
EOF
cat > .env.example <<'EOF'
DATABASE_URL=postgres://app@db/orders
EOF

# K5 volatile default on PG 16.
cat > migrations/004.sql <<'EOF'
ALTER TABLE events ADD COLUMN ingested_at timestamptz NOT NULL DEFAULT clock_timestamp();
EOF

# K6 constant default on a database pinned to PG 10.
cat > migrations/accounts/005.sql <<'EOF'
-- runs on accounts-db
ALTER TABLE users ADD COLUMN tier text NOT NULL DEFAULT 'free';
EOF

# K8 enum member inserted mid-list in an ordinal encoding.
cat > src/codec.js <<'EOF'
// Notification kind <-> int, stored in jobs.kind (int).
const KINDS = ['email', 'webhook', 'sms', 'push'];
exports.encode = (k) => KINDS.indexOf(k);
exports.decode = (i) => KINDS[i];
EOF

# Control 2: an export nothing imports, in a private package.
cat > src/util/ids.js <<'EOF'
exports.formatId = (n) => 'ord_' + String(n).padStart(8, '0');
EOF

git add -A
commit "Orders v2"

# The review inputs, untracked so `git grep` at HEAD sees only the code.
git diff base HEAD > CHANGE.diff
cat > PR.md <<'EOF'
# Orders v2

Adds archived status, currency (has a default, safe), nullable shippedAt,
renames priority rush→express, renames APP_DB_URL to DATABASE_URL (updated
.env.example), adds ingested_at and users.tier (PG 11+ adds defaulted columns
instantly), adds webhook notifications, drops unused formatLegacyId, adds
optional order notes.
EOF
