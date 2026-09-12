# agent-loop v3 — phase runner: the harness owns the state machine, models own judgement

**Date:** 2026-09-13
**Status:** proposed, awaiting Mitch's review. Every decision in §11 was taken without him and is overridable.
**Scope:** `plugins/agent-loop` only. Host memory hygiene stays documented, not engineered.
**Evidence base:** the 2026-09-11 `internal-app` run, archived at `~/Downloads/EXTRACT` and indexed under `EXTRACT/07-index/` (query with `07-index/q`). The analysis reports are in `EXTRACT/08-analysis/`; `00-SYNTHESIS.md` is the summary. Pointers below use `w<N>` for those reports.

---

## 1. What the run proved

88 ticks, 30 h, ≈$360, 64/73 planned tasks done, and an app that did not look like Rise until a human opened a browser. Five defects explain nearly all of it (`w4`, `w5`, `w7`, `w8`):

1. **Nothing rendered the product.** Every gate was lint/tsc/build/vitest(jsdom)/grep. No tick opened a browser before tick 89. The "VISUAL"/"FIDELITY" gates were code-presence greps. The human found the broken shell 3 h and 5 ticks after it landed.
2. **Contracts tested the annotation, not the artefact.** The explicit "copy Rise layouts" task passed because its verification grepped for a `TODO(copied from …)` comment and the "copies" were that six-line comment. Both Evaluators (one on opus) read zero reference files. The false "genuinely copied" claim then entered `LOOP_LEARNINGS.md`.
3. **The tick, not the task, is the unit of failure.** A full Scout→Worker→gate→Evaluator→commit tick takes 85–94% of the 1800 s wall on the happy path; one extra iteration (a NEEDS_WORK re-dispatch, a self-contradictory contract, a task dispatched behind a documented blocker) is always fatal. Shell time was 6–17% of each tick. Nothing was lost on retry; tick 88's timeout cost one `git commit`. The stall detector's seven warnings were a truncate/read race reporting tick age.
4. **The loop deferred decisions it could make.** All three `LOOP_CLEANUP.md` items were decidable from disk: a Scout-invented criterion; a question the plan already answered; a `forbidden` file the Scout wrote itself, which three same-tier Workers then hit before the orchestrator halted "for a human".
5. **The orchestrator spine is a state machine written as a 300-line prompt.** 31% of spend, 2.4 h of plan re-reading, same-model retry 6 of 7 times against its own rule, 10 direct source edits that skipped the Evaluator, tier directives enforced only as prompt text.

The bash harness underneath forks 2–4 `jq` per stream line, appends an unbounded `run.log` (105 MB), and checks `PAUSE` only between 30-minute ticks. Upstream Ralph has nothing to borrow structurally (`w2`); the borrowables are the browser gate and the "cheap evaluator decides stop/continue" pattern.

---

## 2. Goals and non-goals

**Goals**
- G1 The product is seen: a render gate and screenshot-backed evaluation for any task that touches UI, and a diff-similarity check for any copy/port task.
- G2 Timeouts stop being fatal: per-phase budgets, a resumable Worker with a wrap-up step, commit as a separate cheap step, and automatic splitting only after resume has failed.
- G3 The loop decides what it can decide: a Judge phase before any halt, constraint provenance, semantic blocker fan-out, and a configurable decision policy.
- G4 Tiers are enforced in code, and strong models sit where leverage is: Planner, Scout (contract), Judge, Evaluator on complex tasks. No LLM spine.
- G5 Fewer human touches: PAUSE/STOP at phase boundaries, self-resume, a medic that reads the phase timeline, usage recorded even when a phase is killed.
- G6 One Python process per harness: no per-line forks, bounded logs, atomic runtime files.
- G7 The dashboard, the four skills, the plan grammar, the runtime-file contract, and the exit codes keep working (`w10`).

**Non-goals**
- Rewriting `web/serve.py` or `dashboard.html` beyond a STOP button and an artifacts panel.
- Agent Teams, shared message buses, or nested orchestration.
- Any new dependency beyond python3 stdlib and git.

---

## 3. Architecture

```
run.sh (shim) ──exec──▶ runner/run.py ─── one process per loop ───────────────────────────┐
                        │  lock + heartbeat + events + PAUSE/STOP + dashboard sidecar     │
                        │                                                                 │
                        │  per task attempt ("tick"):                                     │
                        │   SELECT ─▶ SCOUT ─▶ VALIDATE ─▶ WORK ─▶ SANDBOX ─▶ GATE ─▶      │
                        │   RENDER ─▶ FIDELITY ─▶ EVALUATE ─▶ COMMIT ─▶ LEARN              │
                        │        any failure ──▶ JUDGE ──▶ retry | escalate | widen |      │
                        │                                  resume | split | defer | halt   │
                        │                                                                 │
                        │  LLM phases = `claude -p` subprocesses, one per phase:          │
                        │   scout · worker · evaluator · judge · planner · reviewer ·     │
                        │   learner · (medic unchanged)                                   │
                        └─────────────────────────────────────────────────────────────────┘
```

The harness is the only thing that reads the plan, picks tasks, edits checkboxes, runs commands, enforces `allow_list`, and commits. Models do exactly one thing each, from a small role prompt plus injected context, and return a JSON document the harness validates. There is no orchestrator model.

### 3.1 Why one process per phase rather than Agent-tool subagents
- `--model` per phase is a command-line fact, not a prompt request (G4).
- Each phase has its own timeout, `--max-turns`, `--max-budget-usd`, transcript, and usage record, flushed by the harness even on kill (G2, G5).
- A killed Worker is `claude -p --resume <session-id>`-able; the session id is captured from the stream-json `init` message (`w9` §1).
- PAUSE/STOP act between phases, i.e. within minutes (G5).
- No plan is ever re-primed into a subagent through a spine; each phase gets only its contract (`w7` §2: 633 M cache-read tokens).

---

## 4. The runner (`plugins/agent-loop/runner/`)

Python 3.9 stdlib. Modules and their single responsibilities:

| module | does | depends on |
|---|---|---|
| `run.py` | argv/env, lock, heartbeat thread, main loop, exit codes | everything below |
| `config.py` | parses `LOOP_CONFIG.md` (same keys as today plus §4.6) | — |
| `plan.py` | parses/edits `LOOP_PLAN.md` with the exact grammar serve.py uses (`w10` §3); dependency eligibility; blocker fan-out; segment detection | — |
| `contract.py` | loads/validates `runtime/sprint-<T>.json` (§5) | plan |
| `claude_proc.py` | launches `claude -p`, parses stream-json in-process, stamps activity atomically, tracks outstanding tool calls, captures `session_id`, accumulates usage per message id, enforces timeout/wrap-up, supports `--resume` | — |
| `phases.py` | one function per phase; each returns a typed result | claude_proc, contract, gate |
| `gate.py` | runs verification / render / fidelity commands with per-command timeouts, captures output to `runtime/gate-<T>-<n>.txt` | — |
| `git_ops.py` | status vs allow_list, revert strays, add allow_list, commit with trailers, similarity diff | — |
| `events.py` | `emit(type, **fields)` with the existing envelope and `seq` (`w10` §2); `LOOP_USAGE.jsonl` writer | — |
| `judge.py` | assembles Judge input (§7), applies its decision within policy | phases, plan, contract |
| `incidents.py` | incident/medic files and dispatch (unchanged shapes) | events |
| `migrate.py` | schema 2→3 | — |
| `sidecar.py` | dashboard adoption/spawn/supervision (port of `run.sh:186-249`) | — |

`run.sh` stays as a shim: `exec python3 -m runner.run --shim "$PLUGIN_ROOT/run.sh" "$@"`. The `--shim <path>` argument exists only so the string `run.sh` stays in the harness's argv after `exec`, because the dashboard and all four skills judge liveness by `harness.json.pid` plus `ps -o command= -p PID` containing `run.sh` (`w10` §4). The dashboard's `Popen(["bash", run_sh])` is unchanged. `lib/*.sh` and `tick-prompt.md` are deleted.

### 4.1 Tick lifecycle
A tick is one attempt cycle for one task. `tick_start`/`tick_end` events keep their shape; `tick_end.by_model` is the sum of the tick's phase usage and is always written, including after a kill (fixes the ~$61 unattributed spend, `w7` §7.1). `role_start`/`role_end`/`tool` events are emitted per LLM phase from the stream parser so the dashboard's current-tick pipeline view is unchanged.

Modes stay `plan | review | execute`, selected by the harness with the same rules as tick-prompt §3 (segment complete and unreviewed → review; next segment unplanned and nothing eligible → plan; else execute).

### 4.2 Phase budgets
Each phase has a wall-clock timeout, a `--max-turns`, and a `--max-budget-usd`, from `Limits:` (§4.6) with defaults:

| phase | model tier (default) | timeout | notes |
|---|---|---|---|
| scout | standard | 8 min | writes the contract; `w5` §2, `w4` tick 85 show haiku here is false economy |
| worker | standard; attempt 2 = next tier up (enforced) | 25 min + 5 min wrap-up | resumable (§6) |
| gate / render / fidelity | — | per-command 10 min | harness runs these, not a model |
| evaluator | standard; `\| complex` → most-capable | 8 min | must-read/must-view inputs pre-loaded (§5) |
| judge | most-capable | 6 min | only on failure paths (§7) |
| planner / reviewer | most-capable | 15 min | unchanged roles |
| learner | cheap | 3 min | writes learnings with evidence (§9) |
| commit | — | — | harness, seconds |

No phase can exceed its own budget; the tick has no single wall. A tick that has run every phase once takes about what it takes today minus the spine's 2.4 h / 84 ticks of lead-in.

### 4.3 Activity and stall
The stream parser stamps `runtime/last-activity` atomically (write temp, `os.replace`) on every stream event and never falls back to tick start. It also tracks outstanding `tool_use` ids; a phase is *stalled* only when no stream event has arrived for `stall_s` **and** no tool call is outstanding. A long `pnpm turbo test` is "in tool", not stalled (`w3` §2, `w4` cross-cutting a).

### 4.4 PAUSE and STOP
- `runtime/PAUSE`: finish the current phase, write `CHECKPOINT.json`, emit `paused`, exit 0. Checked between phases, so it lands within minutes.
- `runtime/STOP`: SIGTERM the current phase now (a Worker gets its wrap-up step first, §6), then behave as PAUSE.
- The dashboard gains a "Stop now" button that writes `STOP`; `Pause` keeps writing `PAUSE`. `derive_status` shows `pausing` for both (`w10` §2 step 5).

### 4.5 Logs and memory
- No `run.log` tee. Each phase's transcript already lives in `~/.claude/projects/…/<session-id>.jsonl`; `phase_end` events record the session id and path. `harness.log` holds the harness's own lines, rotated at 10 MB × 3. The dashboard's 12-line tail reads `harness.log`.
- Runtime files are written atomically. `events.jsonl` is appended by one writer.
- No subprocess is forked per line or per event.

### 4.6 Config additions (`LOOP_CONFIG.md`)
Existing keys are read as today. New or changed:

```
Limits: tick_timeout=1800 scout=480 worker=1500 wrapup=300 evaluator=480 judge=360 planner=900 gate_cmd=600 worker_resume_max=1 worker_budget_usd=6
Tiers: cheap=haiku standard=sonnet most-capable=opus
Decision policy: autonomous | conservative
Render: <command recipe, see §5.3>   # optional; absent = no render gate
Orchestrator model:   # ignored by v3, kept so old configs parse
```
`tick_timeout` remains the outer sanity cap per tick and is the value the dashboard displays. `Tiers:` is the only place model aliases appear, so `most-capable=fable` is a one-word change.

---

## 5. Contracts

`runtime/sprint-<T>.json` is the single document a Worker and an Evaluator see. The Scout writes it; the harness validates it before dispatch and refuses invalid ones (Scout is re-dispatched once with the validation errors).

```json
{
  "task": "T60",
  "success_criteria": ["…"],
  "allow_list": ["packages/api/src/requests/activepipe/internal/organisationalUnits.ts", "…"],
  "forbidden": [{"path": "apps/frontend/**", "source": "plan:Appendix D"},
                {"path": "packages/api/src/requests/activepipe/index.ts", "source": "scout"}],
  "verification": ["pnpm turbo run lint check test --filter=@repo/internal"],
  "render_gate": {"commands": ["pnpm --filter @repo/integration cypress run --spec cypress/e2e/internal/customers/orgunits.cy.ts"],
                  "screenshots": [{"name": "orgunits-list", "path": "apps/integration/cypress/screenshots/orgunits.png"}]},
  "fidelity_source": [{"from": "apps/frontend/src/layouts/Header/Header.tsx", "to": "apps/internal/src/layouts/Header/Header.tsx", "min_similarity": 0.6}],
  "evaluator_must_read": ["apps/frontend/src/layouts/Sidebar/AppNavigation/components/AppNavigationItem.tsx"],
  "evaluator_must_view": ["orgunits-list"],
  "estimated_diff_lines": 300,
  "scout_notes": "…",
  "relevant_learnings": ["…"]
}
```

### 5.1 Validation (harness, `contract.py`)
- Every path named in `success_criteria` or `verification` that looks like a repo path must be inside `allow_list` or be read-only context; a criterion that names a path outside `allow_list` is an error (`w4` tick 85).
- `forbidden` entries carry `source`; `scout`-sourced entries are advisory to the Worker and **can be widened by the Judge** (§7). `plan`/`spec`-sourced entries cannot.
- A task whose `LOOP_CLEANUP.md` entry, or whose plan row `blocked_by:` tag, names it as blocked is not eligible in SELECT (`w4` tick 88).
- `render_gate` is required when the task's `allow_list` touches paths matching the repo's UI globs (`Render:` recipe, §5.3) unless the Planner tagged the task `| no-ui`.
- `fidelity_source` is required when the plan row's verb is `copy`/`port`/`replicate` or carries `| copy_of:`.

### 5.2 Guaranteed reads and views
The harness pre-loads `evaluator_must_read` file contents (capped) into the Evaluator prompt and instructs it to `Read` each `evaluator_must_view` screenshot; the verdict JSON must contain one observation per must-view item or the harness rejects it and re-asks once. This replaces policing tool calls (`w5` §4, §7 cause 3).

### 5.3 Render gate
The setup skill asks once per repo for a **render recipe**: how to start the app if needed, the command pattern that renders a route and writes a screenshot, and the UI path globs. The recipe is stored under `Render:` in `LOOP_CONFIG.md`. The Scout instantiates it per task into `render_gate`. The harness runs it after GATE, copies screenshots into `$LOOP_DIR/artifacts/<T>/`, attaches them to the tick (`artifact` event, ignored by today's dashboard), and fails the tick if a command fails. Reference screenshots (e.g. the Rise page a copy is meant to match) can be listed in the recipe and are handed to the Evaluator alongside the new ones.

### 5.4 Fidelity check
For each `fidelity_source` pair the harness computes a normalized line-similarity ratio (`difflib`, whitespace/import-order normalized) and fails the tick below `min_similarity` with the ratio in the gate output. A copy task that produces a six-line diff hard-fails (`w5` §5).

---

## 6. Worker resume and split (G2)

1. Worker runs with its timeout. At timeout the harness sends SIGTERM, then runs a **wrap-up** continuation: `claude -p --resume <session> --max-turns 8` with "You are out of time. Update `worker-result.json` with exact state; do not start new work." The checkpoint finally has a consumer (`w3` smell 2).
2. If the task is not Worker-complete, the next attempt is a **resume**: `claude -p --resume <session>` with "continue from your checkpoint; N minutes left", up to `worker_resume_max` times.
3. If still not complete, the Judge decides between **escalate** (next tier, fresh session with the checkpoint injected) and **split**: the Judge's decision carries the ordered sub-task rows (`changes.sub_rows`, validated against the plan grammar); the harness inserts them, marks the parent `[-] split→T60a,T60b`, commits, and the loop continues. No separate Planner phase is needed: the Judge already runs at the most-capable tier with the full failure dossier. This is the "automatically split tasks" capability, applied only after resume has failed, because the evidence says most overruns are one extra iteration, not oversized work (`w4`).
4. A killed phase never leaves the tree ambiguous: SANDBOX runs after every Worker phase (killed or not), reverting anything outside `allow_list`; changes inside it are kept for the resume.

---

## 7. Judge phase and decision policy (G3)

The harness dispatches a Judge (most-capable tier) whenever it would otherwise mark `[!]`, halt, raise needs-human, or re-dispatch a second time with the same failure signature. Input, assembled by `judge.py`: task row, contract, gate output tail, Evaluator verdict, Worker checkpoint, `LOOP_CLEANUP.md`, the plan/spec lines that mention the task's ids and any parity ids in its row, constraint provenance (§5.1), and the attempt history for the task.

Output JSON: `{"decision": "retry|escalate|widen|resume|split|defer|halt", "rationale": "…", "changes": {"allow_list_add": [...], "forbidden_remove": [...], "default_choice": "…", "blocks": ["T61","T62"]}}`.

Policy (`Decision policy:`):
- **autonomous** (default): the Judge may widen Scout-sourced constraints, escalate tiers, split, and choose a default for a question the spec is silent on. Every such choice is appended to `LOOP_DECISIONS.md` with the alternatives, so a human can reverse it later. It still defers when the spec forbids the change, when the change touches a `plan`-sourced forbidden path, or when two Judge decisions on the same task have already failed.
- **conservative**: widen/escalate/split allowed; spec-silent defaults are deferred as today.

Deferral writes `LOOP_CLEANUP.md` as today, but the entry must include the Judge's classification (`self-imposed | spec-answered | capability | open`) and, for `open`, the proposed default. The T5, P-SHELL-002 and T60 items would each have been resolved without a human under either policy (`w8` Part A).

Blocker fan-out is semantic: a deferred task carries `blocks:` from the Judge, and SELECT treats those tasks as blocked-upstream in addition to `depends_on`.

---

## 8. Medic (`/agent-loop-medic`)
The skill's contract and allowlist stay. Its input gains the phase timeline: `incident-<id>.json` now includes `phases: [{phase, model, started, ended, rc, session}]` for the tick, and the skill is told to read it before diagnosing. Timeouts that the runner resolves by resume/split are events, not incidents, so most medic runs disappear; the repeat-signature rule keys on `kind + phase + task`, not on `rc=124 after 1800s`.

---

## 9. Learnings with evidence
The Learner phase runs after COMMIT with the gate outputs and the Evaluator verdict in its prompt. A learning that asserts a gate outcome must cite `gate-<T>-<n>.txt`; entries that cannot be tied to evidence go in `## Log` only, never in `## Patterns`. The 2 KB digest cap stays.

---

## 10. Dashboard and skills
- `serve.py`: add `/api/stop` (writes `STOP`), render `artifact` events as thumbnails in the current-tick panel, read `harness.log` for the tail. `derive_status` treats a `STOP` file exactly like `PAUSE` (`pausing`); the status matrix is otherwise unchanged.
- `/agent-loop` (attach): unchanged except it mentions Stop-now and `LOOP_DECISIONS.md`.
- `/agent-loop-setup`: asks for the render recipe and the tier map; writes `Decision policy:`; drops the `Orchestrator model` explanation; the plan-row grammar gains `| no-ui`, `| copy_of:`, `blocked_by:`.
- `/agent-loop-postmortem`: reads `LOOP_DECISIONS.md` and the artifacts dir.
- Prompt-contract tests move from `tick-prompt.md` to `runner/prompts/*.md`.

---

## 11. Decisions made without Mitch (override any)
1. Python runner replaces bash; `run.sh` stays as a shim. Reason: bash 3.2 forces FIFOs and per-line `jq` forks; the same code in Python is one process and testable.
2. Phases are `claude -p` subprocesses, not Agent-tool subagents. Reason: §3.1.
3. Scout default tier `standard`, not `cheap`. Reason: the contract is the highest-leverage document in the tick and haiku wrote the three defective ones.
4. Worker escalation to the next tier on the second attempt is enforced by the harness, not requested of a model.
5. `Decision policy: autonomous` by default, with `LOOP_DECISIONS.md` as the audit trail.
6. Render gate is mandatory for UI-touching tasks once a recipe exists; absence of a recipe is a setup-time warning, not a refusal.
7. `run.log` goes away; per-phase transcripts are referenced by path. The 12-line dashboard tail reads `harness.log`.
8. Tick-level `tick_timeout` stays as an outer cap and for dashboard display; the real budgets are per phase.
9. `LOOP_SCHEMA` becomes 3 (new files: `artifacts/`, `LOOP_DECISIONS.md`, `harness.log`; new contract fields). Migration 2→3 creates them and leaves everything else untouched; a v2 plan keeps working.
10. Version 3.0.0.
11. Model aliases live only in `Tiers:`; nothing in the plugin names a model.

---

## 12. Testing
- `tests/runner/*.py` (unittest, stdlib): plan grammar round-trip against serve.py's regexes; contract validation cases (criteria path outside allow_list, forbidden provenance, blocked-by); stream parser (activity, outstanding tools, usage per message id, session id capture); budgets and wrap-up; Judge decision application under both policies; fidelity ratio; SANDBOX revert; commit trailers.
- `tests/run.e2e.test.sh` rewritten against a stub `claude` that can be scripted per phase (timeouts, malformed JSON, `--resume`), covering lock conflict, takeover, PAUSE, STOP, needs-human, sidecar adopt, migration, exit codes 0/1/2/3.
- `tests/web.contract.sh` gains `/api/stop`.
- Prompt-contract tests for each `runner/prompts/*.md` (sentinel-free: phases return JSON, the harness decides).
- `bash scripts/test-all.sh` green is the definition of done.

---

## 13. Rollout
1. Land v3 on branch `agent-loop-v3`, suite green, no loop running on this machine.
2. On the loop machine: pull, `PAUSE` the running 2.1 harness at a tick boundary, let `run.sh` migrate (schema 3), resume. The dashboard needs no restart.
3. First real run: `Decision policy: conservative` for one segment, then flip to `autonomous` once `LOOP_DECISIONS.md` looks sane.
4. Postmortem compares $/task, wall/task, human touches, and needs-human count against the 2026-09-11 baseline (`EXTRACT/08-analysis/cost-effort.md`).
