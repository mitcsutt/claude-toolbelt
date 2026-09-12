# agent-loop v3 — Plan C: judgement, resume and split

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the harness a Judge phase that decides what the loop can decide, a Worker that survives its own timeout by wrapping up and resuming, and a split path — so the loop stops halting for questions its own files answer.

**Architecture:** `runner/judge.py` assembles a failure dossier from disk (contract, gate tails, verdict, checkpoint, plan/spec excerpts, constraint provenance, and the attempt history plan A keeps in `runtime/task-<T>.json`), runs one `claude -p` Judge phase at the most-capable tier, validates its decision against the configured decision policy in Python, then mutates the contract/plan/runtime files and returns one of `retry|escalate|resume|revert-and-retry|split|defer|halt` to the main loop. Worker timeouts are no longer fatal: the harness runs a bounded wrap-up continuation on the same session, and then **the Judge — not a tier ladder — reads the checkpoint** and decides whether the next attempt resumes the same session at the same tier, escalates, or splits. The same Judge reconciles a task the harness finds mid-flight at boot. Every autonomous choice lands in `LOOP_DECISIONS.md` with its alternatives and how to reverse it.

**Tech Stack:** Python 3.9 stdlib (`unittest`, `dataclasses`, `json`, `re`, `fnmatch`, `difflib`), bash 3.2 for `.sh` tests, git.

**Spec:** `docs/superpowers/specs/2026-09-13-agent-loop-v3-phase-runner-design.md` — this plan implements §6 (worker resume and split), §7 (Judge phase and decision policy), §8 (medic) and §14's boot-reconciliation branch, and consumes `Decision policy:` and the `Limits:` keys from §4.6. Spec §11 item 4 is load-bearing here: **the re-attempt tier is judged, never laddered**; the harness enforces only `max_attempts`, `worker_resume_max`, the one cap extension, and the two-failed-decisions rule.

**Interface contract:** `docs/superpowers/plans/2026-09-13-agent-loop-v3-00-interfaces.md`. Plan A must land first — it creates the `runner/` package this plan extends.

## Global Constraints

- Python 3.9 stdlib only; no third-party packages. macOS `/usr/bin/python3` is 3.9.
- **Python 3.9 has no PEP 604 unions at runtime.** Write `Optional[str]`, `Dict[str, Any]`, `List[str]` from `typing` in dataclass fields and signatures — never `str | None`. The interfaces doc's sketches use `|` as shorthand; the code does not.
- bash 3.2 for any `.sh`: no `mapfile`, no `declare -A`, no `${var,,}`; under `set -u`, expanding `"${arr[@]}"` on an empty array is fatal (`${#arr[@]}` is safe).
- Every runtime file is written atomically: write `<path>.tmp`, then `os.replace`. Durable append-only documents (`LOOP_DECISIONS.md`, `LOOP_CLEANUP.md`, `events.jsonl`) are appended with `open(..., "a")`.
- No model name anywhere in the plugin except the `Tiers:` config line. Code names **tiers** (`cheap`, `standard`, `most-capable`) and resolves them with `config.model_for`.
- `LOOP_SCHEMA = 3`. Plugin version `3.0.0`.
- Exit codes: 0 done/paused/rate-limit-exit, 1 halt/error, 2 needs-human, 3 lock-conflict.
- Events envelope: one compact JSON object per line, fields `t`, `seq`, `type`, then payload.
- `LOOP_PLAN.md` task regex: `^\s*- \[(.)\] (.*)$`, glyphs ` ~ x ! -`, segment = line starting `## `, id = first `\bT\d+\b`.
- Commit messages are `agent-loop: <what>` and end with:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
  ```
  Commit with `git commit -F - <<'EOF'` — bash 3.2 cannot parse a
  `-m "$(cat <<'EOF' …)"` body containing an apostrophe (interfaces doc,
  Cross-plan notes).
- `Limits:` keys are the `<role>_timeout=` names from spec §4.6:
  `tick_timeout`, `scout_timeout`, `worker_timeout`, `wrapup_timeout`,
  `eval_timeout`, `judge_timeout`, `planner_timeout`, `gate_cmd_timeout`,
  `worker_resume_max`, `worker_budget_usd`, `max_attempts`. Always reach them
  through `config.phase_limit(cfg, "<phase>")`, never by indexing `cfg.limits`
  with a bare role name.
- `bash scripts/test-all.sh` must pass at the end of every task. Never call work done without pasting its output.

## Interface gaps this plan fills

These names are not in the interfaces doc; this plan defines them and every later plan may rely on them.

1. **Judge JSON gains five optional fields:** `instruction` (string — what the next Worker must do differently), `alternatives` (list of strings), `reversal` (string), `changes.tier` (`cheap|standard|most-capable` — the tier the next attempt runs at, spec §6 step 2 and §11 item 4) and `changes.extend_cap_s` (int seconds added to the Worker's wall-clock cap, ≤ the phase default, allowed once per task). `LOOP_DECISIONS.md` requires alternatives and a reversal, so the Judge must produce them.
2. **`judge.md` gains placeholders** `{{policy}}`, `{{failure}}`, `{{verdict}}`, `{{cleanup}}`, `{{forbidden}}`, `{{attempts}}`, `{{tier}}`, `{{budget}}` and `{{boot_evidence}}` on top of the interfaces list. `phases.render_prompt(name, **vars)` is generic, so no signature changes. The interfaces doc's cross-plan note says "six judge placeholders"; it is nine — plan D's `tests/prompts.contract.sh` must allow all nine.
3. **`Plan.set_blocked_by(task_id, blockers) -> bool`** — the interfaces doc defines `Task.blocked_by` and the `| blocked_by:` grammar but no setter. Task 3 adds it.
4. **`TickContext` gains** `resume_session: Optional[str] = None`, `resume_count: int = 0`, `last_session: Optional[str] = None`, `tier: Optional[str] = None` (the tier *this* attempt runs at — armed by the Judge, never derived from the attempt number) and `extend_cap_s: int = 0`.
5. **The attempt history is plan A's `runner/task_state.TaskState`**, not a file this plan owns. `judge.py` reads `TaskState.load(runtime_dir, task_id).attempts` (spec §14 shape: `{"n","tier","phase_results":[…],"outcome","judge":{…}}`) and closes an attempt through `TaskState.end_attempt(outcome, judge=…)`. There is no `attempts-<T>.json` and no `judge.record_attempt` anywhere in v3. `phases.py` must still not import `judge.py` (judge imports phases for `render_prompt`/`parse_json_block`); `run.py` imports both.
6. **`run_phase(max_turns=0)` omits the `--max-turns` flag.** If plan A always passes the flag, use `max_turns=120` for a full Worker instead — the wrap-up's `8` is the load-bearing number.
7. **The stub `claude` fixture gains one protocol element:** a file `NNN.sh` beside `NNN.jsonl` is sourced before the transcript is printed, so a scripted phase can write the runtime files a real phase writes (added in Task 4 Step 1, consumed by Task 9).
8. **`judge.boot_reconcile(ctx, contract) -> str` and `run.boot_task(ctx, contract) -> str`** fill plan A's `# plan C` boot hook: a `[~]` task with no `runtime/task-<T>.json` is reconciled by the Judge with `failure="boot-reconcile"` (spec §14, decision 13). Task 8.
9. **`git_ops.last_commit(cwd) -> str`** — HEAD's full message, so the boot dossier can show the last commit's `Loop-*` trailers. Task 8 adds it; plan A has no message reader.

## File Structure

| file | responsibility |
|---|---|
| `plugins/agent-loop/runner/judge.py` | **new.** Failure dossier (over plan A's `TaskState`), Judge dispatch, policy validation, judged tier + cap extension, decision application, `LOOP_DECISIONS.md`, deferral + semantic blockers, split-row validation, boot reconciliation. |
| `plugins/agent-loop/runner/prompts/judge.md` | **new.** Judge role brief and classification rules. |
| `plugins/agent-loop/runner/prompts/worker_wrapup.md` | **new.** Out-of-time checkpoint continuation, 8 turns. |
| `plugins/agent-loop/runner/prompts/worker_resume.md` | **new.** Same-session continuation with minutes left. |
| `plugins/agent-loop/runner/phases.py` | **modify.** `run_worker` learns wrap-up and resume; `worker_tier`/`worker_cap` read the tier and cap the Judge armed; `TickContext` gains five fields. |
| `plugins/agent-loop/runner/plan.py` | **modify.** `set_blocked_by`. |
| `plugins/agent-loop/runner/run.py` | **modify.** `run_worker_attempt`, `sandbox`, `next_worker_action`, `resumable`, `start_resume`, `failure_signature`, `handle_failure`, `boot_task`; plan A's stand-in failure path and its `# plan C` boot hook are both replaced. |
| `plugins/agent-loop/runner/git_ops.py` | **modify.** `last_commit(cwd)` for the boot dossier's commit trailers. |
| `plugins/agent-loop/runner/incidents.py` | **modify.** `phases: […]` in `incident-<id>.json`; `phase-timeout` and `judge-loop` kinds. |
| `plugins/agent-loop/skills/agent-loop-medic/SKILL.md` | **modify.** Phase-timeline step, new signature key, two decision-table rows. |
| `plugins/agent-loop/tests/runner/cfixtures.py` | **new.** Shared on-disk loop fixture (and `seed_attempt`, which drives plan A's `TaskState`) for this plan's four test modules. |
| `plugins/agent-loop/tests/runner/test_judge.py` | **new.** Dossier, policy validation, application. |
| `plugins/agent-loop/tests/runner/test_resume.py` | **new.** Wrap-up, resume, escalation, sandbox-after-kill, attempts file. |
| `plugins/agent-loop/tests/runner/test_split.py` | **new.** Sub-row grammar, numeric-id plan round-trip, split commit. |
| `plugins/agent-loop/tests/runner/test_boot.py` | **new.** Boot dossier, the four boot decisions, `revert-and-retry`. |
| `plugins/agent-loop/tests/medic.contract.sh` | **modify.** New kinds and phase-timeline assertions. |
| `plugins/agent-loop/tests/run.e2e.test.sh` | **modify.** Widen-and-retry and split scenarios. |
| `plugins/agent-loop/tests/fixtures/claude` | **modify.** `NNN.sh` side-effect hook. |

Task order is dependency order. Tasks 1–3 build `judge.py` bottom-up; 4–5 make the Worker survivable under a judged tier; 6 wires the main-loop failure path; 7 is the medic; 8 is boot reconciliation; 9 is end-to-end.

---

### Task 1: The failure dossier over plan A's per-task state

**Files:**
- Create: `plugins/agent-loop/runner/judge.py`
- Create: `plugins/agent-loop/tests/runner/cfixtures.py`
- Create: `plugins/agent-loop/tests/runner/test_judge.py`

**Interfaces:**
- Consumes: `phases.TickContext` (fields `cfg, plan, task, loop_dir, runtime_dir, events, tick, attempt`, plus this plan's `tier`, `extend_cap_s`, `resume_count`, `last_session`); `config.LoopConfig` (fields `decision_policy`, `plan_path`, `spec_path`, `limits`, `role_tiers`, `worktree`), `config.phase_limit`; `contract.Contract`, `contract.Forbidden`; `plan.Plan.load`, `plan.Task`; `events.EventLog`; `claude_proc.PhaseResult`; **`task_state.TaskState` (plan A)** — `load(runtime_dir, task_id)`, `.attempts`, `begin_attempt(tier)`, `begin_phase(name)`, `end_phase(result)`, `end_attempt(outcome, judge=None)`, `save()`, writing `runtime/task-<T>.json` in the spec §14 shape.
- Produces: `judge.Failure`, `judge.JudgeInput`, `judge.build_input(ctx, contract, failure) -> JudgeInput`, `judge.attempts_for`, `judge.judge_decision_count`, `judge.cap_extended`, `judge.last_tier`, `judge.close_attempt`, `judge.tail_lines`, `judge.grep_excerpts`, `judge.MAX_GATE_LINES`, `judge.PARITY_RE`, `judge.one_line`, `judge.json_safe`.
- **Does not produce:** `attempts-<T>.json` or any helper that writes it. That file existed in an earlier draft of the interfaces doc and is replaced by `runtime/task-<T>.json` (spec §14, interfaces "Cross-plan notes"). If plan A's runtime-file list still mentions it, that line is stale.

- [ ] **Step 1: Write the shared fixture**

Create `plugins/agent-loop/tests/runner/cfixtures.py`:

```python
"""On-disk loop fixture shared by the plan-C runner tests."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(os.path.dirname(HERE))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from runner import phases  # noqa: E402
from runner.config import LoopConfig  # noqa: E402
from runner.contract import Contract, Forbidden, save_contract  # noqa: E402
from runner.events import EventLog  # noqa: E402
from runner.plan import Plan  # noqa: E402
from runner.task_state import TaskState  # noqa: E402

LOOP_PLAN_TEXT = """# Loop Plan

## Segment 1 - API
- [~] T60 Add organisationalUnits REST mixin (P-ORG-001)
- [ ] T61 Wire organisationalUnits into the customers page
- [ ] T62 Cypress coverage for organisationalUnits
"""

DESIGN_PLAN_TEXT = """# Rebuild plan

- T60 organisationalUnits REST: register the mixin in the composition root (P-ORG-001).
- T61 depends on T60 landing first.
"""

SPEC_TEXT = """# Design

REST via ActivePipeApi internal mixins; ~12 mixins.
T60 organisationalUnits is REST, composed as a dedicated internal mixin set.
P-ORG-001 is resolved by registering the mixin in the composition root.
"""


def make_loop(tmp, policy="autonomous", blocker_policy="continue-independent"):
    """Build a loop dir inside a worktree. Returns (cfg, plan, loop_dir, runtime_dir)."""
    worktree = os.path.join(tmp, "wt")
    loop_dir = os.path.join(worktree, ".claude", "loop", "run-1")
    runtime = os.path.join(loop_dir, "runtime")
    os.makedirs(runtime)
    os.makedirs(os.path.join(worktree, "docs"))
    loop_plan_path = os.path.join(loop_dir, "LOOP_PLAN.md")
    _write(loop_plan_path, LOOP_PLAN_TEXT)
    _write(os.path.join(loop_dir, "LOOP_CLEANUP.md"), "# Cleanup\n")
    design_plan = os.path.join(worktree, "docs", "plan.md")
    _write(design_plan, DESIGN_PLAN_TEXT)
    spec = os.path.join(worktree, "docs", "spec.md")
    _write(spec, SPEC_TEXT)
    cfg = LoopConfig(
        worktree=worktree, branch="agent-loop-test", goal="test loop",
        granularity="segmented", tdd_mode="none", verification=["lint"],
        blocker_policy=blocker_policy, dashboard="off", medic="off", medic_model="",
        tiers={"cheap": "haiku", "standard": "sonnet", "most-capable": "opus"},
        role_tiers={"planner": "most-capable", "scout": "standard",
                    "worker": "standard", "evaluator": ""},
        limits={"tick_timeout": 1800, "scout_timeout": 480,
                "worker_timeout": 1500, "wrapup_timeout": 300,
                "eval_timeout": 480, "judge_timeout": 360,
                "planner_timeout": 900, "gate_cmd_timeout": 600,
                "worker_resume_max": 1, "worker_budget_usd": 6,
                "max_attempts": 3},
        decision_policy=policy, render={}, spec_path=spec, plan_path=design_plan,
    )
    return cfg, Plan.load(loop_plan_path), loop_dir, runtime


def make_contract(task="T60"):
    return Contract(
        task=task,
        success_criteria=[
            "packages/api/src/requests/activepipe/internal/organisationalUnits.ts exists"],
        allow_list=["packages/api/src/requests/activepipe/internal/**"],
        forbidden=[
            Forbidden(path="apps/frontend/**", source="plan"),
            Forbidden(path="packages/api/src/requests/activepipe/index.ts", source="scout"),
        ],
        verification=["pnpm turbo run lint --filter=@repo/api"],
        render_gate=None, fidelity_source=[], evaluator_must_read=[],
        evaluator_must_view=[], estimated_diff_lines=300,
        scout_notes="follow T59's precedent", relevant_learnings=[],
    )


def make_ctx(cfg, plan, loop_dir, runtime, task_id="T60", attempt=1):
    task = [t for t in plan.tasks() if t.id == task_id][0]
    events = EventLog(os.path.join(loop_dir, "events.jsonl"),
                      os.path.join(runtime, "tickseq"))
    return phases.TickContext(cfg=cfg, plan=plan, task=task, loop_dir=loop_dir,
                              runtime_dir=runtime, events=events, tick=1,
                              attempt=attempt, tier="standard")


def seed_attempt(runtime, task_id, n, outcome, tier="standard", judge=None,
                 phase_results=()):
    """Drive plan A's TaskState the way the harness does, for test setup."""
    state = TaskState.load(runtime, task_id)
    while len(state.attempts) < n - 1:
        state.begin_attempt(tier)
        state.end_attempt("seeded")
    state.begin_attempt(tier)
    for p in phase_results:
        state.begin_phase(p.phase)
        state.end_phase(p)
    state.end_attempt(outcome, judge=judge)
    state.save()
    return state


def write_contract(ctx, contract):
    path = os.path.join(ctx.runtime_dir, "sprint-%s.json" % contract.task)
    save_contract(contract, path)
    return path


def read_events(loop_dir):
    path = os.path.join(loop_dir, "events.jsonl")
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def read(path):
    with open(path, "r") as fh:
        return fh.read()


def git_init(worktree):
    subprocess.check_call(["git", "init", "-q", worktree])
    for k, v in (("user.email", "loop@test"), ("user.name", "Loop Test")):
        subprocess.check_call(["git", "-C", worktree, "config", k, v])
    subprocess.check_call(["git", "-C", worktree, "add", "-A"])
    subprocess.check_call(["git", "-C", worktree, "commit", "-q", "-m", "base"])


def _write(path, text):
    with open(path, "w") as fh:
        fh.write(text)
```

- [ ] **Step 2: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_judge.py`:

```python
import json
import os
import shutil
import tempfile
import unittest

import cfixtures  # noqa: F401  (also puts the plugin root on sys.path)
from runner import judge


class TestAttemptHistory(unittest.TestCase):
    """Plan C owns no attempt file: the record is plan A's runtime/task-<T>.json."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def _phase(self, phase="worker"):
        from runner.claude_proc import PhaseResult
        return PhaseResult(phase=phase, model="sonnet", rc=0, killed=False,
                           timed_out=False, session_id="sid-1", transcript_path=None,
                           started=100, ended=200, result_text="", usage_by_model={},
                           tool_calls=3, last_activity=200)

    def test_attempts_come_from_task_state(self):
        cfixtures.seed_attempt(self.tmp, "T60", 1, "gate:tsc failed",
                               tier="standard", judge={"decision": "retry"},
                               phase_results=[self._phase()])
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "task-T60.json")),
                        "TaskState writes runtime/task-<T>.json (spec §14)")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "attempts-T60.json")),
                         "attempts-<T>.json does not exist in v3")
        attempts = judge.attempts_for(self.tmp, "T60")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["n"], 1)
        self.assertEqual(attempts[0]["tier"], "standard")
        self.assertEqual(attempts[0]["outcome"], "gate:tsc failed")
        self.assertEqual(attempts[0]["judge"]["decision"], "retry")
        self.assertEqual(attempts[0]["phase_results"][0]["phase"], "worker")

    def test_judge_decision_count(self):
        self.assertEqual(judge.judge_decision_count([]), 0)
        self.assertEqual(judge.judge_decision_count(
            [{"n": 1, "judge": {"decision": "widen"}}, {"n": 2}]), 1)

    def test_attempts_for_a_task_with_no_state_file(self):
        self.assertEqual(judge.attempts_for(self.tmp, "T99"), [])

    def test_cap_extension_and_last_tier_read_the_history(self):
        self.assertFalse(judge.cap_extended([{"n": 1, "judge": {"decision": "retry"}}]))
        self.assertTrue(judge.cap_extended(
            [{"n": 1, "judge": {"changes": {"extend_cap_s": 600}}}]))
        self.assertEqual(judge.last_tier([], "standard"), "standard")
        self.assertEqual(judge.last_tier(
            [{"n": 1, "tier": "standard"}, {"n": 2, "tier": "most-capable"}],
            "standard"), "most-capable")


class TestBuildInput(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cfg, self.plan, self.loop_dir, self.runtime = cfixtures.make_loop(self.tmp)
        self.ctx = cfixtures.make_ctx(self.cfg, self.plan, self.loop_dir, self.runtime)

    def test_dossier_contents(self):
        with open(os.path.join(self.runtime, "gate-T60-1.txt"), "w") as fh:
            fh.write("\n".join("line %d" % i for i in range(200)))
        with open(os.path.join(self.runtime, "worker-result.json"), "w") as fh:
            json.dump({"task": "T60", "status": "partial",
                       "checkpoint": "register the mixin in index.ts"}, fh)
        inp = judge.build_input(self.ctx, cfixtures.make_contract(),
                                judge.Failure(kind="gate", detail="tsc failed"))

        self.assertEqual(inp.task_id, "T60")
        self.assertIn("organisationalUnits", inp.task_row)
        self.assertEqual(inp.policy, "autonomous")
        self.assertEqual(inp.failure, "gate: tsc failed")

        self.assertEqual(inp.tier, "standard", "the tier this attempt ran at")
        self.assertEqual(inp.attempt, 1)
        self.assertEqual(inp.max_attempts, 3)
        self.assertEqual(inp.cap_default_s, 1500, "worker_timeout is the ceiling")
        self.assertFalse(inp.cap_extended)
        self.assertEqual(inp.resumes_left, 1)
        self.assertEqual(inp.boot_evidence, "")

        self.assertEqual(len(inp.gate_outputs), 1)
        body = inp.gate_outputs[0]
        self.assertLessEqual(len(body.splitlines()), judge.MAX_GATE_LINES + 1)
        self.assertIn("line 199", body)
        self.assertNotIn("line 5\n", body)

        self.assertEqual(inp.checkpoint, "register the mixin in index.ts")

        sources = dict((f["path"], f["source"]) for f in inp.forbidden)
        self.assertEqual(sources["apps/frontend/**"], "plan")
        self.assertEqual(
            sources["packages/api/src/requests/activepipe/index.ts"], "scout")

        joined = "\n".join(inp.excerpts)
        self.assertEqual(len(inp.excerpts), 2, "one block per source document")
        self.assertIn("dedicated internal mixin set", joined)
        self.assertIn("composition root", joined)

    def test_parity_ids_come_from_the_task_row(self):
        self.assertEqual(judge.PARITY_RE.findall(
            "- [~] T60 Add organisationalUnits REST mixin (P-ORG-001)"), ["P-ORG-001"])
        inp = judge.build_input(self.ctx, cfixtures.make_contract(),
                                judge.Failure(kind="needs-work", detail="criterion 2 unmet"))
        self.assertIn("P-ORG-001", "\n".join(inp.excerpts))

    def test_missing_optional_inputs_do_not_raise(self):
        inp = judge.build_input(self.ctx, cfixtures.make_contract(),
                                judge.Failure(kind="gate", detail=""))
        self.assertEqual(inp.gate_outputs, [])
        self.assertEqual(inp.checkpoint, "")
        self.assertEqual(inp.attempts, [], "no task-T60.json yet")
        self.assertIn("Cleanup", inp.cleanup_text)

    def test_the_tier_falls_back_to_the_history_then_the_config(self):
        self.ctx.tier = None
        cfixtures.seed_attempt(self.runtime, "T60", 1, "gate:tsc",
                               tier="most-capable")
        inp = judge.build_input(self.ctx, cfixtures.make_contract(),
                                judge.Failure(kind="gate", detail="tsc failed"))
        self.assertEqual(inp.tier, "most-capable")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -t tests/runner -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'runner.judge'`.

- [ ] **Step 4: Write the implementation**

Create `plugins/agent-loop/runner/judge.py`:

```python
"""Judge phase: assemble the failure dossier, ask the Judge, apply its decision.

`judge.py` may import `phases`; `phases` must never import `judge` (spec §7 puts
the Judge downstream of every phase it reviews).
"""
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from runner.config import phase_limit
from runner.task_state import TaskState

MAX_GATE_LINES = 80
MAX_EXCERPT_LINES = 40
PARITY_RE = re.compile(r"\bP-[A-Z]+-\d+\b")


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def one_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except (IOError, OSError):
        return ""


def tail_lines(text: str, n: int) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def json_safe(obj: Any) -> Any:
    """Drop `_`-prefixed keys so a decision dict can be written to disk."""
    if isinstance(obj, dict):
        return dict((k, json_safe(v)) for k, v in obj.items()
                    if not str(k).startswith("_"))
    if isinstance(obj, list):
        return [json_safe(v) for v in obj]
    return obj


def grep_excerpts(paths: List[str], ids: List[str],
                  max_lines: int = MAX_EXCERPT_LINES) -> List[str]:
    """One block per document, listing every line that names one of `ids`."""
    out = []
    for path in paths:
        if not path or not os.path.exists(path):
            continue
        lines = read_text(path).splitlines()
        hits = []
        for n, line in enumerate(lines):
            for ident in ids:
                if re.search(r"\b%s\b" % re.escape(ident), line):
                    hits.append((n, line))
                    break
        if not hits:
            continue
        block = ["### %s" % path]
        for n, line in hits[:max_lines]:
            block.append("%s:%d: %s" % (os.path.basename(path), n + 1, line.strip()))
        if len(hits) > max_lines:
            block.append("... %d more matching lines" % (len(hits) - max_lines))
        out.append("\n".join(block))
    return out


# --------------------------------------------------------------------------
# attempt history — plan A's runtime/task-<T>.json (spec §14). This module reads
# that state and closes attempts through TaskState; it owns no file of its own.
# --------------------------------------------------------------------------
def attempts_for(runtime_dir: str, task_id: str) -> List[dict]:
    """The task's attempt list, `[]` when it has no state file yet."""
    try:
        return list(TaskState.load(runtime_dir, task_id).attempts or [])
    except (IOError, OSError, ValueError):
        return []


def judge_decision_count(attempts: List[dict]) -> int:
    return len([a for a in attempts if a.get("judge")])


def cap_extended(attempts: List[dict]) -> bool:
    """True once the Judge has spent this task's one cap extension (spec §4.2)."""
    for a in attempts or []:
        changes = (a.get("judge") or {}).get("changes") or {}
        try:
            if int(changes.get("extend_cap_s") or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def last_tier(attempts: List[dict], default: str) -> str:
    """The tier the most recent recorded attempt ran at."""
    for a in reversed(attempts or []):
        if a.get("tier"):
            return a["tier"]
    return default


def close_attempt(ctx, outcome: str, decision: Optional[dict] = None,
                  judge_phase=None) -> None:
    """Close the current attempt on the task state: the Judge phase result, the
    outcome signature, and the decision. Every other phase was already recorded
    by the harness at its own transition (spec §14)."""
    state = TaskState.load(ctx.runtime_dir, ctx.task.id)
    if judge_phase is not None:
        state.begin_phase("judge")
        state.end_phase(judge_phase)
    state.end_attempt(outcome, judge=json_safe(decision) if decision else None)
    state.save()


# --------------------------------------------------------------------------
# the dossier
# --------------------------------------------------------------------------
@dataclass
class Failure:
    kind: str          # invalid-contract|gate|render|fidelity|needs-work|
                       # blocker|worker-incomplete|repeat|boot-reconcile
    detail: str = ""
    verdict: Dict[str, Any] = field(default_factory=dict)
    signature: str = ""
    repeat: bool = False


@dataclass
class JudgeInput:
    task_id: str
    task_row: str
    contract_json: str
    gate_outputs: List[str]
    verdict: Dict[str, Any]
    checkpoint: str
    cleanup_text: str
    excerpts: List[str]
    forbidden: List[Dict[str, str]]
    attempts: List[dict]
    policy: str
    failure: str
    # the bounds the harness enforces on whatever the Judge decides (spec §7)
    tier: str = "standard"            # the tier this attempt ran at
    attempt: int = 1
    max_attempts: int = 3
    cap_default_s: int = 1500         # worker_timeout: the extension ceiling
    cap_extended: bool = False        # the one extension is already spent
    resume_session: Optional[str] = None
    resumes_left: int = 0
    boot_evidence: str = ""           # spec §14; filled for boot-reconcile only


def contract_path(ctx) -> str:
    return os.path.join(ctx.runtime_dir, "sprint-%s.json" % ctx.task.id)


def _contract_dict(contract) -> dict:
    return {
        "task": contract.task,
        "success_criteria": list(contract.success_criteria),
        "allow_list": list(contract.allow_list),
        "forbidden": [{"path": f.path, "source": f.source} for f in contract.forbidden],
        "verification": list(contract.verification),
        "evaluator_must_read": list(contract.evaluator_must_read),
        "evaluator_must_view": list(contract.evaluator_must_view),
        "estimated_diff_lines": contract.estimated_diff_lines,
        "scout_notes": contract.scout_notes,
    }


def build_input(ctx, contract, failure: Failure) -> JudgeInput:
    """Everything on disk that bears on this failure, capped for one prompt."""
    gate_outputs = []
    prefix = "gate-%s-" % ctx.task.id
    if os.path.isdir(ctx.runtime_dir):
        for name in sorted(os.listdir(ctx.runtime_dir)):
            if name.startswith(prefix) and name.endswith(".txt"):
                body = tail_lines(read_text(os.path.join(ctx.runtime_dir, name)),
                                  MAX_GATE_LINES)
                gate_outputs.append("### %s (last %d lines)\n%s"
                                    % (name, MAX_GATE_LINES, body))

    checkpoint = ""
    try:
        with open(os.path.join(ctx.runtime_dir, "worker-result.json"), "r") as fh:
            checkpoint = one_line(json.load(fh).get("checkpoint", ""))
    except (IOError, OSError, ValueError):
        checkpoint = ""

    ids = [ctx.task.id] + PARITY_RE.findall(ctx.task.raw or "")
    excerpts = grep_excerpts([ctx.cfg.plan_path, ctx.cfg.spec_path], ids)

    attempts = attempts_for(ctx.runtime_dir, ctx.task.id)
    limits = ctx.cfg.limits or {}
    configured = ctx.cfg.role_tiers.get("worker") or "standard"
    resume_max = int(limits.get("worker_resume_max", 1))
    used = int(getattr(ctx, "resume_count", 0) or 0)

    return JudgeInput(
        task_id=ctx.task.id,
        task_row=(ctx.task.raw or "").rstrip("\n"),
        contract_json=json.dumps(_contract_dict(contract), indent=2, sort_keys=True),
        gate_outputs=gate_outputs,
        verdict=failure.verdict or {},
        checkpoint=checkpoint,
        cleanup_text=read_text(os.path.join(ctx.loop_dir, "LOOP_CLEANUP.md")),
        excerpts=excerpts,
        forbidden=[{"path": f.path, "source": f.source} for f in contract.forbidden],
        attempts=attempts,
        policy=ctx.cfg.decision_policy or "autonomous",
        failure=("%s: %s" % (failure.kind, one_line(failure.detail))).strip().rstrip(":"),
        tier=(getattr(ctx, "tier", None) or last_tier(attempts, configured)),
        attempt=int(getattr(ctx, "attempt", 1) or 1),
        max_attempts=int(limits.get("max_attempts", 3)),
        cap_default_s=phase_limit(ctx.cfg, "worker"),
        cap_extended=cap_extended(attempts),
        resume_session=getattr(ctx, "last_session", None),
        resumes_left=max(0, resume_max - used),
        boot_evidence="",          # Task 8 fills this for boot-reconcile
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -t tests/runner -v`
Expected: PASS — `test_judge.py` contributes 8 tests. (The discovery run also
executes plan A's and plan B's modules; only this plan's count is asserted here.)

- [ ] **Step 6: Commit**

```bash
git add plugins/agent-loop/runner/judge.py plugins/agent-loop/tests/runner/cfixtures.py plugins/agent-loop/tests/runner/test_judge.py
git commit -F - <<'EOF'
agent-loop: judge failure dossier over the per-task state file

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 2: The Judge prompt, dispatch and policy validation

**Files:**
- Create: `plugins/agent-loop/runner/prompts/judge.md`
- Modify: `plugins/agent-loop/runner/judge.py` (append)
- Modify: `plugins/agent-loop/tests/runner/test_judge.py` (append)

**Interfaces:**
- Consumes: `phases.render_prompt(name, **vars) -> str`, `phases.parse_json_block(text) -> dict`, `claude_proc.run_phase(...) -> PhaseResult`, `config.model_for(cfg, tier)`, `config.phase_limit(cfg, phase)`, `config.next_tier` (plan A).
- Produces: `judge.DECISIONS`, `judge.BOOT_DECISIONS`, `judge.CLASSIFICATIONS`, `judge.TIERS`, `judge.validate_decision(decision, inp) -> list[str]`, `judge.validate_sub_rows(parent_id, rows) -> list[str]`, `judge.path_covered(path, pattern) -> bool`, `judge._budget_text(inp) -> str`, `judge.decide(ctx, inp) -> dict`. `decide` always returns a dict with keys `decision`, `classification`, `rationale`, `instruction`, `alternatives`, `reversal`, `changes` (with all seven sub-keys — the five from the interfaces doc plus `tier` and `extend_cap_s`), `rejected` (list, empty when the Judge's own decision stood) and `_phase` (the `PhaseResult`, stripped by `json_safe`).
- **Assumption about plan A's `config.next_tier`:** `next_tier(tier) -> str` returns the tier one rung above, and the same tier at the top of the ladder. If plan A's signature is `next_tier(cfg, tier)`, pass `ctx.cfg` — only the single call site in `validate_decision` changes.

- [ ] **Step 1: Write the failing test**

Append to `plugins/agent-loop/tests/runner/test_judge.py` (above the `if __name__` block):

```python
class _PolicyBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.policy = getattr(self, "POLICY", "autonomous")
        self.cfg, self.plan, self.loop_dir, self.runtime = cfixtures.make_loop(
            self.tmp, policy=self.policy)
        self.ctx = cfixtures.make_ctx(self.cfg, self.plan, self.loop_dir, self.runtime)
        self.contract = cfixtures.make_contract()
        self.inp = judge.build_input(self.ctx, self.contract,
                                     judge.Failure(kind="gate", detail="tsc failed"))

    def decision(self, **over):
        d = {"decision": "widen", "classification": "self-imposed",
             "rationale": "the blocking constraint is scout-sourced",
             "instruction": "", "alternatives": ["defer to a human"],
             "reversal": "drop the allow_list entry",
             "changes": {"allow_list_add": [], "forbidden_remove": [],
                         "default_choice": "", "blocks": [], "sub_rows": [],
                         "tier": "", "extend_cap_s": 0}}
        changes = over.pop("changes", {})
        d.update(over)
        d["changes"].update(changes)
        return d


class TestValidateDecision(_PolicyBase):
    def test_widening_a_scout_sourced_constraint_is_allowed(self):
        d = self.decision(changes={
            "forbidden_remove": ["packages/api/src/requests/activepipe/index.ts"],
            "allow_list_add": ["packages/api/src/requests/activepipe/index.ts"]})
        self.assertEqual(judge.validate_decision(d, self.inp), [])

    def test_widening_a_plan_sourced_constraint_is_rejected(self):
        d = self.decision(changes={"forbidden_remove": ["apps/frontend/**"]})
        errors = judge.validate_decision(d, self.inp)
        self.assertTrue(any("plan-sourced" in e for e in errors), errors)

    def test_allow_list_add_under_a_plan_forbidden_glob_is_rejected(self):
        d = self.decision(changes={"allow_list_add": ["apps/frontend/src/App.tsx"]})
        errors = judge.validate_decision(d, self.inp)
        self.assertTrue(any("apps/frontend/**" in e for e in errors), errors)

    def test_removing_a_constraint_the_contract_does_not_have_is_rejected(self):
        d = self.decision(changes={"forbidden_remove": ["packages/api/nope.ts"]})
        errors = judge.validate_decision(d, self.inp)
        self.assertTrue(any("not in the contract" in e for e in errors), errors)

    def test_widen_without_changes_is_rejected(self):
        errors = judge.validate_decision(self.decision(), self.inp)
        self.assertTrue(any("widen" in e for e in errors), errors)

    def test_unknown_decision_and_classification_are_rejected(self):
        errors = judge.validate_decision(
            self.decision(decision="improvise"), self.inp)
        self.assertTrue(any("unknown decision" in e for e in errors), errors)
        errors = judge.validate_decision(
            self.decision(decision="defer", classification="vibes"), self.inp)
        self.assertTrue(any("classification" in e for e in errors), errors)

    def test_default_choice_allowed_under_autonomous(self):
        d = self.decision(decision="retry", classification="open",
                          changes={"default_choice": "place it under internal/"})
        self.assertEqual(judge.validate_decision(d, self.inp), [])

    def test_third_decision_is_forced_to_defer_or_halt(self):
        cfixtures.seed_attempt(self.runtime, "T60", 1, "gate:tsc",
                               judge={"decision": "widen"})
        cfixtures.seed_attempt(self.runtime, "T60", 2, "gate:tsc",
                               judge={"decision": "escalate"})
        inp = judge.build_input(self.ctx, self.contract,
                                judge.Failure(kind="gate", detail="tsc failed"))
        errors = judge.validate_decision(
            self.decision(decision="retry", classification="capability"), inp)
        self.assertTrue(any("only defer or halt" in e for e in errors), errors)
        self.assertEqual(judge.validate_decision(
            self.decision(decision="defer", classification="open",
                          changes={"default_choice": "x"}), inp), [])
        self.assertEqual(judge.validate_decision(
            self.decision(decision="halt", classification="open"), inp), [])


class TestJudgedTier(_PolicyBase):
    """Spec §6 step 2 and §11 item 4: the Judge picks the tier, the harness
    only bounds it. There is no ladder anywhere in the runner."""

    def test_escalate_must_name_a_tier_above_the_current_one(self):
        self.assertTrue(any("above standard" in e for e in judge.validate_decision(
            self.decision(decision="escalate", classification="capability",
                          changes={"tier": "standard"}), self.inp)))
        self.assertTrue(any("above standard" in e for e in judge.validate_decision(
            self.decision(decision="escalate", classification="capability",
                          changes={"tier": "cheap"}), self.inp)))
        self.assertEqual(judge.validate_decision(
            self.decision(decision="escalate", classification="capability",
                          changes={"tier": "most-capable"}), self.inp), [])

    def test_escalate_without_a_tier_is_rejected(self):
        errors = judge.validate_decision(
            self.decision(decision="escalate", classification="capability"),
            self.inp)
        self.assertTrue(any("changes.tier" in e for e in errors), errors)

    def test_escalate_from_the_top_of_the_ladder_is_impossible(self):
        self.ctx.tier = "most-capable"
        inp = judge.build_input(self.ctx, self.contract,
                                judge.Failure(kind="gate", detail="tsc failed"))
        errors = judge.validate_decision(
            self.decision(decision="escalate", classification="capability",
                          changes={"tier": "most-capable"}), inp)
        self.assertTrue(any("no tier above" in e for e in errors), errors)

    def test_an_unknown_tier_is_rejected(self):
        errors = judge.validate_decision(
            self.decision(decision="retry", classification="capability",
                          changes={"tier": "opus"}), self.inp)
        self.assertTrue(any("unknown tier" in e for e in errors),
                        "tiers, never model names")

    def test_resume_keeps_the_same_tier_and_needs_a_session_and_budget(self):
        errors = judge.validate_decision(
            self.decision(decision="resume", classification="capability",
                          changes={"tier": "standard"}), self.inp)
        self.assertTrue(any("session" in e for e in errors), errors)

        self.ctx.last_session = "sid-1"
        inp = judge.build_input(self.ctx, self.contract,
                                judge.Failure(kind="worker-incomplete", detail=""))
        self.assertEqual(judge.validate_decision(
            self.decision(decision="resume", classification="capability",
                          changes={"tier": "standard"}), inp), [])

        self.ctx.resume_count = 1              # worker_resume_max is 1
        spent = judge.build_input(self.ctx, self.contract,
                                  judge.Failure(kind="worker-incomplete", detail=""))
        self.assertTrue(any("resume budget" in e for e in judge.validate_decision(
            self.decision(decision="resume", classification="capability",
                          changes={"tier": "standard"}), spent)))

    def test_cap_extension_is_bounded_and_once_per_task(self):
        self.assertTrue(any("exceeds the phase default" in e
                            for e in judge.validate_decision(
                                self.decision(decision="retry",
                                              classification="capability",
                                              changes={"extend_cap_s": 1501}),
                                self.inp)))
        ok = self.decision(decision="retry", classification="capability",
                           changes={"extend_cap_s": 600})
        self.assertEqual(judge.validate_decision(ok, self.inp), [])

        cfixtures.seed_attempt(self.runtime, "T60", 1, "worker-incomplete:",
                               judge={"decision": "resume",
                                      "changes": {"extend_cap_s": 600}})
        inp = judge.build_input(self.ctx, self.contract,
                                judge.Failure(kind="gate", detail="tsc failed"))
        self.assertTrue(inp.cap_extended)
        self.assertTrue(any("already used its one cap extension" in e
                            for e in judge.validate_decision(ok, inp)))

    def test_the_last_attempt_accepts_only_split_defer_or_halt(self):
        self.ctx.attempt = 3                   # max_attempts defaults to 3
        inp = judge.build_input(self.ctx, self.contract,
                                judge.Failure(kind="gate", detail="tsc failed"))
        errors = judge.validate_decision(
            self.decision(decision="escalate", classification="capability",
                          changes={"tier": "most-capable"}), inp)
        self.assertTrue(any("is the last" in e for e in errors), errors)
        self.assertEqual(judge.validate_decision(
            self.decision(decision="split", classification="capability",
                          changes={"sub_rows": ["- [ ] Build the mixin",
                                                "- [ ] Register the mixin"]}),
            inp), [])


class TestConservativePolicy(_PolicyBase):
    POLICY = "conservative"

    def test_default_choice_is_rejected(self):
        d = self.decision(decision="retry", classification="open",
                          changes={"default_choice": "place it under internal/"})
        errors = judge.validate_decision(d, self.inp)
        self.assertTrue(any("autonomous" in e for e in errors), errors)

    def test_widen_and_escalate_still_allowed(self):
        d = self.decision(changes={
            "forbidden_remove": ["packages/api/src/requests/activepipe/index.ts"]})
        self.assertEqual(judge.validate_decision(d, self.inp), [])
        self.assertEqual(judge.validate_decision(
            self.decision(decision="escalate", classification="capability",
                          changes={"tier": "most-capable"}), self.inp), [])


class TestDecide(_PolicyBase):
    def _run(self, payload):
        from unittest import mock
        from runner.claude_proc import PhaseResult
        result = PhaseResult(
            phase="judge", model="opus", rc=0, killed=False, timed_out=False,
            session_id="sid-judge", transcript_path=None, started=1, ended=2,
            result_text="Here is my call.\n\n```json\n%s\n```\n" % json.dumps(payload),
            usage_by_model={}, tool_calls=2, last_activity=2)
        with mock.patch.object(judge.claude_proc, "run_phase",
                               return_value=result) as spawn:
            return judge.decide(self.ctx, self.inp), spawn

    def test_valid_decision_passes_through_and_runs_at_most_capable(self):
        out, spawn = self._run({
            "decision": "widen", "classification": "self-imposed",
            "rationale": "scout invented it", "alternatives": ["halt"],
            "reversal": "revert the contract",
            "changes": {"forbidden_remove":
                        ["packages/api/src/requests/activepipe/index.ts"]}})
        self.assertEqual(out["decision"], "widen")
        self.assertEqual(out["rejected"], [])
        self.assertEqual(out["changes"]["allow_list_add"], [])
        self.assertEqual(out["changes"]["sub_rows"], [])
        self.assertEqual(out["changes"]["tier"], "")
        self.assertEqual(out["changes"]["extend_cap_s"], 0)
        self.assertEqual(out["_phase"].session_id, "sid-judge")
        kwargs = spawn.call_args[1]
        self.assertEqual(kwargs["model"], "opus")
        self.assertEqual(kwargs["phase"], "judge")
        self.assertEqual(kwargs["timeout_s"], 360, "judge_timeout")
        self.assertIn("P-ORG-001", kwargs["prompt"])
        self.assertIn("autonomous", kwargs["prompt"])
        self.assertIn("attempt 1 of 3", kwargs["prompt"])
        self.assertIn("one cap extension of up to 1500 s", kwargs["prompt"])

    def test_rejected_decision_is_downgraded_to_defer(self):
        out, _ = self._run({
            "decision": "widen", "classification": "self-imposed",
            "rationale": "I want the composition root",
            "changes": {"forbidden_remove": ["apps/frontend/**"]}})
        self.assertEqual(out["decision"], "defer")
        self.assertTrue(any("plan-sourced" in e for e in out["rejected"]))
        self.assertIn("I want the composition root", out["rationale"])
        self.assertEqual(out["changes"]["forbidden_remove"], [])

    def test_malformed_output_defers(self):
        from unittest import mock
        from runner.claude_proc import PhaseResult
        result = PhaseResult(phase="judge", model="opus", rc=0, killed=False,
                             timed_out=False, session_id="s", transcript_path=None,
                             started=1, ended=2, result_text="no json here",
                             usage_by_model={}, tool_calls=0, last_activity=2)
        with mock.patch.object(judge.claude_proc, "run_phase", return_value=result):
            out = judge.decide(self.ctx, self.inp)
        self.assertEqual(out["decision"], "defer")
        self.assertEqual(out["classification"], "open")
        self.assertTrue(any("malformed" in e for e in out["rejected"]))


class TestSubRowGrammar(_PolicyBase):
    def test_sub_rows_are_titles_that_the_harness_numbers(self):
        self.assertEqual(judge.validate_sub_rows(
            "T60", ["- [ ] Build the organisationalUnits mixin",
                    "- [ ] Register it in the composition root"]), [])
        self.assertTrue(judge.validate_sub_rows("T60", ["- [ ] only one"]))
        self.assertTrue(any("must not name a task id" in e
                            for e in judge.validate_sub_rows(
                                "T60", ["- [ ] T60a Build the mixin",
                                        "- [ ] T60b Register it"])),
                        "lettered ids are gone: serve.py's id regex is \\bT\\d+\\b")
        self.assertTrue(any("must not name a task id" in e
                            for e in judge.validate_sub_rows(
                                "T60", ["- [ ] T74 Build the mixin",
                                        "- [ ] T75 Register it"])),
                        "Plan.split allocates the numbers, not the Judge")
        self.assertTrue(any("grammar" in e for e in judge.validate_sub_rows(
            "T60", ["no checkbox", "- [ ] Register it"])))
        self.assertTrue(any("duplicate" in e for e in judge.validate_sub_rows(
            "T60", ["- [ ] Build the mixin", "- [ ] build the mixin"])))

    def test_split_decision_without_sub_rows_is_rejected(self):
        errors = judge.validate_decision(
            self.decision(decision="split", classification="capability"), self.inp)
        self.assertTrue(any("sub_rows" in e for e in errors), errors)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -t tests/runner -v`
Expected: FAIL — `AttributeError: module 'runner.judge' has no attribute 'validate_decision'`.

- [ ] **Step 3: Write the Judge prompt**

Create `plugins/agent-loop/runner/prompts/judge.md`:

````markdown
# Role: Judge

You are the Judge phase of an autonomous coding loop. A task attempt has failed
and the harness is about to do exactly what you decide. There is no human awake.
Decide from the evidence below, and only from it.

Worktree: {{worktree}}
Loop dir: {{loop_dir}}
Decision policy: {{policy}}
Tier of the attempt you are judging: {{tier}}
Budget: {{budget}}

## What failed

{{failure}}

## Task row

{{task_row}}

## Contract

```json
{{contract_json}}
```

## Constraint provenance

```json
{{forbidden}}
```

`"source": "scout"` means the Scout invented this constraint during this run.
`plan` and `spec` sources come from documents a human wrote and are not yours to
move.

## Gate output

{{gate_outputs}}

## Evaluator verdict

```json
{{verdict}}
```

## Worker checkpoint

{{checkpoint}}

## Plan and spec lines naming this task or its parity ids

{{spec_excerpt}}

## LOOP_CLEANUP.md

{{cleanup}}

## Attempt history for this task

```json
{{attempts}}
```

## Boot reconciliation evidence

{{boot_evidence}}

## Classify first, then decide

Work these in order and stop at the first that fits. The classification is not
decoration: it is the one word a human will read first.

1. **self-imposed** — the constraint in the way carries `"source": "scout"`, or
   the unmet criterion was added by the Scout rather than taken from the task
   row. The loop built its own wall. Decide **widen**: name the exact paths in
   `changes.forbidden_remove` and `changes.allow_list_add`. You may only remove
   `scout`-sourced entries, and you may not add a path that a `plan`- or
   `spec`-sourced `forbidden` pattern covers.
2. **spec-answered** — a plan or spec excerpt above already names the
   resolution. Decide **retry** and quote the decisive sentence in
   `instruction`; the harness appends it to the contract so the next Worker
   cannot miss it. Do not re-open a question a human already closed.
3. **capability** — the tier that ran cannot finish this task as it stands.
   Pick the next attempt with the rule in the next section.
4. **open** — nothing on disk answers the question.
   - Under `autonomous`, when the spec is silent **and** the change is
     reversible in one commit: decide **retry**, put the choice in
     `changes.default_choice` and the reasoning in `instruction`.
   - Otherwise, and always under `conservative`: decide **defer**. Put the
     smallest safe default you can justify in `changes.default_choice` and list
     every task this blocks in `changes.blocks`.
   - Decide **halt** only when continuing could destroy work a human would want
     back.

## Choosing the next attempt (spec §6 step 2)

There is **no tier ladder**. You choose the tier of every re-attempt from the
checkpoint, and the harness does exactly what you say inside its bounds:

- The checkpoint shows **steady file-by-file progress** and the phase simply ran
  out of clock → **resume**, same tier: `changes.tier` = `{{tier}}`. Add
  `changes.extend_cap_s` only if the remaining work plainly needs the time —
  at most the phase default, once per task.
- **No progress**, or the checkpoint reports the same dead end twice, or the
  Worker is circling an approach it cannot finish → **escalate**:
  `changes.tier` must name a tier **above** `{{tier}}`, in a fresh session with
  the checkpoint injected.
- The work is **not resumable and too large** for one phase — two or more
  independently verifiable pieces → **split**: fill `changes.sub_rows`.

`changes.tier` is a tier name — `cheap`, `standard`, `most-capable` — never a
model name. The model behind a tier is a config line you cannot see.

Rules the harness enforces in code and will reject you for breaking:

- `default_choice` is refused under `Decision policy: conservative`.
- `widen` needs at least one `allow_list_add` or `forbidden_remove`.
- `escalate` needs a `changes.tier` above the tier this attempt ran at. At the
  top of the ladder there is nothing to escalate to: `split` or `defer`.
- `resume` needs a session that survived and a resume left in the budget.
- `changes.extend_cap_s` must be ≤ the phase default and is refused once this
  task has spent its one extension. The Budget line above says which.
- `split` needs at least two `sub_rows` of the form `- [ ] <title>` with **no
  task id and no tags**. The harness allocates the next free numeric ids
  (`T74`, `T75`) and appends `| split_of: <parent>` itself. Never write `T60a`:
  the dashboard's id regex is `\bT\d+\b` and a lettered id disappears from it.
- On the last attempt (see Budget) only `split`, `defer` and `halt` are
  accepted.
- After two Judge decisions on this task have already failed, only `defer` and
  `halt` are accepted.

## If this is a boot reconciliation

When `{{failure}}` begins with `boot-reconcile`, the harness restarted and found
this task `[~]` with no recorded state (spec §14). Judge from the evidence block
above — the uncommitted tree split by the contract's `allow_list`, the Worker's
`worker-result.json`, and the last commit's trailers. Only four decisions are
accepted, and no contract or plan change comes with them:

- **resume** — the checkpoint is coherent with the tree: keep every uncommitted
  change and re-dispatch the Worker against the existing contract. No re-scout.
- **retry** — the tree is clean, or everything dirty is inside `allow_list` and
  is worth keeping as a starting point; the task restarts from SCOUT.
- **revert-and-retry** — the tree is dirty in a way nothing vouches for. The
  harness reverts **every** uncommitted change, including inside `allow_list`,
  then restarts from SCOUT. This throws work away; say so in `reversal`.
- **defer** — the evidence contradicts itself, or a commit landed that the plan
  does not account for.

A rejected decision is recorded verbatim and downgraded to `defer`, which costs a
human their morning. Read the provenance before you widen.

## Tools

`Read`, `Grep` and `Glob` only. Do not edit a file. Do not run a command. Never
call `AskUserQuestion` or `EnterPlanMode` — nobody is there, and the phase will
be killed at its timeout with no decision recorded.

## Output

End your reply with exactly one fenced json block and nothing after it:

```json
{
  "decision": "retry|escalate|widen|resume|revert-and-retry|split|defer|halt",
  "classification": "self-imposed|spec-answered|capability|open",
  "rationale": "one paragraph: what failed, what the evidence says, why this decision",
  "instruction": "what the next Worker must do differently, or an empty string",
  "alternatives": ["the decision you rejected, and why you rejected it"],
  "reversal": "the exact thing a human does to undo this",
  "changes": {
    "allow_list_add": [],
    "forbidden_remove": [],
    "default_choice": "",
    "blocks": [],
    "sub_rows": [],
    "tier": "",
    "extend_cap_s": 0
  }
}
```
````

- [ ] **Step 4: Write the implementation**

Append to `plugins/agent-loop/runner/judge.py`:

```python
import fnmatch  # noqa: E402  (kept with the block it serves)

from runner import claude_proc, phases  # noqa: E402
from runner.config import model_for, next_tier, phase_limit  # noqa: E402

DECISIONS = ("retry", "escalate", "widen", "resume", "revert-and-retry",
             "split", "defer", "halt")
BOOT_DECISIONS = ("resume", "retry", "revert-and-retry", "defer")
CLASSIFICATIONS = ("self-imposed", "spec-answered", "capability", "open")
TIERS = ("cheap", "standard", "most-capable")
SUB_ROW_RE = re.compile(r"^- \[ \] (?P<title>\S.*)$")
SUB_ROW_ID_RE = re.compile(r"^T\d+[a-z]?\b")


def path_covered(path: str, pattern: str) -> bool:
    """True when `pattern` (a contract forbidden entry) covers `path`."""
    if path == pattern:
        return True
    if fnmatch.fnmatch(path, pattern):
        return True
    base = pattern.rstrip("*").rstrip("/")
    return bool(base) and path.startswith(base + "/")


def validate_sub_rows(parent_id: str, rows: List[str]) -> List[str]:
    """Sub rows are titles; `Plan.split` allocates the ids (interfaces doc).

    serve.py's id regex is `\\bT\\d+\\b`, so a lettered id like `T60a` would
    vanish from the dashboard. The harness therefore allocates the next free
    numeric ids and appends `| split_of: <parent>` itself, and the Judge is not
    allowed to name an id at all.
    """
    errors = []
    rows = rows or []
    if len(rows) < 2:
        errors.append("split needs at least two sub_rows, got %d" % len(rows))
    seen = []
    for row in rows:
        m = SUB_ROW_RE.match((row or "").strip())
        if not m:
            errors.append("sub row breaks the plan grammar "
                          "`- [ ] <title>`: %r" % row)
            continue
        title = m.group("title").strip()
        if SUB_ROW_ID_RE.match(title):
            errors.append("sub row must not name a task id (%s): the harness "
                          "allocates the next free numeric ids and appends "
                          "`| split_of: %s`" % (title.split()[0], parent_id))
            continue
        if "split_of:" in title:
            errors.append("sub row must not carry `| split_of:`; the harness "
                          "appends it: %r" % row)
        key = title.lower()
        if key in seen:
            errors.append("duplicate sub row title %r" % title)
        seen.append(key)
    return errors


def validate_decision(decision: dict, inp: JudgeInput) -> List[str]:
    """[] means the harness will do what the Judge said. Spec §7 policy."""
    errors = []
    d = (decision or {}).get("decision")
    if d not in DECISIONS:
        return ["unknown decision %r (expected one of %s)" % (d, "|".join(DECISIONS))]
    if decision.get("classification") not in CLASSIFICATIONS:
        errors.append("missing or unknown classification %r (expected one of %s)"
                      % (decision.get("classification"), "|".join(CLASSIFICATIONS)))

    boot = inp.failure.startswith("boot-reconcile")
    if boot and d not in BOOT_DECISIONS:
        errors.append("boot reconciliation accepts only %s, not %r (spec §14)"
                      % ("|".join(BOOT_DECISIONS), d))
    if d == "revert-and-retry" and not boot:
        errors.append("revert-and-retry exists only for boot reconciliation "
                      "(spec §14); this failure is %s" % inp.failure)

    changes = decision.get("changes") or {}
    by_path = dict((f["path"], f["source"]) for f in inp.forbidden)

    for p in changes.get("forbidden_remove") or []:
        if p not in by_path:
            errors.append("forbidden_remove %s is not in the contract's "
                          "forbidden list" % p)
        elif by_path[p] != "scout":
            errors.append("forbidden_remove %s is %s-sourced; only scout-sourced "
                          "constraints may be widened" % (p, by_path[p]))

    for p in changes.get("allow_list_add") or []:
        for entry in inp.forbidden:
            if entry["source"] in ("plan", "spec") and path_covered(p, entry["path"]):
                errors.append("allow_list_add %s is covered by the %s-sourced "
                              "forbidden entry %s"
                              % (p, entry["source"], entry["path"]))

    if changes.get("default_choice") and inp.policy != "autonomous":
        errors.append("default_choice is only allowed under "
                      "`Decision policy: autonomous` (this loop is %s)" % inp.policy)

    if d == "widen" and not (changes.get("allow_list_add")
                             or changes.get("forbidden_remove")):
        errors.append("widen with neither allow_list_add nor forbidden_remove "
                      "changes nothing")
    if d == "split":
        errors.extend(validate_sub_rows(inp.task_id, changes.get("sub_rows")))

    # --- the judged tier, and the bounds the harness keeps (spec §6, §7) ---
    tier = changes.get("tier") or ""
    if tier and tier not in TIERS:
        errors.append("unknown tier %r (expected one of %s); `changes.tier` "
                      "names a tier, never a model" % (tier, "|".join(TIERS)))
    if d == "escalate":
        if not tier:
            errors.append("escalate must name changes.tier; this attempt ran "
                          "at %s" % inp.tier)
        elif tier in TIERS and inp.tier in TIERS:
            if TIERS.index(tier) <= TIERS.index(inp.tier):
                errors.append("escalate must name a tier above %s; %s is not"
                              % (inp.tier, tier))
            elif next_tier(inp.tier) == inp.tier:
                errors.append("there is no tier above %s; split or defer "
                              "instead" % inp.tier)

    if d == "resume" and not boot:
        if not inp.resume_session:
            errors.append("resume needs a session to resume; none was captured")
        if inp.resumes_left <= 0:
            errors.append("the resume budget (worker_resume_max) is spent for %s"
                          % inp.task_id)

    try:
        extend = int(changes.get("extend_cap_s") or 0)
    except (TypeError, ValueError):
        extend = -1
        errors.append("extend_cap_s must be a whole number of seconds")
    if extend > 0:
        if extend > inp.cap_default_s:
            errors.append("extend_cap_s %ds exceeds the phase default %ds"
                          % (extend, inp.cap_default_s))
        if inp.cap_extended:
            errors.append("%s has already used its one cap extension"
                          % inp.task_id)
        if d not in ("retry", "widen", "escalate", "resume"):
            errors.append("extend_cap_s means nothing on a %s decision" % d)
    elif extend < 0:
        errors.append("extend_cap_s must not be negative")

    if inp.attempt >= inp.max_attempts and d not in ("split", "defer", "halt"):
        errors.append("attempt %d of %d is the last (max_attempts); only split, "
                      "defer or halt remain" % (inp.attempt, inp.max_attempts))

    if judge_decision_count(inp.attempts) >= 2 and d not in ("defer", "halt"):
        errors.append("two Judge decisions on %s have already failed; "
                      "only defer or halt remain" % inp.task_id)
    return errors


def _normalize(decision: dict) -> dict:
    out = dict(decision or {})
    out.setdefault("rationale", "")
    out.setdefault("instruction", "")
    out.setdefault("alternatives", [])
    out.setdefault("reversal", "")
    out.setdefault("rejected", [])
    changes = dict(out.get("changes") or {})
    for key, empty in (("allow_list_add", []), ("forbidden_remove", []),
                       ("default_choice", ""), ("blocks", []), ("sub_rows", []),
                       ("tier", ""), ("extend_cap_s", 0)):
        changes.setdefault(key, empty)
    out["changes"] = changes
    return out


def _defer(reasons: List[str], original: dict) -> dict:
    original = original or {}
    classification = original.get("classification")
    return _normalize({
        "decision": "defer",
        "classification": classification if classification in CLASSIFICATIONS else "open",
        "rationale": "Judge decision refused by policy: %s. Original rationale: %s"
                     % ("; ".join(reasons), one_line(original.get("rationale"))),
        "instruction": "",
        "alternatives": list(original.get("alternatives") or []),
        "reversal": "reverse nothing - no file was changed; decide the question "
                    "in LOOP_CLEANUP.md and re-run the task",
        "rejected": reasons,
        "changes": {"blocks": list((original.get("changes") or {}).get("blocks") or [])},
    })


def _budget_text(inp: JudgeInput) -> str:
    """The bounds, in one sentence, so the Judge never proposes a refused call."""
    bits = ["attempt %d of %d" % (inp.attempt, inp.max_attempts),
            "the last Worker ran at tier `%s`" % inp.tier,
            "the worker cap default is %d s" % inp.cap_default_s,
            ("this task's one cap extension is already spent" if inp.cap_extended
             else "one cap extension of up to %d s is still available"
                  % inp.cap_default_s),
            "%d resume(s) left in the budget" % inp.resumes_left,
            ("session %s can be resumed" % inp.resume_session
             if inp.resume_session else "there is no session to resume")]
    if inp.attempt >= inp.max_attempts:
        bits.append("this is the last attempt, so only split, defer or halt "
                    "will be accepted")
    return "; ".join(bits) + "."


def decide(ctx, inp: JudgeInput) -> dict:
    """Run the Judge phase and return a decision the harness is allowed to apply."""
    prompt = phases.render_prompt(
        "judge",
        loop_dir=ctx.loop_dir,
        worktree=ctx.cfg.worktree,
        policy=inp.policy,
        tier=inp.tier,
        budget=_budget_text(inp),
        boot_evidence=(inp.boot_evidence
                       or "(not a boot reconciliation — ignore this section)"),
        failure=inp.failure,
        task_row=inp.task_row,
        contract_json=inp.contract_json,
        forbidden=json.dumps(inp.forbidden, indent=2),
        gate_outputs="\n\n".join(inp.gate_outputs) or "(no gate output)",
        verdict=json.dumps(inp.verdict, indent=2) if inp.verdict else "{}",
        checkpoint=inp.checkpoint or "(no checkpoint)",
        spec_excerpt="\n\n".join(inp.excerpts)
                     or "(no plan or spec line names this task)",
        cleanup=inp.cleanup_text.strip() or "(empty)",
        attempts=json.dumps(inp.attempts, indent=2),
    )
    tier = ctx.cfg.role_tiers.get("judge") or "most-capable"
    result = claude_proc.run_phase(
        phase="judge",
        model=model_for(ctx.cfg, tier),
        prompt=prompt,
        cwd=ctx.cfg.worktree,
        timeout_s=phase_limit(ctx.cfg, "judge"),
        max_turns=20,
        max_budget_usd=None,
        env=dict(os.environ),
        events=ctx.events,
        tick=ctx.tick,
        role="judge",
        activity_path=os.path.join(ctx.runtime_dir, "last-activity"),
        allowed_tools=["Read", "Grep", "Glob"],
    )

    raw = phases.parse_json_block(result.result_text) or {}
    if not raw.get("decision"):
        out = _defer(["the Judge returned malformed output (no decision json block)"],
                     raw)
    else:
        errors = validate_decision(raw, inp)
        out = _defer(errors, raw) if errors else _normalize(raw)
    out["_phase"] = result
    return out
```

Hoist the two import lines to the top of the module with the others when you
paste this — they are shown beside the code they serve only for readability.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -t tests/runner -v`
Expected: PASS — `test_judge.py` contributes 27 tests.

- [ ] **Step 6: Commit**

```bash
git add plugins/agent-loop/runner/judge.py plugins/agent-loop/runner/prompts/judge.md plugins/agent-loop/tests/runner/test_judge.py
git commit -F - <<'EOF'
agent-loop: judge prompt, dispatch and decision-policy validation

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 3: Applying a decision — contract, plan, LOOP_DECISIONS.md, deferral, split

**Files:**
- Modify: `plugins/agent-loop/runner/judge.py` (append)
- Modify: `plugins/agent-loop/runner/plan.py` (add `Plan.set_blocked_by`)
- Modify: `plugins/agent-loop/tests/runner/test_judge.py` (append)
- Create: `plugins/agent-loop/tests/runner/test_split.py`

**Interfaces:**
- Consumes: `contract.save_contract(c, path)`, `plan.Plan.set_state/save/split/tasks/eligible`, `git_ops.commit(cwd, paths, subject, trailers) -> sha`, `events.EventLog.emit`.
- Produces: `judge.NEXT_ACTION`, `judge.apply(ctx, contract, decision) -> str` (returns `retry|escalate|resume|split|defer|halt`), `judge.mark_blocked(ctx, decision)`, `judge.append_decision`, `judge.write_cleanup_entry`, `plan.Plan.set_blocked_by(task_id, blockers) -> bool`.
- **`Plan.split(task_id, sub_rows)` is plan A's** and allocates the next free **numeric** ids (`max(existing)+1…`), appending `| split_of: <parent>` to each inserted row and marking the parent `[-] … split→T63,T64`. This plan never constructs an id.
- **Assumption about plan A's `Plan` internals:** the loader keeps the file as `self._lines` (a list of raw, newline-terminated strings), an id index `self._by_id: Dict[str, Task]`, and `Task.line_no` as the 0-based index into `self._lines`; `self.path` is the plan path. If plan A named these differently, rename in `set_blocked_by` — the behaviour asserted by the tests is what matters.

- [ ] **Step 1: Write the failing tests**

Append to `plugins/agent-loop/tests/runner/test_judge.py`:

```python
class TestApply(_PolicyBase):
    def setUp(self):
        _PolicyBase.setUp(self)
        cfixtures.git_init(self.cfg.worktree)
        self.contract_path = cfixtures.write_contract(self.ctx, self.contract)

    def test_widen_mutates_the_contract_and_records_the_decision(self):
        from runner.contract import load_contract
        action = judge.apply(self.ctx, self.contract, judge._normalize({
            "decision": "widen", "classification": "self-imposed",
            "rationale": "the blocking constraint is the Scout's own",
            "instruction": "register the mixin in index.ts, do not re-create it",
            "alternatives": ["halt and ask a human, as the 2026-09-11 run did"],
            "reversal": "delete the allow_list entry from runtime/sprint-T60.json",
            "changes": {
                "forbidden_remove": ["packages/api/src/requests/activepipe/index.ts"],
                "allow_list_add": ["packages/api/src/requests/activepipe/index.ts"]}}))
        self.assertEqual(action, "retry")

        saved = load_contract(self.contract_path)
        self.assertIn("packages/api/src/requests/activepipe/index.ts",
                      saved.allow_list)
        self.assertEqual([f.path for f in saved.forbidden], ["apps/frontend/**"])
        self.assertIn("register the mixin in index.ts", saved.scout_notes)
        self.assertIn("follow T59's precedent", saved.scout_notes)

        text = cfixtures.read(os.path.join(self.loop_dir, "LOOP_DECISIONS.md"))
        self.assertRegex(
            text,
            r"(?m)^## \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ T60 — widen \(self-imposed\)$")
        self.assertIn("- **Rationale:** the blocking constraint is the Scout's own",
                      text)
        self.assertIn("- **Alternatives:** halt and ask a human", text)
        self.assertIn("- **Reverse:** delete the allow_list entry", text)
        self.assertIn("allow_list += packages/api/src/requests/activepipe/index.ts",
                      text)

        evs = [e for e in cfixtures.read_events(self.loop_dir)
               if e["type"] == "decision"]
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["task"], "T60")
        self.assertEqual(evs[0]["decision"], "widen")
        self.assertEqual(evs[0]["classification"], "self-imposed")

    def test_escalate_and_resume_inject_the_instruction_only(self):
        from runner.contract import load_contract
        for decision, expected in (("escalate", "escalate"), ("resume", "resume")):
            action = judge.apply(self.ctx, self.contract, judge._normalize({
                "decision": decision, "classification": "capability",
                "rationale": "same tier twice", "instruction": "use the mixin chain",
                "alternatives": ["split"], "reversal": "none needed",
                "changes": {}}))
            self.assertEqual(action, expected)
        saved = load_contract(self.contract_path)
        self.assertEqual(saved.allow_list, self.contract.allow_list)
        self.assertEqual(saved.scout_notes.count("use the mixin chain"), 2)

    def test_the_judged_tier_and_cap_extension_are_armed_on_the_context(self):
        action = judge.apply(self.ctx, self.contract, judge._normalize({
            "decision": "resume", "classification": "capability",
            "rationale": "steady file-by-file progress, simply out of clock",
            "instruction": "", "alternatives": ["escalate to most-capable"],
            "reversal": "none needed; the cap returns to its default next task",
            "changes": {"tier": "standard", "extend_cap_s": 600}}))
        self.assertEqual(action, "resume")
        self.assertEqual(self.ctx.tier, "standard",
                         "the Judge's tier, not a ladder step")
        self.assertEqual(self.ctx.extend_cap_s, 600,
                         "apply hands the extended cap to the next run_worker")
        text = cfixtures.read(os.path.join(self.loop_dir, "LOOP_DECISIONS.md"))
        self.assertIn("worker cap extended by 600s", text)
        self.assertIn("next attempt runs at tier standard", text)

    def test_escalate_arms_the_tier_the_judge_named(self):
        judge.apply(self.ctx, self.contract, judge._normalize({
            "decision": "escalate", "classification": "capability",
            "rationale": "no progress in two attempts",
            "instruction": "", "alternatives": ["split"], "reversal": "none",
            "changes": {"tier": "most-capable"}}))
        self.assertEqual(self.ctx.tier, "most-capable")

    def test_defer_writes_cleanup_blocks_downstream_and_marks_the_task(self):
        from runner.plan import Plan
        action = judge.apply(self.ctx, self.contract, judge._normalize({
            "decision": "defer", "classification": "open",
            "rationale": "the spec does not say where the mixin is registered",
            "alternatives": ["guess the composition root"],
            "reversal": "clear blocked_by on T61 and re-run T60",
            "changes": {"default_choice": "register it in internal/index.ts",
                        "blocks": ["T61"]}}))
        self.assertEqual(action, "defer")

        cleanup = cfixtures.read(os.path.join(self.loop_dir, "LOOP_CLEANUP.md"))
        self.assertIn("## T60", cleanup)
        self.assertIn("(open)", cleanup)
        self.assertIn("**Proposed default:** register it in internal/index.ts",
                      cleanup)
        self.assertIn("**Blocks:** T61", cleanup)

        reloaded = Plan.load(os.path.join(self.loop_dir, "LOOP_PLAN.md"))
        t60 = [t for t in reloaded.tasks() if t.id == "T60"][0]
        t61 = [t for t in reloaded.tasks() if t.id == "T61"][0]
        self.assertEqual(t60.state, "blocked")
        self.assertEqual(t61.blocked_by, ["T60"])
        self.assertIn("| blocked_by: T60", t61.raw)
        eligible = [t.id for t in reloaded.eligible()]
        self.assertNotIn("T61", eligible)
        self.assertIn("T62", eligible)

    def test_halt_marks_the_task_and_returns_halt(self):
        from runner.plan import Plan
        action = judge.apply(self.ctx, self.contract, judge._normalize({
            "decision": "halt", "classification": "open",
            "rationale": "continuing would drop uncommitted work",
            "alternatives": ["defer"], "reversal": "resume the loop",
            "changes": {}}))
        self.assertEqual(action, "halt")
        reloaded = Plan.load(os.path.join(self.loop_dir, "LOOP_PLAN.md"))
        self.assertEqual(
            [t for t in reloaded.tasks() if t.id == "T60"][0].state, "blocked")

    def test_mark_blocked_refuses_without_a_decision(self):
        with self.assertRaises(RuntimeError) as ctx:
            judge.mark_blocked(self.ctx, {})
        self.assertIn("without a Judge decision", str(ctx.exception))
```

Create `plugins/agent-loop/tests/runner/test_split.py`:

```python
import os
import shutil
import subprocess
import tempfile
import unittest

import cfixtures  # noqa: F401
from runner import judge
from runner.plan import Plan

# Titles only: the Judge never names an id, `Plan.split` allocates them.
SUB_ROWS = ["- [ ] Build the organisationalUnits mixin",
            "- [ ] Register the mixin in the composition root"]


class TestPlanSplit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cfg, self.plan, self.loop_dir, self.runtime = cfixtures.make_loop(self.tmp)
        self.plan_path = os.path.join(self.loop_dir, "LOOP_PLAN.md")

    def test_round_trip(self):
        new_ids = self.plan.split("T60", SUB_ROWS)
        self.plan.save()
        self.assertEqual(new_ids, ["T63", "T64"],
                         "the next free numeric ids, never T60a: serve.py's id "
                         "regex is \\bT\\d+\\b and a lettered id vanishes from "
                         "the dashboard")

        text = cfixtures.read(self.plan_path)
        self.assertIn("- [-] T60 ", text)
        self.assertIn("split→T63,T64", text)
        self.assertEqual(text.count("| split_of: T60"), 2)

        reloaded = Plan.load(self.plan_path)
        self.assertEqual([t.id for t in reloaded.tasks()],
                         ["T60", "T63", "T64", "T61", "T62"])
        by_id = dict((t.id, t) for t in reloaded.tasks())
        self.assertEqual(by_id["T60"].state, "skipped")
        self.assertEqual(by_id["T63"].state, "pending")
        self.assertEqual(by_id["T63"].segment, by_id["T60"].segment)
        eligible = [t.id for t in reloaded.eligible()]
        self.assertIn("T63", eligible)
        self.assertIn("T64", eligible)
        self.assertNotIn("T60", eligible)


class TestApplySplit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cfg, self.plan, self.loop_dir, self.runtime = cfixtures.make_loop(self.tmp)
        self.ctx = cfixtures.make_ctx(self.cfg, self.plan, self.loop_dir, self.runtime)
        self.contract = cfixtures.make_contract()
        cfixtures.write_contract(self.ctx, self.contract)
        cfixtures.git_init(self.cfg.worktree)

    def test_split_rewrites_the_plan_commits_and_emits(self):
        action = judge.apply(self.ctx, self.contract, judge._normalize({
            "decision": "split", "classification": "capability",
            "rationale": "resume did not finish; the task is two verifiable pieces",
            "alternatives": ["escalate to the next tier again"],
            "reversal": "revert the split commit",
            "changes": {"sub_rows": SUB_ROWS}}))
        self.assertEqual(action, "split")

        text = cfixtures.read(os.path.join(self.loop_dir, "LOOP_PLAN.md"))
        self.assertIn("split→T63,T64", text)
        self.assertNotIn("T60a", text)

        log = subprocess.check_output(
            ["git", "-C", self.cfg.worktree, "log", "-1", "--pretty=%B"]
        ).decode()
        self.assertIn("loop: split T60 → T63,T64", log)
        self.assertIn("Loop-Status: skipped", log)

        evs = [e for e in cfixtures.read_events(self.loop_dir) if e["type"] == "split"]
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["task"], "T60")
        self.assertEqual(evs[0]["into"], ["T63", "T64"])

        decisions = cfixtures.read(os.path.join(self.loop_dir, "LOOP_DECISIONS.md"))
        self.assertIn("— split (capability)", decisions)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -t tests/runner -v`
Expected: FAIL — `AttributeError: module 'runner.judge' has no attribute 'apply'`, and `AttributeError: 'Plan' object has no attribute 'set_blocked_by'`.

- [ ] **Step 3: Add `Plan.set_blocked_by`**

Append to the `Plan` class in `plugins/agent-loop/runner/plan.py`:

```python
    def set_blocked_by(self, task_id, blockers):
        """Add semantic blockers to a task row's `| blocked_by:` tag.

        Returns True when the row changed. `eligible()` already treats a task
        with an unfinished `blocked_by` entry as ineligible (spec §7).
        """
        task = self._by_id.get(task_id)
        if task is None:
            return False
        merged = list(task.blocked_by)
        for b in blockers:
            if b and b not in merged:
                merged.append(b)
        if merged == list(task.blocked_by):
            return False
        line = self._lines[task.line_no].rstrip("\n")
        tag = " | blocked_by: %s" % ",".join(merged)
        m = re.search(r"\s*\|\s*blocked_by:\s*[^|]*", line)
        if m:
            line = line[:m.start()] + tag + line[m.end():]
        else:
            line = line.rstrip() + tag
        self._lines[task.line_no] = line + "\n"
        task.blocked_by = merged
        task.raw = line
        return True
```

- [ ] **Step 4: Write `apply` and its helpers**

Append to `plugins/agent-loop/runner/judge.py`:

```python
import time  # noqa: E402  (hoist to the top of the module)

from runner import git_ops  # noqa: E402
from runner.contract import save_contract  # noqa: E402

NEXT_ACTION = {"widen": "retry", "retry": "retry", "escalate": "escalate",
               "resume": "resume", "revert-and-retry": "retry",
               "split": "split", "defer": "defer", "halt": "halt"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def mark_blocked(ctx, decision: dict) -> None:
    """The only place a task becomes `[!]`. Spec §7: never without a Judge."""
    if not (decision or {}).get("decision"):
        raise RuntimeError("refusing to mark %s [!] without a Judge decision "
                           "recorded (spec §7)" % ctx.task.id)
    ctx.plan.set_state(ctx.task.id, "blocked")
    ctx.plan.save()


def append_decision(ctx, decision: dict, applied: List[str]) -> None:
    path = os.path.join(ctx.loop_dir, "LOOP_DECISIONS.md")
    chunks = []
    if not os.path.exists(path):
        chunks.append("# Loop Decisions\n\nEvery judgement this loop made instead "
                      "of waking a human. Each entry says what was decided, what "
                      "was rejected, and how to put it back.\n")
    alts = decision.get("alternatives") or []
    chunks.append("\n## %s %s — %s (%s)\n\n"
                  % (_now(), ctx.task.id, decision["decision"],
                     decision.get("classification", "open")))
    chunks.append("- **Rationale:** %s\n" % one_line(decision.get("rationale")))
    chunks.append("- **Alternatives:** %s\n"
                  % ("; ".join(one_line(a) for a in alts) if alts
                     else "none recorded"))
    chunks.append("- **Applied:** %s\n"
                  % ("; ".join(applied) if applied else "no file changed"))
    chunks.append("- **Reverse:** %s\n"
                  % (one_line(decision.get("reversal"))
                     or "undo the Applied changes and re-run %s" % ctx.task.id))
    if decision.get("rejected"):
        chunks.append("- **Refused by policy:** %s\n"
                      % "; ".join(decision["rejected"]))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("".join(chunks))


def write_cleanup_entry(ctx, decision: dict) -> None:
    changes = decision.get("changes") or {}
    blocks = changes.get("blocks") or []
    default = one_line(changes.get("default_choice"))
    chunks = [
        "\n## %s — %s (%s)\n\n" % (ctx.task.id, one_line(ctx.task.title),
                                   decision.get("classification", "open")),
        "- **Decision needed:** %s\n" % one_line(decision.get("rationale")),
        "- **Proposed default:** %s\n"
        % (default or "none — the Judge could not justify one"),
        "- **Blocks:** %s\n" % (", ".join(blocks) if blocks else "nothing else"),
        "- **Raised by:** Judge, %s\n" % _now(),
    ]
    with open(os.path.join(ctx.loop_dir, "LOOP_CLEANUP.md"), "a",
              encoding="utf-8") as fh:
        fh.write("".join(chunks))


def apply(ctx, contract, decision: dict) -> str:
    """Do what the decision says, then tell the main loop what happens next."""
    d = decision["decision"]
    changes = decision.get("changes") or {}
    applied = []
    path = contract_path(ctx)
    plan_path = os.path.join(ctx.loop_dir, "LOOP_PLAN.md")
    contract_dirty = False

    for p in changes.get("allow_list_add") or []:
        if p not in contract.allow_list:
            contract.allow_list.append(p)
            applied.append("allow_list += %s" % p)
            contract_dirty = True
    removed = set(changes.get("forbidden_remove") or [])
    if removed:
        keep = []
        for f in contract.forbidden:
            if f.path in removed and f.source == "scout":
                applied.append("forbidden -= %s (scout-sourced)" % f.path)
                contract_dirty = True
            else:
                keep.append(f)
        contract.forbidden = keep

    note = one_line(decision.get("instruction")) or one_line(
        changes.get("default_choice"))
    if note and d in ("retry", "widen", "escalate", "resume"):
        contract.scout_notes = ("%s\n\nJUDGE %s (%s): %s"
                                % (contract.scout_notes, _now(), d, note)).strip()
        applied.append("instruction appended to scout_notes")
        contract_dirty = True
    if contract_dirty:
        save_contract(contract, path)

    # The judged tier and the one cap extension are armed on the context; the
    # next `phases.run_worker` reads both (spec §6 step 2). There is no ladder.
    tier = changes.get("tier") or ""
    if tier and d in ("retry", "widen", "escalate", "resume"):
        ctx.tier = tier
        applied.append("next attempt runs at tier %s" % tier)
    extend = int(changes.get("extend_cap_s") or 0)
    if extend > 0 and d in ("retry", "widen", "escalate", "resume"):
        ctx.extend_cap_s = extend
        applied.append("worker cap extended by %ds (this task's only one)"
                       % extend)

    for tid in changes.get("blocks") or []:
        if ctx.plan.set_blocked_by(tid, [ctx.task.id]):
            applied.append("%s blocked_by %s" % (tid, ctx.task.id))

    if d == "revert-and-retry":
        # Boot reconciliation only (spec §14): nothing in the tree is vouched
        # for, so the in-allow_list changes go too, not just the strays.
        changed = git_ops.changed_paths(ctx.cfg.worktree)
        if changed:
            git_ops.revert(ctx.cfg.worktree, changed)
            applied.append("reverted %d uncommitted path(s), allow_list included"
                           % len(changed))
        ctx.resume_session = None
        ctx.last_session = None
    elif d == "split":
        new_ids = ctx.plan.split(ctx.task.id, changes.get("sub_rows") or [])
        ctx.plan.save()
        sha = git_ops.commit(
            ctx.cfg.worktree, [plan_path],
            "loop: split %s → %s" % (ctx.task.id, ",".join(new_ids)),
            {"Loop-Status": "skipped", "Loop-Task": ctx.task.id})
        applied.append("plan split into %s" % ", ".join(new_ids))
        ctx.events.emit("split", tick=ctx.tick, task=ctx.task.id, into=new_ids,
                        sha=sha)
    elif d in ("defer", "halt"):
        write_cleanup_entry(ctx, decision)
        mark_blocked(ctx, decision)
        applied.append("%s marked [!]" % ctx.task.id)
        git_ops.commit(
            ctx.cfg.worktree,
            [plan_path, os.path.join(ctx.loop_dir, "LOOP_CLEANUP.md")],
            "loop: block %s (%s)" % (ctx.task.id,
                                     decision.get("classification", "open")),
            {"Loop-Status": "halted", "Loop-Task": ctx.task.id})
    elif changes.get("blocks"):
        ctx.plan.save()

    append_decision(ctx, decision, applied)
    ctx.events.emit("decision", tick=ctx.tick, task=ctx.task.id, decision=d,
                    classification=decision.get("classification", ""),
                    rejected=decision.get("rejected") or [])
    return NEXT_ACTION[d]
```

`mark_blocked` calls `ctx.plan.save()`, so the `blocks` fan-out is already on disk
by the time the defer commit runs; the `elif` only covers a non-terminal decision
that still blocked something.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -t tests/runner -v`
Expected: PASS — `test_judge.py` 34 tests, `test_split.py` 2 tests.

- [ ] **Step 6: Commit**

```bash
git add plugins/agent-loop/runner/judge.py plugins/agent-loop/runner/plan.py plugins/agent-loop/tests/runner/test_judge.py plugins/agent-loop/tests/runner/test_split.py
git commit -F - <<'EOF'
agent-loop: apply judge decisions — contract, LOOP_DECISIONS, deferral, split

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 4: Worker wrap-up on timeout, the judged tier, and SANDBOX after every Worker phase

**Files:**
- Create: `plugins/agent-loop/runner/prompts/worker_wrapup.md`
- Modify: `plugins/agent-loop/runner/phases.py` (`TickContext`, `worker_tier`, `worker_cap`, full new `run_worker`)
- Modify: `plugins/agent-loop/runner/run.py` (`sandbox`, `run_worker_attempt`)
- Modify: `plugins/agent-loop/tests/runner/cfixtures.py` (stub helpers)
- Create: `plugins/agent-loop/tests/runner/test_resume.py`

**Interfaces:**
- Consumes: `claude_proc.run_phase(..., resume_session=...)`, `git_ops.changed_paths`, `git_ops.strays`, `git_ops.revert`, `config.model_for`, `config.phase_limit`.
- Produces: `phases.worker_tier(ctx) -> str`, `phases.worker_cap(ctx) -> int`, the new `phases.run_worker(ctx, contract, resume_session=None, wrapup=False)`, `run.sandbox(ctx, contract) -> list[str]`, `run.run_worker_attempt(ctx, contract) -> tuple[list[PhaseResult], dict]`, `cfixtures.stub_claude`, `cfixtures.stub_result`.
- **There is no `TIER_LADDER`.** An earlier draft stepped the tier up once per attempt; spec §11 item 4 replaced that with the Judge's call, so `worker_tier` reads `ctx.tier` and nothing in the runner derives a tier from an attempt number.
- **Requirement on plan A's `run_phase`:** it must spawn the child with `start_new_session=True` and send SIGTERM/SIGKILL to the **process group**. Otherwise a killed phase's orphaned child keeps the stdout pipe open and the harness blocks forever on EOF instead of timing out. `test_timeout_runs_a_wrapup_on_the_same_session` is the canary for this; if it hangs, that is the bug.
- `run.py`'s module body must stay import-safe (everything behind `def` / `if __name__ == "__main__":`) — these tests import it.

- [ ] **Step 1: Extend the scripted stub, then add the fixture helpers**

The stub protocol in the interfaces doc can print a transcript, sleep and set an
exit code, but it cannot write a file — and a real Scout writes
`runtime/sprint-<T>.json` while a real Worker writes `runtime/worker-result.json`
before anything else. Add one element to the protocol. In
`plugins/agent-loop/tests/fixtures/claude`, immediately after the stub prints its
`system/init` line and **before** it sleeps or prints the transcript (call the
zero-padded invocation number `$n`):

```bash
side="$STUB_SCRIPT/$n.sh"
if [ -f "$side" ]; then
  # shellcheck source=/dev/null
  . "$side"
fi
```

and document it in the file's header comment:

```bash
#   NNN.sh     sourced after the init line and before the sleep/transcript; lets
#              a scripted phase write the runtime files a real phase would write
```

Then append to `plugins/agent-loop/tests/runner/cfixtures.py`:

```python
def stub_result(payload, model="sonnet"):
    """A stream-json transcript whose `result` text carries a fenced json block."""
    text = "```json\n" + json.dumps(payload) + "\n```"
    lines = [
        json.dumps({"type": "assistant",
                    "message": {"id": "msg_1", "model": model,
                                "content": [{"type": "text", "text": "ok"}],
                                "usage": {"input_tokens": 10, "output_tokens": 5}}}),
        json.dumps({"type": "result", "subtype": "success", "is_error": False,
                    "result": text}),
    ]
    return "\n".join(lines) + "\n"


def stub_claude(tmp, files):
    """Put the scripted stub on PATH as `claude`. Returns env overrides.

    `files` maps script file names ("001.jsonl", "001.sleep", "002.exit") to
    their contents, per the stub protocol in the interfaces doc.
    """
    bindir = os.path.join(tmp, "bin")
    script = os.path.join(tmp, "stub-script")
    os.makedirs(bindir)
    os.makedirs(script)
    os.symlink(os.path.join(PLUGIN_ROOT, "tests", "fixtures", "claude"),
               os.path.join(bindir, "claude"))
    for name, body in files.items():
        _write(os.path.join(script, name), body)
    return {"PATH": bindir + os.pathsep + os.environ.get("PATH", ""),
            "STUB_SCRIPT": script,
            "STUB_LOG": os.path.join(tmp, "stub.log"),
            "STUB_DELAY": "0"}
```

- [ ] **Step 2: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_resume.py`:

```python
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import cfixtures  # noqa: F401
from runner import phases
from runner import run as runner_run


def worker_result(status="partial", checkpoint="register the mixin"):
    return {"task": "T60", "status": status, "files_touched": [], "summary": "s",
            "checkpoint": checkpoint, "next_steps": ["register it"]}


def seed_worker_result(runtime, status="partial", checkpoint="register the mixin"):
    with open(os.path.join(runtime, "worker-result.json"), "w") as fh:
        json.dump(worker_result(status, checkpoint), fh)


def writes_worker_result(runtime, status="partial",
                         checkpoint="register the mixin"):
    """A stub `NNN.sh` body that writes worker-result.json, as a Worker does."""
    return "cat > %s <<'JSON'\n%s\nJSON\n" % (
        os.path.join(runtime, "worker-result.json"),
        json.dumps(worker_result(status, checkpoint)))


class TestWorkerTier(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cfg, self.plan, self.loop_dir, self.runtime = cfixtures.make_loop(self.tmp)
        self.ctx = cfixtures.make_ctx(self.cfg, self.plan, self.loop_dir,
                                      self.runtime)

    def test_the_tier_is_whatever_the_judge_armed_never_a_ladder(self):
        self.ctx.tier = None
        self.assertEqual(phases.worker_tier(self.ctx), "standard",
                         "nothing armed: the configured worker tier stands")
        self.ctx.attempt = 3
        self.assertEqual(phases.worker_tier(self.ctx), "standard",
                         "the attempt number never moves the tier (spec §11.4)")
        self.ctx.tier = "most-capable"
        self.assertEqual(phases.worker_tier(self.ctx), "most-capable")
        self.cfg.role_tiers["worker"] = "cheap"
        self.ctx.tier = None
        self.assertEqual(phases.worker_tier(self.ctx), "cheap")

    def test_the_cap_adds_the_judges_one_extension(self):
        self.assertEqual(phases.worker_cap(self.ctx), 1500, "worker_timeout")
        self.ctx.extend_cap_s = 600
        self.assertEqual(phases.worker_cap(self.ctx), 2100)


class _StubBase(unittest.TestCase):
    def script(self):
        """Stub script files for this class. `self.runtime` is already set."""
        return {}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cfg, self.plan, self.loop_dir, self.runtime = cfixtures.make_loop(self.tmp)
        self.cfg.limits["worker_timeout"] = 2
        self.cfg.limits["wrapup_timeout"] = 30
        self.ctx = cfixtures.make_ctx(self.cfg, self.plan, self.loop_dir, self.runtime)
        self.contract = cfixtures.make_contract()
        cfixtures.write_contract(self.ctx, self.contract)
        cfixtures.git_init(self.cfg.worktree)
        env = cfixtures.stub_claude(self.tmp, self.script())
        self.stub_log = env["STUB_LOG"]
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def log_text(self):
        if not os.path.exists(self.stub_log):
            return ""
        return cfixtures.read(self.stub_log)


class TestWrapup(_StubBase):
    def script(self):
        return {
            "001.sh": writes_worker_result(self.runtime, "partial", "half done"),
            "001.sleep": "20",
            "002.sh": writes_worker_result(self.runtime, "partial",
                                           "register the mixin in index.ts"),
            "002.jsonl": cfixtures.stub_result(
                {"status": "partial", "summary": "out of time"}),
        }

    def test_timeout_runs_a_wrapup_on_the_same_session(self):
        results, payload = runner_run.run_worker_attempt(self.ctx, self.contract)

        self.assertEqual(len(results), 2)
        self.assertTrue(results[0].timed_out)
        self.assertEqual(results[0].phase, "worker")
        self.assertEqual(results[1].phase, "worker-wrapup")
        self.assertFalse(results[1].timed_out)

        self.assertIn(results[0].session_id, self.log_text(),
                      "the wrap-up must --resume the killed worker's session")
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["checkpoint"], "register the mixin in index.ts",
                         "the payload is the wrap-up's checkpoint, not the "
                         "killed worker's")

        evs = [e for e in cfixtures.read_events(self.loop_dir)
               if e["type"] == "resume"]
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["kind"], "wrapup")
        self.assertEqual(evs[0]["task"], "T60")

    def test_sandbox_reverts_strays_after_a_killed_worker(self):
        stray = os.path.join(self.cfg.worktree, "apps", "frontend")
        os.makedirs(stray)
        with open(os.path.join(stray, "oops.tsx"), "w") as fh:
            fh.write("// written outside the allow_list\n")
        runner_run.run_worker_attempt(self.ctx, self.contract)
        self.assertFalse(os.path.exists(os.path.join(stray, "oops.tsx")),
                         "SANDBOX runs after a killed Worker too (spec §6.4)")


class TestNoTimeout(_StubBase):
    def script(self):
        return {"001.sh": writes_worker_result(self.runtime, "complete", ""),
                "001.jsonl": cfixtures.stub_result(
                    {"status": "complete", "summary": "done"})}

    def test_a_clean_worker_runs_no_wrapup(self):
        results, payload = runner_run.run_worker_attempt(self.ctx, self.contract)
        self.assertEqual([r.phase for r in results], ["worker"])
        self.assertEqual(payload["status"], "complete")
        self.assertEqual(self.log_text(), "")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -t tests/runner -v`
Expected: FAIL — `AttributeError: module 'runner.phases' has no attribute 'worker_tier'` and `module 'runner.run' has no attribute 'run_worker_attempt'`.

- [ ] **Step 4: Write the wrap-up prompt**

Create `plugins/agent-loop/runner/prompts/worker_wrapup.md`:

````markdown
# Role: Worker — wrap-up

You are out of time. Your Worker phase was stopped at its deadline. This
continuation exists for one reason: so the next attempt inherits what you know
instead of starting from nothing.

You have **8 turns**. Spend them on the checkpoint, not on the work.

Task: {{task_row}}

Do exactly this, then stop:

1. Rewrite `{{loop_dir}}/runtime/worker-result.json` so it describes the tree as
   it is right now, not as you meant it to be:

   ```json
   {
     "task": "<task id>",
     "status": "complete|partial",
     "files_touched": ["<path>"],
     "summary": "what is done and actually verified",
     "checkpoint": "the next concrete action, in enough detail that a session with no memory of this one can carry on: the file, the symbol, the approach you had settled on, and the approach you had already ruled out and why",
     "next_steps": ["<ordered remaining steps>"]
   }
   ```

2. `status` is `complete` only if every success criterion in the contract is met
   **and** you ran the verification commands and saw them pass. If you are not
   sure, it is `partial`. A false `complete` costs the loop a whole tick and
   lands broken code on the branch.

Do not start new work. Do not open files you have not already read in this
session. Do not run the verification pipeline. Do not commit — the harness
commits. Never call `AskUserQuestion` or `EnterPlanMode`.

Your last checkpoint, for reference:

{{checkpoint}}

End your reply with exactly one fenced json block:

```json
{"status": "complete|partial", "summary": "one sentence"}
```
````

- [ ] **Step 5: Write the implementation**

In `plugins/agent-loop/runner/phases.py`, add the two `TickContext` fields:

```python
@dataclass
class TickContext:
    cfg: "LoopConfig"
    plan: "Plan"
    task: "Task"
    loop_dir: str
    runtime_dir: str
    events: "EventLog"
    tick: int
    attempt: int = 1
    resume_session: Optional[str] = None
    resume_count: int = 0
    last_session: Optional[str] = None
    tier: Optional[str] = None        # armed by the Judge; None = configured
    extend_cap_s: int = 0             # the Judge's one cap extension
```

Add the tier resolver, the cap, and the contract reader:

```python
WORKER_MAX_TURNS = 0      # 0 => run_phase omits --max-turns
WRAPUP_MAX_TURNS = 8


def worker_tier(ctx):
    """The tier this attempt runs at: whatever the Judge armed on the context,
    else the configured worker tier.

    There is deliberately no ladder. The 2026-09-11 run retried the same model
    6 times out of 7 while a prompt asked it not to, and the first fix was to
    step the tier up per attempt; spec §6 step 2 and §11 item 4 replace that
    with the Judge's reading of the checkpoint. The harness enforces only the
    bounds (`max_attempts`, `worker_resume_max`, one cap extension), all in
    `judge.validate_decision`.
    """
    return (getattr(ctx, "tier", None)
            or ctx.cfg.role_tiers.get("worker") or "standard")


def worker_cap(ctx):
    """The Worker's wall clock: `worker_timeout` plus the Judge's extension."""
    return phase_limit(ctx.cfg, "worker") + max(
        0, int(getattr(ctx, "extend_cap_s", 0) or 0))


def contract_json(ctx, contract):
    """The contract exactly as it sits on disk — the one document a Worker sees."""
    path = os.path.join(ctx.runtime_dir, "sprint-%s.json" % contract.task)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (IOError, OSError):
        return json.dumps({"task": contract.task,
                           "allow_list": list(contract.allow_list)}, indent=2)


def worker_checkpoint(ctx):
    try:
        with open(os.path.join(ctx.runtime_dir, "worker-result.json"), "r") as fh:
            return json.load(fh).get("checkpoint", "") or "(no checkpoint written)"
    except (IOError, OSError, ValueError):
        return "(no checkpoint written)"
```

Replace `run_worker` entirely with:

```python
def run_worker(ctx, contract, resume_session=None, wrapup=False):
    """One Worker phase. Three shapes: fresh, resumed, and out-of-time wrap-up.

    Returns (PhaseResult, worker_result_payload). The payload is whatever is in
    runtime/worker-result.json afterwards, `{}` if the Worker never wrote it.
    """
    runtime = ctx.runtime_dir
    result_path = os.path.join(runtime, "worker-result.json")

    if wrapup:
        role = "worker-wrapup"
        prompt = render_prompt("worker_wrapup",
                               loop_dir=ctx.loop_dir,
                               worktree=ctx.cfg.worktree,
                               task_row=ctx.task.raw,
                               contract_json=contract_json(ctx, contract),
                               checkpoint=worker_checkpoint(ctx))
        timeout_s = phase_limit(ctx.cfg, "wrapup")
        max_turns = WRAPUP_MAX_TURNS
    elif resume_session:
        role = "worker"
        prompt = render_prompt("worker_resume",
                               loop_dir=ctx.loop_dir,
                               worktree=ctx.cfg.worktree,
                               task_row=ctx.task.raw,
                               contract_json=contract_json(ctx, contract),
                               checkpoint=worker_checkpoint(ctx),
                               minutes_left=str(max(1, worker_cap(ctx) // 60)))
        timeout_s = worker_cap(ctx)
        max_turns = WORKER_MAX_TURNS
    else:
        role = "worker"
        if os.path.exists(result_path):
            os.remove(result_path)          # a fresh attempt inherits no checkpoint
        prompt = render_prompt("worker",
                               loop_dir=ctx.loop_dir,
                               worktree=ctx.cfg.worktree,
                               task_row=ctx.task.raw,
                               contract_json=contract_json(ctx, contract),
                               learnings_digest=learnings_digest(ctx),
                               knowledge=knowledge_text(ctx))
        timeout_s = worker_cap(ctx)
        max_turns = WORKER_MAX_TURNS

    result = claude_proc.run_phase(
        phase=role,
        model=model_for(ctx.cfg, worker_tier(ctx)),
        prompt=prompt,
        cwd=ctx.cfg.worktree,
        timeout_s=timeout_s,
        max_turns=max_turns,
        max_budget_usd=float(ctx.cfg.limits.get("worker_budget_usd", 6)),
        env=dict(os.environ),
        events=ctx.events,
        tick=ctx.tick,
        role=role,
        activity_path=os.path.join(runtime, "last-activity"),
        resume_session=resume_session,
    )

    payload = {}
    try:
        with open(result_path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (IOError, OSError, ValueError):
        payload = {}
    return result, payload
```

`learnings_digest(ctx)` and `knowledge_text(ctx)` are plan A's existing helpers
for the `{{learnings_digest}}` / `{{knowledge}}` placeholders; keep whatever
plan A called them and leave that branch of the prompt exactly as plan A built
it — the wrap-up and resume branches are the new work.

In `plugins/agent-loop/runner/run.py`:

```python
def sandbox(ctx, contract):
    """Revert everything outside allow_list. Runs after EVERY Worker phase,
    killed or not (spec §6.4), so a killed phase never leaves the tree
    ambiguous."""
    changed = git_ops.changed_paths(ctx.cfg.worktree)
    stray = git_ops.strays(changed, contract.allow_list)
    if stray:
        git_ops.revert(ctx.cfg.worktree, stray)
        ctx.events.emit("sandbox", tick=ctx.tick, task=ctx.task.id,
                        reverted=len(stray), paths=stray[:20])
    return stray


def run_worker_attempt(ctx, contract):
    """One Worker phase plus, on timeout, its bounded wrap-up continuation."""
    results = []
    result, payload = phases.run_worker(ctx, contract,
                                        resume_session=ctx.resume_session)
    results.append(result)
    sandbox(ctx, contract)

    if result.timed_out and result.session_id:
        ctx.events.emit("resume", tick=ctx.tick, task=ctx.task.id,
                        attempt=ctx.attempt, kind="wrapup",
                        session=result.session_id)
        wrap, payload = phases.run_worker(ctx, contract,
                                          resume_session=result.session_id,
                                          wrapup=True)
        results.append(wrap)
        sandbox(ctx, contract)

    ctx.resume_session = None
    if result.session_id:
        ctx.last_session = result.session_id
    return results, payload
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -t tests/runner -v`
Expected: PASS — `test_resume.py` contributes 5 tests. `TestWrapup` takes roughly 3 s (a 2 s worker budget against a 20 s stub sleep). If it hangs instead of failing, fix `run_phase` to use `start_new_session=True` and signal the process group.

- [ ] **Step 7: Commit**

```bash
git add plugins/agent-loop/runner/phases.py plugins/agent-loop/runner/run.py plugins/agent-loop/runner/prompts/worker_wrapup.md plugins/agent-loop/tests/runner/cfixtures.py plugins/agent-loop/tests/runner/test_resume.py
git commit -F - <<'EOF'
agent-loop: worker wrap-up on timeout, judged tier, sandbox after every worker phase

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 5: Resume the Worker — on the Judge's word, inside the harness's budget

**Files:**
- Create: `plugins/agent-loop/runner/prompts/worker_resume.md`
- Modify: `plugins/agent-loop/runner/run.py` (`worker_complete`, `next_worker_action`, `start_resume`)
- Modify: `plugins/agent-loop/tests/runner/test_resume.py` (append)

**Interfaces:**
- Consumes: `phases.run_worker(ctx, contract, resume_session=...)`, `phases.worker_tier`, `cfg.limits["worker_resume_max"]`, `ctx.resume_session`, `ctx.resume_count`, `ctx.last_session`, `ctx.tier`.
- Produces: `run.worker_complete(payload) -> bool`, `run.next_worker_action(ctx, payload, results) -> str` returning `gate|judge`, `run.resumable(ctx) -> bool`, `run.start_resume(ctx, results) -> None`.
- **Substantive change from the earlier draft:** a partial Worker is no longer resumed automatically. `next_worker_action` returns `judge`, the Judge reads the checkpoint and decides `resume|escalate|split` (spec §6 step 2), and `start_resume` runs only when the Judge said `resume`. `worker_resume_max` still bounds it — `judge.validate_decision` refuses a `resume` once the budget is spent, and `resumable(ctx)` is the same predicate for the main loop.

- [ ] **Step 1: Write the failing test**

Append to `plugins/agent-loop/tests/runner/test_resume.py` (above `if __name__`):

```python
class TestNextWorkerAction(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cfg, self.plan, self.loop_dir, self.runtime = cfixtures.make_loop(self.tmp)
        self.ctx = cfixtures.make_ctx(self.cfg, self.plan, self.loop_dir, self.runtime)

    def _result(self, session_id="sid-1"):
        from runner.claude_proc import PhaseResult
        return PhaseResult(phase="worker", model="sonnet", rc=None, killed=True,
                           timed_out=True, session_id=session_id,
                           transcript_path=None, started=1, ended=2,
                           result_text="", usage_by_model={}, tool_calls=1,
                           last_activity=2)

    def test_complete_goes_to_the_gate(self):
        self.assertEqual(
            runner_run.next_worker_action(self.ctx, {"status": "complete"},
                                          [self._result()]), "gate")

    def test_partial_always_goes_to_the_judge_never_straight_to_a_resume(self):
        self.assertEqual(self.ctx.resume_count, 0)
        self.assertEqual(
            runner_run.next_worker_action(self.ctx, {"status": "partial"},
                                          [self._result()]), "judge")
        self.assertEqual(self.ctx.last_session, "sid-1",
                         "the session is remembered so the Judge may resume it")

    def test_resumable_is_the_budget_predicate_the_judge_is_held_to(self):
        self.ctx.last_session = "sid-1"
        self.assertTrue(runner_run.resumable(self.ctx))
        self.ctx.resume_count = 1        # worker_resume_max is 1
        self.assertFalse(runner_run.resumable(self.ctx))
        self.ctx.resume_count = 0
        self.ctx.last_session = None
        self.assertFalse(runner_run.resumable(self.ctx))

    def test_missing_payload_is_not_complete(self):
        self.assertFalse(runner_run.worker_complete({}))
        self.assertFalse(runner_run.worker_complete(None))
        self.assertFalse(runner_run.worker_complete({"status": "partial"}))
        self.assertTrue(runner_run.worker_complete({"status": "complete"}))

    def test_start_resume_arms_the_next_attempt(self):
        results = [self._result("sid-9")]
        self.ctx.last_session = "sid-9"
        runner_run.start_resume(self.ctx, results)
        self.assertEqual(self.ctx.resume_session, "sid-9")
        self.assertEqual(self.ctx.resume_count, 1)
        evs = [e for e in cfixtures.read_events(self.loop_dir)
               if e["type"] == "resume" and e.get("kind") == "resume"]
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["attempt"], 1)


class TestResumeAttempt(_StubBase):
    def script(self):
        return {"001.jsonl": cfixtures.stub_result(
            {"status": "complete", "summary": "finished from the checkpoint"},
            model="opus")}

    def test_resume_passes_the_session_and_runs_at_the_armed_tier(self):
        seed_worker_result(self.runtime)
        self.ctx.attempt = 2
        self.ctx.tier = "most-capable"        # what the Judge armed in apply()
        self.ctx.resume_session = "sid-prev"
        results, payload = runner_run.run_worker_attempt(self.ctx, self.contract)

        self.assertEqual(len(results), 1)
        self.assertIn("sid-prev", self.log_text())
        self.assertEqual(payload["status"], "partial",
                         "the stub cannot write worker-result.json; the file is "
                         "still the seeded one")

        starts = [e for e in cfixtures.read_events(self.loop_dir)
                  if e["type"] == "role_start" and e["role"] == "worker"]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]["model"], "opus",
                         "the tier the Judge armed, not a function of the "
                         "attempt number (spec §6 step 2)")
        self.assertEqual(results[0].model, "opus")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -t tests/runner -v`
Expected: FAIL — `AttributeError: module 'runner.run' has no attribute 'next_worker_action'`, and `TestResumeAttempt` fails to render the missing `worker_resume` prompt.

- [ ] **Step 3: Write the resume prompt**

Create `plugins/agent-loop/runner/prompts/worker_resume.md`:

````markdown
# Role: Worker — resume

This is your own session, continued. You have **{{minutes_left}} minutes**.

Task: {{task_row}}

Your checkpoint:

{{checkpoint}}

Contract — still the only thing you may touch:

```json
{{contract_json}}
```

Rules:

- Start from `next_steps` in your checkpoint. Do not re-read files you already
  read in this session and do not re-derive what the checkpoint already states.
  Re-deriving what you already knew is how the last run burned a 30-minute
  budget twice.
- Edit only paths matching `allow_list`. Everything else is reverted the moment
  this phase ends, including if it is killed.
- Anything the Judge decided since your last turn is at the end of
  `scout_notes` in the contract above, tagged `JUDGE`. That is an instruction,
  not a suggestion.
- Update `{{loop_dir}}/runtime/worker-result.json` as you go, not at the end. If
  you are killed again, that file is the only thing that survives.
- Run the contract's `verification` commands and see them pass before you claim
  `complete`.
- Do not commit — the harness commits. Never call `AskUserQuestion` or
  `EnterPlanMode`.

End your reply with exactly one fenced json block:

```json
{"status": "complete|partial", "summary": "one sentence"}
```
````

- [ ] **Step 4: Write the implementation**

Append to `plugins/agent-loop/runner/run.py`:

```python
def worker_complete(payload):
    return bool(payload) and payload.get("status") == "complete"


def resumable(ctx):
    """Is a --resume still affordable? The same predicate `judge.validate_decision`
    applies to a `resume` decision, so the Judge can never be told yes here and
    no there."""
    return bool(ctx.last_session) and ctx.resume_count < int(
        ctx.cfg.limits.get("worker_resume_max", 1))


def next_worker_action(ctx, payload, results):
    """gate | judge — what happens after a Worker attempt (spec §6 step 2).

    A partial Worker is never resumed automatically. The Judge reads the
    checkpoint and decides resume / escalate / split; the harness only keeps the
    session id so a `resume` decision has something to resume.
    """
    if worker_complete(payload):
        return "gate"
    ctx.last_session = ctx.last_session or (
        results[0].session_id if results else None)
    return "judge"


def start_resume(ctx, results):
    """Arm the next attempt as a --resume of the same session. Called only when
    the Judge decided `resume` (spec §6 step 2)."""
    session = ctx.last_session or (results[0].session_id if results else None)
    ctx.resume_session = session
    ctx.resume_count += 1
    ctx.events.emit("resume", tick=ctx.tick, task=ctx.task.id,
                    attempt=ctx.attempt, kind="resume", session=session)
```

`run_worker_attempt` clears `ctx.resume_session` after each attempt, so the main
loop re-arms it through `start_resume` for exactly the attempts the Judge asked
for.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -t tests/runner -v`
Expected: PASS — `test_resume.py` contributes 11 tests.

- [ ] **Step 6: Commit**

```bash
git add plugins/agent-loop/runner/run.py plugins/agent-loop/runner/prompts/worker_resume.md plugins/agent-loop/tests/runner/test_resume.py
git commit -F - <<'EOF'
agent-loop: resume a timed-out worker when the judge says so

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 6: The main-loop failure path

**Files:**
- Modify: `plugins/agent-loop/runner/run.py` (delete plan A's stand-in; add `failure_signature`, `handle_failure`, `execute_task`)
- Modify: `plugins/agent-loop/tests/runner/test_judge.py` (append)

**Interfaces:**
- Consumes: `judge.build_input`, `judge.decide`, `judge.apply`, `judge.close_attempt`, `judge.attempts_for`, `judge.Failure`, `judge.mark_blocked`, `cfg.blocker_policy`, `cfg.limits["max_attempts"]`, `task_state.TaskState.begin_attempt`.
- Produces: `run.failure_signature(kind, detail) -> str`, `run.handle_failure(ctx, contract, kind, detail, verdict=None, phase_results=None) -> str`, `run.FAILURE_KINDS`.
- **Deletes:** plan A's stand-in failure path in `run.py` — the block marked `# plan C` that did "NEEDS_WORK → escalate once; else defer to LOOP_CLEANUP and mark `[!]`". Every `[!]` now goes through `judge.mark_blocked`; grep `run.py` for `set_state(` with `"blocked"` afterwards and make sure `judge.mark_blocked` is the only caller.

- [ ] **Step 1: Write the failing test**

Append to `plugins/agent-loop/tests/runner/test_judge.py`:

```python
class TestFailurePath(_PolicyBase):
    def setUp(self):
        _PolicyBase.setUp(self)
        cfixtures.git_init(self.cfg.worktree)
        cfixtures.write_contract(self.ctx, self.contract)

    def _judge_says(self, payload):
        from unittest import mock
        from runner.claude_proc import PhaseResult
        return mock.patch.object(
            judge.claude_proc, "run_phase", return_value=PhaseResult(
                phase="judge", model="opus", rc=0, killed=False, timed_out=False,
                session_id="sid-judge", transcript_path=None, started=1, ended=2,
                result_text="```json\n%s\n```" % json.dumps(payload),
                usage_by_model={}, tool_calls=0, last_activity=2))

    def test_signature_is_kind_plus_first_detail_line(self):
        from runner import run as runner_run
        self.assertEqual(
            runner_run.failure_signature("gate", "tsc failed\nand more"),
            "gate:tsc failed")
        self.assertEqual(runner_run.failure_signature("needs-work", ""),
                         "needs-work:")

    def test_widen_decision_returns_retry_and_closes_the_attempt(self):
        from runner import run as runner_run
        from runner.task_state import TaskState
        state = TaskState.load(self.runtime, "T60")
        state.begin_attempt("standard")
        state.save()
        with self._judge_says({
                "decision": "widen", "classification": "self-imposed",
                "rationale": "scout-sourced constraint",
                "alternatives": ["halt"], "reversal": "revert the contract",
                "changes": {"forbidden_remove":
                            ["packages/api/src/requests/activepipe/index.ts"]}}):
            action = runner_run.handle_failure(
                self.ctx, self.contract, "gate", "tsc failed\nTS2339 line 4")
        self.assertEqual(action, "retry")

        attempts = judge.attempts_for(self.runtime, "T60")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["n"], 1)
        self.assertEqual(attempts[0]["tier"], "standard")
        self.assertEqual(attempts[0]["outcome"], "gate:tsc failed")
        self.assertEqual(attempts[0]["judge"]["decision"], "widen")
        self.assertNotIn("_phase", attempts[0]["judge"])
        self.assertIn("judge", [p["phase"] for p in attempts[0]["phase_results"]],
                      "the Judge phase itself is recorded on the attempt")
        self.assertFalse(os.path.exists(
            os.path.join(self.runtime, "attempts-T60.json")))

    def test_second_same_signature_failure_is_flagged_to_the_judge(self):
        from runner import run as runner_run
        seen = {}

        def capture(ctx, inp):
            seen["failure"] = inp.failure
            seen["attempts"] = len(inp.attempts)
            return judge._normalize({"decision": "escalate",
                                     "classification": "capability",
                                     "rationale": "the same tier failed twice",
                                     "changes": {"tier": "most-capable"}})

        cfixtures.seed_attempt(self.runtime, "T60", 1, "gate:tsc failed")
        from unittest import mock
        from runner.task_state import TaskState
        state = TaskState.load(self.runtime, "T60")
        state.begin_attempt("standard")
        state.save()
        with mock.patch.object(judge, "decide", side_effect=capture):
            self.ctx.attempt = 2
            action = runner_run.handle_failure(
                self.ctx, self.contract, "gate", "tsc failed")
        self.assertEqual(action, "escalate")
        self.assertIn("repeat", seen["failure"])
        self.assertEqual(seen["attempts"], 1)

    def test_the_attempt_cap_turns_a_stale_retry_into_a_defer(self):
        from runner import run as runner_run
        self.ctx.attempt = 3                       # max_attempts is 3
        with self._judge_says({
                "decision": "defer", "classification": "capability",
                "rationale": "three attempts, no progress",
                "alternatives": ["split"], "reversal": "clear the [!]",
                "changes": {}}):
            self.assertEqual(runner_run.handle_failure(
                self.ctx, self.contract, "gate", "tsc failed"), "defer")

    def test_halt_obeys_blocker_policy(self):
        from runner import run as runner_run
        halt = {"decision": "halt", "classification": "open",
                "rationale": "uncommitted work would be lost",
                "alternatives": ["defer"], "reversal": "resume", "changes": {}}
        with self._judge_says(halt):
            self.assertEqual(runner_run.handle_failure(
                self.ctx, self.contract, "blocker", "api missing"), "halt")

        self.cfg.blocker_policy = "halt"
        with self._judge_says(halt):
            self.assertEqual(runner_run.handle_failure(
                self.ctx, self.contract, "blocker", "api missing"), "halt")

    def test_defer_continues_under_continue_independent(self):
        from runner import run as runner_run
        with self._judge_says({
                "decision": "defer", "classification": "open",
                "rationale": "the spec is silent",
                "alternatives": ["guess"], "reversal": "clear blocked_by",
                "changes": {"default_choice": "internal/", "blocks": ["T61"]}}):
            self.assertEqual(runner_run.handle_failure(
                self.ctx, self.contract, "needs-work", "criterion 2 unmet"),
                "defer")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -t tests/runner -v`
Expected: FAIL — `AttributeError: module 'runner.run' has no attribute 'failure_signature'`.

- [ ] **Step 3: Write the implementation**

Replace plan A's stand-in failure block in `plugins/agent-loop/runner/run.py` with:

```python
FAILURE_KINDS = ("invalid-contract", "gate", "render", "fidelity", "needs-work",
                 "blocker", "worker-incomplete")


def failure_signature(kind, detail):
    """`kind:first-line-of-detail`, capped. Two identical signatures on one task
    mean the last decision did not hold."""
    lines = (detail or "").strip().splitlines()
    return "%s:%s" % (kind, (lines[0][:120].strip() if lines else ""))


def handle_failure(ctx, contract, kind, detail, verdict=None, phase_results=None):
    """Every failure path in the loop ends here. Returns the next action:
    retry | escalate | resume | split | defer | halt (spec §7)."""
    signature = failure_signature(kind, detail)
    previous = judge.attempts_for(ctx.runtime_dir, ctx.task.id)
    repeat = any(a.get("outcome") == signature for a in previous)
    if repeat:
        kind = "repeat"
        detail = "same failure signature as an earlier attempt: %s" % signature

    failure = judge.Failure(kind=kind, detail=detail, verdict=verdict or {},
                            signature=signature, repeat=repeat)
    inp = judge.build_input(ctx, contract, failure)
    decision = judge.decide(ctx, inp)
    judge_phase = decision.pop("_phase", None)
    action = judge.apply(ctx, contract, decision)
    # The attempt's other phases were recorded as they ran (plan A, spec §14);
    # this closes it with the Judge's own phase, the signature and the decision.
    judge.close_attempt(ctx, signature, decision=decision,
                        judge_phase=judge_phase)

    if action == "halt" and ctx.cfg.blocker_policy != "halt":
        # `halt` still stops this task; the blocker policy decides whether the
        # loop moves on to independent work (spec §12.4 / §7).
        ctx.events.emit("task_status", tick=ctx.tick, task=ctx.task.id,
                        status="blocked", policy=ctx.cfg.blocker_policy)
    return action
```

`run.py` needs `from runner import judge, phases, git_ops, incidents` at the top.

Then, in the execute branch of the main loop, route every failure through it.
The `continue`s below re-enter the **attempt loop, whose body starts at WORK** —
not at SCOUT. The contract is already validated and may have just been widened;
re-scouting would throw the Judge's decision away and reproduce the 2026-09-11
tick-85 failure, where the Scout rewrote its own contract twice inside one tick:

Every attempt opens with

```python
    state = TaskState.load(ctx.runtime_dir, ctx.task.id)
    state.begin_attempt(phases.worker_tier(ctx))
    state.save()
```

— plan A owns the call site, plan C supplies the tier through `ctx.tier`, and
`handle_failure` closes the same attempt through `judge.close_attempt`. An
attempt that is opened and never closed is exactly what Task 8's boot rule
reconciles.

```python
    #   scout invalid twice / gate / render / fidelity / NEEDS_WORK / BLOCKER /
    #   worker not complete / second same-signature failure
    action = handle_failure(ctx, contract, kind, detail,
                            verdict=verdict, phase_results=tick_phases)
    max_attempts = int(ctx.cfg.limits.get("max_attempts", 3))
    if action in ("retry", "escalate", "resume") and ctx.attempt >= max_attempts:
        # Belt to the Judge's braces: validate_decision already refuses these on
        # the last attempt, so this only fires if max_attempts changed under a
        # running loop. Never spend attempt 4.
        judge.write_cleanup_entry(ctx, {
            "classification": "capability",
            "rationale": "the attempt cap (max_attempts=%d) is spent"
                         % max_attempts, "changes": {}})
        judge.mark_blocked(ctx, {"decision": "defer"})
        action = "defer"
    if action == "retry":
        ctx.attempt += 1
        continue                         # same task, contract may have widened
    if action == "escalate":
        ctx.attempt += 1
        ctx.resume_session = None        # fresh session at the Judge's tier
        continue
    if action == "resume":
        start_resume(ctx, tick_phases)   # the Judge asked for it; §6 step 2
        ctx.attempt += 1
        continue
    if action == "split":
        return "continue"                # the sub-tasks are eligible now
    if action == "defer":
        return "continue" if ctx.cfg.blocker_policy == "continue-independent" \
            else "halt"
    if action == "halt":
        return "continue" if ctx.cfg.blocker_policy == "continue-independent" \
            else "halt"
```

`plan.mode()` returns `"stuck"` when a deferral leaves nothing eligible, so
`continue` after a defer still ends the loop cleanly rather than spinning.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -t tests/runner -v`
Expected: PASS — `test_judge.py` contributes 40 tests.

- [ ] **Step 5: Verify nothing else writes `[!]`**

Run: `cd plugins/agent-loop && grep -rn '"blocked"' runner/ | grep -v judge.py`
Expected: no `set_state(..., "blocked")` call outside `judge.mark_blocked`. Delete any survivor from plan A's stand-in.

- [ ] **Step 6: Commit**

```bash
git add plugins/agent-loop/runner/run.py plugins/agent-loop/tests/runner/test_judge.py
git commit -F - <<'EOF'
agent-loop: route every failure through the judge before marking a task blocked

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 7: Medic reads the phase timeline

**Files:**
- Modify: `plugins/agent-loop/runner/incidents.py` (`raise_incident` gains `phases`)
- Modify: `plugins/agent-loop/runner/run.py` (raise `phase-timeout`)
- Modify: `plugins/agent-loop/skills/agent-loop-medic/SKILL.md`
- Modify: `plugins/agent-loop/tests/medic.contract.sh`
- Modify: `plugins/agent-loop/tests/runner/test_resume.py` (append)

**Interfaces:**
- Produces: `incidents.raise_incident(ctx, kind, severity, detail, phase_results=None) -> str` (the incident id). `incident-<id>.json` gains `phases: [{phase, model, started, ended, rc, session}]`.
- New incident kinds: `phase-timeout` (severity `warn` — raised only once the resume budget is spent, because the runner handles the rest itself) and `judge-loop` (severity `error` — two Judge decisions on one task have already failed).

- [ ] **Step 1: Write the failing tests**

Append to `plugins/agent-loop/tests/runner/test_resume.py`:

```python
class TestIncidentPhases(_StubBase):
    def script(self):
        return {"001.sh": writes_worker_result(self.runtime, "partial", "half"),
                "001.sleep": "20",
                "002.jsonl": cfixtures.stub_result(
                    {"status": "partial", "summary": "out of time"})}

    def test_phase_timeout_incident_carries_the_timeline(self):
        from runner import incidents
        self.cfg.limits["worker_resume_max"] = 0
        self.ctx.last_session = "sid-1"
        results, _ = runner_run.run_worker_attempt(self.ctx, self.contract)

        files = [f for f in os.listdir(self.runtime)
                 if f.startswith("incident-") and f.endswith(".json")]
        self.assertEqual(len(files), 1, files)
        data = json.load(open(os.path.join(self.runtime, files[0])))
        self.assertEqual(data["kind"], "phase-timeout")
        self.assertEqual(data["severity"], "warn")
        self.assertEqual([p["phase"] for p in data["phases"]],
                         ["worker", "worker-wrapup"])
        self.assertEqual(data["phases"][0]["rc"], results[0].rc)
        self.assertEqual(data["phases"][0]["session"], results[0].session_id)
        self.assertIn("model", data["phases"][0])
        self.assertIn("started", data["phases"][0])
        self.assertTrue(callable(incidents.raise_incident))
```

Append to `plugins/agent-loop/tests/medic.contract.sh`, just before `assert_summary`:

```bash
# v3: the incident carries the tick's phase timeline and the medic must read it
has '"phases"'
has 'name the phase that consumed the budget'
grep -qE '^\| `phase-timeout` \|' "$F"; assert_true $? "decision table has a row for phase-timeout"
grep -qE '^\| `judge-loop` \|' "$F"; assert_true $? "decision table has a row for judge-loop"
has 'kind` + `phase` + `task`'
has 'LOOP_DECISIONS.md'
# v3: the per-task state file, not the v2 attempts file
has 'runtime/task-<TASK>.json'
if grep -q 'attempts-' "$F"; then rc=1; else rc=0; fi
assert_true $rc "the skill names no attempts-<T>.json (replaced by task-<T>.json)"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -t tests/runner -v && bash tests/medic.contract.sh`
Expected: the python test FAILs (no incident file written), and `medic.contract.sh` FAILs six assertions.

- [ ] **Step 3: Write the incident timeline**

Replace `raise_incident` in `plugins/agent-loop/runner/incidents.py` with:

```python
def raise_incident(ctx, kind, severity, detail, phase_results=None):
    """Write runtime/incident-<id>.json and emit the incident event.

    `phases` is the tick's phase timeline (spec §8): the medic names the phase
    that consumed the budget before it diagnoses anything.
    """
    seq_path = os.path.join(ctx.runtime_dir, "incidentseq")
    n = _next_seq(seq_path)
    incident_id = "i-%03d" % n
    payload = {
        "id": incident_id,
        "kind": kind,
        "severity": severity,
        "detail": detail,
        "tick": ctx.tick,
        "t": int(time.time()),
        "task": ctx.task.id if getattr(ctx, "task", None) else "",
        "phases": [
            {"phase": p.phase, "model": p.model, "started": p.started,
             "ended": p.ended, "rc": p.rc, "session": p.session_id}
            for p in (phase_results or [])
        ],
    }
    path = os.path.join(ctx.runtime_dir, "incident-%s.json" % incident_id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    os.replace(tmp, path)
    ctx.events.emit("incident", tick=ctx.tick, id=incident_id, kind=kind,
                    severity=severity, detail=detail)
    return incident_id
```

Keep plan A's `_next_seq` and its medic-dispatch function unchanged; only the
payload and the `phase_results` keyword are new.

In `handle_failure` (run.py), immediately before `decision = judge.decide(...)`:

```python
    if judge.judge_decision_count(previous) >= 2:
        incidents.raise_incident(
            ctx, kind="judge-loop", severity="error",
            detail="two Judge decisions on %s have already failed; the last "
                   "failure signature is %s" % (ctx.task.id, signature),
            phase_results=phase_results)
```

The Judge is still asked — policy forces it to `defer` or `halt` at this point
(Task 2) — but a human now has an incident telling them the loop is arguing with
itself.

In `run_worker_attempt` (run.py), after the wrap-up block and before the return:

```python
    if result.timed_out and ctx.resume_count >= int(
            ctx.cfg.limits.get("worker_resume_max", 1)):
        incidents.raise_incident(
            ctx, kind="phase-timeout", severity="warn",
            detail="%s timed out after %ds on attempt %d; the resume budget is "
                   "spent, so the Judge decides next"
                   % (result.phase, max(0, result.ended - result.started),
                      ctx.attempt),
            phase_results=results)
```

- [ ] **Step 4: Update the medic skill**

In `plugins/agent-loop/skills/agent-loop-medic/SKILL.md`:

1. In §2, replace the incident bullet with:

   ```markdown
   - `runtime/incident-<id>.json` — `{id, kind, severity, detail, tick, t, task, phases}`. `phases` is the tick's phase timeline: `[{phase, model, started, ended, rc, session}]`.
   ```

2. Add a bullet to the §2 read list, after the `LOOP_PLAN.md` bullet:

   ```markdown
   - `LOOP_DECISIONS.md` — what the Judge already decided for this task. If the Judge has acted twice on the same task, the loop is arguing with itself and that is the finding, not the timeout.
   - `runtime/task-<TASK>.json` — the task's state machine and attempt history: the phase it stopped in, and per attempt the tier that ran, the phase results, the outcome signature and the Judge's decision. An attempt with no `outcome` is one the harness never closed, i.e. it died mid-flight.
   ```

   The medic's allowlist is unchanged: these are reads. `task-<TASK>.json` is
   the harness's control state — the medic never writes it.

3. Insert this paragraph at the top of §4, before the table:

   ```markdown
   **Read `phases` and name the phase that consumed the budget before diagnosing.** The incident's `phases` array is the tick's timeline. Say which phase ran longest, on which model, and whether it was killed (`rc` null with an `ended`), and put that sentence in `summary` before you choose a row. A diagnosis that does not name a phase is a guess — the 2026-09-11 run produced three medic reports that blamed "task size" while the actual timeline showed one extra Evaluator→Worker iteration each time.
   ```

4. Add two rows to the §4 table, after the `tick-stalled` row:

   ```markdown
   | `phase-timeout` | a phase hit its own budget and the runner's resume budget is spent | warn only; no action — the runner already ran the wrap-up, kept the checkpoint, and handed the task to the Judge. Name the phase and its model in `summary` | `noop` |
   | `judge-loop` | two Judge decisions on the same task have already failed | a human decision by definition: summarise both `LOOP_DECISIONS.md` entries for the task and what each tried, attempt no repair | `escalated` |
   ```

5. In §5, replace the first bullet with:

   ```markdown
   - **Same `kind` + `phase` + `task` signature twice in a run → `escalated`.** Before acting, build this incident's signature from its `kind`, the phase named in its `phases` timeline, and its `task`, and compare it against every earlier `runtime/incident-*.json`. A match means your last fix did not hold; do not apply it again. Keying on `kind` alone is what stopped the 2026-09-11 run: three unrelated causes all presented as `rc=124 after 1800s`.
   ```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -t tests/runner -v && bash tests/medic.contract.sh`
Expected: PASS — `test_resume.py` contributes 12 tests, and `medic.contract.sh` green.

- [ ] **Step 6: Commit**

```bash
git add plugins/agent-loop/runner/incidents.py plugins/agent-loop/runner/run.py plugins/agent-loop/skills/agent-loop-medic/SKILL.md plugins/agent-loop/tests/medic.contract.sh plugins/agent-loop/tests/runner/test_resume.py
git commit -F - <<'EOF'
agent-loop: medic reads the phase timeline and the per-task state file

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 8: Boot reconciliation — a `[~]` task with no state file

**Files:**
- Modify: `plugins/agent-loop/runner/judge.py` (append `boot_evidence`, `boot_reconcile`; one line in `build_input`)
- Modify: `plugins/agent-loop/runner/git_ops.py` (`last_commit`)
- Modify: `plugins/agent-loop/runner/run.py` (`boot_task`, replacing plan A's `# plan C` hook)
- Create: `plugins/agent-loop/tests/runner/test_boot.py`

**Interfaces:**
- Consumes: `git_ops.changed_paths`, `git_ops.strays`, `git_ops.revert`, `task_state.TaskState`, `judge.build_input`, `judge.decide`, `judge.apply`.
- Produces: `git_ops.last_commit(cwd) -> str`, `judge.boot_evidence(ctx, contract) -> str`, `judge.boot_reconcile(ctx, contract) -> str` (returns `resume|retry|defer`), `run.boot_task(ctx, contract) -> str` (returns `work|scout|defer`), the `boot_reconcile` event.
- **Plan A's boot rule (spec §14) is the caller.** At boot, a `[~]` task with a `runtime/task-<T>.json` resumes at the recorded phase — that is plan A's work and this task does not touch it. A `[~]` task with **no** state file is the ambiguous case, and plan A leaves a `# plan C` hook there; `run.boot_task` replaces it. This is spec decision 13: the migration framework stays mechanical and the ambiguous step is an agent with a budget and an allowlist.

- [ ] **Step 1: Write the failing tests**

Create `plugins/agent-loop/tests/runner/test_boot.py`:

```python
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import cfixtures  # noqa: F401
from runner import judge
from runner.claude_proc import PhaseResult


def judge_says(payload):
    return mock.patch.object(
        judge.claude_proc, "run_phase", return_value=PhaseResult(
            phase="judge", model="opus", rc=0, killed=False, timed_out=False,
            session_id="sid-judge", transcript_path=None, started=1, ended=2,
            result_text="```json\n%s\n```" % json.dumps(payload),
            usage_by_model={}, tool_calls=0, last_activity=2))


def decision(**over):
    d = {"classification": "open", "rationale": "r", "instruction": "",
         "alternatives": ["defer to a human"], "reversal": "none",
         "changes": {"allow_list_add": [], "forbidden_remove": [],
                     "default_choice": "", "blocks": [], "sub_rows": [],
                     "tier": "", "extend_cap_s": 0}}
    d.update(over)
    return d


class _BootBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cfg, self.plan, self.loop_dir, self.runtime = cfixtures.make_loop(self.tmp)
        self.ctx = cfixtures.make_ctx(self.cfg, self.plan, self.loop_dir, self.runtime)
        self.contract = cfixtures.make_contract()
        cfixtures.write_contract(self.ctx, self.contract)
        cfixtures.git_init(self.cfg.worktree)
        self.kept = os.path.join(
            self.cfg.worktree,
            "packages/api/src/requests/activepipe/internal/organisationalUnits.ts")
        self.stray = os.path.join(self.cfg.worktree, "apps/frontend/App.tsx")
        for path, body in ((self.kept, "export const orgUnits = 1\n"),
                           (self.stray, "// outside the allow_list\n")):
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as fh:
                fh.write(body)
        with open(os.path.join(self.runtime, "worker-result.json"), "w") as fh:
            json.dump({"task": "T60", "status": "partial",
                       "checkpoint": "register the mixin in index.ts"}, fh)


class TestBootEvidence(_BootBase):
    def test_the_dirty_tree_is_split_by_the_allow_list(self):
        body = judge.boot_evidence(self.ctx, self.contract)
        self.assertIn("inside allow_list", body)
        self.assertIn("organisationalUnits.ts", body)
        self.assertIn("outside allow_list", body)
        self.assertIn("apps/frontend/App.tsx", body)

    def test_the_checkpoint_and_the_last_commit_trailers_are_carried(self):
        subprocess.check_call(
            ["git", "-C", self.cfg.worktree, "commit", "-q", "--allow-empty",
             "-m", "loop: complete T59\n\nLoop-Status: done\nLoop-Task: T59\n"])
        body = judge.boot_evidence(self.ctx, self.contract)
        self.assertIn("register the mixin in index.ts", body)
        self.assertIn("Loop-Status: done", body)
        self.assertIn("Loop-Task: T59", body)

    def test_build_input_only_carries_it_for_boot_reconcile(self):
        boot = judge.build_input(self.ctx, self.contract,
                                 judge.Failure(kind="boot-reconcile", detail="no state"))
        self.assertIn("apps/frontend/App.tsx", boot.boot_evidence)
        gate = judge.build_input(self.ctx, self.contract,
                                 judge.Failure(kind="gate", detail="tsc failed"))
        self.assertEqual(gate.boot_evidence, "")


class TestBootDecisions(_BootBase):
    def setUp(self):
        _BootBase.setUp(self)
        self.inp = judge.build_input(
            self.ctx, self.contract,
            judge.Failure(kind="boot-reconcile", detail="no state file"))

    def test_only_the_four_boot_decisions_are_accepted(self):
        for d in ("resume", "retry", "revert-and-retry", "defer"):
            self.assertEqual(
                judge.validate_decision(decision(decision=d), self.inp), [], d)
        for d in ("widen", "escalate", "split", "halt"):
            errors = judge.validate_decision(decision(decision=d), self.inp)
            self.assertTrue(any("boot reconciliation accepts only" in e
                                for e in errors), (d, errors))

    def test_resume_at_boot_needs_no_session_id(self):
        self.assertIsNone(self.inp.resume_session)
        self.assertEqual(
            judge.validate_decision(decision(decision="resume"), self.inp), [],
            "no session survives a boot; resume means keep the tree and re-run "
            "the Worker against the existing contract")

    def test_revert_and_retry_is_refused_on_an_ordinary_failure(self):
        gate = judge.build_input(self.ctx, self.contract,
                                 judge.Failure(kind="gate", detail="tsc failed"))
        errors = judge.validate_decision(
            decision(decision="revert-and-retry"), gate)
        self.assertTrue(any("only for boot reconciliation" in e for e in errors),
                        errors)


class TestBootReconcile(_BootBase):
    def test_revert_and_retry_drops_in_allow_list_work_too(self):
        with judge_says(decision(decision="revert-and-retry",
                                 rationale="nothing vouches for this tree",
                                 reversal="the reverted work was never committed")):
            action = judge.boot_reconcile(self.ctx, self.contract)
        self.assertEqual(action, "retry")
        self.assertFalse(os.path.exists(self.kept),
                         "revert-and-retry reverts inside allow_list too (§14)")
        self.assertFalse(os.path.exists(self.stray))

        state = json.load(open(os.path.join(self.runtime, "task-T60.json")))
        self.assertEqual(state["attempts"][-1]["outcome"],
                         "boot-reconcile:revert-and-retry")
        self.assertEqual(state["attempts"][-1]["judge"]["decision"],
                         "revert-and-retry")
        self.assertIn("— revert-and-retry (open)",
                      cfixtures.read(os.path.join(self.loop_dir,
                                                  "LOOP_DECISIONS.md")))
        evs = [e for e in cfixtures.read_events(self.loop_dir)
               if e["type"] == "boot_reconcile"]
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["decision"], "revert-and-retry")

    def test_resume_keeps_the_tree_and_routes_to_work(self):
        from runner import run as runner_run
        with judge_says(decision(decision="resume", classification="capability",
                                 rationale="the checkpoint matches the tree")):
            self.assertEqual(runner_run.boot_task(self.ctx, self.contract), "work")
        self.assertTrue(os.path.exists(self.kept))
        self.assertTrue(os.path.exists(
            os.path.join(self.runtime, "task-T60.json")),
            "reconciliation creates the state file that was missing")

    def test_retry_routes_to_scout_and_keeps_the_tree(self):
        from runner import run as runner_run
        with judge_says(decision(decision="retry", classification="capability",
                                 rationale="the tree is worth keeping")):
            self.assertEqual(runner_run.boot_task(self.ctx, self.contract), "scout")
        self.assertTrue(os.path.exists(self.kept))

    def test_defer_marks_the_task_and_writes_cleanup(self):
        from runner import run as runner_run
        from runner.plan import Plan
        with judge_says(decision(decision="defer",
                                 rationale="the tree contradicts HEAD",
                                 changes={"default_choice": "revert and re-run",
                                          "blocks": []})):
            self.assertEqual(runner_run.boot_task(self.ctx, self.contract), "defer")
        reloaded = Plan.load(os.path.join(self.loop_dir, "LOOP_PLAN.md"))
        self.assertEqual(
            [t for t in reloaded.tasks() if t.id == "T60"][0].state, "blocked")
        self.assertIn("## T60", cfixtures.read(
            os.path.join(self.loop_dir, "LOOP_CLEANUP.md")))


if __name__ == "__main__":
    unittest.main()
```

Note the `decision()` helper passes `changes` whole, so a test that overrides it
must include every key it needs; `judge._normalize` fills the rest inside
`decide`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -t tests/runner -v`
Expected: FAIL — `AttributeError: module 'runner.judge' has no attribute 'boot_evidence'`.

- [ ] **Step 3: Add `git_ops.last_commit`**

Append to `plugins/agent-loop/runner/git_ops.py`:

```python
def last_commit(cwd):
    """HEAD's full message — subject, body and trailers. '' in an empty repo."""
    try:
        out = subprocess.check_output(["git", "-C", cwd, "log", "-1", "--pretty=%B"],
                                      stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, OSError):
        return ""
    return out.decode("utf-8", "replace")
```

- [ ] **Step 4: Write the boot dossier and the reconciliation**

Append to `plugins/agent-loop/runner/judge.py`:

```python
BOOT_TRAILER_RE = re.compile(r"^(Loop-[A-Za-z-]+|Co-Authored-By):")


def boot_evidence(ctx, contract) -> str:
    """What the 2.x medic used to read by hand before deciding (spec §14).

    The Judge cannot resume a phase it has no record of, so it gets the three
    things that survive a crash: the uncommitted tree split by the contract's
    allow_list, the Worker's own checkpoint, and what HEAD claims happened.
    """
    worktree = ctx.cfg.worktree
    changed = git_ops.changed_paths(worktree)
    stray = git_ops.strays(changed, contract.allow_list)
    inside = [p for p in changed if p not in set(stray)]

    lines = ["### Uncommitted tree vs the contract's allow_list",
             "- inside allow_list (%d): %s"
             % (len(inside), ", ".join(inside[:20]) or "none"),
             "- outside allow_list (%d): %s"
             % (len(stray), ", ".join(stray[:20]) or "none"),
             "",
             "### runtime/worker-result.json"]
    body = read_text(os.path.join(ctx.runtime_dir, "worker-result.json")).strip()
    lines.append(body or "(absent - the Worker never wrote one)")

    message = git_ops.last_commit(worktree)
    msg_lines = message.splitlines()
    trailers = [ln for ln in msg_lines if BOOT_TRAILER_RE.match(ln.strip())]
    lines.append("")
    lines.append("### HEAD commit")
    lines.append(msg_lines[0] if msg_lines else "(no commit on this branch)")
    lines.extend(trailers or ["(no Loop- trailers)"])
    return "\n".join(lines)


def boot_reconcile(ctx, contract) -> str:
    """A `[~]` task with no runtime/task-<T>.json: the ambiguous step of a boot
    or of the 2->3 migration, handed to an agent with a budget and an allowlist
    (spec §14, decision 13). Returns retry | resume | defer."""
    failure = Failure(
        kind="boot-reconcile",
        detail="the harness restarted and found %s marked [~] with no "
               "runtime/task-%s.json to resume from"
               % (ctx.task.id, ctx.task.id))
    inp = build_input(ctx, contract, failure)
    decision = decide(ctx, inp)
    judge_phase = decision.pop("_phase", None)
    action = apply(ctx, contract, decision)

    state = TaskState.load(ctx.runtime_dir, ctx.task.id)
    state.begin_attempt(inp.tier)
    if judge_phase is not None:
        state.begin_phase("judge")
        state.end_phase(judge_phase)
    state.end_attempt("boot-reconcile:%s" % decision["decision"],
                      judge=json_safe(decision))
    state.save()

    ctx.events.emit("boot_reconcile", tick=ctx.tick, task=ctx.task.id,
                    decision=decision["decision"],
                    classification=decision.get("classification", ""))
    return action
```

And in `build_input`, replace the placeholder line from Task 1:

```python
        boot_evidence=(boot_evidence(ctx, contract)
                       if failure.kind == "boot-reconcile" else ""),
```

- [ ] **Step 5: Wire plan A's boot hook**

In `plugins/agent-loop/runner/run.py`, replace plan A's `# plan C` boot
placeholder with:

```python
def boot_task(ctx, contract):
    """Plan A's boot rule (spec §14) routes here when a `[~]` task has no
    runtime/task-<T>.json. Returns where the tick restarts:

      work   - keep the uncommitted tree and re-dispatch the Worker against the
               existing contract. No session survives a boot, so this is not a
               `--resume`; it is the Judge saying the checkpoint and the tree
               agree.
      scout  - restart the task from SCOUT (the tree was kept, or reverted by
               the Judge's `revert-and-retry`).
      defer  - the task is already `[!]` with a LOOP_CLEANUP entry.
    """
    action = judge.boot_reconcile(ctx, contract)
    if action == "resume":
        return "work"
    if action == "retry":
        return "scout"
    return "defer"
```

A task **with** a state file never reaches here: plan A resumes it at the
recorded phase, and a task killed inside WORK takes the §6 wrap-up/resume path
with its recorded session id.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -t tests/runner -v`
Expected: PASS — `test_boot.py` contributes 10 tests.

- [ ] **Step 7: Commit**

```bash
git add plugins/agent-loop/runner/judge.py plugins/agent-loop/runner/git_ops.py plugins/agent-loop/runner/run.py plugins/agent-loop/tests/runner/test_boot.py
git commit -F - <<'EOF'
agent-loop: judge-driven boot reconciliation for a task with no state file

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 9: End-to-end — a judge-driven widen, and a split

**Files:**
- Modify: `plugins/agent-loop/tests/fixtures/claude` (one protocol addition)
- Modify: `plugins/agent-loop/tests/run.e2e.test.sh` (two scenarios)

**Interfaces:**
- Consumes: the whole runner, `run.sh`, and the stub protocol from the interfaces doc.
- Consumes: the stub's `NNN.sh` hook, added in Task 4 Step 1.

- [ ] **Step 1: Confirm the stub's `NNN.sh` hook is in place**

Run: `cd plugins/agent-loop && grep -n 'STUB_SCRIPT/\$n.sh' tests/fixtures/claude`
Expected: one hit — the sourcing block added in Task 4 Step 1. Without it the
scripted Scout cannot write a contract and both scenarios below are impossible.

- [ ] **Step 2: Write the failing scenarios**

Append to `plugins/agent-loop/tests/run.e2e.test.sh`, before its summary line:

`run.e2e.test.sh` already sets `HERE` (its own dir) and `ROOT` (the repo root);
add `PLUGIN="$(cd "$HERE/.." && pwd)"` beside them if it is not already there.
`assert_eq` takes `expected actual message`.

```bash
# ---------------------------------------------------------------------------
# Scenario: a scout-sourced constraint blocks the gate; the Judge widens it and
# the retry passes. (spec §7; the 2026-09-11 T60 halt, decided without a human.)
# ---------------------------------------------------------------------------
scenario_judge_widen() {
  tmp="$(mktemp -d)"; wt="$tmp/wt"; loop="$wt/.claude/loop/run-1"
  mkdir -p "$loop/runtime" "$wt/src" "$wt/docs" "$tmp/script" "$tmp/bin"
  ln -s "$HERE/fixtures/claude" "$tmp/bin/claude"
  git init -q "$wt"; git -C "$wt" config user.email l@t; git -C "$wt" config user.name L
  printf 'placeholder\n' > "$wt/docs/spec.md"; cp "$wt/docs/spec.md" "$wt/docs/plan.md"

  cat > "$loop/LOOP_CONFIG.md" <<CFG
# Loop Config
Goal: e2e judge widen
Granularity: single
TDD mode: none
Verification pipeline: test
Limits: tick_timeout=180 scout_timeout=60 worker_timeout=60 wrapup_timeout=30 eval_timeout=60 judge_timeout=60 gate_cmd_timeout=30 worker_resume_max=0 max_attempts=3
Blocker policy: continue-independent
Branch: main
Worktree: $wt
Dashboard: off
Medic: off
Decision policy: autonomous
Tiers: cheap=haiku standard=sonnet most-capable=opus
Spec: $wt/docs/spec.md
Plan: $wt/docs/plan.md
CFG
  cat > "$loop/LOOP_PLAN.md" <<'PLN'
# Loop Plan

## Segment 1
- [ ] T1 Create both source files
PLN
  git -C "$wt" add -A >/dev/null; git -C "$wt" commit -qm base

  # 001 scout: writes a contract whose gate needs src/b.ts, and forbids it itself
  cat > "$tmp/script/001.sh" <<SH
cat > "$loop/runtime/sprint-T1.json" <<'JSON'
{"task":"T1",
 "success_criteria":["src/a.ts exists","src/b.ts exists"],
 "allow_list":["src/a.ts"],
 "forbidden":[{"path":"src/b.ts","source":"scout"}],
 "verification":["test -f src/b.ts"],
 "render_gate":null,"fidelity_source":[],
 "evaluator_must_read":[],"evaluator_must_view":[],
 "estimated_diff_lines":10,"scout_notes":"","relevant_learnings":[]}
JSON
SH
  stub_json "$tmp/script/001.jsonl" '{"contract_path":"runtime/sprint-T1.json","notes":"ok"}'
  # 002 worker #1: builds only what the allow_list permits
  cat > "$tmp/script/002.sh" <<SH
echo 'export const a = 1' > "$wt/src/a.ts"
printf '{"task":"T1","status":"complete","files_touched":["src/a.ts"],"summary":"a only","checkpoint":"b blocked","next_steps":[]}' > "$loop/runtime/worker-result.json"
SH
  stub_json "$tmp/script/002.jsonl" '{"status":"complete","summary":"a only"}'
  # 003 judge: the blocking constraint is scout-sourced, so widen it
  stub_json "$tmp/script/003.jsonl" '{"decision":"widen","classification":"self-imposed","rationale":"the forbidden entry for src/b.ts is scout-sourced and no plan line supports it","instruction":"create src/b.ts","alternatives":["defer to a human, as the 2026-09-11 run did"],"reversal":"re-add the forbidden entry to runtime/sprint-T1.json","changes":{"allow_list_add":["src/b.ts"],"forbidden_remove":["src/b.ts"],"default_choice":"","blocks":[],"sub_rows":[],"tier":"","extend_cap_s":0}}'
  # 004 worker #2: the retry, with the widened contract
  cat > "$tmp/script/004.sh" <<SH
echo 'export const b = 2' > "$wt/src/b.ts"
printf '{"task":"T1","status":"complete","files_touched":["src/a.ts","src/b.ts"],"summary":"both","checkpoint":"","next_steps":[]}' > "$loop/runtime/worker-result.json"
SH
  stub_json "$tmp/script/004.jsonl" '{"status":"complete","summary":"both"}'
  stub_json "$tmp/script/005.jsonl" '{"verdict":"PASS","findings":[{"criterion":"src/b.ts exists","met":true,"evidence":"gate-T1-1.txt"}],"views":[],"summary":"ok"}'
  stub_json "$tmp/script/006.jsonl" '{"patterns":[],"log":"widened once","invariants":[]}'

  ( cd "$wt" && PATH="$tmp/bin:$PATH" STUB_SCRIPT="$tmp/script" STUB_LOG="$tmp/stub.log" \
      STUB_DELAY=0 LOOP_DIR="$loop" bash "$PLUGIN/run.sh" >"$tmp/out" 2>&1 )
  rc=$?
  assert_eq 0 "$rc" "judge-widen scenario exits 0"
  grep -q '— widen (self-imposed)' "$loop/LOOP_DECISIONS.md"
  assert_true $? "LOOP_DECISIONS.md records the widen with its classification"
  grep -q '"src/b.ts"' "$loop/runtime/sprint-T1.json"
  assert_true $? "the contract's allow_list was widened on disk"
  grep -q '^- \[x\] T1 ' "$loop/LOOP_PLAN.md"
  assert_true $? "T1 completed after the widen"
  grep -q '"type":"decision"' "$loop/events.jsonl"
  assert_true $? "a decision event was emitted"
  grep -q '\[!\]' "$loop/LOOP_PLAN.md"
  assert_false $? "nothing was marked [!]"
  rm -rf "$tmp"
}

# ---------------------------------------------------------------------------
# Scenario: the Worker times out, the wrap-up checkpoints, the resume budget is
# zero, and the Judge splits the task. (spec §6.3)
# ---------------------------------------------------------------------------
scenario_judge_split() {
  tmp="$(mktemp -d)"; wt="$tmp/wt"; loop="$wt/.claude/loop/run-1"
  mkdir -p "$loop/runtime" "$wt/src" "$wt/docs" "$tmp/script" "$tmp/bin"
  ln -s "$HERE/fixtures/claude" "$tmp/bin/claude"
  git init -q "$wt"; git -C "$wt" config user.email l@t; git -C "$wt" config user.name L
  printf 'placeholder\n' > "$wt/docs/spec.md"; cp "$wt/docs/spec.md" "$wt/docs/plan.md"

  cat > "$loop/LOOP_CONFIG.md" <<CFG
# Loop Config
Goal: e2e judge split
Granularity: single
TDD mode: none
Verification pipeline: test
Limits: tick_timeout=180 scout_timeout=60 worker_timeout=2 wrapup_timeout=30 eval_timeout=60 judge_timeout=60 gate_cmd_timeout=30 worker_resume_max=0 max_attempts=3
Blocker policy: continue-independent
Branch: main
Worktree: $wt
Dashboard: off
Medic: off
Decision policy: autonomous
Tiers: cheap=haiku standard=sonnet most-capable=opus
Spec: $wt/docs/spec.md
Plan: $wt/docs/plan.md
CFG
  cat > "$loop/LOOP_PLAN.md" <<'PLN'
# Loop Plan

## Segment 1
- [ ] T1 Build both halves
PLN
  git -C "$wt" add -A >/dev/null; git -C "$wt" commit -qm base

  write_contract() {   # $1 = script number, $2 = task id, $3 = file the gate wants
    cat > "$tmp/script/$1.sh" <<SH
cat > "$loop/runtime/sprint-$2.json" <<'JSON'
{"task":"$2","success_criteria":["$3 exists"],"allow_list":["src/**"],
 "forbidden":[],"verification":["test -f $3"],"render_gate":null,
 "fidelity_source":[],"evaluator_must_read":[],"evaluator_must_view":[],
 "estimated_diff_lines":10,"scout_notes":"","relevant_learnings":[]}
JSON
SH
    stub_json "$tmp/script/$1.jsonl" "{\"contract_path\":\"runtime/sprint-$2.json\",\"notes\":\"ok\"}"
  }
  finish_task() {      # $1 = first script number of a clean scout/worker/eval/learn tick
    write_contract "$1" "$2" "$3"
    n2=$(printf '%03d' $((10#$1 + 1)))
    n3=$(printf '%03d' $((10#$1 + 2)))
    n4=$(printf '%03d' $((10#$1 + 3)))
    cat > "$tmp/script/$n2.sh" <<SH
echo done > "$wt/$3"
printf '{"task":"$2","status":"complete","files_touched":["$3"],"summary":"ok","checkpoint":"","next_steps":[]}' > "$loop/runtime/worker-result.json"
SH
    stub_json "$tmp/script/$n2.jsonl" '{"status":"complete","summary":"ok"}'
    stub_json "$tmp/script/$n3.jsonl" '{"verdict":"PASS","findings":[],"views":[],"summary":"ok"}'
    stub_json "$tmp/script/$n4.jsonl" '{"patterns":[],"log":"ok","invariants":[]}'
  }

  write_contract 001 T1 src/both
  printf '20\n' > "$tmp/script/002.sleep"                      # worker #1 overruns
  stub_json "$tmp/script/003.jsonl" '{"status":"partial","summary":"out of time"}'   # wrap-up
  cat > "$tmp/script/003.sh" <<SH
printf '{"task":"T1","status":"partial","files_touched":[],"summary":"half","checkpoint":"src/a then src/b","next_steps":["src/a","src/b"]}' > "$loop/runtime/worker-result.json"
SH
  stub_json "$tmp/script/004.jsonl" '{"decision":"split","classification":"capability","rationale":"the wrap-up checkpoint shows two independently verifiable halves and the resume budget is spent","instruction":"","alternatives":["escalate to the next tier"],"reversal":"revert the split commit and restore T1","changes":{"allow_list_add":[],"forbidden_remove":[],"default_choice":"","blocks":[],"sub_rows":["- [ ] Build the first half","- [ ] Build the second half"],"tier":"","extend_cap_s":0}}'
  # the harness allocates the ids: T1 is the only row, so the halves are T2, T3
  finish_task 005 T2 src/a
  finish_task 009 T3 src/b

  ( cd "$wt" && PATH="$tmp/bin:$PATH" STUB_SCRIPT="$tmp/script" STUB_LOG="$tmp/stub.log" \
      STUB_DELAY=0 LOOP_DIR="$loop" bash "$PLUGIN/run.sh" >"$tmp/out" 2>&1 )
  rc=$?
  assert_eq 0 "$rc" "judge-split scenario exits 0"
  grep -q 'split→T2,T3' "$loop/LOOP_PLAN.md"
  assert_true $? "the parent row records the split into numeric ids"
  grep -q '| split_of: T1' "$loop/LOOP_PLAN.md"
  assert_true $? "each sub row carries | split_of: T1"
  grep -q 'T1a' "$loop/LOOP_PLAN.md"
  assert_false $? "no lettered ids anywhere: serve.py matches \\bT\\d+\\b"
  grep -q '^- \[-\] T1 ' "$loop/LOOP_PLAN.md"
  assert_true $? "the parent task carries the [-] glyph"
  git -C "$wt" log --pretty=%B | grep -q 'loop: split T1 → T2,T3'
  assert_true $? "the split commit subject is exact"
  git -C "$wt" log --pretty=%B | grep -q 'Loop-Status: skipped'
  assert_true $? "the split commit carries Loop-Status: skipped"
  grep -q '"type":"split"' "$loop/events.jsonl"
  assert_true $? "a split event was emitted"
  grep -q '"type":"resume"' "$loop/events.jsonl"
  assert_true $? "the wrap-up emitted a resume event"
  grep -c "sid" "$tmp/stub.log" >/dev/null
  assert_true $? "the wrap-up was dispatched with --resume"
  rm -rf "$tmp"
}

scenario_judge_widen
scenario_judge_split
```

`stub_json <path> <json>` is the existing helper in `run.e2e.test.sh` that wraps
a payload into a one-line `result` transcript. If plan A named it differently,
use plan A's name; if plan A has none, add:

```bash
stub_json() {   # $1 = destination .jsonl, $2 = the phase's JSON payload
  printf '{"type":"result","subtype":"success","is_error":false,"result":"```json\\n%s\\n```"}\n' \
    "$(printf '%s' "$2" | sed 's/\\/\\\\/g; s/"/\\"/g')" > "$1"
}
```

- [ ] **Step 3: Run the e2e suite to verify the new scenarios fail**

Run: `cd plugins/agent-loop && bash tests/run.e2e.test.sh`
Expected: FAIL — `LOOP_DECISIONS.md: No such file or directory` (the stub cannot
write the contract until Step 1 lands, and the scenarios are new).

- [ ] **Step 4: Run the whole suite**

Run: `bash scripts/test-all.sh`
Expected: PASS — every plugin's `tests/all.sh`, `scripts/validate.sh`,
`scripts/lint.sh` (SKIP if `shellcheck` is absent — that is not a pass), and
`node --test`. Confirm `tests/all.sh` runs `python3 -m unittest discover -s tests/runner -t tests/runner`; if plan A did not add that line, add it beside the `serve.test.py` block.

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/tests/fixtures/claude plugins/agent-loop/tests/run.e2e.test.sh plugins/agent-loop/tests/all.sh
git commit -F - <<'EOF'
agent-loop: e2e coverage for judge-driven widen-and-retry and split

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

## What this plan deliberately does not do

- It does not add the render gate, the fidelity check, or `evaluator_must_view`
  enforcement — those are plan B, and the Judge reads their failures through the
  generic `render` / `fidelity` failure kinds.
- It does not touch `serve.py`, `dashboard.html`, `/agent-loop-setup`, or the
  templates. `LOOP_DECISIONS.md` and the `Decision policy:` config line surface
  in the dashboard and the setup wizard in plan D.
- It does not change `Plan.split`, `Plan.eligible`, or `Plan.mode` beyond adding
  `set_blocked_by`; plan A owns the grammar and the numeric id allocation.
- It does not own `runtime/task-<T>.json`. Plan A's `TaskState` writes it at
  every phase transition; this plan reads its `attempts` and closes attempts
  through it (spec §14).
- It does not implement the schema 2→3 migration or the boot resume-at-recorded-
  phase rule. Plan A owns both; this plan fills only the ambiguous branch, the
  `[~]` task with no state file.
- It does not parse `Decision policy:`. Plan A's `LoopConfig.decision_policy`
  already carries it (spec §4.6); this plan only reads it, and `test_judge.py`'s
  `TestConservativePolicy` is the check that both values reach the Judge.

## Notes on two spec readings

**Split rows come from the Judge, not a Planner phase.** An earlier reading of
spec §6.3 had a **Planner phase** rewrite a split task into ordered sub-tasks;
the amended spec (commit `e18b278`) and the interface contract both put the rows
in the Judge's own `changes.sub_rows`, and this plan follows them. The Judge is
already at the most-capable tier with the full dossier loaded, so a second
`claude -p` round-trip buys nothing, and the harness validates the rows against
the plan grammar either way (`judge.validate_sub_rows`).

**The Judge never writes an id.** `changes.sub_rows` are titles;
`Plan.split` allocates the next free numeric ids and appends `| split_of: T<n>`
(interfaces doc). Lettered ids (`T60a`) are excluded everywhere in this plan
because serve.py's id regex is `\bT\d+\b` and a lettered id would vanish from
the dashboard — so the earlier draft's `- [ ] T60a …` sub-row grammar is now a
validation error, not a format.
