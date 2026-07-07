---
name: agent-loop-setup
description: Use to bootstrap an autonomous coding loop for refactors, new features, test sweeps, or other long-running multi-task work. Interactive, one-time setup that prepares loop artefacts inside a git worktree, then prints a launch command for the bash harness (run.sh) — which re-invokes a fresh headless Claude tick per task. Runs a config wizard, brainstorms and plans via superpowers, sizes the plan adaptively, advisory-checks permissions, and warns about missing rabbit-hole protection. Triggers on "set up a loop", "agent loop setup", "kick off an overnight refactor", "start a loop on X".
---

# Agent Loop Setup

Bootstrap an autonomous coding loop. This is the **interactive** half of the v2 mechanism: you (with the human in the room) prepare a set of artefacts inside a git worktree, then hand off to a bash harness.

The runtime half is **not** an interactive skill. `run.sh` is a bash orchestrator that re-invokes a **fresh headless `claude` tick per task** (each tick reads `tick-prompt.md`). There is no self-scheduling, no in-session wakeup, and no persistent agent — every task gets a clean context. Your job here is to leave behind enough state (config + plan + scaffold) that a stateless tick can pick up the next task and make progress.

This skill is the **only** place where questions are asked of the human. Once `run.sh` is running, the loop is autonomous and headless — it cannot ask for clarification. Front-load everything here.

## Hard preconditions

Bail immediately if any fails:

1. **Must be in a git repo.** `git rev-parse --git-dir` succeeds.
2. **Working tree must be clean.** `git status --porcelain` empty.
3. **No existing loop in progress.** No `LOOP_CONFIG.md` under `.claude/loop/<run-id>/` (the per-run base dir — see Step 1.5). If present, surface to the user — either re-launch the harness, or run `/agent-loop-postmortem` to close out the previous loop first.

## Dependencies

- **Required:** `/postmortem` skill resolvable (used at loop close by `/agent-loop-postmortem`). If not: halt and ask the user to install the `postmortem` plugin.
- **Recommended:** `permission-advisor` skill resolvable. If not: log a warning and skip the advisory permission check in Step 4. Because the harness runs with `--dangerously-skip-permissions`, this check is advisory only — the loop still runs without it.

Resolve both via the Skill tool's dry-resolve. Document the dependency in the plugin README.

## Steps (run in order)

### Step 1: Worktree resolution

The harness refuses to run anywhere except the worktree recorded in `LOOP_CONFIG.md`. Resolve it now.

- **Detect whether this session was launched with `--worktree`.** A `--worktree`-launched session has its cwd under `.claude/worktrees/` (e.g. `<repo>/.claude/worktrees/<name>/`). Check `pwd` against that pattern.
- **If yes** — the session is already inside a worktree: use it. Confirm the path with the user, but do not make them create another one.
- **If no** — the session is in the main checkout (or a plain branch): **ask the user to create or select a worktree.** Recommend `superpowers:using-git-worktrees` (native `--worktree` or the git-worktree fallback), with the canonical path `<repo>/.claude/worktrees/<name>`. Do not silently run the loop in the main checkout — worktree isolation is part of the safety model.
- Record the **absolute** path as `Worktree:` in `LOOP_CONFIG.md`. The harness reads this field and `die`s if `pwd` does not match it.

### Step 1.5: Resolve the per-run base dir

All loop artefacts live under a single per-run directory: `.claude/loop/<run-id>/`, where `<run-id>` is `<YYYY-MM-DD>-<topic>`. `<topic>` is the branch suffix (the part after `agent-loop-`); `<date>` is today (`date -u +%F`). The postmortem is written inside this dir as `$LOOP_DIR/POSTMORTEM.md`.

- Compute `RUN_ID="$(date -u +%F)-<topic>"` and `LOOP_DIR=".claude/loop/$RUN_ID"` (relative to the worktree root).
- `mkdir -p "$LOOP_DIR/runtime"`. Durable artefacts (config, plan, learnings, cleanup) live directly under `$LOOP_DIR/`; ephemeral runtime files (LOCK, PAUSE, sprint-*, worker-result, ratelimit, plan-usage) live under `$LOOP_DIR/runtime/`.
- **Track the durable artefacts; ignore `runtime/` AND the machine-generated logs.** Write `$LOOP_DIR/.gitignore` with:

  ```gitignore
  runtime/

  # Machine-generated logs — bulky, noisy, and not needed on the branch
  # (a forensic review found these were ~74% of one run's branch diff;
  # run.log alone was ~22k lines). The postmortem reads them from disk, not git.
  run.log
  events.jsonl
  LOOP_LOG.jsonl
  LOOP_USAGE.jsonl

  # Reports live on disk, not on the branch.
  *.html
  POSTMORTEM.md
  ```

  These files sit **directly under `$LOOP_DIR`** (not under `runtime/`), so the old one-line ignore never caught them. The durable plan/config/learnings/cleanup files are still tracked, preserving the `Loop-Status:`/`Loop-Verification:` commit-trailer audit trail — that audit trail lives in commit messages (via `git log --grep`), not in the ledger files, so it survives. Do **not** add `.claude/loop/` (or the whole run dir) to the repo's root `.gitignore` — that would re-bury the audit trail.

`LOOP_DIR` is the value the launch command passes to `run.sh` (Step 6).

### Step 2: Config wizard

Write `$LOOP_DIR/LOOP_CONFIG.md` from `templates/LOOP_CONFIG.md`. Use the AskUserQuestion tool to collect the inputs. **Ask one question per AskUserQuestion call** (do not bundle multi-select into single-select questions). Fields to populate:

1. **Goal** — free text, one sentence: "What are we building/changing?"
2. **Loop type** — single-select: `refactor` / `new-feature` / `test-sweep` / `custom`.
3. **TDD mode** — single-select:
   - `none` — run existing tests only; no failing-test-first per task.
   - `tdd-per-task` — write a failing test before each task's implementation; mandatory red → green.
4. **Verification pipeline** — multi-select from `lint`, `tsc`, `build`, `test`, `e2e` (`multiSelect: true`). Listed order is execution order. Write space-separated, e.g. `lint tsc build test`.
5. **Limits** — the harness reads `tick_timeout` from the single `Limits:` line as a `key=value` pair:
   - `tick_timeout` — per-tick wall-clock cap in **seconds**, enforced by `timeout`/`gtimeout` (see Step 4). Default `1200`. This is rabbit-hole protection for a single stuck tick, not a loop budget.

   There is **no cost, iteration, or wall-clock budget**. The loop runs until the plan is done or it hits your subscription's usage window — at which point the harness reads the `rate_limit_event` reset time and **auto-waits, then resumes** (or, if the reset is too far out, exits cleanly so you can re-run `run.sh` later). Write the single pair onto the `Limits:` line: `Limits: tick_timeout=1200`.
6. **Blocker policy** — single-select, default `continue-independent`:
   - `continue-independent` — a blocked task is marked blocked and the loop moves on to the next task whose dependencies are still satisfied; only halts when nothing independent remains.
   - `halt` — any blocked task stops the whole loop.
7. **Branch** — `agent-loop-<topic>`. Created in Step 5.
8. **Granularity** — set by the planning step (Step 3): `single` or `segmented`.
9. **Started** — write the current UTC ISO-8601 timestamp (`date -u +%FT%TZ`) into the `Started:` field of `$LOOP_DIR/LOOP_CONFIG.md`. The postmortem reads this to compute elapsed time; leave it unset and the postmortem reports `Elapsed: unknown`.

**Model tiers.** The template ships `Orchestrator model: sonnet` — the orchestrator (the per-tick spine) now defaults to a **standard** model (Sonnet) rather than inheriting the user's Claude default. Rationale: the spine only coordinates, runs the verification gate, and dispatches role subagents; all heavy reasoning (plan decomposition, quality/workaround judgement) is delegated to the most-capable Planner/Evaluator subagents, so running the spine on a top-tier model every tick is pure cost (it was ~90% of observed spend). Leave the line as-is for cheap-by-default; blank it to inherit the Claude default, or name any alias to override. The per-role `Planner/Scout/Worker/Evaluator tier:` lines are unaffected — they still pick a tier per role (see `tick-prompt.md` §16). The `Evaluator tier:` line now ships **blank**: the Evaluator's tier is governed per-task by `tick-prompt.md` §10 (mechanical→skip, complex→most-capable, default→standard). Setting a tier here is a ceiling for `| complex` reviews only — it will NOT force the most-capable tier onto every task (a flat pin previously did exactly that, costing ~30 most-capable Evaluator runs in one observed loop). Leave it blank unless you have a reason to cap complex-review spend.

### Step 3: Planning (adaptive by size)

Plan once, here, with the human. The harness cannot ask questions later, so this is the only interactive planning opportunity.

1. **Spec via `superpowers:brainstorming`.** Invoke `brainstorming` with goal + loop type + verification pipeline as context. It explores project state, asks clarifying questions, proposes approaches, writes the design to `docs/superpowers/specs/<date>-<topic>-design.md`, and gets user approval. **This is the only step that asks the human questions.** Do not skip it — the spec is the bedrock for the rest of the loop.

2. **Then choose a granularity by feature size:**

   - **Small feature / refactor** (a handful of tasks, single coherent area): run `superpowers:writing-plans` to produce a **full** `$LOOP_DIR/LOOP_PLAN.md` with every task broken out now. Set `Granularity: single` and `Segment count: 1` in `$LOOP_DIR/LOOP_CONFIG.md`. There is no deferred planning — every task is enumerated up front.

   - **Large feature** (multiple distinct areas, many tasks, would be unwieldy to fully enumerate): produce the spec plus a **high-level segment map** — an ordered list of segments, each with a goal and acceptance criteria, but with each segment's detailed tasks **deferred**. Set `Granularity: segmented` and `Segment count: N`. At loop time a dedicated **PLAN tick** expands the next segment's tasks against the actual repo state at the segment boundary, so detailed breakdown stays fresh rather than going stale up front.

3. **Apply these planning rules regardless of size:**
   - **Task sizing:** each task is describable in **2-3 sentences**. Bigger than that → split it.
   - **Dependency ordering:** order tasks/segments so each only depends on earlier ones. A stateless tick picks the next task whose dependencies are done.
   - **Verifiable acceptance criteria:** every task and every segment states a concrete, checkable done-condition (a test that passes, a command that exits 0, a file that exists), because a headless tick has no human to judge "good enough".
   - **Clone-heavy plan warning.** The loop's sweet spot is *independent, novel-per-task* work. When the brainstormed plan is dominated by near-verbatim clones (the same template across many sibling types that differ only in a small substitution set), warn the user: clone work re-pays context per task and a naive plan forbids DRY (one run spent ~35% of wall-clock cloning, with a copy-paste bug shipping). The loop now handles this via `clone_of` tasks + lean reference contracts (tick-prompt §3/§6), but flag it so the user can confirm that is the intended shape rather than, say, one parameterised component built once. Surface the count of suspected clone-families in the setup summary.

   Write `$LOOP_DIR/LOOP_PLAN.md` accordingly: full task list for `single`; segment headers (`## Segment 1`, `## Segment 2`, …) with the first segment's tasks enumerated and later segments as PLAN-tick placeholders for `segmented`.

   Task checkbox legend:
   - `- [ ]` pending — not started
   - `- [~]` in progress — exactly one task at a time may hold this state
   - `- [x]` done — verification passed, commit landed (append the commit SHA)
   - `- [!]` blocked — append reason after the marker; behaviour governed by the Blocker policy
   - `- [-]` skipped — deferred or removed; never blocks downstream tasks
   - `[blocked-upstream]` — a dependency of this task is blocked, so it cannot start

### Step 4: Pre-flight checks

1. **Advisory permission check via `permission-advisor`.** Skip if the skill is unresolvable. Derive the likely bash command set from the verification pipeline (`lint` → `npm run lint`, etc.), the loop type (file ops, `git`, generator scripts), and the project package manager (`npm` / `pnpm` / `yarn`), then invoke `permission-advisor`. **Treat the result as advisory only.** The harness runs headless with `--dangerously-skip-permissions`, so the allowlist is mostly moot at runtime — surface only genuine gaps the user might care about (e.g. commands a hook would still block). Never modify settings files here.

2. **Rabbit-hole protection: check for a timeout binary.** The per-tick wall-clock cap (`tick_timeout`) is enforced by a `timeout` (GNU coreutils) or `gtimeout` (macOS Homebrew coreutils) binary. Check PATH:

   ```bash
   command -v timeout >/dev/null 2>&1 || command -v gtimeout >/dev/null 2>&1
   ```

   If **neither** is present, **warn the user explicitly**: the per-tick wall-clock cap is DISABLED, so a tick that goes down a rabbit hole can run unbounded with no automatic kill. Recommend installing **coreutils** (`brew install coreutils` provides `gtimeout` on macOS) to restore the protection, then re-running setup or the harness. This mirrors the `run.sh` behaviour, which falls back to running ticks without a wrapper and logs the same warning when no `timeout`/`gtimeout` is found.

3. **Environment readiness: prove the loop's environment can run the verification pipeline before handing off.** The loop runs in the worktree from Step 1, but *why* its toolchain might be unready is not the point — don't assume the cause, check the state. Many things leave an environment unable to run the pipeline: a fresh checkout whose dependency trees / build artefacts aren't installed (these are per-checkout and usually gitignored — `node_modules`, `vendor/`, `.venv`, `target/`, generated clients, build caches), a pruned cache, a package-manager version bump, uncompiled generated code, or deps that simply changed since the environment was last prepared. A headless tick that finds its toolchain missing cannot ask for help; the observed failure mode is a tick **improvising a workaround** — e.g. reaching into the main checkout (where deps happen to be installed) to run its pipeline there, silently breaching worktree isolation and risking writes to the main repo. Prevent this here, while a human is in the loop:

   - **Detect the project's toolchain** from the repo (lockfiles / manifests / config — `package.json` + the lockfile that pins the manager, `Gemfile`, `pyproject.toml`/`requirements.txt`, `go.mod`, `Cargo.toml`, etc.). Use the manager the repo already pins; do not assume one.
   - **Get the environment ready in the worktree**: install dependencies and run whatever one-time preparation the verification pipeline depends on (codegen, schema/client generation, an initial build, native deps). The goal is that **every command in the `Verification pipeline` resolves and runs from inside the worktree with no reach-over.** Run these from the worktree root; surface failures to the user rather than working around them.
   - **Prove it**: dry-run or lightly exercise each pipeline stage's tooling in the worktree (e.g. the linter/type-checker/test-runner reports its version or a no-op run succeeds) so you have evidence — not assumption — that the first tick can verify in place. If a stage's tooling still can't run, **stop and surface it** with options; do not hand off a loop whose first tick will improvise.
   - **Treat environment-state as unknown until checked** — never infer it from how the session was launched. A `--worktree` session may never have had deps installed; an existing worktree, a fresh clone, or a long-idle checkout may be just as unready. Verify, don't assume.

4. **Detect reverting PostToolUse hooks (the loop's worst silent enemy).** Inspect the target repo's `.claude/settings.json` and `.claude/settings.local.json` for `PostToolUse` hooks matching `Edit`/`Write`. Classify each:
   - **auto-fix** (runs `prettier --write`, `eslint --fix`, etc. and *keeps* the change) — benign; the loop can lean on it, and string-only edits are safe.
   - **revert/reject** (reformats such that a string-only Edit is undone, or blocks the tool) — HAZARD. In one run a Prettier hook reverted ~22 single-identifier edits, each forcing a manual `sed`/Python bypass. Warn the user at setup, and record an entry in `$LOOP_DIR/LOOP_LEARNINGS.md` `## Patterns`: "repo runs a revert-style PostToolUse hook on Edit — Workers must write-through (sed/Write + re-format) for string-only changes (§7)." This primes the §7 write-through default instead of letting Workers discover the revert the hard way, mid-run, 22 times.
   If you cannot determine the hook's behavior statically, note it as "unknown — treat as revert-style (write-through) to be safe."

### Step 5: Scaffold artefacts

Under `$LOOP_DIR/` (the runtime subdir was already created in Step 1.5), create:

1. **`$LOOP_DIR/LOOP_LEARNINGS.md`** — from the template (running notes the harness/ticks append cross-task learnings to). This is **per-run**: it always starts empty from the template, never seeded from prior runs.
2. **`.claude/loop/KNOWLEDGE.md`** — the **persistent, cross-run** knowledge file. It lives at `$(dirname "$LOOP_DIR")/KNOWLEDGE.md` — the **SIBLING** of the per-run `$LOOP_DIR`, NOT inside it, so it is shared across every run and survives them. Repo-scoped and committed/tracked (it sits under `.claude/loop/`, outside any per-run dir and not under the gitignored `$LOOP_DIR/runtime/`). It is **NOT seeded into the run and is NOT per-run** — it accumulates durable, generalized patterns across *all* loop runs in this repo, is read per task by the Scout, and is auto-promoted into at loop close by `/agent-loop-postmortem`. Create it **only if it does not already exist** (an existing file from a prior run must be preserved untouched): write a `# Loop Knowledge` header plus a `## Patterns (durable, cross-run; auto-promoted at loop close)` section (the `templates/LOOP_KNOWLEDGE.md` template is the canonical seed). The harness (`run.sh`) also creates it on first run if missing, so this is belt-and-braces.
3. **`$LOOP_DIR/LOOP_LOG.jsonl`** — empty file (`touch`). Each line is one JSON tick event.
4. **`$LOOP_DIR/LOOP_USAGE.jsonl`** — empty file (`touch`). The harness appends per-tick cost/usage here for the post-run record.
5. **`$LOOP_DIR/LOOP_CLEANUP.md`** — header `# Loop Cleanup — manual follow-up tasks` plus an empty `- [ ]` placeholder line.

The durable `$LOOP_DIR/` artefacts are tracked (Step 1.5); `$LOOP_DIR/runtime/` and the machine-generated logs/ledgers/reports are ignored via the nested `$LOOP_DIR/.gitignore`. Create the branch and commit the durable artefacts plus the design docs (scope `git add` precisely — never `-A`; the `.gitignore` excludes `runtime/` and the log/ledger files, so a `git add "$LOOP_DIR"` will not pick them up):

```bash
git checkout -b agent-loop-<topic>
git add "$LOOP_DIR/.gitignore" "$LOOP_DIR/LOOP_CONFIG.md" "$LOOP_DIR/LOOP_PLAN.md" "$LOOP_DIR/LOOP_LEARNINGS.md" "$LOOP_DIR/LOOP_CLEANUP.md"
# Ledgers/logs (LOOP_LOG.jsonl, LOOP_USAGE.jsonl, run.log, events.jsonl) are intentionally omitted —
# they are gitignored and read from disk by the postmortem, not from git history.
git add "$(dirname "$LOOP_DIR")/KNOWLEDGE.md"   # persistent cross-run knowledge (.claude/loop/KNOWLEDGE.md) — only newly created here; if it already exists this stages nothing new
git add docs/superpowers/specs/<date>-<topic>-design.md
git add docs/superpowers/plans/<date>-<topic>.md
git commit -m "loop: setup — <topic>"
```

### Step 6: Launch print (no shell-rc edits)

Do **not** start the loop and do **not** edit any shell rc file. Print the exact command for the user to copy and run, with `${CLAUDE_PLUGIN_ROOT}` resolved to this plugin's absolute path:

```
cd <Worktree> && LOOP_DIR=.claude/loop/<run-id> bash "${CLAUDE_PLUGIN_ROOT}/run.sh"
```

**Recommended — live dashboard via `/agent-loop`.** In Claude Code, run `/agent-loop`. It
discovers this loop and background-launches the dashboard (zero ongoing tokens; your session
stays free), printing a `http://127.0.0.1:<port>` URL. Equivalent shell launches still work:
`cd <Worktree> && LOOP_DIR=.claude/loop/<run-id> python3 "${CLAUDE_PLUGIN_ROOT}/web/serve.py"`
(headed) or `bash "${CLAUDE_PLUGIN_ROOT}/run.sh"` (headless). Tick numbers are continuous
across resumes; pausing writes a resume checkpoint (`runtime/CHECKPOINT.json`).

**Optional — headed (live dashboard).** To watch the loop in a browser with Start/Pause/Resume/Stop controls, launch the dashboard server instead of `run.sh` directly. It supervises the same loop and prints a `http://127.0.0.1:<port>` URL:

```
cd <Worktree> && LOOP_DIR=.claude/loop/<run-id> python3 "${CLAUDE_PLUGIN_ROOT}/web/serve.py"
```

Launching the dashboard **never auto-starts the loop** — it opens at the loop's current state (idle, paused, running, or done) so you can review progress and history first. Click **▶ Start** to begin a fresh run, or **⟳ Resume** to continue a paused one; **⏸ Pause**/**■ Stop** halt it after the current tick. This means you can also point it at a paused or finished loop purely to inspect it.

Headless `run.sh` and headed `serve.py` are interchangeable per launch — pick either, any time. The dashboard needs `python3` (stdlib only; no pip). It can also attach to a loop already running headless and observe it live. Add `--no-spawn` for a pure read-only observer (the buttons won't spawn `run.sh`). The dashboard is **event-driven**: the harness appends each tick's events to `$LOOP_DIR/events.jsonl` and the server streams cheap snapshots over SSE (no run.log scraping), so it shows a live status verdict, the role pipeline, effort-first usage, and the full roadmap (including ghosted future segments) in real time. Requires the agent-loop plugin at version ≥ 0.11.0.

- `<Worktree>` is the absolute path recorded in `$LOOP_DIR/LOOP_CONFIG.md`.
- `<run-id>` is the `<date>-<topic>` slug resolved in Step 1.5; pass `LOOP_DIR` so the harness writes/reads every artefact under that single per-run dir.
- Resolve `${CLAUDE_PLUGIN_ROOT}` to the real absolute plugin directory in what you print, so the command is copy-paste runnable.
- `run.sh` is the bash harness; it reads `$LOOP_DIR/LOOP_CONFIG.md`, enforces the worktree guard and the per-tick `tick_timeout`, and re-invokes a fresh headless tick per task.
- **No shell-rc edits.** Do not append anything to the user's shell startup files. The hand-off is the printed command only — the user runs it themselves when ready.

Also surface a short summary:

```
Loop is configured.

Worktree:     <absolute worktree path>
Branch:       agent-loop-<topic>
Run dir:      .claude/loop/<run-id>  (config/plan/learnings/cleanup committed/tracked; runtime/ + logs/ledgers gitignored)
Granularity:  single | segmented (Segment count: N)
Verification: <pipeline>
Limits:       tick_timeout (per-tick rabbit-hole cap; usage window is the real ceiling — loop auto-waits on it)

To start:   cd <Worktree> && LOOP_DIR=.claude/loop/<run-id> bash "<plugin-root>/run.sh"
            (or, live dashboard: /agent-loop  — or headed: python3 "<plugin-root>/web/serve.py")
To pause:   touch .claude/loop/<run-id>/runtime/PAUSE   (the in-flight tick finishes, writes runtime/CHECKPOINT.json, then the harness stops)
To resume:  delete .claude/loop/<run-id>/runtime/PAUSE and re-run the start command (tick numbering continues — never restarts at 1)
To close:   /agent-loop-postmortem
```

### Step 7: Posture note

State the safety posture to the user explicitly so they understand what they are launching:

- The harness runs each tick **headless** with **`--dangerously-skip-permissions`**, **inside the OS sandbox**, **confined to the worktree**.
- Skip-permissions removes the interactive allow/deny prompt, which is why the loop can run unattended — but the OS sandbox and the worktree confinement remain the real guardrails.
- The worktree guard in `run.sh` refuses to run if `pwd` does not equal the configured `Worktree:`. Branch + worktree isolation keeps the loop's writes off the main checkout.

## What to refuse / push back on

- "Skip the wizard, just start running" — refuse. The wizard captures structural config (Limits, Blocker policy, Worktree) the harness reads directly.
- "Skip brainstorming, hand me a plan immediately" — partially allow. A structured plan (`LOOP_PLAN.md`) is still required; a headless loop needs verifiable tasks.
- "Skip the plan, the tick will figure it out" — refuse. The plan is the state store a stateless tick reads.
- "Run on master/main / in the main checkout" — refuse. Use a worktree on a topic branch.
- "Add the launch command to my shell rc / start it for me" — refuse. Print the command; the user runs it.

## Common rationalisations to refuse

| Thought | Reality |
|---------|---------|
| "User wants it fast — combine wizard questions into one prompt" | One question per AskUserQuestion call. Bundled questions get ignored. |
| "I'll just run `run.sh` myself after setup" | No. Print the command; the user launches the loop. |
| "Run in the main checkout, a worktree is overkill" | Worktree isolation is a guardrail. Resolve a `--worktree` or create one. |
| "Fully enumerate every task for a huge feature up front" | Large features are `segmented` — defer per-segment tasks to a PLAN tick so they stay fresh. |
| "No `timeout` binary, but it'll probably be fine" | Warn the user. Without `timeout`/`gtimeout` (install `coreutils`) the per-tick cap is off and a stuck tick runs unbounded. |
| "Skip permission-advisor — skip-permissions makes it moot" | Run it anyway if available; it's advisory and cheap, and surfaces hook-blockable gaps. |
| "The environment already has its deps" | Don't assume — check (Step 4.3). Deps/build artefacts are per-checkout and gitignored; a worktree, fresh clone, or stale checkout may lack them, and the first tick will reach elsewhere to verify. |
| "Skip the install — the first tick can set up its own env" | A headless tick can't ask for help; it improvises (e.g. reaches into the main checkout). Prepare + prove the environment now, while a human can fix breakage. |
