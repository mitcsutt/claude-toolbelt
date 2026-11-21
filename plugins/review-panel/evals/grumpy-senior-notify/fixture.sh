#!/usr/bin/env bash
# Seeds `notify`: a small Node service plus one feature commit ("email the
# customer when an order ships") that carries planted over-engineering.
# Ground truth for each defect is in case.yaml; tests/all.sh re-checks it.
set -euo pipefail
G() { git -c user.name=fixture -c user.email=fixture@example.invalid "$@"; }

mkdir -p lib src config test
cat > package.json <<'EOF'
{
  "name": "notify",
  "version": "1.4.0",
  "private": true,
  "type": "module",
  "main": "src/ship.js",
  "engines": { "node": ">=20" },
  "scripts": { "test": "node --test test/format.test.js test/ship.test.js" }
}
EOF

cat > lib/retry.js <<'EOF'
// Retries an async function with exponential backoff.
export async function retry(fn, { attempts = 3, delayMs = 200 } = {}) {
  let lastError;
  for (let i = 0; i < attempts; i++) {
    try {
      return await fn();
    } catch (err) {
      lastError = err;
      await new Promise((r) => setTimeout(r, delayMs * 2 ** i));
    }
  }
  throw lastError;
}
EOF

cat > src/inventory.js <<'EOF'
import { retry } from '../lib/retry.js';

export async function reserve(client, sku, qty) {
  return retry(() => client.reserve(sku, qty), { attempts: 4 });
}
EOF

cat > config/default.json <<'EOF'
{
  "smtpHost": "localhost",
  "smtpPort": 25,
  "retries": 3
}
EOF

cat > README.md <<'EOF'
# notify

Order-side jobs for the shop backend.

## Configuration (`config/default.json`)

| Key | Default | Meaning |
|---|---|---|
| `smtpHost` | `localhost` | SMTP relay host |
| `smtpPort` | `25` | SMTP relay port |
| `retries` | `3` | Attempts for outbound calls |
EOF

git init -q -b main
G add -A
G commit -q -m "notify: inventory reservation with retry"

# ---- the feature commit under review ----
mkdir -p src/notify/plugins src/util test/helpers

cat > PR.md <<'EOF'
# feat: email the customer when an order ships

Goal: when an order is marked shipped, email the customer once, with the ship time.

Changed files: src/ship.js, src/format.js, src/clock.js, src/metrics.js,
src/util/deepClone.js, src/notify/NotifierStrategy.js, src/notify/EmailNotifier.js,
src/notify/withRetry.js, src/notify/plugins/index.js, src/notify/plugins/email.js,
config/default.json, README.md, test/format.test.js, test/ship.test.js,
test/helpers/fakeClock.js, test/helpers/FakeNotifier.js

Notes from the author:
- NotifierStrategy keeps us flexible for SMS and push later; FakeNotifier makes it testable.
- Plugins in src/notify/plugins/ are discovered automatically.
- Set DRY_RUN=1 to try it without sending mail.
- Retry backoff is configurable with `retryBackoffMs`.
- Clock is injected so the ship-time tests are deterministic.
EOF

cat > src/clock.js <<'EOF'
// Time source. Production uses the system clock; tests pass a fixed one.
export const systemClock = { now: () => new Date() };
EOF

cat > src/format.js <<'EOF'
export function formatShipEmail(order, clock) {
  return {
    to: order.email,
    subject: `Order ${order.id} has shipped`,
    text: `Your order ${order.id} shipped at ${clock.now().toISOString()}.`,
  };
}
EOF

cat > src/metrics.js <<'EOF'
// Prometheus-style counters for the notify service.
const counters = new Map();

export function inc(name, by = 1) {
  counters.set(name, (counters.get(name) ?? 0) + by);
}
EOF

cat > src/util/deepClone.js <<'EOF'
// Deep-copies plain data so formatting can't mutate the caller's order.
export function deepClone(value) {
  return JSON.parse(JSON.stringify(value));
}
EOF

cat > src/notify/NotifierStrategy.js <<'EOF'
// Base class for every notification channel (email now; SMS and push later).
export class NotifierStrategy {
  // eslint-disable-next-line no-unused-vars
  async send(message, options = {}) {
    throw new Error('NotifierStrategy.send is not implemented');
  }
}
EOF

cat > src/notify/withRetry.js <<'EOF'
// Retries an async function with exponential backoff.
export async function withRetry(fn, { attempts = 3, baseDelayMs = 200 } = {}) {
  let lastError;
  for (let attempt = 0; attempt < attempts; attempt++) {
    try {
      return await fn();
    } catch (err) {
      lastError = err;
      await new Promise((r) => setTimeout(r, baseDelayMs * 2 ** attempt));
    }
  }
  throw lastError;
}
EOF

cat > src/notify/EmailNotifier.js <<'EOF'
import { NotifierStrategy } from './NotifierStrategy.js';
import { withRetry } from './withRetry.js';

export class EmailNotifier extends NotifierStrategy {
  constructor({ transport, config }) {
    super();
    this.transport = transport;
    this.config = config;
  }

  async send(message, { dryRun = false } = {}) {
    return withRetry(() => this.transport.sendMail(message), {
      attempts: this.config.retries,
    });
  }
}
EOF

cat > src/notify/plugins/email.js <<'EOF'
import { EmailNotifier } from '../EmailNotifier.js';

export default {
  kind: 'email',
  create: (deps) => new EmailNotifier(deps),
};
EOF

cat > src/notify/plugins/index.js <<'EOF'
// Discovers every notifier plugin in this directory, keyed by kind.
import { readdirSync } from 'node:fs';

export async function loadNotifierPlugins() {
  const dir = new URL('.', import.meta.url);
  const plugins = {};
  for (const file of readdirSync(dir)) {
    if (file === 'index.js' || !file.endsWith('.js')) continue;
    const mod = await import(new URL(file, dir));
    plugins[mod.default.kind] = mod.default;
  }
  return plugins;
}
EOF

cat > src/ship.js <<'EOF'
import { readFileSync } from 'node:fs';
import { systemClock } from './clock.js';
import { formatShipEmail } from './format.js';
import { inc } from './metrics.js';
import { deepClone } from './util/deepClone.js';
import emailPlugin from './notify/plugins/email.js';

const config = JSON.parse(
  readFileSync(new URL('../config/default.json', import.meta.url), 'utf8'),
);

export async function onOrderShipped(order, { transport, clock = systemClock }) {
  const snapshot = deepClone(order);
  const message = formatShipEmail(snapshot, clock);
  const notifier = emailPlugin.create({ transport, config });
  await notifier.send(message, { dryRun: process.env.DRY_RUN === '1' });
  inc('ship_emails_sent');
}
EOF

cat > config/default.json <<'EOF'
{
  "smtpHost": "localhost",
  "smtpPort": 25,
  "retries": 3,
  "retryBackoffMs": 500
}
EOF

cat >> README.md <<'EOF'
| `retryBackoffMs` | `500` | Base delay between email retries, doubled each attempt |

## Ship emails

`onOrderShipped(order, { transport })` emails the customer once. Set `DRY_RUN=1` to skip sending.
EOF

cat > test/helpers/fakeClock.js <<'EOF'
export function fakeClock(iso) {
  return { now: () => new Date(iso) };
}
EOF

cat > test/helpers/FakeNotifier.js <<'EOF'
import { NotifierStrategy } from '../../src/notify/NotifierStrategy.js';

export class FakeNotifier extends NotifierStrategy {
  sent = [];
  async send(message) {
    this.sent.push(message);
  }
}
EOF

cat > test/format.test.js <<'EOF'
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { formatShipEmail } from '../src/format.js';
import { fakeClock } from './helpers/fakeClock.js';

const order = { id: 'A1', email: 'c@example.invalid' };

test('subject names the order', () => {
  const m = formatShipEmail(order, fakeClock('2026-01-02T03:04:05.000Z'));
  assert.equal(m.subject, 'Order A1 has shipped');
});

test('body carries the ship time', () => {
  const m = formatShipEmail(order, fakeClock('2026-01-02T03:04:05.000Z'));
  assert.match(m.text, /2026-01-02T03:04:05\.000Z/);
});
EOF

cat > test/ship.test.js <<'EOF'
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { onOrderShipped } from '../src/ship.js';
import { fakeClock } from './helpers/fakeClock.js';

test('sends one email with the ship time', async () => {
  const sent = [];
  const transport = { sendMail: async (m) => { sent.push(m); } };
  await onOrderShipped(
    { id: 'A1', email: 'c@example.invalid' },
    { transport, clock: fakeClock('2026-01-02T03:04:05.000Z') },
  );
  assert.equal(sent.length, 1);
  assert.match(sent[0].text, /2026-01-02T03:04:05\.000Z/);
});
EOF

G add -A
G commit -q -m "feat: email the customer when an order ships"
