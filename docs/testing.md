# Testing

Three enforcement layers, each with a distinct job and no overlap between
them, plus a fourth manual layer for prompt-shaped plugins.

## The three layers

1. **`scripts/validate.sh`** — repo-wide structural invariants, implemented
   once and looped over every plugin. Wraps `claude plugin validate --strict`
   (skipped with a note if the `claude` CLI isn't on `PATH`) and adds what
   that command doesn't cover: plugin directory name matches `plugin.json`
   name matches the marketplace entry name (R1); marketplace entries carry
   only `{name, source, category}` (R2); the README skeleton and heading
   order (R4); presence of `tests/all.sh` (R5); no plugin-local copy of
   `assert.sh` (R6); file modes against the R9 allowlist; and that any
   `SKILL.md`'s frontmatter `name` matches the directory it lives in.

2. **`plugins/<name>/tests/all.sh`** — behavioural assertions specific to
   that plugin. This is where "one behavioural assertion per product
   promise the README states" lives: if the README's `## What it ships`
   table claims a hook does X, `tests/all.sh` should have an assertion that
   X actually happens. Sources the shared helper at `tests/lib/assert.sh`
   (never a local copy — R6). `scripts/validate.sh` only checks that the
   file exists; the substance of what it asserts is a review concern (R5),
   not something a script can judge.

3. **`scripts/test-all.sh`** — runs `scripts/validate.sh`, then every
   plugin's `tests/all.sh` in turn, then `node --test` over every tracked
   `*.test.mjs` file. This is the single command that must pass before any
   change lands, and the one CI runs.

Deliberately *not* present: a per-plugin manifest-checking script copied six
times. Those checks are structural, so they live once in layer 1.

## Running everything

```bash
bash scripts/test-all.sh
```

Runs validate, lint (shellcheck `--severity=warning`, skipped with a note if
shellcheck isn't installed), every plugin's `tests/all.sh`, and
`node --test` over tracked `*.test.mjs` files. This is what
`.github/workflows/ci.yml` runs on every push and pull request.

To run a single layer in isolation:

```bash
bash scripts/validate.sh   # structural invariants only
bash scripts/lint.sh       # shellcheck only
bash plugins/cutthroat/tests/all.sh   # one plugin's behavioural tests
```

## Evals (manual, fourth layer)

Prompt-shaped plugins — `cutthroat`, `postmortem`, `find-docs` — are best
verified by actually running the prompt and grading the output, which is
what `claude plugin eval` does. An eval suite lives at
`plugins/<name>/evals/` as `case.yaml` files (or `prompt.md` + graders), and
is committed to the repo like any other plugin file.

```bash
claude plugin eval plugins/cutthroat --threshold 0.8
```

**Evals do not run in CI.** They cost money per run — each case invokes a
real agent turn, typically several times, plus grading — and nothing here
should draw on the same budget as ordinary push/PR checks. `.github/workflows/ci.yml`
runs `scripts/test-all.sh` only. Evals are a manual, human-triggered step:
run them before bumping the version of a prompt-shaped plugin, so a change to
the output style, an interview flow, or a routing prompt gets graded before
it ships. Nothing mechanically forces this today — it's a discipline this
document states, not a gate a script enforces.

### What's covered

- **`cutthroat`** (3 cases) — `substance-survives-compression` is the
  anti-caveman regression guard: a dense technical question graded on
  whether the cause and fix survive, not on brevity. `protected-set-never-cut`
  checks that a stack trace's `file:line` and exception name survive
  verbatim. `structure-cut-vs-baseline` tags its graders `arm: both` so
  running it with `--ablation with-without` reports the with/without score
  delta — the discriminating signal the style exists to produce. Run without
  `--ablation`, it scores only the arm under test.
- **`postmortem`** (2 cases) — `writes-file-despite-no-save-request` checks
  hard-contract #1 (the file is the deliverable even when told to skip
  saving) via a `regex` grader on the created-files list, no LLM judging
  needed for that signal. `all-eight-sections-present` checks hard-contract
  #2 (exactly 8 named sections, in order) against the file content visible
  in the tool-call trace.
- **`find-docs`** (2 cases) — `routes-popular-library-to-context7` and
  `routes-vendor-saas-to-exa-not-context7` check the routing table sends
  each question shape to the right backend and doesn't default to
  "Context7 first, Exa fallback." These cases call real MCP tools, which
  `claude plugin eval` gates by default — grant them explicitly:
  `--allow-tools "mcp__context7__*" "mcp__exa__*"`.

**Status:** authored and schema-validated (`claude plugin eval <path> --case
<glob-that-matches-nothing>` loads and validates every `case.yaml` in the
suite at zero cost — a malformed file errors before the "no cases match"
message). One case (`cutthroat`'s `substance-survives-compression`, single
run, no ablation) was executed for real to confirm the harness runs a
committed case end to end; the live agent turn hit an auth error inside the
eval's isolated sandbox environment rather than producing a real answer, so
no baseline score is recorded here yet. Run the suites in a normally
authenticated terminal to get real scores before relying on them.

## Related

- [`docs/authoring-plugins.md`](authoring-plugins.md) — the README skeleton
  that layer 2's assertions are checked against, and where `evals/` sits in
  a plugin's layout.
- [`docs/repo-structure.md`](repo-structure.md) — where `scripts/` and
  `tests/lib/` sit relative to `plugins/`.
