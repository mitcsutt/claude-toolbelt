# Handoff — agent-loop v3, plan E (validation), continuing on a fresh machine

Written 2026-09-14 by the Opus 5 session that ran **plan E inline** (Mitch's
instruction: "use inline development" — no implementer subagents). The previous
session's handoff was `/private/tmp/agent-loop-v3-merge-handoff.md`; this
supersedes its "Your work" section.

**The session ended because the account hit its weekly usage limit**, mid-way
through the second validation run. Nothing is broken; state is on disk.

HEAD on `agent-loop-v3` is this session's commit. `bash scripts/test-all.sh` is
**PASS / exit 0** with every change in it.

---

## 1. Read these first (authority order)

| What | Path |
|---|---|
| Binding authority above every plan | `docs/superpowers/specs/2026-09-13-agent-loop-v3-phase-runner-design.md` |
| Definition of done — §6 is the task list | `docs/superpowers/plans/2026-09-13-agent-loop-v3-HANDOFF.md` |
| Names/signatures; "Cross-plan notes" win | `docs/superpowers/plans/2026-09-13-agent-loop-v3-00-interfaces.md` |
| **This session's ledger — read it, it has the evidence in full** | `.superpowers/sdd/2026-09-13-agent-loop-v3-E-validation/progress.md` |
| Plan D/C/B/A ledgers | `.superpowers/sdd/2026-09-13-agent-loop-v3-{D-surface,C-judgement,B-verification,A-runner-core}/progress.md` |
| The previous handoff (historical) | `/private/tmp/agent-loop-v3-merge-handoff.md` |

Keep all six ledgers.

---

## 2. What this session did

### Code/doc changes (all committed, all covered by a green `test-all`)

1. **Spec §157 `fidelity_source`** — the item plan D was assigned and did not do.
   The example now reads `src`/`dst`; §5.4 gained a sentence saying `from`/`to`
   is accepted on the wire but `src`/`dst` is canonical. **Spec only** —
   `contract.py` was right and is untouched.
2. **`dashboard.html` MOCK drift** — closed **and asserted**.
   `tests/serve.test.py::TestMockMatchesFixture` parses `MOCK` straight out of
   `dashboard.html` and asserts it equals `tests/fixtures/snapshot-sample.json`,
   plus a second test pinning both to `plugin.json`'s version. Run **red first**
   (`'2.1.0' != '3.0.0'` and a 15,677-char document diff). The pair is now one
   coherent v3 example: `plugin_version` 3.0.0, `schema` 3, and a 2→3
   `migration` whose `actions` string is copied from `migrate.ensure_layout`.
   The comment now says "equal as documents, not as bytes", which is what the
   test actually checks.
   *Why asserted rather than dropped:* the fixture is referenced by **nothing
   else in the repo** — the claim was its only reason to exist.
   Serve tests went **126 → 128**.

### Validation runs (real money, Mitch pre-approved)

Two throwaway repos, both under the session scratchpad (**gone when /tmp is
cleared — recreate, do not hunt for them**):
`scratchpad/tally-demo` (main run) and `scratchpad/tally-v2` (migration run).

---

## 3. HANDOFF §6 scoreboard

| §6 item | Status |
|---|---|
| Render gate: screenshot lands in `artifacts/<T>/` | ✅ **PROVEN** |
| …and renders through `/api/artifact` **in a browser** | ✅ **PROVEN** |
| Judge writes `LOOP_DECISIONS.md` | ✅ **PROVEN** (and the output is excellent) |
| Dashboard Stop-now lands at a phase boundary in minutes | ✅ **PROVEN — ~1 s** |
| Dashboard thumbnails | ✅ verified by code path + the proven route (see caveat) |
| Loop dir migrates 2→3 **in place** | ✅ **PROVEN** |
| …and **resumes its `[~]` task** | ❌ **NOT PROVEN** — see §5 |
| A timed-out Worker is `--resume`d from its session id | ❌ **NOT PROVEN** — see §5 |
| Postmortem comparison vs `EXTRACT/08-analysis/cost-effort.md` | ⚠️ partial — numbers in §6 |

### The render gate — the main event, proven end to end

First time plan B's gate has ever run against a real browser (every prior test
used a scripted stub). Recipe: `python3 -m http.server` started **by the
harness** (`render_app_start` event) + Playwright Chromium via
`scripts/shot.sh`. Four links, each checked rather than assumed:

1. `artifacts/T2/board-with-counts.png` on disk, PNG 900×640, 12,951 bytes.
2. The durable event carries `"path":"artifacts/T2/board-with-counts.png"` —
   **loop-dir-relative**, exactly what `e603725` fixed.
3. `GET /api/artifact?tick=2&name=board-with-counts` → `HTTP 200`,
   `Content-Type: image/png`, `Content-Length: 12951`, body SHA-256
   `e55bc35b…78a77e`, **byte-identical to disk**. Also opened in Chrome (tab
   title `artifact (900×640)`).
4. **The image is of the product**: the Tally board with three styled counter
   rows, empty-state gone. A gate that screenshots a blank page would pass 1–3
   and prove nothing.

Fetch at step 3 was made **after tick 3 had started**, which additionally proves
`store.artifact_ix` survives tick rollover as its comment claims.

### Two machine facts a render recipe depends on (neither is in any doc)

- The default `node` here is **18.16**; playwright-core refuses to start below
  node 20. `scripts/shot.sh` resolves an nvm node ≥20 and execs the driver.
- playwright 1.63 wants `chromium_headless_shell-**1243**`; the cache held 1234.
  Had to download it. **A render recipe has a browser-binary dependency the loop
  cannot satisfy for itself** — check this before the loop machine runs one.

### Stop now

Clicked the real button in Chrome. `runtime/STOP` written 13:56:12Z; harness
logged `paused between phases; checkpoint written` at 13:56:13Z and exited 0.
**~1 s, against `tick_timeout=1800`.** `CHECKPOINT.json` correct
(`stopped_after_tick: 5, next_task: T4`). Restart then logged
`boot-reconcile: T4 was recorded in phase 'SCOUT:done'; resuming at scout` and
tick numbering **continued at 6**, never restarting at 1.

### Migration 2→3, in place — proven

`scratchpad/tally-v2` was built to the **v2 layout** rather than described:
`run.log` not `harness.log`, `.gitignore` without `artifacts/`, no
`LOOP_DECISIONS.md`, no `artifacts/`, `runtime/schema` = `2`, a v2 contract whose
`forbidden` is bare strings and which lacks all five v3 keys, **no
`runtime/task-T1.json`**, `T1` at `[~]`, plus a half-finished uncommitted edit.

Result — `schema 2 -> 3 (contracts-normalised)`, and on disk afterwards:
`schema` stamp `3`; `artifacts/` created; `LOOP_DECISIONS.md` created;
`harness.log` created; `.gitignore` gained `artifacts/`; **`run.log` left in
place**; `forbidden` normalised to `[{path: test, source: scout}, {path: public,
source: scout}]`; event
`{"type":"migration","from":2,"to":3,"actions":"contracts-normalised:1","plugin_version":"3.0.0"}`.

Boot reconciliation took the right branch too:
`T1 is [~] but runtime/task-T1.json does not exist … asking the Judge`.

---

## 4. FINDINGS — read this section before deciding to merge

### F1 (blocking, in my view): `PATH_TOKEN_RE` killed two tasks

Not the "~$0.10–0.20 and 43 s per occurrence" tax the previous handoff carries.
Measured over the main run: **6 of 6 tasks refused on contract try 1**, and
**T4 and T5 were deferred to `[!]` and never reached a Worker at all.**

Offending tokens observed, with where each sat:

| task | tokens | where |
|---|---|---|
| T1 | `{`, `m.formatDelta` | inside `node -e "…"` |
| T2 | `app.js`, `{` | prose success criteria |
| T3 | `/**`, `*/`, `[xs` | prose criteria describing a JSDoc block |
| T4 | `{`, `board.dataset.ready`, `counters.length`, `empty.hidden` | prose criteria + grep patterns |
| T5 | `.append`, `.appendChild`, `{print`, `board.dataset.ready`, **`public/app.js`**, **`src/format.js`** | grep patterns |
| T6 | `test/format.test.mjs` | a `node --test` verification command |

**Plan B's carried remedy — "skip tokens inside an inline `-c "…"` / `-e '…'`
one-liner" — would have prevented T1's rejection and none of the others.**
Do not implement it as written.

**The Judge independently found the worse mode**, quoted from
`LOOP_DECISIONS.md`:

> "The suggested remedy in the error ('write counters.length (read)') is already
> satisfied in the prose … **and it is unsatisfiable in the two verification
> commands, because annotating a grep pattern as 'board.dataset.ready (read)'
> makes the pattern match nothing.**"

So for a path-ish token inside a `verification` command there is **no legal
contract**: annotate it and the check is meaningless; leave it and the contract
is refused. T5 died on `public/app.js` and `src/format.js` — *real repo paths*,
referenced read-only inside greps.

The real defect: **the regex runs over `success_criteria`, and success criteria
are prose.** On T3 it rejected the Scout's most careful criterion — the one
correctly warning that 28 of 36 catalog functions are stubs.

### F2 (blocking, in my view): `LOOP_DECISIONS.md` reports changes never made

`run.py:583` → `judge.apply(..., persist=in_force is not None)`. On the
invalid-contract path `in_force is None`, so `judge.py:639`'s
`if contract_dirty and persist:` correctly writes nothing (the contract is a
synthetic stand-in, `run.py:552`). But `applied` is built regardless
(`judge.py:616-638`) and `append_decision` is called unconditionally
(`judge.py:704`).

Verified on disk, twice:
- T4's entry: `Applied: allow_list += counters.length; allow_list +=
  board.dataset.ready; instruction appended to scout_notes`. `sprint-T4.json`'s
  `allow_list` is unchanged.
- T5's entry: `Applied: instruction appended to scout_notes`.
  `'JUDGE' in sprint-T5.json scout_notes` → **False**.
- Both `Reverse:` lines instruct a human to undo edits **that do not exist**.

**Suggested fix:** filter `applied` by `persist` (or have `apply` return what it
actually persisted) so the ledger cannot claim un-persisted changes.

### F3 (blocking, in my view): try-exhausted deferral writes no `LOOP_CLEANUP` entry

`run.py:805-814` — when the Judge answers `retry`/`escalate`/`resume` to an
invalid contract, the harness overrides to a deferral and calls
`finish_deferral`. `finish_deferral`'s docstring (`run.py:431`) assumes
*"`judge.apply` has already … written the LOOP_CLEANUP entry"* — true for
`judge.apply`'s `defer`/`halt` branch, but here the Judge said **`widen`**, so
`write_cleanup_entry` never ran. It then commits `[plan_path, cleanup_path]`
with an unchanged cleanup file.

Verified: `LOOP_CLEANUP.md` stayed the scaffold placeholder; its only commit is
the scaffold; `105595d loop: block T4 — open` touches **`LOOP_PLAN.md` alone**.

**This is not the previously-carried item**, which states the inverse
("cap-forced deferral writes `LOOP_CLEANUP` but no `LOOP_DECISIONS` entry").
Different call site. **Both are real.**

**Control that proves F3 is specific to this path:** in the `tally-v2` run the
genuine `defer` path *did* write full, high-quality `LOOP_CLEANUP.md` entries
for T1 and T2. The machinery is fine; only the override path skips it.

### F1+F2+F3 together — why I would not merge yet

What a human finds the morning after the main run:

| surface | says |
|---|---|
| `LOOP_PLAN.md` | T4 `[!]`, T5 `[!]` |
| `LOOP_CLEANUP.md` | **empty** |
| `LOOP_DECISIONS.md` | two entries headed `widen` and `retry`; **neither says the task was blocked**, and both misreport what was applied |

Two tasks silently lost, an empty follow-up list, and an audit trail that
describes remedies that were never applied. That is the exact failure class v3
exists to eliminate (G3 auditable decisions, G5 fewer human touches).

**The architecture is not the problem** — the render gate, Stop-now,
boot-reconcile, migration, rate-limit handling and the Judge's *reasoning* are
all good, and $/task and wall/task are far better than v2. These are three
bounded bugs. **Mitch's call, not mine.**

### Non-findings (checked, do not re-open)

- **Dashboard "stuck on Waiting for the first snapshot" is NOT a bug.**
  `render()` early-returns when `document.hidden`; a browser-automation tab is
  backgrounded. `paint(latest)` — what its own `visibilitychange` handler calls
  — renders instantly.
- **`tick.artifacts: null` is NOT a bug.** `store.artifacts` is documented as
  "the tick in flight only" and resets at `tick_start`; cross-tick addressing is
  `artifact_ix`, which works.
- **boot-reconcile resume not appearing in `LOOP_DECISIONS.md` is defensible.**
  `run.boot_reconcile`'s has-state-file branch emits the event without
  `append_decision`; resuming at a recorded phase is mechanical, and
  `classification=UNJUDGED` says exactly that. *But* the dashboard's Decisions
  band is event-fed, so the UI shows a resume the ledger does not — worth a line
  in the postmortem.

### New stray found (not in any previous handoff)

`plugins/agent-loop/.claude/loop/run/` is **tracked test detritus** committed by
plan C's `70ce39c` — `.gitignore` and `LOOP_DECISIONS.md` are tracked;
`harness.log` beside them is masked by the root `*.log` rule. A stray loop dir
inside the plugin ships to every installer. **Delete it in the pre-merge
cleanup.**

---

## 5. What is NOT proven, and exactly how to finish it

Both gaps are the same gap: **no Worker was ever killed by its wall.**

1. **T3 was meant to be the deliberate timeout and finished in 8m53s** — a
   sonnet Worker did 36 JSDoc blocks + 36 tests inside a 420 s wall. My sizing
   was wrong, not the harness.
2. `config.load_config` runs **once at boot** (`run.py:1547`), so
   `worker_timeout` cannot be changed mid-run. I stopped the loop and restarted
   with `worker_timeout=90` — correct instrument — but every task after that
   died at **contract validation (F1)** before a Worker ever started.
3. The `tally-v2` migration run was the deterministic fallback (a **hand-written
   valid contract**, so boot-reconcile dispatches the Worker with no Scout in the
   way). It migrated correctly, then **the weekly usage limit hit**: the Judge's
   reply was literally `"You've hit your weekly limit · resets Sep 16 at 11pm"`,
   so it deferred T1 rather than resuming it. The harness handled the limit
   **gracefully** — `usage limit hit — state is saved on disk; re-run after your
   window resets to resume`, exit 0. That part is a pass.

### The recipe to finish both, cheaply and deterministically

Rebuild `tally-v2` exactly as §3 describes (v2 layout, `[~]` T1, **no**
`task-T1.json`), with:

- `worker_timeout=90` in `Limits:`
- a **hand-written** `runtime/sprint-T1.json` that passes validation — the one
  that worked is in the ledger; the rule is **no braces, no dotted identifiers,
  no `/**`, no `*/`, no `[`, no glob metachars anywhere in `success_criteria` or
  `verification`**, and every real path in `allow_list`
- a task big enough to beat 90 s (all 36 exports)

Then `LOOP_DIR=… bash run.sh` and confirm, in order:

- `boot-reconcile: T1 is [~] but runtime/task-T1.json does not exist … asking the Judge`
- the Judge answers **resume** (not defer — it deferred only because it was rate-limited)
- `run.py:628`: `the Worker hit its 90s cap; wrapping up on session <id>`
- a `resume` event with `kind="wrapup"`, then one with `kind="resume"`
- `runtime/task-T1.json` `session_ids` contains a **`worker`** key, and the
  resumed dispatch reuses that same id (that is the `--resume` proof)
- T1 ends `[x]`, i.e. the `[~]` task actually resumed

**Watch out:** a wait that greps `task-T*.json` for a `worker` session across
*all* tasks matches a **previous** task's completed session. Scope it to the
task under test. (I made that mistake; it cost one false signal.)

---

## 6. Postmortem comparison (§6 item 4) — partial

Baseline is `~/Downloads/EXTRACT/08-analysis/cost-effort.md`.

| metric | v2 `2026-09-11-internal-app` | this run (3 tasks committed) |
|---|---|---|
| $/task | **median $3.85**, mean $4.30 (n=65) | **$1.37** |
| wall/task | ~1,250 s tick-time (~1,680 s span) | **434 s** |
| human touches | 3 restarts | 2 (one Stop-now I initiated; one host OOM kill) |
| needs-human | 2 | **0** |

Dashboard's own Effort panel read **$0.96/task, 83 % cache reads, $11.74/h**
mid-run.

**Do not publish these as the headline.** Three trivial tasks on a toy repo is
not comparable to 65 tasks on `internal-app`, and the run's *real* outcome was
3 of 7 tasks committed with 2 lost to F1. The honest summary is: **per-task cost
and wall-clock improved by roughly 3× on like-for-like trivial work, and
completion rate was poor for reasons F1–F3 explain.** Redo this properly after
F1–F3 are fixed.

Use `scratchpad/loop-metrics.py` (recreate it; it is small) — `$/task`,
`wall/task`, needs-human, incidents and every judge/resume/split decision from a
loop dir.

---

## 7. Remaining work, in order

1. **Decide on F1–F3.** My recommendation is to fix all three before merge; it is
   Mitch's call. If merging anyway, they must at minimum be written into the
   plugin README's known-issues.
2. **Finish §5** — the timed-out-Worker `--resume` and the `[~]`-resume after
   migration, using the recipe above. Needs a fresh usage window.
3. **Redo §6's comparison** on a run that completes.
4. **Pre-merge cleanup (do this LAST):**
   - `git rm --cached` the 7 force-added plan files from `3ab6da8`
     (`docs/superpowers/plans/2026-09-13-agent-loop-v3-*.md`, 19,446 lines).
     They are tracked-but-would-be-ignored (the `docs/superpowers/plans/` rule
     exists; `check-ignore` returns 1 only because they are tracked), so
     `--cached` drops them from the merge and keeps them on disk.
     **`docs/superpowers/specs/` is tracked and STAYS.**
   - `git rm` the stray `plugins/agent-loop/.claude/loop/run/` (see §4).
5. **`bash scripts/test-all.sh` green, then merge is Mitch's call. Stop at the
   approval gate.** Every `git push` is gated by an `ask` rule.

## 8. Standing constraints (unchanged, still binding)

All twelve from `/private/tmp/agent-loop-v3-merge-handoff.md` §"Standing
constraints" still apply. Re-verified this session: **constraint 3** —
`grep next_tier runner/phases.py runner/run.py` is still empty (`config.next_tier`
exists and is the harness's own one-step escalation, which the constraint allows).

Model routing: Opus for `serve.py` state derivation and the runner; Sonnet for
docs and skills; Fable for design questions only.
