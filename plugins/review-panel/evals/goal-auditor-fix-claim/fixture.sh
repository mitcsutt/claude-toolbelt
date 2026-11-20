#!/usr/bin/env bash
# Seeds slugkit: branch fix-12-14 claims to fix #12 and #14. The #12 fix has
# planted defects; #14 is the sound control. Ground truth is in case.yaml.
set -euo pipefail
mkdir -p src/legacy test

cat > package.json <<'EOF'
{
  "name": "slugkit",
  "version": "1.3.0",
  "private": true,
  "type": "module",
  "engines": { "node": ">=20" },
  "scripts": { "test": "node --test" }
}
EOF

cat > README.md <<'EOF'
# slugkit

Turns titles into URLs for the site.

- `slug(title)`: lower-case, words joined by single dashes.
- `truncate(s, n)`: returns at most `n` characters (UTF-16 code units, as `String.length` counts them), including the ellipsis. `n` must be at least 1.
- `cardUrl(title)`, `feedId(title)`, `postPath(post)`: the site's title-to-URL helpers.

Run the tests with `npm test`.
EOF

cat > ISSUES.md <<'EOF'
# Open issues

## #12 Extra spaces in a title make URLs with runs of dashes

`'  Hello   World '` becomes `/c/--hello---world-` on cards, and posts and the
feed have the same problem. Expected: `hello-world` everywhere a title becomes
a URL, whatever the spacing.

## #14 truncate() returns one character too many

`truncate('abcdef', 4)` returns `'abcd…'` (5 characters). The README says at
most `n` characters, including the ellipsis.
EOF

cat > src/slug.js <<'EOF'
// Turns a title into a URL slug.
export function slug(s) {
  return s.toLowerCase().replace(/ /g, '-');
}
EOF

cat > src/truncate.js <<'EOF'
// Shortens s to at most n characters, including the ellipsis.
export function truncate(s, n) {
  if (n < 1) throw new RangeError('n must be at least 1');
  return s.length > n ? s.slice(0, n) + '…' : s;
}
EOF

cat > src/card.js <<'EOF'
import { slug } from './slug.js';
import { truncate } from './truncate.js';

export const cardUrl = (title) => '/c/' + slug(title);
export const cardLabel = (title) => truncate(title, 40);
EOF

cat > src/feed.js <<'EOF'
import { slug } from './slug.js';

export const feedId = (title) => 'feed-' + slug(title);
EOF

cat > src/legacy/permalink.js <<'EOF'
// Builds the handle part of a post path.
export function makeHandle(title) {
  return title.toLowerCase().replace(/ /g, '-');
}
EOF

cat > src/paths.js <<'EOF'
import { makeHandle } from './legacy/permalink.js';

export const postPath = (post) => '/p/' + makeHandle(post.title);
EOF

cat > test/slug.test.js <<'EOF'
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { slug } from '../src/slug.js';

test('lower-cases and joins words', () => {
  assert.equal(slug('Release Notes'), 'release-notes');
});
EOF

cat > test/truncate.test.js <<'EOF'
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { truncate } from '../src/truncate.js';

test('leaves short strings alone', () => {
  assert.equal(truncate('abc', 3), 'abc');
});

test('rejects n below 1', () => {
  assert.throws(() => truncate('abc', 0), RangeError);
});
EOF

cat > test/paths.test.js <<'EOF'
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { postPath } from '../src/paths.js';

test('post path from title', () => {
  assert.equal(postPath({ title: 'Hello World' }), '/p/hello-world');
});
EOF

git init -q -b main
git add -A
git -c user.name=fixture -c user.email=fixture@example.invalid commit -q -m "slugkit 1.3.0"
git checkout -q -b fix-12-14

cat > src/slug.js <<'EOF'
// Turns a title into a URL slug.
export function slug(s) {
  return s
    .replace(/^  /, '') // #12: drop the leading indent
    .replace(/ $/, '') // #12: drop the trailing space
    .toLowerCase()
    .replace(/ +/g, '-');
}
EOF

cat >> test/slug.test.js <<'EOF'

test('#12: "  Hello   World " becomes hello-world', () => {
  assert.equal(slug('Hello World'), 'hello-world');
});
EOF
git add -A
git -c user.name=dev -c user.email=dev@example.invalid commit -q -m "Fix #12: collapse and trim spaces in slug()"

cat > src/truncate.js <<'EOF'
// Shortens s to at most n characters, including the ellipsis.
export function truncate(s, n) {
  if (n < 1) throw new RangeError('n must be at least 1');
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}
EOF

cat >> test/truncate.test.js <<'EOF'

test('#14: result is at most n characters, ellipsis included', () => {
  assert.equal(truncate('abcdef', 4), 'abc…');
  assert.equal(truncate('abcdef', 1), '…');
});
EOF
git add -A
git -c user.name=dev -c user.email=dev@example.invalid commit -q -m "Fix #14: count the ellipsis in truncate()"

{
  printf '# PR: Fix #12 and #14\n\n'
  printf 'Fixes #12 in slug(), which every title-to-URL path goes through, and #14 in truncate().\n'
  printf 'Tests added for both; `npm test` passes. Branch `fix-12-14` against `main`.\n\n'
  printf '```diff\n'
  git diff main fix-12-14
  printf '```\n'
} > PR.md
