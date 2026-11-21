# grumpy-senior reinvention checks

Verified on 2026-11-19 (Node 24.14.1); re-check live before a finding depends on a volatile fact (versions, built-ins, doc quotes).

When a part may re-do something that already exists. Evidence IDs resolve in `evidence.md` (this directory). Evidence kinds and sizing are in `deletion-checks.md`.

## 1. The line with outsider

The unit being replaced decides, and this is the one place the rule is written down for grumpy-senior.

- **outsider's:** any finding whose fix replaces or removes a *whole component* of the design (a script, hook, skill, service, harness, or the whole artifact) with a platform built-in, a third-party tool, an existing internal system, or nothing. outsider carries the Claude Code built-ins checklist; grumpy-senior carries none.
- **grumpy-senior's:** reinvention *inside* a component: a function, helper, loop, option or file-level part that duplicates the stdlib or runtime the artifact declares (`engines`, `python_requires`), or an in-repo module, named with `path:line` or a doc line. Plus every other deletable part: one-caller structure, knobs nothing reads, scope beyond the goal.

Examples: a PreToolUse hook that greps for `rm -rf` and could be a `permissions.deny` rule replaces a whole hook, so it's outsider's. A hand-written deep clone inside `ship.js` where `structuredClone` exists is grumpy-senior's.

## 2. In-repo duplicates

Grep for the part's distinctive line (a regex, a constant, a function body's first statement) and for its job's verbs. Check `lib/`, `utils/`, `scripts/` and `shared/` first, and look outside the diff: the copy that already exists is usually not in the change. Report both `path:line` ranges. Not a finding when the copies sit either side of a trust boundary (GS6).

## 3. Runtime and stdlib

1. Find the floor first: `package.json` `engines`, `.nvmrc`, `python_requires`, `go.mod` `go`, the CI matrix.
2. Probe the installed runtime, for example `node --version` and `node -p "typeof structuredClone"`.
3. A feature newer than the floor is not a finding. Respect a stated no-dependency rule.

| Feature | Quote | Page | Since | Verified |
|---|---|---|---|---|
| `structuredClone(value[, options])` | "Added in: v17.0.0" | nodejs.org/api/globals.html | Node 17.0.0 | 2026-11-19 |

A JSON round-trip (`JSON.parse(JSON.stringify(x))`) is not a drop-in equivalent in every case: it drops `undefined` and functions and turns `Date` into a string, while `structuredClone` keeps `Date` and throws on functions. Say which one the caller's data needs.

## 4. Platform built-ins are outsider's

When a whole part looks replaceable by a Claude Code feature, don't report it. Mention it once under `## Checked and fine` as "possible built-in replacement — outsider's lens" so the panel knows it was seen. Recall of a built-in is a hypothesis (GS7). Stdlib and runtime APIs (section 3) stay here.

## 5. Rule

A reinvention cut must show the replacement meets every hard constraint in the goal (GS8). Without a row in section 3 or a local probe, it goes to Coverage with the page to fetch, never to Findings.
