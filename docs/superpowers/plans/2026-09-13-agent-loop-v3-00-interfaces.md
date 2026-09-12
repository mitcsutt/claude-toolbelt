# agent-loop v3 — shared file structure and interfaces

This document is the contract between the four v3 implementation plans. Every plan uses these names verbatim. Spec: `docs/superpowers/specs/2026-09-13-agent-loop-v3-phase-runner-design.md`.

Plans:
- `2026-09-13-agent-loop-v3-A-runner-core.md` — runner package, phases scout/worker/evaluator/learner, gate, git, events, PAUSE/STOP, migration, shim, e2e.
- `2026-09-13-agent-loop-v3-B-verification.md` — render gate, fidelity check, evaluator must-read/must-view, setup recipe.
- `2026-09-13-agent-loop-v3-C-judgement.md` — Judge phase, decision policy, worker wrap-up/resume/split, semantic blockers, medic phase timeline.
- `2026-09-13-agent-loop-v3-D-surface.md` — dashboard STOP + artifacts, skills, templates, README, version/schema notes, prompt-contract tests.

Order: A first (everything else imports it). B and C are independent of each other and both depend on A. D last.

## Global constraints (copy into every plan)
- Python 3.9 stdlib only; no third-party packages. macOS `/usr/bin/python3` is 3.9.
- bash 3.2 for any `.sh` (no `mapfile`, no `declare -A`, no `${var,,}`; `"${arr[@]}"` on an empty array is fatal under `set -u`).
- `run.sh` remains executable (755) and is the only bash entry point; it `exec`s python.
- Every runtime file is written atomically: write `<path>.tmp` then `os.replace`.
- No model name anywhere in the plugin except the `Tiers:` config line and the template default `Tiers: cheap=haiku standard=sonnet most-capable=opus`.
- `LOOP_SCHEMA = 3`. Plugin version `3.0.0`.
- Exit codes: 0 done/paused/rate-limit-exit, 1 halt/error, 2 needs-human, 3 lock-conflict.
- Events envelope: one compact JSON object per line, fields `t` (epoch int), `seq` (int, monotonic per harness run), `type`, then payload. Lifecycle types serve.py understands must keep their fields (`w10` §2).
- `LOOP_PLAN.md` task regex (from serve.py): `^\s*- \[(.)\] (.*)$`, glyphs ` ~ x ! -`, segment = line starting `## `, id = first `\bT\d+\b`.
- `bash scripts/test-all.sh` must pass at the end of every plan.

## Directory layout (final)
```
plugins/agent-loop/
  run.sh                       # shim: exec python3 -m runner.run --shim "$PLUGIN_ROOT/run.sh" "$@"  (keeps "run.sh" in argv for the ps-based liveness checks)
  runner/
    __init__.py
    run.py                     # main loop, lock, heartbeat, exit codes
    config.py                  # LoopConfig
    plan.py                    # Plan, Task
    contract.py                # Contract, validate
    claude_proc.py             # ClaudeProcess, PhaseResult
    phases.py                  # scout/worker/evaluator/learner/planner/reviewer
    gate.py                    # run_commands, CommandResult
    git_ops.py                 # status/revert/commit/similarity
    events.py                  # EventLog, UsageLog
    judge.py                   # Judge input/decision (plan C)
    render.py                  # render gate + fidelity (plan B)
    incidents.py               # incident/medic files + dispatch
    migrate.py                 # schema 2→3
    sidecar.py                 # dashboard adopt/spawn/supervise
    prompts/
      scout.md worker.md worker_wrapup.md worker_resume.md evaluator.md
      judge.md planner.md reviewer.md learner.md
  templates/  (LOOP_CONFIG.md updated; LOOP_PLAN.md legend updated)
  skills/     (unchanged names)
  web/        (serve.py + dashboard.html, plan D)
  tests/
    runner/test_*.py           # python unittest, run by tests/all.sh via `python3 -m unittest discover -s tests/runner`
    fixtures/claude            # scripted stub (rewritten)
    run.e2e.test.sh            # rewritten
    (existing *.contract.sh, web.contract.sh, serve.test.py stay; lib/harness/events/migrate .test.sh are deleted with lib/)
```
`lib/*.sh` and `tick-prompt.md` are deleted in plan A. `tests/tick-prompt.contract.sh` is replaced by `tests/prompts.contract.sh` in plan D.

## Data shapes

### LoopConfig (`config.py`)
```python
@dataclass
class LoopConfig:
    worktree: str                # absolute; harness refuses to run elsewhere
    branch: str
    goal: str
    granularity: str             # single|segmented
    tdd_mode: str
    verification: list[str]      # tokens, e.g. ["lint","tsc","build","test"]
    blocker_policy: str          # continue-independent|halt
    dashboard: str               # auto|off
    medic: str                   # auto|notify|off
    medic_model: str             # alias or ""
    tiers: dict[str,str]         # {"cheap":"haiku","standard":"sonnet","most-capable":"opus"}
    role_tiers: dict[str,str]    # {"planner":"most-capable","scout":"standard","worker":"standard","evaluator":""}
    limits: dict[str,int]        # tick_timeout, scout_timeout, worker_timeout, wrapup_timeout, eval_timeout, judge_timeout, planner_timeout, gate_cmd_timeout, worker_resume_max, worker_budget_usd, max_attempts
    decision_policy: str         # autonomous|conservative
    render: dict                 # parsed from `Render:` block, {} when absent (see plan B)
    spec_path: str; plan_path: str
def load_config(path: str) -> LoopConfig
def model_for(cfg: LoopConfig, tier: str) -> str          # tiers lookup; raises on unknown tier
def phase_limit(cfg: LoopConfig, phase: str) -> int        # seconds, with defaults from spec §4.2
```
Defaults when `Limits:` omits a key: tick_timeout=1800, scout_timeout=480, worker_timeout=1500, wrapup_timeout=300, eval_timeout=480, judge_timeout=360, planner_timeout=900, gate_cmd_timeout=600, worker_resume_max=1, worker_budget_usd=6, max_attempts=3. `phase_limit(cfg, "worker")` reads `worker_timeout`.

### Plan / Task (`plan.py`)
```python
@dataclass
class Task:
    id: str; segment: str; title: str; state: str  # state in {"pending","doing","done","blocked","skipped","blocked-upstream"}
    sha: str | None; class_flag: str | None        # "mechanical"|"complex"|None
    depends_on: list[str]; clone_of: str | None; copy_of: str | None
    blocked_by: list[str]; no_ui: bool; model: str | None
    line_no: int; raw: str
@dataclass
class Segment: name: str; line_no: int; reviewed_sha: str | None; tasks: list[Task]
class Plan:
    @classmethod
    def load(cls, path) -> "Plan"
    def segments(self) -> list[Segment]
    def tasks(self) -> list[Task]
    def eligible(self) -> list[Task]          # pending, all depends_on done/skipped, not in any other task's blocked_by fan-out, not blocked-upstream
    def mode(self) -> str                     # "review"|"plan"|"execute"|"done"|"stuck"  (spec §4.1 rules)
    def set_state(self, task_id, state, sha=None) -> None   # in-place glyph edit, appends ` done(+sha)` once
    def append_tasks(self, segment_name, rows: list[str]) -> None
    def stamp_reviewed(self, segment_name, sha) -> None
    def split(self, task_id, sub_rows: list[str]) -> list[str]  # marks parent `[-] … split→T60a,T60b`, inserts sub rows after it, returns new ids
    def save(self) -> None                    # atomic
```
Plan-row grammar additions (also in `templates/LOOP_PLAN.md`): `| no-ui`, `| copy_of: T<n>`, `| blocked_by: T<n>,T<m>`, `| split_of: T<n>`. **Sub-task ids are the next free numeric ids** (`T74`, `T75`), never `T60a`: serve.py's id regex `\bT\d+\b` does not match `T60a`, so lettered ids would vanish from the dashboard. `Plan.split` allocates `max(existing)+1…` and appends `| split_of: T60` to each sub row; the parent is marked `[-] … split→T74,T75`.

### Contract (`contract.py`)
```python
@dataclass
class Forbidden: path: str; source: str        # source in {"plan","spec","scout"}
@dataclass
class RenderGate: commands: list[str]; screenshots: list[dict]   # {"name","path"}
@dataclass
class FidelitySource: src: str; dst: str; min_similarity: float
@dataclass
class Contract:
    task: str; success_criteria: list[str]; allow_list: list[str]; forbidden: list[Forbidden]
    verification: list[str]; render_gate: RenderGate | None; fidelity_source: list[FidelitySource]
    evaluator_must_read: list[str]; evaluator_must_view: list[str]
    estimated_diff_lines: int; scout_notes: str; relevant_learnings: list[str]
def load_contract(path) -> Contract
def save_contract(c: Contract, path) -> None
def validate(c: Contract, task: Task, cfg: LoopConfig, cleanup_text: str, ui_globs: list[str]) -> list[str]   # [] = valid; messages are human-readable
```
Validation rules (spec §5.1): repo-path tokens in `success_criteria`/`verification` must be in `allow_list` (glob) or be marked read-only with a `(read)` suffix; every `forbidden` has a `source`; task named as blocked in `cleanup_text` → error `blocked-by-cleanup`; `render_gate` required when any allow_list path matches `ui_globs` unless `task.no_ui`; `fidelity_source` required when `task.copy_of` or the row verb ∈ {copy, port, replicate}.
Path tokens: any whitespace-delimited token containing `/` and matching `^[\w.@#-]+(/[\w.@#\[\]{}*-]+)+$`.

### ClaudeProcess (`claude_proc.py`)
```python
@dataclass
class Usage: cost_usd: float; input_tokens: int; output_tokens: int; cache_read_tokens: int; cache_creation_tokens: int
@dataclass
class PhaseResult:
    phase: str; model: str; rc: int | None; killed: bool; timed_out: bool
    session_id: str | None; transcript_path: str | None
    started: int; ended: int
    result_text: str            # the `result` message text, or "" when killed
    usage_by_model: dict[str, Usage]
    tool_calls: int; last_activity: int
def run_phase(*, phase: str, model: str, prompt: str, cwd: str, timeout_s: int, max_turns: int, max_budget_usd: float | None,
              env: dict, events: "EventLog", tick: int, role: str, activity_path: str, resume_session: str | None = None,
              allowed_tools: list[str] | None = None) -> PhaseResult
```
Behaviour: spawns `claude -p --output-format stream-json --verbose --model <model> [--resume <sid>] [--max-turns N] [--max-budget-usd X] --dangerously-skip-permissions`; prompt on stdin; parses each stream line in-process; on `system/init` captures `session_id`; on `assistant` messages accumulates usage per `message.id` (keep max per id); emits `tool` events (`role`, `name`, `count`, `desc`) and stamps `activity_path` atomically; tracks outstanding `tool_use` ids vs `tool_result`; `stalled()` is true only when no event for `stall_s` and no outstanding tool; on timeout sends SIGTERM, waits 30 s, SIGKILL; always returns a PhaseResult with whatever usage was seen. Emits `role_start` (`role`, `model`, `desc`) before and `role_end` (`role`) after, plus `phase_end` (`phase`, `rc`, `dur`, `session_id`, `transcript`).
Transcript path: `~/.claude/projects/<encoded cwd>/<session_id>.jsonl` where encoded cwd replaces `/` with `-` (verify against an existing dir at runtime; store `None` if not found).

### Phase JSON outputs (`phases.py`)
Every LLM phase is asked to end its reply with one fenced block ` ```json … ``` `; `phases.parse_json_block(text) -> dict` extracts the last such block. Malformed → the phase is re-asked once with the parse error appended; still malformed → treated as failure with reason `malformed-output`.

- **scout** → writes `runtime/sprint-<T>.json` itself (Write tool allowed) AND returns `{"contract_path": "...", "notes": "..."}`.
- **worker** → edits allow_list files; writes `runtime/worker-result.json` `{"task","status":"complete|partial","files_touched":[],"summary","checkpoint":"…","next_steps":[]}` first thing and updates it; returns `{"status":"complete|partial","summary":"…"}`.
- **evaluator** → `{"verdict":"PASS|NEEDS_WORK|BLOCKER","findings":[{"criterion","met":bool,"evidence"}],"views":[{"name","observation"}],"summary"}`.
- **learner** → `{"patterns":[{"rule","evidence"}],"log":"…","invariants":[{"rule","check"}]}`; the harness rewrites `LOOP_LEARNINGS.md` (digest ≤ 2 KB, evidence required for `patterns`).
- **planner** → writes plan rows into `LOOP_PLAN.md` under the segment heading (Edit allowed) and returns `{"tasks_added": N}`; the harness re-parses and validates the rows.
- **reviewer** → `{"findings":[{"severity":"nit|should-fix|must-fix","title","detail","follow_up_row":"- [ ] T…"}]}`; the harness appends follow-up rows and stamps `Reviewed:`.
- **judge** (plan C) → `{"decision":"retry|escalate|widen|resume|split|defer|halt","classification":"self-imposed|spec-answered|capability|open","rationale","changes":{"allow_list_add":[],"forbidden_remove":[],"default_choice":"","blocks":[],"sub_rows":[]}}`.

Phase functions (all take `ctx: TickContext` defined in `phases.py`: cfg, plan, task, loop_dir, runtime_dir, events, tick, attempt):
```python
def run_scout(ctx) -> tuple[PhaseResult, Contract | None, list[str]]     # result, contract, validation errors
def run_worker(ctx, contract, resume_session=None, wrapup=False) -> tuple[PhaseResult, dict]
def run_evaluator(ctx, contract, diff_text, gate_outputs, screenshots) -> tuple[PhaseResult, dict]
def run_learner(ctx, contract, gate_outputs, verdict) -> PhaseResult
def run_planner(ctx, segment) -> PhaseResult
def run_reviewer(ctx, segment, diff_text) -> tuple[PhaseResult, dict]
```

### Gate (`gate.py`)
```python
@dataclass
class CommandResult: cmd: str; rc: int; duration_s: int; output_path: str; timed_out: bool
def run_commands(cmds: list[str], cwd: str, timeout_s: int, out_dir: str, tag: str) -> list[CommandResult]   # output to <out_dir>/gate-<tag>-<n>.txt
def all_ok(results) -> bool
```

### Git (`git_ops.py`)
```python
def changed_paths(cwd) -> list[str]                        # porcelain, untracked included
def strays(changed: list[str], allow_list: list[str]) -> list[str]   # glob match
def revert(cwd, paths) -> None                             # checkout -- for tracked, rm for untracked
def head_sha(cwd) -> str
def commit(cwd, paths, subject, trailers: dict) -> str     # returns sha; trailers rendered `Key: value`
def diff_text(cwd, base_sha, paths) -> str
def similarity(src_path, dst_path) -> float                # difflib ratio over normalized lines (strip, drop blank, sort import lines)
```

### Events (`events.py`)
```python
class EventLog:
    def __init__(self, path: str, seq_path: str)
    def emit(self, type: str, **fields) -> None            # appends {"t","seq","type",...}
class UsageLog:
    def __init__(self, path: str)
    def write_tick(self, tick: int, mode: str, duration_s: int, by_model: dict[str, Usage]) -> None
```
New event types (ignored by today's dashboard): `phase_end`, `artifact` (`tick`, `task`, `name`, `path`), `decision` (`task`, `decision`, `classification`), `resume` (`task`, `attempt`), `split` (`task`, `into`).

### Runtime files written by the harness
`harness.json` `{pid,start_epoch,host,loop_dir,plugin_version}`; `HEARTBEAT`; `tick.json` `{tick,pid,started_at,timeout_s}` (pid = current phase pid or harness pid); `last-activity`; `PAUSE`/`STOP` sentinels (read; deleted on resume); `CHECKPOINT.json` `{t,stopped_after_tick,next_task,segment,note}`; `schema`; `tickseq`, `incidentseq`; `incident-<id>.json` `{id,kind,severity,detail,tick,t,phases:[{phase,model,started,ended,rc,session}]}`; `medic-<id>.json` (unchanged, written by the skill); `NEEDS_HUMAN.md`; `gate-<T>-<n>.txt`; `sprint-<T>.json`; `worker-result.json`; `task-<T>.json` (per-task state, spec §14; replaces the earlier `attempts-<T>.json`).
Durable: `LOOP_DECISIONS.md` (plan C), `artifacts/<T>/<name>.png` (plan B), `harness.log` (rotating 10 MB × 3).

### Main loop (`run.py`) — per tick
```
mode = plan.mode()
review → run_reviewer → append follow-ups, stamp Reviewed, commit "loop: review segment X" (Loop-Status: reviewed) → continue
plan   → run_planner → validate rows → commit "loop: plan segment X" → continue
execute:
  task = plan.eligible()[0]; plan.set_state(task,"doing"); commit "loop: start T"
  scout → validate → (invalid: re-scout once → still invalid: judge)
  worker (attempt n, model by tier + escalation) → sandbox revert strays
  gate → render (plan B) → fidelity (plan B) → evaluator (skipped if mechanical)
  PASS & gates ok → commit with trailers Loop-Status: done, Loop-Verification, Loop-Files → set_state done(+sha) → task_status event → learner
  otherwise → judge (plan C; in plan A the stand-in is: NEEDS_WORK → escalate once; else defer to LOOP_CLEANUP and mark [!])
between phases: check STOP then PAUSE; heartbeat thread runs throughout
tick_end always emitted with verdict in {continue,done,halt,retry} and by_model summed from phase results
```

### Sentinel-free control
Phases return JSON; the harness decides `continue|done|halt`. `<<LOOP_*>>` sentinels no longer exist. `plan.mode()=="done"` → loop_end reason `done`, exit 0, postmortem dispatched as today. `"stuck"` → loop_end `halt`, exit 1.

### Prompts (`runner/prompts/*.md`)
Each prompt is a role brief with `{{placeholders}}` filled by `phases.render_prompt(name, **vars)`; placeholders: `{{loop_dir}}`, `{{worktree}}`, `{{task_row}}`, `{{contract_json}}`, `{{must_read_blocks}}`, `{{screenshots}}`, `{{gate_outputs}}`, `{{diff}}`, `{{learnings_digest}}`, `{{knowledge}}`, `{{spec_excerpt}}`, `{{checkpoint}}`, `{{minutes_left}}`, `{{validation_errors}}`. Prompts state the JSON output shape verbatim and the non-interactivity rule (no AskUserQuestion/EnterPlanMode).

### Stub `claude` fixture (`tests/fixtures/claude`)
Bash 3.2 script. Reads `STUB_SCRIPT` env: a directory of numbered response files consumed in order (`001.jsonl`, `002.jsonl`, …), each a stream-json transcript to print line by line with `STUB_DELAY` seconds between lines; a file named `NNN.sleep` makes that invocation sleep for its content in seconds (to trigger timeouts); `NNN.exit` sets the exit code. Prints a `system/init` line with `session_id` first, then the file. Honours `--resume` by recording the sid it was given to `STUB_LOG`.

## Cross-plan notes (from the plan-writers' reports; executors read these before starting)
- **`artifacts/` must be in the per-run `.gitignore`** written by the setup skill and created by `migrate.py`; it sits inside the worktree, so plan A's SANDBOX stray-revert would otherwise delete screenshots between phases (plan B).
- New prompt placeholders beyond the list above: `{{render_recipe}}` (scout, plan B) and the judge placeholders defined in plan C's `judge.md` (nine after reconciliation); plan D's `tests/prompts.contract.sh` must allow them.
- Screenshot dicts carry an optional `"kind": "new"|"reference"` (plan B). Extra runtime files: `fidelity-<T>.json`, `render-app.pid`, `render-app.log`, `gate-render-<T>-<n>.txt` (plan B).
- Re-ask must merge `usage_by_model` from both attempts (`phases._merge_usage`, plan B) so no spend is dropped.
- Judge JSON gains `instruction`, `alternatives`, `reversal`; `Plan.set_blocked_by(task_id, ids)`; `TickContext` gains `resume_session`, `resume_count`, `last_session`; `run_phase(max_turns=0)` omits the flag; attempt helpers live in `judge.py` (plan C).
- Stub `claude` gains an optional sourced `NNN.sh` side-effect file per invocation so a scripted phase can write `sprint-<T>.json` / `worker-result.json` (plan C; plan A's fixture must include this hook).
- Split sub-rows come from the Judge's `changes.sub_rows`; there is no Planner phase for splits (spec §6.3 amended, commit e18b278).
- `/api/stop` refuses when **no** harness is alive (a STOP nobody reads only kills the next launch); the existing `btnStop` is repointed (plan D). Artifact route is keyed `(tick, name)` via an index built from `artifact` events, never a path from the query string (plan D).
- README "Upgrading" heading keeps its anchor (`CLAUDE.md` links to it); schema-3 content is a row under it (plan D).
- `templates/LOOP_CONFIG.md` is edited by both plan B (`Render:`) and plan D (`Tiers:`, `Decision policy:`, `Limits:`); run B before D or merge by hand — never in parallel.
- **Shim argv (plan A):** `exec python3 -m runner.run --shim "$PLUGIN_ROOT/run.sh" "$@"`; `-m` because `runner/` uses relative imports, `--shim` so `ps -o command=` still contains `run.sh`. Extra modules `util.py`, `harness.py`, `status.py`; `model_for` returns `""` for an unmapped tier; `config.next_tier`; `blocked-upstream` is a row marker, not a glyph; `Plan.dependents`; commits in plans use `git commit -F - <<'EOF'` (bash 3.2 cannot parse `-m "$(cat <<'EOF' …)"` bodies containing an apostrophe).
- **Adopted from the parallel session (spec §11 items 12–18, §14, §15):** `runtime/task-<T>.json` replaces `attempts-<T>.json` as the per-task state record (shape in spec §14); boot resumes at the recorded phase; a `[~]` task with no state file goes to the Judge with `failure="boot-reconcile"` (decision `resume|retry|revert-and-retry|defer`). Re-attempt tier is a Judge decision (`changes.tier`, `changes.extend_cap_s` ≤ 1× the phase default, once), not a code ladder; the harness enforces `max_attempts`, `worker_resume_max`, and the two-failed-decisions rule. `migrate.py` normalises 2.x `sprint-*.json` (`forbidden` strings → `{"path","source":"scout"}`) and adds `artifacts/` to the per-run `.gitignore`. New module `runner/calibrate.py` (prints a `Limits:` line from `events.jsonl` role spans: p90 × 1.5 rounded to 60 s) with a unit test on a fixture events file — plan A owns it. `Limits:` keys use the `<role>_timeout=` names above.
- **After reconciliation (plan C):** `config.next_tier(tier: str) -> str` (one argument; returns the next tier up, or the same tier at the top). `TaskState.begin_attempt`/`end_attempt`/`begin_phase`/`end_phase` do NOT autosave; callers pair them with `.save()`. `judge.md` has nine placeholders; `tests/prompts.contract.sh` allows them without enumerating.
- **After reconciliation (plan A):** `Limits:` also accepts `reviewer_timeout` (default 900) and `learner_timeout` (default 180); `phase_limit` maps `evaluator`→`eval_timeout`. Plan A: 22 tasks; plan C: 9 tasks. The plan A editor executed every code block as a package (409 tests green), so plan A's code is known-runnable on Python 3.9.
