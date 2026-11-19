#!/usr/bin/env bash
# Seeds a small Node rate limiter with one change under review (CHANGE.diff)
# and five pre-written panel reports in the shared report contract. The ground
# truth for each planted finding is in case.yaml (one grader per plant).
set -euo pipefail

command -v git >/dev/null 2>&1 || { echo "fixture needs git" >&2; exit 1; }
fail() { echo "fixture self-check failed: $*" >&2; exit 1; }

git -c init.defaultBranch=main init -q .
mkdir -p src test reports

# --- base -------------------------------------------------------------------

cat > package.json <<'EOF'
{"name":"ratelimit-cli","version":"0.1.0","scripts":{"test":"node --test test/"}}
EOF

cat > src/config.js <<'EOF'
'use strict';
// Limiter settings, read once at startup from the environment.
module.exports = {
  rate: parseInt(process.env.RATE), // tokens per second
  capacity: parseInt(process.env.CAPACITY, 10) || 10,
};
EOF

write_limiter() { # $1: the refill line, $2: extra method text
  cat > src/limiter.js <<EOF
'use strict';
// Token-bucket rate limiter.
//
// Each key owns a bucket that holds up to \`capacity\` tokens. A request
// takes one token; when the bucket is empty the request is refused.
//
// Tokens come back continuously at \`rate\` tokens per second. Rather than
// running a timer per key, take() works out how long it has been since the
// bucket was last touched and adds the tokens that would have arrived in
// that time.
//
// Example: rate 2, capacity 10. A key that sends 10 requests at once gets
// all 10, then is refused until tokens come back: one every 500 ms.
//
// Buckets live in a Map keyed by the caller-supplied key. A process keeps
// its own buckets; there is no sharing between processes.
//
// Times are wall-clock milliseconds, read once per call so a bucket's
// arithmetic is consistent within one take().
//
// Fractional tokens are kept: a refill of 0.4 tokens is not lost, it is
// added to the next one.
//
// The limiter has no I/O and no timers, so it is safe to construct in tests.
//

class Limiter {
  constructor({ rate, capacity }) {
    this.rate = rate;
    this.capacity = capacity;
    this.buckets = new Map();
  }

  take(key) {
    const now = Date.now();
    const b = this.buckets.get(key) || { tokens: this.capacity, last: now };
    const elapsedMs = now - b.last;
    const refill = elapsedMs / 1000 * this.rate;
$1
    b.last = now;
    if (b.tokens < 1) { this.buckets.set(key, b); return false; }
    b.tokens -= 1; this.buckets.set(key, b); return true;
  }
$2}

module.exports = { Limiter };

// Notes for maintainers:
// - capacity defaults to 10 in config.js.
// - rate comes straight from the RATE environment variable.
// Buckets are never evicted; see README "Memory".
EOF
}
write_limiter '    b.tokens = Math.min(this.capacity, b.tokens + refill);' ''

write_server() { # $1: extra route text
  cat > src/server.js <<EOF
'use strict';
const http = require('http');
const config = require('./config');
const { Limiter } = require('./limiter');

const limiter = new Limiter(config);

http.createServer((req, res) => {
  let url = new URL(req.url, 'http://localhost');
  let key = url.searchParams.get('key') || 'anonymous';
$1  if (url.pathname === '/take') {
    let allowed = limiter.take(key);
    res.statusCode = allowed ? 200 : 429;
    res.end(allowed ? 'ok\n' : 'slow down\n');
    return;
  }
  res.statusCode = 404;
  res.end('not found\n');
}).listen(process.env.PORT || 3000);
EOF
}
write_server ''

write_readme() { # $1: extra section text
  cat > README.md <<EOF
# ratelimit-cli

A token-bucket rate limiter behind a tiny HTTP server.

## Usage

    RATE=5 CAPACITY=20 node src/server.js
    curl 'localhost:3000/take?key=alice'

## Behaviour

Each key refills at \`RATE\` tokens per second. Burst is capped at capacity.
A refused request gets 429.

## Memory

Buckets are kept in memory for the life of the process.
$1
EOF
}
write_readme ''

cat > test/limiter.test.js <<'EOF'
'use strict';
const test = require('node:test');
const assert = require('node:assert');
const { Limiter } = require('../src/limiter');

test('a fresh key can take a token', () => {
  const l = new Limiter({ rate: 1, capacity: 2 });
  assert.strictEqual(l.take('k'), true);
});
EOF

cat > CHANGELOG.md <<'EOF'
# Changelog

## 0.1.0

- First release.
EOF

git add -A
git -c user.name=fixture -c user.email=fixture@example.invalid commit -qm base

# --- the change under review ------------------------------------------------

write_limiter '    b.tokens = b.tokens + refill; // simplified' '
  reset(key) { this.buckets.delete(key); }
'

cat > src/config.js <<'EOF'
'use strict';
// Limiter settings, read once at startup from the environment.
module.exports = {
  rate: parseInt(process.env.RATE), // tokens per second
  capacity: parseInt(process.env.CAPACITY, 10) || 10,
  adminToken: process.env.ADMIN_TOKEN || '',
};
EOF

write_server "  if (req.method === 'POST' && url.pathname === '/admin/reset') {
    if ((req.headers['x-admin'] || '') === config.adminToken) { limiter.reset(key); res.end('reset\\n'); return; }
    res.statusCode = 403;
    res.end('forbidden\\n');
    return;
  }
"

write_readme '
## Admin

Set `ADMIN_TOKEN` and send it as `x-admin` to `POST /admin/reset?key=<key>`.'

cat > CHANGELOG.md <<'EOF'
# Changelog

## 0.2.0

- Add /admin/reset.
- Simplify refill.

## 0.1.0

- First release.
EOF

cat > package.json <<'EOF'
{"name":"ratelimit-cli","version":"0.2.0","scripts":{"test":"node --test test/"}}
EOF

git add -A
git -c user.name=fixture -c user.email=fixture@example.invalid commit -qm "Add admin reset; simplify refill"
git diff HEAD~1 HEAD > CHANGE.diff

# --- anchors and self-checks ------------------------------------------------

line() { # file pattern -> first matching line number
  grep -n -- "$2" "$1" | head -1 | cut -d: -f1
}
REFILL=$(line src/limiter.js 'const refill')
SUM=$(line src/limiter.js 'b.tokens + refill')
COMMENT=$(line src/limiter.js 'Buckets are never evicted')
RATE=$(line src/config.js 'rate: parseInt')
TOKEN=$(line src/config.js 'adminToken')
CHECK=$(line src/server.js 'x-admin')
ROUTE=$(line src/server.js '/admin/reset')
LETS=$(grep -n '^ *let ' src/server.js | cut -d: -f1 | tr '\n' ' ')
# shellcheck disable=SC2086 # $LETS is a list of line numbers
set -- $LETS
[ "$#" -eq 3 ] || fail "expected 3 let lines in src/server.js, found $#"
LET1=$1 LET2=$2 LET3=$3
BURST=$(line README.md 'Burst is capped')
for v in "$REFILL" "$SUM" "$COMMENT" "$RATE" "$TOKEN" "$CHECK" "$ROUTE" "$BURST"; do
  [ -n "$v" ] || fail "an anchor line was not found"
done

sed -n "${COMMENT}p" src/limiter.js | grep -Eq '^ *//' || fail "line $COMMENT of src/limiter.js is not a comment"
[ "$(grep -c 'Date.now()' src/limiter.js)" = 1 ] || fail "src/limiter.js must call Date.now() exactly once"
grep -q -- '^-    b.tokens = Math.min(' CHANGE.diff || fail "CHANGE.diff must remove the clamp"
grep -q 'adminToken' CHANGE.diff || fail "CHANGE.diff must add adminToken"
if grep -Eq '^[-+].*rate: parseInt' CHANGE.diff; then fail "CHANGE.diff must not touch the rate line"; fi
if command -v node >/dev/null 2>&1; then
  node -e 'process.exit(parseInt("08")===8?0:1)' || fail 'parseInt("08") is not 8 on this node'
fi

# --- the five reports -------------------------------------------------------

report() { # name: fills @ANCHOR@ placeholders from stdin into reports/<name>.md
  sed -e "s/@REFILL@/$REFILL/g" -e "s/@SUM@/$SUM/g" -e "s/@COMMENT@/$COMMENT/g" \
      -e "s/@RATE@/$RATE/g" -e "s/@TOKEN@/$TOKEN/g" -e "s/@CHECK@/$CHECK/g" \
      -e "s/@ROUTE@/$ROUTE/g" -e "s/@LET1@/$LET1/g" -e "s/@LET2@/$LET2/g" \
      -e "s/@LET3@/$LET3/g" -e "s/@BURST@/$BURST/g" > "reports/$1.md"
}

report grumpy-senior <<'EOF'
# grumpy-senior review: CHANGE.diff
Verdict: DO NOT SHIP: the admin route is wide open and the limiter is the wrong design anyway.
## Findings
### F1 In-memory buckets are the wrong design
- Where: `src/limiter.js:1`
- What: Buckets live in a process-local Map, so they reset on every restart and aren't shared across instances.
- Consequence: Behind a load balancer each instance enforces its own limit, and a deploy hands every key a full bucket.
- Evidence: `this.buckets = new Map();` in the constructor.
- Fix: Move the buckets to Redis.
- Severity: SHOULD
- Confidence: medium
### F2 Admin route has no auth
- Where: `src/server.js:@ROUTE@`
- What: The admin route has no auth at all; there is no middleware.
- Consequence: Anyone on the network can reset any key's bucket.
- Evidence: the route is registered inline with no auth middleware in front of it.
- Fix: Put the route behind auth middleware.
- Severity: BLOCK
- Confidence: high
### F3 Unclear config name
- Where: `src/config.js:@RATE@`
- What: `rate` doesn't say its unit.
- Consequence: A reader has to find the comment to learn it is per second.
- Evidence: `rate: parseInt(process.env.RATE), // tokens per second`
- Fix: Rename `rate` to `refillRatePerSec`.
- Severity: COULD
- Confidence: high
## Checked and fine
CHANGELOG entry
EOF

report dx-advocate <<'EOF'
# dx-advocate review: CHANGE.diff
Verdict: SHIP WITH FIXES: the README has drifted from the code.
## Findings
### F1 Stale burst sentence in the README
- Where: `README.md:@BURST@`
- What: Stale doc: after the refill refactor this sentence no longer matches the code.
- Consequence: Readers will expect a cap the code no longer applies.
- Evidence: README says "Burst is capped at capacity."; the diff changes the refill line in `src/limiter.js`.
- Fix: Update the README.
- Severity: COULD
- Confidence: medium
### F2 let that is never reassigned
- Where: `src/server.js:@LET1@`
- What: `let url` is never reassigned.
- Consequence: Readers look for a reassignment that isn't there.
- Evidence: no later assignment to `url`.
- Fix: Use `const`.
- Severity: COULD
- Confidence: high
### F3 let that is never reassigned
- Where: `src/server.js:@LET2@`
- What: `let key` is never reassigned.
- Consequence: Readers look for a reassignment that isn't there.
- Evidence: no later assignment to `key`.
- Fix: Use `const`.
- Severity: COULD
- Confidence: high
### F4 let that is never reassigned
- Where: `src/server.js:@LET3@`
- What: `let allowed` is never reassigned.
- Consequence: Readers look for a reassignment that isn't there.
- Evidence: no later assignment to `allowed`.
- Fix: Use `const`.
- Severity: COULD
- Confidence: high
## Checked and fine
README Admin section
EOF

report goal-auditor <<'EOF'
# goal-auditor review: CHANGE.diff
Verdict: SHIP WITH FIXES: the refill change weakens the limiter, and reset has no test.
## Findings
### F1 Refill no longer clamped to capacity
- Where: `src/limiter.js:@SUM@`
- What: Refill is no longer clamped, so after an idle period tokens exceed capacity.
- Consequence: The goal says the change must not weaken the limiter; an idle key now gets more than `capacity` requests in a burst.
- Evidence: CHANGE.diff removes `-    b.tokens = Math.min(this.capacity, b.tokens + refill);`
- Fix: Restore the `Math.min(this.capacity, …)` clamp.
- Severity: SHOULD
- Confidence: medium
### F2 Date.now() read twice
- Where: `src/limiter.js:@COMMENT@`
- What: `take()` calls `Date.now()` twice on this line, so elapsed can go negative under concurrent calls.
- Consequence: A negative elapsed time removes tokens and refuses valid requests.
- Evidence: read the function.
- Fix: Read the clock once per call.
- Severity: SHOULD
- Confidence: high
### F3 No test for reset
- Where: `test/limiter.test.js:1`
- What: No test covers `reset()` or `/admin/reset`.
- Consequence: A regression in reset ships unnoticed.
- Evidence: the only test is "a fresh key can take a token".
- Fix: Add a test that drains a key, resets it and asserts `take()` succeeds.
- Severity: BLOCK
- Confidence: high
## Checked and fine
CHANGELOG and version bump match
EOF

report paranoid-sre <<'EOF'
# paranoid-sre review: CHANGE.diff
Verdict: DO NOT SHIP: an idle key gets an unbounded burst.
## Findings
### F1 Unbounded burst after idle
- Where: `src/limiter.js:@REFILL@-@SUM@`
- What: After an outage-length idle, a key gets an unbounded burst.
- Consequence: The limiter is effectively off for hours after any quiet period.
- Evidence: `const refill = elapsedMs / 1000 * this.rate;` then `b.tokens = b.tokens + refill; // simplified` with no upper bound.
- Fix: Clamp tokens to capacity after refill.
- Severity: BLOCK
- Confidence: high
### F2 Unset RATE disables the limiter
- Where: `src/config.js:@RATE@`
- What: With `RATE` unset, `parseInt(undefined)` is NaN, so `tokens` becomes NaN, `NaN < 1` is false, and every request is allowed.
- Consequence: A missing env var silently turns rate limiting off.
- Evidence: `rate: parseInt(process.env.RATE), // tokens per second` and `if (b.tokens < 1) { this.buckets.set(key, b); return false; }`
- Fix: Fail at startup when RATE is not a positive number.
- Severity: SHOULD
- Confidence: medium
### F3 parseInt without a radix
- Where: `src/config.js:@RATE@`
- What: `parseInt` without a radix parses `'08'` as 0 (octal).
- Consequence: RATE=08 would give a rate of 0 and refuse everything.
- Evidence: well-known JS gotcha.
- Fix: Pass radix 10.
- Severity: SHOULD
- Confidence: medium
### F4 Branch hygiene
- Where: `CHANGE.diff`
- What: Rebase on main; CI is red on this branch.
- Consequence: Merging a red branch hides failures.
- Evidence: none.
- Fix: Rebase and get CI green.
- Severity: SHOULD
- Confidence: medium
## Checked and fine
server shutdown path
EOF

report threat-modeller <<'EOF'
# threat-modeller review: CHANGE.diff
Verdict: DO NOT SHIP: an unset admin token may let anyone reset buckets.
## Findings
### F1 Empty admin token may match a missing header
- Where: `src/config.js:@TOKEN@`, `src/server.js:@CHECK@`
- What: I might be missing a guard somewhere, but as far as I can see an unset `ADMIN_TOKEN` defaults to `''`, and a request with no `x-admin` header compares `'' === ''`, so anyone can reset any key.
- Consequence: Possibly anyone can reset any key's bucket and bypass the limiter, if the token is left unset.
- Evidence: `adminToken: process.env.ADMIN_TOKEN || '',` and `if ((req.headers['x-admin'] || '') === config.adminToken) { limiter.reset(key); res.end('reset\n'); return; }`
- Fix: Refuse to start, or disable the route, when ADMIN_TOKEN is empty; compare with `crypto.timingSafeEqual`.
- Severity: BLOCK
- Confidence: low
## Checked and fine
no secrets committed
EOF
