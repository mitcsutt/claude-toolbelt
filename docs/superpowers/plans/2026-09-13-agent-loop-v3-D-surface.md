# agent-loop v3 — Plan D: dashboard, skills, docs, version

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring every human-facing surface of `plugins/agent-loop` onto the v3 phase runner — a STOP control and an artifacts/decisions view in the dashboard, four skills that describe phases rather than a tick prompt, templates carrying `Tiers:`/`Decision policy:`/the new plan-row tags, prompt-contract tests over `runner/prompts/*.md`, and version `3.0.0` with a schema-3 upgrade note.

**Architecture:** Nothing here owns loop behaviour. `web/serve.py` stays a pure reader of `$LOOP_DIR` plus a thin PAUSE/STOP control surface: STOP is one more sentinel file, `artifact`/`decision`/`resume`/`split` are three more event types folded into the bounded `EventStore`, and one new read-only route serves recorded screenshots through an index (never through a path built from the query string). The skills and templates are prose contracts pinned by `grep`-based `*.contract.sh` suites, so every prose change in this plan lands with the assertion that will catch its regression.

**Tech Stack:** Python 3.9 stdlib (`http.server`, `unittest`), bash 3.2, vanilla ES5-compatible JS in one HTML file, Markdown.

**Spec:** `docs/superpowers/specs/2026-09-13-agent-loop-v3-phase-runner-design.md` — this plan covers §10, §11 items 7/9/10/11, §12 (prompt-contract tests, `web.contract.sh`), §13, and the dashboard-facing halves of §4.4/§4.5.

**Interface contract:** `docs/superpowers/plans/2026-09-13-agent-loop-v3-00-interfaces.md` — every name used here is verbatim from it.

## Global Constraints

- Python 3.9 stdlib only; no third-party packages. macOS `/usr/bin/python3` is 3.9.
- bash 3.2 for any `.sh`: no `mapfile`, no `declare -A`, no `${var,,}`; under `set -u`, expanding `"${arr[@]}"` on an **empty** array is fatal (`${#arr[@]}` is safe).
- `run.sh` remains 755; every other tracked file is 644. `scripts/validate.sh` R9 enforces this against the `EXEC_OK` allowlist.
- Every runtime file the harness writes is atomic (`<path>.tmp` then `os.replace`). Nothing in this plan writes a runtime file except the `PAUSE`/`STOP` sentinels, which are empty.
- No model name appears anywhere in the plugin except the `Tiers:` config line and the template default `Tiers: cheap=haiku standard=sonnet most-capable=opus`.
- `LOOP_SCHEMA = 3`. Plugin version `3.0.0`.
- Exit codes: 0 done/paused/rate-limit-exit · 1 halt/error · 2 needs-human · 3 lock-conflict.
- Events envelope: one compact JSON object per line, fields `t` (epoch int), `seq` (int, monotonic per harness run), `type`, then payload.
- `LOOP_PLAN.md` task regex (serve.py `_TASK_RE`): `^\s*- \[(.)\] (.*)$`, glyphs ` ~ x ! -`, segment = a line starting `## `, id = first `\bT\d+\b`.
- `bash scripts/test-all.sh` green is the definition of done.
- **Order:** this plan runs LAST. Plans A, B and C have already created `runner/`, `runner/prompts/*.md`, `LOOP_DECISIONS.md`, `artifacts/`, `harness.log`, and have deleted `lib/*.sh` and `tick-prompt.md`. If `plugins/agent-loop/runner/prompts/` does not exist when you start Task 9, stop and report — Plan A has not landed.
- Every commit message starts `agent-loop: ` and ends with these two trailer lines, in this order:

  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
  ```

## File Structure

| File | Responsibility after this plan |
| --- | --- |
| `plugins/agent-loop/web/serve.py` | Reads `$LOOP_DIR`; adds the STOP sentinel to liveness/status/Supervisor, the `harness.log` tail with a `run.log` fallback, the `artifact`/`decision`/`resume`/`split` event folds, and `GET /api/artifact`. |
| `plugins/agent-loop/web/dashboard.html` | Adds a "Stop now" control beside Pause, an artifact thumbnail strip in the *This tick* band, a Decisions band, and a log heading that names the file it is showing. |
| `plugins/agent-loop/tests/serve.test.py` | Python unittest for all four serve.py changes, including the traversal refusal. |
| `plugins/agent-loop/tests/web.contract.sh` | End-to-end handshake: `/api/stop` against a live and a dead harness, the new element ids, the new snapshot keys. |
| `plugins/agent-loop/skills/agent-loop/SKILL.md` | Attach: Stop-now vs Pause, `LOOP_DECISIONS.md` in the health block, `harness.log`. |
| `plugins/agent-loop/skills/agent-loop-setup/SKILL.md` | Wizard: `Tiers:` replaces the orchestrator-model explanation; `Decision policy:`; the new plan-row tags. |
| `plugins/agent-loop/skills/agent-loop-postmortem/SKILL.md` | Reads `LOOP_DECISIONS.md` and `artifacts/`; usage from `tick_end.by_model` for every tick. |
| `plugins/agent-loop/templates/LOOP_CONFIG.md` | `Tiers:`, `Decision policy:`, the §4.2 `Limits:` keys; no orchestrator-model paragraph. |
| `plugins/agent-loop/templates/LOOP_PLAN.md` | Legend gains `\| no-ui`, `\| copy_of:`, `\| blocked_by:`, `\| split_of:`. |
| `plugins/agent-loop/tests/prompts.contract.sh` | **New.** Replaces `tests/tick-prompt.contract.sh`; asserts the JSON shape, the non-interactivity rule and the role invariants of every `runner/prompts/*.md`. |
| `plugins/agent-loop/tests/setup.contract.sh`, `tests/postmortem.contract.sh` | Updated assertions for the prose above. |
| `plugins/agent-loop/README.md` | Rewritten on the 8-section skeleton around the phase runner. |
| `plugins/agent-loop/.claude-plugin/plugin.json` | `3.0.0`, description rewritten, no model names. |
| `README.md` (repo root) | `agent-loop` table row + Plugin-details section rewritten. |

Untouched on purpose: `.claude-plugin/marketplace.json` (entry is `name`/`source`/`category` only — R2), `skills/agent-loop-medic/SKILL.md` and `tests/medic.contract.sh` (Plan C owns the medic's phase timeline), `templates/LOOP_KNOWLEDGE.md`, `templates/LOOP_LEARNINGS.md`.

---

### Task 1: `/api/stop` writes the STOP sentinel; `derive_status` treats it as PAUSE

**Files:**
- Modify: `plugins/agent-loop/web/serve.py` (`liveness`, `derive_status` branches 4 and 5, `Supervisor`)
- Test: `plugins/agent-loop/tests/serve.test.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `Supervisor.stop_path` (property, absolute path of `runtime/STOP`); `Supervisor.stop() -> dict | None` returning `{"error": "no live harness to stop"}` when refused; `liveness()` dict gains `"stop": bool` and folds STOP into the existing `"pause": bool`.

Background: the v2 `/api/stop` wrote PAUSE **and** SIGTERMed the harness pid, because a bash tick had no other stopping point. The v3 runner checks `runtime/STOP` between phases and SIGTERMs the phase itself (spec §4.4), so the dashboard's job is only to write the file. A STOP file with no harness to read it is a trap for the next launch, so the write is refused when no harness is alive — which is also why `renderMast` already disables the button when `health.harness.alive` is false.

- [ ] **Step 1: Write the failing tests**

Add to `plugins/agent-loop/tests/serve.test.py`, inside `class TestDeriveStatus`, immediately after `test_pausing_while_harness_alive`:

```python
    def test_stop_file_is_pausing_like_pause(self):
        s = status_of([
            {"t": 90, "type": "loop_start"},
            {"t": 100, "type": "tick_start", "tick": 7},
        ], pause=True, stop=True)
        self.assertEqual(s["state"], "pausing")
        self.assertIn("stop requested", s["why"])

    def test_stop_file_outliving_a_dead_harness_is_paused(self):
        s = status_of([
            {"t": 90, "type": "loop_start"},
            {"t": 100, "type": "tick_start", "tick": 7},
        ], pause=True, stop=True, harness_pid_alive=False)
        self.assertEqual(s["state"], "paused")
        self.assertIn("STOP present", s["why"])
```

In the same file, extend the `live()` helper's `base` dict with `"stop": False` (it sits directly under `"pause": False`):

```python
    base = {"pause": False, "stop": False, "harness_pid": 4812,
            "harness_pid_alive": True,
            "harness_started_at": 900, "heartbeat_age_s": 1,
            "tick_pid": 4999, "tick_pid_alive": True, "tick_no": 7,
            "tick_started_at": None, "tick_timeout_s": 1800,
            "activity_age_s": 2}
```

Replace `TestSupervisorGuards.test_stop_sigterms_the_recorded_harness` entirely with:

```python
    def test_stop_writes_the_stop_sentinel_for_a_live_harness(self):
        sup, d = self._sup(pid=777)
        killed = []
        orig_alive, orig_kill = serve.process_alive, os.kill
        serve.process_alive = lambda pid, must_contain=None: True
        os.kill = lambda pid, sig: killed.append((pid, sig))
        try:
            res = sup.stop()
        finally:
            serve.process_alive, os.kill = orig_alive, orig_kill
        self.assertIsNone(res)
        self.assertEqual(killed, [])          # the runner stops itself; no signal from here
        self.assertTrue(os.path.exists(os.path.join(d, "runtime", "STOP")))
        self.assertFalse(os.path.exists(os.path.join(d, "runtime", "PAUSE")))

    def test_stop_refuses_when_no_harness_is_alive(self):
        sup, d = self._sup(pid=777)
        orig = serve.process_alive
        serve.process_alive = lambda pid, must_contain=None: False
        try:
            res = sup.stop()
        finally:
            serve.process_alive = orig
        self.assertEqual(res, {"error": "no live harness to stop"})
        self.assertFalse(os.path.exists(os.path.join(d, "runtime", "STOP")))

    def test_resume_clears_a_stop_left_by_a_previous_run(self):
        sup, d = self._sup(pid=777)
        os.makedirs(os.path.join(d, "runtime"), exist_ok=True)
        open(os.path.join(d, "runtime", "STOP"), "w").close()
        orig = serve.process_alive
        serve.process_alive = lambda pid, must_contain=None: False
        try:
            self.assertIsNone(sup.resume())
        finally:
            serve.process_alive = orig
        self.assertFalse(os.path.exists(os.path.join(d, "runtime", "STOP")))
        self.assertEqual(len(sup.spawned), 1)
```

Replace `TestSupervisor.test_stop_creates_pause` (that class's `_sup` writes no `harness.json`, so `harness_pid()` is `None`) with:

```python
    def test_stop_refuses_without_a_harness_json(self):
        sup, d = self._sup()
        self.assertEqual(sup.stop(), {"error": "no live harness to stop"})
        self.assertFalse(os.path.exists(os.path.join(d, "runtime", "STOP")))
```

And add to `class TestLivenessFiles`:

```python
    def test_stop_sentinel_sets_both_flags(self):
        d = tempfile.mkdtemp()
        rt = os.path.join(d, "runtime")
        os.makedirs(rt, exist_ok=True)
        open(os.path.join(rt, "STOP"), "w").close()
        lv = serve.liveness(d, 1000)
        self.assertTrue(lv["stop"])
        self.assertTrue(lv["pause"])       # STOP is a strictly stronger PAUSE
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 plugins/agent-loop/tests/serve.test.py -v 2>&1 | tail -30`
Expected: FAIL — `KeyError`/`AssertionError` on `stop`, and `test_stop_writes_the_stop_sentinel_for_a_live_harness` failing because `killed == [(777, signal.SIGTERM)]`.

- [ ] **Step 3: Implement it in `web/serve.py`**

In `liveness()`, replace the `"pause"` entry of the returned dict with these two lines (keep every other key exactly as it is):

```python
        "pause": (os.path.exists(os.path.join(rt, "PAUSE"))
                  or os.path.exists(os.path.join(rt, "STOP"))),
        "stop": os.path.exists(os.path.join(rt, "STOP")),
```

In `derive_status`, branch 4, replace the two-line PAUSE clause:

```python
        if live.get("pause"):
            what = "STOP" if live.get("stop") else "PAUSE"
            return out("paused", None, "%s present, harness not running" % what)
```

In `derive_status`, branch 5, replace the whole clause:

```python
    # 5. PAUSE or STOP requested, harness still finishing the phase
    if live.get("pause"):
        if live.get("stop"):
            return out("pausing", live.get("tick_started_at"),
                       "stop requested — ends at the next phase boundary")
        tick = live.get("tick_no") or store.cur_tick
        return out("pausing", live.get("tick_started_at"),
                   "stops after tick %s" % (tick if tick is not None else "?"))
```

In `Supervisor`, add a `stop_path` property directly after `pause_path`:

```python
    @property
    def stop_path(self):
        return os.path.join(self.runtime_dir, "STOP")
```

Add a refusal helper directly after `_refuse_if_live`:

```python
    def _refuse_unless_live(self):
        if self.harness_pid() is None:
            return {"error": "no live harness to stop"}
        return None
```

Replace the bodies of `start` and `resume` where they remove the pause file. In `start`, replace:

```python
            try:
                os.remove(self.pause_path)
            except OSError:
                pass
```

with:

```python
            self._clear_sentinels()
```

and make the identical substitution in `resume`. Add the helper directly above `start`:

```python
    def _clear_sentinels(self):
        """Remove both stop sentinels before (re)starting a harness.

        A STOP left by the previous run would otherwise make the new harness
        exit at its first phase boundary, which reads as "the loop died again".
        """
        for path in (self.pause_path, self.stop_path):
            try:
                os.remove(path)
            except OSError:
                pass
```

Replace `Supervisor.stop` entirely:

```python
    def stop(self):
        """Request an immediate stop by writing runtime/STOP.

        The v3 runner checks STOP between phases: it SIGTERMs the phase in
        flight (a Worker gets its wrap-up continuation first), writes
        CHECKPOINT.json and exits 0 — so a stop lands within minutes instead
        of at the end of a 30-minute tick, and no signal is sent from here.
        Refused when no harness is alive: a sentinel nobody reads only arms a
        trap for the next launch, and the button is disabled in that state.
        """
        with self._lock:
            refusal = self._refuse_unless_live()
            if refusal:
                return refusal
            os.makedirs(self.runtime_dir, exist_ok=True)
            open(self.stop_path, "w").close()
            return None
```

`import signal` at the top of the file now has no remaining use — delete that line.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 plugins/agent-loop/tests/serve.test.py 2>&1 | tail -5`
Expected: `OK`. If `NameError: name 'signal' is not defined` appears, a caller was missed — run `grep -n 'signal\.' plugins/agent-loop/web/serve.py` and restore the import if it returns a hit.

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/web/serve.py plugins/agent-loop/tests/serve.test.py
git commit -m "$(cat <<'EOF'
agent-loop: /api/stop writes runtime/STOP; derive_status reads it as PAUSE

The v3 runner checks STOP between phases, so the dashboard only has to write
the file — the v2 SIGTERM existed because a bash tick had no earlier stopping
point. Refused when no harness is alive, so a stale sentinel cannot kill the
next launch.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
)"
```

---

### Task 2: the run-log tail reads `harness.log`, falling back to `run.log`

**Files:**
- Modify: `plugins/agent-loop/web/serve.py` (new `_log_tail`, used by `build_snapshot`)
- Test: `plugins/agent-loop/tests/serve.test.py`

**Interfaces:**
- Consumes: `_tail_lines(path, n=12, window=65536) -> list[str]` (already in serve.py).
- Produces: `_log_tail(loop_dir, n=12) -> (list[str], str)` — lines plus the basename they came from. Snapshot gains `"log_file"` alongside the existing `"log"`.

Spec §4.5: v3 stops teeing a `run.log` (one v2 run reached 105 MB); the harness writes its own lines to `harness.log`, rotated at 10 MB × 3, and each phase's full transcript lives in the session JSONL its `phase_end` event names. A v2 loop dir has only `run.log`, so the fallback keeps a mid-migration dir readable.

- [ ] **Step 1: Write the failing test**

Add to `plugins/agent-loop/tests/serve.test.py`, directly after `class TestTailLines`:

```python
class TestLogTail(unittest.TestCase):
    """v3 writes harness.log; a v2 dir still has only run.log."""

    def test_prefers_harness_log(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "harness.log"), "w") as f:
            f.write("phase scout ok\n")
        with open(os.path.join(d, "run.log"), "w") as f:
            f.write("old v2 line\n")
        lines, name = serve._log_tail(d)
        self.assertEqual(lines, ["phase scout ok"])
        self.assertEqual(name, "harness.log")

    def test_falls_back_to_run_log(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "run.log"), "w") as f:
            f.write("old v2 line\n")
        lines, name = serve._log_tail(d)
        self.assertEqual(lines, ["old v2 line"])
        self.assertEqual(name, "run.log")

    def test_neither_file_is_not_an_error(self):
        self.assertEqual(serve._log_tail(tempfile.mkdtemp()), ([], "harness.log"))
```

And add to `class TestBuildSnapshot` (reuse whatever loop-dir helper that class already uses to build `snap`; the assertion is on the snapshot dict):

```python
    def test_snapshot_names_the_log_file_it_tailed(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "runtime"), exist_ok=True)
        with open(os.path.join(d, "harness.log"), "w") as f:
            f.write("tick 3 phase evaluator rc=0\n")
        store = mk([{"t": 1, "type": "loop_start"}], loop_dir=d)
        snap = serve.build_snapshot(d, store, live(), {"state": "idle"}, NOW)
        self.assertEqual(snap["log_file"], "harness.log")
        self.assertEqual(snap["log"], ["tick 3 phase evaluator rc=0"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 plugins/agent-loop/tests/serve.test.py -v 2>&1 | grep -E 'TestLogTail|log_file|FAILED|ERROR' | head`
Expected: FAIL with `AttributeError: module 'serve' has no attribute '_log_tail'`.

- [ ] **Step 3: Implement it**

Add to `web/serve.py` directly after `_tail_lines`:

```python
def _log_tail(loop_dir, n=12):
    """Last `n` lines of the harness's own log, and the basename they came from.

    v3 does not tee a run.log — one v2 run left a 105 MB file nobody read. The
    harness writes its own lines to harness.log (rotated 10 MB x 3) and each
    phase's full transcript stays in the session JSONL its phase_end event
    names. A v2 loop dir has only run.log, so the fallback keeps a dir that has
    not been migrated yet readable from a v3 dashboard.
    """
    for name in ("harness.log", "run.log"):
        path = os.path.join(loop_dir, name)
        if os.path.exists(path):
            return _tail_lines(path, n), name
    return [], "harness.log"
```

In `build_snapshot`, replace the final `"log": ...` entry of the returned dict. Because `_log_tail` returns a tuple, compute it just above the `return` statement (next to the `dash = _read_json(...)` line):

```python
    log_lines, log_file = _log_tail(loop_dir, 12)
```

and in the returned dict replace:

```python
        "log": _tail_lines(os.path.join(loop_dir, "run.log"), 12),
```

with:

```python
        "log": log_lines,
        "log_file": log_file,
```

Finally update the module docstring: replace the sentence `Status is derived from events.jsonl + PID liveness only. run.log is forensic:` with `Status is derived from events.jsonl + PID liveness only. harness.log (run.log on an unmigrated v2 dir) is forensic:` — the following two sentences still read correctly.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 plugins/agent-loop/tests/serve.test.py 2>&1 | tail -5`
Expected: `OK`. `TestRunLogNeverDecidesStatus` must still pass — it writes `run.log` and asserts the status ignores it, which the fallback preserves.

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/web/serve.py plugins/agent-loop/tests/serve.test.py
git commit -m "$(cat <<'EOF'
agent-loop: dashboard tails harness.log, falling back to run.log

v3 stops teeing run.log (one run left 105 MB); the harness writes harness.log
and phase transcripts stay in their session JSONL. The fallback keeps an
unmigrated v2 loop dir readable. The snapshot now names the file it tailed.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
)"
```

---

### Task 3: `EventStore` folds `artifact`, `decision`, `resume` and `split` events

**Files:**
- Modify: `plugins/agent-loop/web/serve.py` (`EventStore._reset`, `EventStore._ingest`, caps, `build_snapshot`)
- Test: `plugins/agent-loop/tests/serve.test.py`

**Interfaces:**
- Consumes: event shapes from the interface contract — `artifact` (`tick`, `task`, `name`, `path`), `decision` (`task`, `decision`, `classification`), `resume` (`task`, `attempt`), `split` (`task`, `into`).
- Produces: `EventStore.artifacts` — `list[{"name","path"}]` for the tick in flight, cleared on `tick_start`; `EventStore.artifact_ix` — `dict[(tick, name)] -> path`, bounded, surviving tick boundaries (Task 4 reads it); `EventStore.decisions` — bounded `list[{"t","type","task","decision","classification","attempt","into"}]`. Snapshot gains `current.artifacts` and a top-level `decisions` list.

None of these four types goes into `LIFECYCLE_TYPES`: they are per-tick detail, and adding them would change what `store.lifecycle`/`store.last()` mean for `derive_status`. The decisions list is separate and survives ticks because `LOOP_DECISIONS.md` is a run-level audit trail (spec §7) — a human ratifies or reverses those choices after the fact.

- [ ] **Step 1: Write the failing test**

Add to `plugins/agent-loop/tests/serve.test.py`, directly after `class TestEventStore` ends:

```python
class TestArtifactAndDecisionFold(unittest.TestCase):
    """v3 event types: artifacts belong to the tick, decisions to the run."""

    def test_artifacts_fold_into_the_current_tick(self):
        store = mk([
            {"t": 1, "seq": 1, "type": "tick_start", "tick": 4},
            {"t": 2, "seq": 2, "type": "artifact", "tick": 4, "task": "T60",
             "name": "orgunits-list", "path": "artifacts/T60/orgunits-list.png"},
            {"t": 3, "seq": 3, "type": "artifact", "tick": 4, "task": "T60",
             "name": "orgunits-empty", "path": "artifacts/T60/orgunits-empty.png"},
        ])
        self.assertEqual(store.artifacts, [
            {"name": "orgunits-list", "path": "artifacts/T60/orgunits-list.png"},
            {"name": "orgunits-empty", "path": "artifacts/T60/orgunits-empty.png"},
        ])

    def test_a_new_tick_clears_the_previous_tick_artifacts(self):
        store = mk([
            {"t": 1, "seq": 1, "type": "tick_start", "tick": 4},
            {"t": 2, "seq": 2, "type": "artifact", "tick": 4, "task": "T60",
             "name": "a", "path": "artifacts/T60/a.png"},
            {"t": 3, "seq": 3, "type": "tick_start", "tick": 5},
        ])
        self.assertEqual(store.artifacts, [])
        # ...but the index keeps them addressable for a page still showing tick 4
        self.assertEqual(store.artifact_ix[(4, "a")], "artifacts/T60/a.png")

    def test_artifact_without_a_name_or_path_is_ignored(self):
        store = mk([
            {"t": 1, "seq": 1, "type": "tick_start", "tick": 4},
            {"t": 2, "seq": 2, "type": "artifact", "tick": 4, "name": "a"},
            {"t": 3, "seq": 3, "type": "artifact", "tick": 4, "path": "x.png"},
        ])
        self.assertEqual(store.artifacts, [])

    def test_decisions_survive_tick_boundaries(self):
        store = mk([
            {"t": 1, "seq": 1, "type": "tick_start", "tick": 4},
            {"t": 2, "seq": 2, "type": "decision", "task": "T60",
             "decision": "widen", "classification": "self-imposed"},
            {"t": 3, "seq": 3, "type": "resume", "task": "T60", "attempt": 2},
            {"t": 4, "seq": 4, "type": "tick_start", "tick": 5},
            {"t": 5, "seq": 5, "type": "split", "task": "T60",
             "into": ["T74", "T75"]},
        ])
        self.assertEqual([d["type"] for d in store.decisions],
                         ["decision", "resume", "split"])
        self.assertEqual(store.decisions[0]["decision"], "widen")
        self.assertEqual(store.decisions[0]["classification"], "self-imposed")
        self.assertEqual(store.decisions[1]["decision"], "resume")   # type is the default
        self.assertEqual(store.decisions[1]["attempt"], 2)
        self.assertEqual(store.decisions[2]["into"], ["T74", "T75"])

    def test_decisions_are_capped(self):
        evs = [{"t": 1, "seq": 1, "type": "tick_start", "tick": 1}]
        for i in range(serve._MAX_DECISIONS + 25):
            evs.append({"t": 2, "seq": i + 2, "type": "decision",
                        "task": "T%d" % i, "decision": "retry"})
        store = mk(evs)
        self.assertEqual(len(store.decisions), serve._MAX_DECISIONS)
        self.assertEqual(store.decisions[-1]["task"],
                         "T%d" % (serve._MAX_DECISIONS + 24))

    def test_none_of_the_new_types_becomes_a_lifecycle_event(self):
        for typ in ("artifact", "decision", "resume", "split"):
            self.assertNotIn(typ, serve.LIFECYCLE_TYPES)

    def test_snapshot_carries_artifacts_and_decisions(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "runtime"), exist_ok=True)
        store = mk([
            {"t": 1, "seq": 1, "type": "loop_start"},
            {"t": 2, "seq": 2, "type": "tick_start", "tick": 4},
            {"t": 3, "seq": 3, "type": "artifact", "tick": 4, "task": "T60",
             "name": "a", "path": "artifacts/T60/a.png"},
            {"t": 4, "seq": 4, "type": "decision", "task": "T60",
             "decision": "defer", "classification": "open"},
        ], loop_dir=d)
        snap = serve.build_snapshot(d, store, live(), {"state": "running"}, NOW)
        self.assertEqual(snap["current"]["artifacts"],
                         [{"name": "a", "path": "artifacts/T60/a.png"}])
        self.assertEqual(snap["decisions"][-1]["decision"], "defer")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 plugins/agent-loop/tests/serve.test.py -v 2>&1 | grep -E 'TestArtifactAndDecision|FAILED|ERROR' | head`
Expected: FAIL with `AttributeError: 'EventStore' object has no attribute 'artifacts'`.

- [ ] **Step 3: Implement it**

In `web/serve.py`, add two caps next to the existing ones (below `_MAX_INCIDENTS`):

```python
_MAX_ARTIFACTS = 400
_MAX_DECISIONS = 200
```

In `EventStore._reset`, add three lines directly below `self._incident_ix = {}`:

```python
        self.artifacts = []          # the tick in flight only
        self.artifact_ix = {}        # (tick, name) -> path, across ticks
        self.decisions = []          # run-level audit trail (LOOP_DECISIONS.md)
```

In `EventStore._ingest`, inside the `if typ == "tick_start":` block, add one line below `self.cur_sha = None`:

```python
            self.artifacts = []
```

Still in `_ingest`, add two branches to the `elif` chain, directly above `elif typ == "tick_end":`:

```python
        elif typ == "artifact":
            name, path = ev.get("name"), ev.get("path")
            if name and path:
                self.artifacts.append({"name": name, "path": path})
                _cap(self.artifacts, _MAX_ARTIFACTS)
                # Addressable after the tick rolls over: a browser tab still
                # showing tick 4 must keep loading tick 4's screenshots.
                self.artifact_ix[(ev.get("tick"), name)] = path
                if len(self.artifact_ix) > _MAX_ARTIFACTS:
                    self.artifact_ix.pop(next(iter(self.artifact_ix)))
        elif typ in ("decision", "resume", "split"):
            self.decisions.append({
                "t": ev.get("t"), "type": typ, "task": ev.get("task"),
                "decision": ev.get("decision") or typ,
                "classification": ev.get("classification"),
                "attempt": ev.get("attempt"), "into": ev.get("into"),
            })
            _cap(self.decisions, _MAX_DECISIONS)
```

In `build_snapshot`, extend the `current` assignment:

```python
    current = dict(derive_current(store.current), tick=store.cur_tick,
                   task=store.cur_task, artifacts=list(store.artifacts))
```

and add one entry to the returned dict, directly after `"incidents": list(store.incidents),`:

```python
        "decisions": list(store.decisions[-20:]),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 plugins/agent-loop/tests/serve.test.py 2>&1 | tail -5`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/web/serve.py plugins/agent-loop/tests/serve.test.py
git commit -m "$(cat <<'EOF'
agent-loop: fold artifact/decision/resume/split events into the snapshot

Artifacts belong to the tick in flight and are cleared at the next tick_start,
but stay addressable through a bounded (tick, name) index so an open tab keeps
loading them. Decisions are run-level — they are the dashboard's view of
LOOP_DECISIONS.md. None of the four is a lifecycle type, so derive_status is
untouched.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
)"
```

---

### Task 4: `GET /api/artifact?tick=&name=` serves a recorded screenshot

**Files:**
- Modify: `plugins/agent-loop/web/serve.py` (module imports, new `artifact_path`, `_make_handler.do_GET`)
- Test: `plugins/agent-loop/tests/serve.test.py`

**Interfaces:**
- Consumes: `EventStore.artifact_ix` from Task 3.
- Produces: `artifact_path(loop_dir, store, tick, name) -> str | None` — an absolute, existing file path under `$LOOP_DIR/artifacts/`, or `None`. Route `GET /api/artifact?tick=<int>&name=<str>` → 200 with the image bytes, 404 for anything unknown or out of bounds, 413 above 8 MB.

The query string never becomes a path. `(tick, name)` is a lookup key into the index the `artifact` events built; the path that comes back is still untrusted — the Scout names screenshot paths in `render_gate.screenshots` (spec §5.3) and a compromised or careless contract could name `../../../../etc/passwd`. So the recorded path is `realpath`-resolved (which collapses `..` *and* follows symlinks) and must then sit inside the `realpath` of `$LOOP_DIR/artifacts`.

- [ ] **Step 1: Write the failing test**

Add to `plugins/agent-loop/tests/serve.test.py`, directly after `class TestArtifactAndDecisionFold`:

```python
class TestArtifactPath(unittest.TestCase):
    """The query string is a lookup key, never a path. Recorded paths are
    untrusted: the Scout writes them into the contract."""

    def _dir(self, events):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "artifacts", "T60"), exist_ok=True)
        with open(os.path.join(d, "artifacts", "T60", "shot.png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
        return d, mk(events, loop_dir=d)

    def test_resolves_a_recorded_relative_path(self):
        d, store = self._dir([
            {"t": 1, "seq": 1, "type": "tick_start", "tick": 4},
            {"t": 2, "seq": 2, "type": "artifact", "tick": 4, "task": "T60",
             "name": "shot", "path": "artifacts/T60/shot.png"},
        ])
        got = serve.artifact_path(d, store, 4, "shot")
        self.assertEqual(got, os.path.realpath(
            os.path.join(d, "artifacts", "T60", "shot.png")))

    def test_unknown_key_is_none(self):
        d, store = self._dir([{"t": 1, "seq": 1, "type": "tick_start", "tick": 4}])
        self.assertIsNone(serve.artifact_path(d, store, 4, "shot"))
        self.assertIsNone(serve.artifact_path(d, store, 99, "shot"))

    def test_traversal_out_of_the_artifacts_dir_is_refused(self):
        d, store = self._dir([
            {"t": 1, "seq": 1, "type": "tick_start", "tick": 4},
            {"t": 2, "seq": 2, "type": "artifact", "tick": 4, "task": "T60",
             "name": "escape", "path": "artifacts/T60/../../LOOP_CONFIG.md"},
        ])
        with open(os.path.join(d, "LOOP_CONFIG.md"), "w") as f:
            f.write("Worktree: /x\n")
        self.assertIsNone(serve.artifact_path(d, store, 4, "escape"))

    def test_absolute_path_outside_the_loop_dir_is_refused(self):
        d, store = self._dir([
            {"t": 1, "seq": 1, "type": "tick_start", "tick": 4},
            {"t": 2, "seq": 2, "type": "artifact", "tick": 4, "task": "T60",
             "name": "abs", "path": "/etc/hosts"},
        ])
        self.assertIsNone(serve.artifact_path(d, store, 4, "abs"))

    def test_symlink_out_of_the_artifacts_dir_is_refused(self):
        d, store = self._dir([
            {"t": 1, "seq": 1, "type": "tick_start", "tick": 4},
            {"t": 2, "seq": 2, "type": "artifact", "tick": 4, "task": "T60",
             "name": "link", "path": "artifacts/T60/link.png"},
        ])
        outside = os.path.join(tempfile.mkdtemp(), "secret.png")
        with open(outside, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
        os.symlink(outside, os.path.join(d, "artifacts", "T60", "link.png"))
        self.assertIsNone(serve.artifact_path(d, store, 4, "link"))

    def test_non_image_extension_is_refused(self):
        d, store = self._dir([
            {"t": 1, "seq": 1, "type": "tick_start", "tick": 4},
            {"t": 2, "seq": 2, "type": "artifact", "tick": 4, "task": "T60",
             "name": "sh", "path": "artifacts/T60/evil.sh"},
        ])
        with open(os.path.join(d, "artifacts", "T60", "evil.sh"), "w") as f:
            f.write("rm -rf /\n")
        self.assertIsNone(serve.artifact_path(d, store, 4, "sh"))

    def test_recorded_but_deleted_file_is_none(self):
        d, store = self._dir([
            {"t": 1, "seq": 1, "type": "tick_start", "tick": 4},
            {"t": 2, "seq": 2, "type": "artifact", "tick": 4, "task": "T60",
             "name": "gone", "path": "artifacts/T60/gone.png"},
        ])
        self.assertIsNone(serve.artifact_path(d, store, 4, "gone"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 plugins/agent-loop/tests/serve.test.py -v 2>&1 | grep -E 'TestArtifactPath|FAILED|ERROR' | head`
Expected: FAIL with `AttributeError: module 'serve' has no attribute 'artifact_path'`.

- [ ] **Step 3: Implement it**

Add to the imports at the top of `web/serve.py`, after `from http.server import ...`:

```python
from urllib.parse import parse_qs, urlparse
```

Add directly above `class Supervisor:`:

```python
# What the dashboard is allowed to render from $LOOP_DIR/artifacts/.
_ARTIFACT_TYPES = {".png": "image/png", ".jpg": "image/jpeg",
                   ".jpeg": "image/jpeg", ".webp": "image/webp"}
_MAX_ARTIFACT_BYTES = 8 << 20


def artifact_path(loop_dir, store, tick, name):
    """Absolute path of a recorded screenshot, or None. Pure except for stat().

    The query string never becomes a path: (tick, name) is a key into the index
    the `artifact` events built. The path that comes back is still untrusted —
    the Scout writes screenshot paths into the contract (spec 5.3) — so it is
    realpath-resolved (collapsing `..` and following symlinks) and must land
    inside $LOOP_DIR/artifacts/, with an extension the dashboard can render.
    """
    rel = store.artifact_ix.get((tick, name))
    if not rel:
        return None
    root = os.path.realpath(os.path.join(loop_dir, "artifacts"))
    full = os.path.realpath(rel if os.path.isabs(rel)
                            else os.path.join(loop_dir, rel))
    if full != root and not full.startswith(root + os.sep):
        return None
    if os.path.splitext(full)[1].lower() not in _ARTIFACT_TYPES:
        return None
    return full if os.path.isfile(full) else None
```

In `_make_handler`'s `Handler.do_GET`, add a branch directly above the final `else:` (which returns 404):

```python
            elif self.path.startswith("/api/artifact"):
                q = parse_qs(urlparse(self.path).query)
                try:
                    tick = int((q.get("tick") or [""])[0])
                except ValueError:
                    tick = None
                name = (q.get("name") or [""])[0]
                full = artifact_path(state.sup.loop_dir, state.store, tick, name)
                if full is None:
                    self._send_json({"error": "not found"}, 404)
                    return
                try:
                    size = os.path.getsize(full)
                    if size > _MAX_ARTIFACT_BYTES:
                        self._send_json({"error": "artifact too large"}, 413)
                        return
                    with open(full, "rb") as f:
                        blob = f.read()
                except OSError:
                    self._send_json({"error": "not found"}, 404)
                    return
                ctype = _ARTIFACT_TYPES[os.path.splitext(full)[1].lower()]
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(blob)))
                # A retried tick can rewrite the same (tick, name).
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(blob)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 plugins/agent-loop/tests/serve.test.py 2>&1 | tail -5`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/web/serve.py plugins/agent-loop/tests/serve.test.py
git commit -m "$(cat <<'EOF'
agent-loop: GET /api/artifact serves recorded screenshots

(tick, name) is a key into the event-built index, never a path. The recorded
path is untrusted — the Scout writes it into the contract — so it is realpath
resolved and must land inside $LOOP_DIR/artifacts/ with a renderable
extension. Traversal, absolute paths, symlinks out and non-images all 404.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
)"
```

---

### Task 5: dashboard — "Stop now", artifact thumbnails, decisions list

**Files:**
- Modify: `plugins/agent-loop/web/dashboard.html`
- Modify: `plugins/agent-loop/tests/fixtures/snapshot-sample.json`
- Test: `plugins/agent-loop/tests/web.contract.sh`

**Interfaces:**
- Consumes: snapshot keys `current.artifacts` (`[{name,path}]`), `current.tick`, `decisions` (`[{t,type,task,decision,classification,attempt,into}]`), `log_file` (Tasks 2–4); route `GET /api/artifact?tick=&name=`; `POST /api/stop` returning 409 `{"error": "no live harness to stop"}` when refused (Task 1).
- Produces: element ids `btnStop` (relabelled, moved), `artifacts`, `decisions`.

The existing `btnStop` keeps its id — `web.contract.sh` pins the control ids and users recognise the button — but is relabelled **Stop now**, moved beside Pause, and given a title that states the difference. `renderMast` already disables it when the harness is not alive, which is exactly the state Task 1 refuses, so the 409 is a backstop rather than the normal path.

- [ ] **Step 1: Write the failing assertions**

In `plugins/agent-loop/tests/web.contract.sh`, extend the element-id list in check 4 — replace:

```bash
for id in status why health timeline roadmap incidents usage log \
          btnStart btnPause btnResume btnStop tickNo; do
```

with:

```bash
for id in status why health timeline roadmap incidents usage log artifacts \
          decisions btnStart btnPause btnResume btnStop tickNo; do
```

Immediately after that `done`, add:

```bash
grep -q 'Stop now' "$tmp/page.html"; assert_true $? "the stop control is labelled 'Stop now'"
```

Add two snapshot-contract checks inside the `python3 - "$tmp/state.json" <<'PY'` heredoc of check 5, in the `checks` list (directly after the `("incidents is a list", ...)` entry):

```python
    ("decisions is a list", isinstance(d.get("decisions"), list)),
    ("current.artifacts is a list", isinstance(d.get("current", {}).get("artifacts"), list)),
    ("log_file names the tailed log", d.get("log_file") in ("harness.log", "run.log")),
```

Add a whole new numbered check directly after check 7 (the `/api/start` 409 block, after its `rm -f "$tmp/runtime/harness.json"`):

```bash
# 7b) /api/stop writes runtime/STOP only while a harness is alive.
# With no harness, a sentinel nobody reads would only kill the next launch.
code="$(curl -s -o "$tmp/stop-dead.json" -w '%{http_code}' -X POST "$url/api/stop")"
okdead=0; [[ "$code" == "409" ]] || okdead=1
assert_true "$okdead" "POST /api/stop with no live harness => 409 (got $code)"
[[ ! -f "$tmp/runtime/STOP" ]]; assert_true $? "a refused stop writes no STOP sentinel"
grep -q "no live harness" "$tmp/stop-dead.json"; assert_true $? "409 body says there is no harness to stop"

bash -c 'exec -a run.sh sleep 30' &
fake=$!
sleep 0.3
printf '{"pid":%s,"start_epoch":1,"host":"t","loop_dir":"%s","plugin_version":"3.0.0"}\n' \
  "$fake" "$tmp" > "$tmp/runtime/harness.json"
code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$url/api/stop")"
oklive=0; [[ "$code" == "200" ]] || oklive=1
assert_true "$oklive" "POST /api/stop over a live harness => 200 (got $code)"
[[ -f "$tmp/runtime/STOP" ]]; assert_true $? "stop wrote runtime/STOP"
curl -s "$url/api/state" -o "$tmp/stopping.json"
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d["status"]["state"]=="pausing" else 1)' \
  "$tmp/stopping.json"
assert_true $? "a STOP sentinel over a live harness reads as pausing"
kill "$fake" 2>/dev/null; wait "$fake" 2>/dev/null
fake=""
rm -f "$tmp/runtime/harness.json" "$tmp/runtime/STOP"

# an unreachable /api/artifact key is a 404, never a 500
code="$(curl -s -o /dev/null -w '%{http_code}' "$url/api/artifact?tick=1&name=nope")"
ok404=0; [[ "$code" == "404" ]] || ok404=1
assert_true "$ok404" "GET /api/artifact for an unknown key => 404 (got $code)"
```

Note: the `/api/state` snapshot is rebuilt at 1 Hz by `SnapshotThread`, but every `POST` handler calls `state.rebuild()` before responding, so the `pausing` read directly after the stop is not a race.

- [ ] **Step 2: Run the contract test to verify it fails**

Run: `bash plugins/agent-loop/tests/web.contract.sh 2>&1 | grep -E 'FAIL|PASS:' | head -20`
Expected: FAIL on `element id="artifacts" present`, `element id="decisions" present`, `the stop control is labelled 'Stop now'`, and `decisions is a list`. The `/api/stop` 409/200 assertions pass already if Task 1 landed.

- [ ] **Step 3: Implement the dashboard changes**

In `web/dashboard.html`, replace the control block in `<div class="ctrls">`:

```html
      <button id="btnStart" type="button">Start</button>
      <button id="btnPause" type="button" title="Finish the phase in flight, then stop">Pause</button>
      <button id="btnStop" type="button" title="Stop at the next phase boundary — minutes, not the rest of the tick">Stop now</button>
      <button id="btnResume" type="button">Resume</button>
```

(the `btnBell` button stays as the last child, unchanged).

In the *This tick* section, add one div directly after `<div id="activity"></div>`:

```html
    <div id="artifacts" hidden></div>
```

Add a new band directly after the Incidents `</section>` and before the Effort band:

```html
<section class="band" aria-label="Decisions">
  <div class="wrap">
    <div class="hd"><h2>Decisions</h2><span class="grow"></span><span class="note" id="decNote"></span></div>
    <div id="decisions"></div>
  </div>
</section>
```

Change the log `<summary>` so it can name its source:

```html
      <summary id="logName">harness.log, last lines</summary>
```

Add styles — put them with the other rules, directly after the `#btnStop:not(:disabled)` rule:

```css
#artifacts { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 10px; }
#artifacts figure { margin: 0; max-width: 220px; }
#artifacts img { display: block; width: 100%; height: auto; border: 1px solid var(--edge); border-radius: 4px; }
#artifacts figcaption { font-size: 11px; opacity: .75; margin-top: 4px; word-break: break-all; }
.dec { display: flex; gap: 8px; align-items: baseline; padding: 3px 0; font-size: 12px; }
.dec time { margin-left: auto; opacity: .6; }
.dec .cls { opacity: .7; }
```

If `--edge` is not a variable this file defines, use `#3a3a3a` instead — check with `grep -n '\-\-edge' plugins/agent-loop/web/dashboard.html` before writing the rule.

Add two renderers directly above `function renderFooter(d) {`:

```js
function renderArtifacts(d) {
  const cur = d.current || {};
  const items = Array.isArray(cur.artifacts) ? cur.artifacts : [];
  const el = $("artifacts");
  if (!items.length) { el.innerHTML = ""; el.hidden = true; return; }
  el.hidden = false;
  const q = a => "/api/artifact?tick=" + encodeURIComponent(cur.tick) +
                 "&name=" + encodeURIComponent(a.name);
  el.innerHTML = items.map(a =>
    '<figure><a href="' + esc(q(a)) + '" target="_blank" rel="noreferrer">' +
    '<img loading="lazy" alt="' + esc(a.name) + '" src="' + esc(q(a)) + '"></a>' +
    '<figcaption>' + esc(a.name) + '</figcaption></figure>').join("");
}

function renderDecisions(d) {
  const all = Array.isArray(d.decisions) ? d.decisions : [];
  const rows = all.slice(-8).reverse();
  setText($("decNote"), all.length ? all.length + " logged" : "");
  const el = $("decisions");
  if (!rows.length) {
    el.innerHTML = '<p class="note">Nothing decided autonomously yet. ' +
                   'Every entry here is also in LOOP_DECISIONS.md, for a human to ratify or reverse.</p>';
    return;
  }
  el.innerHTML = rows.map(r => {
    const into = Array.isArray(r.into) ? r.into.join(", ") : (r.into || "");
    return '<div class="dec"><b>' + esc(r.task || "—") + '</b>' +
           '<span>' + esc(r.decision || r.type || "") + '</span>' +
           (r.classification ? '<span class="cls">' + esc(r.classification) + '</span>' : "") +
           (r.attempt ? '<span class="cls">attempt ' + esc(r.attempt) + '</span>' : "") +
           (into ? '<span class="cls">→ ' + esc(into) + '</span>' : "") +
           '<time>' + esc(clock(r.t)) + '</time></div>';
  }).join("");
}
```

Replace `renderLog` so the heading and the empty message name the real file:

```js
function renderLog(d) {
  const lines = Array.isArray(d.log) ? d.log : [];
  const name = d.log_file || "harness.log";
  setText($("logName"), name + ", last lines");
  setText($("log"), lines.length ? lines.join("\n") : name + " is empty");
}
```

Wire both into `paint(d)`, directly after the `renderActivity` line:

```js
    if (changed("artifacts", [d.current && d.current.artifacts, d.current && d.current.tick])) renderArtifacts(d);
```

and directly after the `renderIncidents` line:

```js
    if (changed("decisions", d.decisions)) renderDecisions(d);
```

and change the log line to re-render when the source file changes:

```js
    if (changed("log", [d.log, d.log_file])) renderLog(d);
```

Finally, keep `#mock` honest — the `MOCK` constant is documented as byte-identical to `tests/fixtures/snapshot-sample.json`. Add the same two entries to **both** files. In `MOCK`, directly after its `"incidents": [...]` entry, and in the fixture at the matching position:

```json
  "log_file": "harness.log",
  "decisions": [
    {"t": 1757640100, "type": "decision", "task": "T60", "decision": "widen",
     "classification": "self-imposed", "attempt": null, "into": null},
    {"t": 1757640460, "type": "resume", "task": "T60", "decision": "resume",
     "classification": null, "attempt": 2, "into": null}
  ],
```

and add `"artifacts": []` to the `"current"` object in both (the mock has no server behind it, so real thumbnails would 404).

- [ ] **Step 4: Run the contract test to verify it passes**

Run: `bash plugins/agent-loop/tests/web.contract.sh 2>&1 | tail -20`
Expected: `web.contract.sh: PASS`, and no `FAIL:` lines above it.

Also confirm the two JSON blobs still parse:
Run: `python3 -c 'import json; json.load(open("plugins/agent-loop/tests/fixtures/snapshot-sample.json")); print("fixture ok")'`
Expected: `fixture ok`.

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/web/dashboard.html plugins/agent-loop/tests/web.contract.sh \
        plugins/agent-loop/tests/fixtures/snapshot-sample.json
git commit -m "$(cat <<'EOF'
agent-loop: dashboard gains Stop now, artifact thumbnails and a decisions list

Stop now sits beside Pause and says what it does differently: the runner ends
at the next phase boundary rather than at the end of the tick. The tick panel
renders the screenshots the render gate recorded, and a Decisions band mirrors
LOOP_DECISIONS.md so autonomous choices are visible while the loop runs.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
)"
```

---

### Task 6: `/agent-loop` attach skill — Stop now, `LOOP_DECISIONS.md`, `harness.log`

**Files:**
- Modify: `plugins/agent-loop/skills/agent-loop/SKILL.md`
- Test: `plugins/agent-loop/tests/setup.contract.sh` (its second half asserts the attach skill)

**Interfaces:**
- Consumes: the `runtime/STOP` sentinel and `POST /api/stop` semantics from Task 1; `LOOP_DECISIONS.md` written by Plan C; `harness.log` written by Plan A.
- Produces: nothing other tasks consume.

Everything about liveness stays exactly as it is (`harness.json` pid + `kill -0` + `ps -o command= -p`, never `pgrep`) — the runner still `exec`s from `run.sh`, so the `ps` match is unchanged.

- [ ] **Step 1: Write the failing assertions**

In `plugins/agent-loop/tests/setup.contract.sh`, add to the attach-skill block (after the existing `ahas 'migrate'`-adjacent assertions, directly above the `# Task 8:` comment):

```bash
# v3: Stop-now vs Pause, the decisions ledger, and harness.log instead of run.log.
ahas 'runtime/STOP'
ahas 'phase boundary'
ahas 'LOOP_DECISIONS.md'
ahas 'harness.log'
grep -qiE 'PAUSE.*(finish|end of).*(tick|phase)' "$A"; assert_true $? "attach skill contrasts PAUSE with STOP"
grep -qF 'run.log is forensic' "$A"; assert_false $? "attach skill no longer points at run.log"
# Liveness is unchanged by v3 — run.sh still execs the runner, so the ps match holds.
ahas 'ps -o command= -p'
```

- [ ] **Step 2: Run the contract test to verify it fails**

Run: `bash plugins/agent-loop/tests/setup.contract.sh 2>&1 | grep -E 'FAIL|PASS:'`
Expected: FAIL lines for `runtime/STOP`, `phase boundary`, `LOOP_DECISIONS.md`, `harness.log`, the PAUSE/STOP contrast, and `attach skill no longer points at run.log`.

- [ ] **Step 3: Edit the skill**

In `skills/agent-loop/SKILL.md`:

(a) In the **Step 2** bash block, add two lines after the `NEEDS_HUMAN.md` line:

```bash
[ -f "$LOOP_DIR/LOOP_DECISIONS.md" ] && tail -20 "$LOOP_DIR/LOOP_DECISIONS.md"                # autonomous Judge decisions
tail -12 "$LOOP_DIR/harness.log" 2>/dev/null                                                  # the harness's own lines
```

(b) In the paragraph under that block, replace the sentence `run.log is forensic only; never derive state from its text.` with:

> `harness.log` is forensic only; never derive state from its text — a phase's full transcript lives in the session JSONL its `phase_end` event names, not in the loop dir. (A loop dir last written by 2.x has a `run.log` instead; same rule.)

(c) In the same paragraph, after the `NEEDS_HUMAN.md` sentence, add:

> Also report the tail of `$LOOP_DIR/LOOP_DECISIONS.md`: under `Decision policy: autonomous` the Judge widens Scout-invented constraints, escalates tiers, splits oversized tasks and picks a default where the spec is silent, and every one of those lands here with its alternatives. These are decisions a human **ratifies or reverses after the fact**, not blockers — say how many are new since the last attach, and offer to walk them. `LOOP_CLEANUP.md` remains the queue of things the Judge *refused* to decide.

(d) Add a new sub-section directly above `## Step 4: Arm the incident monitor`:

```markdown
### Stopping it: Pause vs Stop now

Two sentinels, both read by the harness between phases; neither kills anything mid-write.

- **Pause** (`⏸` in the dashboard, `POST /api/pause`, or `touch <loop-dir>/runtime/PAUSE`) — the harness finishes the **phase** in flight, writes `runtime/CHECKPOINT.json`, emits `paused`, exits 0.
- **Stop now** (`Stop now` in the dashboard, or `POST /api/stop`) — writes `runtime/STOP`. The harness SIGTERMs the phase in flight (a Worker gets its wrap-up continuation first, so its checkpoint is current), then behaves exactly as Pause. This is the one to reach for when a phase is burning budget on the wrong thing: it lands in minutes, not at the end of the tick.

`/api/stop` refuses with HTTP 409 (`no live harness to stop`) when nothing is running — a sentinel nobody reads would only stop the *next* launch. The dashboard shows `PAUSING` for both sentinels. Resume (`⟳`, `POST /api/resume`, or deleting the file) removes **both** sentinels before relaunching; if you stop the loop by hand, delete `runtime/STOP` as well as `runtime/PAUSE` or the next launch exits immediately.
```

(e) In **Step 5**, in the option-1 bash comment block, after the sentence beginning `Both endpoints delete `runtime/PAUSE``, replace that clause with `Both endpoints delete `runtime/PAUSE` and `runtime/STOP``.

- [ ] **Step 4: Run the contract test to verify it passes**

Run: `bash plugins/agent-loop/tests/setup.contract.sh 2>&1 | tail -5`
Expected: `PASS: <n>  FAIL: 0`.

Also re-check the `pgrep` invariant this file already pins:
Run: `grep -c 'pgrep' plugins/agent-loop/skills/agent-loop/SKILL.md` and `grep -c 'Never \`pgrep\`' plugins/agent-loop/skills/agent-loop/SKILL.md`
Expected: the two counts are equal (the contract test asserts this; do not introduce a second mention).

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/skills/agent-loop/SKILL.md plugins/agent-loop/tests/setup.contract.sh
git commit -m "$(cat <<'EOF'
agent-loop: attach skill covers Stop now, LOOP_DECISIONS.md and harness.log

Pause ends the phase; Stop now SIGTERMs it after a Worker wrap-up, so a stop
lands in minutes. The health block surfaces the Judge's autonomous decisions —
things to ratify or reverse, not blockers — and reads harness.log. Liveness is
unchanged: run.sh still execs the runner, so the ps match holds.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
)"
```

---

### Task 7: setup skill + templates — `Tiers:`, `Decision policy:`, new `Limits:`, new plan-row tags

**Files:**
- Modify: `plugins/agent-loop/skills/agent-loop-setup/SKILL.md`
- Modify: `plugins/agent-loop/templates/LOOP_CONFIG.md`
- Modify: `plugins/agent-loop/templates/LOOP_PLAN.md`
- Test: `plugins/agent-loop/tests/setup.contract.sh`

**Interfaces:**
- Consumes: `LoopConfig.tiers`, `LoopConfig.limits`, `LoopConfig.decision_policy` from `runner/config.py` (Plan A); the `Render:` key and its recipe wizard question are **Plan B's** — do not write them here.
- Produces: the `Tiers:`, `Decision policy:` and `Limits:` lines `runner/config.py` parses; the `| no-ui`, `| copy_of:`, `| blocked_by:`, `| split_of:` row tags `runner/plan.py` parses.

`Tiers:` is the only place in the whole plugin where a model alias appears (spec §11 item 11), which is what makes `most-capable=fable` a one-word change. The `Orchestrator model:` line stays in the template as a dead key so a 2.x config still parses, but nothing explains it any more.

- [ ] **Step 1: Write the failing assertions**

In `plugins/agent-loop/tests/setup.contract.sh`, add directly above the final `assert_summary`:

```bash
# --- v3: tiers, decision policy, per-phase limits, new row tags ---
has 'Tiers:'
has 'cheap='
has 'most-capable='
has 'Decision policy'
has 'autonomous'
has 'conservative'
has 'LOOP_DECISIONS.md'
grep -qE '^1?[0-9]+\. \*\*Decision policy\*\*' "$F"; assert_true $? "setup wizard has a numbered Decision policy question"
grep -qE '^1?[0-9]+\. \*\*Tiers\*\*' "$F"; assert_true $? "setup wizard has a numbered Tiers question"
# The orchestrator-model explanation is gone: v3 has no LLM spine to explain.
grep -qiE 'orchestrator (model|is a coordination)' "$F"; assert_false $? "setup drops the orchestrator-model explanation"
grep -qF 'tick-prompt' "$F"; assert_false $? "setup no longer points at tick-prompt.md"
# Per-phase budgets replace the single tick wall.
has 'worker_resume_max'
has 'per-phase'

# Template: tiers map, decision policy, the spec 4.2 limit defaults, no orchestrator prose.
grep -qE '^Tiers: cheap=haiku standard=sonnet most-capable=opus$' "$TMPL"; assert_true $? "template ships the default tier map"
grep -qE '^Decision policy: autonomous$' "$TMPL"; assert_true $? "template defaults to Decision policy: autonomous"
for k in tick_timeout=1800 scout_timeout=480 worker_timeout=1500 wrapup_timeout=300 eval_timeout=480 judge_timeout=360 planner_timeout=900 gate_cmd_timeout=600 worker_resume_max=1 worker_budget_usd=6 max_attempts=3; do
  grep -qF "$k" "$TMPL"; assert_true $? "template Limits carries $k"
done
grep -qiE '^# The orchestrator' "$TMPL"; assert_false $? "template drops the orchestrator-model paragraph"
grep -qE '^Orchestrator model:' "$TMPL"; assert_true $? "Orchestrator model: kept as an inert key so 2.x configs parse"
# Exactly one place in the plugin names a model, and it is the Tiers line.
n_alias="$(grep -rlE '\b(haiku|sonnet|opus)\b' "$HERE/.." --include='*.md' --include='*.py' --include='*.sh' --include='*.json' | grep -v '/tests/' | wc -l | tr -d ' ')"
[ "$n_alias" -eq 1 ]; assert_true $? "only templates/LOOP_CONFIG.md names a model alias (got $n_alias files)"

# Plan template: the four new row tags.
PLANT="$HERE/../templates/LOOP_PLAN.md"
for tag in 'no-ui' 'copy_of' 'blocked_by' 'split_of'; do
  grep -qF "$tag" "$PLANT"; assert_true $? "plan template documents the $tag row tag"
done
```

- [ ] **Step 2: Run the contract test to verify it fails**

Run: `bash plugins/agent-loop/tests/setup.contract.sh 2>&1 | grep -E 'FAIL' | head -20`
Expected: FAIL on every new assertion (`Tiers:`, `Decision policy`, the numbered questions, the template keys, the row tags).

- [ ] **Step 3a: Rewrite `templates/LOOP_CONFIG.md`**

Replace everything from the `Limits:` line to the end of the file with:

```markdown
Limits: tick_timeout=1800 scout_timeout=480 worker_timeout=1500 wrapup_timeout=300 eval_timeout=480 judge_timeout=360 planner_timeout=900 gate_cmd_timeout=600 worker_resume_max=1 worker_budget_usd=6 max_attempts=3
Blocker policy: continue-independent | halt
Branch: agent-loop-<topic>
Worktree: <absolute path — the harness refuses to run elsewhere>
Segment count: <N or 1>
Spec: docs/superpowers/specs/<file>.md
Plan: docs/superpowers/plans/<file>.md
# auto = run.sh spawns the dashboard as a supervised sidecar and prints its URL; off = don't.
Dashboard: auto
# auto = a budgeted headless medic tick triages incidents; notify = desktop notification only;
# off = neither (an error-severity incident then stops the loop for a human).
Medic: auto
# Model alias for the medic tick. Blank inherits your Claude default.
Medic model:

## Limits — one line, space-separated key=value, seconds unless noted
# Every phase has its own wall, so one slow phase no longer costs the whole tick.
# Defaults, all overridable above:
#   tick_timeout=1800        outer sanity cap for one tick; the figure the dashboard displays
#   scout_timeout=480        contract writing
#   worker_timeout=1500      the edit phase
#   wrapup_timeout=300       the out-of-time continuation that updates worker-result.json
#   eval_timeout=480         verdict
#   judge_timeout=360        failure-path decision
#   planner_timeout=900      PLAN tick / task split
#   gate_cmd_timeout=600     per verification/render/fidelity command
#   worker_resume_max=1      resume attempts before the Judge chooses escalate vs split
#   worker_budget_usd=6      per-Worker-phase spend cap
#   max_attempts=3           hard cap on attempts for one task, resume or escalation included
# These are provisional, derived from one run's phase durations (spec 4.2) — after a
# segment, run `python3 -m runner.calibrate <run-dir>` and paste its suggested Limits:
# line here.

## Model tiers — THE ONLY PLACE THIS PLUGIN NAMES A MODEL
# Phases ask for a tier; this line resolves the tier to an alias. Swapping a
# model is a one-word edit here and nothing else changes.
Tiers: cheap=haiku standard=sonnet most-capable=opus
# Per-role tier preference. Blank = the phase's default from the runner
# (scout/worker/evaluator standard, judge/planner/reviewer most-capable,
# learner cheap). A Worker's second attempt is escalated one tier by the
# harness regardless of what is written here.
Planner tier: most-capable
Scout tier: standard
Worker tier: standard
# Evaluator tier is CLASS-GOVERNED: `| mechanical` skips it, `| complex` runs it
# at most-capable, anything else at standard. A value here is a ceiling for
# `| complex` reviews only — it does NOT force a tier onto default tasks.
Evaluator tier:

## Decision policy — what the Judge may settle without waking you
# autonomous (default): the Judge may widen constraints the Scout invented,
#   escalate a tier, split an oversized task, and choose a default for a
#   question the spec is silent on. Every such choice is appended to
#   LOOP_DECISIONS.md with its alternatives, so a human can reverse it later.
#   It still defers when the spec forbids the change, when the change touches a
#   forbidden path sourced from the plan or spec, and when two Judge decisions
#   on the same task have already failed.
# conservative: widen/escalate/split allowed; spec-silent defaults are deferred
#   to LOOP_CLEANUP.md for a human, as 2.x did.
Decision policy: autonomous

# Ignored by v3 — kept so a config written by 2.x still parses.
Orchestrator model:
```

(The `Render:` key is Plan B's; if Plan B has already added it, leave it exactly where it is.)

- [ ] **Step 3b: Extend `templates/LOOP_PLAN.md`**

Append to the legend block, directly after the clone-family paragraph:

```markdown
Verification tags (read by the harness when it validates the Scout's contract):
- `| no-ui` — this task cannot change what the product looks like, so no render
  gate is required even though its files match the repo's UI globs. Opt-out
  only: without it, a task whose `allow_list` touches a UI path MUST carry a
  `render_gate` and the tick fails if the screenshots are not produced.
- `| copy_of: T<n>` — this task copies/ports/replicates T<n>'s artefact. Forces
  a `fidelity_source` pair in the contract: the harness computes a normalized
  line-similarity ratio between source and target and hard-fails below
  `min_similarity`. A "copy" that produces a six-line comment does not pass.
- `| blocked_by: T<a>,T<b>` — a semantic block the Judge discovered, distinct
  from `depends_on:` (which is planned order). SELECT treats the named tasks as
  blocked-upstream until this one closes.
- `| split_of: T<n>` — this row was inserted by `Plan.split` when a Worker
  overran its resumes; T<n> is the parent task it was split from. Sub-task ids
  are the next free numeric ids (`T74`, `T75`, …), never a lettered suffix like
  `T60a` — `serve.py`'s id regex `\bT\d+\b` does not match one.
```

- [ ] **Step 3c: Edit `skills/agent-loop-setup/SKILL.md`**

(a) In the intro, replace `(each tick reads `tick-prompt.md`)` with `(each phase gets a small role brief from `runner/prompts/` and returns JSON the harness validates)`.

(b) In **Step 2**, replace wizard item 5 (`**Limits**`) with:

```markdown
5. **Limits** — the harness reads one space-separated `key=value` line. v3 budgets each **phase** separately, so one slow phase no longer costs the whole tick; `tick_timeout` survives only as an outer sanity cap and as the figure the dashboard displays. Accept the template defaults unless the user asks otherwise:

   `Limits: tick_timeout=1800 scout_timeout=480 worker_timeout=1500 wrapup_timeout=300 eval_timeout=480 judge_timeout=360 planner_timeout=900 gate_cmd_timeout=600 worker_resume_max=1 worker_budget_usd=6 max_attempts=3`

   `worker_resume_max` is the number of `--resume` continuations a timed-out Worker gets before the Judge chooses between escalating a tier and splitting the task; `max_attempts` is the hard cap on attempts for one task, resumes and escalations included. These defaults are provisional — after a segment has run, `python3 -m runner.calibrate <run-dir>` prints a suggested `Limits:` line from that segment's own phase durations, to paste in for the next one. There is still **no cost, iteration, or wall-clock budget for the run** — the loop runs until the plan is done or it hits your subscription's usage window, at which point the harness reads the reset time and auto-waits, then resumes.
```

(c) Renumber nothing else; add two new wizard items after item 11 (`**Medic**`):

```markdown
12. **Tiers** — free text, one line, default `cheap=haiku standard=sonnet most-capable=opus`. This is the **only** place the plugin names a model. Every phase asks for a tier (`cheap | standard | most-capable`) and the harness resolves it here at dispatch, as a `--model` flag rather than a request inside a prompt. Swapping a frontier model in is a one-word edit to this line. Write `Tiers: cheap=<alias> standard=<alias> most-capable=<alias>`.

    Where the leverage sits, and why: the Scout defaults to **standard**, not cheap — the contract is the highest-leverage document in the tick, and in the analysed run the three defective contracts were all written by the cheap tier. The Judge, Planner and Reviewer run most-capable because they are the only phases making judgement calls. The Learner runs cheap. A Worker's second attempt is escalated one tier **by the harness**, not by asking a model to escalate itself.

13. **Decision policy** — single-select, default `autonomous`:
    - `autonomous` — before marking a task `[!]`, halting, or raising needs-human, the harness dispatches a **Judge** phase. Under this policy the Judge may widen a constraint the Scout invented, escalate a tier, split an oversized task, and pick a default for a question the spec is silent on. Every such choice is appended to `$LOOP_DIR/LOOP_DECISIONS.md` with the alternatives it rejected, so you can ratify or reverse it in the morning. It still defers when the spec forbids the change, when the change touches a forbidden path sourced from the plan or spec, and when two Judge decisions on the same task have already failed.
    - `conservative` — the Judge may still widen, escalate and split, but a question the spec is silent on is deferred to `LOOP_CLEANUP.md` for a human, exactly as 2.x did.

    Tell the user the tradeoff in one sentence: in the analysed run all three `LOOP_CLEANUP.md` items were decidable from disk, and one of them stopped the loop for a human overnight over a `forbidden` entry the Scout had invented itself. `autonomous` trades a longer morning read for fewer stopped nights. Write `Decision policy: autonomous` (or `conservative`).
```

(d) Replace the whole `**Model tiers.**` paragraph (the one beginning "The template ships `Orchestrator model: sonnet`") with:

```markdown
**No orchestrator.** v3 has no LLM spine: the harness is a Python state machine that reads the plan, picks tasks, runs commands, enforces the allow-list and commits, and each phase is a separate `claude -p` process with its own model, timeout, turn cap and usage record. There is nothing left to explain about an "orchestrator model" — `Orchestrator model:` stays in the template as an inert key so a config written by 2.x still parses, and is ignored. Tier policy lives entirely in the `Tiers:` line and the per-role tier keys (item 12).
```

(e) In **Step 3**, in the task checkbox legend, append three bullets:

```markdown
   - `| no-ui` — opt out of the render gate for a task that genuinely cannot change what the product looks like. Without it, a task touching a UI path must carry a render gate and fails if the screenshots are not produced.
   - `| copy_of: T<n>` — this task copies/ports/replicates T<n>. Forces a similarity check against the source; a "copy" that is really a `TODO(copied from …)` comment hard-fails.
   - `| blocked_by: T<a>,T<b>` — a semantic block the Judge found at runtime, distinct from planned `depends_on:` order.
```

(f) In **Step 6**, in the "Stopping and resuming" paragraph, replace the first sentence with:

```markdown
**Stopping and resuming.** `touch $LOOP_DIR/runtime/PAUSE` stops the loop after the **phase** in flight (it writes `runtime/CHECKPOINT.json`); `touch $LOOP_DIR/runtime/STOP` — or **Stop now** in the dashboard — SIGTERMs that phase first (a Worker gets a wrap-up continuation so its checkpoint is current) and then behaves identically, so a stop lands in minutes rather than at the end of a 30-minute tick. Press ⟳ Resume in the dashboard (it deletes **both** sentinels and relaunches), or delete them and re-run the launch command — tick numbers continue, never restart at 1.
```

(g) In the same step's summary block, add two lines to the key/value list:

```
Tiers:        cheap=<alias> standard=<alias> most-capable=<alias>  (the only place a model is named)
Decision:     autonomous | conservative  (autonomous: the Judge settles what it can and logs it to LOOP_DECISIONS.md)
```

and add `LOOP_DECISIONS.md` to the `Run dir:` line's parenthetical.

(h) In **Step 5**, add `$LOOP_DIR/LOOP_DECISIONS.md` to the scaffold list as item 6:

```markdown
6. **`$LOOP_DIR/LOOP_DECISIONS.md`** — header `# Loop Decisions — settled without a human, for review` and nothing else. The Judge appends to it; under `Decision policy: conservative` it may stay empty, which is fine. Tracked and committed (it is the audit trail for everything the loop decided on its own).
```

and add `"$LOOP_DIR/LOOP_DECISIONS.md"` to the `git add` line in the same step.

- [ ] **Step 4: Run the contract test to verify it passes**

Run: `bash plugins/agent-loop/tests/setup.contract.sh 2>&1 | tail -5`
Expected: `PASS: <n>  FAIL: 0`.

If `only templates/LOOP_CONFIG.md names a model alias` fails, run the grep by hand to find the stray file:
Run: `grep -rlE '\b(haiku|sonnet|opus)\b' plugins/agent-loop --include='*.md' --include='*.py' --include='*.sh' --include='*.json' | grep -v '/tests/'`
Expected: exactly `plugins/agent-loop/templates/LOOP_CONFIG.md`.

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/skills/agent-loop-setup/SKILL.md \
        plugins/agent-loop/templates/LOOP_CONFIG.md \
        plugins/agent-loop/templates/LOOP_PLAN.md \
        plugins/agent-loop/tests/setup.contract.sh
git commit -m "$(cat <<'EOF'
agent-loop: setup asks for Tiers and a Decision policy, not an orchestrator model

v3 has no LLM spine, so there is nothing left to explain about an orchestrator
model; Tiers: is now the single place the plugin names a model, and the wizard
asks whether the Judge may settle spec-silent questions on its own. Limits
carries the per-phase budgets, and the plan legend documents no-ui, copy_of and
blocked_by.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
)"
```

---

### Task 8: postmortem skill reads `LOOP_DECISIONS.md`, `artifacts/`, and complete usage

**Files:**
- Modify: `plugins/agent-loop/skills/agent-loop-postmortem/SKILL.md`
- Test: `plugins/agent-loop/tests/postmortem.contract.sh`

**Interfaces:**
- Consumes: `LOOP_DECISIONS.md` (Plan C), `$LOOP_DIR/artifacts/<T>/<name>.png` (Plan B), `tick_end.by_model` written for **every** tick including a killed one (Plan A, spec §4.1), `runtime/task-<T>.json` attempt history (Plan A, spec §14).
- Produces: nothing other tasks consume.

The one substantive correction: the 2.x ledger's top-level `input_tokens`/`output_tokens` were the orchestrator process only, and a killed tick wrote no `by_model` at all — roughly $61 of one run's spend was unattributed. In v3 the harness sums each phase's usage into `tick_end.by_model` and flushes it even when a phase is SIGKILLed, so `by_model` is the whole billed surface and there are no missing ticks. The "orchestrator (parent) process only" caveat must stay in the document as a *historical* note (the contract test pins the phrase, and a v2 ledger read by a v3 postmortem still has the flaw).

- [ ] **Step 1: Write the failing assertions**

In `plugins/agent-loop/tests/postmortem.contract.sh`, add directly above the final `assert_summary`:

```bash
# --- v3 ---
has 'LOOP_DECISIONS.md'
has 'ratify'
has 'reverse'
has 'artifacts/'
has 'screenshot'
has 'task-<T>.json'
grep -qiE 'by_model.*(every|each) tick|no (missing|unattributed) tick' "$F"; assert_true $? "usage comes from tick_end.by_model for every tick"
grep -qiE 'phase' "$F"; assert_true $? "postmortem reports per-phase, not per-subagent, usage"
# The v2 caveat stays, marked as history — a v2 ledger read by a v3 postmortem still has it.
has 'parent) process only'
```

- [ ] **Step 2: Run the contract test to verify it fails**

Run: `bash plugins/agent-loop/tests/postmortem.contract.sh 2>&1 | grep -E 'FAIL' | head`
Expected: FAIL on `LOOP_DECISIONS.md`, `ratify`, `reverse`, `artifacts/`, `screenshot`, `task-<T>.json` and the `by_model` grep.

- [ ] **Step 3: Edit the skill**

(a) In **Step 1**, add two bullets to the "Read all of" list, directly after the `LOOP_CLEANUP.md` bullet:

```markdown
- **`$LOOP_DIR/LOOP_DECISIONS.md`** — every choice the Judge made without a human under `Decision policy: autonomous`: widened constraints, tier escalations, task splits, and defaults picked where the spec was silent, each with the alternatives it rejected. This is **not** a failure list — it is the queue a human **ratifies or reverses** now that the run is over. Absent or empty is valid (a `conservative` run defers instead of deciding).
- **`$LOOP_DIR/artifacts/`** — one directory per task (`artifacts/<T>/<name>.png`), holding the screenshots the render gate produced and the Evaluator was required to observe. List them **per task**: a UI task with no screenshots means either a `| no-ui` tag or a render recipe that never ran, and both are findings.
- **`runtime/task-<T>.json`** for any task worth a closer look — its attempt history (`attempts[].n`, `.tier`, `.outcome`, `.judge`) shows whether a task passed clean or needed a resume/escalation/split, and at which tier each attempt ran. A task with several attempts and no matching `LOOP_DECISIONS.md` entry is a gap to flag.
```

(b) In the `LOOP_USAGE.jsonl` bullet, replace the `**CRITICAL:**` paragraph with:

```markdown
  **Reading `by_model`.** `by_model` is the whole billed surface: from v3 the harness sums every phase's usage into `tick_end.by_model` and flushes it **even when a phase is SIGTERMed or SIGKILLed**, so no tick is missing and no spend is unattributed (a 2.x run left ~$61 unaccounted for exactly this way). Sum across `by_model.*` of `input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens` for tokens; sum the top-level `cost_usd` for cost. **Historical caveat for a ledger written by 2.x:** there, the top-level `input_tokens`/`output_tokens` were the orchestrator (parent) process only — every subagent's usage lived exclusively in `by_model`, and summing the top-level fields undercounted the run by ~350×. v3 has no orchestrator process, so the top-level figures are the sum of the phases; the rule to read `by_model` is the same either way.
```

(c) In **Step 2**, replace the "Evaluator invocation count" bullet with:

```markdown
- **Phase counts and cost** — from `phase_end` events in `$LOOP_DIR/events.jsonl`, grouped by `phase`: how many times each of scout/worker/evaluator/judge/planner/reviewer/learner ran, on which model, and the wall-clock and cost each accounted for. **Evaluator invocation count** is one row of this table; report it separately from the review-tick count, because the per-task Evaluator runs *inside* execute ticks and the `mode:"review"` ledger rows badly understate real judgement cost (in the source run, 30 Evaluator dispatches across only 10 review ticks). Flag two shapes: every Evaluator on the most-capable tier (a tier ceiling set where the per-class split should have governed), and a Judge count that is a large fraction of the tick count (the loop is spending its most expensive phase on recurring failures — name the failure).
- **Resumes and splits** — count `resume` and `split` events. A high resume rate means `worker_timeout=` is set too low; a high split rate means the plan's tasks are too large. These are the two knobs the next run should change.
```

(d) In **Step 3**'s structured context block, add two sections directly after `### Blocked work — human-decision queue`:

```markdown
### Decisions taken without a human (from LOOP_DECISIONS.md)
For each entry — task ID, the decision (`retry|escalate|widen|resume|split|defer|halt`), its classification (`self-imposed|spec-answered|capability|open`), the rationale, and the alternatives rejected. Mark each **ratify** or **reverse** with one line of reasoning. An `open` classification is the one to read hardest: the loop picked a default the spec did not state.

### Product evidence (from artifacts/)
Per task with a render gate: the screenshot names, and whether the Evaluator's `views` carried an observation for each. List UI-touching tasks with **no** artefacts and say which are `| no-ui` (fine) and which are a gap in the render recipe (a finding — the whole point of the render gate is that a human should not be the first to look at the product).
```

(e) In **Step 3**'s `### Usage` sub-block, replace the `Evaluator dispatches:` line with:

```markdown
- Phases: scout <N> · worker <N> (<R> resumed, <S> split) · evaluator <N> · judge <N> · planner <N> · reviewer <N> · learner <N>
- Evaluator dispatches: <N> total, tiers: <model:count …>
```

(f) In **Step 7**'s final user message block, add one line after the `Manual cleanup:` line:

```
Decided without you: <N> entries in LOOP_DECISIONS.md (<M> classified `open` — read those first)
```

- [ ] **Step 4: Run the contract test to verify it passes**

Run: `bash plugins/agent-loop/tests/postmortem.contract.sh 2>&1 | tail -5`
Expected: `PASS: <n>  FAIL: 0`.

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/skills/agent-loop-postmortem/SKILL.md \
        plugins/agent-loop/tests/postmortem.contract.sh
git commit -m "$(cat <<'EOF'
agent-loop: postmortem reads LOOP_DECISIONS.md, artifacts/ and complete usage

The decisions ledger is a ratify-or-reverse queue, not a failure list, and the
artifacts dir is the evidence that something actually rendered. v3 flushes
tick_end.by_model even for a killed phase, so the ~$61 of unattributed spend
from the 2.x run has nowhere left to hide; the 2.x parent-only caveat stays as
history because an old ledger still has it.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
)"
```

---

### Task 9: `tests/prompts.contract.sh` replaces `tests/tick-prompt.contract.sh`

**Files:**
- Create: `plugins/agent-loop/tests/prompts.contract.sh`
- Delete: `plugins/agent-loop/tests/tick-prompt.contract.sh`
- Modify: `plugins/agent-loop/tests/all.sh`

**Interfaces:**
- Consumes: `plugins/agent-loop/runner/prompts/{scout,worker,worker_wrapup,worker_resume,evaluator,judge,planner,reviewer,learner}.md` (Plans A/B/C) and the JSON output shapes in the interface contract's "Phase JSON outputs" section.
- Produces: nothing other tasks consume.

`tick-prompt.contract.sh` pinned a 41 KB prompt that *was* the state machine. These prompts are role briefs: each states its JSON output shape verbatim, forbids interactive tools, and carries the invariants for its role. The two structural rules that apply to every file — the output shape appears verbatim, and no `<<LOOP_` sentinel survives — are what stop the spine creeping back in through prose.

**Precondition:** `ls plugins/agent-loop/runner/prompts/*.md` lists nine files. If it does not, stop — Plan A has not landed.

- [ ] **Step 1: Write the new contract test**

Create `plugins/agent-loop/tests/prompts.contract.sh` (mode 644):

```bash
#!/usr/bin/env bash
# Prompt contracts for the v3 phase runner.
#
# These are role briefs, not a spine: each prompt states its JSON output shape
# verbatim (the harness parses the last fenced json block), forbids the
# interactive tools a headless phase cannot use, and carries the invariants for
# its own role. The two universal rules are what keep the 41 KB orchestrator
# prompt from growing back: every phase returns JSON, and no <<LOOP_*>> sentinel
# exists any more — the harness decides continue/done/halt, not a model.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
# shellcheck source=../../../tests/lib/assert.sh
source "$ROOT/tests/lib/assert.sh"
D="$HERE/../runner/prompts"

[ -d "$D" ]; assert_true $? "runner/prompts/ exists"
if [ ! -d "$D" ]; then assert_summary; exit 1; fi

# has FILE STRING — the file must contain STRING literally.
has() { grep -qF -e "$2" "$D/$1"; assert_true $? "$1 must contain: $2"; }
# hasnt FILE STRING
hasnt() { grep -qF -e "$2" "$D/$1"; assert_false $? "$1 must NOT contain: $2"; }
# hasre FILE REGEX MSG
hasre() { grep -qiE "$2" "$D/$1"; assert_true $? "$1: $3"; }

# Every role the runner dispatches has a brief.
for f in scout worker worker_wrapup worker_resume evaluator judge planner reviewer learner; do
  [ -f "$D/$f.md" ]; assert_true $? "runner/prompts/$f.md exists"
done

# --- universal rules -------------------------------------------------------
# This suite does not enforce a closed set of {{placeholder}} names: plan B adds
# {{render_recipe}} to scout.md and plan C adds six judge-only placeholders to
# judge.md (interfaces doc "Cross-plan notes"), neither of which is in the
# interfaces doc's base placeholder list. Both are expected and allowed; only
# the per-file assertions below (e.g. the render_recipe check under scout)
# require any specific placeholder to be present.
for p in "$D"/*.md; do
  n="$(basename "$p")"
  # A phase returns JSON in one fenced block; the harness parses the last one.
  grep -qF '```json' "$p"; assert_true $? "$n states its output as a fenced json block"
  # Non-interactivity: a headless phase cannot ask anyone anything.
  grep -qF 'AskUserQuestion' "$p"; assert_true $? "$n forbids AskUserQuestion by name"
  grep -qF 'EnterPlanMode' "$p"; assert_true $? "$n forbids EnterPlanMode by name"
  grep -qiE 'never (ask|prompt)|no (interactive|clarif)|cannot ask' "$p"
  assert_true $? "$n carries the non-interactivity sentence"
  # Sentinel-free control: the harness decides, not the model.
  grep -qF '<<LOOP_' "$p"; assert_false $? "$n contains no <<LOOP_*>> sentinel"
  # No model name anywhere in the plugin except the Tiers: config line.
  grep -qiE '\b(haiku|sonnet|opus)\b' "$p"; assert_false $? "$n names no model"
done

# --- scout -----------------------------------------------------------------
has scout.md '"contract_path"'
has scout.md '"notes"'
has scout.md 'sprint-'
has scout.md 'allow_list'
has scout.md 'forbidden'
has scout.md '"source"'
hasre scout.md 'every *(entry|item) *in *`?forbidden|forbidden.*carr(y|ies).*source' \
  'every forbidden entry must carry a source'
hasre scout.md 'plan|spec' 'source values distinguish plan/spec from scout'
has scout.md 'evaluator_must_read'
has scout.md 'evaluator_must_view'
has scout.md 'render_gate'
has scout.md 'fidelity_source'
# plan B's addition beyond the interfaces doc's base placeholder list.
has scout.md '{{render_recipe}}'

# --- worker ----------------------------------------------------------------
has worker.md 'worker-result.json'
has worker.md '"status"'
has worker.md 'complete'
has worker.md 'partial'
has worker.md '"summary"'
has worker.md 'allow_list'
# Containment: the harness commits and the harness owns the plan.
hasre worker.md 'never (run )?`?git commit|do not commit|never commit' \
  'the Worker never commits'
hasre worker.md 'never.*LOOP_PLAN|do not (edit|touch).*LOOP_PLAN' \
  'the Worker never edits LOOP_PLAN.md'
hasre worker.md 'checkpoint' 'the Worker keeps a checkpoint current'
# The Judge picks the tier of every re-attempt from the checkpoint (spec 4.2,
# 11 item 4) — the Worker is never told about tiers or asked to escalate itself.
grep -qiE '\btiers?\b|escalat' "$D/worker.md"
assert_false $? "worker.md does not mention tiers or escalation"

# --- worker wrap-up / resume ----------------------------------------------
has worker_wrapup.md 'worker-result.json'
hasre worker_wrapup.md 'out of time|no new work|do not start' \
  'wrap-up forbids starting new work'
has worker_resume.md 'checkpoint'
has worker_resume.md '{{minutes_left}}'

# --- evaluator -------------------------------------------------------------
has evaluator.md '"verdict"'
has evaluator.md 'PASS'
has evaluator.md 'NEEDS_WORK'
has evaluator.md 'BLOCKER'
has evaluator.md '"findings"'
has evaluator.md '"criterion"'
has evaluator.md '"evidence"'
has evaluator.md '"views"'
has evaluator.md '"observation"'
hasre evaluator.md 'one (entry|observation).*(per|for each).*(screenshot|must_view)|each.*evaluator_must_view' \
  'one views entry per screenshot is mandatory'
hasre evaluator.md 'read|open' 'the evaluator is told to actually read its must-read files'

# --- judge -----------------------------------------------------------------
# judge.md carries six placeholders of its own beyond the interfaces doc's base
# list (plan C); this suite does not enumerate them, so they are allowed by
# default — see the "universal rules" comment above.
has judge.md '"decision"'
for d in retry escalate widen resume split defer halt; do
  grep -qF "$d" "$D/judge.md"; assert_true $? "judge.md lists the $d decision"
done
has judge.md '"classification"'
for c in self-imposed spec-answered capability open; do
  grep -qF "$c" "$D/judge.md"; assert_true $? "judge.md lists the $c classification"
done
has judge.md '"rationale"'
has judge.md 'allow_list_add'
has judge.md 'forbidden_remove'
has judge.md 'LOOP_DECISIONS.md'
hasre judge.md 'scout-sourced|source.*scout' 'only scout-sourced constraints may be widened'

# --- planner / reviewer / learner -----------------------------------------
has planner.md '"tasks_added"'
has planner.md 'LOOP_PLAN.md'
has reviewer.md '"findings"'
has reviewer.md '"severity"'
for s in nit should-fix must-fix; do
  grep -qF "$s" "$D/reviewer.md"; assert_true $? "reviewer.md lists the $s severity"
done
has reviewer.md 'follow_up_row'
has learner.md '"patterns"'
has learner.md '"invariants"'
has learner.md '"log"'
has learner.md 'evidence'
hasre learner.md 'gate-|gate output' 'a pattern must cite a gate output file'

assert_summary
```

- [ ] **Step 2: Run it to verify it fails**

Run: `bash plugins/agent-loop/tests/prompts.contract.sh 2>&1 | tail -20`
Expected: some `FAIL` lines and a non-zero `FAIL:` count — this is the test telling Plans A/B/C what their prompts must say. **If it fails, do not weaken the test:** fix the prompt file, because these assertions are the spec's §12 contract. Only relax an assertion if the prompt says the same thing in different words and the wording is clearly better; then update the assertion's regex, never delete it.

- [ ] **Step 3: Wire it in and delete the old test**

```bash
git rm plugins/agent-loop/tests/tick-prompt.contract.sh
chmod 644 plugins/agent-loop/tests/prompts.contract.sh
```

In `plugins/agent-loop/tests/all.sh`, replace `tick-prompt.contract.sh` with `prompts.contract.sh` in the `for t in …` list. After Plan A's rewrite the list should read (confirm against the file — Plan A deleted `lib.test.sh`, `events.test.sh`, `harness.test.sh` and `migrate.test.sh` with `lib/`):

```bash
for t in run.e2e.test.sh prompts.contract.sh setup.contract.sh medic.contract.sh postmortem.contract.sh; do
```

Verify no reference to the deleted file survives:
Run: `grep -rn 'tick-prompt' plugins/ docs/ README.md CLAUDE.md`
Expected: no output. Fix any hit found (the plugin README is rewritten in Task 10; a hit anywhere else is a leftover to remove now).

- [ ] **Step 4: Run the suite to verify it passes**

Run: `bash plugins/agent-loop/tests/prompts.contract.sh 2>&1 | tail -3`
Expected: `PASS: <n>  FAIL: 0`.

Run: `bash plugins/agent-loop/tests/all.sh 2>&1 | tail -20`
Expected: every named suite reports PASS; exit status 0.

Run: `bash scripts/lint.sh 2>&1 | tail -5`
Expected: clean, or exit 2 with a SKIP message when `shellcheck` is absent — exit 2 is a **skip, not a pass**; if shellcheck is available and flags the new file, fix it. Watch for SC1072/SC1073: a comment starting `# shellcheck ` followed by prose rather than a real directive is parsed as malformed.

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/tests/prompts.contract.sh plugins/agent-loop/tests/all.sh
git commit -m "$(cat <<'EOF'
agent-loop: prompt contracts move from tick-prompt.md to runner/prompts/*.md

The old test pinned a 41 KB prompt that was the state machine. These pin nine
role briefs instead: every one states its JSON output shape verbatim, forbids
the interactive tools a headless phase cannot use, names no model, and carries
no <<LOOP_*>> sentinel — the harness decides continue/done/halt now, not a
model. Role invariants are pinned too: the Worker never commits or edits the
plan, the Evaluator owes one views entry per screenshot, the Judge's four
classifications, the Scout's forbidden provenance.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
)"
```

---

### Task 10: READMEs, `plugin.json` 3.0.0, and the authoring-rules check

**Files:**
- Modify: `plugins/agent-loop/README.md`
- Modify: `plugins/agent-loop/.claude-plugin/plugin.json`
- Modify: `README.md` (repo root)
- Check only (no edit expected): `docs/authoring-plugins.md`, `scripts/validate.sh`

**Interfaces:**
- Consumes: everything above, plus the runner module table from the interface contract.
- Produces: nothing other tasks consume.

Two constraints that bite here. First, `CLAUDE.md:32` links `plugins/agent-loop/README.md#upgrading-loop-dir-schema`, so the **heading text must not change** — the schema-3 content goes inside the existing `### Upgrading (loop-dir schema)` section as a new migration row, and the anchor survives. Second, `scripts/validate.sh` R4 enforces the 8-section skeleton and its order; `## Install` and `## Tests` are mandatory and no heading may be renamed or reordered.

- [ ] **Step 1: Verify the authoring rules permit a `runner/` package (no edit expected)**

Run all four and record the output verbatim in the commit body:

```bash
# R9 only inspects mode 100755 against EXEC_OK; a 644 .py is never checked.
sed -n '/^echo "== file modes/,/^ok "file modes"/p' scripts/validate.sh
# run.sh is already on the allowlist; nothing under runner/ needs to be.
grep -n 'plugins/agent-loop' scripts/validate.sh
# lint.sh shellchecks *.sh only, so .py files are out of its scope.
grep -n 'find\|\.sh' scripts/lint.sh | head
# The canonical layout block is illustrative; no validate.sh rule reads it.
sed -n '/^## Canonical plugin layout/,/^## `plugin.json`/p' docs/authoring-plugins.md
```

Expected: R9 fails only files whose mode is `100755` and which are not in `EXEC_OK`; `plugins/agent-loop/run.sh` is already listed; `scripts/lint.sh` globs `*.sh`. **Conclusion: no rule blocks a `runner/` package of 644 `.py` files or a `tests/runner/` unittest tree, so `docs/authoring-plugins.md` and `scripts/validate.sh` are not edited.** Confirm the modes are right before moving on:

```bash
git ls-files -s plugins/agent-loop | awk '$1 != "100644" {print}'
```

Expected: exactly three lines — `plugins/agent-loop/run.sh`, `plugins/agent-loop/tests/fixtures/claude`, `plugins/agent-loop/tests/fixtures/dashboard-stub`, all `100755`. Anything else is a mode to fix with `chmod 644` before continuing.

- [ ] **Step 2: Rewrite `plugins/agent-loop/README.md`**

Keep the exact heading set and order already in the file — `# agent-loop`, `## What it ships` (with `### Lifecycle`, `### Artefacts`), `## Install` (`### Requirements`), `## Usage` (`### Dashboard…`, `### Attach…`, `### Medic…`, `### Exit codes`, `### Progress & usage`), `## Configuration` (`### Config`, `### Env knobs`, `### Events`, `### Operator notes`, `### Upgrading (loop-dir schema)`, `### Safety posture`, `### Dynamic model selection`), `## Tests`. Change content only. The edits:

(a) Replace the opening three paragraphs (everything between `# agent-loop` and `## What it ships`) with:

```markdown
# agent-loop

An autonomous coding loop for long-running, multi-task work (refactors, new features, test sweeps).

A Python harness (`runner/run.py`, entered through the `run.sh` shim) owns the loop's state machine and runs one **phase** at a time as its own `claude -p` process: SELECT → SCOUT → VALIDATE → WORK → SANDBOX → GATE → RENDER → FIDELITY → EVALUATE → COMMIT → LEARN, with a JUDGE phase on any failure path. The harness is the only thing that reads the plan, picks tasks, edits checkboxes, runs commands, enforces the allow-list and commits. Each model does exactly one thing from a small role brief and returns a JSON document the harness validates.

Why phases rather than one tick-shaped prompt: `--model` per phase is a command-line fact rather than a request inside a prompt; each phase has its own timeout, turn cap, budget and usage record, flushed even when it is killed; a killed Worker is `--resume`-able from its session id; and PAUSE/STOP land between phases, so a stop takes minutes rather than the rest of a 30-minute tick.

Contrast with v2: v2's spine was a 300-line prompt that re-read the plan every tick and cost 31% of one run's spend while enforcing tier directives as prose. v3 deletes it. Contrast with v1: v1 ran one long-lived in-session agent that self-scheduled and accumulated context.
```

(b) In `## What it ships`, replace the `run.sh` and `web/serve.py` rows and add two:

| `runner/` | python harness | One process per loop. Owns the lock, heartbeat, events, PAUSE/STOP, phase dispatch, the gate, git, the Judge and the dashboard sidecar. Python 3.9 stdlib only. |
| `runner/prompts/*.md` | role briefs | One per phase (scout, worker, worker_wrapup, worker_resume, evaluator, judge, planner, reviewer, learner). Each states its JSON output shape and forbids interactive tools. |
| `run.sh` | shim | `exec python3 runner/run.py "$@"` — kept so the dashboard's launcher and every `ps … run.sh` liveness check still work. |
| `web/serve.py` | dashboard server | Stdlib-only Python server; incremental bounded tail of `events.jsonl`, one shared snapshot over SSE, status derived from events + PID liveness (never the log). Serves the render gate's screenshots at `/api/artifact`. |

(c) Rewrite `### Lifecycle` around the phase list above, and `### Artefacts` to include the schema-3 additions: `LOOP_DECISIONS.md` (durable, tracked), `artifacts/<T>/<name>.png` (durable), `harness.log` (rotating 10 MB × 3, gitignored), `runtime/STOP`, `runtime/sprint-<T>.json`, `runtime/task-<T>.json` (the per-task attempt/state record, spec §14), `runtime/gate-<T>-<n>.txt`. State plainly that **`run.log` is gone**: a phase's full transcript lives in `~/.claude/projects/<encoded cwd>/<session-id>.jsonl`, and its `phase_end` event records the path.

(d) In `### Config`, replace the `**Limits**` bullet and add two:

```markdown
- **Limits** (one line, space-separated `key=value`, seconds): `tick_timeout=1800` (outer sanity cap per tick, and the figure the dashboard displays) · `scout_timeout=480` · `worker_timeout=1500` · `wrapup_timeout=300` · `eval_timeout=480` · `judge_timeout=360` · `planner_timeout=900` · `gate_cmd_timeout=600` (per verification/render/fidelity command) · `worker_resume_max=1` · `worker_budget_usd=6` · `max_attempts=3` (hard cap on attempts for one task, resumes and escalations included). Each phase has its own wall; the tick has no single one. There is still **no cost/iteration/wall-clock budget for the run** — the subscription usage window is the only ceiling, and the harness auto-waits on it.
- **Tiers** (one line): `cheap=<alias> standard=<alias> most-capable=<alias>`. The only place this plugin names a model. Phases request a tier; the harness resolves it to `--model` at dispatch. A Worker's second attempt escalates one tier, enforced by the harness.
- **Decision policy**: `autonomous` (default — before marking `[!]`, halting or raising needs-human, a Judge phase may widen a Scout-invented constraint, escalate a tier, split a task, or pick a default where the spec is silent, appending every choice to `LOOP_DECISIONS.md` with its alternatives) or `conservative` (widen/escalate/split allowed; spec-silent defaults deferred to `LOOP_CLEANUP.md`).
```

(e) In `### Events`, add the five new types: `phase_end` (`phase`, `rc`, `dur`, `session_id`, `transcript`), `artifact` (`tick`, `task`, `name`, `path`), `decision` (`task`, `decision`, `classification`), `resume` (`task`, `attempt`), `split` (`task`, `into`). Note that none of them is a lifecycle type, so none can change the derived status.

(f) In `### Operator notes`, add the Pause/Stop-now paragraph (same two-bullet contrast as Task 6(d)), and note that Resume deletes **both** sentinels.

(g) In `### Upgrading (loop-dir schema)` — **do not rename the heading**; `CLAUDE.md:32` links its anchor. Change `LOOP_SCHEMA` in `lib/migrate.sh` to `LOOP_SCHEMA` in `runner/migrate.py`, and add a row to the migration table:

```markdown
| 2 → 3 | `artifacts/` and `LOOP_DECISIONS.md` created; `runtime/STOP` becomes a sentinel the harness reads; `harness.log` replaces `run.log` as the harness's own log; `runtime/task-<T>.json` replaces `attempts-<T>.json` as the per-task attempt/state record; contracts gain `forbidden[].source`, `render_gate`, `fidelity_source`, `evaluator_must_read`, `evaluator_must_view` | v3 runs phases rather than ticks, so a stop needs a second sentinel and screenshots need somewhere durable to live. Migration creates the two empty files and leaves everything else untouched: a v2 `LOOP_PLAN.md`, `LOOP_CONFIG.md`, learnings and usage ledger all keep working, and an existing `run.log` is left in place (the dashboard falls back to it). Contracts written by v2 are re-validated and re-scouted per task; none is rewritten in place. **A dir stopped mid-task upgrades and resumes**: boot reads the `[~]` task's `task-<T>.json` and continues from its recorded phase, and a task with no state file (a 2.x dir, or a crash before the first write) goes to the Judge with `failure="boot-reconcile"` (spec §14) — no human step is required. |
```

Update the **Authoring rule** paragraph at the end of that section to name `runner/migrate.py` and `tests/runner/test_migrate.py` instead of `lib/migrate.sh` and `tests/migrate.test.sh`.

(h) Replace `### Dynamic model selection` wholesale:

```markdown
### Dynamic model selection

The governing rule is unchanged — **use the least powerful model that can handle each phase** — but in v3 it is enforced in code, not asked for in a prompt. Each phase is dispatched with an explicit `--model`, resolved from the `Tiers:` line at launch time. The plugin hardcodes **no** model names; `Tiers:` is the only place an alias ever appears, which makes swapping in a new frontier model a one-word edit.

Defaults, and why the leverage sits where it does:

- **Scout — standard, not cheap.** The contract is the highest-leverage document in a tick: it fixes the allow-list, the success criteria, the render gate and the fidelity check. In the analysed run the three defective contracts were all written by the cheap tier, and one of them invented a `forbidden` entry that then blocked three Workers.
- **Worker — standard, escalated one tier on the second attempt by the harness.** v2 asked the model to escalate itself and it retried on the same tier 6 times out of 7.
- **Evaluator — class-governed.** `| mechanical` skips it, `| complex` runs it most-capable, everything else standard. A tier set in `LOOP_CONFIG.md` is a ceiling for `| complex` only.
- **Judge, Planner, Reviewer — most-capable.** These are the only phases making a judgement call, and the Judge runs only on failure paths.
- **Learner — cheap.** It summarises evidence it is handed.

The Evaluator still returns `PASS | NEEDS_WORK | BLOCKER`, where `BLOCKER` means the diff only "passes" via a workaround or a spec deviation — but it now also owes one observation per screenshot it was told to view, and the harness rejects a verdict that is missing one. That, rather than policing tool calls, is what stopped both v2 Evaluators reading zero reference files on a copy task.
```

(i) Rewrite `## Tests`:

```markdown
## Tests

```bash
bash tests/all.sh
```

Runs `run.e2e.test.sh` (against a scripted stub `claude` that can time a phase out, return malformed JSON, or answer a `--resume`), the four prompt/skill contract suites (`prompts.contract.sh`, `setup.contract.sh`, `medic.contract.sh`, `postmortem.contract.sh`), the runner unit tests (`python3 -m unittest discover -s tests/runner` — plan grammar round-trip against serve.py's regexes, contract validation, the stream parser, budgets and wrap-up, Judge decisions under both policies, the fidelity ratio, SANDBOX revert, commit trailers), `serve.test.py`, and `web.contract.sh`. Python suites are skipped non-fatally when `python3` is absent.

The contract suites pin promises, not implementations: the medic's allowlist and outcomes; the attach skill's Monitor command, its "never `pgrep`" rule and the Pause-vs-Stop-now distinction; each role brief's JSON output shape, its non-interactivity rule, and the absence of any `<<LOOP_*>>` sentinel. Linting is repo-wide via `scripts/lint.sh`, run once by `scripts/test-all.sh`, not per-plugin. Note: the shell suites use `mktemp -d`; if the sandbox blocks it, run with the sandbox disabled.
```

(j) In `### Operator notes`, add the rollout procedure (spec §13) as its own paragraph — this is the only place the operator will look for it:

```markdown
**Upgrading a machine that is mid-run.** Land v3 with the suite green and no loop running on the machine you built it on. On the loop machine: pull, then pause the running 2.1 harness at a tick boundary (`touch <loop-dir>/runtime/PAUSE`, wait for its terminal to exit, delete the file), and relaunch — `run.sh` migrates the dir to schema 3 on the way up. The dashboard needs no restart; it re-reads the dir. For the first real v3 run set `Decision policy: conservative` for one segment and read `LOOP_DECISIONS.md` before flipping to `autonomous` — the ledger is the thing you are learning to trust, and a segment is enough to see whether its entries are sane. When the run closes, have the postmortem compare $/task, wall-clock/task, human touches and needs-human count against the last 2.x run on the same repo; those four numbers are what v3 claims to move.
```

(k) In `### Safety posture`, add a closing paragraph:

```markdown
**Deferred: concurrency and async evaluation.** The Evaluator stays a synchronous phase in v3, on the critical path of every tick — see spec §15 for why (moving it off the path is unsafe across a dependency edge until every task a later one depends on has been *evaluated*, not merely committed) and the edge-case ledger a future implementation must honour. Not designed or scheduled; recorded so it is not re-litigated from scratch.
```

- [ ] **Step 3: Update `plugin.json` and the root README**

`plugins/agent-loop/.claude-plugin/plugin.json` — set `"version": "3.0.0"` and replace the description (no model names, one paragraph):

```json
  "description": "Autonomous coding loop for long-running tasks (refactors, new features, test sweeps). A Python harness owns the state machine and runs one phase at a time as its own headless process — scout, worker, gate, render, evaluate, commit, learn, with a judge on any failure path — each with its own model tier, timeout, budget and usage record. It enforces the allow-list, screenshots the product and checks copy tasks for real similarity before anything commits, resumes or splits a timed-out worker instead of losing the tick, and settles the decisions it can settle on its own into an auditable ledger rather than waking a human. Pause and Stop land at a phase boundary; a browser dashboard shows the phase in flight, the screenshots and the decisions. On an incident it runs a budgeted /agent-loop-medic that repairs only from an allowlist and hands over to a human (NEEDS_HUMAN.md, exit 2) when it cannot. Requires the superpowers plugin; bundles the postmortem plugin and recommends the permissions plugin for its /permissions-advisor pre-flight check.",
```

Root `README.md` — the install line (`/plugin install agent-loop@claude-toolbelt`) needs no change. Replace the `agent-loop` cell in the Plugins table (it currently says "a bash harness", which is now false):

```
| `agent-loop`        | Autonomous coding loop for long-running multi-task work — a Python harness owns the state machine and runs one phase at a time as its own headless process, each with its own model tier, timeout and budget. Screenshots the product before it commits, resumes or splits a timed-out worker, settles what it can into an auditable decisions ledger, and hands over to a human through a budgeted `/agent-loop-medic` when it cannot. Requires `superpowers`; bundles `postmortem` and uses the `permissions` plugin's `/permissions-advisor`. |
```

And replace the `### [`agent-loop`](plugins/agent-loop/README.md)` details paragraph:

```markdown
An autonomous coding loop for long-running, multi-task work. A Python harness (`runner/run.py`, entered through a `run.sh` shim) owns the loop's state machine: it reads the plan, picks the next eligible task, and runs one **phase** at a time as its own headless process — scout writes the contract, a worker edits only what the allow-list permits, the harness runs the verification gate, renders the product and screenshots it, checks copy tasks for real line-similarity, an evaluator judges against the criteria with those screenshots in hand, and only then does the harness commit. Any failure path goes to a judge phase that may widen a constraint the scout invented, escalate a tier, resume or split a timed-out worker, or defer — logging every autonomous choice to `LOOP_DECISIONS.md` for a human to ratify or reverse. Each phase carries its own model tier, timeout, turn cap and usage record, flushed even when it is killed, so a stop lands at a phase boundary in minutes. The harness owns the lock and heartbeat (liveness comes from `runtime/harness.json`, never `pgrep`), shares exactly one browser dashboard per loop, and on an incident runs `/agent-loop-medic` — a bounded triage-and-repair agent that applies only allowlisted fixes and otherwise stops the loop with `NEEDS_HUMAN.md`. `/agent-loop` attaches a session: health, the dashboard, and an incident monitor that wakes the session to run the medic. Requires the `superpowers` plugin; bundles `postmortem` for close-out and uses the `permissions` plugin's `/permissions-advisor` when available.
```

- [ ] **Step 4: Run the validators**

Run: `bash scripts/validate.sh 2>&1 | tail -20`
Expected: `validate: all invariants hold`. R4 checks the README heading set and order; R9 the file modes. Note `validate.sh` silently skips `claude plugin validate --strict` when the `claude` CLI is absent — if it is present, the version check compares `plugin.json` against the marketplace entry (which carries no version, so there is nothing to drift).

Run: `grep -c 'haiku\|sonnet\|opus' plugins/agent-loop/README.md plugins/agent-loop/.claude-plugin/plugin.json README.md`
Expected: `0` for all three.

Run: `grep -n 'upgrading-loop-dir-schema' CLAUDE.md` then `grep -n '^### Upgrading' plugins/agent-loop/README.md`
Expected: the heading is still exactly `### Upgrading (loop-dir schema)`, so `CLAUDE.md`'s anchor resolves.

Run: `grep -rn 'tick-prompt\|lib/loop.sh\|lib/migrate.sh\|run\.log' plugins/agent-loop/README.md README.md`
Expected: `run.log` appears only where the README explains that it is gone and that a v2 dir still has one; no `tick-prompt` or `lib/` hit at all.

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/README.md plugins/agent-loop/.claude-plugin/plugin.json README.md
git commit -m "$(cat <<'EOF'
agent-loop: 3.0.0 — docs describe the phase runner

Plugin README rewritten on the 8-section skeleton around phases, the schema-3
migration row, the per-phase Limits keys, Tiers and Decision policy. The
Upgrading heading is deliberately unchanged: CLAUDE.md links its anchor. Root
README table row and details section no longer describe a bash harness.

docs/authoring-plugins.md and scripts/validate.sh are NOT edited: R9 only
inspects mode-100755 files against EXEC_OK, run.sh is already allowlisted, and
lint.sh globs *.sh — so a runner/ package of 644 .py files and a tests/runner
unittest tree need no new rule.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
)"
```

---

### Task 11: Walk the `CLAUDE.md` checklist and prove the suite is green

**Files:**
- No source edits expected. Any edit this task produces is a gap the earlier tasks left.

**Interfaces:**
- Consumes: everything above.
- Produces: the pasted `bash scripts/test-all.sh` output that `CLAUDE.md` requires before calling this done.

- [ ] **Step 1: Walk "Changing a plugin" item by item and record the evidence**

Run each command and paste its output under the matching checklist line. Do not tick a line from memory.

```bash
# [ ] Bump the version in plugin.json (semver)
python3 -c 'import json; print(json.load(open("plugins/agent-loop/.claude-plugin/plugin.json"))["version"])'
# expect: 3.0.0 — major, because the loop-dir layout, the config keys and the
# harness entry point all changed meaning.

# [ ] Update the plugin's README.md
git log --oneline -1 -- plugins/agent-loop/README.md

# [ ] Schema rule: agent-loop's own Upgrading section
grep -n 'LOOP_SCHEMA' plugins/agent-loop/runner/migrate.py
grep -n '| 2 → 3 |' plugins/agent-loop/README.md
python3 -m unittest discover -s plugins/agent-loop/tests/runner -p 'test_migrate*.py' -v 2>&1 | tail -5

# [ ] Re-run bash scripts/test-all.sh   (Step 2)

# [ ] Prompt-shaped plugins (cutthroat, postmortem, find-docs) run the eval
# suite — agent-loop is NOT on that list, so no eval run is required here.
grep -n 'eval suite' CLAUDE.md
```

- [ ] **Step 2: Run the full suite**

Run: `bash scripts/test-all.sh 2>&1 | tail -60`
Expected: every section green and exit status 0. Paste the output verbatim in the final report — `CLAUDE.md` forbids calling this done without it.

Read the tail carefully for the two false-green traps this repo documents:
- `scripts/lint.sh` exits **2** when `shellcheck` is not on PATH. That is a SKIP, not a pass. If it exits 2, install shellcheck (`brew install shellcheck`) and re-run before claiming green.
- `scripts/validate.sh` silently skips `claude plugin validate --strict` when the `claude` CLI is absent. Note in the report whether that check actually ran.

If anything fails, fix it in the task that owns the file and amend that task's commit rather than piling a fix-up commit on the end.

- [ ] **Step 3: Confirm nothing stale survives**

```bash
git ls-files plugins/agent-loop | grep -E 'tick-prompt|^plugins/agent-loop/lib/'
# expect: no output

grep -rn 'Orchestrator model' plugins/agent-loop --include='*.md' | grep -v templates/LOOP_CONFIG.md
# expect: no output (the template keeps it as an inert key, nothing else mentions it)

git ls-files -s plugins/agent-loop | awk '$1 != "100644" {print}'
# expect: exactly run.sh, tests/fixtures/claude, tests/fixtures/dashboard-stub — all 100755

git status --porcelain
# expect: empty
```

- [ ] **Step 4: Commit (only if Step 3 found something to fix)**

```bash
git add -- <the specific files fixed>
git commit -m "$(cat <<'EOF'
agent-loop: clear the last v2 references found by the release checklist

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
EOF
)"
```

If Step 3 found nothing, there is nothing to commit — say so and stop.

---

## Interface gaps this plan filled

Recorded here because the executor will hit them and should not re-litigate them:

1. **`/api/stop` refusal direction.** The brief said both "refuses like `/api/pause` when no harness alive" and "409 when live, sentinel file when not". These contradict. `/api/pause` never refuses at all, so the comparison was wrong either way. This plan refuses when **no** harness is alive, because a STOP sentinel with nobody to read it stops the *next* launch instead of this one, and because `renderMast` already disables the button in exactly that state. `/api/start` and `/api/resume` keep the opposite guard (409 when a harness *is* alive) — the two guards are inverse for a reason.
2. **Artifact addressing.** The interface contract stores artifacts at `artifacts/<T>/<name>.png`, keyed by **task**, while the route is specified as `?tick=&name=`. This plan keeps the route as specified and resolves it through a bounded `(tick, name) -> path` index built from the `artifact` events, so the query string never becomes a filesystem path and the traversal guard has something real to guard (the Scout writes those paths).
3. **The existing `btnStop`.** v2 already had a Stop button wired to `/api/stop` (PAUSE + SIGTERM). Rather than adding a second stop control, this plan repoints `/api/stop` at the STOP sentinel and relabels the button "Stop now", keeping the element id that `web.contract.sh` pins.
4. **`### Upgrading` heading text.** The brief asked for a section titled "Upgrading (loop-dir schema 3)". `CLAUDE.md:32` links `#upgrading-loop-dir-schema`, so renaming it breaks a tracked doc link. The heading stays; the schema-3 content goes inside it as a migration row.
5. **Root README table row.** The brief said the table row was unchanged. Its current text ("a bash harness that owns exclusivity and liveness") becomes false in v3, so this plan rewrites it. The install line genuinely is unchanged.
6. **`docs/authoring-plugins.md` / `scripts/validate.sh`.** Checked (Task 10 Step 1): R9 only inspects mode-100755 files, `run.sh` is already on `EXEC_OK`, and `lint.sh` globs `*.sh`. Nothing blocks a `runner/` package or a `tests/runner/` unittest tree, so neither file is edited. The canonical-layout block in the doc is illustrative, not enforced.
7. **`import signal`.** Removing the SIGTERM from `Supervisor.stop` leaves `import signal` with no user in `serve.py`; Task 1 deletes it and Task 1 Step 4 names the symptom if a caller was missed.
