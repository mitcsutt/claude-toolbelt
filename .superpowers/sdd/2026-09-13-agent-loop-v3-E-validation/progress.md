# Plan E — validation + merge (agent-loop v3)

Opened 2026-09-13 by the Opus 5 session continuing from
`/private/tmp/agent-loop-v3-merge-handoff.md`. **Inline development** (Mitch's
instruction) — no implementer subagents. Ledger discipline and red-first
assertions kept.

Task list is HANDOFF §6 items 2–4 plus the merge handoff's items 4 (pre-merge
cleanup) and 5 (merge, Mitch's call).

## Log

- **Auth check (merge-handoff first-move 3)** — `claude -p` headless returns
  `PONG`, rc=0, on `claude-sonnet-4-6`. The `stderr=DEVNULL` trap
  (`claude_proc.py:295`) is not going to eat the first hour.
- **Baseline** — `bash scripts/test-all.sh` at `e603725`: see below.
- **New finding, not in the handoff:** `plugins/agent-loop/.claude/loop/run/`
  is **tracked test detritus**, committed by plan C's `70ce39c`
  (`.gitignore` + `LOOP_DECISIONS.md`; `harness.log` beside them is ignored by
  the root `*.log` rule and so was invisible). A stray loop dir inside the
  plugin ships to every installer. Belongs in the pre-merge cleanup with the
  force-added plan files.
- **Baseline confirmed** at `e603725` — `test-all: PASS`, `EXIT=0`. Every number
  matches the merge handoff: `validate --strict: ok` (ran, not skipped),
  `shellcheck clean (25 files)` (present, not a SKIP), e2e 125, prompts 133,
  setup 134, medic 66, postmortem 26, runner `Ran 691 tests ... OK`, serve
  `Ran 126 tests ... OK`, `node --test` pass 61 fail 0.
- **Constraint 3 re-checked:** `grep next_tier runner/phases.py runner/run.py`
  is empty (rc=1). `config.next_tier` exists and is the harness's own one-step
  escalation, which is what the constraint permits.

## Item 1 — the throwaway loop

**Repo** `scratchpad/tally-demo`, branch `agent-loop-tally`: a static tally
board (`public/`), a pure helper module (`src/format.js`), a 36-export
undocumented `src/catalog.js`, `node --test` as the pipeline.

**The render recipe is real, and was proven before a token was spent.**
`scripts/shot.mjs` drives Playwright Chromium against the harness-started
`python3 -m http.server`; standalone it wrote a 900x640 PNG, rc=0. Two things
the machine forced, both of which a stub would have hidden:
  - the default `node` here is **18.16**, and playwright-core refuses to start
    below node 20 — so `scripts/shot.sh` resolves a >=20 nvm node and execs the
    driver. The recipe `command:` points at the wrapper.
  - playwright 1.63 wants `chromium_headless_shell-1243`; the cache held 1234.
    Downloaded. **A render recipe has a browser-binary dependency the loop
    cannot satisfy for itself** — worth knowing before the loop machine runs one.

**Task shapes** (HANDOFF §6 asks for one deliberately timed-out Worker and one
UI task with a render recipe):
  - T1 `| no-ui` — `formatDelta` + tests. Small, should not trip the wall.
  - T2 — renders a counts list; its allow_list must match `public/**`, so the
    contract MUST carry a `render_gate` or the harness refuses it. This is the
    UI task.
  - T3 `| no-ui` — JSDoc + a unit test for **all 36** catalog exports. ~72 edits
    against `worker_timeout=420`. This is the deliberate timeout: not a
    sabotaged limit, real work that does not fit the wall.

### The run, as it happened

- **13:34:11** launched; dashboard sidecar came up on `http://127.0.0.1:57579`.
- **13:34:57 — the carried `PATH_TOKEN_RE` bug fired on the very first Scout**,
  exactly as the merge handoff predicted. T1's contract was refused because
  `m.formatDelta` and a bare `{` inside an inline `node -e "…"` verification
  one-liner read as repo paths outside `allow_list`. **This is now observed, not
  theorised** — second real run, first Scout, same defect. Plan B's ruling
  (carry; fix by skipping tokens inside `-c "…"`/`-e '…'`, never by requiring a
  `/`) stands, but the "cost today ~$0.10–0.20 and 43 s" estimate is confirmed
  live: the re-scout is inside T1's 3m07s.
- **13:37:23** T1 committed `4607e08`, `test=pass`, tick cost **$0.77**.

## Carried item — spec §157 `fidelity_source` (assigned to D, not done)

Fixed in the **spec**, not the code, as instructed: §157's example now reads
`src`/`dst`, and §5.4 gained a sentence saying `from`/`to` is accepted on the
wire but `src`/`dst` is what `contract.FidelitySource` carries and what the
Scout is told to write. `contract.py:53-55` untouched — it was right.

## Carried item — `dashboard.html` MOCK drift

The comment claimed byte-identity with `tests/fixtures/snapshot-sample.json`.
Measured drift: exactly the two fields the handoff named
(`loop.plugin_version` 2.1.0 vs 2.0.0, `loop.migration` object vs null).

**The fixture is referenced by nothing else in the repo** — grep finds exactly
one reference, that comment. So the fixture's entire reason to exist is the
claim, and an unasserted claim made it dead weight. Asserted rather than
dropped: `serve.test.py::TestMockMatchesFixture` parses MOCK straight out of
`dashboard.html` and compares it to the fixture, plus a second test pinning both
to `plugin.json`'s version so 3.0.0 cannot go stale again silently.

Run **red first** (per the plan-D gotcha): both tests failed on the two real
fields — `'2.1.0' != '3.0.0'` and a 15,677-char document diff. Then reconciled
to one coherent v3 example: `plugin_version` 3.0.0, `schema` 3, and a
`migration` of `{from: 2, to: 3, actions: "artifacts-dir,decisions-file,
harness-log,gitignore-artifacts"}` — the action strings copied from
`migrate.ensure_layout`, so the example is what the code actually emits rather
than invented. A 3.0.0 snapshot still showing `schema: 2` and a 1→2 migration
would have been internally impossible.

Comment wording corrected twice: it now says the two are equal **as documents,
not as bytes** (the fixture is indented JSON), which is what the test asserts.
First rewrite still said "byte-identical" and would have been a second comment
that lies. First fixture edit was reverted too — a `json.dump` re-indent
reflowed unrelated compact arrays; redone as three textual substitutions,
5 insertions.

## Item 2 prepared — a real schema-2 paused dir

`scratchpad/tally-v2`, branch `v2-paused`, built to the v2 layout rather than
described: `run.log` (not `harness.log`), a `.gitignore` with no `artifacts/`,
**no** `LOOP_DECISIONS.md`, **no** `artifacts/`, `runtime/schema` = `2`, a
v2-shaped `sprint-T1.json` whose `forbidden` is bare strings and which lacks all
five v3 keys, **no `runtime/task-T1.json`**, `T1` left at `[~]`, and a
half-finished uncommitted edit in `src/catalog.js` standing in for the worker
that died. That combination is the exact spec §14 branch the handoff says is
unproven: `[~]` + readable contract + no state file → the Judge with
`failure="boot-reconcile"`.

## Item 3 baseline (EXTRACT/08-analysis/cost-effort.md)

| metric | v2 run `2026-09-11-internal-app` |
|---|---|
| $/task | **median $3.85**, mean $4.30 (n=65 done) |
| wall/task | 81,381 s of tick time / ~65 tasks ≈ **1,250 s**; 30.3 h harness span ≈ 1,680 s/task |
| human touches | **3 restarts** — one Ctrl-C pause at tick 74, two needs-human halts |
| needs-human | **2** (ticks 85, 88, both `rc=124` timeouts) |

### FINDING — `PATH_TOKEN_RE` is worse than the carried ruling says, and the proposed fix does not cover it

It fired again on **T2** (13:39:47), so **2 of 2 tasks so far, 100%**, each
costing a whole extra Scout dispatch (T1 ~46 s, T2 **144 s**).

The handoff carries plan B's ruling: *"Fix it by skipping tokens inside an
inline `-c "…"` / `-e '…'` one-liner instead."* T1's occurrence was exactly that
shape, so the ruling looked sufficient. **T2's is not**, and the proposed fix
would not have caught either of its two refusals:

1. `'… has a list container inside #board (e.g. <ul id="counts">) that app.js
   populates …'` names **`app.js`** — a bare filename in ordinary English prose.
   `public/app.js` *is* in the `allow_list`; the bare token is not covered by
   that glob, so a criterion describing the very file the task edits is refused.
2. `'… an in-file array of at least 3 counter objects, each with a name and a
   count, e.g. { name: "Coffees", count: 12 }'` names **`{`** — a brace in a
   prose example, matched as a glob metachar.

Neither is inside a `-c`/`-e` one-liner. The real defect is broader: **the regex
runs over prose, and success criteria are prose.** A fix scoped to inline
one-liners closes T1's case and leaves T2's open.

Not fixing it here — it is explicitly carried, and this session's job is
validation, not re-opening plan B. But the ruling's *remedy* needs revising
before anyone implements it, and the cost is not the "~$0.10–0.20 per
occurrence" the handoff estimated: it is one extra Scout on **every task
observed so far**.

### The render gate, against a real browser — PROVEN

T2's second contract was accepted and the Scout instantiated the recipe exactly
as `scout.md` specifies: `{route}` → `/`, `{screenshot}` → `shots/board-counts.png`,
one `screenshots` entry `{"name": "board-with-counts", "path": "shots/board-counts.png"}`,
and `evaluator_must_view: ["board-with-counts"]`. The harness started the app
itself (`render_app_start` event) rather than relying on anything pre-running.

Evidence chain, all four links checked rather than assumed:

1. **On disk** — `artifacts/T2/board-with-counts.png`, PNG 900x640, 12,951 bytes.
2. **The durable event** — `{"type":"artifact","tick":2,"task":"T2",
   "name":"board-with-counts","path":"artifacts/T2/board-with-counts.png"}`.
   **Loop-dir-relative**, which is precisely what `e603725` fixed. Pre-fix this
   read `.claude/loop/2026-09-13-tally/artifacts/...` (worktree-relative);
   `serve.artifact_path` joins it onto its own abspath'd loop_dir, so the result
   would not have started with `<loop_dir>/artifacts` and the sandbox check at
   `serve.py:1150` would have returned None → **404 on every screenshot**.
3. **Through the route** — `GET /api/artifact?tick=2&name=board-with-counts` →
   `HTTP/1.0 200 OK`, `Content-Type: image/png`, `Content-Length: 12951`, and the
   body's SHA-256 is byte-identical to the file on disk
   (`e55bc35b30aa21dc73fddff7d094def19b0bc5c1f4ce2f15914920e3f278a77e`). This is
   the route the handoff says no end-to-end test covers. It now has one, run by hand.
4. **The image is of the product, not of a blank page** — opened it: the Tally
   board with three styled counter rows ("Coffees brewed 12", "Commits shipped 7",
   "Bugs squashed 3") and the empty-state text gone. A render gate that
   screenshots a white screen would satisfy 1–3 and prove nothing; G1 is only met
   because the picture shows the feature.

`bash scripts/test-all.sh` with this session's edits: **test-all: PASS, EXIT=0**;
serve tests **126 → 128** (the two new ones), every other count unchanged.

### `PATH_TOKEN_RE` — the full tally, and a correction to the entry above

**3 of 3 tasks refused on contract try 1.** Every task in the run. The six
offending tokens, with where each actually sat:

| task | token | where it sat | inside an inline `-c`/`-e`? |
|---|---|---|---|
| T1 | `{` | inside `node -e "…"` | **yes** |
| T1 | `m.formatDelta` | inside `node -e "…"` | **yes** |
| T2 | `app.js` | prose success criterion | no |
| T2 | `{` | prose criterion, `e.g. { name: "Coffees", count: 12 }` | no |
| T3 | `/**` | prose criterion describing a JSDoc block | no |
| T3 | `*/` | same criterion | no |
| T3 | `[xs` | prose criterion, `return \`[xs, k]\`` | no |

**Correcting my own earlier note in this ledger**, which said the carried remedy
closes "half the bug" — be precise: plan B's proposed fix (*skip tokens inside an
inline `-c "…"` / `-e '…'` one-liner*) would have prevented **T1's rejection
entirely** (both its tokens are in the `node -e` string) and **neither T2's nor
T3's**. So it fixes **1 of 3 rejections**, not half of six tokens.

The residue is the real defect: **`PATH_TOKEN_RE` is run over `success_criteria`,
and success criteria are English prose.** Any brace, any JSDoc delimiter, any
bare filename, any bracketed code fragment a Scout writes to *explain itself*
reads as an unauthorised repo path. Note what T3's criterion was doing when it
tripped: correctly warning that 28 of the 36 catalog functions are stubs and must
be documented as they behave, not as they are named. **The check punished the
most careful thing the Scout wrote.**

Cost, measured rather than estimated: one extra Scout dispatch on every task —
T1 ~46 s, T2 144 s, T3 ~163 s. The handoff's "~$0.10–0.20 and 43 s per
occurrence" understates both the rate (it is 100%, not occasional) and the
duration. Still **not fixing it here** — it is explicitly carried and this is a
validation session — but the remedy as written should not be implemented as-is.

### Stop now — lands at a phase boundary, measured

Pressed the real **Stop now** button in Chrome (not a curl to `/api/stop`):

- `runtime/STOP` appeared at **13:56:12Z** — so the button's POST reached
  `/api/stop` and `e6a753d`'s sentinel write works from the UI.
- harness logged `paused between phases; checkpoint written` at **13:56:13Z**
  and exited **0**.
- **~1 second from sentinel to stop**, against a `tick_timeout` of 1800 s. The
  tick was 43 s in. §6's "minutes, not the end of the tick" is met with room to
  spare.

Do not overstate the number: I recorded a baseline at 13:56:04 and then spent two
tool calls locating and clicking the button, so the 9 s gap from that baseline is
mostly my own latency. The defensible measurement is **sentinel → stop ~1 s**.

`runtime/CHECKPOINT.json` was written correctly:
`{"stopped_after_tick": 5, "next_task": "T4", "segment": "Segment A: tally board"}`,
T4 left `[~]` with `phase: SCOUT:done` and its scout session id retained.

### T3 did NOT time out — the plan's deliberate timeout failed to fire

T3 finished in **8m53s**, committed `5341cee`: the Worker wrote all 36 JSDoc
blocks and 36 tests inside the 420 s wall. My sizing was simply wrong — the task
was not big enough to beat the wall. Worth recording as a result in its own
right: a sonnet Worker does ~72 mechanical edits in under seven minutes.

The Reviewer then added **4 follow-up rows** (T4–T7), so the plan grew 3 → 7 and
the run continued rather than ending.

**Forcing the timeout properly.** `config.load_config` is called **once at boot**
(`run.py:1547`), never per tick, so editing `LOOP_CONFIG.md` mid-run does
nothing. The restart was therefore the instrument: Stop now (already needed for
§6), then `worker_timeout=420 -> 90`, clear `runtime/STOP`, relaunch. T4 resumes
at `SCOUT:done`, so the next phase is WORK and a 90 s wall is certain to cut it.
This is a deliberate, disclosed instrument — the resume path is what §6 asks to
be proven, not the plausibility of the cause.

### `PATH_TOKEN_RE` — final tally: 4 of 4 tasks

T4 was refused too, on four more tokens: `{`, `board.dataset.ready`,
`counters.length`, `empty.hidden`. Three of those four are the **dotted-identifier
class the handoff already names** (`contract.py:27-29`), and all four sit in
prose success criteria describing JavaScript the task must write.

**Running total: 4 of 4 tasks refused on try 1, 11 offending tokens.** Exactly
**two** of the eleven (T1's `{` and `m.formatDelta`) were inside an inline
`-c`/`-e` one-liner, i.e. inside the scope of the remedy plan B proposed. Nine
were not.

Every task in this run paid for one extra Scout. That is not a tax that shows up
occasionally in a long run; on this evidence it is the default path.

### Observation — the Decisions band and LOOP_DECISIONS.md can disagree

After the restart, `LOOP_DECISIONS.md` was still just its header (57 bytes) even
though a `decision` event existed (`task=T4 decision=resume classification=open
cause=boot-reconcile`).

Traced rather than assumed:
- `judge.boot_reconcile` (the **no state file** branch, spec §14) calls
  `apply(...)` → `append_decision`, so it **does** write the ledger.
- `run.boot_reconcile`'s **has state file** branch (`run.py:290-292`) — the one
  that fired for T4 — emits the event directly and never calls
  `append_decision`.

**Not filing this as a defect.** Resuming a task at its own recorded phase is
mechanical; nothing was judged, which is exactly what `classification=UNJUDGED`
is saying. `LOOP_DECISIONS.md`'s header promises "every judgement", not every
event.

What is worth knowing: `serve.py`'s Decisions band is fed from the
`decision`/`resume`/`split` **events**, so the dashboard shows this resume and
the ledger does not. A human who reads only `LOOP_DECISIONS.md` in the morning —
which is what the setup skill and the postmortem both point them at — will not
see that the loop restarted and resumed a task. Worth a line in the postmortem,
not a code change on my own judgement.

## §6 item — "the Judge writes LOOP_DECISIONS.md": PROVEN, and it is good

T4's contract failed validation on **both** Scout tries, so `handle_failure`
dispatched the Judge, which wrote a full entry to `LOOP_DECISIONS.md`:
`widen (self-imposed)`, with Rationale, Instruction to the next Worker, **five**
rejected alternatives each with its reason, Applied, and Reverse. This is the
artefact §6 asks for and it is genuinely high quality.

**The Judge independently diagnosed the `PATH_TOKEN_RE` bug**, and found a
consequence I had not:

> "The suggested remedy in the error ('write counters.length (read)') is already
> satisfied in the prose … **and it is unsatisfiable in the two verification
> commands, because annotating a grep pattern as 'board.dataset.ready (read)'
> makes the pattern match nothing.**"

That is a third, worse failure mode than the two above. The validator's advertised
escape hatch — the `(read)` marker — **cannot be used inside a `verification`
command**, because the marker becomes part of the shell string and breaks the
grep. For a dotted symbol inside a verification command there is *no* legal
contract: annotate it and the check is meaningless, leave it and the contract is
refused. The Scout used its second try writing `counters.length (read)` in the
prose, and was refused anyway on the two greps it could not annotate.

**T4 is now `[!]` blocked.** So on this evidence `PATH_TOKEN_RE` is not a tax at
all — it **cost a whole task**, on a contract the Judge itself called "sound on
the merits".

## TWO NEW DEFECTS, both traced to their lines

### 1. `LOOP_DECISIONS.md` claims changes that were never written

`run.py:583` calls `judge.apply(..., persist=in_force is not None)`. On the
invalid-contract path `in_force is None`, so **`persist=False`** and
`judge.py:639`'s `if contract_dirty and persist: save_contract(...)` correctly
writes nothing — the contract being widened is a synthetic stand-in
(`run.py:552`), so there is nothing real to persist.

But `applied` is built regardless (`judge.py:616-638`) and
`append_decision(ctx, decision, applied)` is called unconditionally
(`judge.py:704`). Result, verified on disk:

- `LOOP_DECISIONS.md` says `**Applied:** allow_list += counters.length;
  allow_list += board.dataset.ready; instruction appended to scout_notes; next
  attempt runs at tier standard`.
- `runtime/sprint-T4.json`'s `allow_list` is still exactly
  `["public/app.js", "public/index.html"]`. **None of it happened.**
- Its `**Reverse:**` line instructs a human to "delete 'counters.length' and
  'board.dataset.ready' from the allow_list array" — **two entries that are not
  there.**

The ledger is the audit trail a human uses to ratify or reverse; here it
misreports what the loop did. `judge.py:603`'s own comment shows the author knew
`persist=False` means a stand-in — the gap is that `applied` was not filtered by
the same flag.

### 2. A try-exhausted deferral writes no `LOOP_CLEANUP.md` entry

`run.py:805-814`: when the Judge answers `retry`/`escalate`/`resume` to an
invalid contract, the harness overrides it to a deferral, calls
`judge.mark_blocked` and then `finish_deferral(h, ctx, None, {...})`.

`finish_deferral`'s docstring (`run.py:431`) states: *"`judge.apply` has already
flipped the glyph, **written the LOOP_CLEANUP entry** …"* — true for the
`defer`/`halt` branch of `judge.apply`, which calls `write_cleanup_entry`. But
here the Judge decided **`widen`**, so that branch never ran and nothing wrote
the entry. `finish_deferral` then commits `[plan_path, cleanup_path]` with an
unchanged cleanup file.

Verified on disk: `LOOP_CLEANUP.md` is still the scaffold placeholder
(`- [ ]`), its only commit is `e40e175 loop: scaffold tally loop`, and
`105595d loop: block T4 — open` touches **`LOOP_PLAN.md` alone** (1 file,
1 insertion).

**So T4 sits blocked with an empty human follow-up list.** The one file the setup
skill and postmortem both point a human at for manual follow-ups does not mention
the task that needs one.

Note this is **not** the handoff's carried item, which says the opposite
("Cap-forced deferral writes `LOOP_CLEANUP` but no `LOOP_DECISIONS` entry"). This
path does the reverse. Both are real; they are different call sites.

**Not fixing either.** Both live in already-merged plan C/D code, the fix to
`judge.apply` is a judgement call about what `applied` should mean, and this
session's remit is validation up to the approval gate. Recommendations are in the
summary.

### Observation — the Reviewer can plan work inside the loop's own dir

The segment Reviewer wrote T5 as:

> `- [~] T5: Repair the 'ready flag set after DOM insertion' invariant in
> .claude/loop/2026-09-13-tally/LOOP_LEARNINGS.md so its check passes against
> public/app.js as written | depends_on: T2`

i.e. a task whose deliverable is a file inside `$LOOP_DIR`. `git_ops.protected_paths`
protects the loop dir from the sandbox's stray-**revert** (so the loop cannot
destroy its own record), but nothing stops the Planner/Reviewer from *targeting*
a loop artefact as task output, and nothing stops a Scout putting it in an
`allow_list` so `commit_task` commits it.

Not destructive here, and arguably in-bounds — learnings are meant to be written
back. Recorded because a plan row pointing into `$LOOP_DIR` is the kind of thing
that looks fine on a 3-task demo and is a governance problem on a 70-task run: it
is the loop grading its own homework, inside the file the next Scout reads as
context.

## T5 killed the same way — and now the human-facing record is actively wrong

T5 was refused on both tries and deferred, identically to T4. Its tokens:
`.append`, `.appendChild`, `{print` (an awk program), `board.dataset.ready`, and
— worst — **`public/app.js` and `src/format.js`, real repo paths**, referenced
read-only inside grep patterns where the `(read)` marker cannot be used without
destroying the pattern. The Judge's T4 diagnosis, reproduced exactly.

**Score for the run: 7 planned tasks, 3 committed, 2 killed outright by contract
validation, 5 of 5 tasks refused on try 1.**

### What a human finds the next morning

| surface | says |
|---|---|
| `LOOP_PLAN.md` | T4 `[!]`, T5 `[!]` |
| `LOOP_CLEANUP.md` | **empty** — the scaffold `- [ ]` placeholder, never written, never committed |
| `LOOP_DECISIONS.md` | two entries headed `widen` and `retry`. **Neither says the task was blocked.** |

And both entries misreport what happened, verified on disk:

- T4 — `Applied: allow_list += counters.length; allow_list += board.dataset.ready;
  instruction appended to scout_notes`. `sprint-T4.json`'s `allow_list` is
  unchanged.
- T5 — `Applied: instruction appended to scout_notes; next attempt runs at tier
  standard`. `sprint-T5.json`'s `scout_notes` contains no `JUDGE` string at all
  (checked: `'JUDGE' in scout_notes` → `False`).
- Both `Reverse:` lines instruct a human to undo edits that **do not exist**.

So the loop's auditable-decision story (G3) and its fewer-human-touches story
(G5) both break down exactly where they matter most: two tasks are blocked, the
follow-up file is empty, and the decision ledger describes remedies that were
never applied and tells the human to reverse changes that were never made. A
human following `LOOP_DECISIONS.md` literally would go looking for allow_list
entries and scout_notes paragraphs that are not on disk.

This is one root cause (`persist=False` not filtering `applied`) plus one gap
(`write_cleanup_entry` never reached on the try-exhausted path), both recorded
above with line numbers.
