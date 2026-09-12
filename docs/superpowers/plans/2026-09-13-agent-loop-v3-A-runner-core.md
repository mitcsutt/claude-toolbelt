# agent-loop v3 — Plan A: the runner core

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the bash harness and its 300-line orchestrator prompt with one Python process per loop that owns the state machine — selects tasks, dispatches one `claude -p` subprocess per phase, runs the gate, commits, and decides `continue | done | halt` itself.

**Architecture:** `run.sh` becomes a shim that `exec`s `runner/run.py`. The runner is a `python3` stdlib package: `run.py` holds the lock, the heartbeat thread, the outer loop and the three tick bodies; `claude_proc.py` spawns and parses one `claude -p --output-format stream-json` subprocess per phase; `phases.py` turns a role prompt plus injected context into a validated JSON document; `plan.py`, `contract.py`, `gate.py`, `git_ops.py` are the harness's own hands. No model is asked what to do next — every phase returns JSON and the harness decides. In this plan the failure path is a deliberate stand-in for the Judge (plan C): one re-dispatch at the **same** tier with the Evaluator's findings injected, then `[!]` + `LOOP_CLEANUP.md` + blocked-upstream fan-out. There is no code-enforced tier ladder anywhere: spec §11 item 4 makes the re-attempt tier the Judge's call, and the harness enforces only the bounds.

**Tech Stack:** Python 3.9 stdlib only (`unittest`, `dataclasses`, `json`, `re`, `fnmatch`, `difflib`, `threading`, `subprocess`, `signal`), bash 3.2 for `.sh`, git.

**Spec:** `docs/superpowers/specs/2026-09-13-agent-loop-v3-phase-runner-design.md` — this plan implements §3, §4 (all of it, including `runner/calibrate.py` from §4.2 and the `Limits:` keys from §4.6), §5.1–§5.2 validation rules, §9, §11 decisions 1/2/3/4/7/8/9/11/13/15/16, §12 (the `tests/runner/*.py` and `run.e2e.test.sh` bullets), §13 step 1, and §14 in full (`runtime/task-<T>.json`, the boot rule, and the 2→3 migration including the `sprint-*.json` normalisation). Spec §11 item 4 is implemented by *omission*: no tier ladder exists in code, so plan C's Judge is the only thing that can change a re-attempt's tier.

**Interface contract:** `docs/superpowers/plans/2026-09-13-agent-loop-v3-00-interfaces.md`. Every name below is taken from it verbatim. Plans B, C and D all import this package; do not rename anything.

**Evidence:** `~/Downloads/EXTRACT/08-analysis/w10-compat-surface.md` (what the dashboard, the skills and the postmortem read off disk and must keep reading), `w3`/`w4` (shell time was 6–17% of each tick; the stall detector's seven warnings were a truncate/read race), `w7` §7.1 (~$61 of spend unattributed because a killed tick wrote no `by_model`).

## Global Constraints

- Python 3.9 stdlib only; no third-party packages. macOS `/usr/bin/python3` is 3.9.6 — verified.
- **Python 3.9 has no PEP 604 unions at runtime.** Every new module starts with `from __future__ import annotations`, and annotations use `Optional[str]` / `List[str]` / `Dict[str, Any]` from `typing`. The interfaces doc writes `str | None` as shorthand; the code never does. No `match` statement.
- bash 3.2 for any `.sh`: no `mapfile`, no `declare -A`, no `${var,,}`; under `set -u`, expanding `"${arr[@]}"` on an **empty** array is fatal (`${#arr[@]}` is safe). Every `.sh` and `tests/fixtures/claude` must pass `shellcheck --severity=warning`.
- Every runtime file is written atomically: write `<path>.tmp`, then `os.replace`. Append-only documents (`events.jsonl`, `LOOP_USAGE.jsonl`, `LOOP_CLEANUP.md`, `harness.log`) are opened with `"a"`.
- No model name anywhere in the plugin except the `Tiers:` config line and the template default `Tiers: cheap=haiku standard=sonnet most-capable=opus`. Code names **tiers** and resolves them through `config.model_for`.
- `LOOP_SCHEMA = 3`. Plugin version stays `2.1.0` in this plan — **plan D owns the `3.0.0` bump**; do not touch `plugin.json`.
- Exit codes: `0` done/paused/rate-limit-exit, `1` halt/error, `2` needs-human, `3` lock-conflict.
- Events envelope: one compact JSON object per line, fields `t` (epoch int), `seq` (int, monotonic), `type`, then payload. The lifecycle types `serve.py` understands (`loop_start, loop_end, tick_start, tick_end, sleep, memory_pressure, incident, medic_start, medic_end, paused, task_status, plan_oversize, migration`) keep their exact field names.
- `LOOP_PLAN.md` task regex: `^\s*- \[(.)\] (.*)$`, glyphs ` ~ x ! -`, segment = a line starting `## `, id = first `\bT\d+\b` — **exactly** serve.py's regex, never widened. Split sub-tasks get the next free *numeric* ids (`T74`, `T75`), because `\bT\d+\b` does not match `T60a` and a lettered id would vanish from the dashboard.
- Liveness contract (`w10` §4): `runtime/harness.json.pid` must be alive **and** `ps -o command= -p <pid>` must contain `run.sh`. Never `pgrep`, never `ps | grep`.
- **Commit with `git commit -F - <<'EOF'`, never `git commit -m "$(cat <<'EOF' …)"`.** Verified on this machine: bash 3.2 (`/bin/bash` on macOS) cannot parse an apostrophe inside a quoted heredoc nested in a command substitution — `bash -n` reports ``unexpected EOF while looking for matching `'``. Every commit block in this plan already uses the `-F -` form.
- Commit message subject is `agent-loop: <what>`; every commit body ends with:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
  ```
- `bash scripts/test-all.sh` must pass at the end of every task, and pasting its output is the definition of done.

## Interface gaps this plan fills

The interfaces doc leaves these open. This plan decides them; plans B, C and D may rely on the decisions.

1. **`run.sh` must keep the word `run.sh` on the harness's command line.** The interfaces doc and spec §4 both write the shim as `exec python3 "$PLUGIN_ROOT/runner/run.py" "$@"`. After `exec` the process image is replaced, so `ps -o command= -p <pid>` prints `…/Python …/runner/run.py` — which does **not** contain `run.sh`, and `serve.py:process_alive(hpid, "run.sh")` plus all four skills would report every live harness as dead. **Decision:** the shim exports `PYTHONPATH` and passes its own path as an argument — `exec python3 -m runner.run --shim "$PLUGIN_ROOT/run.sh" "$@"` — and `run.py` accepts and ignores `--shim`. Verified on this machine: `ps -o command=` prints the full argv, so the token `run.sh` is present. (`-m` rather than a path because `runner/run.py` uses package-relative imports; running it as a file raises `ImportError: attempted relative import with no known parent package`.) This is the one place the spec is factually wrong; everything else in §3–§4 stands.
2. **Five modules beyond the interfaces list.** Three because the listed ones would each blow past 400 lines: `runner/util.py` (atomic write, JSON/text read, `process_alive` — imports nothing, so everything may import it), `runner/harness.py` (lock, heartbeat thread, runtime-file writers, `harness.log` rotation, memory guard, rate-limit and backoff policy), `runner/status.py` (the `LOOP_STATUS.md` banner string builders). Two because the spec names them and the interfaces doc's module table predates that: `runner/task_state.py` (spec §14's `runtime/task-<T>.json`, and the `boot_resume` rule that reads it) and `runner/calibrate.py` (spec §4.2's `Limits:` suggester). `run.py` itself lands at ~500 lines and is deliberately **not** split further: plans B and C both patch `run.py` by name and a `tick.py` would invalidate their file maps.
3. **`model_for(cfg, tier)` returns `""` for a known tier with no mapping**, and raises `ValueError` only for a tier outside `("cheap","standard","most-capable")`. `""` means "pass no `--model` flag and inherit the user's Claude default". A `DEFAULT_TIERS` constant naming haiku/sonnet/opus in Python would violate the no-model-names rule, so there is none.
4. **`config.next_tier(tier)` exists but nothing in the failure path calls it.** It returns the next tier up, clamped at `most-capable`, and is the helper plan C's Judge `escalate` decision maps onto (`changes.tier`). **There is no code-enforced ladder** — spec §11 item 4 (Mitch, parallel session): the tier of a re-attempt is judged from the checkpoint, and the harness enforces only `max_attempts`, `worker_resume_max`, and the two-failed-decisions rule. `phases.worker_tier(cfg, tier=None)` therefore returns the *configured* Worker tier unless a caller passes an explicit override; plan A never passes one, plan C passes the Judge's.
5. **`"blocked-upstream"` is not a glyph.** `LOOP_PLAN.md` has exactly five glyphs and `serve.py` maps them. A blocked-upstream task keeps glyph `[ ]` and gains the literal marker ` [blocked-upstream]` at the end of its row; `plan.py` reports `state == "blocked-upstream"` for such a row and `eligible()` skips it. The dashboard keeps counting it as remaining, which is the truth.
6. **`Plan.dependents(task_id)`** — transitive dependents over `depends_on`, used by the blocked-upstream fan-out. Not in the interfaces doc; added here.
7. **`PhaseResult` gains three fields** the interfaces doc omits but the rate-limit and failure paths need: `is_error: bool`, `api_error_status: str` (`""` when none), `rate_limit: Optional[Dict[str, Any]]`, plus `stalled: bool`.
8. **`run_phase(max_turns=0)` omits the `--max-turns` flag** (plan C item 6 asks for exactly this).
9. **The stub `claude` fixture gains one protocol element:** a file `NNN.sh` beside `NNN.jsonl` is run (with the phase's cwd) before the transcript is printed, so a scripted phase can write the files a real phase writes. Plan C's Task 9 lists this as its own addition; it lands here instead and plan C's step becomes a no-op.
10. **`tests/tick-prompt.contract.sh` is deleted here**, not in plan D: it asserts against `tick-prompt.md`, which this plan removes. Plan D adds `tests/prompts.contract.sh` in its place.
11. **`scripts/validate.sh` needs no change.** Its R9 check only flags files tracked as `100755`; every new `runner/**.py` and `tests/runner/**.py` is `644` and `run.py` is invoked as `python3 …/run.py`, never directly. Task 22 verifies this rather than editing the script.
12. **`run_scout(ctx, validation_errors=None)`** — the interfaces doc writes `run_scout(ctx)`. The re-scout after a rejected contract must carry the validation errors into the prompt, and threading them through `TickContext` would change a dataclass plans B and C pin; an optional keyword is the smaller change.
13. **`run_planner(ctx, segment)` returns `(PhaseResult, dict)`**, not a bare `PhaseResult`, because `run.py` checks `tasks_added` against the rows the Planner actually wrote. Neither plan B nor plan C calls `run_planner` — plan C's split rows come from the Judge, not a Planner phase — so the divergence stays inside this plan.
14. **`phases.py` gains five helpers** the interfaces doc does not name: `learnings_digest`, `knowledge_text`, `spec_excerpt`, `_dispatch`, `_result_with`. They are what fill the documented placeholders and what performs the single re-ask.

## File Structure

| file | responsibility |
|---|---|
| `plugins/agent-loop/runner/__init__.py` | **new.** Empty package marker. |
| `plugins/agent-loop/runner/util.py` | **new.** `atomic_write`, `write_json`, `read_json`, `read_text`, `read_int`, `stamp_epoch`, `process_alive`. Imports no runner module. |
| `plugins/agent-loop/runner/events.py` | **new.** `EventLog` (envelope + persisted `seq`), `UsageLog` (`LOOP_USAGE.jsonl`). |
| `plugins/agent-loop/runner/config.py` | **new.** `LoopConfig`, `load_config`, `model_for`, `next_tier`, `phase_limit`. |
| `plugins/agent-loop/runner/plan.py` | **new.** `Task`, `Segment`, `Plan` — grammar, eligibility, mode, in-place edits, split. |
| `plugins/agent-loop/runner/contract.py` | **new.** `Forbidden`, `RenderGate`, `FidelitySource`, `Contract`, `load_contract`, `save_contract`, `validate`. |
| `plugins/agent-loop/runner/gate.py` | **new.** `CommandResult`, `run_commands`, `all_ok`. |
| `plugins/agent-loop/runner/git_ops.py` | **new.** `changed_paths`, `strays`, `revert`, `head_sha`, `commit`, `diff_text`, `similarity`. |
| `plugins/agent-loop/runner/harness.py` | **new.** `lock_acquire`/`lock_release`, `Heartbeat`, `write_tick_json`, `RotatingLog`, `mem_headroom`, `mem_guard_action`, `ratelimit_action`, `backoff_delay`. |
| `plugins/agent-loop/runner/status.py` | **new.** `pct`, `fmt_dur`, `progress_bar`, `gates_compact`, `cause_human`, `tick_line`, `session_header`, `write_status`. |
| `plugins/agent-loop/runner/task_state.py` | **new.** `TaskState`, `state_path`, `has_state`, `boot_resume` — spec §14's `runtime/task-<T>.json`. |
| `plugins/agent-loop/runner/calibrate.py` | **new.** `role_spans`, `p90`, `round_up_60`, `suggest`, `limits_line`, `main` — spec §4.2's calibrator. |
| `plugins/agent-loop/runner/claude_proc.py` | **new.** `Usage`, `PhaseResult`, `run_phase`, `is_stalled`, `transcript_path`. |
| `plugins/agent-loop/runner/phases.py` | **new.** `TickContext`, `render_prompt`, `parse_json_block`, `worker_tier`, `run_scout/worker/evaluator/learner/planner/reviewer`, `apply_learnings`. |
| `plugins/agent-loop/runner/prompts/*.md` | **new.** `scout.md`, `worker.md`, `evaluator.md`, `learner.md`, `planner.md`, `reviewer.md`. |
| `plugins/agent-loop/runner/incidents.py` | **new.** `incident_new`, `escalate`, `run_medic`, `handle_incident`, `notify_desktop`. |
| `plugins/agent-loop/runner/migrate.py` | **new.** `LOOP_SCHEMA`, `schema_read`, `legacy_harness_live`, `migrate_1_to_2`, `migrate_2_to_3`, `migrate_loop_dir`. |
| `plugins/agent-loop/runner/sidecar.py` | **new.** `adoptable`, `start`, `stop`, `Supervisor`. |
| `plugins/agent-loop/runner/run.py` | **new.** argv/env, worktree guard, lock, heartbeat, migration, outer loop, the three tick bodies, `sandbox`, `handle_failure`, `mark_blocked`, exit codes. |
| `plugins/agent-loop/run.sh` | **rewritten.** Four-line shim (755): sets `PYTHONPATH` and `exec`s `python3 -m runner.run --shim …`. |
| `plugins/agent-loop/lib/*.sh` | **deleted.** |
| `plugins/agent-loop/tick-prompt.md` | **deleted.** |
| `plugins/agent-loop/tests/{lib,harness,events,migrate}.test.sh` | **deleted.** |
| `plugins/agent-loop/tests/tick-prompt.contract.sh` | **deleted.** |
| `plugins/agent-loop/tests/fixtures/claude` | **rewritten.** `STUB_SCRIPT` directory protocol. |
| `plugins/agent-loop/tests/fixtures/events-calibrate.jsonl` | **new.** A hand-written events stream for the calibrator's unit test. |
| `plugins/agent-loop/tests/run.e2e.test.sh` | **rewritten.** Eleven end-to-end scenarios against the stub. |
| `plugins/agent-loop/tests/runner/_path.py` | **new.** `sys.path` bootstrap imported by every test module. |
| `plugins/agent-loop/tests/runner/test_*.py` | **new.** One module per runner module. |
| `plugins/agent-loop/tests/all.sh` | **modified.** Drops the four deleted suites, adds `python3 -m unittest discover -s tests/runner`. |
| `docs/testing.md` | **modified.** One paragraph: where the runner's unit tests live and how they skip. |

### Runtime files this plan writes

Ephemeral `$LOOP_DIR/runtime/`: `harness.json`, `HEARTBEAT`, `tick.json`, `last-activity`, `schema`, `tickseq`, `incidentseq`, `PAUSE`/`STOP` (read only), `CHECKPOINT.json`, `NEEDS_HUMAN.md`, `ratelimit.json`, `plan-usage.json`, `dashboard.json`, `sidecar.pid`, `dashboard.out`, `incident-<id>.json`, `sprint-<T>.json`, `worker-result.json`, `task-<T>.json`, `gate-<tag>-<n>.txt`.
`runtime/task-<T>.json` (spec §14) is the durable record of one task's state machine and **replaces** the `attempts-<T>.json` the interfaces doc's runtime-file list still names; the interfaces doc's own cross-plan notes carry that correction, and plan C reads `task-<T>.json`.
Durable `$LOOP_DIR/`: `events.jsonl`, `LOOP_USAGE.jsonl`, `LOOP_STATUS.md`, `LOOP_LEARNINGS.md`, `LOOP_CLEANUP.md`, `LOOP_DECISIONS.md` (created empty by the migration; written by plan C), `harness.log` (rotating 10 MB × 3), `artifacts/` (created by the migration; filled by plan B).
**Gone:** `run.log`, `runtime/tick.fifo`, `runtime/tick-result.json`.

Task order is dependency order, bottom-up: every task is independently testable with `python3 -m unittest` before the next one starts.

---

### Task 1: Package skeleton and `util.py`

**Files:**
- Create: `plugins/agent-loop/runner/__init__.py`
- Create: `plugins/agent-loop/runner/util.py`
- Create: `plugins/agent-loop/tests/runner/_path.py`
- Test: `plugins/agent-loop/tests/runner/test_util.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `util.atomic_write(path, text)`, `util.write_json(path, obj)`, `util.read_json(path) -> Optional[Any]`, `util.read_text(path, default="") -> str`, `util.read_int(path, default=0) -> int`, `util.stamp_epoch(path, now=None)`, `util.process_alive(pid, must_contain=None) -> bool`. Every later module imports this one.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/runner/_path.py`:

```python
"""Put the plugin root on sys.path so `import runner.x` works under unittest discover.

`python3 -m unittest discover -s tests/runner` puts tests/runner on sys.path[0],
not the plugin root, so every test module starts with `import _path  # noqa: F401`.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
```

Create `plugins/agent-loop/tests/runner/test_util.py`:

```python
import _path  # noqa: F401
import json
import os
import tempfile
import unittest

from runner import util


class TestAtomicWrite(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_writes_content_and_leaves_no_tmp(self):
        p = os.path.join(self.d, "sub", "f.txt")
        util.atomic_write(p, "hello")
        with open(p) as f:
            self.assertEqual("hello", f.read())
        self.assertFalse(os.path.exists(p + ".tmp"))

    def test_replaces_existing(self):
        p = os.path.join(self.d, "f.txt")
        util.atomic_write(p, "one")
        util.atomic_write(p, "two")
        with open(p) as f:
            self.assertEqual("two", f.read())

    def test_write_json_is_compact_and_roundtrips(self):
        p = os.path.join(self.d, "f.json")
        util.write_json(p, {"a": 1, "b": [2, 3]})
        with open(p) as f:
            raw = f.read()
        self.assertNotIn(" ", raw)
        self.assertEqual({"a": 1, "b": [2, 3]}, json.loads(raw))


class TestReaders(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_read_json_returns_none_for_missing_and_garbage(self):
        self.assertIsNone(util.read_json(os.path.join(self.d, "nope.json")))
        p = os.path.join(self.d, "bad.json")
        util.atomic_write(p, "{not json")
        self.assertIsNone(util.read_json(p))

    def test_read_text_default(self):
        self.assertEqual("fallback", util.read_text(os.path.join(self.d, "x"), "fallback"))

    def test_read_int_handles_missing_garbage_and_float(self):
        self.assertEqual(7, util.read_int(os.path.join(self.d, "x"), 7))
        p = os.path.join(self.d, "n")
        util.atomic_write(p, "42\n")
        self.assertEqual(42, util.read_int(p))
        util.atomic_write(p, "nope")
        self.assertEqual(0, util.read_int(p))

    def test_stamp_epoch_writes_an_integer(self):
        p = os.path.join(self.d, "HEARTBEAT")
        util.stamp_epoch(p, now=1700000000.9)
        self.assertEqual(1700000000, util.read_int(p))


class TestProcessAlive(unittest.TestCase):
    def test_self_is_alive(self):
        self.assertTrue(util.process_alive(os.getpid()))

    def test_bogus_pids_are_dead(self):
        self.assertFalse(util.process_alive(0))
        self.assertFalse(util.process_alive(-1))
        self.assertFalse(util.process_alive("nope"))
        self.assertFalse(util.process_alive(None))

    def test_must_contain_matches_our_own_command_line(self):
        self.assertTrue(util.process_alive(os.getpid(), "python") or
                        util.process_alive(os.getpid(), "Python"))
        self.assertFalse(util.process_alive(os.getpid(), "definitely-not-in-argv"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_*.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'runner'`

- [ ] **Step 3: Write minimal implementation**

Create `plugins/agent-loop/runner/__init__.py` as an empty file (zero bytes).

Create `plugins/agent-loop/runner/util.py`:

```python
"""Primitives every runner module needs: atomic writes, JSON/text I/O, pid liveness.

This module imports nothing from `runner`, so every other module may import it
without risking a cycle.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any, Optional


def atomic_write(path: str, text: str) -> None:
    """Write `text` to `path` via `<path>.tmp` + `os.replace`.

    The runtime-file contract: an observer (the dashboard, the medic, a second
    harness) must never read a half-written file, so the rename is the only
    moment the new content becomes visible.
    """
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def write_json(path: str, obj: Any) -> None:
    """One compact JSON document plus a trailing newline, written atomically."""
    atomic_write(path, json.dumps(obj, separators=(",", ":")) + "\n")


def read_json(path: str) -> Optional[Any]:
    """Parsed JSON, or None when the file is missing, unreadable or malformed."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def read_text(path: str, default: str = "") -> str:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return default


def read_int(path: str, default: int = 0) -> int:
    """First whitespace-delimited number in a one-line file, floored to an int."""
    txt = read_text(path).strip()
    if not txt:
        return default
    try:
        return int(float(txt.split()[0]))
    except (ValueError, IndexError):
        return 0


def stamp_epoch(path: str, now: Optional[float] = None) -> None:
    atomic_write(path, "%d\n" % int(time.time() if now is None else now))


def process_alive(pid: Any, must_contain: Optional[str] = None) -> bool:
    """True when `pid` exists and, when given, its command line contains `must_contain`.

    The command check defeats PID reuse: a recycled pid belonging to some other
    program must not read as a live harness. Deliberately not `pgrep` and not
    `ps | grep` — both match the observing agent's own tool call.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass                       # alive, just owned by another user
    except OSError:
        return False
    if not must_contain:
        return True
    try:
        proc = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return must_contain in proc.stdout.decode("utf-8", "replace")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_*.py' -v`
Expected: PASS — 10 tests, `OK`

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/runner/__init__.py plugins/agent-loop/runner/util.py \
        plugins/agent-loop/tests/runner/_path.py plugins/agent-loop/tests/runner/test_util.py
git commit -F - <<'EOF'
agent-loop: runner package skeleton and util primitives

Atomic <path>.tmp + os.replace writes, JSON/text readers that degrade to a
default instead of raising, and the pid+command liveness check the dashboard
and the four skills already use. Imports nothing from runner, so every later
module can import it.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 2: `events.py` — the event substrate and the usage ledger

**Files:**
- Create: `plugins/agent-loop/runner/events.py`
- Test: `plugins/agent-loop/tests/runner/test_events.py`

**Interfaces:**
- Consumes: `util.atomic_write`, `util.read_int`.
- Produces: `EventLog(path, seq_path)` with `.emit(type, **fields)` and `.seq`; `UsageLog(path)` with `.write_tick(tick, mode, duration_s, by_model)`. `by_model` values are anything with the five `Usage` attributes (`cost_usd`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`) or a plain dict of them — `events.py` never imports `claude_proc`, so `claude_proc` is free to import nothing.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_events.py`:

```python
import _path  # noqa: F401
import json
import os
import tempfile
import unittest

from runner.events import EventLog, UsageLog


def read_lines(path):
    with open(path) as f:
        return [json.loads(ln) for ln in f if ln.strip()]


class TestEventLog(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.ev = os.path.join(self.d, "events.jsonl")
        self.seq = os.path.join(self.d, "eventseq")

    def test_envelope_has_t_seq_type_then_payload(self):
        log = EventLog(self.ev, self.seq)
        log.emit("loop_start", pid=42, host="box")
        rec = read_lines(self.ev)[0]
        self.assertEqual(["t", "seq", "type", "pid", "host"], list(rec.keys()))
        self.assertEqual("loop_start", rec["type"])
        self.assertEqual(42, rec["pid"])
        self.assertIsInstance(rec["t"], int)

    def test_seq_is_monotonic_within_a_run(self):
        log = EventLog(self.ev, self.seq)
        for _ in range(3):
            log.emit("tick_start", tick=1)
        self.assertEqual([1, 2, 3], [r["seq"] for r in read_lines(self.ev)])

    def test_seq_continues_across_harness_runs(self):
        EventLog(self.ev, self.seq).emit("tick_start", tick=1)
        EventLog(self.ev, self.seq).emit("tick_start", tick=2)
        self.assertEqual([1, 2], [r["seq"] for r in read_lines(self.ev)])

    def test_one_line_per_event_even_with_newlines_in_a_field(self):
        log = EventLog(self.ev, self.seq)
        log.emit("incident", detail="line one\nline two")
        with open(self.ev) as f:
            self.assertEqual(1, len(f.readlines()))
        self.assertEqual("line one\nline two", read_lines(self.ev)[0]["detail"])

    def test_nested_payload_survives(self):
        log = EventLog(self.ev, self.seq)
        log.emit("tick_end", tick=1, by_model={"m": {"cost_usd": 0.5}})
        self.assertEqual(0.5, read_lines(self.ev)[0]["by_model"]["m"]["cost_usd"])

    def test_empty_path_is_a_silent_no_op(self):
        EventLog("", self.seq).emit("loop_start")   # must not raise


class TestUsageLog(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.path = os.path.join(self.d, "LOOP_USAGE.jsonl")

    def test_row_shape_matches_what_serve_py_reads(self):
        UsageLog(self.path).write_tick(
            3, "execute", 91,
            {"model-a": {"cost_usd": 1.5, "input_tokens": 10, "output_tokens": 4,
                         "cache_read_tokens": 100, "cache_creation_tokens": 2}})
        rec = read_lines(self.path)[0]
        self.assertEqual(3, rec["tick"])
        self.assertEqual("execute", rec["mode"])
        self.assertEqual(91, rec["duration_s"])
        self.assertEqual(1.5, rec["cost_usd"])
        self.assertEqual(10, rec["input_tokens"])
        self.assertEqual(4, rec["output_tokens"])
        self.assertEqual(100, rec["by_model"]["model-a"]["cache_read_tokens"])
        self.assertNotIn("cumulative_cost_usd", rec)

    def test_sums_across_models(self):
        UsageLog(self.path).write_tick(1, "execute", 5, {
            "a": {"cost_usd": 1.0, "input_tokens": 1, "output_tokens": 1,
                  "cache_read_tokens": 0, "cache_creation_tokens": 0},
            "b": {"cost_usd": 2.0, "input_tokens": 2, "output_tokens": 3,
                  "cache_read_tokens": 0, "cache_creation_tokens": 0}})
        rec = read_lines(self.path)[0]
        self.assertEqual(3.0, rec["cost_usd"])
        self.assertEqual(3, rec["input_tokens"])
        self.assertEqual(4, rec["output_tokens"])

    def test_empty_by_model_still_writes_a_row(self):
        UsageLog(self.path).write_tick(9, "execute", 0, {})
        rec = read_lines(self.path)[0]
        self.assertEqual({}, rec["by_model"])
        self.assertEqual(0, rec["cost_usd"])

    def test_accepts_dataclass_like_objects(self):
        class U(object):
            cost_usd = 0.25
            input_tokens = 7
            output_tokens = 8
            cache_read_tokens = 9
            cache_creation_tokens = 10

        UsageLog(self.path).write_tick(1, "execute", 1, {"m": U()})
        rec = read_lines(self.path)[0]
        self.assertEqual(0.25, rec["cost_usd"])
        self.assertEqual(9, rec["by_model"]["m"]["cache_read_tokens"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_events*.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'runner.events'`

- [ ] **Step 3: Write minimal implementation**

Create `plugins/agent-loop/runner/events.py`:

```python
"""The event substrate: one compact JSON object per line, and the usage ledger.

`events.jsonl` is what the dashboard tails and what the medic and postmortem
read back. The envelope is fixed — `t`, `seq`, `type`, then the payload — and the
lifecycle types `serve.py` understands keep their exact field names. `seq` is
persisted under `runtime/` so it stays monotonic across resume, which is what
lets a consumer tell a gap from a restart.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict

from . import util

_USAGE_FIELDS = ("cost_usd", "input_tokens", "output_tokens",
                 "cache_read_tokens", "cache_creation_tokens")


def _usage_dict(u: Any) -> Dict[str, Any]:
    """Normalise a Usage dataclass or a plain dict to the five ledger fields."""
    out = {}
    for name in _USAGE_FIELDS:
        if isinstance(u, dict):
            val = u.get(name, 0)
        else:
            val = getattr(u, name, 0)
        out[name] = val
    return out


class EventLog(object):
    """Append-only writer for events.jsonl. One instance per harness process."""

    def __init__(self, path: str, seq_path: str):
        self.path = path
        self.seq_path = seq_path
        self.seq = util.read_int(seq_path, 0) if seq_path else 0
        self._lock = threading.Lock()
        if self.path:
            parent = os.path.dirname(os.path.abspath(self.path))
            if parent:
                os.makedirs(parent, exist_ok=True)

    def emit(self, type: str, **fields: Any) -> None:
        """Append `{"t":…,"seq":…,"type":…, **fields}` as one line.

        An empty path is a silent no-op so a headless invocation that wants no
        events never errors. Thread-safe: the heartbeat and the sidecar
        supervisor both emit.
        """
        if not self.path:
            return
        with self._lock:
            self.seq += 1
            rec = {"t": int(time.time()), "seq": self.seq, "type": type}
            rec.update(fields)
            line = json.dumps(rec, separators=(",", ":"))
            try:
                with open(self.path, "a") as f:
                    f.write(line + "\n")
            except OSError:
                return
            if self.seq_path:
                util.atomic_write(self.seq_path, str(self.seq))


class UsageLog(object):
    """Append-only writer for LOOP_USAGE.jsonl (postmortem + dashboard input).

    cost_usd is raw and informational: it is never summed into a budget, never
    projected, and never a control input.
    """

    def __init__(self, path: str):
        self.path = path

    def write_tick(self, tick: int, mode: str, duration_s: int,
                   by_model: Dict[str, Any]) -> None:
        models = dict((k, _usage_dict(v)) for k, v in (by_model or {}).items())
        rec = {
            "tick": tick,
            "mode": mode,
            "cost_usd": sum(m["cost_usd"] for m in models.values()),
            "input_tokens": sum(m["input_tokens"] for m in models.values()),
            "output_tokens": sum(m["output_tokens"] for m in models.values()),
            "duration_s": duration_s,
            "by_model": models,
        }
        try:
            with open(self.path, "a") as f:
                f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        except OSError:
            return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_*.py' -v`
Expected: PASS — 20 tests, `OK`

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/runner/events.py plugins/agent-loop/tests/runner/test_events.py
git commit -F - <<'EOF'
agent-loop: events.jsonl writer and the usage ledger in Python

One process, one writer, no jq fork per event. seq is persisted under runtime/
so it stays monotonic across resume; the envelope and every lifecycle field
name serve.py reads are unchanged. UsageLog duck-types Usage so events.py
never imports claude_proc.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 3: `config.py` — `LOOP_CONFIG.md`, tiers and budgets

**Files:**
- Create: `plugins/agent-loop/runner/config.py`
- Test: `plugins/agent-loop/tests/runner/test_config.py`

**Interfaces:**
- Consumes: `util.read_text`.
- Produces: `LoopConfig` (fields exactly as in the interfaces doc), `load_config(path) -> LoopConfig`, `model_for(cfg, tier) -> str`, `next_tier(tier) -> str`, `phase_limit(cfg, phase) -> int`, and the constants `TIER_ORDER`, `DEFAULT_LIMITS`, `DEFAULT_ROLE_TIERS`, `PHASE_LIMIT_KEY`.
- **`Limits:` key names are the interfaces doc's, verbatim:** `tick_timeout`, `scout_timeout`, `worker_timeout`, `wrapup_timeout`, `eval_timeout`, `judge_timeout`, `planner_timeout`, `gate_cmd_timeout`, `worker_resume_max`, `worker_budget_usd`, `max_attempts`. `phase_limit(cfg, "worker")` reads `worker_timeout`; the phase→key map is `PHASE_LIMIT_KEY`. `reviewer_timeout` and `learner_timeout` are not part of the documented line but are accepted and defaulted, because `phase_limit` is called for both.
- `next_tier` is a **helper only**. Nothing in plan A's failure path calls it; plan C's Judge `escalate` decision does.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_config.py`:

```python
import _path  # noqa: F401
import os
import tempfile
import unittest

from runner import config

FULL = """# Loop Config
Started: 2026-09-13T00:00:00Z
Goal: port the internal app
Loop type: refactor
Granularity: segmented
TDD mode: tdd-per-task
Verification pipeline: lint tsc build test
Limits: tick_timeout=900 worker_timeout=1200 worker_resume_max=2
Tiers: cheap=tiny standard=mid most-capable=big
Decision policy: conservative
Blocker policy: halt
Branch: agent-loop-port
Worktree: /tmp/wt
Segment count: 4
Spec: docs/spec.md
Plan: docs/plan.md
Dashboard: off   # no sidecar
Medic: notify
Medic model: mid
Render: ui_globs=apps/*/src/**,packages/ui/**
Planner tier: most-capable
Scout tier: standard
Worker tier: standard
Evaluator tier:
"""

MINIMAL = "Worktree: /tmp/wt\n"


def write(text):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "LOOP_CONFIG.md")
    with open(p, "w") as f:
        f.write(text)
    return p


class TestLoadConfig(unittest.TestCase):
    def test_scalar_fields(self):
        c = config.load_config(write(FULL))
        self.assertEqual("/tmp/wt", c.worktree)
        self.assertEqual("agent-loop-port", c.branch)
        self.assertEqual("port the internal app", c.goal)
        self.assertEqual("segmented", c.granularity)
        self.assertEqual("tdd-per-task", c.tdd_mode)
        self.assertEqual(["lint", "tsc", "build", "test"], c.verification)
        self.assertEqual("halt", c.blocker_policy)
        self.assertEqual("conservative", c.decision_policy)
        self.assertEqual("docs/spec.md", c.spec_path)
        self.assertEqual("docs/plan.md", c.plan_path)

    def test_first_word_wins_for_mode_fields_with_trailing_comments(self):
        c = config.load_config(write(FULL))
        self.assertEqual("off", c.dashboard)
        self.assertEqual("notify", c.medic)
        self.assertEqual("mid", c.medic_model)

    def test_limits_merge_over_defaults(self):
        c = config.load_config(write(FULL))
        self.assertEqual(900, c.limits["tick_timeout"])
        self.assertEqual(1200, c.limits["worker_timeout"])
        self.assertEqual(2, c.limits["worker_resume_max"])
        self.assertEqual(480, c.limits["scout_timeout"])   # default survives
        self.assertEqual(600, c.limits["gate_cmd_timeout"])
        self.assertEqual(3, c.limits["max_attempts"])
        self.assertEqual(6, c.limits["worker_budget_usd"])

    def test_the_documented_limits_keys_are_exactly_the_interface_contract(self):
        keys = set(config.load_config(write(MINIMAL)).limits)
        self.assertEqual(set(), {
            "tick_timeout", "scout_timeout", "worker_timeout", "wrapup_timeout",
            "eval_timeout", "judge_timeout", "planner_timeout", "gate_cmd_timeout",
            "worker_resume_max", "worker_budget_usd", "max_attempts"} - keys)

    def test_a_v2_style_bare_role_key_is_carried_but_never_read(self):
        c = config.load_config(write(MINIMAL + "Limits: worker=99\n"))
        self.assertEqual(1500, config.phase_limit(c, "worker"))

    def test_tiers_and_role_tiers(self):
        c = config.load_config(write(FULL))
        self.assertEqual({"cheap": "tiny", "standard": "mid", "most-capable": "big"}, c.tiers)
        self.assertEqual("most-capable", c.role_tiers["planner"])
        self.assertEqual("standard", c.role_tiers["scout"])
        self.assertEqual("", c.role_tiers["evaluator"])
        self.assertEqual("cheap", c.role_tiers["learner"])

    def test_render_line_yields_ui_globs(self):
        c = config.load_config(write(FULL))
        self.assertEqual(["apps/*/src/**", "packages/ui/**"], c.render["ui_globs"])
        self.assertIn("ui_globs=", c.render["raw"])

    def test_defaults_when_everything_is_absent(self):
        c = config.load_config(write(MINIMAL))
        self.assertEqual("autonomous", c.decision_policy)
        self.assertEqual("continue-independent", c.blocker_policy)
        self.assertEqual("auto", c.dashboard)
        self.assertEqual("auto", c.medic)
        self.assertEqual({}, c.tiers)
        self.assertEqual({}, c.render)
        self.assertEqual(1800, c.limits["tick_timeout"])
        self.assertEqual(1500, c.limits["worker_timeout"])
        self.assertEqual(300, c.limits["wrapup_timeout"])
        self.assertEqual(360, c.limits["judge_timeout"])
        self.assertEqual(900, c.limits["planner_timeout"])
        self.assertEqual(1, c.limits["worker_resume_max"])
        self.assertEqual([], c.verification)

    def test_v2_config_with_orchestrator_model_still_parses(self):
        c = config.load_config(write(MINIMAL + "Orchestrator model: mid\nLimits: tick_timeout=1200\n"))
        self.assertEqual(1200, c.limits["tick_timeout"])
        self.assertFalse(hasattr(c, "orchestrator_model"))

    def test_missing_file_raises(self):
        with self.assertRaises(ValueError):
            config.load_config("/nonexistent/LOOP_CONFIG.md")


class TestTiers(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load_config(write(FULL))

    def test_model_for_resolves_through_the_tier_map(self):
        self.assertEqual("mid", config.model_for(self.cfg, "standard"))
        self.assertEqual("big", config.model_for(self.cfg, "most-capable"))

    def test_model_for_unmapped_tier_means_inherit_the_default(self):
        cfg = config.load_config(write(MINIMAL))
        self.assertEqual("", config.model_for(cfg, "standard"))

    def test_model_for_rejects_an_unknown_tier(self):
        with self.assertRaises(ValueError):
            config.model_for(self.cfg, "turbo")

    def test_next_tier_steps_up_and_clamps(self):
        # A helper for plan C's Judge `escalate` decision. Nothing in plan A's
        # failure path calls it: there is no ladder.
        self.assertEqual("standard", config.next_tier("cheap"))
        self.assertEqual("most-capable", config.next_tier("standard"))
        self.assertEqual("most-capable", config.next_tier("most-capable"))

    def test_next_tier_rejects_an_unknown_tier(self):
        with self.assertRaises(ValueError):
            config.next_tier("")


class TestPhaseLimit(unittest.TestCase):
    def test_configured_value_wins_and_defaults_fill_in(self):
        cfg = config.load_config(write(FULL))
        self.assertEqual(1200, config.phase_limit(cfg, "worker"))
        self.assertEqual(480, config.phase_limit(cfg, "scout"))
        self.assertEqual(360, config.phase_limit(cfg, "judge"))

    def test_every_phase_maps_onto_its_documented_limits_key(self):
        cfg = config.load_config(write(MINIMAL))
        for phase, secs in (("scout", 480), ("worker", 1500), ("wrapup", 300),
                            ("evaluator", 480), ("judge", 360), ("planner", 900),
                            ("reviewer", 900), ("learner", 180),
                            ("gate_cmd", 600), ("tick_timeout", 1800)):
            self.assertEqual(secs, config.phase_limit(cfg, phase), phase)

    def test_the_evaluator_phase_reads_eval_timeout(self):
        cfg = config.load_config(write(MINIMAL + "Limits: eval_timeout=42\n"))
        self.assertEqual(42, config.phase_limit(cfg, "evaluator"))

    def test_unknown_phase_falls_back_to_the_gate_budget(self):
        cfg = config.load_config(write(MINIMAL))
        self.assertEqual(600, config.phase_limit(cfg, "something-else"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_config*.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'runner.config'`

- [ ] **Step 3: Write minimal implementation**

Create `plugins/agent-loop/runner/config.py`:

```python
"""LOOP_CONFIG.md — the loop's durable settings, parsed once per harness run.

Keys are `Key: value`, exactly as v2 wrote and serve.py reads them. `Limits:`
and `Tiers:` are space-separated `k=v` tokens. `Tiers:` is the ONLY place a
concrete model name appears anywhere in this plugin; everything in code names a
tier and resolves it through `model_for`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

from . import util

TIER_ORDER = ("cheap", "standard", "most-capable")

# Spec §4.2/§4.6. Key names are the interfaces doc's, verbatim: every budget is
# `<role>_timeout`, and the last three are not seconds at all. `reviewer_timeout`
# and `learner_timeout` are not part of the documented `Limits:` line but are
# accepted and defaulted, because phase_limit is called for both roles.
DEFAULT_LIMITS = {
    "tick_timeout": 1800,
    "scout_timeout": 480,
    "worker_timeout": 1500,
    "wrapup_timeout": 300,
    "eval_timeout": 480,
    "judge_timeout": 360,
    "planner_timeout": 900,
    "reviewer_timeout": 900,
    "learner_timeout": 180,
    "gate_cmd_timeout": 600,
    "worker_resume_max": 1,
    "worker_budget_usd": 6,
    "max_attempts": 3,
}

# The phase name the harness uses -> the `Limits:` key that budgets it. The two
# differ wherever the spec's key is shorter than the phase (`evaluator` reads
# `eval_timeout`), so nothing may index cfg.limits by phase name directly.
PHASE_LIMIT_KEY = {
    "scout": "scout_timeout",
    "worker": "worker_timeout",
    "wrapup": "wrapup_timeout",
    "evaluator": "eval_timeout",
    "judge": "judge_timeout",
    "planner": "planner_timeout",
    "reviewer": "reviewer_timeout",
    "learner": "learner_timeout",
    "gate_cmd": "gate_cmd_timeout",
    "tick_timeout": "tick_timeout",
}

# Spec §4.2 "model tier (default)" column. An empty string means "no override":
# the Evaluator's tier is class-governed by the task's flag, not by config.
DEFAULT_ROLE_TIERS = {
    "planner": "most-capable",
    "reviewer": "most-capable",
    "judge": "most-capable",
    "scout": "standard",
    "worker": "standard",
    "evaluator": "",
    "learner": "cheap",
}


@dataclass
class LoopConfig:
    worktree: str = ""
    branch: str = ""
    goal: str = ""
    granularity: str = "single"
    tdd_mode: str = "none"
    verification: List[str] = field(default_factory=list)
    blocker_policy: str = "continue-independent"
    dashboard: str = "auto"
    medic: str = "auto"
    medic_model: str = ""
    tiers: Dict[str, str] = field(default_factory=dict)
    role_tiers: Dict[str, str] = field(default_factory=dict)
    limits: Dict[str, int] = field(default_factory=dict)
    decision_policy: str = "autonomous"
    render: Dict[str, Any] = field(default_factory=dict)
    spec_path: str = ""
    plan_path: str = ""


def _field_value(text: str, name: str) -> Any:
    m = re.search(r"^%s:[ \t]*(.*)$" % re.escape(name), text, re.MULTILINE)
    return m.group(1).strip() if m else None


def _first_word(value: Any, default: str) -> str:
    """First whitespace-delimited token, tolerating a trailing inline comment."""
    parts = (value or "").split()
    return parts[0] if parts else default


def _kv_tokens(value: Any) -> Dict[str, str]:
    out = {}
    for tok in (value or "").split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = v
    return out


def load_config(path: str) -> LoopConfig:
    """Parse LOOP_CONFIG.md. Raises ValueError when the file is missing or empty."""
    text = util.read_text(path)
    if not text.strip():
        raise ValueError("no readable LOOP_CONFIG at %s" % path)

    limits = dict(DEFAULT_LIMITS)
    for k, v in _kv_tokens(_field_value(text, "Limits")).items():
        try:
            limits[k] = int(v)
        except ValueError:
            pass                      # a garbage token keeps the default

    tiers = {}
    for k, v in _kv_tokens(_field_value(text, "Tiers")).items():
        if k in TIER_ORDER:
            tiers[k] = v

    role_tiers = dict(DEFAULT_ROLE_TIERS)
    for role in list(role_tiers):
        v = _field_value(text, "%s tier" % role.capitalize())
        if v is not None:
            role_tiers[role] = v.strip()

    render: Dict[str, Any] = {}
    render_line = _field_value(text, "Render")
    if render_line:
        globs = _kv_tokens(render_line).get("ui_globs", "")
        render = {"raw": render_line,
                  "ui_globs": [g for g in globs.split(",") if g]}

    return LoopConfig(
        worktree=_field_value(text, "Worktree") or "",
        branch=_field_value(text, "Branch") or "",
        goal=_field_value(text, "Goal") or "",
        granularity=_first_word(_field_value(text, "Granularity"), "single"),
        tdd_mode=_first_word(_field_value(text, "TDD mode"), "none"),
        verification=(_field_value(text, "Verification pipeline") or "").split(),
        blocker_policy=_first_word(_field_value(text, "Blocker policy"),
                                   "continue-independent"),
        dashboard=_first_word(_field_value(text, "Dashboard"), "auto"),
        medic=_first_word(_field_value(text, "Medic"), "auto"),
        medic_model=_first_word(_field_value(text, "Medic model"), ""),
        tiers=tiers,
        role_tiers=role_tiers,
        limits=limits,
        decision_policy=_first_word(_field_value(text, "Decision policy"), "autonomous"),
        render=render,
        spec_path=_field_value(text, "Spec") or "",
        plan_path=_field_value(text, "Plan") or "",
    )


def model_for(cfg: LoopConfig, tier: str) -> str:
    """The model alias for a tier.

    An unknown tier is a programming error and raises. A *known* tier with no
    mapping returns "", which callers turn into "pass no --model flag" — the
    user's Claude default. That is the only reason no DEFAULT_TIERS constant
    exists here: naming a model in Python would break the one-place rule.
    """
    if tier not in TIER_ORDER:
        raise ValueError("unknown tier %r (expected one of %s)" % (tier, ", ".join(TIER_ORDER)))
    return cfg.tiers.get(tier, "")


def next_tier(tier: str) -> str:
    """One tier up, clamped at most-capable.

    A helper, not a policy. Spec §11 item 4: the tier of a re-attempt is the
    Judge's call from the Worker checkpoint, never a fixed ladder — so nothing in
    the failure path calls this. Plan C's `escalate` decision maps onto it.
    """
    if tier not in TIER_ORDER:
        raise ValueError("unknown tier %r" % tier)
    i = TIER_ORDER.index(tier)
    return TIER_ORDER[min(i + 1, len(TIER_ORDER) - 1)]


def phase_limit(cfg: LoopConfig, phase: str) -> int:
    """Wall-clock seconds for one phase; an unlisted phase gets the gate budget.

    The phase name is mapped through PHASE_LIMIT_KEY first, so `worker` reads
    `worker_timeout` and `evaluator` reads `eval_timeout`. A v2 config carrying a
    bare `worker=1200` token parses (it is kept in `limits`) but is never read —
    the key it would have to use is `worker_timeout`.
    """
    key = PHASE_LIMIT_KEY.get(phase, phase)
    if key in cfg.limits:
        return int(cfg.limits[key])
    return int(DEFAULT_LIMITS.get(key, DEFAULT_LIMITS["gate_cmd_timeout"]))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_*.py' -v`
Expected: PASS — 39 tests, `OK`

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/runner/config.py plugins/agent-loop/tests/runner/test_config.py
git commit -F - <<'EOF'
agent-loop: LOOP_CONFIG parsing, tier resolution and phase budgets

Same keys v2 wrote plus Tiers:, Decision policy: and Render:; Orchestrator
model: is read by nobody but still parses, so a v2 config keeps working.
model_for returns "" for an unmapped tier (inherit the Claude default) rather
than naming a model in Python. Limits: keys are the interfaces doc's
<role>_timeout names and phase_limit maps a phase onto its key, so worker reads
worker_timeout and evaluator reads eval_timeout. next_tier exists as a helper
for plan C's Judge escalate decision; nothing in the failure path calls it,
because spec 11.4 makes the re-attempt tier a judgement, not a ladder.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 4: `plan.py` — the plan grammar, eligibility and in-place edits

**Files:**
- Create: `plugins/agent-loop/runner/plan.py`
- Test: `plugins/agent-loop/tests/runner/test_plan.py`

**Interfaces:**
- Consumes: `util.read_text`, `util.atomic_write`.
- Produces: `Task`, `Segment`, `Plan.load(path)`, `Plan.segments()`, `Plan.tasks()`, `Plan.task(task_id)`, `Plan.eligible()`, `Plan.mode()`, `Plan.set_state(task_id, state, sha=None)`, `Plan.append_tasks(segment_name, rows)`, `Plan.stamp_reviewed(segment_name, sha)`, `Plan.next_ids(count)`, `Plan.split(task_id, sub_rows)`, `Plan.dependents(task_id)`, `Plan.next_review_segment()`, `Plan.next_plan_segment()`, `Plan.save()`, and the constants `TASK_RE`, `ID_RE`, `GLYPH_STATE`, `STATE_GLYPH`, `DONE_STATES`, `BLOCKED_UPSTREAM`.
- Grammar additions over v2: `| no-ui`, `| copy_of: T<n>`, `| blocked_by: T<n>,T<m>`, `| split_of: T<n>`.
- **Ids stay `T<digits>`.** `ID_RE` is serve.py's `\bT\d+\b` unchanged. `Plan.split` allocates the next free numeric ids (`max(existing) + 1 …`), rewrites each sub row's own id to the allocated one, appends `| split_of: T<parent>`, and marks the parent `[-] … split→T74,T75`. No `T60a` anywhere: `\bT\d+\b` does not match it, so a lettered id disappears from every dashboard count.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_plan.py`:

```python
import _path  # noqa: F401
import os
import re
import tempfile
import unittest

from runner import plan as planmod
from runner.plan import Plan

SAMPLE = """# Loop Plan

Legend: [ ] pending | [~] in-progress | [x] done(+SHA) | [!] blocked | [-] skipped

## Segment A: bootstrap
Reviewed: abc1234
- [x] T1: Scaffold the package done(+deadbee)
- [-] T2: Drop the old shim

## Segment B: wiring
Goal: wire it up
- [ ] T3: Add the parser | depends_on: T1 | complex
- [ ] T4: Add the writer | depends_on: T3 | model: mid
- [ ] T5: Copy the header from the reference | copy_of: T3 | no-ui
- [~] T6: In flight | mechanical

## Segment C: polish
"""


def write(text):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "LOOP_PLAN.md")
    with open(p, "w") as f:
        f.write(text)
    return p


class TestParsing(unittest.TestCase):
    def setUp(self):
        self.p = Plan.load(write(SAMPLE))

    def test_every_task_row_is_found_in_order(self):
        self.assertEqual(["T1", "T2", "T3", "T4", "T5", "T6"],
                         [t.id for t in self.p.tasks()])

    def test_states_come_from_the_glyph(self):
        states = dict((t.id, t.state) for t in self.p.tasks())
        self.assertEqual("done", states["T1"])
        self.assertEqual("skipped", states["T2"])
        self.assertEqual("pending", states["T3"])
        self.assertEqual("doing", states["T6"])

    def test_segment_membership_and_headings(self):
        self.assertEqual(["Segment A: bootstrap", "Segment B: wiring", "Segment C: polish"],
                         [s.name for s in self.p.segments()])
        self.assertEqual("Segment B: wiring", self.p.task("T4").segment)
        self.assertEqual([], self.p.segments()[2].tasks)

    def test_reviewed_sha_is_read_from_under_the_heading(self):
        self.assertEqual("abc1234", self.p.segments()[0].reviewed_sha)
        self.assertIsNone(self.p.segments()[1].reviewed_sha)

    def test_metadata_flags(self):
        t3, t4, t5, t6 = (self.p.task(i) for i in ("T3", "T4", "T5", "T6"))
        self.assertEqual(["T1"], t3.depends_on)
        self.assertEqual("complex", t3.class_flag)
        self.assertEqual("mid", t4.model)
        self.assertEqual("T3", t5.copy_of)
        self.assertTrue(t5.no_ui)
        self.assertFalse(t3.no_ui)
        self.assertEqual("mechanical", t6.class_flag)
        self.assertIsNone(t4.class_flag)

    def test_title_is_the_text_before_the_first_divider_without_the_id(self):
        self.assertEqual("Add the parser", self.p.task("T3").title)
        self.assertEqual("Scaffold the package done(+deadbee)", self.p.task("T1").title)

    def test_sha_is_read_back_off_a_done_row(self):
        self.assertEqual("deadbee", self.p.task("T1").sha)
        self.assertIsNone(self.p.task("T3").sha)

    def test_blocked_by_and_split_of_parse(self):
        p = Plan.load(write("## S\n- [ ] T74: sub one | blocked_by: T7,T8 | split_of: T60\n"))
        t = p.tasks()[0]
        self.assertEqual("T74", t.id)
        self.assertEqual(["T7", "T8"], t.blocked_by)
        self.assertEqual("T60", t.split_of)

    def test_a_lettered_id_is_not_an_id(self):
        # serve.py greps \bT\d+\b; T60a would vanish from every dashboard count,
        # so this module must not invent one either.
        p = Plan.load(write("## S\n- [ ] T60a: never written by this module\n"))
        self.assertEqual("", p.tasks()[0].id)

    def test_blocked_upstream_marker_becomes_a_state(self):
        p = Plan.load(write("## S\n- [ ] T9: later [blocked-upstream]\n"))
        self.assertEqual("blocked-upstream", p.tasks()[0].state)

    def test_rows_before_the_first_heading_belong_to_the_empty_segment(self):
        p = Plan.load(write("- [ ] T1: flat\n- [ ] T2: also flat\n"))
        self.assertEqual(["T1", "T2"], [t.id for t in p.tasks()])
        self.assertEqual("", p.tasks()[0].segment)
        self.assertEqual([], p.segments())


class TestEligibility(unittest.TestCase):
    def setUp(self):
        self.p = Plan.load(write(SAMPLE))

    def test_pending_with_satisfied_depends_on_is_eligible(self):
        self.assertEqual(["T3", "T5"], [t.id for t in self.p.eligible()])

    def test_skipped_dependency_counts_as_satisfied(self):
        p = Plan.load(write("## S\n- [-] T1: dropped\n- [ ] T2: next | depends_on: T1\n"))
        self.assertEqual(["T2"], [t.id for t in p.eligible()])

    def test_blocked_by_suppresses_eligibility(self):
        p = Plan.load(write("## S\n- [ ] T1: a\n- [ ] T2: b | blocked_by: T1\n"))
        self.assertEqual(["T1"], [t.id for t in p.eligible()])

    def test_blocked_upstream_is_never_eligible(self):
        p = Plan.load(write("## S\n- [ ] T1: a [blocked-upstream]\n- [ ] T2: b\n"))
        self.assertEqual(["T2"], [t.id for t in p.eligible()])

    def test_dependents_are_transitive(self):
        p = Plan.load(write("## S\n- [ ] T1: a\n- [ ] T2: b | depends_on: T1\n"
                            "- [ ] T3: c | depends_on: T2\n- [ ] T4: d\n"))
        self.assertEqual(["T2", "T3"], [t.id for t in p.dependents("T1")])


class TestMode(unittest.TestCase):
    def test_execute_when_something_is_eligible(self):
        self.assertEqual("execute", Plan.load(write(SAMPLE)).mode())

    def test_review_when_a_segment_is_complete_and_unstamped(self):
        p = Plan.load(write("## S\n- [x] T1: a\n- [-] T2: b\n\n## T\n- [ ] T3: c\n"))
        self.assertEqual("review", p.mode())
        self.assertEqual("S", p.next_review_segment().name)

    def test_plan_when_a_segment_is_empty_and_nothing_is_eligible(self):
        p = Plan.load(write("## S\nReviewed: aaa\n- [x] T1: a\n\n## T\n"))
        self.assertEqual("plan", p.mode())
        self.assertEqual("T", p.next_plan_segment().name)

    def test_execute_beats_plan_while_work_remains(self):
        p = Plan.load(write("## S\nReviewed: aaa\n- [ ] T1: a\n\n## T\n"))
        self.assertEqual("execute", p.mode())

    def test_done_when_nothing_is_open_and_nothing_is_unplanned(self):
        p = Plan.load(write("## S\nReviewed: aaa\n- [x] T1: a\n- [-] T2: b\n"))
        self.assertEqual("done", p.mode())

    def test_flat_plan_reaches_done_without_review(self):
        p = Plan.load(write("- [x] T1: a\n- [x] T2: b\n"))
        self.assertEqual("done", p.mode())

    def test_stuck_when_work_remains_but_nothing_can_run(self):
        p = Plan.load(write("## S\nReviewed: aaa\n- [!] T1: blocked\n- [ ] T2: b | depends_on: T1\n"))
        self.assertEqual("stuck", p.mode())


class TestEdits(unittest.TestCase):
    def setUp(self):
        self.path = write(SAMPLE)
        self.p = Plan.load(self.path)

    def test_set_state_flips_the_glyph_in_place_without_touching_the_text(self):
        self.p.set_state("T3", "doing")
        self.assertEqual("doing", self.p.task("T3").state)
        self.assertIn("- [~] T3: Add the parser | depends_on: T1 | complex",
                      "\n".join(self.p.lines))

    def test_set_state_done_appends_the_sha_exactly_once(self):
        self.p.set_state("T3", "done", sha="feed123")
        self.p.set_state("T3", "done", sha="feed123")
        row = [ln for ln in self.p.lines if "T3:" in ln][0]
        self.assertEqual(1, row.count("done(+feed123)"))
        self.assertTrue(row.startswith("- [x] T3: Add the parser"))
        self.assertEqual("feed123", self.p.task("T3").sha)

    def test_set_state_blocked_upstream_keeps_the_pending_glyph(self):
        self.p.set_state("T4", "blocked-upstream")
        row = [ln for ln in self.p.lines if "T4:" in ln][0]
        self.assertTrue(row.startswith("- [ ] "))
        self.assertTrue(row.endswith(planmod.BLOCKED_UPSTREAM))
        self.assertEqual("blocked-upstream", self.p.task("T4").state)

    def test_set_state_clears_the_marker_when_the_task_unblocks(self):
        self.p.set_state("T4", "blocked-upstream")
        self.p.set_state("T4", "pending")
        self.assertNotIn(planmod.BLOCKED_UPSTREAM, "\n".join(self.p.lines))
        self.assertEqual("pending", self.p.task("T4").state)

    def test_set_state_rejects_an_unknown_task_or_state(self):
        with self.assertRaises(KeyError):
            self.p.set_state("T99", "done")
        with self.assertRaises(ValueError):
            self.p.set_state("T3", "elsewhere")

    def test_append_tasks_lands_at_the_end_of_the_named_segment(self):
        self.p.append_tasks("Segment B: wiring", ["- [ ] T7: follow-up | depends_on: T3"])
        text = "\n".join(self.p.lines)
        self.assertLess(text.index("T7: follow-up"), text.index("## Segment C"))
        self.assertGreater(text.index("T7: follow-up"), text.index("T6: In flight"))
        self.assertEqual("Segment B: wiring", self.p.task("T7").segment)

    def test_append_tasks_into_an_empty_segment(self):
        self.p.append_tasks("Segment C: polish", ["- [ ] T8: tidy"])
        self.assertEqual("Segment C: polish", self.p.task("T8").segment)

    def test_stamp_reviewed_inserts_under_the_heading_and_replaces_an_old_stamp(self):
        self.p.stamp_reviewed("Segment B: wiring", "cafe999")
        self.assertEqual("cafe999", self.p.segments()[1].reviewed_sha)
        self.p.stamp_reviewed("Segment A: bootstrap", "0000111")
        self.assertEqual("0000111", self.p.segments()[0].reviewed_sha)
        self.assertEqual(1, "\n".join(self.p.lines).count("Reviewed: 0000111"))

    def test_stamp_reviewed_is_not_a_task_row(self):
        self.p.stamp_reviewed("Segment B: wiring", "cafe999")
        before = len(self.p.tasks())
        self.assertEqual(6, before)

    def test_split_allocates_the_next_free_numeric_ids(self):
        ids = self.p.split("T4", ["- [ ] first half | depends_on: T3",
                                  "- [ ] second half"])
        self.assertEqual(["T7", "T8"], ids)
        parent = [ln for ln in self.p.lines if ln.startswith("- [-] T4:")][0]
        self.assertIn("split→T7,T8", parent)
        text = "\n".join(self.p.lines)
        self.assertLess(text.index("first half"), text.index("T5: Copy the header"))
        self.assertEqual("Segment B: wiring", self.p.task("T8").segment)

    def test_split_rewrites_whatever_id_the_caller_proposed(self):
        # The Judge's changes.sub_rows carry ids it invented; the harness owns
        # numbering, so they are replaced rather than trusted.
        ids = self.p.split("T4", ["- [ ] T4a: first half | depends_on: T3"])
        self.assertEqual(["T7"], ids)
        row = [ln for ln in self.p.lines if "first half" in ln][0]
        self.assertTrue(row.startswith("- [ ] T7: first half | depends_on: T3"), row)
        self.assertNotIn("T4a", "\n".join(self.p.lines))

    def test_each_sub_row_records_its_parent(self):
        self.p.split("T4", ["- [ ] first half", "- [ ] second half"])
        self.assertEqual("T4", self.p.task("T7").split_of)
        self.assertEqual("T4", self.p.task("T8").split_of)

    def test_next_ids_counts_from_the_highest_id_in_the_whole_plan(self):
        self.assertEqual(["T7", "T8", "T9"], self.p.next_ids(3))
        p = Plan.load(write("## S\n- [ ] T41: only one\n"))
        self.assertEqual(["T42"], p.next_ids(1))
        self.assertEqual(["T1"], Plan.load(write("## S\n")).next_ids(1))

    def test_save_is_a_byte_exact_round_trip_when_nothing_changed(self):
        self.p.save()
        with open(self.path) as f:
            self.assertEqual(SAMPLE, f.read())

    def test_every_row_this_module_writes_still_matches_the_serve_py_regex(self):
        serve_re = re.compile(r"^\s*- \[(.)\] (.*)$")
        self.p.set_state("T3", "done", sha="feed123")
        self.p.set_state("T4", "blocked-upstream")
        self.p.split("T5", ["- [ ] half"])
        self.p.append_tasks("Segment C: polish", ["- [ ] T20: tidy"])
        rows = [ln for ln in self.p.lines if ln.lstrip().startswith("- [")]
        self.assertEqual(8, len(rows))
        for row in rows:
            self.assertTrue(serve_re.match(row), row)
            self.assertIn(serve_re.match(row).group(1), " ~x!-")
            self.assertTrue(planmod.ID_RE.search(row), "every row keeps a T<n> id: %s" % row)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_plan*.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'runner.plan'`

- [ ] **Step 3: Write minimal implementation**

Create `plugins/agent-loop/runner/plan.py`:

```python
"""LOOP_PLAN.md — the only file that says what work exists and what is left.

The grammar is exactly the one serve.py greps (`w10` §3): a task row is
`- [<glyph>] <rest>`, a segment is a line starting `## `, an id is the first
`T<digits>` token. This module adds the v3 row metadata (`| no-ui`,
`| copy_of:`, `| blocked_by:`, `| split_of:`), none of which changes the glyph
set or the id shape — so a v2 plan parses unchanged and the dashboard keeps
counting. Ids are never widened past `\bT\d+\b`: a `T60a` would be invisible to
serve.py's grep, so split sub-tasks take the next free numbers instead.

The harness is the ONLY writer. Every edit is in place on the existing row:
flip the glyph, append at most one suffix. The description text is immutable
after the Planner writes it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from . import util

TASK_RE = re.compile(r"^(\s*)- \[(.)\] (.*)$")
ID_RE = re.compile(r"\bT\d+\b")                      # serve.py's, unchanged
ROW_TITLE_RE = re.compile(r"^(\s*- \[.\] )(?:T\d+[a-z]?:\s*)?(.*)$")
REVIEWED_RE = re.compile(r"^Reviewed:\s*(\S+)\s*$")
SHA_RE = re.compile(r"done\(\+([0-9a-fA-F]+)\)")
MODEL_RE = re.compile(r"\|\s*model:\s*(\S+)")
DEPENDS_RE = re.compile(r"\|\s*depends_on:\s*([^|]+)")
BLOCKED_BY_RE = re.compile(r"\|\s*blocked_by:\s*([^|]+)")
CLONE_RE = re.compile(r"\|\s*clone_of:\s*(T\d+)")
COPY_RE = re.compile(r"\|\s*copy_of:\s*(T\d+)")
SPLIT_OF_RE = re.compile(r"\|\s*split_of:\s*(T\d+)")
NO_UI_RE = re.compile(r"\|\s*no-ui\b")
CLASS_RE = re.compile(r"\|\s*(mechanical|complex)\b")

GLYPH_STATE = {" ": "pending", "~": "doing", "x": "done", "!": "blocked", "-": "skipped"}
STATE_GLYPH = {"pending": " ", "doing": "~", "done": "x", "blocked": "!",
               "skipped": "-", "blocked-upstream": " "}
DONE_STATES = ("done", "skipped")
# Not a glyph: the plan has exactly five and serve.py maps all of them. A
# blocked-upstream task stays `[ ]` (it IS still outstanding work) and carries
# this marker, which plan.py reads back as a state and eligible() skips.
BLOCKED_UPSTREAM = "[blocked-upstream]"


@dataclass
class Task:
    id: str
    segment: str
    title: str
    state: str
    sha: Optional[str] = None
    class_flag: Optional[str] = None
    depends_on: List[str] = field(default_factory=list)
    clone_of: Optional[str] = None
    copy_of: Optional[str] = None
    split_of: Optional[str] = None
    blocked_by: List[str] = field(default_factory=list)
    no_ui: bool = False
    model: Optional[str] = None
    line_no: int = -1
    raw: str = ""


@dataclass
class Segment:
    name: str
    line_no: int
    reviewed_sha: Optional[str] = None
    tasks: List[Task] = field(default_factory=list)


def _csv(value: str) -> List[str]:
    return [tok.strip() for tok in value.split(",") if tok.strip()]


def _retitle(row: str, new_id: str) -> str:
    """Put `new_id` in the row's id position, replacing whatever was proposed.

    Matching on the row's shape rather than on the first `T<n>` token anywhere in
    it: a proposed `- [ ] T4a: half | depends_on: T3` has no valid id of its own,
    and a token search would rewrite the DEPENDENCY instead of the title.
    """
    m = ROW_TITLE_RE.match(row)
    if not m:
        return "- [ ] %s: %s" % (new_id, row.strip().lstrip("- ").lstrip())
    return "%s%s: %s" % (m.group(1), new_id, m.group(2))


def _parse_task(line: str, line_no: int, segment: str) -> Optional[Task]:
    m = TASK_RE.match(line)
    if not m:
        return None
    glyph, rest = m.group(2), m.group(3)
    state = GLYPH_STATE.get(glyph, "pending")
    if state == "pending" and BLOCKED_UPSTREAM in rest:
        state = "blocked-upstream"
    id_m = ID_RE.search(rest)
    title = rest.split(" | ")[0]
    title = re.sub(r"^T\d+[a-z]?:\s*", "", title).strip()
    dep_m = DEPENDS_RE.search(rest)
    blk_m = BLOCKED_BY_RE.search(rest)
    cls_m = CLASS_RE.search(rest)
    mdl_m = MODEL_RE.search(rest)
    cln_m = CLONE_RE.search(rest)
    cpy_m = COPY_RE.search(rest)
    spl_m = SPLIT_OF_RE.search(rest)
    sha_m = SHA_RE.search(rest)
    return Task(
        id=id_m.group(0) if id_m else "",
        segment=segment,
        title=title,
        state=state,
        sha=sha_m.group(1) if sha_m else None,
        class_flag=cls_m.group(1) if cls_m else None,
        depends_on=_csv(dep_m.group(1)) if dep_m else [],
        clone_of=cln_m.group(1) if cln_m else None,
        copy_of=cpy_m.group(1) if cpy_m else None,
        split_of=spl_m.group(1) if spl_m else None,
        blocked_by=_csv(blk_m.group(1)) if blk_m else [],
        no_ui=bool(NO_UI_RE.search(rest)),
        model=mdl_m.group(1) if mdl_m else None,
        line_no=line_no,
        raw=line,
    )


class Plan(object):
    """An in-memory LOOP_PLAN.md. `lines` is the file split on "\\n"."""

    def __init__(self, path: str, lines: List[str]):
        self.path = path
        self.lines = lines

    @classmethod
    def load(cls, path: str) -> "Plan":
        return cls(path, util.read_text(path).split("\n"))

    # ---------------------------------------------------------------- reading
    def _scan(self):
        """(segments, tasks) rebuilt from self.lines. Cheap; always current."""
        segments: List[Segment] = []
        tasks: List[Task] = []
        current: Optional[Segment] = None
        name = ""
        for i, line in enumerate(self.lines):
            if line.startswith("## "):
                name = line[3:].strip()
                current = Segment(name=name, line_no=i)
                segments.append(current)
                continue
            t = _parse_task(line, i, name)
            if t is not None:
                tasks.append(t)
                if current is not None:
                    current.tasks.append(t)
                continue
            if current is not None and current.reviewed_sha is None:
                rm = REVIEWED_RE.match(line.strip())
                if rm:
                    current.reviewed_sha = rm.group(1)
        return segments, tasks

    def segments(self) -> List[Segment]:
        return self._scan()[0]

    def tasks(self) -> List[Task]:
        return self._scan()[1]

    def task(self, task_id: str) -> Optional[Task]:
        for t in self.tasks():
            if t.id == task_id:
                return t
        return None

    def eligible(self) -> List[Task]:
        """Pending tasks whose dependencies and blockers are all settled."""
        tasks = self.tasks()
        settled = set(t.id for t in tasks if t.state in DONE_STATES)
        out = []
        for t in tasks:
            if t.state != "pending":
                continue
            if any(d not in settled for d in t.depends_on):
                continue
            if any(b not in settled for b in t.blocked_by):
                continue
            out.append(t)
        return out

    def dependents(self, task_id: str) -> List[Task]:
        """Every task that transitively depends on `task_id`, in plan order."""
        tasks = self.tasks()
        frontier = set([task_id])
        found = set()
        changed = True
        while changed:
            changed = False
            for t in tasks:
                if t.id in found or t.id == task_id:
                    continue
                if any(d in frontier for d in t.depends_on):
                    found.add(t.id)
                    frontier.add(t.id)
                    changed = True
        return [t for t in tasks if t.id in found]

    def next_review_segment(self) -> Optional[Segment]:
        for s in self.segments():
            if s.tasks and not s.reviewed_sha and all(t.state in DONE_STATES for t in s.tasks):
                return s
        return None

    def next_plan_segment(self) -> Optional[Segment]:
        for s in self.segments():
            if not s.tasks:
                return s
        return None

    def mode(self) -> str:
        """review | plan | execute | done | stuck (spec §4.1).

        Review wins over everything: a finished segment must be graded before
        the next one is written. Execute wins over plan: there is no reason to
        write more rows while eligible ones are waiting.
        """
        if self.next_review_segment() is not None:
            return "review"
        eligible = self.eligible()
        unplanned = self.next_plan_segment()
        if unplanned is not None and not eligible:
            return "plan"
        if eligible:
            return "execute"
        open_tasks = [t for t in self.tasks() if t.state not in DONE_STATES]
        if not open_tasks and unplanned is None:
            return "done"
        return "stuck"

    # ---------------------------------------------------------------- writing
    def _segment(self, name: str) -> Segment:
        for s in self.segments():
            if s.name == name:
                return s
        raise KeyError("no segment named %r" % name)

    def _segment_end(self, seg: Segment) -> int:
        """Index one past the segment's last non-blank line."""
        end = len(self.lines)
        for i in range(seg.line_no + 1, len(self.lines)):
            if self.lines[i].startswith("## "):
                end = i
                break
        while end - 1 > seg.line_no and not self.lines[end - 1].strip():
            end -= 1
        return end

    def set_state(self, task_id: str, state: str, sha: Optional[str] = None) -> None:
        """Flip one row's glyph in place; append ` done(+sha)` at most once.

        Never rewrites the description — an earlier bug inserted the sha
        mid-row and repeated the whole (verbose) description, doubling it.
        """
        if state not in STATE_GLYPH:
            raise ValueError("unknown task state %r" % state)
        t = self.task(task_id)
        if t is None:
            raise KeyError("no task %r in %s" % (task_id, self.path))
        m = TASK_RE.match(self.lines[t.line_no])
        indent, rest = m.group(1), m.group(3).rstrip()
        rest = rest.replace(" " + BLOCKED_UPSTREAM, "").rstrip()
        if state == "blocked-upstream":
            rest = rest + " " + BLOCKED_UPSTREAM
        if sha and not SHA_RE.search(rest):
            rest = rest + " done(+%s)" % sha
        self.lines[t.line_no] = "%s- [%s] %s" % (indent, STATE_GLYPH[state], rest)

    def append_tasks(self, segment_name: str, rows: List[str]) -> None:
        """Insert rows at the end of the named segment's block."""
        seg = self._segment(segment_name)
        at = self._segment_end(seg)
        self.lines[at:at] = list(rows)

    def stamp_reviewed(self, segment_name: str, sha: str) -> None:
        """Write `Reviewed: <sha>` directly beneath the heading (plain text,
        never a `- [ ]` row — a task-shaped marker would corrupt the
        denominator every progress reading uses)."""
        seg = self._segment(segment_name)
        end = self._segment_end(seg)
        for i in range(seg.line_no + 1, end):
            if REVIEWED_RE.match(self.lines[i].strip()):
                self.lines[i] = "Reviewed: %s" % sha
                return
        self.lines[seg.line_no + 1:seg.line_no + 1] = ["Reviewed: %s" % sha]

    def next_ids(self, count: int) -> List[str]:
        """The next `count` free numeric ids, continuing the plan's numbering."""
        used = []
        for t in self.tasks():
            if t.id.startswith("T") and t.id[1:].isdigit():
                used.append(int(t.id[1:]))
        start = (max(used) + 1) if used else 1
        return ["T%d" % (start + i) for i in range(max(0, int(count)))]

    def split(self, task_id: str, sub_rows: List[str]) -> List[str]:
        """Mark the parent `[-] … split→T74,T75` and insert the renumbered sub rows.

        The harness owns numbering, not the caller: plan C's Judge proposes
        `changes.sub_rows` with ids it invented, and every one of them is
        rewritten to the next free NUMERIC id here. A lettered `T60a` would not
        match serve.py's `\\bT\\d+\\b`, so the dashboard would stop counting the
        work the moment a task was split.
        """
        t = self.task(task_id)
        if t is None:
            raise KeyError("no task %r in %s" % (task_id, self.path))
        ids = self.next_ids(len(sub_rows))
        rows = []
        for new_id, row in zip(ids, sub_rows):
            rows.append(_retitle(row, new_id).rstrip() + " | split_of: %s" % task_id)
        self.set_state(task_id, "skipped")
        self.lines[t.line_no] = self.lines[t.line_no] + " split→%s" % ",".join(ids)
        self.lines[t.line_no + 1:t.line_no + 1] = rows
        return ids

    def save(self) -> None:
        util.atomic_write(self.path, "\n".join(self.lines))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_*.py' -v`
Expected: PASS — 77 tests, `OK`

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/runner/plan.py plugins/agent-loop/tests/runner/test_plan.py
git commit -F - <<'EOF'
agent-loop: LOOP_PLAN grammar, eligibility and in-place edits in Python

The harness is the only thing that reads or edits the plan now. Same glyphs and
same row regex serve.py greps, plus | no-ui, | copy_of:, | blocked_by: and
| split_of:. Ids stay T<digits>: split allocates the next free numbers and
rewrites whatever the caller proposed, because a lettered T60a does not match
serve.py's grep and would vanish from every dashboard count. blocked-upstream
is a row marker rather than a sixth glyph, so a fanned-out task still counts as
outstanding work. mode() encodes spec 4.1: review beats execute beats plan.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 5: `contract.py` — the sprint contract and its validation rules

**Files:**
- Create: `plugins/agent-loop/runner/contract.py`
- Test: `plugins/agent-loop/tests/runner/test_contract.py`

**Interfaces:**
- Consumes: `util.read_json`, `util.write_json`, `plan.Task`, `config.LoopConfig`.
- Produces: `Forbidden(path, source)`, `RenderGate(commands, screenshots)`, `FidelitySource(src, dst, min_similarity)`, `Contract`, `ContractError`, `load_contract(path) -> Contract`, `save_contract(c, path)`, `validate(c, task, cfg, cleanup_text, ui_globs) -> List[str]`, `PATH_TOKEN_RE`, `COPY_VERBS`.
- On-disk JSON keeps the spec §5 key names: a fidelity pair is `{"from":…,"to":…,"min_similarity":…}` and maps onto the `src`/`dst` dataclass fields. `load_contract` also accepts `src`/`dst`.
- `cfg` is accepted by `validate` but unused in plan A; plan B reads `cfg.render` through it rather than changing the signature.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_contract.py`:

```python
import _path  # noqa: F401
import json
import os
import tempfile
import unittest

from runner import contract as cmod
from runner.config import LoopConfig
from runner.contract import Contract, FidelitySource, Forbidden, RenderGate
from runner.plan import Task

CFG = LoopConfig(worktree="/tmp/wt")
UI_GLOBS = ["apps/*/src/**", "packages/ui/**"]


def task(**kw):
    base = dict(id="T60", segment="S", title="Add the org-unit request", state="pending")
    base.update(kw)
    return Task(**base)


def contract(**kw):
    base = dict(
        task="T60",
        success_criteria=["packages/api/src/requests/orgUnits.ts exports listOrgUnits"],
        allow_list=["packages/api/src/requests/orgUnits.ts"],
        forbidden=[Forbidden(path="apps/frontend/**", source="plan")],
        verification=["pnpm turbo run lint --filter=@repo/api"],
        render_gate=None,
        fidelity_source=[],
        evaluator_must_read=[],
        evaluator_must_view=[],
        estimated_diff_lines=120,
        scout_notes="inline everything the worker needs",
        relevant_learnings=[],
    )
    base.update(kw)
    return Contract(**base)


class TestValidateHappyPath(unittest.TestCase):
    def test_a_well_formed_contract_has_no_errors(self):
        self.assertEqual([], cmod.validate(contract(), task(), CFG, "", UI_GLOBS))


class TestCriteriaPaths(unittest.TestCase):
    def test_a_criterion_naming_a_path_outside_allow_list_is_an_error(self):
        c = contract(success_criteria=["apps/frontend/src/Header.tsx gains the nav item"])
        errs = cmod.validate(c, task(), CFG, "", UI_GLOBS)
        self.assertEqual(1, len(errs))
        self.assertIn("apps/frontend/src/Header.tsx", errs[0])
        self.assertIn("allow_list", errs[0])

    def test_the_read_marker_exempts_a_reference_path(self):
        c = contract(success_criteria=[
            "matches apps/frontend/src/Header.tsx (read) in structure"])
        self.assertEqual([], cmod.validate(c, task(), CFG, "", UI_GLOBS))

    def test_a_verification_command_may_only_touch_allow_list_paths(self):
        c = contract(verification=["pnpm vitest run apps/frontend/src/Header.test.tsx"])
        errs = cmod.validate(c, task(), CFG, "", UI_GLOBS)
        self.assertEqual(1, len(errs))
        self.assertIn("apps/frontend/src/Header.test.tsx", errs[0])

    def test_a_glob_in_allow_list_covers_the_files_under_it(self):
        c = contract(allow_list=["packages/api/src/**"],
                     success_criteria=["packages/api/src/requests/orgUnits.ts compiles"])
        self.assertEqual([], cmod.validate(c, task(), CFG, "", UI_GLOBS))

    def test_bare_words_and_flags_are_not_path_tokens(self):
        c = contract(verification=["pnpm turbo run lint check test --filter=@repo/api"])
        self.assertEqual([], cmod.validate(c, task(), CFG, "", UI_GLOBS))

    def test_the_path_token_regex_matches_repo_paths_only(self):
        for good in ("packages/api/src/x.ts", "apps/frontend/**", "a/b-c/d_e.tsx",
                     "apps/*/src/**"):
            self.assertTrue(cmod.PATH_TOKEN_RE.match(good), good)
        for bad in ("lint", "--filter=@repo/api", "pnpm", "T60", "0.6"):
            self.assertFalse(cmod.PATH_TOKEN_RE.match(bad), bad)


class TestForbiddenProvenance(unittest.TestCase):
    def test_an_unknown_source_is_an_error(self):
        c = contract(forbidden=[Forbidden(path="apps/**", source="vibes")])
        errs = cmod.validate(c, task(), CFG, "", UI_GLOBS)
        self.assertEqual(1, len(errs))
        self.assertIn("source", errs[0])

    def test_an_empty_source_is_an_error(self):
        c = contract(forbidden=[Forbidden(path="apps/**", source="")])
        self.assertEqual(1, len(cmod.validate(c, task(), CFG, "", UI_GLOBS)))

    def test_all_three_provenances_are_accepted(self):
        c = contract(forbidden=[Forbidden(path="a/b", source="plan"),
                                Forbidden(path="c/d", source="spec"),
                                Forbidden(path="e/f", source="scout")])
        self.assertEqual([], cmod.validate(c, task(), CFG, "", UI_GLOBS))


class TestBlockedByCleanup(unittest.TestCase):
    CLEANUP = ("# Cleanup\n\n"
               "- T60: blocked on whether org units are a tab or a page — needs a human\n"
               "- T99: unrelated\n")

    def test_a_task_named_in_cleanup_is_an_error(self):
        errs = cmod.validate(contract(), task(), CFG, self.CLEANUP, UI_GLOBS)
        self.assertEqual(1, len(errs))
        self.assertIn("blocked-by-cleanup", errs[0])

    def test_another_task_in_cleanup_is_not_our_problem(self):
        errs = cmod.validate(contract(task="T61"), task(id="T61"), CFG,
                             self.CLEANUP, UI_GLOBS)
        self.assertEqual([], errs)

    def test_a_prefix_match_does_not_count(self):
        errs = cmod.validate(contract(task="T6"), task(id="T6"), CFG,
                             self.CLEANUP, UI_GLOBS)
        self.assertEqual([], errs)

    def test_headings_are_ignored(self):
        errs = cmod.validate(contract(), task(), CFG, "## T60 notes\n", UI_GLOBS)
        self.assertEqual([], errs)


class TestRenderRequirement(unittest.TestCase):
    UI = ["apps/internal/src/pages/OrgUnits.tsx"]
    CRITERIA = ["apps/internal/src/pages/OrgUnits.tsx lists the org units"]

    def test_a_ui_touching_allow_list_needs_a_render_gate(self):
        c = contract(allow_list=self.UI, success_criteria=self.CRITERIA)
        errs = cmod.validate(c, task(), CFG, "", ["apps/*/src/**"])
        self.assertEqual(1, len(errs))
        self.assertIn("render_gate", errs[0])

    def test_the_no_ui_flag_waives_it(self):
        c = contract(allow_list=self.UI, success_criteria=self.CRITERIA)
        self.assertEqual([], cmod.validate(c, task(no_ui=True), CFG, "", ["apps/*/src/**"]))

    def test_a_declared_render_gate_satisfies_it(self):
        c = contract(allow_list=self.UI, success_criteria=self.CRITERIA,
                     render_gate=RenderGate(commands=["pnpm cypress run"],
                                            screenshots=[{"name": "orgunits",
                                                          "path": "shots/orgunits.png"}]))
        self.assertEqual([], cmod.validate(c, task(), CFG, "", ["apps/*/src/**"]))

    def test_no_recipe_means_no_requirement(self):
        c = contract(allow_list=self.UI, success_criteria=self.CRITERIA)
        self.assertEqual([], cmod.validate(c, task(), CFG, "", []))

    def test_a_screenshot_needs_a_name_and_a_path(self):
        c = contract(render_gate=RenderGate(commands=["x"], screenshots=[{"name": "a"}]))
        errs = cmod.validate(c, task(), CFG, "", UI_GLOBS)
        self.assertEqual(1, len(errs))
        self.assertIn("screenshot", errs[0])

    def test_a_render_gate_needs_at_least_one_command(self):
        c = contract(render_gate=RenderGate(commands=[], screenshots=[]))
        errs = cmod.validate(c, task(), CFG, "", UI_GLOBS)
        self.assertEqual(1, len(errs))
        self.assertIn("command", errs[0])


class TestFidelityRequirement(unittest.TestCase):
    def test_a_copy_of_row_needs_a_fidelity_source(self):
        errs = cmod.validate(contract(), task(copy_of="T12"), CFG, "", UI_GLOBS)
        self.assertEqual(1, len(errs))
        self.assertIn("fidelity_source", errs[0])

    def test_a_copy_verb_in_the_title_needs_one_too(self):
        for verb in ("Copy", "port", "Replicate"):
            errs = cmod.validate(contract(), task(title="%s the header" % verb),
                                 CFG, "", UI_GLOBS)
            self.assertEqual(1, len(errs), verb)

    def test_a_declared_pair_satisfies_it(self):
        c = contract(fidelity_source=[FidelitySource(src="a/b.tsx", dst="c/d.tsx",
                                                     min_similarity=0.6)])
        self.assertEqual([], cmod.validate(c, task(copy_of="T12"), CFG, "", UI_GLOBS))

    def test_min_similarity_must_be_a_ratio_above_zero(self):
        for bad in (0.0, -1.0, 1.5):
            c = contract(fidelity_source=[FidelitySource(src="a/b", dst="c/d",
                                                         min_similarity=bad)])
            errs = cmod.validate(c, task(copy_of="T12"), CFG, "", UI_GLOBS)
            self.assertEqual(1, len(errs), bad)
            self.assertIn("min_similarity", errs[0])

    def test_an_ordinary_task_needs_nothing(self):
        self.assertEqual([], cmod.validate(contract(), task(title="Add the parser"),
                                           CFG, "", UI_GLOBS))


class TestStructuralErrors(unittest.TestCase):
    def test_a_task_id_mismatch_is_an_error(self):
        errs = cmod.validate(contract(task="T7"), task(id="T60"), CFG, "", UI_GLOBS)
        self.assertEqual(1, len(errs))
        self.assertIn("T7", errs[0])

    def test_empty_required_arrays_are_errors(self):
        errs = cmod.validate(contract(success_criteria=[], allow_list=[], verification=[]),
                             task(), CFG, "", UI_GLOBS)
        self.assertEqual(3, len(errs))

    def test_errors_accumulate_rather_than_short_circuiting(self):
        c = contract(task="T7", verification=[],
                     forbidden=[Forbidden(path="a/b", source="guess")])
        self.assertEqual(3, len(cmod.validate(c, task(), CFG, "", UI_GLOBS)))


class TestRoundTrip(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "sprint-T60.json")

    def test_save_then_load_preserves_every_field(self):
        c = contract(render_gate=RenderGate(commands=["pnpm cypress run"],
                                            screenshots=[{"name": "a", "path": "b.png"}]),
                     fidelity_source=[FidelitySource(src="a/b.tsx", dst="c/d.tsx",
                                                     min_similarity=0.6)],
                     evaluator_must_read=["a/b.tsx"], evaluator_must_view=["a"],
                     relevant_learnings=["always run x"])
        cmod.save_contract(c, self.p)
        back = cmod.load_contract(self.p)
        self.assertEqual(c, back)

    def test_the_on_disk_pair_uses_the_spec_key_names(self):
        cmod.save_contract(contract(fidelity_source=[
            FidelitySource(src="a/b", dst="c/d", min_similarity=0.6)]), self.p)
        with open(self.p) as f:
            raw = json.load(f)
        self.assertEqual({"from": "a/b", "to": "c/d", "min_similarity": 0.6},
                         raw["fidelity_source"][0])

    def test_src_dst_keys_are_also_accepted_on_load(self):
        with open(self.p, "w") as f:
            json.dump({"task": "T60", "fidelity_source":
                       [{"src": "a/b", "dst": "c/d", "min_similarity": 0.5}]}, f)
        self.assertEqual("a/b", cmod.load_contract(self.p).fidelity_source[0].src)

    def test_missing_optional_fields_default(self):
        with open(self.p, "w") as f:
            json.dump({"task": "T60", "success_criteria": ["x"],
                       "allow_list": ["a/b"], "verification": ["true"]}, f)
        c = cmod.load_contract(self.p)
        self.assertEqual([], c.forbidden)
        self.assertIsNone(c.render_gate)
        self.assertEqual(0, c.estimated_diff_lines)

    def test_a_missing_or_malformed_file_raises_contract_error(self):
        with self.assertRaises(cmod.ContractError):
            cmod.load_contract(os.path.join(self.d, "nope.json"))
        with open(self.p, "w") as f:
            f.write("{not json")
        with self.assertRaises(cmod.ContractError):
            cmod.load_contract(self.p)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_contract*.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'runner.contract'`

- [ ] **Step 3: Write minimal implementation**

Create `plugins/agent-loop/runner/contract.py`:

```python
"""runtime/sprint-<T>.json — the single document a Worker and an Evaluator see.

The Scout writes it; the harness validates it BEFORE dispatch and refuses an
invalid one. That refusal is the whole point: in the 2026-09-11 run the
contract for tick 85 named a success criterion about a file its own allow_list
forbade, and three Workers burned on it. Validation is cheap, mechanical, and
happens in Python — never asked of a model.
"""
from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import util

# A whitespace-delimited token that looks like a repo path: at least one "/",
# and only characters a path (or a glob) legitimately contains.
PATH_TOKEN_RE = re.compile(r"^[\w.@#-]+(/[\w.@#\[\]{}*-]+)+$")
COPY_VERBS = ("copy", "port", "replicate")
VALID_SOURCES = ("plan", "spec", "scout")
READ_MARKER = "(read)"


class ContractError(ValueError):
    """The contract file is missing, unparseable, or not an object."""


@dataclass
class Forbidden:
    path: str
    source: str


@dataclass
class RenderGate:
    commands: List[str] = field(default_factory=list)
    screenshots: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class FidelitySource:
    src: str
    dst: str
    min_similarity: float


@dataclass
class Contract:
    task: str = ""
    success_criteria: List[str] = field(default_factory=list)
    allow_list: List[str] = field(default_factory=list)
    forbidden: List[Forbidden] = field(default_factory=list)
    verification: List[str] = field(default_factory=list)
    render_gate: Optional[RenderGate] = None
    fidelity_source: List[FidelitySource] = field(default_factory=list)
    evaluator_must_read: List[str] = field(default_factory=list)
    evaluator_must_view: List[str] = field(default_factory=list)
    estimated_diff_lines: int = 0
    scout_notes: str = ""
    relevant_learnings: List[str] = field(default_factory=list)


def load_contract(path: str) -> Contract:
    raw = util.read_json(path)
    if not isinstance(raw, dict):
        raise ContractError("no readable contract at %s" % path)
    rg = raw.get("render_gate")
    render_gate = None
    if isinstance(rg, dict):
        render_gate = RenderGate(commands=list(rg.get("commands") or []),
                                 screenshots=list(rg.get("screenshots") or []))
    fidelity = []
    for item in raw.get("fidelity_source") or []:
        if not isinstance(item, dict):
            continue
        fidelity.append(FidelitySource(
            src=item.get("from", item.get("src", "")),
            dst=item.get("to", item.get("dst", "")),
            min_similarity=float(item.get("min_similarity", 0.0))))
    forbidden = []
    for item in raw.get("forbidden") or []:
        if isinstance(item, dict):
            forbidden.append(Forbidden(path=item.get("path", ""),
                                       source=item.get("source", "")))
    return Contract(
        task=raw.get("task", ""),
        success_criteria=list(raw.get("success_criteria") or []),
        allow_list=list(raw.get("allow_list") or []),
        forbidden=forbidden,
        verification=list(raw.get("verification") or []),
        render_gate=render_gate,
        fidelity_source=fidelity,
        evaluator_must_read=list(raw.get("evaluator_must_read") or []),
        evaluator_must_view=list(raw.get("evaluator_must_view") or []),
        estimated_diff_lines=int(raw.get("estimated_diff_lines") or 0),
        scout_notes=raw.get("scout_notes", "") or "",
        relevant_learnings=list(raw.get("relevant_learnings") or []),
    )


def to_dict(c: Contract) -> Dict[str, Any]:
    """The spec §5 JSON shape — `from`/`to` for a fidelity pair."""
    out: Dict[str, Any] = {
        "task": c.task,
        "success_criteria": c.success_criteria,
        "allow_list": c.allow_list,
        "forbidden": [{"path": f.path, "source": f.source} for f in c.forbidden],
        "verification": c.verification,
        "render_gate": None,
        "fidelity_source": [{"from": f.src, "to": f.dst,
                             "min_similarity": f.min_similarity}
                            for f in c.fidelity_source],
        "evaluator_must_read": c.evaluator_must_read,
        "evaluator_must_view": c.evaluator_must_view,
        "estimated_diff_lines": c.estimated_diff_lines,
        "scout_notes": c.scout_notes,
        "relevant_learnings": c.relevant_learnings,
    }
    if c.render_gate is not None:
        out["render_gate"] = {"commands": c.render_gate.commands,
                              "screenshots": c.render_gate.screenshots}
    return out


def save_contract(c: Contract, path: str) -> None:
    util.write_json(path, to_dict(c))


def _matches_any(path: str, patterns: List[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(path, pat):
            return True
    return False


def _path_allowed(token: str, allow_list: List[str]) -> bool:
    """True when the token names something inside the allow_list.

    Three ways to be inside: the token matches a pattern; the token sits under a
    directory the allow_list names; or the token IS a directory the allow_list
    writes into (a criterion may legitimately name the folder).
    """
    if _matches_any(token, allow_list):
        return True
    for pat in allow_list:
        base = pat.rstrip("/")
        if token.startswith(base + "/") or base.startswith(token.rstrip("/") + "/"):
            return True
    return False


def _scan_paths(text: str):
    """[(token, is_read_only)] for every repo-path-shaped token in `text`."""
    raw = text.split()
    out = []
    for i, tok in enumerate(raw):
        clean = tok.strip("`'\"(),;:")
        if not PATH_TOKEN_RE.match(clean):
            continue
        nxt = raw[i + 1].strip("`'\"") if i + 1 < len(raw) else ""
        out.append((clean, nxt == READ_MARKER))
    return out


def _cleanup_blocks(cleanup_text: str, task_id: str) -> bool:
    """True when LOOP_CLEANUP.md names this task on a content line."""
    pattern = re.compile(r"\b%s\b" % re.escape(task_id))
    for line in (cleanup_text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if pattern.search(stripped):
            return True
    return False


def _needs_fidelity(task) -> bool:
    if task.copy_of:
        return True
    first = (task.title or "").split()
    verb = first[0].lower().strip(".,:") if first else ""
    return verb in COPY_VERBS


def validate(c: Contract, task, cfg, cleanup_text: str,
             ui_globs: List[str]) -> List[str]:
    """[] when the contract may be dispatched; otherwise human-readable errors.

    Every rule accumulates — the Scout is re-dispatched once with the full list,
    so handing it one error at a time would waste a whole phase per problem.
    `cfg` is unused here; plan B reads `cfg.render` through this same signature.
    """
    errors: List[str] = []

    if c.task != task.id:
        errors.append("contract task %r does not match the plan row %r" % (c.task, task.id))
    if not c.success_criteria:
        errors.append("success_criteria is empty: nothing defines done for this task")
    if not c.allow_list:
        errors.append("allow_list is empty: the Worker would have nothing it may edit")
    if not c.verification:
        errors.append("verification is empty: the harness would have no gate to run")

    for text in list(c.success_criteria) + list(c.verification):
        for token, is_read in _scan_paths(text):
            if is_read or _path_allowed(token, c.allow_list):
                continue
            errors.append(
                "%r names %s, which is outside allow_list; add it, or mark the "
                "reference read-only by writing '%s (read)'" % (text, token, token))

    for f in c.forbidden:
        if f.source not in VALID_SOURCES:
            errors.append("forbidden %r has source %r; expected one of %s"
                          % (f.path, f.source, ", ".join(VALID_SOURCES)))

    if _cleanup_blocks(cleanup_text, task.id):
        errors.append("blocked-by-cleanup: LOOP_CLEANUP.md names %s as needing a "
                      "human decision, so it is not eligible" % task.id)

    if ui_globs and not task.no_ui and c.render_gate is None:
        touched = [p for p in c.allow_list if _matches_any(p, ui_globs)]
        if touched:
            errors.append("allow_list touches UI paths (%s) but the contract declares "
                          "no render_gate; add one or tag the plan row '| no-ui'"
                          % ", ".join(touched))

    if c.render_gate is not None:
        if not c.render_gate.commands:
            errors.append("render_gate declares no command to run")
        for shot in c.render_gate.screenshots:
            if not shot.get("name") or not shot.get("path"):
                errors.append("render_gate screenshot %r needs both a name and a path"
                              % (shot,))

    if _needs_fidelity(task) and not c.fidelity_source:
        errors.append("this is a copy/port task but the contract declares no "
                      "fidelity_source pair, so a six-line 'copy' would pass")
    for f in c.fidelity_source:
        if not (0.0 < f.min_similarity <= 1.0):
            errors.append("fidelity_source %s -> %s has min_similarity %r; expected a "
                          "ratio in (0, 1]" % (f.src, f.dst, f.min_similarity))

    return errors
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_*.py' -v`
Expected: PASS — 110 tests, `OK`

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/runner/contract.py plugins/agent-loop/tests/runner/test_contract.py
git commit -F - <<'EOF'
agent-loop: sprint contract loading and the validation rules from spec 5.1

Criteria and verification commands may only name paths inside allow_list or
paths explicitly marked (read); every forbidden entry carries provenance; a
task LOOP_CLEANUP names is refused as blocked-by-cleanup; a UI-touching
allow_list needs a render_gate unless the row says no-ui; a copy/port task
needs a fidelity pair. Errors accumulate so one re-scout sees all of them.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 6: `gate.py` — running verification commands with per-command budgets

**Files:**
- Create: `plugins/agent-loop/runner/gate.py`
- Test: `plugins/agent-loop/tests/runner/test_gate.py`

**Interfaces:**
- Consumes: nothing from `runner`.
- Produces: `CommandResult(cmd, rc, duration_s, output_path, timed_out)`, `run_commands(cmds, cwd, timeout_s, out_dir, tag) -> List[CommandResult]`, `all_ok(results) -> bool`, `outputs_text(results, limit=2000) -> str`.
- Output files are `<out_dir>/gate-<tag>-<n>.txt`, `n` starting at 1. Plan B calls this with `tag="render-T60"`, so `tag` must be interpolated verbatim.
- `run_commands` stops at the **first** non-zero rc: any gate failure re-dispatches the Worker, so the remaining commands cost up to `timeout_s` each for information nobody acts on.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_gate.py`:

```python
import _path  # noqa: F401
import os
import tempfile
import unittest

from runner import gate


class TestRunCommands(unittest.TestCase):
    def setUp(self):
        self.cwd = tempfile.mkdtemp()
        self.out = tempfile.mkdtemp()

    def test_a_passing_command_returns_rc_zero_and_captures_output(self):
        res = gate.run_commands(["echo hello"], self.cwd, 30, self.out, "T1")
        self.assertEqual(1, len(res))
        self.assertEqual(0, res[0].rc)
        self.assertFalse(res[0].timed_out)
        self.assertTrue(gate.all_ok(res))
        with open(res[0].output_path) as f:
            body = f.read()
        self.assertIn("hello", body)
        self.assertIn("$ echo hello", body)

    def test_output_files_are_named_gate_tag_n(self):
        res = gate.run_commands(["true", "true"], self.cwd, 30, self.out, "render-T60")
        self.assertEqual(os.path.join(self.out, "gate-render-T60-1.txt"), res[0].output_path)
        self.assertEqual(os.path.join(self.out, "gate-render-T60-2.txt"), res[1].output_path)

    def test_stderr_is_captured_too(self):
        res = gate.run_commands(["echo oops >&2"], self.cwd, 30, self.out, "T1")
        with open(res[0].output_path) as f:
            self.assertIn("oops", f.read())

    def test_a_failing_command_stops_the_run(self):
        res = gate.run_commands(["exit 3", "echo never"], self.cwd, 30, self.out, "T1")
        self.assertEqual(1, len(res))
        self.assertEqual(3, res[0].rc)
        self.assertFalse(gate.all_ok(res))

    def test_commands_run_in_the_given_cwd(self):
        with open(os.path.join(self.cwd, "marker.txt"), "w") as f:
            f.write("x")
        res = gate.run_commands(["ls marker.txt"], self.cwd, 30, self.out, "T1")
        self.assertEqual(0, res[0].rc)

    def test_a_hanging_command_times_out_with_rc_124(self):
        res = gate.run_commands(["sleep 30"], self.cwd, 1, self.out, "T1")
        self.assertTrue(res[0].timed_out)
        self.assertEqual(124, res[0].rc)
        self.assertLess(res[0].duration_s, 20)
        with open(res[0].output_path) as f:
            self.assertIn("timed out", f.read())

    def test_a_timeout_kills_the_whole_process_group(self):
        marker = os.path.join(self.cwd, "child-alive")
        cmd = "(sleep 20; touch %s) & sleep 20" % marker
        gate.run_commands([cmd], self.cwd, 1, self.out, "T1")
        self.assertFalse(os.path.exists(marker))

    def test_an_empty_command_list_is_vacuously_ok(self):
        res = gate.run_commands([], self.cwd, 30, self.out, "T1")
        self.assertEqual([], res)
        self.assertTrue(gate.all_ok(res))

    def test_the_out_dir_is_created_when_missing(self):
        target = os.path.join(self.out, "deep", "er")
        gate.run_commands(["true"], self.cwd, 30, target, "T1")
        self.assertTrue(os.path.isdir(target))


class TestOutputsText(unittest.TestCase):
    def setUp(self):
        self.cwd = tempfile.mkdtemp()
        self.out = tempfile.mkdtemp()

    def test_renders_command_rc_and_a_bounded_tail(self):
        res = gate.run_commands(["echo one", "echo two"], self.cwd, 30, self.out, "T1")
        text = gate.outputs_text(res)
        self.assertIn("$ echo one", text)
        self.assertIn("rc=0", text)
        self.assertIn("two", text)

    def test_the_tail_is_capped_per_command(self):
        res = gate.run_commands(["head -c 9000 /dev/zero | tr '\\0' 'x'"],
                                self.cwd, 30, self.out, "T1")
        text = gate.outputs_text(res, limit=500)
        self.assertLess(len(text), 1500)
        self.assertIn("truncated", text)

    def test_no_results_renders_a_placeholder(self):
        self.assertIn("none", gate.outputs_text([]))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_gate*.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'runner.gate'`

- [ ] **Step 3: Write minimal implementation**

Create `plugins/agent-loop/runner/gate.py`:

```python
"""The parent-side verification gate: the harness runs the commands, not a model.

Never trust a Worker's self-reported pass. These commands come from the
contract's `verification` array (which the Scout also seeds with any invariant
`check` from the learnings), and a non-zero exit is a gate failure exactly like
a failing test. Each command gets its own wall-clock budget and its own output
file, so a learning may cite `gate-<T>-<n>.txt` as evidence.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import List


@dataclass
class CommandResult:
    cmd: str
    rc: int
    duration_s: int
    output_path: str
    timed_out: bool


def _kill_group(proc: subprocess.Popen) -> None:
    """SIGTERM the command's own process group, then SIGKILL what survives.

    `start_new_session=True` put the shell in its own group, so a build tool
    that forked workers goes down with it instead of being orphaned holding RAM.
    """
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        return
    for sig, grace in ((signal.SIGTERM, 5), (signal.SIGKILL, 5)):
        try:
            os.killpg(pgid, sig)
        except OSError:
            return
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


def run_commands(cmds: List[str], cwd: str, timeout_s: int, out_dir: str,
                 tag: str) -> List[CommandResult]:
    """Run each command under its own timeout; stop at the first failure."""
    results: List[CommandResult] = []
    if not cmds:
        return results
    os.makedirs(out_dir, exist_ok=True)
    for n, cmd in enumerate(cmds, 1):
        path = os.path.join(out_dir, "gate-%s-%d.txt" % (tag, n))
        started = time.time()
        timed_out = False
        with open(path, "w") as out:
            out.write("$ %s\n" % cmd)
            out.flush()
            proc = subprocess.Popen(cmd, shell=True, cwd=cwd, stdout=out,
                                    stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL,
                                    start_new_session=True)
            try:
                rc = proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_group(proc)
                rc = 124
                out.write("\n[timed out after %ds]\n" % timeout_s)
        results.append(CommandResult(cmd=cmd, rc=rc,
                                     duration_s=int(time.time() - started),
                                     output_path=path, timed_out=timed_out))
        if rc != 0:
            break
    return results


def all_ok(results: List[CommandResult]) -> bool:
    return all(r.rc == 0 for r in results)


def outputs_text(results: List[CommandResult], limit: int = 2000) -> str:
    """A bounded rendering of the gate for a prompt: command, rc, output tail."""
    if not results:
        return "(none)"
    chunks = []
    for r in results:
        try:
            with open(r.output_path) as f:
                body = f.read()
        except OSError:
            body = ""
        if len(body) > limit:
            body = "[... truncated, full output in %s ...]\n%s" % (
                r.output_path, body[-limit:])
        chunks.append("$ %s\nrc=%d (%ds)\n%s" % (r.cmd, r.rc, r.duration_s, body.rstrip()))
    return "\n\n".join(chunks)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_*.py' -v`
Expected: PASS — 122 tests, `OK`

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/runner/gate.py plugins/agent-loop/tests/runner/test_gate.py
git commit -F - <<'EOF'
agent-loop: the verification gate runs in the harness, with per-command budgets

Each command gets its own wall clock and its own gate-<tag>-<n>.txt, so a
timeout is attributable and a learning can cite the evidence file. A timeout
kills the command's whole process group rather than orphaning a build tool that
keeps holding memory. The run stops at the first failure because any failure
re-dispatches the Worker anyway.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 7: `git_ops.py` — containment, commits and diff similarity

**Files:**
- Create: `plugins/agent-loop/runner/git_ops.py`
- Test: `plugins/agent-loop/tests/runner/test_git_ops.py`

**Interfaces:**
- Consumes: `util.read_text`.
- Produces: `GitError`, `changed_paths(cwd)`, `strays(changed, allow_list)`, `revert(cwd, paths)`, `head_sha(cwd)`, `commit(cwd, paths, subject, trailers) -> str`, `diff_text(cwd, base_sha, paths, max_chars=200000) -> str`, `similarity(src_path, dst_path) -> float`.
- `commit` returns `""` when there is nothing staged (a plan/review tick where the model changed nothing), and raises `GitError` on a real git failure.
- Plan B imports `similarity` and nothing else from this module.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_git_ops.py`:

```python
import _path  # noqa: F401
import os
import subprocess
import tempfile
import unittest

from runner import git_ops


def sh(cwd, *args):
    subprocess.run(list(args), cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def repo():
    d = tempfile.mkdtemp()
    sh(d, "git", "init", "-q")
    sh(d, "git", "config", "user.email", "loop@example.com")
    sh(d, "git", "config", "user.name", "Loop")
    sh(d, "git", "config", "commit.gpgsign", "false")
    write(d, "README.md", "seed\n")
    sh(d, "git", "add", "README.md")
    sh(d, "git", "commit", "-q", "-m", "seed")
    return d


def write(d, rel, text):
    path = os.path.join(d, rel)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return path


class TestChangedPaths(unittest.TestCase):
    def setUp(self):
        self.d = repo()

    def test_tracked_edits_and_untracked_files_are_both_reported(self):
        write(self.d, "README.md", "edited\n")
        write(self.d, "src/new.ts", "export const a = 1\n")
        self.assertEqual(["README.md", "src/new.ts"], sorted(git_ops.changed_paths(self.d)))

    def test_a_clean_tree_reports_nothing(self):
        self.assertEqual([], git_ops.changed_paths(self.d))

    def test_a_path_with_a_space_survives_git_quoting(self):
        write(self.d, "a dir/b file.ts", "x\n")
        self.assertIn("a dir/b file.ts", git_ops.changed_paths(self.d))

    def test_a_rename_reports_the_destination(self):
        write(self.d, "old.ts", "x\n")
        sh(self.d, "git", "add", "old.ts")
        sh(self.d, "git", "commit", "-q", "-m", "add old")
        sh(self.d, "git", "mv", "old.ts", "new.ts")
        self.assertIn("new.ts", git_ops.changed_paths(self.d))


class TestStrays(unittest.TestCase):
    def test_exact_paths_and_globs_are_inside(self):
        allow = ["src/a.ts", "packages/api/**"]
        changed = ["src/a.ts", "packages/api/src/b.ts", "apps/web/c.ts"]
        self.assertEqual(["apps/web/c.ts"], git_ops.strays(changed, allow))

    def test_a_directory_entry_covers_files_under_it(self):
        self.assertEqual([], git_ops.strays(["src/deep/a.ts"], ["src"]))

    def test_an_empty_allow_list_makes_everything_a_stray(self):
        self.assertEqual(["a/b.ts"], git_ops.strays(["a/b.ts"], []))


class TestRevert(unittest.TestCase):
    def setUp(self):
        self.d = repo()

    def test_a_tracked_edit_is_restored(self):
        write(self.d, "README.md", "vandalised\n")
        git_ops.revert(self.d, ["README.md"])
        with open(os.path.join(self.d, "README.md")) as f:
            self.assertEqual("seed\n", f.read())

    def test_an_untracked_file_is_deleted(self):
        write(self.d, "junk/stray.ts", "x\n")
        git_ops.revert(self.d, ["junk/stray.ts"])
        self.assertFalse(os.path.exists(os.path.join(self.d, "junk/stray.ts")))

    def test_reverting_a_path_that_is_already_gone_is_not_an_error(self):
        git_ops.revert(self.d, ["never/existed.ts"])

    def test_other_changes_are_untouched(self):
        write(self.d, "keep.ts", "keep\n")
        write(self.d, "drop.ts", "drop\n")
        git_ops.revert(self.d, ["drop.ts"])
        self.assertTrue(os.path.exists(os.path.join(self.d, "keep.ts")))


class TestCommit(unittest.TestCase):
    def setUp(self):
        self.d = repo()

    def test_commit_returns_the_new_sha_and_writes_the_trailers(self):
        write(self.d, "src/a.ts", "export const a = 1\n")
        sha = git_ops.commit(self.d, ["src/a.ts"], "loop(T1): add a",
                             {"Loop-Status": "done",
                              "Loop-Verification": "lint=pass test=pass",
                              "Loop-Files": "src/a.ts"})
        self.assertEqual(40, len(sha))
        self.assertEqual(sha, git_ops.head_sha(self.d))
        body = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=self.d,
                              stdout=subprocess.PIPE).stdout.decode()
        self.assertIn("loop(T1): add a", body)
        self.assertIn("Loop-Status: done", body)
        self.assertIn("Loop-Verification: lint=pass test=pass", body)
        self.assertIn("Loop-Files: src/a.ts", body)

    def test_only_the_named_paths_are_committed(self):
        write(self.d, "src/a.ts", "a\n")
        write(self.d, "src/b.ts", "b\n")
        git_ops.commit(self.d, ["src/a.ts"], "loop(T1): add a", {"Loop-Status": "done"})
        self.assertEqual(["src/b.ts"], git_ops.changed_paths(self.d))

    def test_nothing_to_commit_returns_empty_string(self):
        self.assertEqual("", git_ops.commit(self.d, [], "loop: nothing", {}))
        write(self.d, "src/a.ts", "a\n")
        git_ops.commit(self.d, ["src/a.ts"], "first", {})
        self.assertEqual("", git_ops.commit(self.d, ["src/a.ts"], "again", {}))

    def test_a_git_failure_raises(self):
        with self.assertRaises(git_ops.GitError):
            git_ops.commit(tempfile.mkdtemp(), ["x"], "s", {})


class TestDiffText(unittest.TestCase):
    def setUp(self):
        self.d = repo()

    def test_working_tree_changes_appear_against_the_base_sha(self):
        base = git_ops.head_sha(self.d)
        write(self.d, "README.md", "changed\n")
        diff = git_ops.diff_text(self.d, base, ["README.md"])
        self.assertIn("-seed", diff)
        self.assertIn("+changed", diff)

    def test_an_untracked_new_file_still_shows_up(self):
        base = git_ops.head_sha(self.d)
        write(self.d, "src/new.ts", "export const a = 1\n")
        diff = git_ops.diff_text(self.d, base, ["src/new.ts"])
        self.assertIn("src/new.ts", diff)
        self.assertIn("+export const a = 1", diff)

    def test_a_huge_diff_is_truncated_with_a_marker(self):
        base = git_ops.head_sha(self.d)
        write(self.d, "big.ts", "x\n" * 50000)
        diff = git_ops.diff_text(self.d, base, ["big.ts"], max_chars=1000)
        self.assertLess(len(diff), 1400)
        self.assertIn("truncated", diff)

    def test_no_paths_means_the_whole_tree(self):
        base = git_ops.head_sha(self.d)
        write(self.d, "README.md", "changed\n")
        self.assertIn("+changed", git_ops.diff_text(self.d, base, []))


class TestSimilarity(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def file(self, name, text):
        return write(self.d, name, text)

    def test_identical_files_are_one(self):
        a = self.file("a.ts", "const x = 1\nconst y = 2\n")
        b = self.file("b.ts", "const x = 1\nconst y = 2\n")
        self.assertEqual(1.0, git_ops.similarity(a, b))

    def test_whitespace_and_blank_lines_are_normalised_away(self):
        a = self.file("a.ts", "const x = 1\n\n  const y = 2\n")
        b = self.file("b.ts", "   const x = 1\nconst y = 2\n\n\n")
        self.assertEqual(1.0, git_ops.similarity(a, b))

    def test_import_order_does_not_count_against_a_copy(self):
        a = self.file("a.ts", "import b from 'b'\nimport a from 'a'\nconst x = 1\n")
        b = self.file("b.ts", "import a from 'a'\nimport b from 'b'\nconst x = 1\n")
        self.assertEqual(1.0, git_ops.similarity(a, b))

    def test_a_real_copy_scores_above_the_default_threshold(self):
        body = "".join("  line %d\n" % i for i in range(40))
        a = self.file("a.tsx", "export const Header = () => (\n" + body + ")\n")
        b = self.file("b.tsx", "export const Header = () => (\n" + body.replace("line 3\n", "line 3b\n") + ")\n")
        self.assertGreater(git_ops.similarity(a, b), 0.9)

    def test_a_six_line_comment_pretending_to_be_a_copy_scores_near_zero(self):
        body = "".join("  line %d\n" % i for i in range(200))
        a = self.file("a.tsx", "export const Header = () => (\n" + body + ")\n")
        b = self.file("b.tsx", "// TODO(copied from a.tsx)\n// see the original\n"
                               "export const Header = () => null\n")
        self.assertLess(git_ops.similarity(a, b), 0.2)

    def test_a_missing_file_scores_zero_rather_than_raising(self):
        a = self.file("a.ts", "const x = 1\n")
        self.assertEqual(0.0, git_ops.similarity(a, os.path.join(self.d, "gone.ts")))

    def test_two_empty_files_are_identical(self):
        self.assertEqual(1.0, git_ops.similarity(self.file("a", ""), self.file("b", "")))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_git*.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'runner.git_ops'`

- [ ] **Step 3: Write minimal implementation**

Create `plugins/agent-loop/runner/git_ops.py`:

```python
"""Every git operation the harness performs. Models never run git.

Containment lives here: after a Worker returns (or is killed), the harness
compares the working tree against the contract's allow_list and reverts
anything outside it, so a killed phase can never leave the tree ambiguous.
"""
from __future__ import annotations

import difflib
import fnmatch
import os
import re
import subprocess
from typing import Dict, List

from . import util

_IMPORT_RE = re.compile(r"^(import|from|#include|use|require|using)\b")


class GitError(RuntimeError):
    """A git invocation failed and the caller cannot proceed."""


def _git(cwd: str, args: List[str], check: bool = True):
    try:
        proc = subprocess.run(["git"] + list(args), cwd=cwd,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise GitError("git %s: %s" % (" ".join(args), exc))
    out = proc.stdout.decode("utf-8", "replace")
    if check and proc.returncode != 0:
        raise GitError("git %s failed (rc=%d): %s"
                       % (" ".join(args), proc.returncode,
                          proc.stderr.decode("utf-8", "replace").strip()))
    return proc.returncode, out


def _unquote(path: str) -> str:
    """Undo git's C-style quoting of paths containing spaces or specials."""
    if len(path) >= 2 and path.startswith('"') and path.endswith('"'):
        try:
            return path[1:-1].encode("utf-8").decode("unicode_escape")
        except (UnicodeDecodeError, UnicodeEncodeError):
            return path[1:-1]
    return path


def changed_paths(cwd: str) -> List[str]:
    """Every path the working tree differs on, untracked files included."""
    _, out = _git(cwd, ["status", "--porcelain", "-uall"])
    paths = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        rest = line[3:]
        if " -> " in rest:                     # a rename: the new name is ours
            rest = rest.split(" -> ", 1)[1]
        paths.append(_unquote(rest))
    return paths


def _covered(path: str, patterns: List[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(path, pat):
            return True
        base = pat.rstrip("/")
        if path.startswith(base + "/"):
            return True
    return False


def strays(changed: List[str], allow_list: List[str]) -> List[str]:
    """The changed paths the contract did not sanction."""
    return [p for p in changed if not _covered(p, allow_list)]


def revert(cwd: str, paths: List[str]) -> None:
    """Restore tracked paths; delete untracked ones. Missing paths are fine."""
    for path in paths:
        rc, _ = _git(cwd, ["ls-files", "--error-unmatch", "--", path], check=False)
        if rc == 0:
            _git(cwd, ["checkout", "--", path], check=False)
            continue
        full = os.path.join(cwd, path)
        try:
            os.remove(full)
        except OSError:
            continue
        parent = os.path.dirname(full)
        while parent and parent != cwd:
            try:
                os.rmdir(parent)
            except OSError:
                break
            parent = os.path.dirname(parent)


def head_sha(cwd: str) -> str:
    rc, out = _git(cwd, ["rev-parse", "HEAD"], check=False)
    return out.strip() if rc == 0 else ""


def commit(cwd: str, paths: List[str], subject: str, trailers: Dict[str, str]) -> str:
    """Stage exactly `paths`, commit, return the sha (or "" when nothing staged)."""
    if not paths:
        return ""
    _git(cwd, ["add", "--"] + list(paths))
    rc, _ = _git(cwd, ["diff", "--cached", "--quiet"], check=False)
    if rc == 0:
        return ""                              # staged set is identical to HEAD
    message = subject
    body = "\n".join("%s: %s" % (k, v) for k, v in trailers.items())
    if body:
        message = subject + "\n\n" + body
    _git(cwd, ["commit", "-q", "-m", message])
    return head_sha(cwd)


def diff_text(cwd: str, base_sha: str, paths: List[str],
              max_chars: int = 200000) -> str:
    """The working tree's diff against `base_sha`, bounded.

    `add -N` first so a file the Worker created shows as a diff rather than as
    nothing at all — an Evaluator handed an empty diff for a new component is
    exactly how an unreviewed change slips through.
    """
    if paths:
        _git(cwd, ["add", "-N", "--"] + list(paths), check=False)
    args = ["diff"]
    if base_sha:
        args.append(base_sha)
    if paths:
        args.append("--")
        args.extend(paths)
    _, out = _git(cwd, args, check=False)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n[... diff truncated at %d characters ...]\n" % max_chars
    return out


def _normalize(text: str) -> List[str]:
    """Stripped, blank-free lines with the import block sorted to the front."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    imports = sorted(ln for ln in lines if _IMPORT_RE.match(ln))
    rest = [ln for ln in lines if not _IMPORT_RE.match(ln)]
    return imports + rest


def similarity(src_path: str, dst_path: str) -> float:
    """difflib ratio over normalized lines: 1.0 identical, 0.0 nothing in common.

    This is what makes a "copy" task provable. The 2026-09-11 run's copy task
    passed with a six-line TODO comment because its verification grepped for
    that comment; this ratio would have scored it under 0.05.
    """
    a = _normalize(util.read_text(src_path))
    b = _normalize(util.read_text(dst_path))
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_*.py' -v`
Expected: PASS — 148 tests, `OK`

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/runner/git_ops.py plugins/agent-loop/tests/runner/test_git_ops.py
git commit -F - <<'EOF'
agent-loop: git containment, trailer commits and diff similarity

changed_paths/strays/revert are the sandbox step: anything outside the
contract's allow_list goes back, including after a killed phase. commit stages
exactly the paths it is given and renders Loop-Status/Loop-Verification/
Loop-Files as real trailers. similarity normalises whitespace and import order
and is what makes a copy task provable rather than grep-assertable.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 8: `harness.py` — the lock, the heartbeat, the rotating log and the guards

**Files:**
- Create: `plugins/agent-loop/runner/harness.py`
- Test: `plugins/agent-loop/tests/runner/test_harness.py`

**Interfaces:**
- Consumes: `util.atomic_write`, `util.read_json`, `util.read_int`, `util.stamp_epoch`, `util.process_alive`, `util.write_json`.
- Produces: `harness_alive(pid)`, `lock_acquire(runtime_dir, pid, loop_dir, version, alive=None, sleep=time.sleep) -> str`, `lock_release(runtime_dir, pid)`, `Heartbeat(runtime_dir, interval)` with `.start()`/`.stop()`, `write_tick_json(runtime_dir, tick, pid, started_at, timeout_s)`, `clear_tick_json(runtime_dir)`, `RotatingLog(path, max_bytes, backups)` with `.write(line)`, `mem_headroom() -> (int, int)`, `mem_guard_action(free_mb, swap_pct, min_mb, max_swap_pct) -> str`, `ratelimit_action(info, now, max_wait) -> str`, `backoff_delay(attempt) -> int`.
- `lock_acquire` prints one of `acquired` / `takeover:<oldpid>` / `held:<pid>:<start_epoch>` — the exact three tokens `run.sh` produced in v2, because the e2e asserts on the messages derived from them.
- `alive` is injectable so the lock's state machine is unit-testable without spawning a second harness.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_harness.py`:

```python
import _path  # noqa: F401
import json
import os
import tempfile
import time
import unittest

from runner import harness, util


class TestLock(unittest.TestCase):
    def setUp(self):
        self.rt = tempfile.mkdtemp()

    def test_a_free_dir_is_acquired_and_the_payload_names_the_owner(self):
        self.assertEqual("acquired",
                         harness.lock_acquire(self.rt, 4242, "/loop", "3.0.0",
                                              alive=lambda pid: False))
        rec = util.read_json(os.path.join(self.rt, "harness.json"))
        self.assertEqual(4242, rec["pid"])
        self.assertEqual("/loop", rec["loop_dir"])
        self.assertEqual("3.0.0", rec["plugin_version"])
        self.assertIsInstance(rec["start_epoch"], int)
        self.assertTrue(rec["host"])

    def test_a_live_owner_is_held_with_its_pid_and_start(self):
        harness.lock_acquire(self.rt, 111, "/loop", "3.0.0", alive=lambda pid: False)
        out = harness.lock_acquire(self.rt, 222, "/loop", "3.0.0", alive=lambda pid: True)
        self.assertTrue(out.startswith("held:111:"), out)
        self.assertEqual(111, util.read_json(os.path.join(self.rt, "harness.json"))["pid"])

    def test_a_dead_owner_is_taken_over(self):
        harness.lock_acquire(self.rt, 111, "/loop", "3.0.0", alive=lambda pid: False)
        out = harness.lock_acquire(self.rt, 222, "/loop", "3.0.0", alive=lambda pid: False)
        self.assertEqual("takeover:111", out)
        self.assertEqual(222, util.read_json(os.path.join(self.rt, "harness.json"))["pid"])

    def test_an_unreadable_lock_is_taken_over_too(self):
        util.atomic_write(os.path.join(self.rt, "harness.json"), "{garbage")
        out = harness.lock_acquire(self.rt, 222, "/loop", "3.0.0", alive=lambda pid: False)
        self.assertTrue(out.startswith("takeover:"), out)

    def test_the_lock_and_its_payload_appear_together(self):
        """There is no instant at which the lock exists without its owner record:
        it is link(2)ed into place from a fully written private file."""
        seen = {}

        def alive(pid):
            seen["at_check"] = util.read_json(os.path.join(self.rt, "harness.json"))
            return True

        harness.lock_acquire(self.rt, 111, "/loop", "3.0.0", alive=lambda p: False)
        harness.lock_acquire(self.rt, 222, "/loop", "3.0.0", alive=alive)
        self.assertEqual(111, seen["at_check"]["pid"])

    def test_release_only_removes_our_own_lock(self):
        harness.lock_acquire(self.rt, 111, "/loop", "3.0.0", alive=lambda pid: False)
        harness.lock_release(self.rt, 999)
        self.assertTrue(os.path.exists(os.path.join(self.rt, "harness.json")))
        harness.lock_release(self.rt, 111)
        self.assertFalse(os.path.exists(os.path.join(self.rt, "harness.json")))

    def test_release_is_safe_when_there_is_no_lock(self):
        harness.lock_release(self.rt, 111)

    def test_no_private_temp_files_are_left_behind(self):
        harness.lock_acquire(self.rt, 111, "/loop", "3.0.0", alive=lambda pid: False)
        harness.lock_acquire(self.rt, 222, "/loop", "3.0.0", alive=lambda pid: True)
        leftovers = [f for f in os.listdir(self.rt) if f.startswith(".harness.")]
        self.assertEqual([], leftovers)


class TestHeartbeat(unittest.TestCase):
    def test_it_stamps_an_epoch_and_stops_on_request(self):
        rt = tempfile.mkdtemp()
        hb = harness.Heartbeat(rt, interval=0.05)
        hb.start()
        try:
            deadline = time.time() + 3
            while time.time() < deadline and not os.path.exists(os.path.join(rt, "HEARTBEAT")):
                time.sleep(0.02)
            first = util.read_int(os.path.join(rt, "HEARTBEAT"))
            self.assertGreater(first, 0)
        finally:
            hb.stop()
        self.assertFalse(hb.thread.is_alive())

    def test_stop_is_idempotent(self):
        hb = harness.Heartbeat(tempfile.mkdtemp(), interval=0.05)
        hb.start()
        hb.stop()
        hb.stop()


class TestTickJson(unittest.TestCase):
    def test_written_while_a_tick_runs_and_removed_after(self):
        rt = tempfile.mkdtemp()
        harness.write_tick_json(rt, 7, 4242, 1700000000, 1800)
        rec = util.read_json(os.path.join(rt, "tick.json"))
        self.assertEqual({"tick": 7, "pid": 4242, "started_at": 1700000000,
                          "timeout_s": 1800}, rec)
        harness.clear_tick_json(rt)
        self.assertFalse(os.path.exists(os.path.join(rt, "tick.json")))

    def test_garbage_inputs_degrade_to_zero_rather_than_raising(self):
        rt = tempfile.mkdtemp()
        harness.write_tick_json(rt, "x", None, "y", None)
        self.assertEqual({"tick": 0, "pid": 0, "started_at": 0, "timeout_s": 0},
                         util.read_json(os.path.join(rt, "tick.json")))


class TestRotatingLog(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.path = os.path.join(self.d, "harness.log")

    def test_lines_are_appended_with_a_newline(self):
        log = harness.RotatingLog(self.path)
        log.write("first")
        log.write("second")
        with open(self.path) as f:
            self.assertEqual(["first\n", "second\n"], f.readlines())

    def test_it_rotates_at_the_size_cap_and_keeps_n_backups(self):
        log = harness.RotatingLog(self.path, max_bytes=100, backups=2)
        for i in range(60):
            log.write("x" * 20)
        self.assertTrue(os.path.exists(self.path + ".1"))
        self.assertTrue(os.path.exists(self.path + ".2"))
        self.assertFalse(os.path.exists(self.path + ".3"))
        self.assertLessEqual(os.path.getsize(self.path), 200)

    def test_the_newest_content_stays_in_the_live_file(self):
        log = harness.RotatingLog(self.path, max_bytes=50, backups=1)
        log.write("old" * 30)
        log.write("newest")
        with open(self.path) as f:
            self.assertIn("newest", f.read())


class TestMemoryGuard(unittest.TestCase):
    def test_headroom_returns_two_non_negative_integers(self):
        free_mb, swap_pct = harness.mem_headroom()
        self.assertIsInstance(free_mb, int)
        self.assertIsInstance(swap_pct, int)
        self.assertGreaterEqual(free_mb, 0)
        self.assertGreaterEqual(swap_pct, 0)

    def test_both_conditions_must_hold_before_it_delays(self):
        self.assertEqual("delay", harness.mem_guard_action(100, 95, 1024, 90))
        self.assertEqual("proceed", harness.mem_guard_action(100, 10, 1024, 90))
        self.assertEqual("proceed", harness.mem_guard_action(8000, 95, 1024, 90))

    def test_a_zero_ceiling_always_trips(self):
        self.assertEqual("delay", harness.mem_guard_action(0, 0, 999999999, 0))


class TestRateLimitAction(unittest.TestCase):
    def test_no_info_is_ok(self):
        self.assertEqual("ok", harness.ratelimit_action(None, 1000, 21600))
        self.assertEqual("ok", harness.ratelimit_action({}, 1000, 21600))

    def test_any_allowed_status_is_ok(self):
        for status in ("allowed", "allowed_warning"):
            self.assertEqual("ok", harness.ratelimit_action(
                {"status": status, "resetsAt": 9999999999}, 1000, 21600))

    def test_a_rejected_status_with_a_near_reset_waits(self):
        out = harness.ratelimit_action({"status": "rejected", "resetsAt": 1300}, 1000, 21600)
        self.assertEqual("wait 360", out)

    def test_a_reset_already_past_waits_the_floor(self):
        self.assertEqual("wait 5", harness.ratelimit_action(
            {"status": "rejected", "resetsAt": 1}, 1000, 21600))

    def test_a_reset_beyond_max_wait_exits(self):
        self.assertEqual("exit", harness.ratelimit_action(
            {"status": "rejected", "resetsAt": 9999999999}, 1000, 21600))

    def test_a_rejection_without_a_reset_exits(self):
        self.assertEqual("exit", harness.ratelimit_action({"status": "rejected"}, 1000, 21600))


class TestBackoff(unittest.TestCase):
    def test_it_doubles_and_caps(self):
        self.assertEqual(2, harness.backoff_delay(0))
        self.assertEqual(4, harness.backoff_delay(1))
        self.assertEqual(8, harness.backoff_delay(2))
        self.assertEqual(300, harness.backoff_delay(20))

    def test_a_negative_attempt_still_returns_at_least_one_second(self):
        self.assertEqual(1, harness.backoff_delay(-5))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_harness*.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'runner.harness'`

- [ ] **Step 3: Write minimal implementation**

Create `plugins/agent-loop/runner/harness.py`:

```python
"""Exclusivity, liveness, the rotating log, and the two guards that decide
whether the harness may start a tick at all.

`runtime/harness.json` IS the lock. It is created with `link(2)` from a fully
written private file, so the lock can never exist without its owner payload —
there is no instant at which a racer can read a stale pid out of a fresh lock.
A dead owner is cleared by rename, which exactly one racer can win.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from . import util

LOCK_NAME = "harness.json"


def harness_alive(pid: Any) -> bool:
    """Live pid whose command line contains `run.sh` — the only liveness test.

    The shim passes its own path to python as `--shim …/run.sh`, so the token is
    on the command line of the exec'd process too.
    """
    return util.process_alive(pid, "run.sh")


def _lock_payload(pid: int, loop_dir: str, version: str) -> Dict[str, Any]:
    try:
        host = socket.gethostname()
    except OSError:
        host = "unknown"
    return {"pid": int(pid), "start_epoch": int(time.time()), "host": host,
            "loop_dir": loop_dir, "plugin_version": version}


def lock_acquire(runtime_dir: str, pid: int, loop_dir: str, version: str,
                 alive: Optional[Callable[[Any], bool]] = None,
                 sleep: Callable[[float], None] = time.sleep) -> str:
    """`acquired` | `takeover:<oldpid>` | `held:<pid>:<start_epoch>`."""
    if alive is None:
        alive = harness_alive
    os.makedirs(runtime_dir, exist_ok=True)
    lock = os.path.join(runtime_dir, LOCK_NAME)
    tmp = os.path.join(runtime_dir, ".harness.%d.tmp" % pid)
    util.write_json(tmp, _lock_payload(pid, loop_dir, version))
    old_pid = ""
    cleared = 0
    cur_pid = "?"
    cur_start = 0
    try:
        for _ in range(10):
            try:
                os.link(tmp, lock)
            except OSError:
                rec = util.read_json(lock) or {}
                cur_pid = rec.get("pid", "")
                cur_start = rec.get("start_epoch", 0) or 0
                if cur_pid and alive(cur_pid):
                    return "held:%s:%s" % (cur_pid, cur_start)
                # Dead or unreadable owner. Clear by rename: exactly one racer
                # wins; the others see ENOENT, pause, and retry the link.
                if cur_pid:
                    old_pid = str(cur_pid)
                stale = os.path.join(runtime_dir, ".harness.stale.%d" % pid)
                try:
                    os.rename(lock, stale)
                    os.unlink(stale)
                    cleared += 1
                except OSError:
                    sleep(0.1)
                continue
            # Settle, then confirm: a racer that read the dead pid a microsecond
            # before our link could have renamed our fresh lock away.
            sleep(0.2)
            rec = util.read_json(lock) or {}
            if rec.get("pid") != pid:
                return "held:%s:%s" % (rec.get("pid", "?"), rec.get("start_epoch", 0))
            return ("takeover:%s" % (old_pid or "none")) if cleared else "acquired"
        return "held:%s:%s" % (cur_pid, cur_start)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def lock_release(runtime_dir: str, pid: int) -> None:
    """Remove the lock, but only when this pid still owns it. Never raises."""
    lock = os.path.join(runtime_dir, LOCK_NAME)
    rec = util.read_json(lock) or {}
    owner = rec.get("pid")
    if owner is not None and owner != pid:
        return
    try:
        os.unlink(lock)
    except OSError:
        pass


class Heartbeat(object):
    """A daemon thread stamping runtime/HEARTBEAT.

    Observers treat the harness as alive only when the pid is live AND this file
    is younger than 3x the interval, so a wedged-but-not-exited harness is still
    detectable. As a thread it cannot outlive the process it reports on, which
    is what the v2 background job needed an explicit parent check for.
    """

    def __init__(self, runtime_dir: str, interval: float = 10.0):
        self.runtime_dir = runtime_dir
        self.interval = interval
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="heartbeat")
        self.thread.daemon = True

    def _run(self) -> None:
        os.makedirs(self.runtime_dir, exist_ok=True)
        while True:
            util.stamp_epoch(os.path.join(self.runtime_dir, "HEARTBEAT"))
            if self._stop.wait(self.interval):
                return

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def write_tick_json(runtime_dir: str, tick: Any, pid: Any, started_at: Any,
                    timeout_s: Any) -> None:
    """The in-flight tick's liveness record; its presence means a tick is running."""
    util.write_json(os.path.join(runtime_dir, "tick.json"),
                    {"tick": _int_or_zero(tick), "pid": _int_or_zero(pid),
                     "started_at": _int_or_zero(started_at),
                     "timeout_s": _int_or_zero(timeout_s)})


def clear_tick_json(runtime_dir: str) -> None:
    try:
        os.unlink(os.path.join(runtime_dir, "tick.json"))
    except OSError:
        pass


class RotatingLog(object):
    """harness.log: the harness's own narration, bounded at max_bytes x backups.

    v2's run.log also carried every raw stream line and reached 105 MB in one
    run. The per-phase transcripts live in ~/.claude/projects/ and are referenced
    by path from `phase_end`, so nothing needs to be duplicated here.
    """

    def __init__(self, path: str, max_bytes: int = 10 * 1024 * 1024, backups: int = 3):
        self.path = path
        self.max_bytes = max_bytes
        self.backups = backups
        self._lock = threading.Lock()
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _rotate(self) -> None:
        for i in range(self.backups, 0, -1):
            src = self.path if i == 1 else "%s.%d" % (self.path, i - 1)
            dst = "%s.%d" % (self.path, i)
            if os.path.exists(src):
                try:
                    os.replace(src, dst)
                except OSError:
                    return
        oldest = "%s.%d" % (self.path, self.backups + 1)
        try:
            os.unlink(oldest)
        except OSError:
            pass

    def write(self, line: str) -> None:
        with self._lock:
            try:
                if os.path.exists(self.path) and os.path.getsize(self.path) >= self.max_bytes:
                    self._rotate()
                with open(self.path, "a") as f:
                    f.write(line.rstrip("\n") + "\n")
            except OSError:
                return


def _darwin_headroom() -> Tuple[int, int]:
    free_mb = 0
    swap_pct = 0
    try:
        out = subprocess.run(["vm_stat"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=5).stdout.decode()
        page = 4096
        pages = 0
        for line in out.splitlines():
            if "page size of" in line:
                for tok in line.split():
                    if tok.isdigit():
                        page = int(tok)
                        break
            for label in ("Pages free:", "Pages inactive:", "Pages speculative:"):
                if line.startswith(label):
                    pages += int(line.split()[-1].rstrip("."))
        free_mb = (pages * page) // (1024 * 1024)
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        pass
    try:
        out = subprocess.run(["sysctl", "-n", "vm.swapusage"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=5).stdout.decode()
        parts = out.replace("M", " ").split()
        total = used = 0.0
        for i, tok in enumerate(parts):
            if tok == "total" and i + 2 < len(parts):
                total = float(parts[i + 2])
            if tok == "used" and i + 2 < len(parts):
                used = float(parts[i + 2])
        if total > 0:
            swap_pct = int((used * 100) / total)
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        pass
    return free_mb, swap_pct


def _linux_headroom() -> Tuple[int, int]:
    free_mb = 0
    swap_total = swap_free = 0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    free_mb = int(line.split()[1]) // 1024
                elif line.startswith("SwapTotal:"):
                    swap_total = int(line.split()[1])
                elif line.startswith("SwapFree:"):
                    swap_free = int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return 0, 0
    swap_pct = int(((swap_total - swap_free) * 100) / swap_total) if swap_total > 0 else 0
    return free_mb, swap_pct


def mem_headroom() -> Tuple[int, int]:
    """(free_mb, swap_used_pct); (0, 0) on an unknown platform."""
    try:
        system = os.uname().sysname
    except AttributeError:
        return 0, 0
    if system == "Darwin":
        return _darwin_headroom()
    if system == "Linux":
        return _linux_headroom()
    return 0, 0


def mem_guard_action(free_mb: int, swap_pct: int, min_mb: int, max_swap_pct: int) -> str:
    """`delay` only when BOTH conditions hold.

    A low free figure alone is normal on a healthy box (the OS caches
    aggressively); it only matters once the machine is also paging. `>=` on the
    ceiling so an explicit ceiling of 0 always trips.
    """
    if free_mb < min_mb and swap_pct >= max_swap_pct:
        return "delay"
    return "proceed"


def ratelimit_action(info: Optional[Dict[str, Any]], now: int, max_wait: int) -> str:
    """`ok` | `wait <seconds>` | `exit` from a rate_limit_info payload.

    Any `allowed*` status (including `allowed_warning`, emitted near the
    threshold but still serving) is ok; only a genuinely throttled status waits.
    """
    if not info or not isinstance(info, dict):
        return "ok"
    status = info.get("status", "allowed")
    if isinstance(status, str) and status.startswith("allowed"):
        return "ok"
    resets = info.get("resetsAt")
    try:
        resets = int(resets)
    except (TypeError, ValueError):
        return "exit"
    wait = resets - int(now) + 60
    if wait < 5:
        wait = 5
    if wait > max_wait:
        return "exit"
    return "wait %d" % wait


def backoff_delay(attempt: int) -> int:
    """2^(attempt+1) seconds, at least 1, capped at 300."""
    try:
        d = 2 ** (int(attempt) + 1)
    except (TypeError, ValueError):
        d = 2
    if d < 1:
        d = 1
    return min(int(d), 300)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_*.py' -v`
Expected: PASS — 174 tests, `OK`

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/runner/harness.py plugins/agent-loop/tests/runner/test_harness.py
git commit -F - <<'EOF'
agent-loop: lock, heartbeat, rotating log and the pre-tick guards in Python

harness.json is still THE lock, still link(2)ed into place from a fully written
private file, still taken over from a dead owner by rename — the same race-free
semantics lib/harness.sh had, now with an injectable liveness probe so the
state machine is unit-testable. The heartbeat is a daemon thread, so it cannot
outlive the process it reports on. harness.log rotates at 10 MB x 3; run.log is
gone.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 9: `status.py` — the `LOOP_STATUS.md` banner

**Files:**
- Create: `plugins/agent-loop/runner/status.py`
- Test: `plugins/agent-loop/tests/runner/test_status.py`

**Interfaces:**
- Consumes: `util.atomic_write`.
- Produces: `pct(done, total)`, `fmt_dur(secs)`, `progress_bar(done, total, width)`, `gates_compact(verification_line)`, `cause_human(cause)`, `verdict_glyph(verdict)`, `tick_line(...)`, `session_header(...)`, `write_status(path, header, lines, tail=10)`.
- Output strings are byte-identical to v2's (`lib/loop.sh`): `LOOP_STATUS.md` is read by a human and by `/agent-loop`, and the e2e greps `── loop` and `^✓ t`.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_status.py`:

```python
import _path  # noqa: F401
import os
import tempfile
import unittest

from runner import status


class TestPrimitives(unittest.TestCase):
    def test_pct_rounds_to_nearest_and_handles_zero(self):
        self.assertEqual(0, status.pct(0, 0))
        self.assertEqual(62, status.pct(26, 42))
        self.assertEqual(100, status.pct(3, 3))
        self.assertEqual(50, status.pct(1, 2))

    def test_fmt_dur_has_three_shapes(self):
        self.assertEqual("45s", status.fmt_dur(45))
        self.assertEqual("3m12s", status.fmt_dur(192))
        self.assertEqual("1h18m", status.fmt_dur(4680))
        self.assertEqual("0s", status.fmt_dur(0))

    def test_progress_bar_fills_proportionally(self):
        self.assertEqual("████████████", status.progress_bar(3, 3, 12))
        self.assertEqual("░" * 12, status.progress_bar(0, 5, 12))
        self.assertEqual(12, len(status.progress_bar(1, 3, 12)))
        self.assertEqual("░" * 12, status.progress_bar(1, 0, 12))

    def test_gates_compact_marks_pass_and_fail(self):
        line = "Loop-Verification: lint=pass tsc=pass build=pass test=fail"
        self.assertEqual("lint✓ tsc✓ build✓ test✗", status.gates_compact(line))
        self.assertEqual("", status.gates_compact(""))

    def test_cause_human_spells_out_the_kill_signals(self):
        self.assertEqual("ok", status.cause_human("ok"))
        self.assertIn("SIGKILL", status.cause_human("killed"))
        self.assertIn("timeout", status.cause_human("timeout"))
        self.assertEqual("weird", status.cause_human("weird"))

    def test_verdict_glyphs(self):
        self.assertEqual("✓", status.verdict_glyph("done"))
        self.assertEqual("✓", status.verdict_glyph("continue"))
        self.assertEqual("✦", status.verdict_glyph("plan"))
        self.assertEqual("◆", status.verdict_glyph("review"))
        self.assertEqual("↻", status.verdict_glyph("retry"))
        self.assertEqual("✗", status.verdict_glyph("halt"))


class TestTickLine(unittest.TestCase):
    def test_a_committed_tick_shows_gates_and_the_sha(self):
        line = status.tick_line("done", 3, "T26", 192,
                                "lint✓ tsc✓ build✓ test✓", "a1b2c3", 26, 42)
        self.assertEqual("✓ t3 T26 · 3m12s · lint✓ tsc✓ build✓ test✓ · → a1b2c3   62% (26/42)",
                         line)

    def test_no_sha_reads_no_commit(self):
        line = status.tick_line("retry", 4, "T27", 60, "", "", 26, 42)
        self.assertIn("(no commit)", line)
        self.assertTrue(line.startswith("↻ t4 T27"))

    def test_an_unknown_task_renders_a_question_mark(self):
        self.assertIn("t5 ?", status.tick_line("plan", 5, "", 10, "", "", 0, 3))

    def test_a_non_ok_cause_is_appended_in_words(self):
        line = status.tick_line("retry", 12, "T34", 1800, "", "", 26, 42, "killed")
        self.assertTrue(line.endswith("· killed (SIGKILL — likely OS memory pressure)"))

    def test_an_ok_cause_adds_nothing(self):
        self.assertEqual(status.tick_line("done", 1, "T1", 5, "", "s", 1, 2),
                         status.tick_line("done", 1, "T1", 5, "", "s", 1, 2, "ok"))


class TestSessionHeader(unittest.TestCase):
    def test_the_task_weighted_form(self):
        head = status.session_header(26, 42, 4680, 2700, "5h 90% ↺2h12m")
        self.assertTrue(head.startswith("── loop · 62% "))
        self.assertIn("26/42", head)
        self.assertIn("⏱ 1h18m", head)
        self.assertIn("~45m00s left", head)
        self.assertIn("5h 90%", head)
        self.assertTrue(head.endswith(" ──"))

    def test_the_quota_segment_is_dropped_when_empty(self):
        self.assertNotIn(" · ·", status.session_header(1, 2, 10, 10, ""))

    def test_the_segment_weighted_form_while_segments_are_unplanned(self):
        head = status.session_header(48, 48, 10, 0, "", 3, 12)
        self.assertIn("25%", head)
        self.assertIn("seg 3/12", head)
        self.assertIn("48/48 planned tasks", head)

    def test_a_fully_planned_run_uses_the_task_form(self):
        head = status.session_header(48, 48, 10, 0, "", 12, 12)
        self.assertIn("48/48", head)
        self.assertNotIn("planned tasks", head)


class TestWriteStatus(unittest.TestCase):
    def test_it_writes_the_header_then_the_last_n_lines(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "LOOP_STATUS.md")
        status.write_status(p, "── loop · 10% ──", ["t%d line" % i for i in range(25)], tail=10)
        with open(p) as f:
            body = f.read()
        self.assertTrue(body.startswith("── loop · 10% ──\n\n"))
        self.assertIn("t24 line", body)
        self.assertNotIn("t14 line", body)
        self.assertIn("t15 line", body)

    def test_fewer_lines_than_the_tail_is_fine(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "LOOP_STATUS.md")
        status.write_status(p, "head", ["only"], tail=10)
        with open(p) as f:
            self.assertEqual("head\n\nonly\n", f.read())

    def test_a_banner_can_be_inserted_between_header_and_tail(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "LOOP_STATUS.md")
        status.write_status(p, "head", ["a"], tail=10, banner="⏸ usage limit")
        with open(p) as f:
            self.assertEqual("head\n\n⏸ usage limit\n\na\n", f.read())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_status*.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'runner.status'`

- [ ] **Step 3: Write minimal implementation**

Create `plugins/agent-loop/runner/status.py`:

```python
"""LOOP_STATUS.md — the glanceable, human-facing view of the run.

Pure string builders plus one writer. Every format here is byte-identical to
v2's lib/loop.sh: a human reads this file over someone's shoulder, /agent-loop
quotes it, and the e2e greps it.
"""
from __future__ import annotations

from typing import List, Optional

from . import util

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧"

_CAUSE_TEXT = {
    "ok": "ok",
    "timeout": "timeout (phase exceeded its wall-clock budget)",
    "killed": "killed (SIGKILL — likely OS memory pressure)",
    "terminated": "terminated (SIGTERM)",
    "api_error": "api error",
    "rate_limit": "rate limited (429)",
    "malformed-output": "the phase returned no usable JSON",
    "crashed": "crashed (no result payload)",
}

_GLYPHS = {"done": "✓", "continue": "✓", "plan": "✦", "review": "◆",
           "skip": "⏭", "retry": "↻"}


def pct(done: int, total: int) -> int:
    return (done * 100 + total // 2) // total if total > 0 else 0


def fmt_dur(secs: int) -> str:
    s = int(secs or 0)
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm%02ds" % (s // 60, s % 60)
    return "%dh%02dm" % (s // 3600, (s % 3600) // 60)


def progress_bar(done: int, total: int, width: int) -> str:
    filled = (done * width // total) if total > 0 else 0
    filled = max(0, min(filled, width))
    return "█" * filled + "░" * (width - filled)


def gates_compact(verification_line: str) -> str:
    """`Loop-Verification: lint=pass test=fail` -> `lint✓ test✗`."""
    line = verification_line or ""
    if "Loop-Verification:" in line:
        line = line.split("Loop-Verification:", 1)[1]
    out = []
    for tok in line.split():
        if "=" not in tok:
            continue
        key, val = tok.split("=", 1)
        out.append(key + ("✓" if val == "pass" else "✗"))
    return " ".join(out)


def cause_human(cause: str) -> str:
    return _CAUSE_TEXT.get(cause or "", cause or "")


def verdict_glyph(verdict: str) -> str:
    return _GLYPHS.get(verdict or "", "✗")


def tick_line(verdict: str, tick: int, task: str, dur: int, gates: str, sha: str,
              done: int, total: int, cause: str = "") -> str:
    """One permanent per-tick summary line."""
    mid = ("%s · → %s" % (gates, sha)) if (sha and gates) else \
          ("→ %s" % sha if sha else "(no commit)")
    why = ""
    if cause and cause != "ok":
        why = " · %s" % cause_human(cause)
    return "%s t%s %s · %s · %s   %d%% (%d/%d)%s" % (
        verdict_glyph(verdict), tick, task or "?", fmt_dur(dur), mid,
        pct(done, total), done, total, why)


def session_header(done: int, total: int, elapsed: int, eta: int, plan_usage: str,
                   seg_done: int = 0, seg_total: int = 0) -> str:
    """The one-line run header.

    While any segment is still unplanned the percent switches to the
    segment-weighted estimate: "48/48 tasks" is not progress when nine segments
    have yet to be written.
    """
    quota = (" · %s" % plan_usage) if plan_usage else ""
    if seg_total > 0 and seg_done < seg_total:
        return ("── loop · %d%% %s seg %d/%d · %d/%d planned tasks · ⏱ %s · ~%s left%s ──"
                % (pct(seg_done, seg_total), progress_bar(seg_done, seg_total, 12),
                   seg_done, seg_total, done, total, fmt_dur(elapsed), fmt_dur(eta), quota))
    return ("── loop · %d%% %s %d/%d · ⏱ %s · ~%s left%s ──"
            % (pct(done, total), progress_bar(done, total, 12), done, total,
               fmt_dur(elapsed), fmt_dur(eta), quota))


def write_status(path: str, header: str, lines: List[str], tail: int = 10,
                 banner: Optional[str] = None) -> None:
    """Header, an optional banner, then the last `tail` per-tick lines."""
    body = list(lines)[-tail:] if tail > 0 else list(lines)
    parts = [header, ""]
    if banner:
        parts.extend([banner, ""])
    parts.extend(body)
    util.atomic_write(path, "\n".join(parts) + "\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_*.py' -v`
Expected: PASS — 192 tests, `OK`

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/runner/status.py plugins/agent-loop/tests/runner/test_status.py
git commit -F - <<'EOF'
agent-loop: the LOOP_STATUS banner as pure string builders

Byte-identical output to lib/loop.sh's session_header/tick_line/gates_compact,
now testable without a running loop and without a subshell per field. The
segment-weighted header still kicks in while any segment is unplanned.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 10: `task_state.py` — `runtime/task-<T>.json`, the per-task state machine

**Files:**
- Create: `plugins/agent-loop/runner/task_state.py`
- Test: `plugins/agent-loop/tests/runner/test_task_state.py`

**Interfaces:**
- Consumes: `util.read_json`, `util.write_json`.
- Produces: `PHASES`, `DONE_SUFFIX`, `state_path(runtime_dir, task_id)`, `has_state(runtime_dir, task_id)`, `TaskState` with `load(runtime_dir, task_id)`, `save()`, `begin_attempt(tier)`, `end_attempt(outcome, judge=None)`, `begin_phase(name)`, `end_phase(result=None)`, `add_artifact(path)`, `last_attempt()`, and `boot_resume(state) -> str`.
- The on-disk shape is spec §14's, verbatim: `{"task","phase","attempt","attempts":[{"n","tier","phase_results":[…],"outcome","judge":{…}}],"session_ids":{"worker":"…"},"contract","artifacts":[…],"updated"}`.
- This **replaces** `runtime/attempts-<T>.json`. Nothing writes that file; plan C reads `task-<T>.json` for the Judge dossier and for `incident-<id>.json`'s `phases: []`, and plan B appends screenshots through `add_artifact`.
- `phase` is a PHASES name while the phase is in flight and `"<NAME>:done"` once it has returned. That one distinction is what lets the boot rule tell "killed inside WORK" from "Worker complete, killed before the gate" — the whole point of spec §14.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_task_state.py`:

```python
import _path  # noqa: F401
import json
import os
import tempfile
import unittest

from runner import task_state, util
from runner.claude_proc import PhaseResult
from runner.task_state import TaskState


class Base(unittest.TestCase):
    def setUp(self):
        self.rt = tempfile.mkdtemp()

    def raw(self, task_id="T60"):
        return util.read_json(task_state.state_path(self.rt, task_id))

    def result(self, phase="worker", session="sess-1", rc=0):
        return PhaseResult(phase=phase, model="mid", rc=rc, session_id=session,
                           started=100, ended=160)


class TestFreshState(Base):
    def test_a_task_with_no_file_loads_empty_rather_than_raising(self):
        state = TaskState.load(self.rt, "T60")
        self.assertEqual("T60", state.task)
        self.assertEqual("", state.phase)
        self.assertEqual(0, state.attempt)
        self.assertEqual([], state.attempts)
        self.assertFalse(task_state.has_state(self.rt, "T60"))

    def test_a_malformed_file_loads_empty_rather_than_raising(self):
        util.atomic_write(task_state.state_path(self.rt, "T60"), "{not json")
        self.assertEqual(0, TaskState.load(self.rt, "T60").attempt)

    def test_the_path_is_task_id_json_under_runtime(self):
        self.assertEqual(os.path.join(self.rt, "task-T60.json"),
                         task_state.state_path(self.rt, "T60"))


class TestShape(Base):
    def test_the_document_carries_exactly_the_spec_keys(self):
        state = TaskState.load(self.rt, "T60")
        state.contract = "runtime/sprint-T60.json"
        state.begin_attempt("standard")
        state.begin_phase("WORK")
        state.end_phase(self.result())
        state.end_attempt("pass", judge={"decision": "none"})
        doc = self.raw()
        self.assertEqual(["task", "phase", "attempt", "attempts", "session_ids",
                          "contract", "artifacts", "updated"], list(doc.keys()))
        self.assertEqual(["n", "tier", "phase_results", "outcome", "judge"],
                         list(doc["attempts"][0].keys()))
        self.assertIsInstance(doc["updated"], int)
        self.assertEqual("runtime/sprint-T60.json", doc["contract"])

    def test_every_transition_writes_the_file(self):
        state = TaskState.load(self.rt, "T60")
        state.begin_attempt("standard")
        self.assertEqual(1, self.raw()["attempt"])
        state.begin_phase("SCOUT")
        self.assertEqual("SCOUT", self.raw()["phase"])
        state.end_phase(self.result(phase="scout"))
        self.assertEqual("SCOUT:done", self.raw()["phase"])

    def test_no_tmp_file_is_left_behind(self):
        TaskState.load(self.rt, "T60").save()
        self.assertEqual(["task-T60.json"], os.listdir(self.rt))

    def test_a_round_trip_preserves_everything(self):
        state = TaskState.load(self.rt, "T60")
        state.contract = "runtime/sprint-T60.json"
        state.begin_attempt("standard")
        state.begin_phase("WORK")
        state.end_phase(self.result(session="sess-w"))
        state.add_artifact("artifacts/T60/orgunits.png")
        state.end_attempt("needs-work")
        back = TaskState.load(self.rt, "T60")
        self.assertEqual(state.phase, back.phase)
        self.assertEqual(1, back.attempt)
        self.assertEqual("sess-w", back.session_ids["worker"])
        self.assertEqual(["artifacts/T60/orgunits.png"], back.artifacts)
        self.assertEqual("needs-work", back.attempts[0]["outcome"])


class TestAttempts(Base):
    def test_begin_attempt_numbers_from_one_and_records_the_tier(self):
        state = TaskState.load(self.rt, "T60")
        state.begin_attempt("standard")
        state.begin_attempt("most-capable")
        self.assertEqual(2, state.attempt)
        self.assertEqual([1, 2], [a["n"] for a in state.attempts])
        self.assertEqual(["standard", "most-capable"],
                         [a["tier"] for a in state.attempts])

    def test_end_attempt_stamps_the_open_attempt_only(self):
        state = TaskState.load(self.rt, "T60")
        state.begin_attempt("standard")
        state.end_attempt("needs-work")
        state.begin_attempt("standard")
        state.end_attempt("pass", judge={"decision": "retry"})
        self.assertEqual(["needs-work", "pass"], [a["outcome"] for a in state.attempts])
        self.assertEqual({}, state.attempts[0]["judge"])
        self.assertEqual("retry", state.attempts[1]["judge"]["decision"])

    def test_end_attempt_with_no_open_attempt_is_a_no_op(self):
        TaskState.load(self.rt, "T60").end_attempt("pass")   # must not raise

    def test_phase_results_accumulate_under_the_open_attempt(self):
        state = TaskState.load(self.rt, "T60")
        state.begin_attempt("standard")
        for name, phase in (("SCOUT", "scout"), ("WORK", "worker"),
                            ("EVALUATE", "evaluator")):
            state.begin_phase(name)
            state.end_phase(self.result(phase=phase))
        self.assertEqual(["scout", "worker", "evaluator"],
                         [p["phase"] for p in state.attempts[0]["phase_results"]])
        self.assertEqual(60, state.attempts[0]["phase_results"][0]["dur"])

    def test_a_phase_ending_before_any_attempt_opens_one(self):
        state = TaskState.load(self.rt, "T60")
        state.begin_phase("SCOUT")
        state.end_phase(self.result(phase="scout"))
        self.assertEqual(1, state.attempt)
        self.assertEqual(1, len(state.attempts[0]["phase_results"]))

    def test_a_harness_phase_records_no_phase_result(self):
        state = TaskState.load(self.rt, "T60")
        state.begin_attempt("standard")
        state.begin_phase("GATE")
        state.end_phase()
        self.assertEqual("GATE:done", state.phase)
        self.assertEqual([], state.attempts[0]["phase_results"])

    def test_session_ids_are_keyed_by_role_and_the_newest_wins(self):
        state = TaskState.load(self.rt, "T60")
        state.begin_attempt("standard")
        state.end_phase(self.result(phase="worker", session="sess-1"))
        state.end_phase(self.result(phase="worker", session="sess-2"))
        state.end_phase(self.result(phase="scout", session="sess-s"))
        self.assertEqual({"worker": "sess-2", "scout": "sess-s"}, state.session_ids)


class TestBootResume(Base):
    def resume_for(self, phase):
        state = TaskState.load(self.rt, "T60")
        state.phase = phase
        return task_state.boot_resume(state)

    def test_a_worker_complete_task_resumes_at_the_gate(self):
        for phase in ("WORK:done", "SANDBOX", "SANDBOX:done", "GATE",
                      "EVALUATE", "COMMIT:done"):
            self.assertEqual("gate", self.resume_for(phase), phase)

    def test_a_task_killed_inside_work_goes_to_the_wrap_up_path(self):
        self.assertEqual("wrapup", self.resume_for("WORK"))

    def test_anything_before_work_restarts_from_the_scout(self):
        for phase in ("", "SELECT", "SCOUT", "SCOUT:done", "VALIDATE", "nonsense"):
            self.assertEqual("scout", self.resume_for(phase), phase)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_task_state*.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'runner.task_state'`

- [ ] **Step 3: Write minimal implementation**

Create `plugins/agent-loop/runner/task_state.py`:

```python
"""runtime/task-<T>.json — the durable record of ONE task's state machine (spec §14).

The plan file is the graph. `events.jsonl` is the stream the dashboard reads and
is never used to reconstruct control state. This file is the only thing that says
where inside a task the harness was when it died, and it is rewritten atomically
at every phase transition — so a kill costs at most the phase in flight, never
the task.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import util

# The tick's phases, in order. `phase` holds one of these while it is in flight
# and "<NAME>:done" once it has returned; boot_resume needs exactly that
# distinction to tell a killed Worker from a Worker-complete task.
PHASES = ("SELECT", "SCOUT", "VALIDATE", "WORK", "SANDBOX", "GATE",
          "RENDER", "FIDELITY", "EVALUATE", "COMMIT", "LEARN")
DONE_SUFFIX = ":done"
_WORK_INDEX = PHASES.index("WORK")


def state_path(runtime_dir: str, task_id: str) -> str:
    return os.path.join(runtime_dir, "task-%s.json" % task_id)


def has_state(runtime_dir: str, task_id: str) -> bool:
    """True when a previous harness got far enough to record anything at all."""
    return os.path.exists(state_path(runtime_dir, task_id))


@dataclass
class TaskState:
    task: str
    runtime_dir: str
    phase: str = ""
    attempt: int = 0
    attempts: List[Dict[str, Any]] = field(default_factory=list)
    session_ids: Dict[str, str] = field(default_factory=dict)
    contract: str = ""
    artifacts: List[str] = field(default_factory=list)
    updated: int = 0

    # ---------------------------------------------------------------- loading
    @classmethod
    def load(cls, runtime_dir: str, task_id: str) -> "TaskState":
        """The recorded state, or an empty one. A malformed file is an empty one."""
        raw = util.read_json(state_path(runtime_dir, task_id))
        if not isinstance(raw, dict):
            return cls(task=task_id, runtime_dir=runtime_dir)
        return cls(
            task=raw.get("task") or task_id,
            runtime_dir=runtime_dir,
            phase=raw.get("phase", "") or "",
            attempt=int(raw.get("attempt") or 0),
            attempts=list(raw.get("attempts") or []),
            session_ids=dict(raw.get("session_ids") or {}),
            contract=raw.get("contract", "") or "",
            artifacts=list(raw.get("artifacts") or []),
            updated=int(raw.get("updated") or 0),
        )

    @property
    def path(self) -> str:
        return state_path(self.runtime_dir, self.task)

    def to_dict(self) -> Dict[str, Any]:
        """Spec §14's key order, so a human diffing two of these can read them."""
        return {"task": self.task, "phase": self.phase, "attempt": self.attempt,
                "attempts": self.attempts, "session_ids": self.session_ids,
                "contract": self.contract, "artifacts": self.artifacts,
                "updated": self.updated}

    def save(self) -> None:
        self.updated = int(time.time())
        util.write_json(self.path, self.to_dict())

    # -------------------------------------------------------------- attempts
    def last_attempt(self) -> Optional[Dict[str, Any]]:
        return self.attempts[-1] if self.attempts else None

    def begin_attempt(self, tier: str) -> Dict[str, Any]:
        """Open attempt n+1 at `tier`. The tier is recorded, never derived.

        Whatever chose the tier — config on attempt 1, plan C's Judge on a
        re-attempt — this is the only place it is written down, so a postmortem
        can see what was actually dispatched rather than what a ladder implies.
        """
        self.attempt += 1
        record = {"n": self.attempt, "tier": tier, "phase_results": [],
                  "outcome": "", "judge": {}}
        self.attempts.append(record)
        self.save()
        return record

    def end_attempt(self, outcome: str, judge: Optional[Dict[str, Any]] = None) -> None:
        record = self.last_attempt()
        if record is None:
            return
        record["outcome"] = outcome
        record["judge"] = dict(judge or {})
        self.save()

    # ---------------------------------------------------------------- phases
    def begin_phase(self, name: str) -> None:
        self.phase = name
        self.save()

    def end_phase(self, result: Any = None) -> None:
        """Stamp the phase finished and, for an LLM phase, record what it did.

        `result` is None for a harness phase (SANDBOX, GATE, COMMIT): there is no
        subprocess, no session and no usage, only the fact that it completed.
        """
        if self.phase and not self.phase.endswith(DONE_SUFFIX):
            self.phase = self.phase + DONE_SUFFIX
        if result is not None:
            record = self.last_attempt()
            if record is None:
                record = self.begin_attempt("")
            record["phase_results"].append({
                "phase": result.phase, "model": result.model, "rc": result.rc,
                "started": result.started, "ended": result.ended,
                "dur": max(0, int(result.ended) - int(result.started)),
                "session": result.session_id or "", "killed": bool(result.killed)})
            if result.session_id:
                self.session_ids[result.phase] = result.session_id
        self.save()

    def add_artifact(self, path: str) -> None:
        """Plan B's screenshots land here so the Evaluator's inputs are replayable."""
        if path and path not in self.artifacts:
            self.artifacts.append(path)
            self.save()


def boot_resume(state: TaskState) -> str:
    """`gate` | `wrapup` | `scout` — where a `[~]` task picks up (spec §14).

    A Worker that returned means the tree already holds its work, so re-running
    it would burn a Worker budget to redo what is on disk: resume at GATE. A
    Worker killed mid-phase has a checkpoint and a session id, so it gets the
    wrap-up/resume path. Anything earlier has no work to preserve.
    """
    name = (state.phase or "").split(DONE_SUFFIX)[0]
    finished = (state.phase or "").endswith(DONE_SUFFIX)
    if name not in PHASES:
        return "scout"
    index = PHASES.index(name)
    if index < _WORK_INDEX:
        return "scout"
    if index == _WORK_INDEX and not finished:
        return "wrapup"
    return "gate"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_*.py' -v`
Expected: PASS — 209 tests, `OK`

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/runner/task_state.py \
        plugins/agent-loop/tests/runner/test_task_state.py
git commit -F - <<'EOF'
agent-loop: runtime/task-<T>.json, the per-task state machine from spec 14

The plan file stays the graph and events.jsonl stays the stream; this file is
the only thing that says where inside a task the harness was when it died. It
is rewritten atomically at every phase transition, and `phase` distinguishes
"WORK" from "WORK:done" so boot_resume can tell a killed Worker (wrap-up and
resume its session) from a Worker-complete task (straight to the gate, no
second Worker budget spent redoing what is already in the tree). Attempts
record the tier that was actually dispatched rather than one a ladder implies.
Replaces attempts-<T>.json, which nothing writes any more.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 11: `calibrate.py` — a `Limits:` line from a real run's events

**Files:**
- Create: `plugins/agent-loop/runner/calibrate.py`
- Create: `plugins/agent-loop/tests/fixtures/events-calibrate.jsonl`
- Test: `plugins/agent-loop/tests/runner/test_calibrate.py`

**Interfaces:**
- Consumes: `config.DEFAULT_LIMITS` (for the keys it does not calibrate).
- Produces: `ROLE_KEY`, `DOC_KEYS`, `events_path(target)`, `role_spans(path)`, `p90(values)`, `round_up_60(secs)`, `suggest(spans)`, `limits_line(values)`, `main(argv=None) -> int`.
- Run as `python3 -m runner.calibrate <loop-dir|events.jsonl> [--json]`. It prints one `Limits:` line to stdout and nothing else, so it can be pasted straight into `LOOP_CONFIG.md`. Documenting that workflow in the README is **plan D's**; this task only builds the tool.
- Calibrated roles: scout, worker, evaluator, judge, planner (spec §4.2). A role with no observed span keeps its default — a calibration from a run that never needed a Judge must not silently shrink the Judge's budget to zero.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/fixtures/events-calibrate.jsonl` (one compact object per line, the real envelope):

```json
{"t":1000,"seq":1,"type":"tick_start","tick":1}
{"t":1000,"seq":2,"type":"role_start","role":"Scout","model":"mid","desc":"Scout T1"}
{"t":1100,"seq":3,"type":"role_end","role":"Scout"}
{"t":1100,"seq":4,"type":"role_start","role":"Worker","model":"mid","desc":"Worker T1"}
{"t":1500,"seq":5,"type":"role_end","role":"Worker"}
{"t":1500,"seq":6,"type":"role_start","role":"Evaluator","model":"big","desc":"Evaluate T1"}
{"t":1700,"seq":7,"type":"role_end","role":"Evaluator"}
{"t":1700,"seq":8,"type":"tick_end","tick":1,"verdict":"continue"}
{"t":2000,"seq":9,"type":"tick_start","tick":2}
{"t":2000,"seq":10,"type":"role_start","role":"Scout","model":"mid","desc":"Scout T2"}
{"t":2200,"seq":11,"type":"role_end","role":"Scout"}
{"t":2200,"seq":12,"type":"role_start","role":"Worker","model":"mid","desc":"Worker T2"}
{"t":3200,"seq":13,"type":"role_end","role":"Worker"}
{"t":3200,"seq":14,"type":"tick_end","tick":2,"verdict":"continue"}
{"t":4000,"seq":15,"type":"tick_start","tick":3}
{"t":4000,"seq":16,"type":"role_start","role":"Worker","model":"mid","desc":"Worker T3"}
{"t":9000,"seq":17,"type":"tick_start","tick":4}
{"t":9000,"seq":18,"type":"loop_end","reason":"paused","exit_code":0}
```

Create `plugins/agent-loop/tests/runner/test_calibrate.py`:

```python
import _path  # noqa: F401
import json
import os
import tempfile
import unittest

from runner import calibrate, config

FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "fixtures", "events-calibrate.jsonl")


class TestRoleSpans(unittest.TestCase):
    def setUp(self):
        self.spans = calibrate.role_spans(FIXTURE)

    def test_each_role_end_is_paired_with_its_own_start_in_order(self):
        self.assertEqual([100, 200], self.spans["scout"])
        self.assertEqual([400, 1000], self.spans["worker"])
        self.assertEqual([200], self.spans["evaluator"])

    def test_a_killed_phase_contributes_no_span(self):
        """Tick 3's Worker never emitted role_end. Its wall clock is the cap, not
        the work, so counting it would calibrate the budget up to the budget."""
        self.assertEqual(2, len(self.spans["worker"]))

    def test_a_role_that_never_ran_is_absent(self):
        self.assertNotIn("judge", self.spans)
        self.assertNotIn("planner", self.spans)

    def test_a_missing_or_empty_file_yields_nothing(self):
        self.assertEqual({}, calibrate.role_spans("/nonexistent/events.jsonl"))

    def test_a_garbage_line_is_skipped(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "events.jsonl")
        with open(path, "w") as f:
            f.write("not json\n")
            f.write('{"t":10,"type":"role_start","role":"Scout"}\n')
            f.write('{"t":40,"type":"role_end","role":"Scout"}\n')
        self.assertEqual([30], calibrate.role_spans(path)["scout"])


class TestArithmetic(unittest.TestCase):
    def test_p90_is_nearest_rank(self):
        self.assertEqual(10, calibrate.p90([10]))
        self.assertEqual(200, calibrate.p90([100, 200]))
        self.assertEqual(9, calibrate.p90(list(range(1, 11))))   # nearest rank 9
        self.assertEqual(0, calibrate.p90([]))

    def test_round_up_60(self):
        self.assertEqual(60, calibrate.round_up_60(1))
        self.assertEqual(60, calibrate.round_up_60(60))
        self.assertEqual(120, calibrate.round_up_60(61))
        self.assertEqual(60, calibrate.round_up_60(0))


class TestSuggest(unittest.TestCase):
    def setUp(self):
        self.values = calibrate.suggest(calibrate.role_spans(FIXTURE))

    def test_an_observed_role_gets_p90_times_one_and_a_half_rounded_up(self):
        self.assertEqual(300, self.values["scout_timeout"])       # 200 * 1.5
        self.assertEqual(1500, self.values["worker_timeout"])     # 1000 * 1.5
        self.assertEqual(300, self.values["eval_timeout"])        # 200 * 1.5

    def test_an_unobserved_role_keeps_its_default(self):
        self.assertEqual(config.DEFAULT_LIMITS["judge_timeout"],
                         self.values["judge_timeout"])
        self.assertEqual(config.DEFAULT_LIMITS["planner_timeout"],
                         self.values["planner_timeout"])

    def test_the_non_budget_keys_are_passed_through_untouched(self):
        for key in ("tick_timeout", "gate_cmd_timeout", "worker_resume_max",
                    "worker_budget_usd", "max_attempts", "wrapup_timeout"):
            self.assertEqual(config.DEFAULT_LIMITS[key], self.values[key], key)


class TestLimitsLine(unittest.TestCase):
    def test_it_renders_every_documented_key_once(self):
        line = calibrate.limits_line(calibrate.suggest(calibrate.role_spans(FIXTURE)))
        self.assertTrue(line.startswith("Limits: "))
        keys = [tok.split("=")[0] for tok in line[len("Limits: "):].split()]
        self.assertEqual(list(calibrate.DOC_KEYS), keys)

    def test_the_line_round_trips_through_the_config_parser(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "LOOP_CONFIG.md")
        line = calibrate.limits_line(calibrate.suggest(calibrate.role_spans(FIXTURE)))
        with open(path, "w") as f:
            f.write("Worktree: /tmp/wt\n%s\n" % line)
        cfg = config.load_config(path)
        self.assertEqual(1500, config.phase_limit(cfg, "worker"))
        self.assertEqual(300, config.phase_limit(cfg, "evaluator"))


class TestMain(unittest.TestCase):
    def test_a_loop_dir_and_a_file_are_both_accepted(self):
        self.assertEqual(FIXTURE, calibrate.events_path(FIXTURE))
        d = os.path.dirname(FIXTURE)
        self.assertEqual(os.path.join(d, "events.jsonl"), calibrate.events_path(d))

    def test_json_mode_reports_the_sample_counts(self, ):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = calibrate.main([FIXTURE, "--json"])
        payload = json.loads(buf.getvalue())
        self.assertEqual(0, rc)
        self.assertEqual(2, payload["samples"]["worker"])
        self.assertEqual(1500, payload["suggested"]["worker_timeout"])

    def test_a_missing_events_file_is_an_error_not_a_traceback(self):
        self.assertEqual(1, calibrate.main(["/nonexistent"]))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_calibrate*.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'runner.calibrate'`

- [ ] **Step 3: Write minimal implementation**

Create `plugins/agent-loop/runner/calibrate.py`:

```python
"""Suggest `Limits:` values from a real run's events.jsonl (spec §4.2).

The shipped budgets are derived from one run (2026-09-11) and are explicitly
provisional. After a segment finishes, run

    python3 -m runner.calibrate .claude/loop/<run>

and paste the single line it prints into LOOP_CONFIG.md. v3's spans are exact —
one subprocess per phase, `role_start`/`role_end` around it — unlike 2.x's
description-regex role sniffing, so the input is trustworthy.
"""
from __future__ import annotations

import json
import math
import os
import sys
from typing import Any, Dict, List, Optional

from .config import DEFAULT_LIMITS

# The five roles spec §4.2 calibrates, keyed by the event's `role` lowercased.
ROLE_KEY = {"scout": "scout_timeout", "worker": "worker_timeout",
            "evaluator": "eval_timeout", "judge": "judge_timeout",
            "planner": "planner_timeout"}

# The documented `Limits:` line, in the interfaces doc's order.
DOC_KEYS = ("tick_timeout", "scout_timeout", "worker_timeout", "wrapup_timeout",
            "eval_timeout", "judge_timeout", "planner_timeout", "gate_cmd_timeout",
            "worker_resume_max", "worker_budget_usd", "max_attempts")

HEADROOM = 1.5


def events_path(target: str) -> str:
    """Accept either the events file or the loop dir that contains it."""
    if os.path.isdir(target):
        return os.path.join(target, "events.jsonl")
    return target


def role_spans(path: str) -> Dict[str, List[int]]:
    """{role: [seconds, …]} from paired role_start/role_end events, in order.

    An unpaired `role_start` is a phase that was killed: its wall clock is the
    budget, not the work, so counting it would calibrate every cap up to itself.
    `tick_start` closes the book on any start still open, which is exactly when a
    killed phase becomes visible.
    """
    spans: Dict[str, List[int]] = {}
    open_at: Dict[str, int] = {}
    try:
        handle = open(path)
    except OSError:
        return spans
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            kind = rec.get("type")
            if kind == "tick_start":
                open_at = {}
                continue
            role = str(rec.get("role") or "").lower()
            if not role:
                continue
            if kind == "role_start":
                open_at[role] = int(rec.get("t") or 0)
            elif kind == "role_end" and role in open_at:
                dur = int(rec.get("t") or 0) - open_at.pop(role)
                if dur >= 0:
                    spans.setdefault(role, []).append(dur)
    return spans


def p90(values: List[int]) -> int:
    """Nearest-rank p90. On a short sample that is the slowest run, which is the
    honest reading: a cap set from a median kills the tail every time."""
    ordered = sorted(values)
    if not ordered:
        return 0
    rank = int(math.ceil(0.9 * len(ordered)))
    return int(ordered[max(1, rank) - 1])


def round_up_60(secs: float) -> int:
    """Up to the next whole minute; never zero."""
    return max(60, int(math.ceil(float(secs) / 60.0) * 60))


def suggest(spans: Dict[str, List[int]]) -> Dict[str, Any]:
    """Every documented key: calibrated where observed, default where not.

    A role that never ran keeps its default. A run with no Judge must not
    silently shrink the Judge's budget to a minute.
    """
    values = dict(DEFAULT_LIMITS)
    for role, key in ROLE_KEY.items():
        observed = spans.get(role) or []
        if observed:
            values[key] = round_up_60(p90(observed) * HEADROOM)
    return dict((k, values[k]) for k in DOC_KEYS)


def limits_line(values: Dict[str, Any]) -> str:
    return "Limits: " + " ".join("%s=%s" % (k, values[k]) for k in DOC_KEYS)


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    targets = [a for a in args if not a.startswith("-")]
    if not targets:
        sys.stderr.write("usage: python3 -m runner.calibrate <loop-dir|events.jsonl> "
                         "[--json]\n")
        return 1
    path = events_path(targets[0])
    if not os.path.exists(path):
        sys.stderr.write("no events file at %s\n" % path)
        return 1
    spans = role_spans(path)
    values = suggest(spans)
    if as_json:
        print(json.dumps({"events": path,
                          "samples": dict((r, len(v)) for r, v in spans.items()),
                          "p90": dict((r, p90(v)) for r, v in spans.items()),
                          "suggested": values}, indent=2, sort_keys=True))
    else:
        print(limits_line(values))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_*.py' -v`
Expected: PASS — 224 tests, `OK`
Sanity-check the CLI by hand: `cd plugins/agent-loop && python3 -m runner.calibrate tests/fixtures/events-calibrate.jsonl` must print exactly one `Limits: …` line.

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/runner/calibrate.py \
        plugins/agent-loop/tests/fixtures/events-calibrate.jsonl \
        plugins/agent-loop/tests/runner/test_calibrate.py
git commit -F - <<'EOF'
agent-loop: calibrate.py prints a Limits: line from a real run's events

Spec 4.2 says the shipped budgets are provisional. This pairs role_start with
role_end per tick, takes the p90 of each role's spans, multiplies by 1.5 and
rounds up to the minute. An unpaired role_start is a killed phase and is
dropped: its wall clock is the cap, not the work, so counting it would
calibrate every budget up to itself. A role that never ran keeps its default
rather than collapsing to a minute. Output is one pasteable line, or --json for
the sample counts behind it.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 12: `claude_proc.py` — one `claude -p` subprocess per phase, parsed in-process

**Files:**
- Create: `plugins/agent-loop/runner/claude_proc.py`
- Rewrite: `plugins/agent-loop/tests/fixtures/claude` (mode 755)
- Test: `plugins/agent-loop/tests/runner/test_claude_proc.py`

**Interfaces:**
- Consumes: `util.stamp_epoch`, `util.write_json`.
- Produces: `Usage`, `PhaseResult`, `build_argv(...)`, `is_stalled(last_activity, outstanding, stall_s, now)`, `transcript_path(cwd, session_id)`, `run_phase(**kwargs) -> PhaseResult`.
- `run_phase` keyword-only parameters are exactly the interfaces doc's, plus three with defaults: `stall_s=300`, `ratelimit_path=None`, `desc=""`, `claude_bin="claude"`. `claude_bin` exists because `subprocess.Popen(env=…)` does **not** use `env["PATH"]` to resolve the program on POSIX, so a test cannot redirect `claude` through `env` alone.
- Events emitted: `role_start` (`role`, `model`, `desc`), `tool` (`role`, `name`, `count`, `desc`) per tool call, `role_end` (`role`), `phase_end` (`phase`, `rc`, `dur`, `session_id`, `transcript`).
- `max_turns=0` omits `--max-turns`; `model=""` omits `--model`; `max_budget_usd=None` omits `--max-budget-usd`.
- The stub fixture protocol: `STUB_SCRIPT` names a directory; invocation *n* consumes `NNN.jsonl` (printed line by line, `STUB_DELAY` seconds apart), preceded by `NNN.sh` (a bash side-effect script run in the invocation's cwd so a scripted phase can write `sprint-<T>.json`, edit files, or drop a PAUSE/STOP sentinel mid-phase) and followed by `NNN.sleep` (seconds to hang *after* printing, so a phase can be killed with its usage already on the wire), and exits with `NNN.exit` if present. Every invocation prints a `system/init` line carrying `session_id` first, and appends `<idx> <resumed-sid|none>` to `STUB_LOG` when set.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_claude_proc.py`:

````python
import _path  # noqa: F401
import json
import os
import shlex
import stat
import tempfile
import time
import unittest

from runner import claude_proc
from runner.events import EventLog


def fake_claude(d, lines, exit_code=0, sleep_s=0):
    """A stand-in `claude` that prints canned stream-json lines."""
    path = os.path.join(d, "claude")
    body = ["#!/usr/bin/env bash", "cat >/dev/null 2>&1 || true"]
    if sleep_s:
        body.append("sleep %d" % sleep_s)
    for line in lines:
        body.append("printf '%%s\\n' %s" % shlex.quote(line))
    body.append("exit %d" % exit_code)
    with open(path, "w") as f:
        f.write("\n".join(body) + "\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


INIT = json.dumps({"type": "system", "subtype": "init", "session_id": "sess-1"})
ASSISTANT = json.dumps({
    "type": "assistant",
    "message": {"id": "msg_1", "model": "model-a",
                "usage": {"input_tokens": 10, "output_tokens": 4,
                          "cache_read_input_tokens": 100,
                          "cache_creation_input_tokens": 2},
                "content": [{"type": "text", "text": "working"},
                            {"type": "tool_use", "id": "tu_1", "name": "Edit",
                             "input": {"file_path": "src/a.ts"}}]}})
TOOL_RESULT = json.dumps({
    "type": "user",
    "message": {"content": [{"type": "tool_result", "tool_use_id": "tu_1"}]}})
RESULT = json.dumps({
    "type": "result", "subtype": "success", "is_error": False,
    "result": "done\n```json\n{\"status\": \"complete\"}\n```",
    "modelUsage": {"model-a": {"costUSD": 0.5, "inputTokens": 10, "outputTokens": 4,
                               "cacheReadInputTokens": 100,
                               "cacheCreationInputTokens": 2}}})


class Harnessed(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = tempfile.mkdtemp()
        self.events_path = os.path.join(self.d, "events.jsonl")
        self.events = EventLog(self.events_path, os.path.join(self.d, "eventseq"))
        self.activity = os.path.join(self.d, "last-activity")

    def emitted(self, type_):
        out = []
        with open(self.events_path) as f:
            for line in f:
                rec = json.loads(line)
                if rec["type"] == type_:
                    out.append(rec)
        return out

    def run_it(self, binary, **kw):
        kwargs = dict(phase="worker", model="model-a", prompt="do the thing",
                      cwd=self.cwd, timeout_s=30, max_turns=0, max_budget_usd=None,
                      env=dict(os.environ), events=self.events, tick=3, role="Worker",
                      activity_path=self.activity, claude_bin=binary)
        kwargs.update(kw)
        return claude_proc.run_phase(**kwargs)


class TestBuildArgv(unittest.TestCase):
    def test_the_base_command(self):
        argv = claude_proc.build_argv("claude", "", None, 0, None, None)
        self.assertEqual(["claude", "-p", "--output-format", "stream-json", "--verbose",
                          "--dangerously-skip-permissions"], argv)

    def test_a_model_is_passed_when_the_tier_resolves(self):
        self.assertIn("--model", claude_proc.build_argv("claude", "model-a", None, 0, None, None))

    def test_an_empty_model_inherits_the_users_default(self):
        self.assertNotIn("--model", claude_proc.build_argv("claude", "", None, 0, None, None))

    def test_max_turns_zero_omits_the_flag(self):
        self.assertNotIn("--max-turns", claude_proc.build_argv("claude", "", None, 0, None, None))
        self.assertIn("--max-turns", claude_proc.build_argv("claude", "", None, 8, None, None))

    def test_budget_and_resume_and_allowed_tools(self):
        argv = claude_proc.build_argv("claude", "m", "sess-9", 8, 6.0, ["Read", "Edit"])
        self.assertEqual("sess-9", argv[argv.index("--resume") + 1])
        self.assertEqual("6.0", argv[argv.index("--max-budget-usd") + 1])
        self.assertEqual("Read,Edit", argv[argv.index("--allowedTools") + 1])


class TestIsStalled(unittest.TestCase):
    def test_quiet_with_no_outstanding_tool_is_stalled(self):
        self.assertTrue(claude_proc.is_stalled(1000, 0, 300, 1400))

    def test_quiet_but_inside_a_tool_call_is_not_stalled(self):
        self.assertFalse(claude_proc.is_stalled(1000, 1, 300, 1400))

    def test_recent_activity_is_not_stalled(self):
        self.assertFalse(claude_proc.is_stalled(1000, 0, 300, 1100))


class TestTranscriptPath(unittest.TestCase):
    def test_none_without_a_session_id(self):
        self.assertIsNone(claude_proc.transcript_path("/tmp/wt", None))

    def test_none_when_the_file_does_not_exist(self):
        self.assertIsNone(claude_proc.transcript_path("/tmp/wt", "sess-1"))

    def test_the_encoded_cwd_is_used_when_the_file_exists(self):
        home = tempfile.mkdtemp()
        cwd = "/Users/x/proj"
        d = os.path.join(home, ".claude", "projects", "-Users-x-proj")
        os.makedirs(d)
        open(os.path.join(d, "sess-1.jsonl"), "w").close()
        old = os.environ.get("HOME")
        os.environ["HOME"] = home
        try:
            self.assertEqual(os.path.join(d, "sess-1.jsonl"),
                             claude_proc.transcript_path(cwd, "sess-1"))
        finally:
            if old is not None:
                os.environ["HOME"] = old


class TestHappyPhase(Harnessed):
    def setUp(self):
        Harnessed.setUp(self)
        self.binary = fake_claude(self.d, [INIT, ASSISTANT, TOOL_RESULT, RESULT])
        self.res = self.run_it(self.binary)

    def test_it_captures_rc_session_and_result_text(self):
        self.assertEqual(0, self.res.rc)
        self.assertFalse(self.res.killed)
        self.assertFalse(self.res.timed_out)
        self.assertEqual("sess-1", self.res.session_id)
        self.assertIn("status", self.res.result_text)

    def test_usage_comes_from_model_usage_on_the_result(self):
        self.assertEqual(["model-a"], list(self.res.usage_by_model))
        u = self.res.usage_by_model["model-a"]
        self.assertEqual(0.5, u.cost_usd)
        self.assertEqual(10, u.input_tokens)
        self.assertEqual(100, u.cache_read_tokens)
        self.assertEqual(2, u.cache_creation_tokens)

    def test_tool_calls_are_counted_and_narrated(self):
        self.assertEqual(1, self.res.tool_calls)
        tools = self.emitted("tool")
        self.assertEqual(1, len(tools))
        self.assertEqual("Edit", tools[0]["name"])
        self.assertEqual("Worker", tools[0]["role"])
        self.assertEqual("src/a.ts", tools[0]["desc"])

    def test_role_and_phase_events_bracket_the_run(self):
        self.assertEqual("model-a", self.emitted("role_start")[0]["model"])
        self.assertEqual("Worker", self.emitted("role_end")[0]["role"])
        end = self.emitted("phase_end")[0]
        self.assertEqual("worker", end["phase"])
        self.assertEqual(0, end["rc"])
        self.assertEqual("sess-1", end["session_id"])

    def test_activity_is_stamped_for_every_stream_line(self):
        self.assertTrue(os.path.exists(self.activity))
        with open(self.activity) as f:
            self.assertGreater(int(f.read().strip()), 0)


class TestUsageFallback(Harnessed):
    def test_per_message_usage_is_folded_when_model_usage_is_absent(self):
        result = json.dumps({"type": "result", "is_error": False, "result": "ok"})
        binary = fake_claude(self.d, [INIT, ASSISTANT, result])
        res = self.run_it(binary)
        self.assertEqual(10, res.usage_by_model["model-a"].input_tokens)
        self.assertEqual(100, res.usage_by_model["model-a"].cache_read_tokens)

    def test_the_largest_reading_per_message_id_wins(self):
        small = json.loads(ASSISTANT)
        big = json.loads(ASSISTANT)
        big["message"]["usage"]["output_tokens"] = 40
        result = json.dumps({"type": "result", "is_error": False, "result": "ok"})
        binary = fake_claude(self.d, [INIT, json.dumps(small), json.dumps(big), result])
        res = self.run_it(binary)
        self.assertEqual(40, res.usage_by_model["model-a"].output_tokens)

    def test_two_message_ids_accumulate(self):
        second = json.loads(ASSISTANT)
        second["message"]["id"] = "msg_2"
        result = json.dumps({"type": "result", "is_error": False, "result": "ok"})
        binary = fake_claude(self.d, [INIT, ASSISTANT, json.dumps(second), result])
        res = self.run_it(binary)
        self.assertEqual(20, res.usage_by_model["model-a"].input_tokens)


class TestFailurePaths(Harnessed):
    def test_a_timeout_kills_the_phase_and_still_reports_usage(self):
        binary = fake_claude(self.d, [INIT, ASSISTANT], sleep_s=0)
        # The script prints, then sleeps forever before exiting.
        with open(binary) as f:
            body = f.read()
        with open(binary, "w") as f:
            f.write(body.replace("exit 0", "sleep 60\nexit 0"))
        started = time.time()
        res = self.run_it(binary, timeout_s=1)
        self.assertTrue(res.timed_out)
        self.assertTrue(res.killed)
        self.assertLess(time.time() - started, 45)
        self.assertIn("model-a", res.usage_by_model)
        self.assertEqual(1, len(self.emitted("phase_end")))

    def test_an_api_error_result_is_recorded(self):
        result = json.dumps({"type": "result", "is_error": True,
                             "api_error_status": 429, "result": ""})
        binary = fake_claude(self.d, [INIT, result])
        res = self.run_it(binary)
        self.assertTrue(res.is_error)
        self.assertEqual("429", res.api_error_status)

    def test_a_rate_limit_event_is_captured_to_disk_and_to_the_result(self):
        rl = json.dumps({"type": "rate_limit_event",
                         "rate_limit_info": {"status": "rejected", "resetsAt": 42}})
        result = json.dumps({"type": "result", "is_error": False, "result": "ok"})
        path = os.path.join(self.d, "ratelimit.json")
        res = self.run_it(fake_claude(self.d, [INIT, rl, result]), ratelimit_path=path)
        self.assertEqual("rejected", res.rate_limit["status"])
        with open(path) as f:
            self.assertEqual(42, json.load(f)["resetsAt"])

    def test_a_non_json_line_is_ignored(self):
        result = json.dumps({"type": "result", "is_error": False, "result": "ok"})
        res = self.run_it(fake_claude(self.d, [INIT, "this is not json", result]))
        self.assertEqual(0, res.rc)
        self.assertEqual("ok", res.result_text)

    def test_a_nonzero_exit_is_carried_through(self):
        res = self.run_it(fake_claude(self.d, [INIT], exit_code=3))
        self.assertEqual(3, res.rc)
        self.assertEqual("", res.result_text)

    def test_a_missing_binary_returns_rc_127_rather_than_raising(self):
        res = self.run_it(os.path.join(self.d, "no-such-binary"))
        self.assertEqual(127, res.rc)
        self.assertEqual(1, len(self.emitted("phase_end")))


class TestStubFixture(unittest.TestCase):
    """The scripted stub the e2e drives; asserted here so a protocol change fails fast."""

    def setUp(self):
        self.stub = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "fixtures", "claude")
        self.script = tempfile.mkdtemp()
        self.cwd = tempfile.mkdtemp()

    def invoke(self, args=None, env_extra=None):
        import subprocess
        env = dict(os.environ)
        env["STUB_SCRIPT"] = self.script
        env.update(env_extra or {})
        proc = subprocess.run(["bash", self.stub] + (args or []), cwd=self.cwd,
                              env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, input=b"")
        return proc.returncode, proc.stdout.decode()

    def test_it_prints_an_init_line_then_the_numbered_transcript(self):
        with open(os.path.join(self.script, "001.jsonl"), "w") as f:
            f.write('{"type":"result","result":"first"}\n')
        rc, out = self.invoke()
        lines = [json.loads(ln) for ln in out.splitlines() if ln.strip()]
        self.assertEqual(0, rc)
        self.assertEqual("system", lines[0]["type"])
        self.assertTrue(lines[0]["session_id"])
        self.assertEqual("first", lines[1]["result"])

    def test_invocations_are_consumed_in_order(self):
        for n, text in (("001", "one"), ("002", "two")):
            with open(os.path.join(self.script, "%s.jsonl" % n), "w") as f:
                f.write('{"type":"result","result":"%s"}\n' % text)
        self.invoke()
        _, out = self.invoke()
        self.assertIn("two", out)

    def test_an_exit_file_sets_the_exit_code(self):
        with open(os.path.join(self.script, "001.exit"), "w") as f:
            f.write("7")
        rc, _ = self.invoke()
        self.assertEqual(7, rc)

    def test_a_side_effect_script_runs_in_the_invocation_cwd(self):
        with open(os.path.join(self.script, "001.sh"), "w") as f:
            f.write("echo written > side-effect.txt\n")
        with open(os.path.join(self.script, "001.jsonl"), "w") as f:
            f.write('{"type":"result","result":"ok"}\n')
        self.invoke()
        self.assertTrue(os.path.exists(os.path.join(self.cwd, "side-effect.txt")))

    def test_resume_is_recorded_to_the_stub_log(self):
        log = os.path.join(self.script, "log")
        self.invoke(["--resume", "sess-9"], {"STUB_LOG": log})
        with open(log) as f:
            self.assertIn("sess-9", f.read())

    def test_a_skill_invocation_consumes_no_transcript(self):
        with open(os.path.join(self.script, "001.jsonl"), "w") as f:
            f.write('{"type":"result","result":"one"}\n')
        rc, out = self.invoke(["--print", "/agent-loop-postmortem"])
        self.assertEqual(0, rc)
        self.assertEqual("", out.strip())
        _, out2 = self.invoke()
        self.assertIn("one", out2)


if __name__ == "__main__":
    unittest.main()
````

- [ ] **Step 2: Run test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_claude*.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'runner.claude_proc'`

- [ ] **Step 3a: Write the stub fixture**

Replace `plugins/agent-loop/tests/fixtures/claude` (keep mode 755):

```bash
#!/usr/bin/env bash
# Scripted stand-in for `claude`, driven by a directory of numbered responses.
#
#   STUB_SCRIPT=<dir>   invocation N consumes, in this order:
#                         <dir>/NNN.sh     bash side effects, run in this cwd, so a
#                                          scripted phase can write sprint-<T>.json,
#                                          worker-result.json, edit tracked files, or
#                                          drop a PAUSE/STOP sentinel mid-phase
#                         <dir>/NNN.jsonl  the stream-json transcript, printed line by line
#                         <dir>/NNN.sleep  seconds to hang AFTER printing, so a phase can
#                                          be killed by its timeout or by STOP with its
#                                          usage already on the wire
#                         <dir>/NNN.exit   the exit code (default 0)
#   STUB_DELAY=<secs>   sleep between transcript lines (stall/activity tests)
#   STUB_LOG=<file>     append "<NNN> <resumed-session-id|none>" per invocation
#
# Every invocation prints a system/init line carrying a session_id first, which is
# what the harness captures for --resume.
#
# A skill invocation (`/agent-loop-medic`, `/agent-loop-postmortem`) must NOT consume a
# numbered response, or every medic call would eat a phase.
set -uo pipefail

resume=""
prev=""
for arg in "$@"; do
  if [ "$prev" = "--resume" ]; then
    resume="$arg"
  fi
  case "$arg" in
    */agent-loop-medic*)
      if [ -n "${MOCK_MEDIC_SCRIPT:-}" ]; then
        bash "$MOCK_MEDIC_SCRIPT" "$@"
        exit "$?"
      fi
      exit 0
      ;;
    */agent-loop-postmortem*)
      exit 0
      ;;
  esac
  prev="$arg"
done

cat >/dev/null 2>&1 || true   # drain the prompt on stdin

dir="${STUB_SCRIPT:?STUB_SCRIPT unset}"
state="$dir/.n"
n=0
if [ -f "$state" ]; then
  n="$(cat "$state")"
fi
case "$n" in
  ''|*[!0-9]*) n=0 ;;
esac
n=$((n + 1))
printf '%s' "$n" > "$state"
idx="$(printf '%03d' "$n")"

if [ -n "${STUB_LOG:-}" ]; then
  printf '%s %s\n' "$idx" "${resume:-none}" >> "$STUB_LOG"
fi

if [ -f "$dir/$idx.sh" ]; then
  bash "$dir/$idx.sh"
fi

printf '{"type":"system","subtype":"init","session_id":"stub-%s","model":"stub"}\n' "$idx"

if [ -f "$dir/$idx.jsonl" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    if [ -n "$line" ]; then
      printf '%s\n' "$line"
      if [ -n "${STUB_DELAY:-}" ]; then
        sleep "$STUB_DELAY"
      fi
    fi
  done < "$dir/$idx.jsonl"
fi

if [ -f "$dir/$idx.sleep" ]; then
  sleep "$(cat "$dir/$idx.sleep")"
fi

rc=0
if [ -f "$dir/$idx.exit" ]; then
  rc="$(cat "$dir/$idx.exit")"
fi
exit "$rc"
```

- [ ] **Step 3b: Write `claude_proc.py`**

Create `plugins/agent-loop/runner/claude_proc.py`:

```python
"""One `claude -p` subprocess per phase, with its stream parsed in this process.

This is where v2's per-line `jq` forks and its FIFO go away. Everything the
harness needs about a phase — the session id (so a killed Worker is resumable),
the usage (so a killed phase still reports what it spent), the outstanding tool
calls (so "in a 20-minute test run" is not mistaken for "stalled"), and the
activity stamp — comes out of this one pass over stdout.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import util


@dataclass
class Usage:
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


@dataclass
class PhaseResult:
    phase: str
    model: str
    rc: Optional[int] = None
    killed: bool = False
    timed_out: bool = False
    session_id: Optional[str] = None
    transcript_path: Optional[str] = None
    started: int = 0
    ended: int = 0
    result_text: str = ""
    usage_by_model: Dict[str, Usage] = field(default_factory=dict)
    tool_calls: int = 0
    last_activity: int = 0
    is_error: bool = False
    api_error_status: str = ""
    rate_limit: Optional[Dict[str, Any]] = None
    stalled: bool = False


def build_argv(claude_bin: str, model: str, resume_session: Optional[str],
               max_turns: int, max_budget_usd: Optional[float],
               allowed_tools: Optional[List[str]]) -> List[str]:
    """The exact command line for one phase. Flags are omitted, never empty."""
    argv = [claude_bin, "-p", "--output-format", "stream-json", "--verbose",
            "--dangerously-skip-permissions"]
    if model:
        argv.extend(["--model", model])
    if resume_session:
        argv.extend(["--resume", resume_session])
    if max_turns:
        argv.extend(["--max-turns", str(int(max_turns))])
    if max_budget_usd is not None:
        argv.extend(["--max-budget-usd", str(max_budget_usd)])
    if allowed_tools:
        argv.extend(["--allowedTools", ",".join(allowed_tools)])
    return argv


def is_stalled(last_activity: float, outstanding: int, stall_s: int, now: float) -> bool:
    """A phase is stalled only when it is quiet AND not inside a tool call.

    A 20-minute `pnpm turbo test` emits nothing while it runs; v2's detector
    called that a stall seven times in one run and was wrong every time.
    """
    return outstanding == 0 and (now - last_activity) > stall_s


def transcript_path(cwd: str, session_id: Optional[str]) -> Optional[str]:
    """~/.claude/projects/<cwd with / replaced by ->/<session>.jsonl, if it exists."""
    if not session_id:
        return None
    encoded = os.path.abspath(cwd).replace("/", "-")
    path = os.path.join(os.path.expanduser("~"), ".claude", "projects",
                        encoded, "%s.jsonl" % session_id)
    return path if os.path.exists(path) else None


def _terminate(proc: subprocess.Popen) -> None:
    """SIGTERM the phase's process group, then SIGKILL what survives 30 s.

    A `claude` wedged under memory pressure can ignore SIGTERM entirely, which
    is how a "timed out" tick used to keep holding RAM.
    """
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        return
    for sig, grace in ((signal.SIGTERM, 30), (signal.SIGKILL, 10)):
        try:
            os.killpg(pgid, sig)
        except OSError:
            return
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


def _usage_from_message(u: Dict[str, Any]) -> Usage:
    return Usage(cost_usd=0.0,
                 input_tokens=int(u.get("input_tokens") or 0),
                 output_tokens=int(u.get("output_tokens") or 0),
                 cache_read_tokens=int(u.get("cache_read_input_tokens") or 0),
                 cache_creation_tokens=int(u.get("cache_creation_input_tokens") or 0))


def _usage_total(u: Usage) -> int:
    return u.input_tokens + u.output_tokens + u.cache_read_tokens + u.cache_creation_tokens


def _fold(per_message: Dict[str, Any]) -> Dict[str, Usage]:
    """Sum the kept-max-per-message-id readings into one Usage per model."""
    out: Dict[str, Usage] = {}
    for model, usage in per_message.values():
        acc = out.setdefault(model, Usage())
        acc.input_tokens += usage.input_tokens
        acc.output_tokens += usage.output_tokens
        acc.cache_read_tokens += usage.cache_read_tokens
        acc.cache_creation_tokens += usage.cache_creation_tokens
    return out


def _from_model_usage(raw: Dict[str, Any]) -> Dict[str, Usage]:
    out: Dict[str, Usage] = {}
    for model, u in (raw or {}).items():
        if not isinstance(u, dict):
            continue
        out[model] = Usage(cost_usd=float(u.get("costUSD") or 0),
                           input_tokens=int(u.get("inputTokens") or 0),
                           output_tokens=int(u.get("outputTokens") or 0),
                           cache_read_tokens=int(u.get("cacheReadInputTokens") or 0),
                           cache_creation_tokens=int(u.get("cacheCreationInputTokens") or 0))
    return out


def _tool_desc(inp: Dict[str, Any]) -> str:
    for key in ("description", "file_path", "path"):
        val = inp.get(key)
        if val:
            return str(val)[:120]
    cmd = inp.get("command")
    return str(cmd)[:60] if cmd else ""


def run_phase(*, phase: str, model: str, prompt: str, cwd: str, timeout_s: int,
              max_turns: int, max_budget_usd: Optional[float], env: Dict[str, str],
              events, tick: int, role: str, activity_path: str,
              resume_session: Optional[str] = None,
              allowed_tools: Optional[List[str]] = None,
              stall_s: int = 300, ratelimit_path: Optional[str] = None,
              desc: str = "", claude_bin: str = "claude") -> PhaseResult:
    """Run one phase to completion (or to its timeout) and return what happened.

    A PhaseResult is ALWAYS returned, with whatever usage was seen — that is the
    fix for the ~$61 of unattributed spend in the 2026-09-11 run, where a killed
    tick wrote no by_model at all.
    """
    argv = build_argv(claude_bin, model, resume_session, max_turns,
                      max_budget_usd, allowed_tools)
    started = int(time.time())
    result = PhaseResult(phase=phase, model=model, started=started, ended=started,
                         last_activity=started)
    events.emit("role_start", role=role, model=model or "default", desc=desc, tick=tick)

    try:
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                start_new_session=True, universal_newlines=True,
                                bufsize=1)
    except OSError:
        result.rc = 127
        result.ended = int(time.time())
        events.emit("role_end", role=role)
        events.emit("phase_end", phase=phase, rc=127, dur=0, session_id="", transcript="")
        return result

    state = {"last": float(started), "outstanding": set(), "timed_out": False}
    per_message: Dict[str, Any] = {}

    def feed_stdin():
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except (OSError, ValueError):
            pass

    writer = threading.Thread(target=feed_stdin, name="phase-stdin")
    writer.daemon = True
    writer.start()

    stop = threading.Event()
    deadline = started + timeout_s

    def watchdog():
        while not stop.wait(1.0):
            now = time.time()
            if is_stalled(state["last"], len(state["outstanding"]), stall_s, now):
                result.stalled = True
            if now >= deadline:
                state["timed_out"] = True
                _terminate(proc)
                return

    watch = threading.Thread(target=watchdog, name="phase-watchdog")
    watch.daemon = True
    watch.start()

    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            state["last"] = time.time()
            util.stamp_epoch(activity_path, state["last"])
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            kind = rec.get("type")
            if kind == "system" and rec.get("subtype") == "init":
                result.session_id = rec.get("session_id") or result.session_id
            elif kind == "assistant":
                msg = rec.get("message") or {}
                mid = msg.get("id") or ""
                mmodel = msg.get("model") or model or "unknown"
                usage = _usage_from_message(msg.get("usage") or {})
                prev = per_message.get(mid)
                if prev is None or _usage_total(usage) >= _usage_total(prev[1]):
                    per_message[mid] = (mmodel, usage)
                for block in msg.get("content") or []:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    result.tool_calls += 1
                    if block.get("id"):
                        state["outstanding"].add(block["id"])
                    events.emit("tool", role=role, name=block.get("name") or "tool",
                                count=result.tool_calls,
                                desc=_tool_desc(block.get("input") or {}))
            elif kind == "user":
                for block in (rec.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        state["outstanding"].discard(block.get("tool_use_id"))
            elif kind == "rate_limit_event":
                info = rec.get("rate_limit_info")
                if isinstance(info, dict):
                    result.rate_limit = info
                    if ratelimit_path:
                        util.write_json(ratelimit_path, info)
            elif kind == "result":
                result.result_text = rec.get("result") or ""
                result.is_error = bool(rec.get("is_error"))
                status = rec.get("api_error_status")
                result.api_error_status = "" if status is None else str(status)
                model_usage = _from_model_usage(rec.get("modelUsage") or {})
                if model_usage:
                    result.usage_by_model = model_usage
    finally:
        stop.set()
        rc = proc.wait()
        watch.join(timeout=5)

    if rc < 0:                                   # killed by a signal
        rc = 128 - rc
    result.rc = rc
    result.timed_out = bool(state["timed_out"])
    result.killed = result.timed_out or rc in (137, 143)
    result.ended = int(time.time())
    result.last_activity = int(state["last"])
    if not result.usage_by_model:
        result.usage_by_model = _fold(per_message)
    result.transcript_path = transcript_path(cwd, result.session_id)

    events.emit("role_end", role=role)
    events.emit("phase_end", phase=phase, rc=result.rc,
                dur=result.ended - result.started,
                session_id=result.session_id or "",
                transcript=result.transcript_path or "")
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_*.py' -v`
Expected: PASS — 255 tests, `OK`
Run: `bash scripts/lint.sh`
Expected: `shellcheck clean (N files)` — the rewritten fixture is on the lint list.
Check the mode survived the rewrite: `git ls-files -s plugins/agent-loop/tests/fixtures/claude` must print `100755`. If it prints `100644`, run `chmod 755 plugins/agent-loop/tests/fixtures/claude`.

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/runner/claude_proc.py plugins/agent-loop/tests/fixtures/claude \
        plugins/agent-loop/tests/runner/test_claude_proc.py
git commit -F - <<'EOF'
agent-loop: one claude -p subprocess per phase, stream parsed in process

No FIFO and no jq fork per line. The parser captures the session id (so a
killed Worker is resumable), accumulates usage per message id and prefers the
result's modelUsage, tracks outstanding tool_use ids so a long test run reads
as "in tool" rather than stalled, and stamps last-activity atomically. A
PhaseResult with whatever usage was seen is returned even on a kill, which is
the fix for spend that used to vanish with a timed-out tick.

The tests/fixtures/claude stub is rewritten to the STUB_SCRIPT directory
protocol: numbered transcripts, an optional pre-sleep, an optional side-effect
script, and an exit code per invocation.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 13: the role prompts, `render_prompt` and `parse_json_block`

**Files:**
- Create: `plugins/agent-loop/runner/prompts/scout.md`, `worker.md`, `evaluator.md`, `learner.md`, `planner.md`, `reviewer.md`
- Create: `plugins/agent-loop/runner/phases.py` (this task adds `TickContext`, `render_prompt`, `parse_json_block`, `JsonBlockError`, `_merge_usage` only)
- Test: `plugins/agent-loop/tests/runner/test_prompts.py`

**Interfaces:**
- Consumes: `config.LoopConfig`, `plan.Plan`, `plan.Task`, `claude_proc.Usage`.
- Produces: `TickContext` (fields `cfg, plan, task, loop_dir, runtime_dir, events, tick, attempt` — plan B relies on exactly these, plan C adds `resume_session`, `resume_count`, `last_session`), `render_prompt(name, **vars) -> str`, `parse_json_block(text) -> dict`, `JsonBlockError`, `_merge_usage(a, b) -> Dict[str, Usage]` (plan B's re-ask path merges both attempts' usage so no spend is dropped).
- Placeholders beyond the interfaces doc's list, added here: `{{tdd_note}}` and `{{evaluator_findings}}` (worker), `{{verdict}}` (learner; plan C reuses it in `judge.md`), `{{segment}}` (planner, reviewer). Plan D's `tests/prompts.contract.sh` must allow them.
- **No prompt talks about tiers, attempts or escalation.** A re-dispatch is the same role brief with the Evaluator's findings filled into `{{evaluator_findings}}`; which model runs it is a command-line fact the role never sees.
- `render_prompt` raises `KeyError` if any `{{placeholder}}` is left unfilled — a silently unsubstituted token is a prompt that lies to the model.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_prompts.py`:

````python
import _path  # noqa: F401
import os
import re
import unittest

from runner import phases
from runner.claude_proc import Usage

PROMPT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "runner", "prompts")
NAMES = ("scout", "worker", "evaluator", "learner", "planner", "reviewer")


def body(name):
    with open(os.path.join(PROMPT_DIR, "%s.md" % name)) as f:
        return f.read()


class TestEveryPromptExists(unittest.TestCase):
    def test_all_six_role_briefs_are_present_and_non_trivial(self):
        for name in NAMES:
            self.assertGreater(len(body(name)), 400, name)


class TestSentinelFree(unittest.TestCase):
    def test_no_prompt_mentions_a_loop_sentinel(self):
        for name in NAMES:
            for token in ("<<LOOP_DONE>>", "<<LOOP_CONTINUE>>", "<<LOOP_HALT", "sentinel"):
                self.assertNotIn(token, body(name), "%s mentions %s" % (name, token))

    def test_no_prompt_names_a_model_or_asks_a_role_to_choose_a_tier(self):
        for name in NAMES:
            text = body(name).lower()
            for token in ("haiku", "sonnet", "opus", "cheapest capable",
                          "choose its tier", "resolve the tier"):
                self.assertNotIn(token, text, "%s mentions %s" % (name, token))

    def test_no_prompt_describes_an_orchestrator_spine(self):
        for name in NAMES:
            self.assertNotIn("orchestrator", body(name).lower(), name)

    def test_no_prompt_mentions_attempts_escalation_or_a_ladder(self):
        """Which model runs a re-attempt is a command-line fact decided by the
        harness (plan A) or the Judge (plan C). A role that reads "attempt 2
        escalates" starts reasoning about its own replacement."""
        for name in NAMES:
            text = body(name).lower()
            for token in ("attempt 2", "next tier", "tier up", "escalat", "a stronger model"):
                self.assertNotIn(token, text, "%s mentions %s" % (name, token))


class TestNonInteractivity(unittest.TestCase):
    def test_every_prompt_forbids_the_blocking_tools(self):
        for name in NAMES:
            text = body(name)
            self.assertIn("AskUserQuestion", text, name)
            self.assertIn("EnterPlanMode", text, name)


class TestJsonContract(unittest.TestCase):
    def test_every_prompt_states_a_fenced_json_block_is_required(self):
        for name in NAMES:
            self.assertIn("```json", body(name), name)

    def test_the_declared_keys_match_what_the_harness_parses(self):
        expected = {
            "scout": ("contract_path", "notes"),
            "worker": ("status", "summary"),
            "evaluator": ("verdict", "findings", "views", "summary"),
            "learner": ("patterns", "log", "invariants"),
            "planner": ("tasks_added",),
            "reviewer": ("findings",),
        }
        for name, keys in expected.items():
            text = body(name)
            for key in keys:
                self.assertIn('"%s"' % key, text, "%s is missing %s" % (name, key))


class TestDurableRulesSurvived(unittest.TestCase):
    def test_scout_carries_the_clone_contract_and_inlining_rules(self):
        text = body("scout")
        self.assertIn("clone_of", text)
        self.assertIn("substitutions", text)
        self.assertIn("see file", text)          # the anti-reference rule names it
        self.assertIn("relevant_learnings", text)
        self.assertIn("Invariants", text)
        self.assertIn("(read)", text)

    def test_worker_carries_the_containment_and_edit_mechanics_rules(self):
        text = body("worker")
        self.assertIn("worker-result.json", text)
        self.assertIn("git commit", text)
        self.assertIn("LOOP_PLAN.md", text)
        self.assertIn("replace_all", text)       # batch mechanical edits
        self.assertIn("re-grep", text)           # write-through on a reverting hook
        self.assertIn("allow_list", text)

    def test_evaluator_carries_the_workaround_catalogue(self):
        text = body("evaluator")
        for token in ("BLOCKER", "NEEDS_WORK", "PASS", "workaround", "stub", "loosen"):
            self.assertIn(token, text)

    def test_learner_carries_the_evidence_rule_and_the_digest_cap(self):
        text = body("learner")
        self.assertIn("2 KB", text)
        self.assertIn("gate-", text)
        self.assertIn("Patterns", text)

    def test_planner_carries_the_one_line_row_budget(self):
        text = body("planner")
        self.assertIn("ONE line", text)
        self.assertIn("depends_on", text)
        self.assertIn("mechanical", text)
        self.assertIn("complex", text)
        self.assertIn("no-ui", text)

    def test_reviewer_keeps_follow_ups_in_the_reviewed_segment(self):
        text = body("reviewer")
        self.assertIn("must-fix", text)
        self.assertIn("follow_up_row", text)
        self.assertIn("never a later", text)

    def test_the_filesystem_is_the_only_durable_state_rule_is_everywhere(self):
        for name in NAMES:
            self.assertIn("filesystem", body(name).lower(), name)


class TestRenderPrompt(unittest.TestCase):
    def test_every_placeholder_in_every_prompt_is_documented(self):
        known = {"loop_dir", "worktree", "task_row", "contract_json", "must_read_blocks",
                 "screenshots", "gate_outputs", "diff", "learnings_digest", "knowledge",
                 "spec_excerpt", "checkpoint", "minutes_left", "validation_errors",
                 "tdd_note", "evaluator_findings", "verdict", "segment"}
        for name in NAMES:
            for token in re.findall(r"\{\{(\w+)\}\}", body(name)):
                self.assertIn(token, known, "%s uses undocumented {{%s}}" % (name, token))

    def test_substitution_replaces_every_occurrence(self):
        out = phases.render_prompt("worker", loop_dir="/l", worktree="/w",
                                   contract_json="{}", tdd_note="",
                                   evaluator_findings="(first attempt)")
        self.assertNotIn("{{", out)
        self.assertIn("/l", out)

    def test_a_missing_placeholder_raises_rather_than_shipping_a_literal(self):
        with self.assertRaises(KeyError):
            phases.render_prompt("worker", loop_dir="/l")

    def test_an_unknown_prompt_name_raises(self):
        with self.assertRaises(KeyError):
            phases.render_prompt("nope")


class TestParseJsonBlock(unittest.TestCase):
    def test_it_extracts_a_fenced_block(self):
        self.assertEqual({"a": 1},
                         phases.parse_json_block('chatter\n```json\n{"a": 1}\n```\n'))

    def test_the_last_block_wins(self):
        text = '```json\n{"a": 1}\n```\nthen\n```json\n{"a": 2}\n```'
        self.assertEqual({"a": 2}, phases.parse_json_block(text))

    def test_an_unlabelled_fence_is_accepted_as_a_fallback(self):
        self.assertEqual({"a": 1}, phases.parse_json_block('```\n{"a": 1}\n```'))

    def test_a_bare_object_is_accepted_when_there_is_no_fence(self):
        self.assertEqual({"a": 1}, phases.parse_json_block('here you go: {"a": 1}'))

    def test_malformed_json_raises_with_the_parser_error(self):
        with self.assertRaises(phases.JsonBlockError) as ctx:
            phases.parse_json_block('```json\n{"a": ,}\n```')
        self.assertIn("line", str(ctx.exception).lower())

    def test_no_block_at_all_raises(self):
        with self.assertRaises(phases.JsonBlockError):
            phases.parse_json_block("I did the thing.")

    def test_a_json_array_is_rejected(self):
        with self.assertRaises(phases.JsonBlockError):
            phases.parse_json_block('```json\n[1, 2]\n```')


class TestMergeUsage(unittest.TestCase):
    def test_both_attempts_are_summed_per_model(self):
        a = {"m": Usage(cost_usd=1.0, input_tokens=10, output_tokens=1,
                        cache_read_tokens=5, cache_creation_tokens=1)}
        b = {"m": Usage(cost_usd=2.0, input_tokens=20, output_tokens=2,
                        cache_read_tokens=6, cache_creation_tokens=0),
             "n": Usage(cost_usd=0.5)}
        out = phases._merge_usage(a, b)
        self.assertEqual(3.0, out["m"].cost_usd)
        self.assertEqual(30, out["m"].input_tokens)
        self.assertEqual(11, out["m"].cache_read_tokens)
        self.assertEqual(0.5, out["n"].cost_usd)

    def test_merging_does_not_mutate_the_inputs(self):
        a = {"m": Usage(cost_usd=1.0)}
        phases._merge_usage(a, {"m": Usage(cost_usd=2.0)})
        self.assertEqual(1.0, a["m"].cost_usd)


if __name__ == "__main__":
    unittest.main()
````

- [ ] **Step 2: Run test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_prompts*.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'runner.phases'`

- [ ] **Step 3a: Write `runner/prompts/scout.md`**

````markdown
# Role: Scout

You investigate ONE task and write ONE file: its sprint contract. You do not
modify the tree — not one line of source. Read, then write the contract.

Worktree: `{{worktree}}` · loop dir: `{{loop_dir}}`

## The task

```
{{task_row}}
```

## Spec excerpt

{{spec_excerpt}}

## What this run has already learned

{{learnings_digest}}

## Durable knowledge from earlier runs in this repo

{{knowledge}}

## Validation errors from your previous attempt

{{validation_errors}}

(If that section is not empty, your last contract was REJECTED by the harness.
Fix exactly those errors. Do not start over and do not argue with them.)

## Write `{{loop_dir}}/runtime/sprint-<TASK>.json`

The filesystem is the only durable state in this loop. The Worker and the
Evaluator see this file and nothing else — not the plan, not the spec, not your
reasoning. Anything you leave out is lost.

- `task` — the task id, exactly as it appears in the row.
- `success_criteria` — what "done" means, concretely and verifiably. Every repo
  path you name here MUST be inside `allow_list`, or be marked read-only by
  writing it as `path/to/file.ts (read)`. The harness rejects a criterion that
  names a path the Worker may not touch.
- `allow_list` — the exact set of files the Worker may edit. Be tight. A path
  not in this list is reverted by the harness after the Worker returns.
- `forbidden` — `{"path": …, "source": "plan" | "spec" | "scout"}`. `plan` and
  `spec` mean a document forbids it; `scout` means you judged it out of scope.
  A `scout`-sourced entry can be widened later; a `plan`/`spec` one cannot. Say
  which it is — a constraint with no provenance cannot be reasoned about.
- `verification` — the exact commands the harness will re-run. It never trusts
  the Worker's self-report; only these commands decide whether the code works.
  Also scan the `## Invariants` sections above and **append the literal `check`
  command of every invariant relevant to this task's files**, scoped to those
  files (e.g. `! grep -rEn "<violation pattern>" <changed files>`). That is how
  a convention becomes enforced instead of merely advised.
- `estimated_diff_lines` — a rough size, used as a sanity check.
- `scout_notes` — INLINE the exact APIs, imports and intended diff. Never write
  "see file X": if the next process is truncated, everything it needs must
  already be in this file.
- `relevant_learnings` — inline, verbatim, only the entries from the two
  sections above that apply to THIS task. Pull nothing else; this per-task
  relevance filter is what keeps the contract small.
- `evaluator_must_read` — reference files the Evaluator must have in front of it
  to judge the result. Naming them here is what makes the Evaluator read them.
- `evaluator_must_view` — screenshot names from `render_gate` the Evaluator must
  describe. Leave `[]` when there is no render gate.

**A clone or copy task (`| clone_of:` / `| copy_of:`) gets a LEAN contract.**
Do not inline the sibling's source — one run paid 34 KB of duplicated component
and test as cache-creation on every dispatch. Instead: name the sibling's file
paths so the Worker reads them directly, and list `substitutions` as explicit
`{from, to}` pairs covering every per-clone difference (type identifier, ticket
reference, display name, …). Anything not listed must be copied byte for byte.
Add success criteria that assert each substitution landed AND that no
source-only string survived, with the proving grep in `verification`.

## Non-interactivity (HARD)

You are headless. NEVER call `AskUserQuestion`, NEVER call `EnterPlanMode`, and
never invoke any tool that waits on a person — it will hang this process
forever. If the task genuinely cannot be scoped, say so in `notes` and write the
best contract you can; the harness decides what happens next.

## Output

End your reply with exactly one fenced JSON block:

```json
{"contract_path": "{{loop_dir}}/runtime/sprint-T<n>.json", "notes": "one or two sentences on what you found and anything that surprised you"}
```
````

- [ ] **Step 3b: Write `runner/prompts/worker.md`**

````markdown
# Role: Worker

You implement ONE task, defined entirely by the contract below. Your world is
this contract: do not read `{{loop_dir}}/LOOP_PLAN.md` or any other loop
artefact — they carry stale and out-of-scope state.

Worktree: `{{worktree}}`

## Contract

```json
{{contract_json}}
```

{{tdd_note}}

## What the last attempt got wrong

{{evaluator_findings}}

(If that section says this is the first attempt, ignore it. Otherwise an earlier
Worker already tried this task and the Evaluator rejected the result for the
reasons above — the tree still holds that work. Fix exactly those findings;
do not start over, and do not argue with them.)

## Scope boundary (HARD)

Your entire job is: edit the files in `allow_list` so the contract's
`success_criteria` hold, and keep `{{loop_dir}}/runtime/worker-result.json`
current. Nothing else.

- NEVER run `git commit` or `git add`. NEVER edit `{{loop_dir}}/LOOP_PLAN.md` —
  no checkbox marking. NEVER touch `{{loop_dir}}/LOOP_LEARNINGS.md`.
- NEVER run the verification pipeline as your own gate, and never treat a green
  run of your own as permission to stop early.
- NEVER start the next task.
- Touch nothing outside `allow_list`. The harness compares the working tree to
  it the moment you return and reverts every stray, so a file you edit outside
  the list is wasted work, not a shortcut.

Verification, judgement and the commit happen after you return, and they are not
yours. A Worker that commits its own work converts a reviewed change into an
unreviewed one.

## Checkpoint first (HARD)

The filesystem is the only durable state in this loop. Before you begin deep
work, write `{{loop_dir}}/runtime/worker-result.json`:

```json
{"task": "T<n>", "status": "partial", "files_touched": [], "summary": "starting", "checkpoint": "what I am about to do", "next_steps": ["…"]}
```

Update it as you go. If this process is killed mid-task, that file is all that
survives — truncation must never mean lost signal.

## Edit mechanics

- **Batch mechanical edits.** For a multi-file rewrite (an import path, a
  renamed symbol), use one codemod, `sed`/`perl -i`, or `Edit` with
  `replace_all` across files — not one `Edit` per file. Where a PostToolUse
  formatter runs, per-file edits each trigger a format + re-read round trip, so
  N files cost ~N round trips. Batching is the single biggest lever on a
  mechanical task's cost.
- **Write through a reverting hook.** Some repos run a formatter that reverts a
  string-only `Edit` after it lands. After every `Edit`, **re-grep the file for
  the new string.** If it is gone, the hook reverted it: fall back to a
  whole-file `Write` (or `sed -i ''`), then re-run the repo formatter in its
  write mode so the file ends both correct and formatted. Never leave a silently
  reverted edit as done.

## Non-interactivity (HARD)

You are headless. NEVER call `AskUserQuestion`, NEVER call `EnterPlanMode`, and
never invoke any tool that waits on a person. If the contract is
self-contradictory or you are genuinely stuck, record that in `checkpoint` and
`next_steps`, set `"status": "partial"`, and return — the harness decides.

## Output

Keep `worker-result.json` current, then end your reply with exactly one fenced
JSON block:

```json
{"status": "complete", "summary": "what you changed and why it satisfies the criteria"}
```

Use `"partial"` when any success criterion is not yet met.
````

- [ ] **Step 3c: Write `runner/prompts/evaluator.md`**

````markdown
# Role: Evaluator

You grade one finished task against its contract. You judge; you do not fix.
Change no file — the filesystem state you are handed is the evidence.

## The task

```
{{task_row}}
```

## Contract

```json
{{contract_json}}
```

## The diff under review

```diff
{{diff}}
```

## Gate output (the harness already ran these)

```
{{gate_outputs}}
```

## Reference material you were asked to read

{{must_read_blocks}}

## Screenshots you must look at

{{screenshots}}

Use the `Read` tool on each screenshot path listed above and describe what you
actually see. Your verdict must carry one entry in `views` per screenshot. A
verdict that skips one is rejected by the harness.

## The three verdicts

- **PASS** — every success criterion is genuinely met by the diff, and the gate
  is green. Both must hold.
- **NEEDS_WORK** — the approach is right but the result is incomplete or wrong
  in a way another attempt can fix. Say precisely what is missing.
- **BLOCKER** — the only way this diff "passes" is a **workaround**: a silenced
  or weakened test, behaviour the spec requires left stubbed, a loosened or
  widened type, faked or hardcoded data, a criterion satisfied by a comment
  rather than by code, or any other divergence from what was asked. A green
  pipeline plus a plausible diff can still be a hidden defect, and catching that
  is the entire reason you exist.

Judge the artefact, never the annotation. If a criterion says a file was copied
from a reference, compare the two files' actual content — a `TODO(copied from …)`
comment is not a copy. If a criterion names a behaviour, find the code that
implements it.

## Non-interactivity (HARD)

You are headless. NEVER call `AskUserQuestion`, NEVER call `EnterPlanMode`, and
never invoke any tool that waits on a person.

## Output

End your reply with exactly one fenced JSON block:

```json
{"verdict": "PASS", "findings": [{"criterion": "the criterion, quoted", "met": true, "evidence": "the file and line, or the gate output, that proves it"}], "views": [{"name": "screenshot name", "observation": "what you saw"}], "summary": "one paragraph"}
```
````

- [ ] **Step 3d: Write `runner/prompts/learner.md`**

````markdown
# Role: Learner

You turn one finished task into knowledge a later task can use. Cheap, short,
evidence-backed. You write no code.

## The task

```
{{task_row}}
```

## What the Evaluator concluded

{{verdict}}

## Gate output from this task

```
{{gate_outputs}}
```

## The current Patterns digest

{{learnings_digest}}

## Rules

The filesystem is the only durable state in this loop: a lesson you do not write
down did not happen.

- **Evidence or it goes in the log.** A claim about what the gate did must cite
  the evidence file it came from — `gate-<task>-<n>.txt`. Anything you cannot
  tie to evidence in front of you belongs in `log`, never in `patterns`. One run
  promoted "the layouts were genuinely copied" into its patterns on a model's
  say-so and every later task inherited the lie.
- **`patterns` are reusable rules**, not a diary: "module X needs flag Y before
  Z", "the formatter reverts string-only edits in this repo". Task-specific
  detail is `log`.
- **The digest is capped at 2 KB** (roughly 20 short rules). If the digest above
  is already at the cap, or your addition would push it past, consolidate in the
  same breath: merge duplicates, drop stale or task-specific entries, keep only
  high-signal reusable rules. The harness enforces the cap by truncating oldest
  first, so pruning badly loses your own new rule.
- **An `invariant` is a rule with a machine-checkable command.** Give the literal
  shell check, generic enough to scope to any task's own files — a Scout will
  copy it into a later contract's `verification`, where a violation becomes a
  hard gate failure rather than advice.

## Non-interactivity (HARD)

You are headless. NEVER call `AskUserQuestion`, NEVER call `EnterPlanMode`, and
never invoke any tool that waits on a person.

## Output

End your reply with exactly one fenced JSON block:

```json
{"patterns": [{"rule": "short reusable rule", "evidence": "gate-T12-1.txt, or the file and line"}], "log": "the verbose note, with context", "invariants": [{"rule": "what must always hold", "check": "! grep -rEn \"<pattern>\" <files>"}]}
```
````

- [ ] **Step 3e: Write `runner/prompts/planner.md`**

````markdown
# Role: Planner

You write the task rows for ONE segment of `{{loop_dir}}/LOOP_PLAN.md`, and
nothing else. No code, no other segment, no prose in the plan.

Worktree: `{{worktree}}`

## The segment to plan

```
{{segment}}
```

## Spec excerpt

{{spec_excerpt}}

## What this run has learned so far

{{learnings_digest}}

## Row grammar (HARD, ONE line per task)

```
- [ ] T<n>: <one-sentence imperative> | depends_on: T<a>,T<b> | <class?> | copy_of: T<m>? | no-ui?
```

- Ids continue the plan's existing numbering. Never renumber an existing row.
- The description is **one sentence**. Verbose detail belongs in the sprint
  contract a Scout writes per task — never inline in the plan. The plan is read
  by the harness on every tick, so every character you add is paid for
  repeatedly; a row over ~140 characters is a row carrying recon it shouldn't.
- `depends_on` lists only real ordering constraints. An over-constrained plan
  starves the loop of eligible work; an under-constrained one breaks the build.
- **Class flag** — this decides how the finished task is graded:
  - `| mechanical` — ONLY when success is fully provable by the gate plus a grep
    that the success criteria will name. No judgement involved. Skips the
    per-task Evaluator.
  - `| complex` — genuinely high subjective risk: architecture, tricky logic,
    security or wide blast radius.
  - no flag — ordinary work. This is the default; `complex` is opt-in and
    `mechanical` is a claim you must be able to defend.
- `| copy_of: T<m>` when the task reproduces an earlier task's artefact; the
  harness will require a similarity check, so a six-line "copy" cannot pass.
- `| no-ui` only when the task provably renders nothing a person can look at.
  Without it, a task touching UI paths must declare a render gate.

## Where the rows go

Append them under this segment's `## ` heading, after any existing rows.
**Never write a row under a later segment's heading.** A segment with zero rows
is how the harness knows it is still unplanned; dropping one row under a future
heading suppresses that segment's own planning tick and its real tasks are never
written.

The filesystem is the only durable state in this loop: edit the file, do not
describe the edit.

## Non-interactivity (HARD)

You are headless. NEVER call `AskUserQuestion`, NEVER call `EnterPlanMode`, and
never invoke any tool that waits on a person. If the spec is silent on something
you need, write the smallest reasonable set of rows and say so in your reply.

## Output

End your reply with exactly one fenced JSON block:

```json
{"tasks_added": 7}
```
````

- [ ] **Step 3f: Write `runner/prompts/reviewer.md`**

````markdown
# Role: Reviewer

You grade one finished segment as a whole — the cross-task problems a per-task
Evaluator cannot see. You change no code: the diff stays attributable to the
gated task commits that produced it.

## The segment

```
{{segment}}
```

## Its tasks

```
{{task_row}}
```

## Spec excerpt

{{spec_excerpt}}

## The cumulative segment diff

```diff
{{diff}}
```

## What to look for

Scope creep, architectural drift, logic duplicated across tasks that should have
been shared, patterns applied inconsistently between tasks, and any workaround
that slipped through a `mechanical` task's deterministic-only gate.

Classify every finding:

- `nit` — recorded and otherwise ignored.
- `should-fix` / `must-fix` — each one gets a follow-up row, written into **this
  segment**, never a later or not-yet-planned one. A row under a future heading
  makes that segment look planned and suppresses its own planning tick.
- A finding that needs a **human decision** is not a follow-up row: say so in the
  detail and leave `follow_up_row` empty. The harness routes it.

**No verify-only follow-ups.** If the finding is "convention X should hold" and X
is already an invariant the gate checks, do not add a row to re-check it — the
gate proved it per task. Six tasks in one run existed only to re-assert a
convention that was already machine-checked.

The filesystem is the only durable state in this loop; the harness writes your
follow-up rows and the `Reviewed:` stamp from the JSON below, so the rows must
be complete and well-formed.

## Non-interactivity (HARD)

You are headless. NEVER call `AskUserQuestion`, NEVER call `EnterPlanMode`, and
never invoke any tool that waits on a person.

## Output

End your reply with exactly one fenced JSON block:

```json
{"findings": [{"severity": "must-fix", "title": "short title", "detail": "what is wrong and why it matters", "follow_up_row": "- [ ] T<n>: <one-sentence imperative> | depends_on: T<a>"}]}
```
````

- [ ] **Step 3g: Write the first half of `runner/phases.py`**

Create `plugins/agent-loop/runner/phases.py`:

````python
"""One function per LLM phase: build the prompt, run it, validate what came back.

Every phase is a `claude -p` subprocess with a small role brief plus exactly the
context it needs. No phase is handed the plan, and no phase decides what happens
next — it returns JSON and the harness decides.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .claude_proc import Usage

PROMPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")
_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class JsonBlockError(ValueError):
    """The phase's reply carried no usable JSON document."""


@dataclass
class TickContext:
    """Everything a phase function needs about the tick it belongs to."""
    cfg: Any
    plan: Any
    task: Any
    loop_dir: str
    runtime_dir: str
    events: Any
    tick: int
    attempt: int = 1


def render_prompt(name: str, **variables: Any) -> str:
    """Fill `{{placeholders}}` in `prompts/<name>.md`.

    An unfilled placeholder raises: shipping the literal `{{diff}}` to a model is
    a prompt that lies about what it was given.
    """
    path = os.path.join(PROMPT_DIR, "%s.md" % name)
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        raise KeyError("no prompt named %r at %s" % (name, path))
    out = text
    for key, value in variables.items():
        out = out.replace("{{%s}}" % key, "" if value is None else str(value))
    missing = _PLACEHOLDER_RE.findall(out)
    if missing:
        raise KeyError("prompt %r left %s unfilled" % (name, ", ".join(sorted(set(missing)))))
    return out


def parse_json_block(text: str) -> Dict[str, Any]:
    """The last fenced JSON object in a phase's reply.

    Falls back to the last bare `{…}` so a model that forgot the fence but
    produced the document still counts; anything else is `malformed-output` and
    the caller re-asks once.
    """
    candidates = [m.strip() for m in _FENCE_RE.findall(text or "")]
    if not candidates:
        start = (text or "").rfind("{")
        end = (text or "").rfind("}")
        if start >= 0 and end > start:
            candidates = [text[start:end + 1]]
    if not candidates:
        raise JsonBlockError("no JSON block in the reply")
    last_error = None
    for raw in reversed(candidates):
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            last_error = exc
            continue
        if isinstance(parsed, dict):
            return parsed
        last_error = ValueError("expected a JSON object, got %s" % type(parsed).__name__)
    raise JsonBlockError("could not parse the JSON block: %s" % last_error)


def _merge_usage(a: Dict[str, Usage], b: Dict[str, Usage]) -> Dict[str, Usage]:
    """Sum two phases' usage per model without mutating either input.

    A re-ask is a second subprocess; dropping its usage is exactly how spend
    goes missing from the ledger.
    """
    out: Dict[str, Usage] = {}
    for source in (a or {}, b or {}):
        for model, usage in source.items():
            acc = out.setdefault(model, Usage())
            acc.cost_usd += usage.cost_usd
            acc.input_tokens += usage.input_tokens
            acc.output_tokens += usage.output_tokens
            acc.cache_read_tokens += usage.cache_read_tokens
            acc.cache_creation_tokens += usage.cache_creation_tokens
    return out
````

- [ ] **Step 4: Run test to verify it passes**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_*.py' -v`
Expected: PASS — 283 tests, `OK`

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/runner/prompts plugins/agent-loop/runner/phases.py \
        plugins/agent-loop/tests/runner/test_prompts.py
git commit -F - <<'EOF'
agent-loop: six role briefs replace the 300-line orchestrator prompt

Each role gets only its own contract, states its JSON output shape verbatim,
and is told it is headless. The durable rules from tick-prompt survive where
they belong: inlining and the lean clone contract in the Scout, containment,
batched mechanical edits and write-through-on-a-reverting-hook in the Worker,
the workaround catalogue in the Evaluator, the evidence rule and the 2 KB
digest cap in the Learner, the one-line row budget in the Planner, the
same-segment follow-up rule in the Reviewer. Sentinels, the spine and
self-chosen tiers are gone.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 14: `phases.py` — the six role functions, the tier seam and learnings

**Files:**
- Modify: `plugins/agent-loop/runner/phases.py` (append to what Task 13 created)
- Test: `plugins/agent-loop/tests/runner/test_phases.py`

**Interfaces:**
- Consumes: `claude_proc.run_phase` (called as `claude_proc.run_phase(...)` through the module, which is the seam tests and plan C patch), `config.model_for`, `config.TIER_ORDER`, `config.phase_limit`, `contract.load_contract`, `contract.validate`, `util.read_text`, `util.atomic_write`. Deliberately **not** `config.next_tier`: no phase function escalates anything.
- Produces: `worker_tier(cfg, tier=None) -> str`, `evaluator_tier(cfg, task) -> str`, `run_scout(ctx)`, `run_worker(ctx, contract, resume_session=None, wrapup=False, tier=None, findings="")`, `run_evaluator(ctx, contract, diff_text, gate_outputs, screenshots)`, `run_learner(ctx, contract, gate_outputs, verdict)`, `run_planner(ctx, segment)`, `run_reviewer(ctx, segment, diff_text)`, `apply_learnings(loop_dir, data)`, `learnings_digest(loop_dir)`, `knowledge_text(loop_dir)`, `spec_excerpt(cfg, task)`.
- **`worker_tier` is a lookup, not a ladder.** It returns the configured Worker tier and ignores the attempt number entirely; an explicit `tier` argument overrides it. Plan A never passes one, so every re-dispatch runs at the same tier as the first. Plan C passes the Judge's `changes.tier`. This is spec §11 item 4: the harness enforces `max_attempts` and `worker_resume_max`, never a tier progression.
- `run_worker`'s `findings` is the Evaluator's rejection text for the previous attempt, filled into `{{evaluator_findings}}`. `tier` and `findings` are additive keyword arguments; the four positional/keyword names the interfaces doc fixes are unchanged.
- Every phase that expects JSON gets exactly **one** re-ask with the parse error appended. The returned `PhaseResult` carries both attempts' usage merged. A killed phase is never re-asked.
- `run_worker(wrapup=True)` and `run_worker(resume_session=…)` raise `NotImplementedError` here: the parameters exist because the interfaces doc fixes the signature, and plan C fills them in. That is the seam, and it is asserted by a test so it cannot rot silently.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_phases.py`:

````python
import _path  # noqa: F401
import json
import os
import tempfile
import unittest

from runner import claude_proc, config, contract as cmod, phases, plan as planmod, util
from runner.claude_proc import PhaseResult, Usage
from runner.events import EventLog

CONFIG = """Worktree: %s
Verification pipeline: lint test
Tiers: cheap=tiny standard=mid most-capable=big
Spec: %s
"""

PLAN = """## Segment A: wiring
- [ ] T3: Add the parser | depends_on: T1
- [ ] T4: Copy the header | copy_of: T3
- [ ] T5: Rename the import | mechanical
- [ ] T6: Design the cache | complex
"""


def block(obj):
    return "did it\n```json\n%s\n```" % json.dumps(obj)


class Recorder(object):
    """Stand-in for claude_proc.run_phase; records kwargs, replays scripted replies."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, **kw):
        self.calls.append(kw)
        reply = self.replies.pop(0) if self.replies else {}
        side_effect = reply.get("side_effect")
        if side_effect:
            side_effect()
        return PhaseResult(phase=kw["phase"], model=kw["model"], rc=0,
                           killed=reply.get("killed", False),
                           session_id="sess-1",
                           result_text=reply.get("text", ""),
                           usage_by_model=reply.get("usage", {}))


class Base(unittest.TestCase):
    def setUp(self):
        self.wt = tempfile.mkdtemp()
        self.loop_dir = os.path.join(self.wt, ".claude", "loop", "run")
        self.runtime = os.path.join(self.loop_dir, "runtime")
        os.makedirs(self.runtime)
        self.spec = os.path.join(self.wt, "spec.md")
        with open(self.spec, "w") as f:
            f.write("# Spec\n\nT3 parses the plan rows.\nT9 is unrelated.\n")
        cfg_path = os.path.join(self.loop_dir, "LOOP_CONFIG.md")
        with open(cfg_path, "w") as f:
            f.write(CONFIG % (self.wt, self.spec))
        plan_path = os.path.join(self.loop_dir, "LOOP_PLAN.md")
        with open(plan_path, "w") as f:
            f.write(PLAN)
        self.cfg = config.load_config(cfg_path)
        self.plan = planmod.Plan.load(plan_path)
        self.events = EventLog(os.path.join(self.loop_dir, "events.jsonl"),
                               os.path.join(self.runtime, "eventseq"))
        self._real = claude_proc.run_phase

    def tearDown(self):
        claude_proc.run_phase = self._real

    def ctx(self, task_id="T3", attempt=1):
        return phases.TickContext(cfg=self.cfg, plan=self.plan,
                                  task=self.plan.task(task_id),
                                  loop_dir=self.loop_dir, runtime_dir=self.runtime,
                                  events=self.events, tick=1, attempt=attempt)

    def patch(self, replies):
        rec = Recorder(replies)
        claude_proc.run_phase = rec
        return rec

    def write_contract(self, task_id="T3", **over):
        data = {"task": task_id, "success_criteria": ["src/a.ts exports parse"],
                "allow_list": ["src/a.ts"], "verification": ["true"],
                "forbidden": [], "scout_notes": "inline"}
        data.update(over)
        path = os.path.join(self.runtime, "sprint-%s.json" % task_id)
        util.write_json(path, data)
        return path


class TestTiers(Base):
    def test_worker_tier_is_the_configured_one_and_never_a_ladder(self):
        self.assertEqual("standard", phases.worker_tier(self.cfg))
        self.cfg.role_tiers["worker"] = "cheap"
        self.assertEqual("cheap", phases.worker_tier(self.cfg))

    def test_an_explicit_tier_overrides_the_configured_one(self):
        # The seam plan C's Judge uses (changes.tier). Nothing in plan A calls it.
        self.assertEqual("most-capable", phases.worker_tier(self.cfg, "most-capable"))

    def test_a_garbage_tier_from_either_source_falls_back_to_standard(self):
        self.cfg.role_tiers["worker"] = "turbo"
        self.assertEqual("standard", phases.worker_tier(self.cfg))
        self.assertEqual("standard", phases.worker_tier(self.cfg, "turbo"))

    def test_evaluator_tier_is_governed_by_the_task_class(self):
        self.assertEqual("standard", phases.evaluator_tier(self.cfg, self.plan.task("T3")))
        self.assertEqual("most-capable", phases.evaluator_tier(self.cfg, self.plan.task("T6")))

    def test_a_configured_evaluator_tier_is_the_ceiling_for_complex_only(self):
        self.cfg.role_tiers["evaluator"] = "most-capable"
        self.assertEqual("standard", phases.evaluator_tier(self.cfg, self.plan.task("T3")))
        self.cfg.role_tiers["evaluator"] = "standard"
        self.assertEqual("standard", phases.evaluator_tier(self.cfg, self.plan.task("T6")))


class TestScout(Base):
    def test_it_loads_and_validates_the_contract_the_scout_wrote(self):
        rec = self.patch([{"text": block({"contract_path": "x", "notes": "ok"}),
                           "side_effect": self.write_contract}])
        res, contract, errors = phases.run_scout(self.ctx())
        self.assertEqual([], errors)
        self.assertEqual("T3", contract.task)
        self.assertEqual(0, res.rc)
        self.assertEqual("mid", rec.calls[0]["model"])
        self.assertEqual("scout", rec.calls[0]["phase"])

    def test_the_prompt_carries_the_task_row_the_digest_and_the_spec_lines(self):
        util.atomic_write(os.path.join(self.loop_dir, "LOOP_LEARNINGS.md"),
                          "## Patterns\n- always run pnpm install first\n\n## Log\n")
        rec = self.patch([{"text": block({"contract_path": "x", "notes": ""}),
                           "side_effect": self.write_contract}])
        phases.run_scout(self.ctx())
        prompt = rec.calls[0]["prompt"]
        self.assertIn("T3: Add the parser", prompt)
        self.assertIn("always run pnpm install first", prompt)
        self.assertIn("T3 parses the plan rows", prompt)
        self.assertNotIn("{{", prompt)

    def test_validation_errors_are_reported_not_raised(self):
        self.patch([{"text": block({"contract_path": "x", "notes": ""}),
                     "side_effect": lambda: self.write_contract(allow_list=[])}])
        _, contract, errors = phases.run_scout(self.ctx())
        self.assertIsNotNone(contract)
        self.assertTrue(any("allow_list" in e for e in errors))

    def test_a_missing_contract_file_is_an_error_not_a_crash(self):
        self.patch([{"text": block({"contract_path": "x", "notes": ""})}])
        _, contract, errors = phases.run_scout(self.ctx())
        self.assertIsNone(contract)
        self.assertEqual(1, len(errors))
        self.assertIn("sprint-T3.json", errors[0])

    def test_a_second_attempt_injects_the_previous_errors(self):
        rec = self.patch([{"text": block({"contract_path": "x", "notes": ""}),
                           "side_effect": self.write_contract}])
        phases.run_scout(self.ctx(attempt=2), validation_errors=["allow_list is empty"])
        self.assertIn("allow_list is empty", rec.calls[0]["prompt"])

    def test_the_copy_rule_reaches_validation(self):
        self.patch([{"text": block({"contract_path": "x", "notes": ""}),
                     "side_effect": lambda: self.write_contract("T4")}])
        _, _, errors = phases.run_scout(self.ctx("T4"))
        self.assertTrue(any("fidelity_source" in e for e in errors))


class TestReask(Base):
    def test_a_malformed_reply_is_re_asked_once_with_the_parse_error(self):
        rec = self.patch([{"text": "no json here",
                           "usage": {"m": Usage(cost_usd=1.0)}},
                          {"text": block({"contract_path": "x", "notes": ""}),
                           "usage": {"m": Usage(cost_usd=2.0)},
                           "side_effect": self.write_contract}])
        res, contract, errors = phases.run_scout(self.ctx())
        self.assertEqual(2, len(rec.calls))
        self.assertIn("JSON", rec.calls[1]["prompt"])
        self.assertIsNotNone(contract)
        self.assertEqual(3.0, res.usage_by_model["m"].cost_usd)

    def test_two_malformed_replies_give_up(self):
        rec = self.patch([{"text": "nope"}, {"text": "still nope"}])
        res, data = phases.run_reviewer(self.ctx(), "Segment A: wiring", "diff")
        self.assertEqual(2, len(rec.calls))
        self.assertEqual({}, data)

    def test_a_killed_phase_is_not_re_asked(self):
        rec = self.patch([{"text": "", "killed": True}])
        phases.run_reviewer(self.ctx(), "Segment A: wiring", "diff")
        self.assertEqual(1, len(rec.calls))


class TestWorker(Base):
    def test_a_re_attempt_dispatches_at_the_SAME_tier(self):
        self.write_contract()
        rec = self.patch([{"text": block({"status": "complete", "summary": "done"})}])
        contract = cmod.load_contract(os.path.join(self.runtime, "sprint-T3.json"))
        res, data = phases.run_worker(self.ctx(attempt=2), contract)
        self.assertEqual("mid", rec.calls[0]["model"])
        self.assertEqual("complete", data["status"])
        self.assertEqual(0, res.rc)

    def test_an_explicit_tier_reaches_the_command_line(self):
        self.write_contract()
        rec = self.patch([{"text": block({"status": "complete", "summary": ""})}])
        contract = cmod.load_contract(os.path.join(self.runtime, "sprint-T3.json"))
        phases.run_worker(self.ctx(attempt=2), contract, tier="most-capable")
        self.assertEqual("big", rec.calls[0]["model"])

    def test_the_previous_findings_are_injected_on_a_re_dispatch(self):
        self.write_contract()
        contract = cmod.load_contract(os.path.join(self.runtime, "sprint-T3.json"))
        rec = self.patch([{"text": block({"status": "complete", "summary": ""})}])
        phases.run_worker(self.ctx(), contract)
        self.assertIn("first attempt", rec.calls[0]["prompt"])
        rec = self.patch([{"text": block({"status": "complete", "summary": ""})}])
        phases.run_worker(self.ctx(attempt=2), contract,
                          findings="the copy is a six-line TODO comment")
        self.assertIn("six-line TODO comment", rec.calls[0]["prompt"])

    def test_the_contract_is_the_only_context_it_gets(self):
        self.write_contract()
        rec = self.patch([{"text": block({"status": "complete", "summary": ""})}])
        contract = cmod.load_contract(os.path.join(self.runtime, "sprint-T3.json"))
        phases.run_worker(self.ctx(), contract)
        prompt = rec.calls[0]["prompt"]
        self.assertIn("src/a.ts", prompt)
        self.assertNotIn("Segment A", prompt)

    def test_the_tdd_note_appears_only_when_configured(self):
        self.write_contract()
        contract = cmod.load_contract(os.path.join(self.runtime, "sprint-T3.json"))
        rec = self.patch([{"text": block({"status": "complete", "summary": ""})}])
        phases.run_worker(self.ctx(), contract)
        self.assertNotIn("test-driven-development", rec.calls[0]["prompt"])
        self.cfg.tdd_mode = "tdd-per-task"
        rec = self.patch([{"text": block({"status": "complete", "summary": ""})}])
        phases.run_worker(self.ctx(), contract)
        self.assertIn("test-driven-development", rec.calls[0]["prompt"])

    def test_wrapup_and_resume_are_plan_c(self):
        self.write_contract()
        contract = cmod.load_contract(os.path.join(self.runtime, "sprint-T3.json"))
        self.patch([{"text": ""}])
        with self.assertRaises(NotImplementedError):
            phases.run_worker(self.ctx(), contract, wrapup=True)
        with self.assertRaises(NotImplementedError):
            phases.run_worker(self.ctx(), contract, resume_session="sess-1")


class TestEvaluator(Base):
    def test_the_diff_gate_and_screenshots_reach_the_prompt(self):
        self.write_contract()
        contract = cmod.load_contract(os.path.join(self.runtime, "sprint-T3.json"))
        rec = self.patch([{"text": block({"verdict": "PASS", "findings": [],
                                          "views": [], "summary": "fine"})}])
        res, data = phases.run_evaluator(self.ctx(), contract, "--- a/src/a.ts",
                                         "$ true\nrc=0", ["shots/a.png"])
        prompt = rec.calls[0]["prompt"]
        self.assertIn("--- a/src/a.ts", prompt)
        self.assertIn("rc=0", prompt)
        self.assertIn("shots/a.png", prompt)
        self.assertEqual("PASS", data["verdict"])

    def test_an_unknown_verdict_is_normalised_to_needs_work(self):
        self.write_contract()
        contract = cmod.load_contract(os.path.join(self.runtime, "sprint-T3.json"))
        self.patch([{"text": block({"verdict": "looks good", "summary": ""})}])
        _, data = phases.run_evaluator(self.ctx(), contract, "", "", [])
        self.assertEqual("NEEDS_WORK", data["verdict"])

    def test_a_malformed_verdict_twice_is_needs_work_with_a_reason(self):
        self.write_contract()
        contract = cmod.load_contract(os.path.join(self.runtime, "sprint-T3.json"))
        self.patch([{"text": "nope"}, {"text": "nope"}])
        _, data = phases.run_evaluator(self.ctx(), contract, "", "", [])
        self.assertEqual("NEEDS_WORK", data["verdict"])
        self.assertIn("malformed", data["summary"])


class TestLearner(Base):
    def test_it_writes_evidenced_patterns_and_logs_the_rest(self):
        self.write_contract()
        contract = cmod.load_contract(os.path.join(self.runtime, "sprint-T3.json"))
        self.patch([{"text": block({
            "patterns": [{"rule": "run install first", "evidence": "gate-T3-1.txt"},
                         {"rule": "the layouts were genuinely copied", "evidence": ""}],
            "log": "T3 went fine",
            "invariants": [{"rule": "describe blocks are PascalCase",
                            "check": "! grep -n 'describe(.[a-z]' src/*.test.ts"}]})}])
        phases.run_learner(self.ctx(), contract, "$ true\nrc=0", "PASS")
        text = util.read_text(os.path.join(self.loop_dir, "LOOP_LEARNINGS.md"))
        self.assertIn("run install first", text.split("## Invariants")[0])
        self.assertNotIn("genuinely copied", text.split("## Invariants")[0])
        self.assertIn("genuinely copied", text)
        self.assertIn("PascalCase", text)
        self.assertIn("T3 went fine", text)

    def test_the_patterns_digest_is_capped_at_2kb_oldest_first(self):
        old = "\n".join("- rule %03d padded %s" % (i, "x" * 60) for i in range(60))
        util.atomic_write(os.path.join(self.loop_dir, "LOOP_LEARNINGS.md"),
                          "## Patterns\n%s\n\n## Invariants\n\n## Log\n" % old)
        phases.apply_learnings(self.loop_dir, {
            "patterns": [{"rule": "the newest rule", "evidence": "gate-T3-1.txt"}],
            "log": "", "invariants": []})
        text = util.read_text(os.path.join(self.loop_dir, "LOOP_LEARNINGS.md"))
        digest = text.split("## Invariants")[0]
        self.assertLessEqual(len(digest.encode("utf-8")), 2048 + 64)
        self.assertIn("the newest rule", digest)
        self.assertNotIn("rule 000", digest)

    def test_duplicate_rules_and_invariants_are_not_re_added(self):
        for _ in range(3):
            phases.apply_learnings(self.loop_dir, {
                "patterns": [{"rule": "same rule", "evidence": "gate-T3-1.txt"}],
                "log": "", "invariants": [{"rule": "same inv", "check": "true"}]})
        text = util.read_text(os.path.join(self.loop_dir, "LOOP_LEARNINGS.md"))
        self.assertEqual(1, text.count("same rule"))
        self.assertEqual(1, text.count("same inv"))

    def test_the_digest_reader_returns_only_the_patterns_section(self):
        util.atomic_write(os.path.join(self.loop_dir, "LOOP_LEARNINGS.md"),
                          "## Patterns\n- a rule\n\n## Log\n- a very long log entry\n")
        digest = phases.learnings_digest(self.loop_dir)
        self.assertIn("a rule", digest)
        self.assertNotIn("very long log entry", digest)

    def test_a_missing_learnings_file_is_created(self):
        phases.apply_learnings(self.loop_dir, {"patterns": [], "log": "first",
                                               "invariants": []})
        self.assertTrue(os.path.exists(os.path.join(self.loop_dir, "LOOP_LEARNINGS.md")))


class TestPlannerAndReviewer(Base):
    def test_planner_runs_at_the_top_tier_with_the_segment_named(self):
        rec = self.patch([{"text": block({"tasks_added": 4})}])
        res, data = phases.run_planner(self.ctx(), "Segment B: polish")
        self.assertEqual("big", rec.calls[0]["model"])
        self.assertIn("Segment B: polish", rec.calls[0]["prompt"])
        self.assertEqual(4, data["tasks_added"])

    def test_reviewer_gets_the_segment_diff_and_its_task_rows(self):
        rec = self.patch([{"text": block({"findings": [
            {"severity": "must-fix", "title": "t", "detail": "d",
             "follow_up_row": "- [ ] T9: fix it"}]})}])
        _, data = phases.run_reviewer(self.ctx(), "Segment A: wiring", "--- a/src/a.ts")
        prompt = rec.calls[0]["prompt"]
        self.assertIn("--- a/src/a.ts", prompt)
        self.assertIn("T3: Add the parser", prompt)
        self.assertEqual(1, len(data["findings"]))


if __name__ == "__main__":
    unittest.main()
````

- [ ] **Step 2: Run test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_phases*.py' -v`
Expected: FAIL — `AttributeError: module 'runner.phases' has no attribute 'worker_tier'`

- [ ] **Step 3: Append the role functions to `runner/phases.py`**

Add these imports at the top of the file (replacing the existing import block):

```python
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import claude_proc, config, contract as contract_mod, util
from .claude_proc import PhaseResult, Usage
```

Then append to the end of `runner/phases.py`:

````python
DIGEST_CAP = 2048                      # bytes; spec §9 keeps the 2 KB digest cap
_REASK = ("\n\n---\n\nYour previous reply could not be parsed as JSON: %s\n"
          "Reply again with the SAME content, ending in exactly one fenced ```json "
          "block matching the shape above. Add no commentary after it.")
_TDD_NOTE = ("**TDD is on for this run.** Invoke the "
             "`superpowers:test-driven-development` skill: write the failing test "
             "first, watch it fail, then make it pass.")
_FIRST_ATTEMPT = "(nothing — this is the first attempt at this task)"
_VERDICTS = ("PASS", "NEEDS_WORK", "BLOCKER")


def _env() -> Dict[str, str]:
    return dict(os.environ)


def _stall_s() -> int:
    try:
        return int(os.environ.get("STALL_S", "300"))
    except ValueError:
        return 300


def _read_section(text: str, heading: str) -> str:
    """The body under `## <heading>` up to the next `## `."""
    out = []
    collecting = False
    for line in text.splitlines():
        if line.startswith("## "):
            collecting = line[3:].strip().lower().startswith(heading.lower())
            continue
        if collecting:
            out.append(line)
    return "\n".join(out).strip()


def learnings_digest(loop_dir: str) -> str:
    """The `## Patterns` digest — the only part of the learnings on the hot path."""
    text = util.read_text(os.path.join(loop_dir, "LOOP_LEARNINGS.md"))
    return _read_section(text, "Patterns") or "(nothing learned yet)"


def knowledge_text(loop_dir: str, cap: int = DIGEST_CAP) -> str:
    """`.claude/loop/KNOWLEDGE.md` — durable, cross-run, sibling of the loop dir."""
    path = os.environ.get("LOOP_KNOWLEDGE") or os.path.join(
        os.path.dirname(os.path.abspath(loop_dir)), "KNOWLEDGE.md")
    body = _read_section(util.read_text(path), "Patterns")
    if not body:
        return "(no cross-run knowledge yet)"
    return body[:cap]


def spec_excerpt(cfg, task, cap: int = 4000) -> str:
    """The spec lines that mention this task's id, or the spec's opening."""
    if not cfg.spec_path:
        return "(no spec configured)"
    path = cfg.spec_path
    if not os.path.isabs(path):
        path = os.path.join(cfg.worktree, path)
    text = util.read_text(path)
    if not text:
        return "(spec not readable at %s)" % path
    lines = text.splitlines()
    pattern = re.compile(r"\b%s\b" % re.escape(task.id)) if task is not None else None
    hits = [ln for ln in lines if pattern and pattern.search(ln)]
    body = "\n".join(hits) if hits else "\n".join(lines[:60])
    return body[:cap]


def _result_with(base: PhaseResult, extra: PhaseResult) -> PhaseResult:
    """`extra` (the re-ask) as the phase's result, carrying both attempts' spend."""
    extra.usage_by_model = _merge_usage(base.usage_by_model, extra.usage_by_model)
    extra.tool_calls += base.tool_calls
    extra.started = base.started
    return extra


def _dispatch(ctx: TickContext, *, phase: str, role: str, prompt_name: str,
              tier: str, variables: Dict[str, Any], desc: str,
              max_turns: int = 0, max_budget_usd: Optional[float] = None
              ) -> Tuple[PhaseResult, Optional[Dict[str, Any]]]:
    """Render, run, parse. One re-ask on a parse failure; none after a kill."""
    model = config.model_for(ctx.cfg, tier)
    timeout_s = config.phase_limit(ctx.cfg, phase)
    prompt = render_prompt(prompt_name, **variables)
    kwargs = dict(phase=phase, model=model, cwd=ctx.cfg.worktree,
                  timeout_s=timeout_s, max_turns=max_turns,
                  max_budget_usd=max_budget_usd, env=_env(), events=ctx.events,
                  tick=ctx.tick, role=role,
                  activity_path=os.path.join(ctx.runtime_dir, "last-activity"),
                  ratelimit_path=os.path.join(ctx.runtime_dir, "ratelimit.json"),
                  stall_s=_stall_s(), desc=desc)
    res = claude_proc.run_phase(prompt=prompt, **kwargs)
    try:
        return res, parse_json_block(res.result_text)
    except JsonBlockError as exc:
        if res.killed:
            return res, None
        # Python 3 unbinds the `as` name at the end of the except block, so the
        # message has to be carried out of it by hand.
        parse_error = str(exc)
    again = claude_proc.run_phase(prompt=prompt + (_REASK % parse_error), **kwargs)
    merged = _result_with(res, again)
    try:
        return merged, parse_json_block(again.result_text)
    except JsonBlockError:
        return merged, None


def worker_tier(cfg, tier: Optional[str] = None) -> str:
    """The tier to dispatch the Worker at. A lookup, not a ladder.

    Spec §11 item 4 (Mitch, parallel session): the tier of a re-attempt is judged
    from the Worker's checkpoint, not stepped up on a schedule. Most overruns are
    one extra iteration, and paying the top tier for them is how a cheap retry
    becomes an expensive one. So the attempt number is not an input here: every
    attempt runs at the configured tier unless a caller names a different one,
    and in plan A nobody does. Plan C passes the Judge's `changes.tier`.
    """
    chosen = tier or cfg.role_tiers.get("worker") or "standard"
    if chosen not in config.TIER_ORDER:
        chosen = "standard"
    return chosen


def evaluator_tier(cfg, task) -> str:
    """Class-governed: `complex` earns the top tier, everything else standard.

    A configured `Evaluator tier:` is the ceiling for `complex` only. A flat
    config pin used to drag every ordinary clone onto the most capable model.
    """
    if getattr(task, "class_flag", None) == "complex":
        configured = cfg.role_tiers.get("evaluator") or ""
        return configured if configured in config.TIER_ORDER else "most-capable"
    return "standard"


def run_scout(ctx: TickContext, validation_errors: Optional[List[str]] = None
              ) -> Tuple[PhaseResult, Optional[contract_mod.Contract], List[str]]:
    """Dispatch the Scout, then load and validate the contract it wrote."""
    errors_block = "\n".join("- %s" % e for e in (validation_errors or [])) or "(none)"
    res, _ = _dispatch(
        ctx, phase="scout", role="Scout", prompt_name="scout",
        tier=ctx.cfg.role_tiers.get("scout") or "standard",
        desc="Scout %s" % ctx.task.id,
        variables={"loop_dir": ctx.loop_dir, "worktree": ctx.cfg.worktree,
                   "task_row": ctx.task.raw.strip(),
                   "spec_excerpt": spec_excerpt(ctx.cfg, ctx.task),
                   "learnings_digest": learnings_digest(ctx.loop_dir),
                   "knowledge": knowledge_text(ctx.loop_dir),
                   "validation_errors": errors_block})
    path = os.path.join(ctx.runtime_dir, "sprint-%s.json" % ctx.task.id)
    try:
        contract = contract_mod.load_contract(path)
    except contract_mod.ContractError:
        return res, None, ["the Scout wrote no readable contract at %s"
                           % os.path.basename(path)]
    cleanup = util.read_text(os.path.join(ctx.loop_dir, "LOOP_CLEANUP.md"))
    ui_globs = (ctx.cfg.render or {}).get("ui_globs", [])
    return res, contract, contract_mod.validate(contract, ctx.task, ctx.cfg,
                                                cleanup, ui_globs)


def run_worker(ctx: TickContext, contract, resume_session: Optional[str] = None,
               wrapup: bool = False, tier: Optional[str] = None,
               findings: str = "") -> Tuple[PhaseResult, Dict[str, Any]]:
    """Dispatch the Worker. `tier` overrides the configured one; nothing in plan A
    passes it, so a re-dispatch runs at the same tier with the findings injected."""
    if wrapup or resume_session:
        raise NotImplementedError(
            "worker wrap-up and resume land in plan C (spec §6); plan A dispatches "
            "a fresh Worker per attempt")
    tdd = _TDD_NOTE if ctx.cfg.tdd_mode not in ("", "none") else ""
    res, data = _dispatch(
        ctx, phase="worker", role="Worker", prompt_name="worker",
        tier=worker_tier(ctx.cfg, tier),
        desc="Worker %s attempt %d" % (ctx.task.id, ctx.attempt),
        max_budget_usd=float(ctx.cfg.limits.get("worker_budget_usd", 6)),
        variables={"loop_dir": ctx.loop_dir, "worktree": ctx.cfg.worktree,
                   "contract_json": json.dumps(contract_mod.to_dict(contract),
                                               indent=2),
                   "tdd_note": tdd,
                   "evaluator_findings": findings or _FIRST_ATTEMPT})
    if data is None:
        data = {"status": "partial", "summary": "the Worker returned no usable JSON"}
    return res, data


def run_evaluator(ctx: TickContext, contract, diff_text: str, gate_outputs: str,
                  screenshots) -> Tuple[PhaseResult, Dict[str, Any]]:
    """Grade the diff against the contract. Plan B fills must_read/screenshots."""
    shots = "\n".join("- %s" % s for s in (screenshots or [])) or "(none)"
    res, data = _dispatch(
        ctx, phase="evaluator", role="Evaluator", prompt_name="evaluator",
        tier=evaluator_tier(ctx.cfg, ctx.task),
        desc="Evaluate %s" % ctx.task.id,
        variables={"task_row": ctx.task.raw.strip(),
                   "contract_json": json.dumps(contract_mod.to_dict(contract), indent=2),
                   "diff": diff_text or "(empty diff)",
                   "gate_outputs": gate_outputs or "(none)",
                   "must_read_blocks": "(none)",
                   "screenshots": shots})
    if data is None:
        return res, {"verdict": "NEEDS_WORK", "findings": [], "views": [],
                     "summary": "the Evaluator returned malformed output twice"}
    if data.get("verdict") not in _VERDICTS:
        data["verdict"] = "NEEDS_WORK"
    data.setdefault("findings", [])
    data.setdefault("views", [])
    data.setdefault("summary", "")
    return res, data


def run_learner(ctx: TickContext, contract, gate_outputs: str, verdict: str
                ) -> PhaseResult:
    """Record what this task taught, with evidence, and rewrite the learnings."""
    res, data = _dispatch(
        ctx, phase="learner", role="Learner", prompt_name="learner", tier="cheap",
        desc="Learn from %s" % ctx.task.id,
        variables={"task_row": ctx.task.raw.strip(), "verdict": verdict or "(none)",
                   "gate_outputs": gate_outputs or "(none)",
                   "learnings_digest": learnings_digest(ctx.loop_dir)})
    if data:
        apply_learnings(ctx.loop_dir, data)
    return res


def run_planner(ctx: TickContext, segment: str) -> Tuple[PhaseResult, Dict[str, Any]]:
    """Write the rows for one segment. The harness re-parses and validates them."""
    res, data = _dispatch(
        ctx, phase="planner", role="Planner", prompt_name="planner",
        tier=ctx.cfg.role_tiers.get("planner") or "most-capable",
        desc="Plan %s" % segment,
        variables={"loop_dir": ctx.loop_dir, "worktree": ctx.cfg.worktree,
                   "segment": segment,
                   "spec_excerpt": spec_excerpt(ctx.cfg, ctx.task),
                   "learnings_digest": learnings_digest(ctx.loop_dir)})
    return res, (data or {"tasks_added": 0})


def run_reviewer(ctx: TickContext, segment: str, diff_text: str
                 ) -> Tuple[PhaseResult, Dict[str, Any]]:
    """Grade a finished segment as a whole."""
    rows = [t.raw.strip() for t in ctx.plan.tasks() if t.segment == segment]
    res, data = _dispatch(
        ctx, phase="reviewer", role="Reviewer", prompt_name="reviewer",
        tier=ctx.cfg.role_tiers.get("reviewer") or "most-capable",
        desc="Review %s" % segment,
        variables={"loop_dir": ctx.loop_dir, "segment": segment,
                   "task_row": "\n".join(rows) or "(no rows)",
                   "spec_excerpt": spec_excerpt(ctx.cfg, ctx.task),
                   "diff": diff_text or "(empty diff)"})
    return res, (data or {})


def _trim_digest(lines: List[str], cap: int = DIGEST_CAP) -> List[str]:
    """Drop oldest-first until the rendered digest fits the cap."""
    kept = list(lines)
    while kept and len(("\n".join(kept)).encode("utf-8")) > cap:
        kept.pop(0)
    return kept


def apply_learnings(loop_dir: str, data: Dict[str, Any]) -> None:
    """Rewrite LOOP_LEARNINGS.md: evidenced patterns up top, everything else below.

    A pattern with no evidence goes to the log and never to the digest. One run
    promoted a false "genuinely copied" claim into its patterns and every later
    Scout inlined the lie into a fresh contract.
    """
    path = os.path.join(loop_dir, "LOOP_LEARNINGS.md")
    text = util.read_text(path)
    patterns = [ln for ln in _read_section(text, "Patterns").splitlines() if ln.strip()]
    invariants = [ln for ln in _read_section(text, "Invariants").splitlines() if ln.strip()]
    log = _read_section(text, "Log")

    demoted = []
    for item in data.get("patterns") or []:
        rule = (item.get("rule") or "").strip()
        evidence = (item.get("evidence") or "").strip()
        if not rule:
            continue
        if not evidence:
            demoted.append("- (no evidence, not promoted) %s" % rule)
            continue
        row = "- %s — evidence: %s" % (rule, evidence)
        if not any(rule in existing for existing in patterns):
            patterns.append(row)

    for item in data.get("invariants") or []:
        rule = (item.get("rule") or "").strip()
        check = (item.get("check") or "").strip()
        if not rule or not check:
            continue
        row = "- %s — check: `%s`" % (rule, check)
        if not any(rule in existing for existing in invariants):
            invariants.append(row)

    entry = (data.get("log") or "").strip()
    new_log = [log] if log else []
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if entry:
        new_log.append("- %s %s" % (stamp, entry))
    new_log.extend(demoted)

    util.atomic_write(path, "\n".join([
        "# Loop Learnings", "",
        "## Patterns", "\n".join(_trim_digest(patterns)), "",
        "## Invariants", "\n".join(invariants), "",
        "## Log", "\n".join(new_log), "",
    ]))
````

- [ ] **Step 4: Run test to verify it passes**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_*.py' -v`
Expected: PASS — 313 tests, `OK`

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/runner/phases.py plugins/agent-loop/tests/runner/test_phases.py
git commit -F - <<'EOF'
agent-loop: the six phase functions, and the tier seam the Judge will use

worker_tier is a lookup, not a ladder: spec 11.4 makes the tier of a re-attempt
a judgement from the Worker's checkpoint, so the attempt number is not an input
and plan C's Judge is the only thing that can change it. evaluator_tier is
governed by the task's class flag with a configured tier as the ceiling for
complex only — a prompt request in v2, ignored in the live run. A re-dispatch
carries the Evaluator's findings into the same role brief instead of a stronger
model. Each phase gets exactly one re-ask on malformed JSON, with both
attempts' usage merged so no spend is dropped. apply_learnings refuses to
promote a pattern with no evidence and keeps the 2 KB digest oldest-first.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 15: `incidents.py` — incidents, the medic and the needs-human handover

**Files:**
- Create: `plugins/agent-loop/runner/incidents.py`
- Test: `plugins/agent-loop/tests/runner/test_incidents.py`

**Interfaces:**
- Consumes: `util.read_int`, `util.write_json`, `util.atomic_write`.
- Produces: `MedicState`, `next_id(runtime_dir)`, `incident_new(runtime_dir, events, kind, severity, detail, tick, phases=None) -> str`, `run_medic(runtime_dir, events, cfg, incident_id, state, log=None) -> str`, `handle_incident(runtime_dir, events, cfg, kind, severity, detail, tick, state, log=None) -> bool`, `escalate(runtime_dir, events, kind, detail, tick, worktree, loop_dir, run_sh, incident_id="") -> None`, `notify_desktop(title, body)`.
- `incident-<id>.json` keeps its v2 shape `{id,kind,severity,detail,tick,t}` and gains `phases: []` (spec §8; plan C fills it).
- `LOOP_MEDIC_CMD` overrides the medic command line, and `LOOP_NOTIFY=0` suppresses the desktop notification — both as in v2, because the e2e drives them.
- `handle_incident` returns True when the loop may continue: a `warn` always may; an `error` may only when a medic ran and reported `resumed`.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_incidents.py`:

```python
import _path  # noqa: F401
import json
import os
import stat
import tempfile
import unittest

from runner import incidents, util
from runner.config import LoopConfig
from runner.events import EventLog


def medic_script(d, outcome):
    path = os.path.join(d, "medic.sh")
    with open(path, "w") as f:
        f.write(
            "#!/usr/bin/env bash\n"
            'id="${MEDIC_INCIDENT_ID:-}"\n'
            'printf \'{"id":"%%s","kind":"mock","outcome":"%s","actions":[],'
            '"summary":"mock medic %s","human_next_step":"look at the box"}\' '
            '"$id" > "$RUNTIME_DIR/medic-$id.json"\n' % (outcome, outcome))
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.rt = os.path.join(self.d, "runtime")
        os.makedirs(self.rt)
        self.events = EventLog(os.path.join(self.d, "events.jsonl"),
                               os.path.join(self.rt, "eventseq"))
        self.cfg = LoopConfig(worktree=self.d, medic="auto")
        os.environ["RUNTIME_DIR"] = self.rt
        os.environ["LOOP_NOTIFY"] = "0"

    def emitted(self, type_):
        out = []
        with open(os.path.join(self.d, "events.jsonl")) as f:
            for line in f:
                rec = json.loads(line)
                if rec["type"] == type_:
                    out.append(rec)
        return out


class TestIncidentNew(Base):
    def test_ids_are_sequential_and_persisted(self):
        self.assertEqual("i-001", incidents.incident_new(
            self.rt, self.events, "tick-killed", "error", "rc=137", 3))
        self.assertEqual("i-002", incidents.incident_new(
            self.rt, self.events, "tick-killed", "error", "rc=137", 4))
        self.assertEqual(2, util.read_int(os.path.join(self.rt, "incidentseq")))

    def test_the_file_keeps_the_v2_shape_plus_phases(self):
        incidents.incident_new(self.rt, self.events, "tick-timeout", "error", "slow", 7)
        rec = util.read_json(os.path.join(self.rt, "incident-i-001.json"))
        self.assertEqual("i-001", rec["id"])
        self.assertEqual("tick-timeout", rec["kind"])
        self.assertEqual("error", rec["severity"])
        self.assertEqual("slow", rec["detail"])
        self.assertEqual(7, rec["tick"])
        self.assertIsInstance(rec["t"], int)
        self.assertEqual([], rec["phases"])

    def test_the_event_carries_the_same_fields(self):
        incidents.incident_new(self.rt, self.events, "lock-conflict", "warn", "pid 9", 0)
        ev = self.emitted("incident")[0]
        self.assertEqual(["t", "seq", "type", "id", "kind", "severity", "detail", "tick"],
                         list(ev.keys()))

    def test_a_phase_timeline_is_stored_when_given(self):
        incidents.incident_new(self.rt, self.events, "k", "warn", "d", 1,
                               phases=[{"phase": "worker", "rc": 143}])
        rec = util.read_json(os.path.join(self.rt, "incident-i-001.json"))
        self.assertEqual("worker", rec["phases"][0]["phase"])


class TestMedic(Base):
    def test_a_resumed_medic_lets_the_loop_continue(self):
        os.environ["LOOP_MEDIC_CMD"] = "bash %s" % medic_script(self.d, "resumed")
        state = incidents.MedicState(max_per_run=3)
        ok = incidents.handle_incident(self.rt, self.events, self.cfg,
                                       "tick-killed", "error", "rc=137", 3, state)
        self.assertTrue(ok)
        self.assertEqual("resumed", self.emitted("medic_end")[0]["outcome"])
        self.assertEqual(1, state.count)

    def test_a_paused_medic_stops_the_loop(self):
        os.environ["LOOP_MEDIC_CMD"] = "bash %s" % medic_script(self.d, "paused")
        self.assertFalse(incidents.handle_incident(
            self.rt, self.events, self.cfg, "tick-killed", "error", "rc=137", 3,
            incidents.MedicState()))

    def test_a_medic_that_writes_nothing_counts_as_escalated(self):
        os.environ["LOOP_MEDIC_CMD"] = "true"
        self.assertFalse(incidents.handle_incident(
            self.rt, self.events, self.cfg, "tick-killed", "error", "x", 3,
            incidents.MedicState()))
        self.assertEqual("escalated", self.emitted("medic_end")[0]["outcome"])

    def test_a_warn_never_runs_a_medic(self):
        os.environ["LOOP_MEDIC_CMD"] = "bash %s" % medic_script(self.d, "resumed")
        self.assertTrue(incidents.handle_incident(
            self.rt, self.events, self.cfg, "tick-stalled", "warn", "quiet", 3,
            incidents.MedicState()))
        self.assertEqual([], self.emitted("medic_start"))

    def test_medic_off_escalates_immediately(self):
        self.cfg.medic = "off"
        self.assertFalse(incidents.handle_incident(
            self.rt, self.events, self.cfg, "tick-killed", "error", "x", 3,
            incidents.MedicState()))
        self.assertEqual([], self.emitted("medic_start"))

    def test_medic_notify_escalates_without_dispatching(self):
        self.cfg.medic = "notify"
        self.assertFalse(incidents.handle_incident(
            self.rt, self.events, self.cfg, "halt-sentinel", "error", "x", 3,
            incidents.MedicState()))
        self.assertEqual([], self.emitted("medic_start"))

    def test_the_budget_is_a_hard_ceiling(self):
        os.environ["LOOP_MEDIC_CMD"] = "bash %s" % medic_script(self.d, "resumed")
        state = incidents.MedicState(max_per_run=1)
        self.assertTrue(incidents.handle_incident(self.rt, self.events, self.cfg,
                                                  "k", "error", "x", 1, state))
        self.assertFalse(incidents.handle_incident(self.rt, self.events, self.cfg,
                                                   "k", "error", "x", 2, state))
        self.assertEqual(1, len(self.emitted("medic_start")))


class TestEscalate(Base):
    def test_it_writes_needs_human_with_the_resume_command(self):
        incidents.escalate(self.rt, self.events, "tick-timeout", "rc=124", 9,
                           "/tmp/wt", ".claude/loop/run", "/plugin/run.sh")
        body = util.read_text(os.path.join(self.rt, "NEEDS_HUMAN.md"))
        self.assertIn("tick-timeout", body)
        self.assertIn("rc=124", body)
        self.assertIn("tick: 9", body)
        self.assertIn("/plugin/run.sh", body)
        self.assertIn("LOOP_DIR=.claude/loop/run", body)

    def test_the_escalation_is_itself_a_needs_human_incident(self):
        incidents.escalate(self.rt, self.events, "no-progress", "stuck", 9,
                           "/tmp/wt", "ld", "/plugin/run.sh")
        self.assertEqual("needs-human", self.emitted("incident")[-1]["severity"])

    def test_a_medic_summary_is_carried_into_the_file(self):
        util.write_json(os.path.join(self.rt, "medic-i-007.json"),
                        {"summary": "disk was full", "human_next_step": "free space"})
        incidents.escalate(self.rt, self.events, "k", "d", 1, "/w", "ld",
                           "/plugin/run.sh", incident_id="i-007")
        body = util.read_text(os.path.join(self.rt, "NEEDS_HUMAN.md"))
        self.assertIn("disk was full", body)
        self.assertIn("free space", body)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_incidents*.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'runner.incidents'`

- [ ] **Step 3: Write minimal implementation**

Create `plugins/agent-loop/runner/incidents.py`:

```python
"""Incidents, the budgeted medic, and the one file a human is left with.

An incident is a fact about the run that an operator or the medic needs to see.
`warn` never stops the loop. `error` runs the medic when policy and budget
allow, and anything short of a `resumed` verdict is the caller's cue to
escalate — which writes NEEDS_HUMAN.md and exits 2.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from . import util


@dataclass
class MedicState:
    """Per-harness-run medic budget and the last outcome, for the caller."""
    max_per_run: int = 3
    count: int = 0
    last_id: str = ""
    last_outcome: str = ""
    ran: bool = False


def notify_desktop(title: str, body: str) -> None:
    """Best-effort push to the human at the keyboard. Never blocks, never fails."""
    if os.environ.get("LOOP_NOTIFY", "1") == "0":
        return
    title = title.replace('"', "'").replace("\n", " ")
    body = body.replace('"', "'").replace("\n", " ")
    try:
        system = os.uname().sysname
    except AttributeError:
        return
    try:
        if system == "Darwin":
            subprocess.run(["osascript", "-e",
                            'display notification "%s" with title "%s"' % (body, title)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        elif system == "Linux":
            subprocess.run(["notify-send", title, body], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return


def next_id(runtime_dir: str) -> str:
    """i-NNN, counted per loop dir (the counter persists under runtime/)."""
    path = os.path.join(runtime_dir, "incidentseq")
    n = util.read_int(path, 0) + 1
    util.atomic_write(path, str(n))
    return "i-%03d" % n


def incident_new(runtime_dir: str, events, kind: str, severity: str, detail: str,
                 tick: int, phases: Optional[List[Dict[str, Any]]] = None) -> str:
    """Write incident-<id>.json for the medic and narrate one `incident` event."""
    ident = next_id(runtime_dir)
    util.write_json(os.path.join(runtime_dir, "incident-%s.json" % ident),
                    {"id": ident, "kind": kind, "severity": severity, "detail": detail,
                     "tick": int(tick or 0), "t": int(time.time()),
                     "phases": list(phases or [])})
    events.emit("incident", id=ident, kind=kind, severity=severity, detail=detail,
                tick=int(tick or 0))
    return ident


def run_medic(runtime_dir: str, events, cfg, incident_id: str, state: MedicState,
              log=None) -> str:
    """Dispatch one budgeted medic tick; return its outcome token.

    No file, an unparseable file, or a timeout all mean the same thing: the
    medic did not conclude it fixed anything, so it is an escalation.
    """
    path = os.path.join(runtime_dir, "medic-%s.json" % incident_id)
    try:
        os.unlink(path)
    except OSError:
        pass
    state.ran = True
    state.count += 1
    state.last_id = incident_id
    events.emit("medic_start", id=incident_id)
    if log:
        log("medic %s starting (%d/%d this run)"
            % (incident_id, state.count, state.max_per_run))

    override = os.environ.get("LOOP_MEDIC_CMD")
    if override:
        argv = shlex.split(override)
    else:
        argv = ["claude", "--print", "--dangerously-skip-permissions"]
        if cfg.medic_model:
            argv.extend(["--model", cfg.medic_model])
    argv.append("/agent-loop-medic %s" % incident_id)

    env = dict(os.environ)
    env["MEDIC_INCIDENT_ID"] = incident_id
    env["RUNTIME_DIR"] = runtime_dir
    try:
        subprocess.run(argv, env=env, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=int(os.environ.get("MEDIC_TIMEOUT", "600")))
    except (OSError, subprocess.SubprocessError):
        pass

    record = util.read_json(path) or {}
    outcome = record.get("outcome")
    if outcome not in ("resumed", "paused", "escalated", "noop"):
        outcome = "escalated"
    summary = record.get("summary", "") or ""
    events.emit("medic_end", id=incident_id, outcome=outcome, summary=summary)
    state.last_outcome = outcome
    if log:
        log("medic %s -> %s%s" % (incident_id, outcome, (" · " + summary) if summary else ""))
    return outcome


def handle_incident(runtime_dir: str, events, cfg, kind: str, severity: str,
                    detail: str, tick: int, state: MedicState, log=None) -> bool:
    """Raise the incident; return True when the loop may carry on."""
    state.ran = False
    state.last_outcome = ""
    state.last_id = incident_new(runtime_dir, events, kind, severity, detail, tick)
    if log:
        log("incident %s · %s · %s" % (state.last_id, kind, detail))
    if cfg.medic in ("auto", "notify"):
        notify_desktop("agent-loop: %s" % kind, detail)
    if severity == "warn":
        return True
    if cfg.medic == "auto" and state.count < state.max_per_run:
        return run_medic(runtime_dir, events, cfg, state.last_id, state, log) == "resumed"
    if cfg.medic == "auto" and log:
        log("medic budget exhausted (%d) — escalating %s" % (state.max_per_run, kind))
    return False


def escalate(runtime_dir: str, events, kind: str, detail: str, tick: int,
             worktree: str, loop_dir: str, run_sh: str, incident_id: str = "") -> None:
    """The loop cannot continue and no automation will fix it.

    Leave the human one file that says what broke and the exact command to
    resume. The caller exits 2 immediately after.
    """
    summary = ""
    next_step = ""
    if incident_id:
        record = util.read_json(os.path.join(runtime_dir,
                                             "medic-%s.json" % incident_id)) or {}
        summary = record.get("summary", "") or ""
        next_step = record.get("human_next_step", "") or ""
    lines = ["# agent-loop needs a human", "",
             "- incident: %s (%s)" % (incident_id or "none", kind),
             "- detail: %s" % detail,
             "- tick: %s" % tick,
             "- when: %s" % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())]
    if summary:
        lines.append("- medic summary: %s" % summary)
    if next_step:
        lines.append("- medic next step: %s" % next_step)
    lines.extend(["", "## Resume", "",
                  '    cd %s && LOOP_DIR=%s bash "%s"' % (worktree, loop_dir, run_sh), ""])
    util.atomic_write(os.path.join(runtime_dir, "NEEDS_HUMAN.md"), "\n".join(lines))
    incident_new(runtime_dir, events, kind, "needs-human", detail, tick)
    notify_desktop("agent-loop needs you", "%s: %s" % (kind, detail))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_*.py' -v`
Expected: PASS — 327 tests, `OK`

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/runner/incidents.py plugins/agent-loop/tests/runner/test_incidents.py
git commit -F - <<'EOF'
agent-loop: incidents, the budgeted medic and the needs-human handover

Same incident-<id>.json shape the medic skill already reads, plus the phases[]
field spec 8 asks for (plan C fills it). warn never stops the loop; an error
runs at most MEDIC_MAX_PER_RUN medics and only a "resumed" verdict lets the
loop carry on. escalate leaves one file with the failure and the exact resume
command.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 16: `migrate.py` — loop-dir schema 1 → 2 → 3

**Files:**
- Create: `plugins/agent-loop/runner/migrate.py`
- Test: `plugins/agent-loop/tests/runner/test_migrate.py`

**Interfaces:**
- Consumes: `util.read_int`, `util.atomic_write`, `util.read_text`.
- Produces: `LOOP_SCHEMA = 3`, `schema_read(runtime_dir, current) -> int`, `legacy_harness_live(loop_dir, recent_s=120) -> bool`, `migrate_1_to_2(loop_dir, runtime_dir) -> str`, `migrate_2_to_3(loop_dir, runtime_dir) -> str`, `migrate_loop_dir(loop_dir, runtime_dir, events, target, legacy_live, force, version) -> str`.
- Return tokens are unchanged from v2 — `ok:<n>`, `migrated:<from>:<to>:<actions>`, `blocked:<detail>`, `newer:<n>` — because `run.py` and the e2e branch on them.
- 2 → 3 creates `artifacts/`, `LOOP_DECISIONS.md`, `harness.log`, **adds `artifacts/` to the per-run `.gitignore`** so the sandbox's stray-revert never deletes plan B's screenshots, and **normalises every existing `runtime/sprint-*.json`**: a bare string in `forbidden` becomes `{"path": <string>, "source": "scout"}` and every missing field gets its v3 default, so a 2.x contract passes `contract.validate` instead of failing provenance on its first tick.
- It does **not** touch `LOOP_PLAN.md`, and in particular it leaves a `[~]` task exactly as it found it. Spec §14: a dir stopped mid-task upgrades and resumes, and the in-flight task is the boot rule's problem (Task 19's `boot_reconcile`), not the migration's. That is decision 13 — the framework is mechanical, the ambiguous step is an agent with a budget.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_migrate.py`:

```python
import _path  # noqa: F401
import json
import os
import tempfile
import time
import unittest

from runner import migrate, util
from runner.events import EventLog


class Base(unittest.TestCase):
    def setUp(self):
        self.ld = tempfile.mkdtemp()
        self.rt = os.path.join(self.ld, "runtime")
        os.makedirs(self.rt)
        self.events = EventLog(os.path.join(self.ld, "events.jsonl"),
                               os.path.join(self.rt, "eventseq"))

    def migrations(self):
        out = []
        path = os.path.join(self.ld, "events.jsonl")
        if not os.path.exists(path):
            return out
        with open(path) as f:
            for line in f:
                rec = json.loads(line)
                if rec["type"] == "migration":
                    out.append(rec)
        return out

    def run_migrate(self, legacy_live=False, force=False):
        return migrate.migrate_loop_dir(self.ld, self.rt, self.events,
                                        migrate.LOOP_SCHEMA, legacy_live, force, "3.0.0")


class TestSchemaRead(Base):
    def test_a_stamped_dir_reports_its_stamp(self):
        util.atomic_write(os.path.join(self.rt, "schema"), "2")
        self.assertEqual(2, migrate.schema_read(self.rt, 3))

    def test_a_tick_counter_without_a_stamp_means_schema_one(self):
        util.atomic_write(os.path.join(self.rt, "tickseq"), "41")
        self.assertEqual(1, migrate.schema_read(self.rt, 3))

    def test_a_fresh_dir_reports_the_current_schema(self):
        self.assertEqual(3, migrate.schema_read(self.rt, 3))


class TestMigrateFresh(Base):
    def test_a_fresh_dir_is_stamped_with_no_migration_event(self):
        self.assertEqual("ok:3", self.run_migrate())
        self.assertEqual(3, util.read_int(os.path.join(self.rt, "schema")))
        self.assertEqual([], self.migrations())


class TestMigrateTwoToThree(Base):
    def setUp(self):
        Base.setUp(self)
        util.atomic_write(os.path.join(self.rt, "schema"), "2")

    def test_it_creates_the_schema_three_files(self):
        out = self.run_migrate()
        self.assertTrue(out.startswith("migrated:2:3:"), out)
        self.assertTrue(os.path.isdir(os.path.join(self.ld, "artifacts")))
        self.assertTrue(os.path.exists(os.path.join(self.ld, "LOOP_DECISIONS.md")))
        self.assertTrue(os.path.exists(os.path.join(self.ld, "harness.log")))
        self.assertEqual(3, util.read_int(os.path.join(self.rt, "schema")))

    def test_artifacts_are_gitignored_so_the_sandbox_cannot_revert_them(self):
        self.run_migrate()
        body = util.read_text(os.path.join(self.ld, ".gitignore"))
        self.assertIn("artifacts/", body)
        self.assertIn("runtime/", body)

    def test_an_existing_gitignore_is_appended_to_not_replaced(self):
        util.atomic_write(os.path.join(self.ld, ".gitignore"), "runtime/\n")
        self.run_migrate()
        body = util.read_text(os.path.join(self.ld, ".gitignore"))
        self.assertEqual(1, body.count("runtime/"))
        self.assertIn("artifacts/", body)

    def test_an_existing_decisions_file_is_left_alone(self):
        util.atomic_write(os.path.join(self.ld, "LOOP_DECISIONS.md"), "# keep me\n")
        self.run_migrate()
        self.assertIn("keep me", util.read_text(os.path.join(self.ld, "LOOP_DECISIONS.md")))

    def test_the_migration_event_records_the_step(self):
        self.run_migrate()
        ev = self.migrations()[0]
        self.assertEqual(2, ev["from"])
        self.assertEqual(3, ev["to"])
        self.assertEqual("3.0.0", ev["plugin_version"])
        self.assertIn("artifacts-dir", ev["actions"])


class TestContractNormalisation(Base):
    def setUp(self):
        Base.setUp(self)
        util.atomic_write(os.path.join(self.rt, "schema"), "2")

    def contract(self, task_id, doc):
        path = os.path.join(self.rt, "sprint-%s.json" % task_id)
        util.write_json(path, doc)
        return path

    def test_a_string_forbidden_entry_gains_scout_provenance(self):
        path = self.contract("T60", {"task": "T60", "forbidden": ["apps/frontend/**"]})
        self.run_migrate()
        self.assertEqual([{"path": "apps/frontend/**", "source": "scout"}],
                         util.read_json(path)["forbidden"])

    def test_an_entry_that_already_has_provenance_is_left_alone(self):
        path = self.contract("T61", {"task": "T61", "forbidden": [
            {"path": "apps/frontend/**", "source": "plan"}]})
        self.run_migrate()
        self.assertEqual("plan", util.read_json(path)["forbidden"][0]["source"])

    def test_missing_v3_fields_get_their_defaults(self):
        path = self.contract("T62", {"task": "T62", "success_criteria": ["x"],
                                     "allow_list": ["src/a.ts"], "verification": ["true"]})
        self.run_migrate()
        doc = util.read_json(path)
        self.assertEqual([], doc["forbidden"])
        self.assertIsNone(doc["render_gate"])
        self.assertEqual([], doc["fidelity_source"])
        self.assertEqual([], doc["evaluator_must_read"])
        self.assertEqual([], doc["evaluator_must_view"])
        self.assertEqual(0, doc["estimated_diff_lines"])
        self.assertEqual(["x"], doc["success_criteria"])

    def test_the_count_lands_in_the_migration_actions(self):
        self.contract("T60", {"task": "T60", "forbidden": ["a/**"]})
        self.contract("T61", {"task": "T61", "forbidden": ["b/**"]})
        self.run_migrate()
        self.assertIn("contracts-normalised:2", self.migrations()[0]["actions"])

    def test_a_malformed_contract_is_skipped_rather_than_crashing_the_migration(self):
        util.atomic_write(os.path.join(self.rt, "sprint-T63.json"), "{not json")
        self.assertTrue(self.run_migrate().startswith("migrated:2:3:"))
        self.assertEqual("{not json",
                         util.read_text(os.path.join(self.rt, "sprint-T63.json")))

    def test_nothing_else_under_runtime_is_touched(self):
        util.write_json(os.path.join(self.rt, "worker-result.json"),
                        {"task": "T60", "status": "partial"})
        self.run_migrate()
        self.assertEqual("partial",
                         util.read_json(os.path.join(self.rt, "worker-result.json"))["status"])

    def test_an_in_flight_task_is_left_for_the_boot_rule(self):
        plan = os.path.join(self.ld, "LOOP_PLAN.md")
        util.atomic_write(plan, "## S\n- [~] T60: in flight\n- [ ] T61: next\n")
        self.run_migrate()
        self.assertEqual("## S\n- [~] T60: in flight\n- [ ] T61: next\n",
                         util.read_text(plan))


class TestMigrateOneToThree(Base):
    def setUp(self):
        Base.setUp(self)
        util.atomic_write(os.path.join(self.rt, "tickseq"), "41")
        util.atomic_write(os.path.join(self.rt, "LOCK"), "4242")

    def test_every_step_runs_and_each_stamps_before_the_next(self):
        out = self.run_migrate()
        self.assertTrue(out.startswith("migrated:1:3:"), out)
        self.assertFalse(os.path.exists(os.path.join(self.rt, "LOCK")))
        self.assertTrue(os.path.isdir(os.path.join(self.ld, "artifacts")))
        self.assertEqual([(1, 2), (2, 3)],
                         [(e["from"], e["to"]) for e in self.migrations()])

    def test_the_tick_counter_is_untouched(self):
        self.run_migrate()
        self.assertEqual(41, util.read_int(os.path.join(self.rt, "tickseq")))


class TestGuards(Base):
    def test_a_newer_dir_is_refused_and_nothing_is_touched(self):
        util.atomic_write(os.path.join(self.rt, "schema"), "99")
        self.assertEqual("newer:99", self.run_migrate())
        self.assertEqual(99, util.read_int(os.path.join(self.rt, "schema")))
        self.assertFalse(os.path.exists(os.path.join(self.ld, "LOOP_DECISIONS.md")))

    def test_a_live_one_x_harness_blocks_the_start(self):
        util.atomic_write(os.path.join(self.rt, "tickseq"), "41")
        out = self.run_migrate(legacy_live=True)
        self.assertTrue(out.startswith("blocked:"), out)
        self.assertIn("LOOP_MIGRATE_FORCE", out)
        self.assertIn("PAUSE", out)
        self.assertFalse(os.path.exists(os.path.join(self.rt, "schema")))

    def test_force_overrides_the_live_guard(self):
        util.atomic_write(os.path.join(self.rt, "tickseq"), "41")
        self.assertTrue(self.run_migrate(legacy_live=True, force=True)
                        .startswith("migrated:1:3:"))

    def test_the_guard_only_applies_to_schema_one(self):
        util.atomic_write(os.path.join(self.rt, "schema"), "2")
        self.assertTrue(self.run_migrate(legacy_live=True).startswith("migrated:2:3:"))


class TestLegacyLiveDetection(Base):
    def test_no_run_log_means_not_live(self):
        self.assertFalse(migrate.legacy_harness_live(self.ld))

    def test_a_recent_log_whose_last_line_is_not_an_exit_line_looks_live(self):
        util.atomic_write(os.path.join(self.ld, "run.log"),
                          "2026-01-01T00:00:00Z tick 41 starting\n")
        self.assertTrue(migrate.legacy_harness_live(self.ld))

    def test_a_clean_exit_line_means_not_live(self):
        util.atomic_write(os.path.join(self.ld, "run.log"),
                          "2026-01-01T00:00:00Z tick 41 starting\n"
                          "2026-01-01T00:20:00Z LOOP_DONE after 41 ticks\n")
        self.assertFalse(migrate.legacy_harness_live(self.ld))

    def test_an_old_log_means_not_live(self):
        path = os.path.join(self.ld, "run.log")
        util.atomic_write(path, "2026-01-01T00:00:00Z tick 41 starting\n")
        old = time.time() - 3600
        os.utime(path, (old, old))
        self.assertFalse(migrate.legacy_harness_live(self.ld))

    def test_an_untimestamped_stream_line_cannot_fake_an_exit(self):
        util.atomic_write(os.path.join(self.ld, "run.log"),
                          "2026-01-01T00:00:00Z tick 41 starting\n"
                          '{"type":"assistant","text":"LOOP_DONE after"}\n')
        self.assertTrue(migrate.legacy_harness_live(self.ld))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_migrate*.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'runner.migrate'`

- [ ] **Step 3: Write minimal implementation**

Create `plugins/agent-loop/runner/migrate.py`:

```python
"""LOOP_SCHEMA — the version of a loop dir's LAYOUT, not of the plugin.

Prompt, UI and harness-internal changes never bump it. `runtime/schema` holds
the stamp and the harness is its only writer. A dir with no stamp but a tick
counter predates stamping (schema 1); a dir with neither is new. Each step
stamps before the next runs, so an interrupted upgrade resumes where it stopped.
"""
from __future__ import annotations

import glob
import os
import time
from typing import Any, Dict, List

from . import util

LOOP_SCHEMA = 3

# The v3 shape of runtime/sprint-<T>.json (spec §5). A 2.x contract predates the
# last five keys and wrote `forbidden` as bare strings; both make contract.validate
# fail on the first tick after an upgrade, which is a needless needs-human.
# Spelled out here rather than imported from contract.py: migrate must keep
# importing nothing but util, so it can run before anything else is sane.
_CONTRACT_DEFAULTS = (
    ("task", ""),
    ("success_criteria", []),
    ("allow_list", []),
    ("forbidden", []),
    ("verification", []),
    ("render_gate", None),
    ("fidelity_source", []),
    ("evaluator_must_read", []),
    ("evaluator_must_view", []),
    ("estimated_diff_lines", 0),
    ("scout_notes", ""),
    ("relevant_learnings", []),
)

_CLEAN_EXIT_MARKERS = ("LOOP_DONE after", "HALT:", "PAUSE present;",
                       "re-run run.sh", "NEEDS HUMAN:")
_TIMESTAMP_LEN = len("2026-01-01T00:00:00Z ")


def schema_read(runtime_dir: str, current: int) -> int:
    path = os.path.join(runtime_dir, "schema")
    if os.path.exists(path):
        n = util.read_int(path, 0)
        if n > 0:
            return n
    if os.path.exists(os.path.join(runtime_dir, "tickseq")):
        return 1
    return current


def legacy_harness_live(loop_dir: str, recent_s: int = 120) -> bool:
    """Evidence (never proof) that a pre-2.0 harness is still running.

    1.x wrote no pid file. The signal is: run.log was modified within recent_s
    AND its last timestamped harness line is not one of 1.x's clean-exit lines.
    Raw stream lines carry no leading timestamp and are ignored, so a subagent
    that merely mentions LOOP_DONE cannot make a live harness look stopped.
    Must be evaluated before this process writes anything to run.log.
    """
    path = os.path.join(loop_dir, "run.log")
    try:
        age = time.time() - os.path.getmtime(path)
    except OSError:
        return False
    if age >= recent_s:
        return False
    last = ""
    for line in util.read_text(path).splitlines():
        if len(line) > _TIMESTAMP_LEN and line[4] == "-" and line[10] == "T" \
                and line[_TIMESTAMP_LEN - 1] == " ":
            last = line
    for marker in _CLEAN_EXIT_MARKERS:
        if marker in last:
            return False
    return True


def migrate_1_to_2(loop_dir: str, runtime_dir: str) -> str:
    """1.x -> 2.0: the tick prompt's runtime/LOCK is dead; the harness owns the mutex."""
    actions: List[str] = []
    lock = os.path.join(runtime_dir, "LOCK")
    if os.path.exists(lock):
        try:
            os.unlink(lock)
            actions.append("legacy-lock-removed")
        except OSError:
            pass
    return ",".join(actions) or "none"


def _ensure_gitignore(loop_dir: str) -> bool:
    """`artifacts/` must be ignored or the sandbox's stray-revert deletes screenshots."""
    path = os.path.join(loop_dir, ".gitignore")
    body = util.read_text(path)
    wanted = [line for line in ("runtime/", "artifacts/")
              if line not in body.splitlines()]
    if not wanted:
        return False
    parts = [body.rstrip("\n")] if body.strip() else []
    parts.extend(wanted)
    util.atomic_write(path, "\n".join(parts) + "\n")
    return True


def _normalise_contract(doc: Dict[str, Any]) -> Dict[str, Any]:
    """One 2.x sprint contract in the v3 shape. Field order follows spec §5."""
    out: Dict[str, Any] = {}
    for key, default in _CONTRACT_DEFAULTS:
        value = doc.get(key, default)
        if value is None and default is not None:
            value = default
        out[key] = value
    forbidden = []
    for item in out["forbidden"] or []:
        if isinstance(item, dict):
            forbidden.append({"path": item.get("path", ""),
                              "source": item.get("source") or "scout"})
        elif isinstance(item, str):
            # A 2.x Scout wrote bare paths. Provenance decides whether the Judge
            # may widen the constraint later, and the only honest answer for a
            # constraint the Scout invented is "scout".
            forbidden.append({"path": item, "source": "scout"})
    out["forbidden"] = forbidden
    for key in doc:
        out.setdefault(key, doc[key])
    return out


def _normalise_contracts(runtime_dir: str) -> int:
    """Rewrite every runtime/sprint-*.json in the v3 shape; return how many.

    A file that will not parse is left exactly as it is: it is evidence for the
    boot rule, and a migration that mangles evidence is worse than one that
    skips a file.
    """
    count = 0
    for path in sorted(glob.glob(os.path.join(runtime_dir, "sprint-*.json"))):
        doc = util.read_json(path)
        if not isinstance(doc, dict):
            continue
        util.write_json(path, _normalise_contract(doc))
        count += 1
    return count


def migrate_2_to_3(loop_dir: str, runtime_dir: str) -> str:
    """2.x -> 3.0: the phase runner's new durable files, plus the contract shape.

    LOOP_PLAN.md is not touched, so a `[~]` task survives the upgrade untouched
    and is reconciled by the boot rule (spec §14) on the first tick.
    """
    actions: List[str] = []
    artifacts = os.path.join(loop_dir, "artifacts")
    if not os.path.isdir(artifacts):
        os.makedirs(artifacts, exist_ok=True)
        actions.append("artifacts-dir")
    decisions = os.path.join(loop_dir, "LOOP_DECISIONS.md")
    if not os.path.exists(decisions):
        util.atomic_write(decisions, "# Loop Decisions\n\n"
                          "Autonomous choices the loop made, with their alternatives "
                          "and how to reverse them.\n")
        actions.append("decisions-file")
    log = os.path.join(loop_dir, "harness.log")
    if not os.path.exists(log):
        util.atomic_write(log, "")
        actions.append("harness-log")
    if _ensure_gitignore(loop_dir):
        actions.append("gitignore-artifacts")
    normalised = _normalise_contracts(runtime_dir)
    if normalised:
        actions.append("contracts-normalised:%d" % normalised)
    return ",".join(actions) or "none"


_STEPS = {1: migrate_1_to_2, 2: migrate_2_to_3}


def migrate_loop_dir(loop_dir: str, runtime_dir: str, events, target: int,
                     legacy_live: bool, force: bool, version: str) -> str:
    """`ok:<n>` | `migrated:<from>:<to>:<actions>` | `blocked:<detail>` | `newer:<n>`."""
    start = schema_read(runtime_dir, target)
    if start > target:
        return "newer:%d" % start
    if start == target:
        stamp = os.path.join(runtime_dir, "schema")
        if not os.path.exists(stamp):
            util.atomic_write(stamp, str(target))
        return "ok:%d" % target
    if start == 1 and legacy_live and not force:
        return ("blocked:run.log was written in the last 2 minutes and its last harness "
                "line is not a 1.x exit line, so the pre-2.0 harness may still be "
                "running. Pause it (touch %s/PAUSE and wait for its terminal to exit), "
                "kill its dashboard, then re-run; or re-run with LOOP_MIGRATE_FORCE=1 if "
                "you are certain it is dead" % runtime_dir)
    current = start
    actions = "none"
    while current < target:
        step = _STEPS.get(current)
        actions = step(loop_dir, runtime_dir) if step else "none"
        current += 1
        util.atomic_write(os.path.join(runtime_dir, "schema"), str(current))
        events.emit("migration", **{"from": current - 1, "to": current,
                                    "actions": actions, "plugin_version": version})
    return "migrated:%d:%d:%s" % (start, target, actions)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_*.py' -v`
Expected: PASS — 354 tests, `OK`

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/runner/migrate.py plugins/agent-loop/tests/runner/test_migrate.py
git commit -F - <<'EOF'
agent-loop: loop-dir schema 3, migrated in place from 1 or 2

2 -> 3 creates artifacts/, LOOP_DECISIONS.md and harness.log, adds artifacts/
to the per-run .gitignore so the sandbox's stray-revert can never delete a
screenshot between phases, and normalises every existing sprint-*.json into the
v3 shape: bare `forbidden` strings become {"path", "source": "scout"} and
missing fields get defaults, so a 2.x contract does not fail provenance
validation on the first tick after an upgrade. LOOP_PLAN.md is untouched, so a
[~] task survives and the boot rule reconciles it. The 1.x live-harness guard,
the newer-dir refusal and the per-step stamping all survive from lib/migrate.sh,
with the same four return tokens run.sh branches on.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 17: `sidecar.py` — one dashboard per loop dir

**Files:**
- Create: `plugins/agent-loop/runner/sidecar.py`
- Test: `plugins/agent-loop/tests/runner/test_sidecar.py`

**Interfaces:**
- Consumes: `util.read_json`, `util.process_alive`.
- Produces: `adoptable(runtime_dir) -> Optional[str]`, `start(plugin_root, loop_dir, runtime_dir, cmd_override=None, wait_s=5) -> Optional[str]`, `stop(runtime_dir)`, `Supervisor(runtime_dir, plugin_root, loop_dir, on_crashloop, interval=10, max_restarts=5)` with `.start()`/`.stop()`.
- `LOOP_DASHBOARD_CMD` overrides the spawn command line (the e2e points it at `tests/fixtures/dashboard-stub`).
- `Supervisor` takes an `on_crashloop(count)` callback instead of importing `incidents`, so the dependency arrow stays one-way.
- Adoption ignores the `Dashboard:` setting: a live standalone dashboard is always adopted, because `Dashboard:` decides only whether to *spawn*.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_sidecar.py`:

```python
import _path  # noqa: F401
import os
import subprocess
import tempfile
import time
import unittest

from runner import sidecar, util

STUB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "fixtures", "dashboard-stub")


class Base(unittest.TestCase):
    def setUp(self):
        self.rt = tempfile.mkdtemp()
        self.spawned = []

    def tearDown(self):
        for proc in self.spawned:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
        sidecar.stop(self.rt)

    def fake_serve(self):
        """A live process whose command line contains serve.py."""
        proc = subprocess.Popen(["bash", "-c", "exec -a serve.py sleep 60"])
        self.spawned.append(proc)
        time.sleep(0.2)
        return proc


class TestAdoptable(Base):
    def test_a_live_standalone_dashboard_is_adopted(self):
        proc = self.fake_serve()
        util.write_json(os.path.join(self.rt, "dashboard.json"),
                        {"pid": proc.pid, "port": 7, "url": "http://127.0.0.1:7",
                         "sidecar": False})
        self.assertEqual("http://127.0.0.1:7", sidecar.adoptable(self.rt))

    def test_a_sidecar_record_is_never_adopted(self):
        proc = self.fake_serve()
        util.write_json(os.path.join(self.rt, "dashboard.json"),
                        {"pid": proc.pid, "url": "http://127.0.0.1:9", "sidecar": True})
        self.assertIsNone(sidecar.adoptable(self.rt))

    def test_a_dead_pid_is_not_adopted(self):
        util.write_json(os.path.join(self.rt, "dashboard.json"),
                        {"pid": 999999, "url": "http://x", "sidecar": False})
        self.assertIsNone(sidecar.adoptable(self.rt))

    def test_a_pid_that_is_not_serve_py_is_not_adopted(self):
        util.write_json(os.path.join(self.rt, "dashboard.json"),
                        {"pid": os.getpid(), "url": "http://x", "sidecar": False})
        self.assertIsNone(sidecar.adoptable(self.rt))

    def test_no_record_and_a_garbage_record_are_both_none(self):
        self.assertIsNone(sidecar.adoptable(self.rt))
        util.atomic_write(os.path.join(self.rt, "dashboard.json"), "{nope")
        self.assertIsNone(sidecar.adoptable(self.rt))


class TestStartStop(Base):
    def test_it_spawns_the_override_records_the_pid_and_returns_the_url(self):
        url = sidecar.start("/plugin", "ld", self.rt, cmd_override=STUB, wait_s=10)
        self.assertEqual("http://127.0.0.1:1", url)
        pid = util.read_int(os.path.join(self.rt, "sidecar.pid"))
        self.assertTrue(util.process_alive(pid))
        sidecar.stop(self.rt)
        time.sleep(0.3)
        self.assertFalse(util.process_alive(pid))
        self.assertFalse(os.path.exists(os.path.join(self.rt, "sidecar.pid")))

    def test_a_command_that_announces_nothing_returns_none(self):
        self.assertIsNone(sidecar.start("/plugin", "ld", self.rt,
                                        cmd_override="sleep 5", wait_s=1))

    def test_the_previous_port_is_reused_so_an_open_tab_reconnects(self):
        util.write_json(os.path.join(self.rt, "dashboard.json"), {"port": 8899})
        argv = sidecar.build_argv("/plugin", "ld", self.rt, None)
        self.assertEqual("8899", argv[argv.index("--port") + 1])
        self.assertIn("--sidecar", argv)
        self.assertIn("--no-spawn", argv)
        self.assertTrue(argv[1].endswith(os.path.join("web", "serve.py")))

    def test_stop_is_safe_with_no_sidecar(self):
        sidecar.stop(self.rt)


class TestSupervisor(Base):
    def test_it_respawns_a_dead_sidecar(self):
        sidecar.start("/plugin", "ld", self.rt, cmd_override=STUB, wait_s=10)
        first = util.read_int(os.path.join(self.rt, "sidecar.pid"))
        sup = sidecar.Supervisor(self.rt, "/plugin", "ld", lambda n: None,
                                 interval=0.2, cmd_override=STUB)
        sup.start()
        try:
            os.kill(first, 9)
            deadline = time.time() + 10
            second = first
            while time.time() < deadline and second == first:
                time.sleep(0.2)
                second = util.read_int(os.path.join(self.rt, "sidecar.pid"))
            self.assertNotEqual(first, second)
            self.assertTrue(util.process_alive(second))
        finally:
            sup.stop()

    def test_the_restart_ceiling_reports_a_crashloop_and_gives_up(self):
        seen = []
        sup = sidecar.Supervisor(self.rt, "/plugin", "ld", seen.append,
                                 interval=0.05, max_restarts=2, cmd_override="true")
        sup.start()
        deadline = time.time() + 10
        while time.time() < deadline and not seen:
            time.sleep(0.1)
        sup.stop()
        self.assertEqual([3], seen)

    def test_stop_takes_the_sidecar_down_with_it(self):
        sup = sidecar.Supervisor(self.rt, "/plugin", "ld", lambda n: None,
                                 interval=0.2, cmd_override=STUB)
        sidecar.start("/plugin", "ld", self.rt, cmd_override=STUB, wait_s=10)
        pid = util.read_int(os.path.join(self.rt, "sidecar.pid"))
        sup.start()
        sup.stop()
        time.sleep(0.3)
        self.assertFalse(util.process_alive(pid))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_sidecar*.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'runner.sidecar'`

- [ ] **Step 3: Write minimal implementation**

Create `plugins/agent-loop/runner/sidecar.py`:

```python
"""One dashboard per loop dir: adopt a live standalone one, else spawn a sidecar.

A standalone dashboard (the one /agent-loop opened, or the one whose Start
button spawned this harness) outlives any single harness and is never killed on
exit. A sidecar belongs to this harness and dies with it. A dead sidecar is a
warn-level fact, never a reason to stop the loop.
"""
from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import threading
from typing import Callable, List, Optional

from . import util


def adoptable(runtime_dir: str) -> Optional[str]:
    """The URL of a live, non-sidecar dashboard for this loop dir, else None."""
    record = util.read_json(os.path.join(runtime_dir, "dashboard.json"))
    if not isinstance(record, dict):
        return None
    if record.get("sidecar"):
        return None
    url = record.get("url")
    if not url:
        return None
    if not util.process_alive(record.get("pid"), "serve.py"):
        return None
    return url


def build_argv(plugin_root: str, loop_dir: str, runtime_dir: str,
               cmd_override: Optional[str]) -> List[str]:
    """The sidecar's command line, reusing the previous port when there was one."""
    if cmd_override:
        return shlex.split(cmd_override)
    record = util.read_json(os.path.join(runtime_dir, "dashboard.json")) or {}
    port = record.get("port") or 0
    return [sys.executable, os.path.join(plugin_root, "web", "serve.py"),
            "--sidecar", "--no-spawn", "--loop-dir", loop_dir, "--port", str(port)]


def start(plugin_root: str, loop_dir: str, runtime_dir: str,
          cmd_override: Optional[str] = None, wait_s: float = 5.0) -> Optional[str]:
    """Spawn the observer and return the URL it announces, or None in time."""
    os.makedirs(runtime_dir, exist_ok=True)
    out_path = os.path.join(runtime_dir, "dashboard.out")
    argv = build_argv(plugin_root, loop_dir, runtime_dir,
                      cmd_override or os.environ.get("LOOP_DASHBOARD_CMD"))
    try:
        with open(out_path, "w") as out:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, start_new_session=True)
    except OSError:
        return None
    util.atomic_write(os.path.join(runtime_dir, "sidecar.pid"), str(proc.pid))

    stop_at = wait_s
    step = 0.25
    waited = 0.0
    while waited < stop_at:
        for line in util.read_text(out_path).splitlines():
            if "dashboard-started" not in line:
                continue
            try:
                url = json.loads(line).get("url")
            except ValueError:
                continue
            if url:
                return url
        threading.Event().wait(step)
        waited += step
    return None


def stop(runtime_dir: str) -> None:
    """Kill this harness's sidecar, if it has one. Never raises."""
    path = os.path.join(runtime_dir, "sidecar.pid")
    pid = util.read_int(path, 0)
    if pid > 0:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    try:
        os.unlink(path)
    except OSError:
        pass


class Supervisor(object):
    """Restarts a dead sidecar, up to a ceiling, then reports a crash loop."""

    def __init__(self, runtime_dir: str, plugin_root: str, loop_dir: str,
                 on_crashloop: Callable[[int], None], interval: float = 10.0,
                 max_restarts: int = 5, cmd_override: Optional[str] = None):
        self.runtime_dir = runtime_dir
        self.plugin_root = plugin_root
        self.loop_dir = loop_dir
        self.on_crashloop = on_crashloop
        self.interval = interval
        self.max_restarts = max_restarts
        self.cmd_override = cmd_override
        self.restarts = 0
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="sidecar-supervisor")
        self.thread.daemon = True

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            pid = util.read_int(os.path.join(self.runtime_dir, "sidecar.pid"), 0)
            if pid > 0 and util.process_alive(pid):
                continue
            self.restarts += 1
            if self.restarts > self.max_restarts:
                self.on_crashloop(self.restarts)
                return
            start(self.plugin_root, self.loop_dir, self.runtime_dir,
                  cmd_override=self.cmd_override, wait_s=2)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)
        stop(self.runtime_dir)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_*.py' -v`
Expected: PASS — 368 tests, `OK`

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/runner/sidecar.py plugins/agent-loop/tests/runner/test_sidecar.py
git commit -F - <<'EOF'
agent-loop: dashboard adoption, spawn and supervision as a Python module

Same rule as v2: a live non-sidecar dashboard is adopted and outlives the
harness, a sidecar record never is, and the previous port is reused so an open
tab's SSE reconnect lands on the same origin. The supervisor is a daemon thread
that reports a crash loop through a callback rather than importing incidents.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 18: `run.py` part 1 — the three tick bodies, the sandbox and the failure stand-in

**Files:**
- Create: `plugins/agent-loop/runner/run.py` (this task adds `Harness`, `TickOutcome`, `pause_state`, `sandbox`, `commit_task`, `checkpoint_note`, `boot_reconcile`, `execute_tick`, `plan_tick`, `review_tick`, `run_worker_attempt`, `failure_signature`, `handle_failure`, `mark_blocked`; Task 19 adds `main` and the outer loop)
- Modify: `plugins/agent-loop/runner/claude_proc.py` (a `stop_check` hook so `runtime/STOP` kills the phase in flight)
- Modify: `plugins/agent-loop/runner/phases.py` (`_dispatch` passes `stop_check`; a stopped phase is never re-asked)
- Test: `plugins/agent-loop/tests/runner/test_run_tick.py`

**Interfaces:**
- Consumes: everything below it.
- Produces: `Harness` (the per-run state record, now carrying `resume_task`/`resume_phase` and `take_resume`), `TickOutcome(verdict, cause, task_id, sha, results, gates)`, `pause_state(h) -> str`, `sandbox(h, contract) -> List[str]`, `verification_summary(cfg, ok)`, `commit_task(h, task, contract) -> str`, `checkpoint_note(h, task)`, `boot_reconcile(h)`, `run_worker_attempt(h, ctx, contract, attempt, findings="", tier=None)`, `failure_signature(reason, phase, task_id)`, `handle_failure(h, ctx, reason, evidence)`, `mark_blocked(h, task, reason, evidence)`, `execute_tick(h, tick, task)`, `plan_tick(h, tick, segment)`, `review_tick(h, tick, segment)`.
- Attempt bookkeeping is `task_state.TaskState` (Task 10), not a `record_attempt` helper: `execute_tick` calls `begin_attempt`/`begin_phase`/`end_phase`/`end_attempt` around every phase, so `runtime/task-<T>.json` is current at every transition and a kill costs at most the phase in flight.
- **The failure stand-in is a SAME-TIER re-dispatch.** NEEDS_WORK or a failed gate on attempt 1 → one more Worker at the same tier with the Evaluator's findings in its prompt; a BLOCKER, or a second failure of any kind → `[!]` + `LOOP_CLEANUP.md` + the blocked-upstream fan-out. No tier ladder: spec §11 item 4.
- **Plan C replaces the body of `handle_failure`** with a `judge.decide(...)` call and the retry branch in `execute_tick` with the Judge's decision (bounded by `cfg.limits["max_attempts"]`); both sites are marked `# plan C`. `mark_blocked` survives as the `defer`/`halt` implementation.
- **The boot rule (spec §14) lives in `boot_reconcile`,** called once from `run_loop` before the first tick. A `[~]` task with a state file resumes at the recorded phase (Worker-complete → GATE, no second Worker budget; killed inside WORK → the wrap-up/resume hook, a stub in plan A). A `[~]` task with **no** state file gets the mechanical branch: keep in-`allow_list` changes, revert strays, re-run from SCOUT, and log a `boot-reconcile` warning — marked `# plan C: route to the Judge`.
- `PhaseResult` gains `stopped: bool`.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_run_tick.py`:

````python
import _path  # noqa: F401
import json
import os
import subprocess
import tempfile
import unittest

from runner import claude_proc, config, contract as cmod, incidents, phases
from runner import plan as plan_mod, run, status, task_state, util
from runner.claude_proc import PhaseResult
from runner.events import EventLog, UsageLog

PLAN = """## Segment A: wiring
- [ ] T1: Add the parser
- [ ] T2: Add the writer | depends_on: T1
- [ ] T3: Rename the import | mechanical
"""


def sh(cwd, *args):
    subprocess.run(list(args), cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def block(obj):
    return "ok\n```json\n%s\n```" % json.dumps(obj)


class Replies(object):
    """Replays scripted phase replies and records the kwargs each phase got."""

    def __init__(self, by_phase):
        self.by_phase = dict(by_phase)
        self.calls = []

    def __call__(self, **kw):
        self.calls.append(kw)
        # claude_proc.run_phase is what emits role_start/role_end in production and
        # this stands in for it — without these two lines every role_start
        # assertion below passes vacuously against an empty list.
        kw["events"].emit("role_start", role=kw["role"], model=kw["model"],
                          desc=kw.get("desc", ""), tick=kw.get("tick", 0))
        script = self.by_phase.get(kw["phase"], [])
        reply = script.pop(0) if script else {}
        if reply.get("side_effect"):
            reply["side_effect"]()
        kw["events"].emit("role_end", role=kw["role"])
        return PhaseResult(phase=kw["phase"], model=kw["model"], rc=0,
                           killed=reply.get("killed", False),
                           session_id="sess-%s" % kw["phase"],
                           result_text=reply.get("text", ""),
                           usage_by_model=reply.get("usage", {}))


class Base(unittest.TestCase):
    def setUp(self):
        self.wt = tempfile.mkdtemp()
        sh(self.wt, "git", "init", "-q")
        sh(self.wt, "git", "config", "user.email", "loop@example.com")
        sh(self.wt, "git", "config", "user.name", "Loop")
        sh(self.wt, "git", "config", "commit.gpgsign", "false")
        os.makedirs(os.path.join(self.wt, "src"))
        with open(os.path.join(self.wt, "src", "a.ts"), "w") as f:
            f.write("export const a = 0\n")
        sh(self.wt, "git", "add", "-A")
        sh(self.wt, "git", "commit", "-q", "-m", "seed")

        self.loop_dir = os.path.join(self.wt, ".claude", "loop", "run")
        self.runtime = os.path.join(self.loop_dir, "runtime")
        os.makedirs(self.runtime)
        util.atomic_write(os.path.join(self.loop_dir, ".gitignore"),
                          "runtime/\nartifacts/\n")
        self.plan_path = os.path.join(self.loop_dir, "LOOP_PLAN.md")
        util.atomic_write(self.plan_path, PLAN)
        cfg_path = os.path.join(self.loop_dir, "LOOP_CONFIG.md")
        util.atomic_write(cfg_path,
                          "Worktree: %s\nVerification pipeline: lint test\n"
                          "Tiers: cheap=tiny standard=mid most-capable=big\n" % self.wt)
        self.cfg = config.load_config(cfg_path)
        self.events_path = os.path.join(self.loop_dir, "events.jsonl")
        self.h = run.Harness(
            cfg=self.cfg, plugin_root="/plugin", loop_dir=self.loop_dir,
            runtime_dir=self.runtime, worktree=self.wt, config_path=cfg_path,
            plan_path=self.plan_path,
            events=EventLog(self.events_path, os.path.join(self.runtime, "eventseq")),
            usage=UsageLog(os.path.join(self.loop_dir, "LOOP_USAGE.jsonl")),
            log=run.Log(os.path.join(self.loop_dir, "harness.log")),
            plan=plan_mod.Plan.load(self.plan_path),
            medic=incidents.MedicState())
        self._real = claude_proc.run_phase

    def tearDown(self):
        claude_proc.run_phase = self._real

    def patch(self, by_phase):
        rec = Replies(by_phase)
        claude_proc.run_phase = rec
        return rec

    def emitted(self, type_):
        out = []
        if not os.path.exists(self.events_path):
            return out
        with open(self.events_path) as f:
            for line in f:
                rec = json.loads(line)
                if rec["type"] == type_:
                    out.append(rec)
        return out

    def contract_writer(self, task_id="T1", **over):
        def write():
            data = {"task": task_id, "success_criteria": ["src/a.ts exports parse"],
                    "allow_list": ["src/a.ts"], "verification": ["true"],
                    "forbidden": [], "scout_notes": "n"}
            data.update(over)
            util.write_json(os.path.join(self.runtime, "sprint-%s.json" % task_id), data)
        return write

    def worker_edit(self, path="src/a.ts", text="export const parse = () => 1\n"):
        def write():
            full = os.path.join(self.wt, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as f:
                f.write(text)
        return write


class TestPauseState(Base):
    def test_it_reports_stop_pause_or_nothing(self):
        self.assertEqual("", run.pause_state(self.h))
        util.atomic_write(os.path.join(self.runtime, "PAUSE"), "")
        self.assertEqual("pause", run.pause_state(self.h))
        util.atomic_write(os.path.join(self.runtime, "STOP"), "")
        self.assertEqual("stop", run.pause_state(self.h))


class TestSandbox(Base):
    def test_a_stray_outside_allow_list_is_reverted(self):
        contract = cmod.Contract(task="T1", allow_list=["src/a.ts"])
        self.worker_edit()()
        self.worker_edit("src/b.ts", "stray\n")()
        stray = run.sandbox(self.h, contract)
        self.assertEqual(["src/b.ts"], stray)
        self.assertFalse(os.path.exists(os.path.join(self.wt, "src", "b.ts")))
        self.assertTrue(os.path.exists(os.path.join(self.wt, "src", "a.ts")))

    def test_the_loop_dir_is_never_treated_as_a_stray(self):
        contract = cmod.Contract(task="T1", allow_list=["src/a.ts"])
        util.atomic_write(os.path.join(self.loop_dir, "LOOP_LEARNINGS.md"), "x\n")
        self.assertEqual([], run.sandbox(self.h, contract))
        self.assertTrue(os.path.exists(os.path.join(self.loop_dir, "LOOP_LEARNINGS.md")))


class TestExecuteTickHappyPath(Base):
    def setUp(self):
        Base.setUp(self)
        self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer()}],
            "worker": [{"text": block({"status": "complete", "summary": "did it"}),
                        "side_effect": self.worker_edit()}],
            "evaluator": [{"text": block({"verdict": "PASS", "findings": [],
                                          "views": [], "summary": "good"})}],
            "learner": [{"text": block({"patterns": [], "log": "fine",
                                        "invariants": []})}]})
        self.outcome = run.execute_tick(self.h, 1, self.h.plan.task("T1"))

    def test_the_verdict_is_continue_and_the_work_is_committed(self):
        self.assertEqual("continue", self.outcome.verdict)
        self.assertEqual(40, len(self.outcome.sha))
        body = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=self.wt,
                              stdout=subprocess.PIPE).stdout.decode()
        self.assertIn("loop(T1): Add the parser", body)
        self.assertIn("Loop-Status: done", body)
        self.assertIn("Loop-Verification: lint=pass test=pass", body)
        self.assertIn("Loop-Files: src/a.ts", body)

    def test_the_row_is_marked_done_with_the_sha(self):
        reloaded = plan_mod.Plan.load(self.plan_path)
        self.assertEqual("done", reloaded.task("T1").state)
        self.assertEqual(self.outcome.sha[:7], reloaded.task("T1").sha)

    def test_a_task_status_event_carries_the_sha(self):
        ev = self.emitted("task_status")[0]
        self.assertEqual("T1", ev["id"])
        self.assertEqual("done", ev["status"])
        self.assertEqual(self.outcome.sha[:7], ev["sha"])

    def test_the_task_state_file_records_the_attempt(self):
        doc = util.read_json(os.path.join(self.runtime, "task-T1.json"))
        self.assertEqual("T1", doc["task"])
        self.assertEqual(1, doc["attempt"])
        self.assertEqual(1, len(doc["attempts"]))
        self.assertEqual("pass", doc["attempts"][0]["outcome"])
        self.assertEqual("standard", doc["attempts"][0]["tier"])
        self.assertEqual("runtime/sprint-T1.json", doc["contract"])
        phases_seen = [p["phase"] for p in doc["attempts"][0]["phase_results"]]
        self.assertEqual(["scout", "worker", "evaluator", "learner"], phases_seen)

    def test_the_recorded_phase_ends_on_the_last_transition(self):
        self.assertEqual("LEARN:done",
                         util.read_json(os.path.join(self.runtime, "task-T1.json"))["phase"])

    def test_nothing_writes_the_old_attempts_file(self):
        self.assertFalse(os.path.exists(os.path.join(self.runtime, "attempts-T1.json")))

    def test_the_learner_ran_after_the_commit(self):
        self.assertTrue(os.path.exists(os.path.join(self.loop_dir, "LOOP_LEARNINGS.md")))


class TestExecuteTickRetry(Base):
    def test_needs_work_re_dispatches_at_the_same_tier_with_the_findings(self):
        rec = self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer()}],
            "worker": [{"text": block({"status": "complete", "summary": "one"}),
                        "side_effect": self.worker_edit()},
                       {"text": block({"status": "complete", "summary": "two"}),
                        "side_effect": self.worker_edit(text="export const parse = () => 2\n")}],
            "evaluator": [{"text": block({"verdict": "NEEDS_WORK",
                                          "summary": "the parser is still a stub"})},
                          {"text": block({"verdict": "PASS", "summary": "good"})}],
            "learner": [{"text": block({"patterns": [], "log": "", "invariants": []})}]})
        outcome = run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        self.assertEqual("continue", outcome.verdict)
        workers = [c for c in rec.calls if c["phase"] == "worker"]
        self.assertEqual(["mid", "mid"], [c["model"] for c in workers])
        roles = [e["model"] for e in self.emitted("role_start") if e["role"] == "Worker"]
        self.assertEqual(["mid", "mid"], roles)
        self.assertNotIn("still a stub", workers[0]["prompt"])
        self.assertIn("the parser is still a stub", workers[1]["prompt"])
        self.assertEqual("retry", self.emitted("decision")[0]["decision"])
        doc = util.read_json(os.path.join(self.runtime, "task-T1.json"))
        self.assertEqual(["standard", "standard"], [a["tier"] for a in doc["attempts"]])
        self.assertEqual(["needs-work", "pass"], [a["outcome"] for a in doc["attempts"]])

    def test_a_second_failure_blocks_the_task(self):
        self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer()}],
            "worker": [{"text": block({"status": "partial", "summary": "one"}),
                        "side_effect": self.worker_edit()},
                       {"text": block({"status": "partial", "summary": "two"}),
                        "side_effect": self.worker_edit()}],
            "evaluator": [{"text": block({"verdict": "NEEDS_WORK", "summary": "thin"})},
                          {"text": block({"verdict": "NEEDS_WORK", "summary": "still thin"})}]})
        outcome = run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        self.assertEqual("continue", outcome.verdict)      # continue-independent
        reloaded = plan_mod.Plan.load(self.plan_path)
        self.assertEqual("blocked", reloaded.task("T1").state)
        self.assertEqual("blocked-upstream", reloaded.task("T2").state)

    def test_a_blocker_verdict_blocks_immediately_without_a_second_worker(self):
        rec = self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer()}],
            "worker": [{"text": block({"status": "complete", "summary": "one"}),
                        "side_effect": self.worker_edit()}],
            "evaluator": [{"text": block({"verdict": "BLOCKER",
                                          "summary": "the test was weakened"})}]})
        run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        self.assertEqual(1, len([c for c in rec.calls if c["phase"] == "worker"]))
        self.assertEqual("blocked", plan_mod.Plan.load(self.plan_path).task("T1").state)


class TestBlockedBookkeeping(Base):
    def setUp(self):
        Base.setUp(self)
        self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer()}],
            "worker": [{"text": block({"status": "complete", "summary": ""}),
                        "side_effect": self.worker_edit()}],
            "evaluator": [{"text": block({"verdict": "BLOCKER",
                                          "summary": "stubbed the requirement"})}]})
        self.outcome = run.execute_tick(self.h, 1, self.h.plan.task("T1"))

    def test_the_cleanup_entry_names_the_task_and_the_evidence(self):
        body = util.read_text(os.path.join(self.loop_dir, "LOOP_CLEANUP.md"))
        self.assertIn("T1", body)
        self.assertIn("stubbed the requirement", body)
        self.assertIn("decision", body.lower())

    def test_the_tree_is_returned_to_clean(self):
        with open(os.path.join(self.wt, "src", "a.ts")) as f:
            self.assertEqual("export const a = 0\n", f.read())

    def test_the_halt_commit_carries_the_trailer(self):
        body = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=self.wt,
                              stdout=subprocess.PIPE).stdout.decode()
        self.assertIn("Loop-Status: halted", body)

    def test_a_decision_event_records_the_deferral(self):
        ev = self.emitted("decision")[-1]
        self.assertEqual("T1", ev["task"])
        self.assertEqual("defer", ev["decision"])

    def test_halt_policy_turns_the_block_into_a_halt_verdict(self):
        # T3 is `| mechanical`, so the gate is its only judge: only a gate that
        # fails on both attempts drives it into handle_failure. With a passing
        # gate this task is a PASS and the assertion below never sees a halt.
        self.h.cfg.blocker_policy = "halt"
        self.h.plan = plan_mod.Plan.load(self.plan_path)
        self.patch({"scout": [{"text": block({"contract_path": "x", "notes": ""}),
                               "side_effect": self.contract_writer(
                                   "T3", verification=["exit 1"])}],
                    "worker": [{"text": block({"status": "complete", "summary": ""})},
                               {"text": block({"status": "complete", "summary": ""})}],
                    "evaluator": []})
        outcome = run.execute_tick(self.h, 2, self.h.plan.task("T3"))
        self.assertEqual("halt", outcome.verdict)
        self.assertEqual("blocked", plan_mod.Plan.load(self.plan_path).task("T3").state)


class TestContractRejection(Base):
    def test_an_invalid_contract_is_re_scouted_once_then_blocks(self):
        rec = self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer(allow_list=[])},
                      {"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer(allow_list=[])}],
            "worker": []})
        outcome = run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        self.assertEqual(2, len([c for c in rec.calls if c["phase"] == "scout"]))
        self.assertEqual(0, len([c for c in rec.calls if c["phase"] == "worker"]))
        self.assertEqual("blocked", plan_mod.Plan.load(self.plan_path).task("T1").state)
        self.assertIn("allow_list", util.read_text(
            os.path.join(self.loop_dir, "LOOP_CLEANUP.md")))
        self.assertEqual("continue", outcome.verdict)

    def test_the_second_scout_is_told_what_was_wrong(self):
        rec = self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer(allow_list=[])},
                      {"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer()}],
            "worker": [{"text": block({"status": "complete", "summary": ""}),
                        "side_effect": self.worker_edit()}],
            "evaluator": [{"text": block({"verdict": "PASS", "summary": ""})}],
            "learner": [{"text": block({"patterns": [], "log": "", "invariants": []})}]})
        run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        second = [c for c in rec.calls if c["phase"] == "scout"][1]
        self.assertIn("allow_list is empty", second["prompt"])


class TestGateFailure(Base):
    def test_a_failing_gate_re_dispatches_then_blocks(self):
        rec = self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer(verification=["exit 1"])}],
            "worker": [{"text": block({"status": "complete", "summary": ""}),
                        "side_effect": self.worker_edit()},
                       {"text": block({"status": "complete", "summary": ""}),
                        "side_effect": self.worker_edit()}],
            "evaluator": []})
        run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        self.assertEqual(2, len([c for c in rec.calls if c["phase"] == "worker"]))
        self.assertEqual(0, len([c for c in rec.calls if c["phase"] == "evaluator"]))
        self.assertEqual("blocked", plan_mod.Plan.load(self.plan_path).task("T1").state)
        self.assertIn("exit 1", util.read_text(
            os.path.join(self.loop_dir, "LOOP_CLEANUP.md")))


class TestMechanicalTask(Base):
    def test_a_mechanical_task_skips_the_evaluator(self):
        rec = self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer("T3")}],
            "worker": [{"text": block({"status": "complete", "summary": ""}),
                        "side_effect": self.worker_edit()}],
            "learner": [{"text": block({"patterns": [], "log": "", "invariants": []})}]})
        outcome = run.execute_tick(self.h, 1, self.h.plan.task("T3"))
        self.assertEqual("continue", outcome.verdict)
        self.assertEqual(0, len([c for c in rec.calls if c["phase"] == "evaluator"]))


class TestPauseBetweenPhases(Base):
    def test_pause_after_the_scout_stops_the_tick_cleanly(self):
        def pause_then_write():
            self.contract_writer()()
            util.atomic_write(os.path.join(self.runtime, "PAUSE"), "")
        rec = self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": pause_then_write}],
            "worker": []})
        outcome = run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        self.assertEqual("paused", outcome.verdict)
        self.assertEqual(0, len([c for c in rec.calls if c["phase"] == "worker"]))


class TestPlanAndReviewTicks(Base):
    def test_a_plan_tick_commits_the_new_rows(self):
        util.atomic_write(self.plan_path, PLAN + "\n## Segment B: polish\n")
        self.h.plan = plan_mod.Plan.load(self.plan_path)

        def add_rows():
            with open(self.plan_path, "a") as f:
                f.write("- [ ] T4: Tidy the exports\n")
        self.patch({"planner": [{"text": block({"tasks_added": 1}),
                                 "side_effect": add_rows}]})
        outcome = run.plan_tick(self.h, 1, "Segment B: polish")
        self.assertEqual("continue", outcome.verdict)
        body = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=self.wt,
                              stdout=subprocess.PIPE).stdout.decode()
        self.assertIn("loop: plan Segment B: polish", body)
        self.assertEqual("Segment B: polish",
                         plan_mod.Plan.load(self.plan_path).task("T4").segment)

    def test_a_plan_tick_that_adds_nothing_is_stuck_not_a_loop(self):
        util.atomic_write(self.plan_path, PLAN + "\n## Segment B: polish\n")
        self.h.plan = plan_mod.Plan.load(self.plan_path)
        self.patch({"planner": [{"text": block({"tasks_added": 0})}]})
        self.assertEqual("halt", run.plan_tick(self.h, 1, "Segment B: polish").verdict)

    def test_a_review_tick_stamps_the_segment_and_appends_follow_ups(self):
        util.atomic_write(self.plan_path,
                          "## Segment A: wiring\n- [x] T1: Add the parser\n")
        self.h.plan = plan_mod.Plan.load(self.plan_path)
        self.patch({"reviewer": [{"text": block({"findings": [
            {"severity": "must-fix", "title": "no tests", "detail": "none at all",
             "follow_up_row": "- [ ] T9: Add tests for the parser"},
            {"severity": "nit", "title": "naming", "detail": "meh",
             "follow_up_row": "- [ ] T10: rename"}]})}]})
        outcome = run.review_tick(self.h, 1, "Segment A: wiring")
        self.assertEqual("continue", outcome.verdict)
        reloaded = plan_mod.Plan.load(self.plan_path)
        self.assertTrue(reloaded.segments()[0].reviewed_sha)
        self.assertIsNotNone(reloaded.task("T9"))
        self.assertIsNone(reloaded.task("T10"))          # nits are not rows
        body = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=self.wt,
                              stdout=subprocess.PIPE).stdout.decode()
        self.assertIn("Loop-Status: reviewed", body)


class TestBootReconcile(Base):
    """Spec §14: a `[~]` row means a harness died inside the task. eligible()
    only returns pending rows, so without this the plan reads as `stuck` forever."""

    def mark_doing(self, task_id="T1"):
        self.h.plan.set_state(task_id, "doing")
        self.h.plan.save()
        self.h.plan = plan_mod.Plan.load(self.plan_path)

    def state_for(self, task_id="T1"):
        state = task_state.TaskState.load(self.runtime, task_id)
        state.begin_attempt("standard")
        return state

    def test_a_clean_plan_is_left_alone(self):
        run.boot_reconcile(self.h)
        self.assertEqual("", self.h.resume_task)
        self.assertEqual("pending", plan_mod.Plan.load(self.plan_path).task("T1").state)

    def test_a_worker_complete_task_resumes_at_the_gate(self):
        self.mark_doing()
        self.contract_writer()()
        state = self.state_for()
        state.begin_phase("WORK")
        state.end_phase(PhaseResult(phase="worker", model="mid", rc=0,
                                    session_id="sess-w"))
        run.boot_reconcile(self.h)
        self.assertEqual(("T1", "gate"), (self.h.resume_task, self.h.resume_phase))
        self.assertEqual("pending", plan_mod.Plan.load(self.plan_path).task("T1").state)

        self.worker_edit()()                      # the killed Worker's work
        rec = self.patch({
            "evaluator": [{"text": block({"verdict": "PASS", "findings": [],
                                          "views": [], "summary": "good"})}],
            "learner": [{"text": block({"patterns": [], "log": "", "invariants": []})}]})
        outcome = run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        self.assertEqual("continue", outcome.verdict)
        self.assertEqual([], [c for c in rec.calls if c["phase"] in ("scout", "worker")])

    def test_a_task_killed_inside_work_re_dispatches_with_its_checkpoint(self):
        self.mark_doing()
        self.contract_writer()()
        util.write_json(os.path.join(self.runtime, "worker-result.json"),
                        {"task": "T1", "status": "partial", "files_touched": ["src/a.ts"],
                         "summary": "half done",
                         "checkpoint": "renamed the export, tests not updated yet",
                         "next_steps": ["update the tests"]})
        self.state_for().begin_phase("WORK")
        run.boot_reconcile(self.h)
        self.assertEqual(("T1", "wrapup"), (self.h.resume_task, self.h.resume_phase))

        rec = self.patch({
            "worker": [{"text": block({"status": "complete", "summary": "finished it"}),
                        "side_effect": self.worker_edit()}],
            "evaluator": [{"text": block({"verdict": "PASS", "findings": [],
                                          "views": [], "summary": "good"})}],
            "learner": [{"text": block({"patterns": [], "log": "", "invariants": []})}]})
        run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        workers = [c for c in rec.calls if c["phase"] == "worker"]
        self.assertEqual(1, len(workers))
        self.assertIn("renamed the export", workers[0]["prompt"])
        self.assertEqual([], [c for c in rec.calls if c["phase"] == "scout"])

    def test_no_state_file_keeps_allow_list_work_reverts_strays_and_re_scouts(self):
        self.mark_doing()
        self.contract_writer()()                 # allow_list = ["src/a.ts"]
        self.worker_edit()()                     # inside it: kept
        self.worker_edit("src/stray.ts", "stray\n")()   # outside it: reverted
        run.boot_reconcile(self.h)
        self.assertEqual(("T1", "scout"), (self.h.resume_task, self.h.resume_phase))
        self.assertFalse(os.path.exists(os.path.join(self.wt, "src", "stray.ts")))
        with open(os.path.join(self.wt, "src", "a.ts")) as f:
            self.assertIn("parse", f.read())
        self.assertIn("boot-reconcile",
                      util.read_text(os.path.join(self.loop_dir, "harness.log")))
        self.assertEqual("boot-reconcile", self.emitted("decision")[0]["classification"])

    def test_no_state_file_and_no_contract_reverts_everything_outside_the_loop_dir(self):
        self.mark_doing()
        self.worker_edit("src/whatever.ts", "half a task\n")()
        run.boot_reconcile(self.h)
        self.assertFalse(os.path.exists(os.path.join(self.wt, "src", "whatever.ts")))
        self.assertEqual("scout", self.h.resume_phase)

    def test_a_second_in_flight_row_is_reset_to_pending(self):
        self.mark_doing("T1")
        self.mark_doing("T2")
        run.boot_reconcile(self.h)
        self.assertEqual("pending", plan_mod.Plan.load(self.plan_path).task("T2").state)

    def test_the_resume_answer_is_consumed_once(self):
        self.h.resume_task = "T1"
        self.h.resume_phase = "gate"
        self.assertEqual("gate", self.h.take_resume("T1"))
        self.assertEqual("", self.h.take_resume("T1"))

    def test_the_resume_answer_belongs_to_one_task_only(self):
        self.h.resume_task = "T1"
        self.h.resume_phase = "gate"
        self.assertEqual("", self.h.take_resume("T2"))


class TestFailureSignature(Base):
    def test_it_keys_on_kind_phase_and_task_not_on_the_return_code(self):
        self.assertEqual(run.failure_signature("gate-failed", "gate", "T1"),
                         run.failure_signature("gate-failed", "gate", "T1"))
        self.assertNotEqual(run.failure_signature("gate-failed", "gate", "T1"),
                            run.failure_signature("gate-failed", "gate", "T2"))
        self.assertNotEqual(run.failure_signature("needs-work", "evaluator", "T1"),
                            run.failure_signature("gate-failed", "gate", "T1"))


if __name__ == "__main__":
    unittest.main()
````

- [ ] **Step 2: Run test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_run_tick*.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'runner.run'`

- [ ] **Step 3a: Add the STOP hook to `claude_proc.py`**

In `PhaseResult`, add the field after `stalled`:

```python
    stopped: bool = False
```

In `run_phase`'s signature, add one keyword-only parameter after `desc`:

```python
              stop_check: Optional[Callable[[], bool]] = None,
```

(and add `Callable` to the `typing` import).

In `run_phase`, change the state dict and the watchdog:

```python
    state = {"last": float(started), "outstanding": set(), "timed_out": False,
             "stopped": False}
```

```python
    def watchdog():
        while not stop.wait(1.0):
            now = time.time()
            if is_stalled(state["last"], len(state["outstanding"]), stall_s, now):
                result.stalled = True
            if stop_check is not None and stop_check():
                state["stopped"] = True
                _terminate(proc)
                return
            if now >= deadline:
                state["timed_out"] = True
                _terminate(proc)
                return
```

and the post-run classification:

```python
    result.timed_out = bool(state["timed_out"])
    result.stopped = bool(state["stopped"])
    result.killed = result.timed_out or result.stopped or rc in (137, 143)
```

- [ ] **Step 3b: Pass `stop_check` from `phases._dispatch`**

In `phases._dispatch`, add to `kwargs`:

```python
                  stop_check=lambda: os.path.exists(
                      os.path.join(ctx.runtime_dir, "STOP")),
```

and make a stopped phase un-re-askable by widening the existing guard:

```python
    except JsonBlockError as exc:
        if res.killed or res.stopped:
            return res, None
        parse_error = str(exc)
```

- [ ] **Step 3c: Write the first half of `runner/run.py`**

Create `plugins/agent-loop/runner/run.py`:

````python
"""The harness: one Python process per loop.

The harness owns the state machine. It reads the plan, picks the task, dispatches
one `claude -p` subprocess per phase, runs the gate, commits, and decides what
happens next. No model is asked what to do — every phase returns JSON and this
file acts on it.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional

from . import (config, contract as contract_mod, events as events_mod, gate,
               git_ops, harness, incidents, migrate, phases, plan as plan_mod,
               sidecar, status, task_state, util)

EXIT_OK = 0
EXIT_HALT = 1
EXIT_NEEDS_HUMAN = 2
EXIT_LOCK = 3
EXIT_SIGNAL = 130

PAUSE_FILE = "PAUSE"
STOP_FILE = "STOP"


class Log(object):
    """harness.log plus the operator's terminal. Never the loop's state source."""

    def __init__(self, path: str):
        self.rotating = harness.RotatingLog(path)

    def __call__(self, message: str) -> None:
        self._write("%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                               message))

    def feed(self, message: str) -> None:
        """A pretty, timestamp-free line for the human."""
        self._write(message)

    def _write(self, line: str) -> None:
        self.rotating.write(line)
        try:
            sys.stderr.write(line + "\n")
            sys.stderr.flush()
        except (OSError, ValueError):
            pass


@dataclass
class Harness:
    cfg: Any
    plugin_root: str
    loop_dir: str
    runtime_dir: str
    worktree: str
    config_path: str
    plan_path: str
    events: Any
    usage: Any
    log: Any
    plan: Any = None
    medic: Any = field(default_factory=incidents.MedicState)
    tick_lines: List[str] = field(default_factory=list)
    exit_reason: str = ""
    exit_detail: str = ""
    loop_start_epoch: int = 0
    dashboard_url: str = ""
    resume_task: str = ""
    resume_phase: str = ""

    def take_resume(self, task_id: str) -> str:
        """The boot rule's answer for this task — `gate`, `wrapup` or `scout`.

        Consumed once: a resume is a fact about the tick that follows the boot,
        not a standing property of the task.
        """
        if self.resume_task != task_id:
            return ""
        where = self.resume_phase
        self.resume_task = ""
        self.resume_phase = ""
        return where


@dataclass
class TickOutcome:
    verdict: str = "continue"          # continue | done | halt | retry | paused
    cause: str = "ok"
    task_id: str = ""
    sha: str = ""
    gates: str = ""
    results: List[Any] = field(default_factory=list)


def pause_state(h: Harness) -> str:
    """`stop` | `pause` | `""`. STOP wins: it is the more urgent request."""
    if os.path.exists(os.path.join(h.runtime_dir, STOP_FILE)):
        return "stop"
    if os.path.exists(os.path.join(h.runtime_dir, PAUSE_FILE)):
        return "pause"
    return ""


def _protected(h: Harness) -> List[str]:
    """Paths the sandbox must never revert: the loop's own dir, inside the worktree."""
    rel = os.path.relpath(os.path.abspath(h.loop_dir), h.worktree)
    return [rel, os.path.join(rel, "**")]


def sandbox(h: Harness, contract) -> List[str]:
    """Revert everything the contract did not sanction. Runs after EVERY Worker.

    Including a killed one — that is what keeps a timed-out phase from leaving
    the tree ambiguous.
    """
    changed = git_ops.changed_paths(h.worktree)
    stray = git_ops.strays(changed, list(contract.allow_list) + _protected(h))
    if stray:
        git_ops.revert(h.worktree, stray)
        h.log("sandbox: reverted %d path(s) outside allow_list: %s"
              % (len(stray), ", ".join(stray[:5])))
    return stray


def checkpoint_note(h: Harness, task) -> str:
    """The killed Worker's own checkpoint, rendered for the next Worker's prompt.

    # plan C: this is the wrap-up/resume path of spec §6 — SIGTERM, a bounded
    # `--resume <session>` wrap-up turn, then `claude -p --resume` with the
    # minutes left. Plan A cannot resume a session, so it does the next best
    # thing: a fresh Worker with the checkpoint in front of it, over a tree that
    # still holds the partial work. The checkpoint finally has a consumer either
    # way, which is the point of writing it first thing.
    """
    doc = util.read_json(os.path.join(h.runtime_dir, "worker-result.json")) or {}
    if doc.get("task") and doc.get("task") != task.id:
        return ""
    parts = []
    if doc.get("checkpoint"):
        parts.append("Checkpoint: %s" % doc["checkpoint"])
    if doc.get("summary"):
        parts.append("What it said it had done: %s" % doc["summary"])
    if doc.get("files_touched"):
        parts.append("Files it had already touched: %s"
                     % ", ".join(str(p) for p in doc["files_touched"][:20]))
    if doc.get("next_steps"):
        parts.append("Next steps it recorded: %s"
                     % "; ".join(str(s) for s in doc["next_steps"][:10]))
    if not parts:
        return ("A previous Worker on this task was interrupted and left no usable "
                "checkpoint. Re-establish the state from the working tree before "
                "you change anything.")
    return ("A previous Worker on this task was interrupted before it finished. Its "
            "own checkpoint follows, and the work it had already done is still in "
            "the tree — continue from there rather than starting over.\n\n"
            + "\n".join(parts))


def boot_reconcile(h: Harness) -> None:
    """Decide where a `[~]` task picks up. Runs once, before the first tick (spec §14).

    A `[~]` row means a harness died inside the task. `eligible()` only returns
    pending rows, so without this the task is never selected again and the plan
    reads as `stuck` — which is how a v2 dir stopped mid-task needed a human just
    to restart.
    """
    h.plan = plan_mod.Plan.load(h.plan_path)
    doing = [t for t in h.plan.tasks() if t.state == "doing"]
    if not doing:
        return
    task = doing[0]
    for extra in doing[1:]:
        h.log("boot-reconcile: %s is also [~]; resetting it to pending so it re-runs "
              "from the top on a later tick" % extra.id)
        h.plan.set_state(extra.id, "pending")

    if task_state.has_state(h.runtime_dir, task.id):
        state = task_state.TaskState.load(h.runtime_dir, task.id)
        h.resume_phase = task_state.boot_resume(state)
        h.log("boot-reconcile: %s was recorded in phase %r; resuming at %s"
              % (task.id, state.phase or "?", h.resume_phase))
        h.events.emit("decision", task=task.id, decision="resume",
                      classification="capability")
    else:
        # No state file: a 2.x dir, or a crash before the first write.
        # plan C: hand this to the Judge with failure="boot-reconcile" and the
        # evidence the 2.x medic used to read by hand (the dirty tree against
        # allow_list, worker-result.json, the commit trailers, sprint-<T>.json);
        # it answers resume | retry | revert-and-retry | defer. Plan A takes the
        # mechanical branch: keep what the contract sanctioned, revert the rest,
        # and re-run the task from SCOUT.
        allow: List[str] = []
        try:
            allow = list(contract_mod.load_contract(
                os.path.join(h.runtime_dir, "sprint-%s.json" % task.id)).allow_list)
        except contract_mod.ContractError:
            pass
        changed = git_ops.changed_paths(h.worktree)
        stray = git_ops.strays(changed, allow + _protected(h))
        kept = [p for p in changed if p not in stray]
        if stray:
            git_ops.revert(h.worktree, stray)
        h.resume_phase = "scout"
        h.log("boot-reconcile WARNING: %s is [~] but runtime/task-%s.json does not "
              "exist, so nothing records how far it got. Kept %d change(s) inside "
              "allow_list, reverted %d stray path(s), and re-running the task from "
              "SCOUT." % (task.id, task.id, len(kept), len(stray)))
        h.events.emit("decision", task=task.id, decision="retry",
                      classification="boot-reconcile")

    h.resume_task = task.id
    h.plan.set_state(task.id, "pending")
    h.plan.save()


def verification_summary(cfg, ok: bool) -> str:
    tokens = cfg.verification or ["gate"]
    return " ".join("%s=%s" % (t, "pass" if ok else "fail") for t in tokens)


def commit_task(h: Harness, task, contract) -> str:
    """Commit exactly the allow_list paths the Worker touched, with the trailers."""
    changed = git_ops.changed_paths(h.worktree)
    outside = set(git_ops.strays(changed, contract.allow_list))
    paths = [p for p in changed if p not in outside]
    if not paths:
        return ""
    shown = ", ".join(paths[:20])
    if len(paths) > 20:
        shown += " +%d more" % (len(paths) - 20)
    return git_ops.commit(h.worktree, paths,
                          "loop(%s): %s" % (task.id, task.title),
                          {"Loop-Status": "done",
                           "Loop-Verification": verification_summary(h.cfg, True),
                           "Loop-Files": shown})


def failure_signature(reason: str, phase: str, task_id: str) -> str:
    """Keyed on what actually repeated, not on `rc=124 after 1800s` (spec §8)."""
    return "%s|%s|%s" % (reason, phase, task_id)


def mark_blocked(h: Harness, task, reason: str, evidence: str) -> None:
    """`[!]`, a LOOP_CLEANUP entry with the evidence, and the blocked-upstream fan-out.

    Reverts first: a half-applied workaround left in the tree is worse than no
    attempt at all. The worktree belongs to the loop and to nothing else, so
    everything outside the loop's own dir is this task's partial work.
    """
    changed = git_ops.changed_paths(h.worktree)
    to_revert = git_ops.strays(changed, _protected(h))   # everything but the loop dir
    if to_revert:
        git_ops.revert(h.worktree, to_revert)

    h.plan = plan_mod.Plan.load(h.plan_path)
    h.plan.set_state(task.id, "blocked")
    downstream = [t.id for t in h.plan.dependents(task.id)]
    for tid in downstream:
        h.plan.set_state(tid, "blocked-upstream")
    h.plan.save()

    cleanup_path = os.path.join(h.loop_dir, "LOOP_CLEANUP.md")
    entry = [
        "",
        "## %s — %s" % (task.id, reason),
        "",
        "- when: %s" % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "- task: %s" % task.raw.strip(),
        "- evidence:",
        "",
        "```",
        (evidence or "(none)").strip()[:4000],
        "```",
        "",
        "- decision a person must make: review the evidence above and either widen "
        "the contract, restate the task, or drop it. The loop will not retry %s "
        "until this entry is resolved." % task.id,
    ]
    if downstream:
        entry.append("- blocked downstream: %s" % ", ".join(downstream))
    entry.append("")
    existing = util.read_text(cleanup_path)
    header = "" if existing.strip() else "# Loop Cleanup\n"
    with open(cleanup_path, "a") as f:
        f.write(header + "\n".join(entry) + "\n")

    h.events.emit("task_status", id=task.id, status="blocked", sha="")
    h.events.emit("decision", task=task.id, decision="defer", classification="open")
    git_ops.commit(h.worktree, [h.plan_path, cleanup_path],
                   "loop(%s): blocked — %s" % (task.id, reason),
                   {"Loop-Status": "halted",
                    "Loop-Verification": verification_summary(h.cfg, False)})
    h.log("blocked %s (%s); %d downstream task(s) marked blocked-upstream"
          % (task.id, reason, len(downstream)))


def handle_failure(h: Harness, task, reason: str, evidence: str) -> str:
    """What to do when a task cannot pass. Returns the tick verdict.

    # plan C: this is where `judge.decide(ctx, contract, results, verdict,
    # task_state.TaskState.load(...))` is called, and its decision (retry |
    # escalate | widen | resume | split | defer | halt) replaces everything below.
    # In plan A the stand-in is the last two lines: defer, then apply the blocker
    # policy. Note what is NOT here: nothing consults config.next_tier.
    """
    h.log("failure signature %s" % failure_signature(reason, "execute", task.id))
    mark_blocked(h, task, reason, evidence)
    return "halt" if h.cfg.blocker_policy == "halt" else "continue"


def run_worker_attempt(h: Harness, ctx, contract, attempt: int, findings: str = "",
                       tier: Optional[str] = None):
    """One Worker dispatch plus the sandbox that always follows it.

    `tier` is the seam plan C's Judge writes through (`changes.tier`); plan A
    never passes it, so every attempt runs at the configured tier.
    """
    ctx.attempt = attempt
    result, data = phases.run_worker(ctx, contract, tier=tier, findings=findings)
    sandbox(h, contract)
    return result, data


def execute_tick(h: Harness, tick: int, task) -> TickOutcome:
    """SELECT -> SCOUT -> VALIDATE -> WORK -> SANDBOX -> GATE -> EVALUATE ->
    COMMIT -> LEARN, with one same-tier re-dispatch on failure.

    `runtime/task-<T>.json` is rewritten at every transition, so a kill costs the
    phase in flight and nothing more: the next boot reads it and picks up where
    this left off instead of paying for the whole task again.
    """
    out = TickOutcome(task_id=task.id)
    ctx = phases.TickContext(cfg=h.cfg, plan=h.plan, task=task, loop_dir=h.loop_dir,
                             runtime_dir=h.runtime_dir, events=h.events, tick=tick,
                             attempt=1)
    state = task_state.TaskState.load(h.runtime_dir, task.id)
    state.contract = os.path.join("runtime", "sprint-%s.json" % task.id)
    resume = h.take_resume(task.id)
    contract_path = os.path.join(h.runtime_dir, "sprint-%s.json" % task.id)

    h.plan.set_state(task.id, "doing")
    h.plan.save()
    base_sha = git_ops.head_sha(h.worktree)
    git_ops.commit(h.worktree, [h.plan_path], "loop: start %s" % task.id,
                   {"Loop-Status": "progress"})

    contract = None
    if resume in ("gate", "wrapup"):
        # The contract the killed attempt was working to is still on disk, and it
        # is the only thing that makes the partial work in the tree legible.
        try:
            contract = contract_mod.load_contract(contract_path)
        except contract_mod.ContractError:
            h.log("%s: no readable contract survived, so the resume falls back to a "
                  "fresh Scout" % task.id)
            resume = "scout"

    if contract is None:
        state.begin_attempt(phases.worker_tier(h.cfg))
        errors: List[str] = []
        for scout_try in (1, 2):
            state.begin_phase("SCOUT")
            res, contract, errors = phases.run_scout(ctx, validation_errors=errors)
            out.results.append(res)
            state.end_phase(res)
            if contract is not None and not errors:
                break
            if pause_state(h):
                out.verdict = "paused"
                return out
            h.log("contract for %s rejected (try %d): %s"
                  % (task.id, scout_try, "; ".join(errors)))
        if contract is None or errors:
            state.end_attempt("invalid-contract")
            out.verdict = handle_failure(h, task, "invalid-contract",
                                         "\n".join(errors) or "the Scout wrote no contract")
            out.cause = "invalid-contract"
            return out

    attempt = max(1, state.attempt)
    findings = checkpoint_note(h, task) if resume == "wrapup" else ""
    skip_worker = (resume == "gate")
    while True:
        ctx.attempt = attempt
        if pause_state(h):
            out.verdict = "paused"
            return out

        if skip_worker:
            # The previous harness recorded a Worker that returned, so its work is
            # already in the tree. Re-running it would spend a whole Worker budget
            # reproducing what is on disk.
            skip_worker = False
            h.log("%s: resuming at GATE — the recorded Worker had already returned"
                  % task.id)
        else:
            state.begin_phase("WORK")
            wres, _ = run_worker_attempt(h, ctx, contract, attempt, findings=findings)
            out.results.append(wres)
            state.end_phase(wres)
            if wres.stopped:
                out.verdict = "paused"
                return out
            if pause_state(h):
                out.verdict = "paused"
                return out
        findings = ""

        state.begin_phase("GATE")
        gate_results = gate.run_commands(
            contract.verification, h.worktree, config.phase_limit(h.cfg, "gate_cmd"),
            h.runtime_dir, "%s-%d" % (task.id, attempt))
        gate_ok = gate.all_ok(gate_results)
        gate_text = gate.outputs_text(gate_results)
        out.gates = verification_summary(h.cfg, gate_ok)
        state.end_phase()
        # plan B: run_visual_checks(h, ctx, contract) goes here, between GATE and
        # EVALUATE; a render or fidelity failure joins the gate_ok branch below.

        diff = git_ops.diff_text(h.worktree, base_sha, contract.allow_list)
        if task.class_flag == "mechanical":
            verdict = "PASS" if gate_ok else "NEEDS_WORK"
            summary = "mechanical task: the gate is the only judge"
        elif not gate_ok:
            verdict = "NEEDS_WORK"
            summary = "the verification gate failed"
        else:
            state.begin_phase("EVALUATE")
            eres, vdata = phases.run_evaluator(ctx, contract, diff, gate_text, [])
            out.results.append(eres)
            state.end_phase(eres)
            verdict = vdata["verdict"]
            summary = vdata.get("summary", "")

        if gate_ok and verdict == "PASS":
            state.begin_phase("COMMIT")
            sha = commit_task(h, task, contract)
            out.sha = sha
            h.plan = plan_mod.Plan.load(h.plan_path)
            h.plan.set_state(task.id, "done", sha=sha[:7] if sha else None)
            h.plan.save()
            h.events.emit("task_status", id=task.id, status="done", sha=sha[:7])
            state.end_phase()
            state.end_attempt("pass")
            state.begin_phase("LEARN")
            lres = phases.run_learner(ctx, contract, gate_text, summary)
            out.results.append(lres)
            state.end_phase(lres)
            out.verdict = "continue"
            return out

        reason = ("blocker" if verdict == "BLOCKER"
                  else "gate-failed" if not gate_ok else "needs-work")
        state.end_attempt(reason)
        # plan C: judge.decide(...) replaces this branch entirely, and its bound is
        # cfg.limits["max_attempts"] rather than the literal two below.
        if attempt < 2 and verdict != "BLOCKER":
            # Same tier, with the Evaluator's findings in front of it. Spec §11
            # item 4: a re-attempt's tier is a judgement from the checkpoint, not
            # a ladder, and most overruns are one extra iteration — paying the top
            # tier for every one of them is how a cheap retry becomes expensive.
            attempt += 1
            findings = summary or "the previous attempt did not satisfy the contract"
            state.begin_attempt(phases.worker_tier(h.cfg))
            h.events.emit("decision", task=task.id, decision="retry",
                          classification="capability")
            h.log("%s %s on attempt %d — re-dispatching the Worker at the same tier "
                  "with the Evaluator's findings" % (task.id, reason, attempt - 1))
            continue
        evidence = "%s\n\n%s" % (summary, gate_text)
        out.verdict = handle_failure(h, task, reason, evidence)
        out.cause = reason
        return out


def plan_tick(h: Harness, tick: int, segment: str) -> TickOutcome:
    """Expand one unplanned segment into rows, then validate what landed."""
    out = TickOutcome(verdict="continue", task_id="")
    ctx = phases.TickContext(cfg=h.cfg, plan=h.plan, task=None, loop_dir=h.loop_dir,
                             runtime_dir=h.runtime_dir, events=h.events, tick=tick,
                             attempt=1)
    before = len(h.plan.tasks())
    res, _ = phases.run_planner(ctx, segment)
    out.results.append(res)
    h.plan = plan_mod.Plan.load(h.plan_path)
    added = len(h.plan.tasks()) - before
    if added <= 0:
        h.log("the Planner added no rows to %r — nothing left to do here" % segment)
        out.verdict = "halt"
        out.cause = "plan-empty"
        return out
    git_ops.commit(h.worktree, [h.plan_path], "loop: plan %s" % segment,
                   {"Loop-Status": "progress"})
    h.log("planned %s: %d row(s)" % (segment, added))
    return out


def review_tick(h: Harness, tick: int, segment: str) -> TickOutcome:
    """Grade a finished segment, append its follow-ups, stamp it reviewed."""
    out = TickOutcome(verdict="continue", task_id="")
    ctx = phases.TickContext(cfg=h.cfg, plan=h.plan, task=None, loop_dir=h.loop_dir,
                             runtime_dir=h.runtime_dir, events=h.events, tick=tick,
                             attempt=1)
    seg = None
    for candidate in h.plan.segments():
        if candidate.name == segment:
            seg = candidate
    base = ""
    if seg is not None and seg.tasks:
        first_sha = next((t.sha for t in seg.tasks if t.sha), "")
        base = "%s~1" % first_sha if first_sha else ""
    if not base:
        h.log("no task in %r carries a sha — reviewing against the working tree only"
              % segment)
    diff = git_ops.diff_text(h.worktree, base, [])
    res, data = phases.run_reviewer(ctx, segment, diff)
    out.results.append(res)

    rows = []
    for finding in data.get("findings") or []:
        if finding.get("severity") not in ("should-fix", "must-fix"):
            continue
        row = (finding.get("follow_up_row") or "").strip()
        if row.lstrip().startswith("- ["):
            rows.append(row)
    h.plan = plan_mod.Plan.load(h.plan_path)
    if rows:
        h.plan.append_tasks(segment, rows)
    h.plan.stamp_reviewed(segment, git_ops.head_sha(h.worktree)[:7] or "none")
    h.plan.save()
    git_ops.commit(h.worktree, [h.plan_path], "loop: review %s" % segment,
                   {"Loop-Status": "reviewed"})
    h.log("reviewed %s: %d follow-up row(s)" % (segment, len(rows)))
    return out
````

- [ ] **Step 4: Run test to verify it passes**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_*.py' -v`
Expected: PASS — 403 tests, `OK`

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/runner/run.py plugins/agent-loop/runner/claude_proc.py \
        plugins/agent-loop/runner/phases.py plugins/agent-loop/tests/runner/test_run_tick.py
git commit -F - <<'EOF'
agent-loop: the three tick bodies, the sandbox, the boot rule and the stand-in

execute_tick runs SCOUT -> VALIDATE -> WORK -> SANDBOX -> GATE -> EVALUATE ->
COMMIT -> LEARN and decides the outcome in Python, rewriting runtime/task-T.json
at every transition so a kill costs the phase in flight and not the task. The
sandbox reverts every path outside allow_list after every Worker, killed or not,
while never touching the loop's own dir. On failure the stand-in re-dispatches
ONCE AT THE SAME TIER with the Evaluator's findings injected — spec 11.4 makes
the tier of a re-attempt a judgement, not a ladder, so nothing here calls
next_tier — then marks [!], writes a LOOP_CLEANUP entry with the gate evidence,
fans blocked-upstream out over depends_on, commits with Loop-Status: halted and
applies the blocker policy. The judge.decide call site plan C replaces is
marked in place, as is the max_attempts bound it brings with it.

boot_reconcile implements spec 14: a [~] task with a state file resumes at the
recorded phase (Worker-complete goes straight to the gate rather than paying a
second Worker budget to redo what is in the tree; killed inside WORK gets the
checkpoint injected, which is plan C's wrap-up/resume path in stub form), and
one with no state file keeps its in-allow_list work, reverts the strays, logs a
boot-reconcile warning and re-runs from SCOUT.

runtime/STOP now kills the phase in flight: claude_proc's watchdog polls a
stop_check, and a stopped phase is never re-asked.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 19: `run.py` part 2 — bootstrap, the outer loop and the exit codes

**Files:**
- Modify: `plugins/agent-loop/runner/run.py` (append the bootstrap and the outer loop)
- Test: `plugins/agent-loop/tests/runner/test_run_main.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `parse_args(argv)`, `plugin_version(plugin_root)`, `resolve_paths()`, `env_int(name, default)`, `write_checkpoint(h, tick, reason)`, `memory_guard(h, tick)`, `sum_usage(results)`, `usage_payload(by_model)`, `render_status(h, tick, mode, outcome, dur)`, `dispatch_postmortem(h)`, `flush_plan(h)`, `setup_dashboard(h)`, `run_loop(h) -> int`, `teardown(h, hb, sup, adopted, code)`, `main(argv=None) -> int`.
- Exit codes: `0` done / paused / rate-limit-exit, `1` halt or a start-up refusal, `2` needs-human, `3` lock-conflict, `130` signal.
- `run_loop` calls `boot_reconcile` (Task 18) once, before its first `plan.mode()`. Without that a loop dir stopped mid-task has a `[~]` row that `eligible()` never returns, so `mode()` answers `stuck` and the harness exits 1 on a dir that only needed resuming.
- A lock conflict emits an `incident` into the **running** loop's event stream and **no** `loop_end` — a `loop_end` there would make every observer render the healthy loop as terminal.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_run_main.py`:

```python
import _path  # noqa: F401
import json
import os
import subprocess
import tempfile
import unittest

from runner import claude_proc, harness, migrate, run, util


def sh(cwd, *args):
    subprocess.run(list(args), cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Base(unittest.TestCase):
    def setUp(self):
        self.wt = os.path.realpath(tempfile.mkdtemp())
        sh(self.wt, "git", "init", "-q")
        sh(self.wt, "git", "config", "user.email", "loop@example.com")
        sh(self.wt, "git", "config", "user.name", "Loop")
        sh(self.wt, "git", "config", "commit.gpgsign", "false")
        util.atomic_write(os.path.join(self.wt, "README.md"), "seed\n")
        sh(self.wt, "git", "add", "-A")
        sh(self.wt, "git", "commit", "-q", "-m", "seed")

        self.ld = ".claude/loop/test-run"
        self.loop_dir = os.path.join(self.wt, self.ld)
        self.runtime = os.path.join(self.loop_dir, "runtime")
        os.makedirs(self.runtime)
        util.atomic_write(os.path.join(self.loop_dir, "LOOP_CONFIG.md"),
                          "Worktree: %s\nDashboard: off\nMedic: off\n" % self.wt)
        self.write_plan("## S\nReviewed: aaa\n- [x] T1: done already\n")
        self.old_cwd = os.getcwd()
        self.old_env = dict(os.environ)
        os.chdir(self.wt)
        os.environ["LOOP_DIR"] = self.ld
        os.environ["LOOP_NOTIFY"] = "0"
        os.environ["AGENT_LOOP_SKIP_POSTMORTEM"] = "1"
        os.environ["PAUSE_BETWEEN"] = "0"
        self._real = claude_proc.run_phase
        claude_proc.run_phase = self._never_called

    def tearDown(self):
        claude_proc.run_phase = self._real
        os.chdir(self.old_cwd)
        os.environ.clear()
        os.environ.update(self.old_env)

    def _never_called(self, **kw):
        raise AssertionError("no phase should have run: %s" % kw.get("phase"))

    def write_plan(self, text):
        util.atomic_write(os.path.join(self.loop_dir, "LOOP_PLAN.md"), text)

    def events(self, type_=None):
        path = os.path.join(self.loop_dir, "events.jsonl")
        out = []
        if not os.path.exists(path):
            return out
        with open(path) as f:
            for line in f:
                rec = json.loads(line)
                if type_ is None or rec["type"] == type_:
                    out.append(rec)
        return out


class TestParseArgs(unittest.TestCase):
    def test_shim_is_captured_and_everything_else_is_ignored(self):
        args = run.parse_args(["--shim", "/p/run.sh", "--whatever"])
        self.assertEqual("/p/run.sh", args["shim"])
        self.assertEqual(["--whatever"], args["rest"])

    def test_no_arguments_is_fine(self):
        self.assertEqual("", run.parse_args([])["shim"])


class TestStartupRefusals(Base):
    def test_a_missing_config_exits_one_and_writes_no_events(self):
        os.unlink(os.path.join(self.loop_dir, "LOOP_CONFIG.md"))
        self.assertEqual(run.EXIT_HALT, run.main([]))
        self.assertEqual([], self.events())

    def test_running_outside_the_worktree_exits_one(self):
        os.chdir(tempfile.mkdtemp())
        os.environ["CONFIG_PATH"] = os.path.join(self.loop_dir, "LOOP_CONFIG.md")
        os.environ["LOOP_DIR"] = self.loop_dir
        self.assertEqual(run.EXIT_HALT, run.main([]))

    def test_a_live_owner_exits_three_with_an_incident_and_no_loop_end(self):
        harness.lock_acquire(self.runtime, 4242, self.ld, "3.0.0",
                             alive=lambda pid: False)
        original = harness.harness_alive
        harness.harness_alive = lambda pid: True
        try:
            self.assertEqual(run.EXIT_LOCK, run.main([]))
        finally:
            harness.harness_alive = original
        self.assertEqual("lock-conflict", self.events("incident")[0]["kind"])
        self.assertEqual("warn", self.events("incident")[0]["severity"])
        self.assertEqual([], self.events("loop_end"))
        self.assertEqual(4242, util.read_json(
            os.path.join(self.runtime, "harness.json"))["pid"])


class TestTerminalModes(Base):
    def test_a_finished_plan_exits_zero_with_loop_end_done(self):
        self.assertEqual(run.EXIT_OK, run.main([]))
        end = self.events("loop_end")[-1]
        self.assertEqual("done", end["reason"])
        self.assertEqual(0, end["exit_code"])
        self.assertEqual("loop_start", self.events()[0]["type"])
        self.assertFalse(os.path.exists(os.path.join(self.runtime, "harness.json")))
        self.assertFalse(os.path.exists(os.path.join(self.runtime, "tick.json")))

    def test_a_stuck_plan_exits_one(self):
        self.write_plan("## S\nReviewed: aaa\n- [!] T1: blocked\n"
                        "- [ ] T2: next | depends_on: T1\n")
        self.assertEqual(run.EXIT_HALT, run.main([]))
        self.assertEqual("halt", self.events("loop_end")[-1]["reason"])

    def test_pause_at_the_top_of_the_loop_exits_zero_with_a_checkpoint(self):
        self.write_plan("## S\n- [ ] T1: work to do\n")
        util.atomic_write(os.path.join(self.runtime, "PAUSE"), "")
        self.assertEqual(run.EXIT_OK, run.main([]))
        self.assertEqual("paused", self.events("loop_end")[-1]["reason"])
        self.assertEqual(1, len(self.events("paused")))
        check = util.read_json(os.path.join(self.runtime, "CHECKPOINT.json"))
        self.assertEqual("T1", check["next_task"])
        self.assertEqual("S", check["segment"])
        self.assertIn("resume", check["note"])

    def test_stop_is_treated_like_pause_at_the_top_of_the_loop(self):
        self.write_plan("## S\n- [ ] T1: work to do\n")
        util.atomic_write(os.path.join(self.runtime, "STOP"), "")
        self.assertEqual(run.EXIT_OK, run.main([]))
        self.assertEqual("paused", self.events("loop_end")[-1]["reason"])


class TestSchema(Base):
    def test_a_fresh_dir_is_stamped_with_the_current_schema(self):
        run.main([])
        self.assertEqual(migrate.LOOP_SCHEMA,
                         util.read_int(os.path.join(self.runtime, "schema")))

    def test_a_dir_newer_than_the_plugin_exits_two(self):
        util.atomic_write(os.path.join(self.runtime, "schema"), "99")
        self.assertEqual(run.EXIT_NEEDS_HUMAN, run.main([]))
        body = util.read_text(os.path.join(self.runtime, "NEEDS_HUMAN.md"))
        self.assertIn("update the plugin", body)
        self.assertEqual("needs-human", self.events("loop_end")[-1]["reason"])

    def test_a_live_one_x_harness_blocks_the_start(self):
        util.atomic_write(os.path.join(self.runtime, "tickseq"), "41")
        util.atomic_write(os.path.join(self.loop_dir, "run.log"),
                          "2026-01-01T00:00:00Z tick 41 starting\n")
        self.assertEqual(run.EXIT_NEEDS_HUMAN, run.main([]))
        self.assertIn("LOOP_MIGRATE_FORCE",
                      util.read_text(os.path.join(self.runtime, "NEEDS_HUMAN.md")))
        self.assertFalse(os.path.exists(os.path.join(self.runtime, "schema")))


class TestResumeAndTakeover(Base):
    def test_a_second_run_reports_resume(self):
        util.atomic_write(os.path.join(self.runtime, "tickseq"), "7")
        run.main([])
        self.assertEqual(1, self.events("loop_start")[-1]["resume"])

    def test_a_dead_owner_is_taken_over_and_recorded(self):
        harness.lock_acquire(self.runtime, 999999, self.ld, "2.1.0",
                             alive=lambda pid: False)
        self.assertEqual(run.EXIT_OK, run.main([]))
        self.assertIn("harness-crash", [e["kind"] for e in self.events("incident")])


class TestBootRule(Base):
    """Spec §14 at the outer-loop level: a dir stopped mid-task resumes itself."""

    def _malformed(self, **kw):
        from runner.claude_proc import PhaseResult
        return PhaseResult(phase=kw["phase"], model=kw["model"], rc=0,
                           session_id="s", result_text="no json here")

    def test_an_in_flight_task_is_reconciled_instead_of_reading_as_stuck(self):
        # A `[~]` row is what a dir stopped mid-task looks like. eligible() never
        # returns it, so before the boot rule this exited 1 having touched nothing.
        self.write_plan("## S\nReviewed: aaa\n- [~] T1: in flight\n")
        claude_proc.run_phase = self._malformed
        run.main([])
        self.assertTrue(self.events("tick_start"), "the task was actually attempted")
        self.assertIn("retry", [e["decision"] for e in self.events("decision")])
        self.assertIn("boot-reconcile",
                      util.read_text(os.path.join(self.loop_dir, "harness.log")))

    def test_a_finished_plan_never_triggers_the_boot_rule(self):
        run.main([])
        self.assertNotIn("boot-reconcile",
                         util.read_text(os.path.join(self.loop_dir, "harness.log")))


class TestHelpers(Base):
    def test_sum_usage_folds_every_phase(self):
        class R(object):
            def __init__(self, usage):
                self.usage_by_model = usage
        from runner.claude_proc import Usage
        total = run.sum_usage([R({"m": Usage(cost_usd=1.0, input_tokens=2)}),
                               R({"m": Usage(cost_usd=2.0, input_tokens=3)})])
        self.assertEqual(3.0, total["m"].cost_usd)
        self.assertEqual(5, total["m"].input_tokens)

    def test_usage_payload_is_plain_json(self):
        from runner.claude_proc import Usage
        payload = run.usage_payload({"m": Usage(cost_usd=1.5)})
        self.assertEqual(1.5, payload["m"]["cost_usd"])
        json.dumps(payload)

    def test_env_int_falls_back_on_garbage(self):
        os.environ["SOME_KNOB"] = "not a number"
        self.assertEqual(5, run.env_int("SOME_KNOB", 5))
        os.environ["SOME_KNOB"] = "9"
        self.assertEqual(9, run.env_int("SOME_KNOB", 5))

    def test_plugin_version_reads_the_manifest(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(run.__file__)))
        self.assertNotEqual("0.0.0", run.plugin_version(root))
        self.assertEqual("0.0.0", run.plugin_version("/nowhere"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_run_main*.py' -v`
Expected: FAIL — `AttributeError: module 'runner.run' has no attribute 'parse_args'`

- [ ] **Step 3: Append the bootstrap and the outer loop to `runner/run.py`**

Add `import signal` and `import subprocess` to the import block, then append:

```python
class Stopped(Exception):
    """A signal asked the harness to stop."""


def parse_args(argv: List[str]) -> dict:
    """`--shim <path>` records the bash entry point. Everything else is ignored.

    The shim passes its own path so that `ps -o command= -p <pid>` still contains
    `run.sh` after the exec — which is the liveness test serve.py and all four
    skills use. Without it every live harness reads as dead.
    """
    shim = ""
    rest: List[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--shim" and i + 1 < len(argv):
            shim = argv[i + 1]
            i += 2
            continue
        rest.append(argv[i])
        i += 1
    return {"shim": shim, "rest": rest}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def plugin_version(plugin_root: str) -> str:
    record = util.read_json(os.path.join(plugin_root, ".claude-plugin", "plugin.json"))
    if isinstance(record, dict) and record.get("version"):
        return str(record["version"])
    return "0.0.0"


def resolve_paths() -> dict:
    loop_dir = os.environ.get("LOOP_DIR") or os.path.join(".claude", "loop", "run")
    return {"loop_dir": loop_dir,
            "runtime_dir": os.path.join(loop_dir, "runtime"),
            "config": os.environ.get("CONFIG_PATH") or os.path.join(loop_dir, "LOOP_CONFIG.md"),
            "plan": os.environ.get("PLAN_PATH") or os.path.join(loop_dir, "LOOP_PLAN.md"),
            "events": os.environ.get("EVENTS") or os.path.join(loop_dir, "events.jsonl"),
            "usage": os.path.join(loop_dir, "LOOP_USAGE.jsonl"),
            "status": os.environ.get("STATUS_FILE") or os.path.join(loop_dir, "LOOP_STATUS.md")}


def write_checkpoint(h: Harness, tick: int, reason: str) -> None:
    eligible = h.plan.eligible() if h.plan else []
    segments = h.plan.segments() if h.plan else []
    util.write_json(os.path.join(h.runtime_dir, "CHECKPOINT.json"),
                    {"t": int(time.time()), "stopped_after_tick": tick,
                     "next_task": eligible[0].id if eligible else "?",
                     "segment": segments[-1].name if segments else "?",
                     "note": "%s requested; resume by re-running the launch command "
                             "or clicking Resume" % reason})


def memory_guard(h: Harness, tick: int) -> None:
    """Advisory: wait for headroom, then start the tick anyway.

    Refusing to work is worse than risking a retry, and the memory_pressure +
    sleep events make the wait legible instead of looking like a stall.
    """
    min_mb = env_int("MEM_MIN_MB", 1024)
    max_swap = env_int("SWAP_MAX_PCT", 90)
    backoff = env_int("MEM_BACKOFF", 60)
    max_delays = env_int("MEM_MAX_DELAYS", 5)
    delays = 0
    while True:
        free_mb, swap_pct = harness.mem_headroom()
        if harness.mem_guard_action(free_mb, swap_pct, min_mb, max_swap) == "proceed":
            return
        if delays >= max_delays:
            h.events.emit("memory_pressure", free_mb=free_mb, swap_used_pct=swap_pct,
                          action="proceed")
            h.log.feed("⚠ low memory headroom (%dMB free, swap %d%%) — starting tick "
                       "%d anyway after %d delays" % (free_mb, swap_pct, tick, delays))
            return
        h.events.emit("memory_pressure", free_mb=free_mb, swap_used_pct=swap_pct,
                      action="delay")
        h.events.emit("sleep", tick=tick, until=int(time.time()) + backoff,
                      reason="memory")
        h.log.feed("◌ waiting for memory headroom (%dMB free, swap %d%%) — retry in %ds"
                   % (free_mb, swap_pct, backoff))
        delays += 1
        time.sleep(backoff)


def sum_usage(results: List[Any]):
    total = {}
    for result in results:
        total = phases._merge_usage(total, getattr(result, "usage_by_model", {}) or {})
    return total


def usage_payload(by_model) -> dict:
    return dict((model, {"cost_usd": u.cost_usd, "input_tokens": u.input_tokens,
                         "output_tokens": u.output_tokens,
                         "cache_read_tokens": u.cache_read_tokens,
                         "cache_creation_tokens": u.cache_creation_tokens})
                for model, u in (by_model or {}).items())


def render_status(h: Harness, tick: int, mode: str, outcome: TickOutcome,
                  dur: int) -> None:
    """The session header plus one permanent line per tick, into LOOP_STATUS.md."""
    tasks = h.plan.tasks()
    done = len([t for t in tasks if t.state == "done"])
    total = len(tasks)
    segments = h.plan.segments()
    seg_done = len([s for s in segments
                    if s.tasks and all(t.state in plan_mod.DONE_STATES for t in s.tasks)])
    unplanned = len([s for s in segments if not s.tasks])
    display = outcome.verdict
    if display == "continue" and mode in ("plan", "review"):
        display = mode
    plan_usage = ""
    rl = util.read_json(os.path.join(h.runtime_dir, "ratelimit.json"))
    if isinstance(rl, dict) and rl.get("utilization") is not None:
        util.write_json(os.path.join(h.runtime_dir, "plan-usage.json"), rl)
    header = status.session_header(
        done, total, int(time.time()) - h.loop_start_epoch, 0, plan_usage,
        seg_done if unplanned else 0, len(segments) if unplanned else 0)
    line = status.tick_line(display, tick, outcome.task_id, dur, outcome.gates,
                            outcome.sha[:7], done, total, outcome.cause)
    h.tick_lines.append(line)
    h.log.feed(header)
    h.log.feed(line)
    status.write_status(resolve_paths()["status"], header, h.tick_lines)


def flush_plan(h: Harness) -> None:
    """Commit any plan edit still sitting in the tree (the last `done(+sha)` row)."""
    changed = git_ops.changed_paths(h.worktree)
    rel = os.path.relpath(os.path.abspath(h.plan_path), h.worktree)
    if rel in changed:
        git_ops.commit(h.worktree, [h.plan_path], "loop: checkpoint plan",
                       {"Loop-Status": "progress"})


def dispatch_postmortem(h: Harness) -> None:
    if os.environ.get("AGENT_LOOP_SKIP_POSTMORTEM") == "1":
        return
    try:
        subprocess.run(["claude", "--print", "--dangerously-skip-permissions",
                        "/agent-loop-postmortem"], cwd=h.worktree,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=env_int("POSTMORTEM_TIMEOUT", 900))
    except (OSError, subprocess.SubprocessError):
        h.log("the postmortem dispatch failed; the loop artefacts are all on disk")


def setup_dashboard(h: Harness):
    """(supervisor, adopted). Adoption ignores the setting — it governs spawning only.

    `LOOP_DASHBOARD` overrides `Dashboard:`, because serve.py spawns the harness
    with `LOOP_DASHBOARD=off` when it is already the dashboard (`w10` §4).
    """
    url = sidecar.adoptable(h.runtime_dir)
    if url:
        h.dashboard_url = url
        h.log.feed("◉ dashboard %s (adopted)" % url)
        return None, True
    mode = os.environ.get("LOOP_DASHBOARD") or h.cfg.dashboard
    if mode != "auto":
        return None, False
    url = sidecar.start(h.plugin_root, h.loop_dir, h.runtime_dir)
    if url:
        h.dashboard_url = url
        h.log.feed("◉ dashboard %s" % url)
    else:
        h.log("dashboard sidecar did not announce a URL — see %s/dashboard.out"
              % h.runtime_dir)
    supervisor = sidecar.Supervisor(
        h.runtime_dir, h.plugin_root, h.loop_dir,
        lambda n: incidents.incident_new(h.runtime_dir, h.events, "dashboard-crashloop",
                                         "warn", "sidecar died %d times — not "
                                         "restarting" % n, 0),
        interval=env_int("HB_INTERVAL", 10),
        max_restarts=env_int("DASH_MAX_RESTARTS", 5))
    supervisor.start()
    return supervisor, False


def run_loop(h: Harness) -> int:
    """Tick until the plan is finished, a human is needed, or someone says stop."""
    h.loop_start_epoch = int(time.time())
    tickseq = os.path.join(h.runtime_dir, "tickseq")
    pause_between = env_int("PAUSE_BETWEEN", 5)
    np_max = env_int("NP_MAX", 3)
    fail_streak = 0
    rl_streak = 0
    no_progress = 0
    remaining_prev = None

    # Spec §14: settle any [~] task BEFORE the first mode() call, or a dir
    # stopped mid-task reads as `stuck` and exits 1 without touching the work.
    h.plan = plan_mod.Plan.load(h.plan_path)
    boot_reconcile(h)

    while True:
        requested = pause_state(h)
        h.plan = plan_mod.Plan.load(h.plan_path)
        if requested:
            tick = util.read_int(tickseq)
            write_checkpoint(h, tick, requested)
            h.events.emit("paused", tick=tick)
            h.log("%s present; checkpoint written, exiting cleanly" % requested.upper())
            h.exit_reason = "paused"
            return EXIT_OK

        mode = h.plan.mode()
        if mode == "done":
            flush_plan(h)
            h.log("every task is done or skipped after %d ticks" % util.read_int(tickseq))
            h.exit_reason = "done"
            dispatch_postmortem(h)
            return EXIT_OK
        if mode == "stuck":
            flush_plan(h)
            h.exit_detail = ("work remains but nothing is eligible, plannable or "
                             "reviewable — see LOOP_CLEANUP.md")
            h.exit_reason = "halt"
            h.log("HALT: %s" % h.exit_detail)
            return EXIT_HALT

        tick = util.read_int(tickseq) + 1
        util.atomic_write(tickseq, str(tick))
        memory_guard(h, tick)

        started = int(time.time())
        tick_timeout = config.phase_limit(h.cfg, "tick_timeout")
        harness.write_tick_json(h.runtime_dir, tick, os.getpid(), started, tick_timeout)
        h.events.emit("tick_start", tick=tick, pid=os.getpid(),
                      timeout_at=started + tick_timeout)
        h.log("tick %d starting (%s)" % (tick, mode))
        try:
            if mode == "review":
                outcome = review_tick(h, tick, h.plan.next_review_segment().name)
            elif mode == "plan":
                outcome = plan_tick(h, tick, h.plan.next_plan_segment().name)
            else:
                outcome = execute_tick(h, tick, h.plan.eligible()[0])
        finally:
            harness.clear_tick_json(h.runtime_dir)

        dur = int(time.time()) - started
        by_model = sum_usage(outcome.results)
        rcs = [r.rc for r in outcome.results if getattr(r, "rc", None)]
        h.usage.write_tick(tick, mode, dur, by_model)
        h.events.emit("tick_end", tick=tick, verdict=outcome.verdict,
                      cause=outcome.cause, rc=max(rcs) if rcs else 0, dur=dur,
                      by_model=usage_payload(by_model))
        h.plan = plan_mod.Plan.load(h.plan_path)
        render_status(h, tick, mode, outcome, dur)

        for result in outcome.results:
            if result.stalled:
                incidents.handle_incident(h.runtime_dir, h.events, h.cfg,
                                          "tick-stalled", "warn",
                                          "the %s phase emitted nothing for %ds with no "
                                          "tool call outstanding"
                                          % (result.phase, env_int("STALL_S", 300)),
                                          tick, h.medic, h.log)
        timed_out = [r for r in outcome.results if r.timed_out]
        if timed_out:
            detail = "the %s phase exceeded its budget" % timed_out[0].phase
            if not incidents.handle_incident(h.runtime_dir, h.events, h.cfg,
                                             "phase-timeout", "error", detail, tick,
                                             h.medic, h.log):
                incidents.escalate(h.runtime_dir, h.events, "phase-timeout", detail,
                                   tick, h.worktree, h.loop_dir,
                                   os.path.join(h.plugin_root, "run.sh"),
                                   h.medic.last_id)
                h.exit_reason = "needs-human"
                h.exit_detail = detail
                return EXIT_NEEDS_HUMAN

        rate_limit = next((r.rate_limit for r in outcome.results if r.rate_limit), None)
        action = harness.ratelimit_action(rate_limit, int(time.time()),
                                          env_int("MAX_WAIT", 21600))
        if action == "exit":
            h.log("usage limit hit — state is saved on disk; re-run after your window "
                  "resets to resume")
            h.exit_reason = "rate-limit-exit"
            return EXIT_OK
        if action.startswith("wait "):
            secs = int(action.split()[1])
            h.events.emit("sleep", tick=tick, until=int(time.time()) + secs,
                          reason="rate-limit")
            h.log("usage limit hit — sleeping %ds until the window resets, then resuming"
                  % secs)
            time.sleep(secs)
            h.log.feed("▶ resumed · running tick %d" % (tick + 1))
            continue
        if any(r.api_error_status == "429" for r in outcome.results):
            rl_streak += 1
            if rl_streak >= env_int("RL_MAX_STRIKES", 5):
                h.log("rate-limited %dx with no reset info — state saved; re-run later"
                      % rl_streak)
                h.exit_reason = "rate-limit-exit"
                return EXIT_OK
            backoff = env_int("RL_BACKOFF", 60)
            h.events.emit("sleep", tick=tick, until=int(time.time()) + backoff,
                          reason="rate-limit")
            time.sleep(backoff)
            continue
        rl_streak = 0

        if outcome.verdict == "paused":
            write_checkpoint(h, tick, "pause")
            h.events.emit("paused", tick=tick)
            h.exit_reason = "paused"
            h.log("paused between phases; checkpoint written")
            return EXIT_OK
        if outcome.verdict == "halt":
            flush_plan(h)
            h.exit_reason = "halt"
            h.exit_detail = outcome.cause or "the loop cannot continue"
            h.log("HALT: %s" % h.exit_detail)
            return EXIT_HALT

        if outcome.cause in ("", "ok"):
            fail_streak = 0
        else:
            fail_streak += 1
            if fail_streak >= 3:
                detail = "3 consecutive failed ticks (last cause: %s)" % outcome.cause
                if not incidents.handle_incident(h.runtime_dir, h.events, h.cfg,
                                                 "garbage-ticks", "error", detail, tick,
                                                 h.medic, h.log):
                    incidents.escalate(h.runtime_dir, h.events, "garbage-ticks", detail,
                                       tick, h.worktree, h.loop_dir,
                                       os.path.join(h.plugin_root, "run.sh"),
                                       h.medic.last_id)
                    h.exit_reason = "needs-human"
                    h.exit_detail = detail
                    return EXIT_NEEDS_HUMAN
                fail_streak = 0

        remaining = len([t for t in h.plan.tasks() if t.state == "pending"])
        if mode != "execute" or remaining != remaining_prev:
            no_progress = 0
        else:
            no_progress += 1
            if no_progress >= np_max:
                detail = ("no progress in %d consecutive execute ticks (remaining stuck "
                          "at %d)" % (np_max, remaining))
                if not incidents.handle_incident(h.runtime_dir, h.events, h.cfg,
                                                 "no-progress", "error", detail, tick,
                                                 h.medic, h.log):
                    incidents.escalate(h.runtime_dir, h.events, "no-progress", detail,
                                       tick, h.worktree, h.loop_dir,
                                       os.path.join(h.plugin_root, "run.sh"),
                                       h.medic.last_id)
                    h.exit_reason = "needs-human"
                    h.exit_detail = detail
                    return EXIT_NEEDS_HUMAN
                no_progress = 0
        remaining_prev = remaining

        h.events.emit("sleep", tick=tick, until=int(time.time()) + pause_between,
                      reason="between-ticks")
        if pause_between:
            time.sleep(pause_between)


def teardown(h: Harness, hb, supervisor, adopted: bool, code: int) -> None:
    """One exit path: stop what we spawned, narrate why, drop the lock.

    loop_end is the only thing that makes a state terminal for an observer, so it
    is emitted on every exit — including a signal. An ADOPTED dashboard is left
    running; it outlives any one harness.
    """
    if supervisor is not None:
        supervisor.stop()
    elif not adopted:
        sidecar.stop(h.runtime_dir)
    hb.stop()
    h.events.emit("loop_end", reason=h.exit_reason or "error",
                  detail=h.exit_detail, exit_code=code)
    harness.lock_release(h.runtime_dir, os.getpid())
    harness.clear_tick_json(h.runtime_dir)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    paths = resolve_paths()
    loop_dir = paths["loop_dir"]
    runtime_dir = paths["runtime_dir"]
    os.makedirs(runtime_dir, exist_ok=True)
    log = Log(os.path.join(loop_dir, "harness.log"))
    # Read the 1.x liveness evidence BEFORE this process writes anything.
    legacy_live = migrate.legacy_harness_live(loop_dir)

    try:
        cfg = config.load_config(paths["config"])
    except ValueError as exc:
        log("HALT: %s" % exc)
        return EXIT_HALT

    worktree = os.path.realpath(cfg.worktree) if cfg.worktree else ""
    if worktree != os.path.realpath(os.getcwd()):
        log("HALT: cwd %r is not the configured Worktree %r"
            % (os.path.realpath(os.getcwd()), worktree))
        return EXIT_HALT

    plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    version = plugin_version(plugin_root)
    events = events_mod.EventLog(paths["events"], os.path.join(runtime_dir, "eventseq"))

    lock = harness.lock_acquire(runtime_dir, os.getpid(), loop_dir, version)
    if lock.startswith("held:"):
        _, owner, since = lock.split(":", 2)
        log("another harness already owns %s: pid %s (since %s). Stop it with "
            "'kill %s', or use a different LOOP_DIR." % (loop_dir, owner, since, owner))
        incidents.incident_new(runtime_dir, events, "lock-conflict", "warn",
                               "pid %s alive since %s; refused second harness"
                               % (owner, since), 0)
        return EXIT_LOCK
    crashed_pid = lock.split(":", 1)[1] if lock.startswith("takeover:") else ""

    os.environ["LOOP_DIR"] = loop_dir
    os.environ["RUNTIME_DIR"] = runtime_dir
    os.environ.setdefault("LOOP_KNOWLEDGE",
                          os.path.join(os.path.dirname(os.path.abspath(loop_dir)),
                                       "KNOWLEDGE.md"))

    h = Harness(cfg=cfg, plugin_root=plugin_root, loop_dir=loop_dir,
                runtime_dir=runtime_dir, worktree=worktree,
                config_path=paths["config"], plan_path=paths["plan"], events=events,
                usage=events_mod.UsageLog(paths["usage"]), log=log,
                plan=plan_mod.Plan.load(paths["plan"]),
                medic=incidents.MedicState(env_int("MEDIC_MAX_PER_RUN", 3)))
    if args["shim"]:
        h.log("launched via %s" % args["shim"])

    def _signal(signum, frame):
        raise Stopped()

    signal.signal(signal.SIGINT, _signal)
    signal.signal(signal.SIGTERM, _signal)

    hb = harness.Heartbeat(runtime_dir, env_int("HB_INTERVAL", 10))
    hb.start()
    supervisor = None
    adopted = False
    code = EXIT_HALT
    try:
        supervisor, adopted = setup_dashboard(h)
        resume = 1 if util.read_int(os.path.join(runtime_dir, "tickseq")) > 0 else 0
        fields = {"pid": os.getpid(), "host": os.uname().nodename,
                  "plugin_version": version, "resume": resume}
        if h.dashboard_url:
            fields["dashboard_url"] = h.dashboard_url
        events.emit("loop_start", **fields)

        outcome = migrate.migrate_loop_dir(
            loop_dir, runtime_dir, events, migrate.LOOP_SCHEMA, legacy_live,
            os.environ.get("LOOP_MIGRATE_FORCE") == "1", version)
        if outcome.startswith("blocked:") or outcome.startswith("newer:"):
            if outcome.startswith("newer:"):
                kind = "schema-newer"
                detail = ("this loop dir is schema %s but plugin v%s only knows schema "
                          "%d — update the plugin on this machine, then re-run"
                          % (outcome.split(":", 1)[1], version, migrate.LOOP_SCHEMA))
            else:
                kind = "migration-blocked"
                detail = outcome.split(":", 1)[1]
            incidents.escalate(runtime_dir, events, kind, detail, 0, worktree, loop_dir,
                               os.path.join(plugin_root, "run.sh"))
            h.exit_reason = "needs-human"
            h.exit_detail = detail
            code = EXIT_NEEDS_HUMAN
            return code
        if outcome.startswith("migrated:"):
            steps = outcome.split(":")
            h.log("loop dir migrated: schema %s -> %s (%s)"
                  % (steps[1], steps[2], steps[3]))
            h.log.feed("⇡ schema %s → %s · %s" % (steps[1], steps[2], steps[3]))

        if crashed_pid:
            detail = "previous harness pid %s died without cleanup" % crashed_pid
            h.log("stale harness lock (pid %s dead) — taking over" % crashed_pid)
            if cfg.medic == "auto":
                if not incidents.handle_incident(runtime_dir, events, cfg,
                                                 "harness-crash", "error", detail, 0,
                                                 h.medic, h.log):
                    incidents.escalate(runtime_dir, events, "harness-crash", detail, 0,
                                       worktree, loop_dir,
                                       os.path.join(plugin_root, "run.sh"),
                                       h.medic.last_id)
                    h.exit_reason = "needs-human"
                    h.exit_detail = detail
                    code = EXIT_NEEDS_HUMAN
                    return code
            else:
                incidents.handle_incident(runtime_dir, events, cfg, "harness-crash",
                                          "warn", detail + "; the next tick "
                                          "re-evaluates any in-progress task", 0,
                                          h.medic, h.log)

        code = run_loop(h)
        return code
    except Stopped:
        h.exit_reason = "signal"
        code = EXIT_SIGNAL
        return code
    finally:
        teardown(h, hb, supervisor, adopted, code)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_*.py' -v`
Expected: PASS — 423 tests, `OK`

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/runner/run.py plugins/agent-loop/tests/runner/test_run_main.py
git commit -F - <<'EOF'
agent-loop: harness bootstrap, the outer loop and the exit codes

Lock, heartbeat thread, dashboard adoption, migration, then tick until the plan
is finished. Same exit codes as v2 (0 done/paused/rate-limit-exit, 1 halt,
2 needs-human, 3 lock-conflict) and the same loop_end contract: emitted on every
exit path including a signal, and never into a live loop's stream on a lock
conflict. PAUSE and STOP are both checked at the top of every tick and between
phases, so a request lands within minutes rather than at the next 30-minute
boundary.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 20: the `run.sh` shim, the deletions and `tests/all.sh`

**Files:**
- Rewrite: `plugins/agent-loop/run.sh` (stays mode 755)
- Delete: `plugins/agent-loop/lib/events.sh`, `lib/harness.sh`, `lib/loop.sh`, `lib/migrate.sh`, `plugins/agent-loop/tick-prompt.md`
- Delete: `plugins/agent-loop/tests/lib.test.sh`, `tests/harness.test.sh`, `tests/events.test.sh`, `tests/migrate.test.sh`, `tests/tick-prompt.contract.sh`
- Modify: `plugins/agent-loop/tests/all.sh`
- Test: `plugins/agent-loop/tests/runner/test_shim.py`

**Interfaces:**
- Consumes: `runner/run.py:main`.
- Produces: the only bash entry point. `bash run.sh` from a directory with no `LOOP_CONFIG.md` must exit 1 having reached Python, which is what proves the shim wires up.
- `tests/tick-prompt.contract.sh` goes now rather than in plan D, because `tick-prompt.md` goes now. Plan D adds `tests/prompts.contract.sh`.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_shim.py`:

```python
import _path  # noqa: F401
import os
import subprocess
import tempfile
import unittest

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUN_SH = os.path.join(PLUGIN_ROOT, "run.sh")


class TestShim(unittest.TestCase):
    def run_shim(self, cwd, env_extra=None, args=None):
        env = dict(os.environ)
        env["LOOP_NOTIFY"] = "0"
        env.update(env_extra or {})
        return subprocess.run(["bash", RUN_SH] + (args or []), cwd=cwd, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_it_reaches_python_and_reports_the_missing_config(self):
        d = tempfile.mkdtemp()
        proc = self.run_shim(d, {"LOOP_DIR": os.path.join(d, "loop")})
        self.assertEqual(1, proc.returncode)
        self.assertIn("no readable LOOP_CONFIG", proc.stderr.decode())

    def test_the_package_imports_without_the_plugin_root_on_the_callers_path(self):
        d = tempfile.mkdtemp()
        proc = self.run_shim(d, {"LOOP_DIR": os.path.join(d, "loop"), "PYTHONPATH": ""})
        self.assertNotIn("ImportError", proc.stderr.decode())
        self.assertNotIn("ModuleNotFoundError", proc.stderr.decode())

    def test_the_shim_path_is_on_the_command_line_so_liveness_still_works(self):
        with open(RUN_SH) as f:
            body = f.read()
        self.assertIn("--shim", body)
        self.assertIn("run.sh", body.split("--shim", 1)[1])

    def test_run_sh_is_executable(self):
        self.assertTrue(os.access(RUN_SH, os.X_OK))

    def test_the_deleted_bash_harness_is_really_gone(self):
        for stale in ("lib/loop.sh", "lib/harness.sh", "lib/events.sh",
                      "lib/migrate.sh", "tick-prompt.md",
                      "tests/lib.test.sh", "tests/harness.test.sh",
                      "tests/events.test.sh", "tests/migrate.test.sh",
                      "tests/tick-prompt.contract.sh"):
            self.assertFalse(os.path.exists(os.path.join(PLUGIN_ROOT, stale)), stale)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_shim*.py' -v`
Expected: FAIL — the old `run.sh` sources `lib/loop.sh` and dies on `no $LOOP_DIR/LOOP_CONFIG.md found`, and every deleted-file assertion fails.

- [ ] **Step 3a: Rewrite `run.sh`**

```bash
#!/usr/bin/env bash
# agent-loop harness entry point. No logic lives here: the loop is runner/run.py.
#
# The shim stays for two reasons. The dashboard spawns the harness as
# `Popen(["bash", run_sh], ...)`, and every liveness check in the system is
# "pid is alive AND `ps -o command= -p <pid>` contains run.sh" — so the exec'd
# python must keep that token on its command line, which `--shim` does.
# `-m` rather than a file path: runner/ uses package-relative imports.
set -uo pipefail
PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$PLUGIN_ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m runner.run --shim "$PLUGIN_ROOT/run.sh" "$@"
```

- [ ] **Step 3b: Delete the bash harness**

```bash
git rm -q plugins/agent-loop/lib/events.sh plugins/agent-loop/lib/harness.sh \
          plugins/agent-loop/lib/loop.sh plugins/agent-loop/lib/migrate.sh \
          plugins/agent-loop/tick-prompt.md \
          plugins/agent-loop/tests/lib.test.sh \
          plugins/agent-loop/tests/harness.test.sh \
          plugins/agent-loop/tests/events.test.sh \
          plugins/agent-loop/tests/migrate.test.sh \
          plugins/agent-loop/tests/tick-prompt.contract.sh
```

- [ ] **Step 3c: Rewrite `tests/all.sh`**

```bash
#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
fail=0
for t in run.e2e.test.sh setup.contract.sh medic.contract.sh postmortem.contract.sh; do
  echo "### $t"
  bash "$HERE/$t" || fail=1
done
echo "### runner unit tests"
if command -v python3 >/dev/null 2>&1; then
  ( cd "$HERE/.." && python3 -m unittest discover -s tests/runner -p 'test_*.py' ) || fail=1
else
  echo "(python tests skipped — python3 not installed)"
fi
echo "### serve.test.py"
if command -v python3 >/dev/null 2>&1; then
  python3 "$HERE/serve.test.py" || fail=1
else
  echo "(python tests skipped — python3 not installed)"
fi
echo "### web.contract.sh"
bash "$HERE/web.contract.sh" || fail=1
exit "$fail"
```

- [ ] **Step 4: Run the tests**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -p 'test_*.py' -v`
Expected: PASS — 428 tests, `OK`
Run: `bash scripts/lint.sh`
Expected: `shellcheck clean (N files)` — N is four lower than before the deletions.
Run: `git ls-files -s plugins/agent-loop/run.sh`
Expected: `100755` (if not: `chmod 755 plugins/agent-loop/run.sh`).
`bash plugins/agent-loop/tests/all.sh` still fails at `run.e2e.test.sh` — Task 21 rewrites it.

- [ ] **Step 5: Commit**

```bash
git add -A plugins/agent-loop/run.sh plugins/agent-loop/lib plugins/agent-loop/tick-prompt.md \
           plugins/agent-loop/tests plugins/agent-loop/tests/runner/test_shim.py
git commit -F - <<'EOF'
agent-loop: run.sh becomes a shim; the bash harness and tick-prompt are deleted

3,600 lines of bash and a 300-line orchestrator prompt go; the shim keeps the
"run.sh" token on the harness's command line so serve.py and the four skills
keep resolving liveness the way they always have. tests/all.sh now runs the
runner's unittest suite, skipping it with a message when python3 is absent,
exactly as it already does for serve.test.py.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 21: `run.e2e.test.sh` — eleven scenarios against the scripted stub

**Files:**
- Rewrite: `plugins/agent-loop/tests/run.e2e.test.sh`

**Interfaces:**
- Consumes: `run.sh`, `tests/fixtures/claude`, `tests/fixtures/dashboard-stub`, `tests/lib/assert.sh`.
- Produces: the behavioural proof for every product promise in the README's table. Scenarios: happy execute tick (including `runtime/task-<T>.json`), lock conflict (3), dead-owner takeover, PAUSE between phases, STOP killing the phase in flight, a Worker timeout that still reports `by_model`, the **same-tier** re-dispatch, needs-human (2), migration 2→3 (files, `.gitignore`, and the `sprint-*.json` normalisation), halt on a stuck plan (1), sidecar adoption.
- bash 3.2 only, and `shellcheck --severity=warning` clean (`scripts/lint.sh` covers it).
- The whole file skips with a message when `python3` is absent — the harness *is* a Python program, so there is nothing to test without it. That matches how `tests/all.sh` already treats `serve.test.py`.

- [ ] **Step 1: Write the failing test**

Replace `plugins/agent-loop/tests/run.e2e.test.sh`:

```bash
#!/usr/bin/env bash
# End-to-end: drive run.sh against the scripted `claude` stub and assert on the
# artefacts a real operator, the dashboard and the skills read off disk.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
# shellcheck source=../../../tests/lib/assert.sh
source "$ROOT/tests/lib/assert.sh"
RUN="$HERE/../run.sh"

if ! command -v python3 >/dev/null 2>&1; then
  echo "(run.e2e skipped — python3 not installed; the harness is a python program)"
  exit 0
fi

# `[[ ... ]]` followed by `assert_* $?` trips SC2319, so predicates are real commands.
isfile() { [ -f "$1" ]; }
isdir()  { [ -d "$1" ]; }
isint()  { case "${1:-}" in '' | *[!0-9]*) return 1 ;; *) return 0 ;; esac }

LD=".claude/loop/test-run"

# reply <NNN> <json-document>
#   Writes $STUB/NNN.jsonl: one stream-json result line whose text ends in the
#   fenced JSON block the harness parses, with a usage rollup attached.
reply() {
  python3 - "$STUB/$1.jsonl" "$2" <<'PY'
import json
import sys
path, doc = sys.argv[1], sys.argv[2]
record = {"type": "result", "subtype": "success", "is_error": False,
          "result": "ok\n```json\n%s\n```" % doc,
          "modelUsage": {"stub-model": {"costUSD": 0.01, "inputTokens": 10,
                                        "outputTokens": 5,
                                        "cacheReadInputTokens": 0,
                                        "cacheCreationInputTokens": 0}}}
with open(path, "w") as handle:
    handle.write(json.dumps(record) + "\n")
PY
}

# side <NNN> <shell body>
side() { printf '%s\n' "$2" > "$STUB/$1.sh"; }

# contract_for <NNN> <task-id>
#   A side-effect script that writes a valid sprint contract for the task.
contract_for() {
  side "$1" "cat > \"\$RUNTIME_DIR/sprint-$2.json\" <<'JSON'
{\"task\":\"$2\",\"success_criteria\":[\"src/a.ts exports parse\"],
 \"allow_list\":[\"src/a.ts\"],\"forbidden\":[],\"verification\":[\"true\"],
 \"evaluator_must_read\":[],\"evaluator_must_view\":[],
 \"estimated_diff_lines\":5,\"scout_notes\":\"inline\",\"relevant_learnings\":[]}
JSON"
  reply "$1" "{\"contract_path\":\"sprint-$2.json\",\"notes\":\"ok\"}"
}

# setup_case [plan-body]
setup_case() {
  WT="$(mktemp -d)"
  (
    cd "$WT" || exit 1
    git init -q
    git config user.email loop@example.com
    git config user.name Loop
    git config commit.gpgsign false
    mkdir -p src
    printf 'export const a = 0\n' > src/a.ts
    git add -A
    git commit -q -m seed
  ) >/dev/null 2>&1
  mkdir -p "$WT/$LD/runtime"
  STUB="$WT/stub"
  mkdir -p "$STUB"
  printf '%s\n' 'runtime/' 'artifacts/' > "$WT/$LD/.gitignore"
  cat > "$WT/$LD/LOOP_CONFIG.md" <<EOF
Worktree: $WT
Verification pipeline: lint
Tiers: cheap=tier-cheap standard=tier-standard most-capable=tier-big
Dashboard: off
Medic: off
Limits: tick_timeout=120 scout_timeout=30 worker_timeout=30 eval_timeout=30 learner_timeout=30 gate_cmd_timeout=30
EOF
  printf '%s\n' "${1:-- [ ] T1: Add the parser}" > "$WT/$LD/LOOP_PLAN.md"
  export LOOP_DIR="$LD" STUB_SCRIPT="$STUB"
  export PATH="$HERE/fixtures:$PATH"
  export LOOP_NOTIFY=0 AGENT_LOOP_SKIP_POSTMORTEM=1 PAUSE_BETWEEN=0 NP_MAX=99
  export LOOP_DASHBOARD=off
  unset LOOP_DASHBOARD_CMD MOCK_MEDIC_SCRIPT STUB_LOG STUB_DELAY 2>/dev/null || true
}

ev() { jq -r "select(.type==\"$1\") | ${2:-.type}" "$WT/$LD/events.jsonl"; }

# ---------------------------------------------------------------- happy path
setup_case
contract_for 001 T1
side 002 "printf 'export const parse = () => 1\n' > \"$WT/src/a.ts\""
reply 002 '{"status":"complete","summary":"did it"}'
reply 003 '{"verdict":"PASS","findings":[],"views":[],"summary":"criteria met"}'
reply 004 '{"patterns":[],"log":"T1 was straightforward","invariants":[]}'
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "happy path exits 0 once the plan is finished"
assert_eq "done" "$(ev loop_end .reason | tail -1)" "loop_end reason=done"
assert_eq "loop_start" "$(head -1 "$WT/$LD/events.jsonl" | jq -r .type)" "loop_start is first"
assert_eq "loop_end" "$(tail -1 "$WT/$LD/events.jsonl" | jq -r .type)" "loop_end is last"
assert_eq "T1" "$(ev task_status .id | head -1)" "task_status names the task"
assert_eq "continue" "$(ev tick_end .verdict | head -1)" "tick_end verdict=continue"
grep -q '"by_model"' "$WT/$LD/events.jsonl"; assert_true $? "tick_end carries by_model"
grep -q '^- \[x\] T1:' "$WT/$LD/LOOP_PLAN.md"; assert_true $? "the row is marked [x]"
grep -q 'done(+' "$WT/$LD/LOOP_PLAN.md"; assert_true $? "the row carries the sha"
( cd "$WT" && git log --format=%B | grep -q '^Loop-Status: done$' ); assert_true $? "the work commit carries Loop-Status: done"
( cd "$WT" && git log -1 --format=%s ) | grep -q 'T1'; assert_true $? "the subject names the task"
assert_eq "1" "$(wc -l < "$WT/$LD/LOOP_USAGE.jsonl" | tr -d ' ')" "the ledger has one row"
assert_eq "execute" "$(jq -r .mode "$WT/$LD/LOOP_USAGE.jsonl")" "the ledger records the mode"
grep -q '── loop' "$WT/$LD/LOOP_STATUS.md"; assert_true $? "LOOP_STATUS has the session header"
grep -q '^✓ t' "$WT/$LD/LOOP_STATUS.md"; assert_true $? "LOOP_STATUS has a per-tick line"
isfile "$WT/$LD/harness.log"; assert_true $? "harness.log is written"
isfile "$WT/$LD/run.log"; assert_false $? "run.log is gone"
isfile "$WT/$LD/runtime/harness.json"; assert_false $? "the lock is released on exit"
isfile "$WT/$LD/runtime/tick.json"; assert_false $? "tick.json is removed after the tick"
isint "$(cat "$WT/$LD/runtime/HEARTBEAT" 2>/dev/null)"; assert_true $? "HEARTBEAT holds an epoch"
assert_eq "3" "$(cat "$WT/$LD/runtime/schema")" "a fresh dir is stamped schema 3"
isdir "$WT/$LD/artifacts"; assert_true $? "artifacts/ exists"
isfile "$WT/$LD/LOOP_DECISIONS.md"; assert_true $? "LOOP_DECISIONS.md exists"
assert_eq "1" "$(jq -r .attempt "$WT/$LD/runtime/task-T1.json")" "task-T1.json records one attempt"
assert_eq "pass" "$(jq -r '.attempts[0].outcome' "$WT/$LD/runtime/task-T1.json")" "the attempt outcome is pass"
assert_eq "LEARN:done" "$(jq -r .phase "$WT/$LD/runtime/task-T1.json")" "the recorded phase ends at LEARN:done"
isfile "$WT/$LD/runtime/attempts-T1.json"; assert_false $? "nothing writes the old attempts file"

# ------------------------------------------------------------- lock conflict
setup_case
contract_for 001 T1
printf '30\n' > "$STUB/001.sleep"
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 ) &
first=$!
tries=0
while [ "$tries" -lt 200 ]; do
  [ -f "$WT/$LD/runtime/harness.json" ] && break
  sleep 0.1
  tries=$((tries + 1))
done
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 )
assert_eq "3" "$?" "a second harness on a live LOOP_DIR exits 3"
assert_eq "lock-conflict" "$(ev incident .kind | head -1)" "the refusal is an incident"
assert_eq "warn" "$(ev incident .severity | head -1)" "a refused starter is warn severity"
assert_eq "0" "$(ev loop_end .reason | grep -c 'lock-conflict')" "no loop_end in the live loop's stream"
kill "$first" 2>/dev/null
wait "$first" 2>/dev/null

# ------------------------------------------------------------------ takeover
setup_case "- [x] T1: already done"
sleep 0.01 & deadpid=$!
wait "$deadpid"
printf '{"pid":%s,"start_epoch":1,"host":"x","loop_dir":"x","plugin_version":"2.1.0"}' \
  "$deadpid" > "$WT/$LD/runtime/harness.json"
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "a stale lock is taken over and the run completes"
grep -q 'taking over' "$WT/$LD/harness.log"; assert_true $? "the takeover is logged"
assert_eq "harness-crash" "$(ev incident .kind | head -1)" "the takeover raises harness-crash"
assert_eq "warn" "$(ev incident .severity | head -1)" "harness-crash is warn with Medic: off"

# --------------------------------------------------- PAUSE between two phases
setup_case
contract_for 001 T1
side 001 "cat > \"\$RUNTIME_DIR/sprint-T1.json\" <<'JSON'
{\"task\":\"T1\",\"success_criteria\":[\"src/a.ts exports parse\"],
 \"allow_list\":[\"src/a.ts\"],\"forbidden\":[],\"verification\":[\"true\"],
 \"evaluator_must_read\":[],\"evaluator_must_view\":[],
 \"estimated_diff_lines\":5,\"scout_notes\":\"inline\",\"relevant_learnings\":[]}
JSON
touch \"\$RUNTIME_DIR/PAUSE\""
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "PAUSE between phases exits 0"
assert_eq "paused" "$(ev loop_end .reason | tail -1)" "loop_end reason=paused"
assert_eq "T1" "$(jq -r .next_task "$WT/$LD/runtime/CHECKPOINT.json")" "the checkpoint names the next task"
assert_eq "1" "$(cat "$STUB/.n")" "the Worker never ran — PAUSE landed after the Scout"
assert_eq "1" "$(ev paused .tick | wc -l | tr -d ' ')" "one paused event"

# -------------------------------------------- STOP kills the phase in flight
setup_case
side 001 "touch \"\$RUNTIME_DIR/STOP\""
reply 001 '{"contract_path":"none","notes":"never parsed"}'
printf '60\n' > "$STUB/001.sleep"
started="$(date +%s)"
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 )
rc=$?
elapsed=$(( $(date +%s) - started ))
assert_eq "0" "$rc" "STOP exits 0"
assert_eq "paused" "$(ev loop_end .reason | tail -1)" "STOP reports loop_end reason=paused"
[ "$elapsed" -lt 40 ]; assert_true $? "STOP killed the phase instead of waiting it out (${elapsed}s)"
isfile "$WT/$LD/runtime/CHECKPOINT.json"; assert_true $? "STOP writes a checkpoint"

# ---------------------------------- Worker timeout still reports its spend
setup_case
contract_for 001 T1
reply 002 '{"status":"partial","summary":"ran out of time"}'
printf '60\n' > "$STUB/002.sleep"
sed -i.bak 's/worker_timeout=30/worker_timeout=2/' "$WT/$LD/LOOP_CONFIG.md"
rm -f "$WT/$LD/LOOP_CONFIG.md.bak"
( cd "$WT" && MEDIC_MAX_PER_RUN=0 bash "$RUN" >/dev/null 2>&1 )
assert_eq "2" "$?" "a Worker timeout with no medic ends in needs-human"
assert_eq "phase-timeout" "$(ev incident .kind | head -1)" "the timeout is an incident"
grep -q '"by_model"' "$WT/$LD/events.jsonl"; assert_true $? "tick_end still carries by_model after a kill"
assert_eq "1" "$(jq -r 'select(.type=="tick_end") | (.by_model | length)' "$WT/$LD/events.jsonl")" \
  "the killed tick attributes the spend it did incur"
isfile "$WT/$LD/runtime/NEEDS_HUMAN.md"; assert_true $? "NEEDS_HUMAN.md is written"

# ------------------------ NEEDS_WORK re-dispatches at the SAME tier
setup_case
contract_for 001 T1
side 002 "printf 'export const parse = () => 1\n' > \"$WT/src/a.ts\""
reply 002 '{"status":"complete","summary":"first try"}'
reply 003 '{"verdict":"NEEDS_WORK","findings":[],"views":[],"summary":"too thin"}'
side 004 "printf 'export const parse = () => 2\n' > \"$WT/src/a.ts\""
reply 004 '{"status":"complete","summary":"second try"}'
reply 005 '{"verdict":"PASS","findings":[],"views":[],"summary":"good now"}'
reply 006 '{"patterns":[],"log":"needed two goes","invariants":[]}'
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "the re-dispatch reaches done"
assert_eq "tier-standard" "$(jq -r 'select(.type=="role_start" and .role=="Worker") | .model' "$WT/$LD/events.jsonl" | head -1)" \
  "the first Worker runs at the configured tier"
assert_eq "tier-standard" "$(jq -r 'select(.type=="role_start" and .role=="Worker") | .model' "$WT/$LD/events.jsonl" | tail -1)" \
  "the second Worker runs at the SAME tier — there is no ladder in code"
assert_eq "retry" "$(ev decision .decision | head -1)" "the re-dispatch is recorded as a retry decision"
assert_eq "2" "$(jq -r '.attempts | length' "$WT/$LD/runtime/task-T1.json")" "both attempts are recorded"
assert_eq "standard standard" "$(jq -r '[.attempts[].tier] | join(" ")' "$WT/$LD/runtime/task-T1.json")" \
  "both attempts record the same dispatched tier"

# ------------------------------------------------- needs-human on a newer dir
setup_case "- [x] T1: already done"
printf '99' > "$WT/$LD/runtime/schema"
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 )
assert_eq "2" "$?" "a loop dir newer than the plugin exits 2"
assert_eq "needs-human" "$(ev loop_end .reason | tail -1)" "loop_end reason=needs-human"
assert_eq "schema-newer" "$(ev incident .kind | head -1)" "schema-newer incident"
grep -q 'update the plugin' "$WT/$LD/runtime/NEEDS_HUMAN.md"; assert_true $? "NEEDS_HUMAN says to update the plugin"
grep -q 'run.sh' "$WT/$LD/runtime/NEEDS_HUMAN.md"; assert_true $? "NEEDS_HUMAN carries the resume command"
assert_eq "99" "$(cat "$WT/$LD/runtime/schema")" "the stamp is untouched"

# ------------------------------------------------------------ migration 2 -> 3
setup_case "- [x] T1: already done"
printf '2' > "$WT/$LD/runtime/schema"
rm -f "$WT/$LD/.gitignore"
printf '%s' '{"task":"T1","forbidden":["apps/frontend/**"],"allow_list":["src/a.ts"]}' \
  > "$WT/$LD/runtime/sprint-T1.json"
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "a schema-2 dir is migrated and the run completes"
assert_eq "scout" "$(jq -r '.forbidden[0].source' "$WT/$LD/runtime/sprint-T1.json")" \
  "a 2.x string forbidden entry gains scout provenance"
assert_eq "apps/frontend/**" "$(jq -r '.forbidden[0].path' "$WT/$LD/runtime/sprint-T1.json")" \
  "the forbidden path itself survives"
assert_eq "null" "$(jq -r '.render_gate' "$WT/$LD/runtime/sprint-T1.json")" \
  "missing v3 contract fields get their defaults"
assert_eq "3" "$(cat "$WT/$LD/runtime/schema")" "runtime/schema is stamped 3"
assert_eq "2" "$(ev migration .from)" "the migration event records from=2"
assert_eq "3" "$(ev migration .to)" "the migration event records to=3"
isdir "$WT/$LD/artifacts"; assert_true $? "artifacts/ is created"
grep -q 'artifacts/' "$WT/$LD/.gitignore"; assert_true $? "artifacts/ is gitignored"
grep -q 'schema 2 -> 3' "$WT/$LD/harness.log"; assert_true $? "the migration is logged"

# ----------------------------------------------------- halt on a stuck plan
setup_case "$(printf '%s\n' '## Segment A' 'Reviewed: aaa' '- [!] T1: blocked by hand' '- [ ] T2: waits on it | depends_on: T1')"
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 )
assert_eq "1" "$?" "a plan with work left but nothing runnable exits 1"
assert_eq "halt" "$(ev loop_end .reason | tail -1)" "loop_end reason=halt"
assert_eq "1" "$(ev loop_end .exit_code | tail -1)" "loop_end carries exit_code 1"
assert_eq "0" "$(cat "$STUB/.n" 2>/dev/null || echo 0)" "no phase ran"

# -------------------------------------------------------- sidecar adoption
setup_case "- [x] T1: already done"
export LOOP_DASHBOARD=auto
export LOOP_DASHBOARD_CMD="$HERE/fixtures/dashboard-stub"
bash -c 'exec -a serve.py sleep 60' &
dashfake=$!
sleep 0.2
printf '{"pid":%s,"port":7,"url":"http://127.0.0.1:7","sidecar":false}' "$dashfake" \
  > "$WT/$LD/runtime/dashboard.json"
( cd "$WT" && bash "$RUN" >/dev/null 2>&1 )
assert_eq "0" "$?" "the adopting run completes"
grep -q 'dashboard http://127.0.0.1:7 (adopted)' "$WT/$LD/harness.log"; assert_true $? "the existing URL is announced as adopted"
isfile "$WT/$LD/runtime/sidecar.pid"; assert_false $? "no sidecar is spawned when one is adopted"
assert_eq "http://127.0.0.1:7" "$(ev loop_start .dashboard_url | tail -1)" "loop_start carries the adopted URL"
kill -0 "$dashfake" 2>/dev/null; assert_true $? "the standalone dashboard outlives the harness"
assert_eq "$dashfake" "$(jq -r .pid "$WT/$LD/runtime/dashboard.json")" "dashboard.json is untouched"
kill "$dashfake" 2>/dev/null
wait "$dashfake" 2>/dev/null

assert_summary
```

- [ ] **Step 2: Run it to watch it fail against the old suite**

Run: `bash plugins/agent-loop/tests/run.e2e.test.sh`
Expected before Task 20's shim exists: every scenario fails. After Task 20 it should pass; if a scenario fails, fix the runner, not the assertion — each one encodes a promise the README makes.

- [ ] **Step 3: No implementation step**

This task is the test. If a scenario fails, the defect is in `runner/`, and the fix belongs to whichever earlier task owns that module — go back, add the unit test there first, then re-run this file.

- [ ] **Step 4: Run the whole plugin suite**

Run: `bash plugins/agent-loop/tests/all.sh`
Expected: every section passes, ending in `PASS: <n>  FAIL: 0`.
Run: `bash scripts/lint.sh`
Expected: `shellcheck clean (N files)`.

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/tests/run.e2e.test.sh
git commit -F - <<'EOF'
agent-loop: rewrite the e2e against the scripted stub, eleven scenarios

One assertion set per product promise: a happy execute tick that commits with
trailers and marks the row, the lock refusal at exit 3 that writes no loop_end
into the live loop's stream, dead-owner takeover, PAUSE landing between two
phases, STOP killing the phase in flight rather than waiting it out, a Worker
timeout whose tick_end still attributes the spend, a NEEDS_WORK re-dispatch
that role_start proves ran at the SAME tier as the first, needs-human at exit 2,
migration 2->3 stamping the schema, gitignoring artifacts/ and normalising a
2.x contract's bare forbidden strings, halt at exit 1 on a stuck plan, and
dashboard adoption leaving the standalone observer running. runtime/task-T.json
is asserted on the happy path and across both attempts of the retry. All four
exit codes are covered end to end.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

### Task 22: docs, the `validate.sh` check, and the green suite

**Files:**
- Modify: `docs/testing.md`
- Verify (probably no edit): `scripts/validate.sh`
- Test: the whole suite

**Interfaces:**
- Consumes: everything.
- Produces: a repo whose `bash scripts/test-all.sh` is green with the runner in place. The plugin README, `plugin.json` version, templates and skills are **plan D's**; do not touch them here.

- [ ] **Step 1: Check whether `scripts/validate.sh` needs a change**

Run:

```bash
git ls-files -s plugins/agent-loop | awk '$1 == "100755" {print $4}'
```

Expected output, exactly:

```
plugins/agent-loop/run.sh
plugins/agent-loop/tests/fixtures/claude
plugins/agent-loop/tests/fixtures/dashboard-stub
```

All three are already on `EXEC_OK` in `scripts/validate.sh`, and R9 only flags files tracked as `100755` — every `runner/**.py` and `tests/runner/**.py` is `644` and is invoked through an interpreter. **So no edit is needed.** If the command prints anything else, a file picked up the wrong mode: `chmod 644 <path>` it rather than widening the allowlist.

Run: `bash scripts/validate.sh`
Expected: `validate: all invariants hold`.

- [ ] **Step 2: Update `docs/testing.md`**

In the numbered list, replace the second item's closing sentence with a paragraph that names the new layer. Change:

```
   `scripts/validate.sh` only checks that the
   file exists; the substance of what it asserts is a review concern (R5),
   not something a script can judge.
```

to:

```
   `scripts/validate.sh` only checks that the
   file exists; the substance of what it asserts is a review concern (R5),
   not something a script can judge.

   A plugin whose implementation is Python puts its unit tests in
   `plugins/<name>/tests/runner/test_*.py` and has `tests/all.sh` run them with
   `python3 -m unittest discover -s tests/runner -p 'test_*.py'`, skipping with
   a printed note when `python3` is absent — the same treatment `serve.test.py`
   already gets. `agent-loop` is the only plugin in that shape: its harness is
   `runner/run.py` and `run.sh` is a four-line shim, so `tests/run.e2e.test.sh`
   skips too when there is no `python3` to run.
```

- [ ] **Step 3: Run the full suite**

Run: `bash scripts/test-all.sh`
Expected, in order: `### validate` ending `validate: all invariants hold`; `### lint` ending `shellcheck clean (N files)`; `### plugins/agent-loop/tests/all.sh` ending `PASS: <n>  FAIL: 0`; the other plugins' suites; `### node --test`; and finally `test-all: PASS`.

**Paste that output into the task's completion note.** Per `CLAUDE.md` the work is not done without it. If `lint` reports it was skipped (exit 2, shellcheck not installed) that is a SKIP, not a pass — install shellcheck (`brew install shellcheck`) and re-run before claiming the task.

- [ ] **Step 4: Confirm nothing in plan D's scope moved**

Run:

```bash
git diff --name-only main...HEAD -- plugins/agent-loop/README.md \
    plugins/agent-loop/.claude-plugin/plugin.json plugins/agent-loop/templates \
    plugins/agent-loop/skills plugins/agent-loop/web
```

Expected: empty. Those files belong to plan D; if anything shows up, revert it here and raise it in plan D.

- [ ] **Step 5: Commit**

```bash
git add docs/testing.md
git commit -F - <<'EOF'
agent-loop: document where the runner's unit tests live

docs/testing.md's layer 2 now names the tests/runner/ convention and the
python3-absent skip, so the next person adding a python-implemented plugin
finds the pattern instead of inventing one. scripts/validate.sh needed no
change: R9 only flags files tracked 100755, and the runner is all 644.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
```

---

## Handover to plans B, C and D

After Task 22 the runner is complete and green, and the seams the other plans need are in place:

- **Plan B** hooks `run_visual_checks` into `execute_tick` at the marked comment between GATE and EVALUATE, fills `run_evaluator`'s `must_read_blocks`/`screenshots`, extends `cfg.render`, uses `git_ops.similarity`, and records each screenshot through `task_state.TaskState.add_artifact` so `runtime/task-<T>.json`'s `artifacts` is populated.
- **Plan C** replaces the body of `handle_failure` at its `# plan C` marker with `judge.decide(...)` and the retry branch in `execute_tick` (bounded by `cfg.limits["max_attempts"]`, not the literal two); implements `run_worker(wrapup=…, resume_session=…)` where plan A raises `NotImplementedError`, and turns `checkpoint_note` into the real §6 wrap-up/resume; passes the Judge's `changes.tier` through `run_worker_attempt(..., tier=…)` — the one path that may change a re-attempt's tier, since no ladder exists in code; adds `Plan.set_blocked_by`; feeds the Judge's `changes.sub_rows` to `Plan.split`, which renumbers them to the next free numeric ids and appends `| split_of:`; routes the no-state-file branch of `boot_reconcile` to the Judge with `failure="boot-reconcile"`; and fills `incident-<id>.json`'s `phases: []` from `runtime/task-<T>.json` (**not** `attempts-<T>.json`, which no longer exists).
- **Plan D** bumps `plugin.json` to `3.0.0`, adds `/api/stop` and the artifacts panel to `serve.py`, rewrites the templates and the four skills, adds `tests/prompts.contract.sh` — which must allow `{{evaluator_findings}}` alongside the other placeholders listed in Task 13 — documents `python3 -m runner.calibrate <loop-dir>` and the paste-the-`Limits:`-line workflow in the README, and documents the schema-3 upgrade under the README's existing `Upgrading` anchor.
