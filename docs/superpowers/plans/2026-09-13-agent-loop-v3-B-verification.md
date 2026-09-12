# agent-loop v3 — Plan B: verification gates (render, fidelity, evaluator inputs)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the loop *see* what it built — a render gate that produces real screenshots, a diff-similarity check that hard-fails fake "copies", and an Evaluator that is handed the reference code and the images instead of being trusted to fetch them.

**Architecture:** A new `runner/render.py` owns three harness-side (non-LLM) steps — start/probe the app, run the render commands and archive their screenshots into `$LOOP_DIR/artifacts/<T>/`, and compute `difflib` similarity for every declared copy pair. `config.py` grows a multi-line `Render:` recipe block whose `ui_globs` feed `contract.validate`. `phases.run_evaluator` pre-loads the must-read files and the screenshot paths into its prompt and rejects a verdict that omits a required view, re-asking once. `run.py` calls the whole thing between GATE and EVALUATE; any failure goes down the existing failure path.

**Tech Stack:** Python 3.9 stdlib only (`difflib`, `urllib.request`, `subprocess`, `shutil`, `fnmatch`, `unittest`), bash 3.2 for `.sh`, git.

**Spec:** `docs/superpowers/specs/2026-09-13-agent-loop-v3-phase-runner-design.md` (§4.6 `Render:`, §5.1–5.4, §10, §12). Interface contract: `docs/superpowers/plans/2026-09-13-agent-loop-v3-00-interfaces.md`.

**Evidence:** `~/Downloads/EXTRACT/08-analysis/w5-fidelity-forensics.md` (the R3 "copy" was a six-line `TODO(rise-regression)` comment that passed because `verification` grepped for the comment; both Evaluators read zero reference files; T8's opus Evaluator made 12 Reads, none of `apps/frontend`) and `w8-deferrals-and-verification.md` Part B (one browser-shaped tool call in 88 ticks, and it was reverted; the human found the broken shell 1 h 51 m before the loop's first Cypress run).

## Depends on Plan A

Plan A (`2026-09-13-agent-loop-v3-A-runner-core.md`) must be merged first. This plan consumes, and never redefines:

- `runner/config.py`: `LoopConfig`, `load_config`, `model_for`, `phase_limit`
- `runner/contract.py`: `Contract`, `RenderGate`, `FidelitySource`, `validate`
- `runner/gate.py`: `CommandResult`, `run_commands`, `all_ok`
- `runner/git_ops.py`: `similarity`
- `runner/events.py`: `EventLog.emit`
- `runner/claude_proc.py`: `PhaseResult`, `Usage`, `run_phase`
- `runner/phases.py`: `TickContext` (fields `cfg, plan, task, loop_dir, runtime_dir, events, tick, attempt`), `render_prompt`, `parse_json_block`, `run_evaluator`
- `runner/prompts/scout.md`, `runner/prompts/evaluator.md` (with the `{{must_read_blocks}}` and `{{screenshots}}` placeholders already present)
- `tests/fixtures/claude` (the scripted stub; `STUB_SCRIPT` protocol in the interfaces doc)
- `tests/all.sh` already runs `python3 -m unittest discover -s tests/runner`. If it does not, add that line in Task 2's commit.

## Global Constraints

- Python 3.9 stdlib only; no third-party packages. macOS `/usr/bin/python3` is 3.9.
- Type annotations use `typing.Optional` / `typing.List` / `typing.Dict`. No `X | None` in executable positions, no `match` statement. Every new module starts with `from __future__ import annotations`.
- bash 3.2 for any `.sh`: no `mapfile`, no `declare -A`, no `${var,,}`; `"${arr[@]}"` on an empty array is fatal under `set -u`.
- Every runtime file is written atomically: write `<path>.tmp`, then `os.replace`.
- No model name anywhere in the plugin except the `Tiers:` config line and the template default `Tiers: cheap=haiku standard=sonnet most-capable=opus`.
- `LOOP_SCHEMA = 3`. Plugin version `3.0.0`. (Plan D owns the bump; do not touch `plugin.json` here.)
- Exit codes: 0 done/paused/rate-limit-exit, 1 halt/error, 2 needs-human, 3 lock-conflict.
- Events envelope: one compact JSON object per line, fields `t`, `seq`, `type`, then payload.
- `bash scripts/test-all.sh` must pass at the end of every task, and is the definition of done for the plan.
- Commit message subject is `agent-loop: <what>`; every commit body ends with:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
  ```

## File Structure

| file | responsibility |
|---|---|
| `plugins/agent-loop/runner/render.py` | **new.** Render app lifecycle, render gate + screenshot archival, fidelity ratios, and the `run_visual_checks` façade `run.py` calls. |
| `plugins/agent-loop/runner/config.py` | **modified.** `_parse_render_block` + `load_config` populate `cfg.render`. |
| `plugins/agent-loop/runner/phases.py` | **modified.** `run_evaluator` pre-loads must-read files and screenshots, validates `views[]`, re-asks once; `run_scout` passes `{{render_recipe}}`. |
| `plugins/agent-loop/runner/run.py` | **modified.** GATE → RENDER → FIDELITY → EVALUATE; `ui_globs` into `contract.validate`; `stop_app` in the shutdown path. |
| `plugins/agent-loop/runner/prompts/scout.md` | **modified.** Appended "Render, fidelity and evaluator inputs" section. |
| `plugins/agent-loop/skills/agent-loop-setup/SKILL.md` | **modified.** Wizard question 12 (render recipe), the no-recipe warning, `artifacts/` in the run-dir `.gitignore`. |
| `plugins/agent-loop/templates/LOOP_CONFIG.md` | **modified.** Commented `Render:` block. |
| `plugins/agent-loop/tests/runner/test_config_render.py` | **new.** Block parsing, inline form, template-parses-to-empty, `ui_globs` → `contract.validate`. |
| `plugins/agent-loop/tests/runner/test_render.py` | **new.** Render commands run, screenshots archived atomically, `artifact` events, missing-screenshot failure. |
| `plugins/agent-loop/tests/runner/test_render_app.py` | **new.** `ensure_app`/`stop_app`, pid file, `ready:` polling and timeout. |
| `plugins/agent-loop/tests/runner/test_fidelity.py` | **new.** ≥0.9 for a real copy, <0.2 for the six-line comment, threshold + `fidelity-<T>.json`. |
| `plugins/agent-loop/tests/runner/test_visual_checks.py` | **new.** `run_visual_checks` composition + `run.py` wiring. |
| `plugins/agent-loop/tests/runner/test_phases_evaluator.py` | **new.** must-read blocks, truncation, screenshot listing, missing-view re-ask (recorder + stub `claude`). |
| `plugins/agent-loop/tests/runner/test_prompts_scout_render.py` | **new.** Scout prompt contract. |
| `plugins/agent-loop/tests/setup.contract.sh` | **modified.** Render-recipe assertions. |

## Runtime files this plan adds

- `$LOOP_DIR/artifacts/<T>/<name>.png` — archived screenshots (durable; gitignored, read from disk by the postmortem).
- `$LOOP_DIR/runtime/gate-render-<T>-<n>.txt` — render command output (written by `gate.run_commands` with tag `render-<T>`).
- `$LOOP_DIR/runtime/fidelity-<T>.json` — `{"task","t","checks":[{"src","dst","ratio","min","ok"}]}`.
- `$LOOP_DIR/runtime/render-app.pid` — pid of the backgrounded `start:` process.
- `$LOOP_DIR/runtime/render-app.log` — that process's stdout/stderr.

---

### Task 1: `Render:` recipe block in `config.py`

**Files:**
- Modify: `plugins/agent-loop/runner/config.py`
- Modify: `plugins/agent-loop/runner/run.py` (one call site)
- Test: `plugins/agent-loop/tests/runner/test_config_render.py`

**Interfaces:**
- Consumes: `LoopConfig`, `load_config` (plan A); `contract.validate(c, task, cfg, cleanup_text, ui_globs)` (plan A).
- Produces: `cfg.render` — a dict with any of `start: str`, `ready: str`, `command: str`, `ui_globs: List[str]`, `reference: List[{"name","path"}]`. `{}` when the config has no `Render:` line. Also `config._parse_render_block(lines, i) -> (dict, int)`.

**Grammar (this is the authority; spec §4.6 defers to §5.3):**

```
Render:
  start: pnpm --filter @repo/internal dev --port 5273
  ready: http://127.0.0.1:5273/
  command: pnpm --filter @repo/integration cypress run --spec {route} --env screenshot={screenshot}
  ui_globs: apps/*/src/**/*.tsx packages/ui/**/*.tsx
  reference: rise-customers=docs/reference/rise-customers.png rise-header=docs/reference/rise-header.png
```

Sub-keys are indented at least one space, `key: value`. The block ends at the first non-indented, non-blank line or EOF. Blank lines and indented `#` comments are skipped. Unknown sub-keys are ignored. An inline value on the `Render:` line itself (`Render: cypress run --spec {route}`) is taken as `command`, except a `<placeholder>` value, which is ignored.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_config_render.py`:

```python
"""Render: recipe block parsing (plan B task 1)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from runner import config as config_mod  # noqa: E402

BASE = """# Loop Config
Started: 2026-09-13T00:00:00Z
Goal: rebuild the internal app
Loop type: new-feature
Granularity: segmented
TDD mode: none
Verification pipeline: lint tsc build test
Limits: tick_timeout=1800 scout_timeout=480
Blocker policy: continue-independent
Branch: agent-loop-internal
Worktree: /tmp/wt
Spec: docs/superpowers/specs/x-design.md
Plan: docs/superpowers/plans/x.md
Dashboard: auto
Medic: auto
Medic model:
Tiers: cheap=haiku standard=sonnet most-capable=opus
Decision policy: autonomous
"""

RECIPE = """Render:
  start: pnpm --filter @repo/internal dev --port 5273
  ready: http://127.0.0.1:5273/
  # a comment inside the block is ignored
  command: cypress run --spec {route} --env screenshot={screenshot}
  ui_globs: apps/*/src/**/*.tsx packages/ui/**/*.tsx
  reference: rise-customers=docs/reference/rise-customers.png rise-header=docs/ref/h.png

Segment count: 3
"""


def write_cfg(body):
    fd, path = tempfile.mkstemp(suffix="-LOOP_CONFIG.md")
    with os.fdopen(fd, "w") as fh:
        fh.write(body)
    return path


class RenderBlockTest(unittest.TestCase):
    def test_absent_render_block_gives_empty_dict(self):
        cfg = config_mod.load_config(write_cfg(BASE))
        self.assertEqual(cfg.render, {})

    def test_full_block_parses_every_subkey(self):
        cfg = config_mod.load_config(write_cfg(BASE + RECIPE))
        self.assertEqual(cfg.render["start"], "pnpm --filter @repo/internal dev --port 5273")
        self.assertEqual(cfg.render["ready"], "http://127.0.0.1:5273/")
        self.assertEqual(
            cfg.render["command"],
            "cypress run --spec {route} --env screenshot={screenshot}",
        )
        self.assertEqual(
            cfg.render["ui_globs"],
            ["apps/*/src/**/*.tsx", "packages/ui/**/*.tsx"],
        )
        self.assertEqual(
            cfg.render["reference"],
            [
                {"name": "rise-customers", "path": "docs/reference/rise-customers.png"},
                {"name": "rise-header", "path": "docs/ref/h.png"},
            ],
        )

    def test_block_ends_at_the_next_unindented_key(self):
        cfg = config_mod.load_config(write_cfg(BASE + RECIPE))
        self.assertNotIn("Segment count", cfg.render)
        self.assertEqual(cfg.branch, "agent-loop-internal")

    def test_inline_value_is_taken_as_command(self):
        cfg = config_mod.load_config(write_cfg(BASE + "Render: cypress run --spec {route}\n"))
        self.assertEqual(cfg.render, {"command": "cypress run --spec {route}"})

    def test_placeholder_inline_value_is_ignored(self):
        cfg = config_mod.load_config(write_cfg(BASE + "Render: <command recipe, see 5.3>\n"))
        self.assertEqual(cfg.render, {})

    def test_shipped_template_parses_with_no_recipe(self):
        tmpl = os.path.join(PLUGIN_ROOT, "templates", "LOOP_CONFIG.md")
        cfg = config_mod.load_config(tmpl)
        self.assertEqual(cfg.render, {})


class UiGlobsFeedValidationTest(unittest.TestCase):
    def test_ui_glob_match_without_render_gate_is_a_validation_error(self):
        from runner import contract as contract_mod
        from runner import plan as plan_mod

        cfg = config_mod.load_config(write_cfg(BASE + RECIPE))
        c = contract_mod.Contract(
            task="T60",
            success_criteria=["the customers page renders"],
            allow_list=["apps/internal/src/pages/Customers.tsx"],
            forbidden=[],
            verification=["pnpm lint"],
            render_gate=None,
            fidelity_source=[],
            evaluator_must_read=[],
            evaluator_must_view=[],
            estimated_diff_lines=120,
            scout_notes="",
            relevant_learnings=[],
        )
        task = plan_mod.Task(
            id="T60", segment="Segment 1", title="build the customers page", state="doing",
            sha=None, class_flag=None, depends_on=[], clone_of=None, copy_of=None,
            blocked_by=[], no_ui=False, model=None, line_no=10,
            raw="- [ ] T60 build the customers page",
        )
        errs = contract_mod.validate(c, task, cfg, "", cfg.render["ui_globs"])
        self.assertTrue(
            any("render_gate" in e for e in errs),
            "expected a render_gate-required error, got %r" % (errs,),
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest tests.runner.test_config_render -v`
Expected: FAIL — `AttributeError: 'LoopConfig' object has no attribute 'render'` or `KeyError: 'start'` (plan A leaves `render` at `{}` unconditionally).

- [ ] **Step 3: Add `_parse_render_block` to `config.py`**

Insert above `load_config`:

```python
_RENDER_KEYS = ("start", "ready", "command", "ui_globs", "reference")


def _parse_render_block(lines, i):
    """Parse the `Render:` block whose header is lines[i].

    Returns (render_dict, index_of_first_line_after_the_block).
    """
    render = {}
    inline = lines[i].split(":", 1)[1].strip()
    if inline and not inline.startswith("<"):
        render["command"] = inline
    j = i + 1
    while j < len(lines):
        line = lines[j].rstrip("\n")
        if line.strip() == "":
            j += 1
            continue
        if not (line.startswith(" ") or line.startswith("\t")):
            break
        body = line.strip()
        if body.startswith("#"):
            j += 1
            continue
        if ":" not in body:
            break
        key, _, val = body.partition(":")
        key = key.strip()
        val = val.strip()
        if key in _RENDER_KEYS and val:
            render[key] = val
        j += 1
    if "ui_globs" in render:
        render["ui_globs"] = render["ui_globs"].split()
    if "reference" in render:
        refs = []
        for token in render["reference"].split():
            name, sep, path = token.partition("=")
            if sep and name and path:
                refs.append({"name": name, "path": path})
        render["reference"] = refs
    return render, j
```

- [ ] **Step 4: Rewrite `load_config` so it consumes the block**

Replace the whole body of `load_config` with this. It is plan A's flat `Key: value` scan plus the `Render:` branch — if plan A's version carries extra fields or different default handling, **keep plan A's lines and splice in only the two blocks marked `# --- plan B ---`**; the contract is that `Render:` is consumed as a block and never leaks into another key.

```python
def load_config(path):
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()

    flat = {}
    render = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("##"):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        # --- plan B ---
        if key == "Render":
            render, i = _parse_render_block(lines, i)
            continue
        # --- end plan B ---
        flat[key] = val.strip()
        i += 1

    limits = dict(DEFAULT_LIMITS)
    for pair in flat.get("Limits", "").split():
        k, _, v = pair.partition("=")
        if k and v:
            try:
                limits[k] = int(v)
            except ValueError:
                pass

    tiers = {"cheap": "haiku", "standard": "sonnet", "most-capable": "opus"}
    for pair in flat.get("Tiers", "").split():
        k, _, v = pair.partition("=")
        if k and v:
            tiers[k] = v

    role_tiers = {}
    for role, key in (("planner", "Planner tier"), ("scout", "Scout tier"),
                      ("worker", "Worker tier"), ("evaluator", "Evaluator tier")):
        role_tiers[role] = flat.get(key, "").strip()

    return LoopConfig(
        worktree=flat.get("Worktree", ""),
        branch=flat.get("Branch", ""),
        goal=flat.get("Goal", ""),
        granularity=flat.get("Granularity", "single"),
        tdd_mode=flat.get("TDD mode", "none"),
        verification=flat.get("Verification pipeline", "").split(),
        blocker_policy=flat.get("Blocker policy", "continue-independent"),
        dashboard=flat.get("Dashboard", "auto"),
        medic=flat.get("Medic", "auto"),
        medic_model=flat.get("Medic model", ""),
        tiers=tiers,
        role_tiers=role_tiers,
        limits=limits,
        decision_policy=flat.get("Decision policy", "autonomous"),
        # --- plan B ---
        render=render,
        # --- end plan B ---
        spec_path=flat.get("Spec", ""),
        plan_path=flat.get("Plan", ""),
    )
```

- [ ] **Step 5: Pass `ui_globs` into contract validation in `run.py`**

In `run.py`, find plan A's single call to `contract.validate(...)` in the execute path and replace its last argument so the call reads exactly:

```python
    errors = contract_mod.validate(
        c, task, cfg, cleanup_text, cfg.render.get("ui_globs", [])
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd plugins/agent-loop && python3 -m unittest tests.runner.test_config_render -v`
Expected: PASS, 7 tests.

- [ ] **Step 7: Commit**

```bash
git add plugins/agent-loop/runner/config.py plugins/agent-loop/runner/run.py \
        plugins/agent-loop/tests/runner/test_config_render.py
git commit -m "$(cat <<'MSG'
agent-loop: parse the Render: recipe block into cfg.render

A multi-line block under `Render:` with start/ready/command/ui_globs/reference
sub-keys. ui_globs is what makes contract.validate demand a render_gate on a
UI-touching task (spec 5.1); without it every UI task ships unseen, which is
what the 2026-09-11 run did for 88 ticks.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
MSG
)"
```

---

### Task 2: `runner/render.py` — the render gate

**Files:**
- Create: `plugins/agent-loop/runner/render.py`
- Test: `plugins/agent-loop/tests/runner/test_render.py`

**Interfaces:**
- Consumes: `gate.run_commands(cmds, cwd, timeout_s, out_dir, tag) -> List[CommandResult]`, `gate.CommandResult(cmd, rc, duration_s, output_path, timed_out)`, `gate.all_ok`, `config.phase_limit(cfg, "gate_cmd")`, `EventLog.emit`, `TickContext` (`cfg, task, loop_dir, runtime_dir, events, tick`), `contract.RenderGate(commands, screenshots)`.
- Produces:
  - `render.run_render_gate(ctx, contract) -> Tuple[List[CommandResult], List[Dict]]` — screenshot dicts are `{"name": str, "path": str}` with `path` the **absolute archived** path under `$LOOP_DIR/artifacts/<T>/`.
  - `render.artifacts_dir(ctx) -> str`
  - `render.reference_screenshots(cfg) -> List[Dict]` — `{"name","path","kind":"reference"}`, absolute, existing files only.
  - Every screenshot dict may carry `"kind"` (`"new"` when absent). Consumers must treat a missing `kind` as `"new"`.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_render.py`:

```python
"""Render gate: commands, screenshot archival, artifact events (plan B task 2)."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import types
import unittest

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from runner import contract as contract_mod  # noqa: E402
from runner import gate as gate_mod  # noqa: E402
from runner import render as render_mod  # noqa: E402

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
# bash 3.2 printf understands octal escapes; this writes exactly the PNG magic bytes.
WRITE_PNG = "mkdir -p shots && printf '\\211PNG\\r\\n\\032\\n' > shots/%s"


class FakeEvents(object):
    def __init__(self):
        self.emitted = []

    def emit(self, type, **fields):
        self.emitted.append((type, fields))


def make_ctx(tmp, task_id="T60"):
    worktree = os.path.join(tmp, "wt")
    loop_dir = os.path.join(worktree, ".claude", "loop", "run")
    runtime_dir = os.path.join(loop_dir, "runtime")
    os.makedirs(runtime_dir)
    cfg = types.SimpleNamespace(
        worktree=worktree,
        render={},
        limits={"gate_cmd_timeout": 60},
    )
    task = types.SimpleNamespace(id=task_id, no_ui=False, copy_of=None)
    return types.SimpleNamespace(
        cfg=cfg, task=task, loop_dir=loop_dir, runtime_dir=runtime_dir,
        events=FakeEvents(), tick=7, attempt=1,
    )


def make_contract(commands, screenshots):
    return contract_mod.Contract(
        task="T60", success_criteria=[], allow_list=[], forbidden=[], verification=[],
        render_gate=contract_mod.RenderGate(commands=commands, screenshots=screenshots),
        fidelity_source=[], evaluator_must_read=[], evaluator_must_view=[],
        estimated_diff_lines=0, scout_notes="", relevant_learnings=[],
    )


class RenderGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ctx = make_ctx(self.tmp)

    def test_screenshot_is_archived_and_an_artifact_event_is_emitted(self):
        c = make_contract(
            [WRITE_PNG % "orgunits.png"],
            [{"name": "orgunits-list", "path": "shots/orgunits.png"}],
        )
        results, shots = render_mod.run_render_gate(self.ctx, c)

        self.assertTrue(gate_mod.all_ok(results), [r.rc for r in results])
        dest = os.path.join(self.ctx.loop_dir, "artifacts", "T60", "orgunits-list.png")
        self.assertTrue(os.path.exists(dest), "screenshot not archived to %s" % dest)
        with open(dest, "rb") as fh:
            self.assertEqual(fh.read(len(PNG_MAGIC)), PNG_MAGIC)
        self.assertEqual(shots, [{"name": "orgunits-list", "path": dest}])
        self.assertFalse(os.path.exists(dest + ".tmp"), "atomic copy left its temp file")

        artifacts = [f for (t, f) in self.ctx.events.emitted if t == "artifact"]
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["name"], "orgunits-list")
        self.assertEqual(artifacts[0]["path"], dest)
        self.assertEqual(artifacts[0]["task"], "T60")
        self.assertEqual(artifacts[0]["tick"], 7)

    def test_render_output_lands_in_a_render_tagged_gate_file(self):
        c = make_contract([WRITE_PNG % "a.png"], [{"name": "a", "path": "shots/a.png"}])
        results, _ = render_mod.run_render_gate(self.ctx, c)
        self.assertIn("gate-render-T60-1.txt", results[0].output_path)

    def test_failing_render_command_fails_the_gate_and_skips_the_copy(self):
        c = make_contract(
            ["exit 3"],
            [{"name": "orgunits-list", "path": "shots/orgunits.png"}],
        )
        results, shots = render_mod.run_render_gate(self.ctx, c)
        self.assertFalse(gate_mod.all_ok(results))
        self.assertEqual(shots, [])

    def test_missing_screenshot_is_a_failure_with_a_clear_message(self):
        c = make_contract(["true"], [{"name": "orgunits-list", "path": "shots/orgunits.png"}])
        results, shots = render_mod.run_render_gate(self.ctx, c)

        self.assertFalse(gate_mod.all_ok(results))
        self.assertEqual(shots, [])
        failing = [r for r in results if r.rc != 0]
        self.assertEqual(len(failing), 1)
        self.assertEqual(failing[0].cmd, "screenshot:orgunits-list")
        with open(failing[0].output_path) as fh:
            body = fh.read()
        self.assertIn("orgunits-list", body)
        self.assertIn("shots/orgunits.png", body)
        self.assertIn("not found", body)

    def test_no_render_gate_is_a_no_op(self):
        c = make_contract([], [])
        c.render_gate = None
        self.assertEqual(render_mod.run_render_gate(self.ctx, c), ([], []))


class ReferenceScreenshotTest(unittest.TestCase):
    def test_reference_entries_resolve_against_the_worktree_and_skip_missing(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        ctx = make_ctx(tmp)
        os.makedirs(os.path.join(ctx.cfg.worktree, "docs", "reference"))
        present = os.path.join(ctx.cfg.worktree, "docs", "reference", "rise.png")
        with open(present, "wb") as fh:
            fh.write(PNG_MAGIC)
        ctx.cfg.render = {"reference": [
            {"name": "rise-customers", "path": "docs/reference/rise.png"},
            {"name": "gone", "path": "docs/reference/gone.png"},
        ]}
        refs = render_mod.reference_screenshots(ctx.cfg)
        self.assertEqual(
            refs,
            [{"name": "rise-customers", "path": present, "kind": "reference"}],
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest tests.runner.test_render -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'runner.render'`.

- [ ] **Step 3: Write `runner/render.py`**

Create the file with exactly this content:

```python
"""Harness-side visual verification: render gate, screenshot archival, fidelity.

Nothing in this module calls a model. The harness runs the commands, keeps the
images, and computes the ratios; the Evaluator is handed the results (spec 5.2).
"""
from __future__ import annotations

import json
import os
import shutil
import time
from typing import Dict, List, Optional, Tuple

from . import gate as gate_mod
from . import git_ops
from .config import phase_limit


def artifacts_dir(ctx) -> str:
    """`$LOOP_DIR/artifacts/<T>` — created on demand."""
    path = os.path.join(ctx.loop_dir, "artifacts", ctx.task.id)
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def _abs(worktree: str, path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(worktree, path)


def _write_failure(out_dir: str, tag: str, n: int, text: str) -> str:
    path = os.path.join(out_dir, "gate-%s-%d.txt" % (tag, n))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return path


def _copy_atomic(src: str, dst: str) -> None:
    tmp = dst + ".tmp"
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def run_render_gate(ctx, contract) -> Tuple[List, List[Dict]]:
    """Run the contract's render commands, then archive every declared screenshot.

    Returns (command results, [{"name","path"}]) where `path` is the archived
    absolute path. A declared screenshot the commands did not produce is a
    failure: a synthetic CommandResult with rc=1 joins the list so the caller's
    `gate.all_ok` sees it.
    """
    rg = getattr(contract, "render_gate", None)
    if rg is None:
        return [], []

    cfg = ctx.cfg
    tag = "render-%s" % ctx.task.id
    results = list(gate_mod.run_commands(
        list(rg.commands),
        cfg.worktree,
        phase_limit(cfg, "gate_cmd"),
        ctx.runtime_dir,
        tag,
    ))
    if not gate_mod.all_ok(results):
        return results, []

    dest_dir = artifacts_dir(ctx)
    shots = []  # type: List[Dict]
    n = len(results)
    for shot in rg.screenshots:
        name = shot.get("name", "")
        rel = shot.get("path", "")
        src = _abs(cfg.worktree, rel)
        n += 1
        if not name or not rel:
            results.append(gate_mod.CommandResult(
                cmd="screenshot:%s" % (name or "<unnamed>"), rc=1, duration_s=0,
                output_path=_write_failure(
                    ctx.runtime_dir, tag, n,
                    "render_gate.screenshots entry needs both a name and a path; got %r\n" % (shot,),
                ),
                timed_out=False,
            ))
            continue
        if not os.path.isfile(src):
            results.append(gate_mod.CommandResult(
                cmd="screenshot:%s" % name, rc=1, duration_s=0,
                output_path=_write_failure(
                    ctx.runtime_dir, tag, n,
                    "screenshot %r not found at %s (declared as %r).\n"
                    "The render commands ran and exited 0 but wrote no image there.\n"
                    "Fix the contract's screenshot path so it matches where the\n"
                    "render command actually writes, or fix the command.\n" % (name, src, rel),
                ),
                timed_out=False,
            ))
            continue
        dst = os.path.join(dest_dir, "%s.png" % name)
        _copy_atomic(src, dst)
        shots.append({"name": name, "path": dst})
        ctx.events.emit("artifact", tick=ctx.tick, task=ctx.task.id, name=name, path=dst)

    if not gate_mod.all_ok(results):
        return results, []
    return results, shots


def reference_screenshots(cfg) -> List[Dict]:
    """Reference images from the `Render:` recipe, absolute, existing only."""
    out = []  # type: List[Dict]
    for ref in (cfg.render or {}).get("reference", []):
        name = ref.get("name", "")
        path = _abs(cfg.worktree, ref.get("path", ""))
        if name and os.path.isfile(path):
            out.append({"name": name, "path": path, "kind": "reference"})
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd plugins/agent-loop && python3 -m unittest tests.runner.test_render -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/runner/render.py plugins/agent-loop/tests/runner/test_render.py
git commit -m "$(cat <<'MSG'
agent-loop: render gate — run the recipe, archive the screenshots

Runs contract.render_gate.commands through gate.run_commands (tag render-<T>),
copies each declared screenshot into $LOOP_DIR/artifacts/<T>/<name>.png
atomically, and emits one artifact event per image. A declared screenshot the
commands did not write is a hard failure with the path in the message — a green
render command that produced no image is exactly how a UI task passes unseen.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
MSG
)"
```

---

### Task 3: Render app lifecycle — `start:`, `ready:`, `runtime/render-app.pid`

**Files:**
- Modify: `plugins/agent-loop/runner/render.py`
- Test: `plugins/agent-loop/tests/runner/test_render_app.py`

**Interfaces:**
- Consumes: `cfg.render["start"]`, `cfg.render["ready"]`.
- Produces:
  - `render.RenderAppError(Exception)`
  - `render.ensure_app(cfg, runtime_dir, events=None, ready_timeout_s=120, poll_s=2.0) -> Optional[int]` — returns the pid, or `None` when the recipe has no `start:`. Idempotent: a live pid in `runtime/render-app.pid` is reused, so the app starts once per harness.
  - `render.stop_app(runtime_dir) -> None` — SIGTERM the process group, 10 s grace, SIGKILL, unlink the pid file. Safe to call when nothing is running.
- Spec: §5.3 ("how to start the app if needed"); ready polling capped at 120 s.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_render_app.py`:

```python
"""Render app lifecycle (plan B task 3)."""
from __future__ import annotations

import os
import shutil
import socket
import sys
import tempfile
import time
import types
import unittest

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from runner import render as render_mod  # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class RenderAppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.runtime = os.path.join(self.tmp, "runtime")
        os.makedirs(self.runtime)
        self.worktree = os.path.join(self.tmp, "wt")
        os.makedirs(self.worktree)
        self.addCleanup(render_mod.stop_app, self.runtime)

    def cfg(self, render):
        return types.SimpleNamespace(worktree=self.worktree, render=render, limits={})

    def test_no_start_command_is_a_no_op(self):
        self.assertIsNone(render_mod.ensure_app(self.cfg({}), self.runtime))
        self.assertFalse(os.path.exists(os.path.join(self.runtime, "render-app.pid")))

    def test_start_backgrounds_the_app_records_the_pid_and_waits_for_ready(self):
        port = free_port()
        cfg = self.cfg({
            "start": "python3 -m http.server %d --bind 127.0.0.1" % port,
            "ready": "http://127.0.0.1:%d/" % port,
        })
        pid = render_mod.ensure_app(cfg, self.runtime, ready_timeout_s=30, poll_s=0.2)
        self.assertIsNotNone(pid)
        self.assertTrue(alive(pid))
        with open(os.path.join(self.runtime, "render-app.pid")) as fh:
            self.assertEqual(int(fh.read().strip()), pid)

    def test_second_call_reuses_the_running_app(self):
        port = free_port()
        cfg = self.cfg({
            "start": "python3 -m http.server %d --bind 127.0.0.1" % port,
            "ready": "http://127.0.0.1:%d/" % port,
        })
        first = render_mod.ensure_app(cfg, self.runtime, ready_timeout_s=30, poll_s=0.2)
        second = render_mod.ensure_app(cfg, self.runtime, ready_timeout_s=30, poll_s=0.2)
        self.assertEqual(first, second)

    def test_stop_app_kills_it_and_removes_the_pid_file(self):
        port = free_port()
        cfg = self.cfg({"start": "python3 -m http.server %d --bind 127.0.0.1" % port})
        pid = render_mod.ensure_app(cfg, self.runtime)
        render_mod.stop_app(self.runtime)
        deadline = time.time() + 10
        while alive(pid) and time.time() < deadline:
            time.sleep(0.1)
        self.assertFalse(alive(pid))
        self.assertFalse(os.path.exists(os.path.join(self.runtime, "render-app.pid")))

    def test_ready_url_that_never_answers_raises_with_the_url_in_the_message(self):
        port = free_port()  # nothing listens here
        cfg = self.cfg({
            "start": "sleep 60",
            "ready": "http://127.0.0.1:%d/" % port,
        })
        with self.assertRaises(render_mod.RenderAppError) as caught:
            render_mod.ensure_app(cfg, self.runtime, ready_timeout_s=2, poll_s=0.2)
        self.assertIn(str(port), str(caught.exception))

    def test_default_ready_timeout_is_120_seconds(self):
        self.assertEqual(render_mod.READY_TIMEOUT_S, 120)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest tests.runner.test_render_app -v`
Expected: FAIL — `AttributeError: module 'runner.render' has no attribute 'ensure_app'`.

- [ ] **Step 3: Add the lifecycle to `runner/render.py`**

Add `import signal`, `import subprocess`, `import urllib.error`, `import urllib.request` to the imports, and append after `reference_screenshots`:

```python
READY_TIMEOUT_S = 120
READY_POLL_S = 2.0
STOP_GRACE_S = 10


class RenderAppError(Exception):
    """The render recipe's app would not start or would not become ready."""


def _pid_path(runtime_dir: str) -> str:
    return os.path.join(runtime_dir, "render-app.pid")


def _read_pid(runtime_dir: str) -> Optional[int]:
    try:
        with open(_pid_path(runtime_dir)) as fh:
            return int(fh.read().strip())
    except (IOError, OSError, ValueError):
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _wait_ready(url: str, timeout_s: int, poll_s: float) -> None:
    deadline = time.time() + timeout_s
    last = "no attempt made"
    while time.time() < deadline:
        try:
            resp = urllib.request.urlopen(url, timeout=5)
            code = resp.getcode()
            resp.close()
            if code == 200:
                return
            last = "HTTP %s" % code
        except urllib.error.HTTPError as exc:
            last = "HTTP %s" % exc.code
        except Exception as exc:  # URLError, socket.timeout, ConnectionRefused
            last = "%s: %s" % (type(exc).__name__, exc)
        time.sleep(poll_s)
    raise RenderAppError(
        "render app never became ready: %s did not return 200 within %ds (last: %s)"
        % (url, timeout_s, last)
    )


def ensure_app(cfg, runtime_dir: str, events=None,
               ready_timeout_s: int = READY_TIMEOUT_S,
               poll_s: float = READY_POLL_S) -> Optional[int]:
    """Start the recipe's app once per harness; return its pid (None if no recipe)."""
    start = (cfg.render or {}).get("start", "")
    if not start:
        return None

    pid = _read_pid(runtime_dir)
    if pid is not None and _alive(pid):
        ready = (cfg.render or {}).get("ready", "")
        if ready:
            _wait_ready(ready, ready_timeout_s, poll_s)
        return pid

    log_path = os.path.join(runtime_dir, "render-app.log")
    log = open(log_path, "ab")
    proc = subprocess.Popen(
        start, shell=True, cwd=cfg.worktree,
        stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    log.close()
    tmp = _pid_path(runtime_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("%d\n" % proc.pid)
    os.replace(tmp, _pid_path(runtime_dir))
    if events is not None:
        events.emit("render_app_start", pid=proc.pid, cmd=start)

    ready = (cfg.render or {}).get("ready", "")
    if ready:
        try:
            _wait_ready(ready, ready_timeout_s, poll_s)
        except RenderAppError:
            stop_app(runtime_dir)
            raise
    return proc.pid


def stop_app(runtime_dir: str) -> None:
    """Kill the backgrounded render app and drop its pid file. Never raises."""
    pid = _read_pid(runtime_dir)
    if pid is None:
        return
    for sig, wait in ((signal.SIGTERM, STOP_GRACE_S), (signal.SIGKILL, 0)):
        if not _alive(pid):
            break
        try:
            os.killpg(os.getpgid(pid), sig)
        except OSError:
            try:
                os.kill(pid, sig)
            except OSError:
                break
        deadline = time.time() + wait
        while wait and _alive(pid) and time.time() < deadline:
            time.sleep(0.1)
    try:
        os.unlink(_pid_path(runtime_dir))
    except OSError:
        pass
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd plugins/agent-loop && python3 -m unittest tests.runner.test_render_app -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/runner/render.py plugins/agent-loop/tests/runner/test_render_app.py
git commit -m "$(cat <<'MSG'
agent-loop: start the render app once per harness, kill it at exit

`start:` is backgrounded in its own session with the pid in
runtime/render-app.pid, `ready:` is polled for HTTP 200 for at most 120s, and
stop_app SIGTERMs the process group with a 10s grace. Idempotent, so every tick
after the first reuses the running app.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
MSG
)"
```

---

### Task 4: Fidelity check

**Files:**
- Modify: `plugins/agent-loop/runner/render.py`
- Test: `plugins/agent-loop/tests/runner/test_fidelity.py`

**Interfaces:**
- Consumes: `git_ops.similarity(src_path, dst_path) -> float` (difflib ratio over normalized lines: strip, drop blank, sort import lines); `contract.FidelitySource(src, dst, min_similarity)`.
- Produces: `render.run_fidelity(ctx, contract) -> List[Dict]` — one `{"src","dst","ratio","min","ok"}` per pair, and `$LOOP_DIR/runtime/fidelity-<T>.json` = `{"task","t","checks":[…]}`.
- A missing `dst` scores `0.0` and `ok: False`; a missing `src` is the same, with the reason carried in the JSON file (`"error"` key).

**Why:** `w5` §5 — the R3 "copy" was `Header.tsx +6` lines of `TODO(rise-regression): copied from …`, the Evaluator cited that comment as evidence the file was copied, and `LOOP_LEARNINGS.md:238` recorded "frontend code genuinely copied (not rewritten)". A ratio gate is the only cheap thing that catches this.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_fidelity.py`:

```python
"""Fidelity ratios and thresholds (plan B task 4)."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import types
import unittest

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from runner import contract as contract_mod  # noqa: E402
from runner import git_ops  # noqa: E402
from runner import render as render_mod  # noqa: E402

REFERENCE = """import { Icon } from '@repo/ui';
import { Text } from '@repo/ui';

export interface MenuItem {
  label: string;
  icon: IconName;
  to: string;
  gate?: Gate;
}

export function AppNavigationItem({ item }: { item: MenuItem }) {
  return (
    <Link to={item.to} className="flex items-center gap-2 px-3 py-2">
      <Icon name={item.icon} size="md" />
      <Text className="xl:hidden">{item.label}</Text>
    </Link>
  );
}
"""

# The real thing: same file, ported, with the import path swapped and one class changed.
PORTED = REFERENCE.replace("@repo/ui", "@internal/ui").replace("gap-2", "gap-3")

# What the 2026-09-11 run actually shipped for the same task (w5 s5).
COMMENT_ONLY = """// TODO(rise-regression): copied from apps/frontend/src/layouts/Header/Header.tsx
// Keep in sync with the Rise implementation.
// See the parity matrix for the expected props.
// This file intentionally reimplements the shape.
// Regression note: icons are deferred.
// Regression note: rail width is deferred.
"""


def write(dirpath, name, body):
    path = os.path.join(dirpath, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def make_ctx(tmp):
    worktree = os.path.join(tmp, "wt")
    loop_dir = os.path.join(worktree, ".claude", "loop", "run")
    runtime_dir = os.path.join(loop_dir, "runtime")
    os.makedirs(runtime_dir)
    cfg = types.SimpleNamespace(worktree=worktree, render={}, limits={})
    task = types.SimpleNamespace(id="R3", no_ui=False, copy_of="T8")
    return types.SimpleNamespace(
        cfg=cfg, task=task, loop_dir=loop_dir, runtime_dir=runtime_dir,
        events=types.SimpleNamespace(emit=lambda *a, **k: None), tick=42, attempt=1,
    )


def contract_with(pairs):
    return contract_mod.Contract(
        task="R3", success_criteria=[], allow_list=[], forbidden=[], verification=[],
        render_gate=None,
        fidelity_source=[contract_mod.FidelitySource(src=s, dst=d, min_similarity=m)
                         for (s, d, m) in pairs],
        evaluator_must_read=[], evaluator_must_view=[],
        estimated_diff_lines=0, scout_notes="", relevant_learnings=[],
    )


class SimilarityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_real_port_scores_at_least_0_9(self):
        a = write(self.tmp, "ref.tsx", REFERENCE)
        b = write(self.tmp, "ported.tsx", PORTED)
        self.assertGreaterEqual(git_ops.similarity(a, b), 0.9)

    def test_a_six_line_comment_scores_under_0_2(self):
        a = write(self.tmp, "ref.tsx", REFERENCE)
        b = write(self.tmp, "fake.tsx", COMMENT_ONLY)
        self.assertLess(git_ops.similarity(a, b), 0.2)


class RunFidelityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ctx = make_ctx(self.tmp)
        os.makedirs(os.path.join(self.ctx.cfg.worktree, "src"))

    def place(self, name, body):
        return write(os.path.join(self.ctx.cfg.worktree, "src"), name, body)

    def test_a_real_port_passes_its_threshold(self):
        self.place("ref.tsx", REFERENCE)
        self.place("dst.tsx", PORTED)
        checks = render_mod.run_fidelity(self.ctx, contract_with([("src/ref.tsx", "src/dst.tsx", 0.6)]))
        self.assertEqual(len(checks), 1)
        self.assertTrue(checks[0]["ok"])
        self.assertEqual(checks[0]["src"], "src/ref.tsx")
        self.assertEqual(checks[0]["dst"], "src/dst.tsx")
        self.assertEqual(checks[0]["min"], 0.6)
        self.assertGreaterEqual(checks[0]["ratio"], 0.9)

    def test_the_comment_only_copy_fails_its_threshold(self):
        self.place("ref.tsx", REFERENCE)
        self.place("dst.tsx", COMMENT_ONLY)
        checks = render_mod.run_fidelity(self.ctx, contract_with([("src/ref.tsx", "src/dst.tsx", 0.6)]))
        self.assertFalse(checks[0]["ok"])
        self.assertLess(checks[0]["ratio"], 0.2)

    def test_a_missing_destination_scores_zero_and_fails(self):
        self.place("ref.tsx", REFERENCE)
        checks = render_mod.run_fidelity(self.ctx, contract_with([("src/ref.tsx", "src/gone.tsx", 0.6)]))
        self.assertEqual(checks[0]["ratio"], 0.0)
        self.assertFalse(checks[0]["ok"])

    def test_results_are_written_to_runtime_fidelity_json(self):
        self.place("ref.tsx", REFERENCE)
        self.place("dst.tsx", PORTED)
        render_mod.run_fidelity(self.ctx, contract_with([("src/ref.tsx", "src/dst.tsx", 0.6)]))
        path = os.path.join(self.ctx.runtime_dir, "fidelity-R3.json")
        self.assertTrue(os.path.exists(path))
        with open(path) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["task"], "R3")
        self.assertEqual(len(doc["checks"]), 1)
        self.assertTrue(doc["checks"][0]["ok"])
        self.assertFalse(os.path.exists(path + ".tmp"))

    def test_no_fidelity_source_writes_nothing(self):
        checks = render_mod.run_fidelity(self.ctx, contract_with([]))
        self.assertEqual(checks, [])
        self.assertFalse(os.path.exists(os.path.join(self.ctx.runtime_dir, "fidelity-R3.json")))

    def test_fidelity_text_names_the_failing_pair_and_both_numbers(self):
        self.place("ref.tsx", REFERENCE)
        self.place("dst.tsx", COMMENT_ONLY)
        checks = render_mod.run_fidelity(self.ctx, contract_with([("src/ref.tsx", "src/dst.tsx", 0.6)]))
        text = render_mod.fidelity_text(checks)
        self.assertIn("src/ref.tsx", text)
        self.assertIn("src/dst.tsx", text)
        self.assertIn("0.6", text)
        self.assertIn("FAIL", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest tests.runner.test_fidelity -v`
Expected: FAIL — `AttributeError: module 'runner.render' has no attribute 'run_fidelity'`. (The two `SimilarityTest` cases should already pass against plan A's `git_ops.similarity`; if they do not, fix `git_ops.similarity` — the normalization is strip, drop blank lines, sort `import`/`from` lines — before going on.)

- [ ] **Step 3: Add `run_fidelity` and `fidelity_text` to `runner/render.py`**

Append to `runner/render.py`:

```python
def run_fidelity(ctx, contract) -> List[Dict]:
    """Line-similarity ratio for every declared copy/port pair (spec 5.4)."""
    pairs = list(getattr(contract, "fidelity_source", []) or [])
    if not pairs:
        return []

    checks = []  # type: List[Dict]
    for fs in pairs:
        src = _abs(ctx.cfg.worktree, fs.src)
        dst = _abs(ctx.cfg.worktree, fs.dst)
        entry = {"src": fs.src, "dst": fs.dst, "min": fs.min_similarity}
        if not os.path.isfile(src):
            entry["ratio"] = 0.0
            entry["error"] = "reference file not found: %s" % src
        elif not os.path.isfile(dst):
            entry["ratio"] = 0.0
            entry["error"] = "target file not found: %s" % dst
        else:
            entry["ratio"] = round(git_ops.similarity(src, dst), 4)
        entry["ok"] = entry["ratio"] >= fs.min_similarity
        checks.append(entry)

    path = os.path.join(ctx.runtime_dir, "fidelity-%s.json" % ctx.task.id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"task": ctx.task.id, "t": int(time.time()), "checks": checks}, fh, indent=2)
    os.replace(tmp, path)
    return checks


def fidelity_text(checks: List[Dict]) -> str:
    """Human/model-readable summary; goes into the gate outputs on failure."""
    if not checks:
        return ""
    lines = ["## Fidelity (line similarity vs the reference)"]
    for c in checks:
        lines.append(
            "%s  %s -> %s  ratio=%.2f  min=%.2f%s"
            % ("PASS" if c["ok"] else "FAIL", c["src"], c["dst"],
               c["ratio"], c["min"],
               "  (%s)" % c["error"] if c.get("error") else "")
        )
    if any(not c["ok"] for c in checks):
        lines.append(
            "A copy/port task whose target barely resembles its reference is not a copy. "
            "Port the reference file's structure, not a note that says you did."
        )
    return "\n".join(lines)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd plugins/agent-loop && python3 -m unittest tests.runner.test_fidelity -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add plugins/agent-loop/runner/render.py plugins/agent-loop/tests/runner/test_fidelity.py
git commit -m "$(cat <<'MSG'
agent-loop: fidelity ratio for every declared copy/port pair

difflib similarity per fidelity_source entry, written to
runtime/fidelity-<T>.json and rendered into the gate outputs. The regression
test is the real one: the six-line TODO(rise-regression) comment that shipped
as a "copy" of Header.tsx scores under 0.2 and hard-fails.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
MSG
)"
```

---

### Task 5: `run_visual_checks` and the main-loop wiring

**Files:**
- Modify: `plugins/agent-loop/runner/render.py`
- Modify: `plugins/agent-loop/runner/run.py`
- Test: `plugins/agent-loop/tests/runner/test_visual_checks.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass
  class VisualResult:
      ok: bool
      render_results: List      # gate.CommandResult
      screenshots: List[Dict]   # new, archived; kind defaults to "new"
      references: List[Dict]    # {"name","path","kind":"reference"}
      fidelity: List[Dict]
      failure_text: str         # "" when ok

  def run_visual_checks(ctx, contract) -> VisualResult
  def evaluator_screenshots(vis: VisualResult) -> List[Dict]   # screenshots + references
  ```
- Consumed by `run.py`: RENDER runs only when `contract.render_gate` is set, FIDELITY only when `contract.fidelity_source` is non-empty; either failing is a gate failure that takes the existing failure path with `failure_text` appended to the gate outputs.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_visual_checks.py`:

```python
"""run_visual_checks composition and run.py wiring (plan B task 5)."""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import types
import unittest

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from runner import contract as contract_mod  # noqa: E402
from runner import render as render_mod  # noqa: E402

WRITE_PNG = "mkdir -p shots && printf '\\211PNG\\r\\n\\032\\n' > shots/a.png"
SRC = "".join("line %d\n" % n for n in range(40))


class FakeEvents(object):
    def __init__(self):
        self.emitted = []

    def emit(self, type, **fields):
        self.emitted.append((type, fields))


def make_ctx(tmp, render=None):
    worktree = os.path.join(tmp, "wt")
    loop_dir = os.path.join(worktree, ".claude", "loop", "run")
    runtime_dir = os.path.join(loop_dir, "runtime")
    os.makedirs(runtime_dir)
    cfg = types.SimpleNamespace(worktree=worktree, render=render or {}, limits={"gate_cmd_timeout": 60})
    task = types.SimpleNamespace(id="T60", no_ui=False, copy_of=None)
    return types.SimpleNamespace(
        cfg=cfg, task=task, loop_dir=loop_dir, runtime_dir=runtime_dir,
        events=FakeEvents(), tick=1, attempt=1,
    )


def make_contract(render_gate=None, fidelity=None):
    return contract_mod.Contract(
        task="T60", success_criteria=[], allow_list=[], forbidden=[], verification=[],
        render_gate=render_gate, fidelity_source=fidelity or [],
        evaluator_must_read=[], evaluator_must_view=[],
        estimated_diff_lines=0, scout_notes="", relevant_learnings=[],
    )


class VisualChecksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_no_render_gate_and_no_fidelity_is_ok_and_does_nothing(self):
        ctx = make_ctx(self.tmp)
        vis = render_mod.run_visual_checks(ctx, make_contract())
        self.assertTrue(vis.ok)
        self.assertEqual(vis.render_results, [])
        self.assertEqual(vis.screenshots, [])
        self.assertEqual(vis.fidelity, [])
        self.assertEqual(vis.failure_text, "")

    def test_green_render_yields_screenshots_and_references(self):
        ctx = make_ctx(self.tmp, render={"reference": [{"name": "rise", "path": "docs/rise.png"}]})
        os.makedirs(os.path.join(ctx.cfg.worktree, "docs"))
        with open(os.path.join(ctx.cfg.worktree, "docs", "rise.png"), "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")
        c = make_contract(contract_mod.RenderGate(
            commands=[WRITE_PNG], screenshots=[{"name": "a", "path": "shots/a.png"}]))
        vis = render_mod.run_visual_checks(ctx, c)
        self.assertTrue(vis.ok, vis.failure_text)
        self.assertEqual([s["name"] for s in vis.screenshots], ["a"])
        self.assertEqual([r["name"] for r in vis.references], ["rise"])
        names = [s["name"] for s in render_mod.evaluator_screenshots(vis)]
        self.assertEqual(names, ["a", "rise"])

    def test_a_failing_render_makes_the_result_not_ok_with_the_output_path_named(self):
        ctx = make_ctx(self.tmp)
        c = make_contract(contract_mod.RenderGate(
            commands=["echo boom >&2; exit 3"], screenshots=[{"name": "a", "path": "shots/a.png"}]))
        vis = render_mod.run_visual_checks(ctx, c)
        self.assertFalse(vis.ok)
        self.assertIn("render", vis.failure_text)
        self.assertIn("gate-render-T60-1.txt", vis.failure_text)

    def test_a_failing_fidelity_pair_makes_the_result_not_ok(self):
        ctx = make_ctx(self.tmp)
        os.makedirs(os.path.join(ctx.cfg.worktree, "src"))
        with open(os.path.join(ctx.cfg.worktree, "src", "ref.tsx"), "w") as fh:
            fh.write(SRC)
        with open(os.path.join(ctx.cfg.worktree, "src", "dst.tsx"), "w") as fh:
            fh.write("// copied from src/ref.tsx\n")
        c = make_contract(fidelity=[contract_mod.FidelitySource(
            src="src/ref.tsx", dst="src/dst.tsx", min_similarity=0.6)])
        vis = render_mod.run_visual_checks(ctx, c)
        self.assertFalse(vis.ok)
        self.assertIn("FAIL", vis.failure_text)
        self.assertIn("src/dst.tsx", vis.failure_text)


class RunWiringTest(unittest.TestCase):
    """run.py must call the checks between the gate and the evaluator."""

    def setUp(self):
        with open(os.path.join(PLUGIN_ROOT, "runner", "run.py")) as fh:
            self.src = fh.read()

    def test_run_imports_render_and_calls_the_three_entry_points(self):
        self.assertRegex(self.src, r"from \. import render|import render")
        self.assertIn("run_visual_checks(", self.src)
        self.assertIn("stop_app(", self.src)

    def test_visual_checks_sit_between_the_gate_and_the_evaluator(self):
        gate_at = self.src.index("run_commands(")
        visual_at = self.src.index("run_visual_checks(")
        evaluator_at = self.src.index("run_evaluator(")
        self.assertLess(gate_at, visual_at)
        self.assertLess(visual_at, evaluator_at)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest tests.runner.test_visual_checks -v`
Expected: FAIL — `AttributeError: module 'runner.render' has no attribute 'run_visual_checks'`.

- [ ] **Step 3: Add `VisualResult`, `run_visual_checks`, `evaluator_screenshots`**

Add `from dataclasses import dataclass, field` to `runner/render.py`'s imports and append:

```python
@dataclass
class VisualResult:
    ok: bool = True
    render_results: List = field(default_factory=list)
    screenshots: List[Dict] = field(default_factory=list)
    references: List[Dict] = field(default_factory=list)
    fidelity: List[Dict] = field(default_factory=list)
    failure_text: str = ""


def run_visual_checks(ctx, contract) -> VisualResult:
    """RENDER then FIDELITY. Either failing is a gate failure (spec 3, 5.3, 5.4)."""
    vis = VisualResult()
    problems = []  # type: List[str]

    if getattr(contract, "render_gate", None) is not None:
        try:
            ensure_app(ctx.cfg, ctx.runtime_dir, events=ctx.events)
        except RenderAppError as exc:
            vis.ok = False
            vis.failure_text = "## Render\n%s" % exc
            return vis
        vis.render_results, vis.screenshots = run_render_gate(ctx, contract)
        failed = [r for r in vis.render_results if r.rc != 0]
        if failed:
            vis.ok = False
            problems.append(
                "## Render gate FAILED\n"
                + "\n".join("%s (rc=%s%s) -> %s"
                            % (r.cmd, r.rc, ", timed out" if r.timed_out else "", r.output_path)
                            for r in failed)
            )
        vis.references = reference_screenshots(ctx.cfg)

    vis.fidelity = run_fidelity(ctx, contract)
    if any(not c["ok"] for c in vis.fidelity):
        vis.ok = False
        problems.append(fidelity_text(vis.fidelity))

    vis.failure_text = "\n\n".join(problems)
    return vis


def evaluator_screenshots(vis: VisualResult) -> List[Dict]:
    """New images first, then the recipe's reference images."""
    return list(vis.screenshots) + list(vis.references)
```

- [ ] **Step 4: Wire it into `run.py`**

In `run.py`'s execute path, between plan A's GATE block (the `gate.run_commands(...)` call producing `gate_results` / `gate_outputs`) and the `phases.run_evaluator(...)` call, insert:

```python
        # RENDER + FIDELITY (plan B). Either failing is a gate failure.
        vis = render.run_visual_checks(ctx, c)
        gate_outputs = gate_outputs + ([vis.failure_text] if vis.failure_text else [])
        if not gate.all_ok(gate_results) or not vis.ok:
            return self._failure_path(ctx, c, gate_results + vis.render_results, gate_outputs)
```

and change the evaluator dispatch's `screenshots` argument to:

```python
        phase_result, verdict = phases.run_evaluator(
            ctx, c, diff_text, gate_outputs, render.evaluator_screenshots(vis)
        )
```

Add `from . import render` to `run.py`'s imports. In the harness shutdown path (the `finally:` that releases the lock and stops the sidecar), add:

```python
        render.stop_app(self.runtime_dir)
```

`_failure_path` is plan A's name for the "hand this tick to the Judge / defer" branch; if plan A calls it something else, call that instead — the requirement is that a render or fidelity failure takes the *same* path a failing verification command takes, with the outputs attached.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd plugins/agent-loop && python3 -m unittest tests.runner.test_visual_checks -v`
Expected: PASS, 6 tests.

- [ ] **Step 6: Run the whole runner suite**

Run: `cd plugins/agent-loop && python3 -m unittest discover -s tests/runner -v`
Expected: PASS (plan A's tests plus tasks 1–5).

- [ ] **Step 7: Commit**

```bash
git add plugins/agent-loop/runner/render.py plugins/agent-loop/runner/run.py \
        plugins/agent-loop/tests/runner/test_visual_checks.py
git commit -m "$(cat <<'MSG'
agent-loop: run RENDER and FIDELITY between GATE and EVALUATE

run_visual_checks starts the app, runs the render gate, archives the images and
scores the copy pairs; a failure in either goes down the same path a failing
verification command does, with the render output paths and the ratio table
attached. The Evaluator is then handed the new images plus the recipe's
reference images.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
MSG
)"
```

---

### Task 6: Evaluator must-read / must-view inputs and the single re-ask

**Files:**
- Modify: `plugins/agent-loop/runner/phases.py`
- Test: `plugins/agent-loop/tests/runner/test_phases_evaluator.py`

**Interfaces:**
- Consumes: `phases.render_prompt`, `phases.parse_json_block`, `claude_proc.run_phase`, `claude_proc.PhaseResult`, `config.model_for`, `config.phase_limit`; `runner/prompts/evaluator.md` already carries `{{must_read_blocks}}` and `{{screenshots}}`.
- Produces (unchanged signature, new behaviour): `phases.run_evaluator(ctx, contract, diff_text, gate_outputs, screenshots) -> Tuple[PhaseResult, Dict]`, plus module-level `MUST_READ_MAX_LINES = 400`, `_must_read_blocks`, `_screenshots_block`, `_missing_views`, `_merge_usage`.
- Returned `PhaseResult` carries the **summed** usage of both attempts (a re-ask must not lose the first attempt's cost — spec §4.1).
- A verdict still missing a required view after the re-ask is returned as `{"verdict": "NEEDS_WORK", "reason": "missing-views", …}`, which takes the failure path.

**Why:** `w5` §3/§4/§5 — the T8 Evaluator (opus, `| complex`) made zero Reads of the reference tree and passed a nav with no icons; the R3 Evaluator cited a `TODO` comment as evidence a file was copied. Both were *asked* to check and both were trusted to fetch their own evidence.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_phases_evaluator.py`:

```python
"""Evaluator must-read / must-view inputs and the one re-ask (plan B task 6)."""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
import types
import unittest

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from runner import claude_proc  # noqa: E402
from runner import contract as contract_mod  # noqa: E402
from runner import phases  # noqa: E402


def verdict_json(views):
    return "Here is my verdict.\n\n```json\n%s\n```\n" % json.dumps({
        "verdict": "PASS",
        "findings": [{"criterion": "renders", "met": True, "evidence": "saw it"}],
        "views": views,
        "summary": "looks right",
    })


class FakeEvents(object):
    def emit(self, type, **fields):
        pass


def make_ctx(tmp):
    worktree = os.path.join(tmp, "wt")
    runtime_dir = os.path.join(worktree, "loop", "runtime")
    os.makedirs(runtime_dir)
    cfg = types.SimpleNamespace(
        worktree=worktree,
        render={},
        tiers={"cheap": "haiku", "standard": "sonnet", "most-capable": "opus"},
        role_tiers={"evaluator": ""},
        limits={"eval_timeout": 480},
    )
    task = types.SimpleNamespace(
        id="T8", class_flag=None, no_ui=False, copy_of=None,
        raw="- [ ] T8 mirror the layouts",
    )
    return types.SimpleNamespace(
        cfg=cfg, plan=None, task=task, loop_dir=os.path.join(worktree, "loop"),
        runtime_dir=runtime_dir, events=FakeEvents(), tick=9, attempt=1,
    )


def make_contract(must_read, must_view):
    return contract_mod.Contract(
        task="T8", success_criteria=["nav shows icons"], allow_list=[], forbidden=[],
        verification=[], render_gate=None, fidelity_source=[],
        evaluator_must_read=must_read, evaluator_must_view=must_view,
        estimated_diff_lines=0, scout_notes="", relevant_learnings=[],
    )


def fake_result(text, cost=1.0):
    return claude_proc.PhaseResult(
        phase="evaluator", model="sonnet", rc=0, killed=False, timed_out=False,
        session_id="sess", transcript_path=None, started=0, ended=1,
        result_text=text,
        usage_by_model={"sonnet": claude_proc.Usage(
            cost_usd=cost, input_tokens=10, output_tokens=5,
            cache_read_tokens=0, cache_creation_tokens=0)},
        tool_calls=3, last_activity=1,
    )


class Recorder(object):
    """Stands in for claude_proc.run_phase; records prompts, replays canned texts."""

    def __init__(self, texts):
        self.texts = list(texts)
        self.prompts = []

    def __call__(self, **kwargs):
        self.prompts.append(kwargs["prompt"])
        return fake_result(self.texts.pop(0))


class EvaluatorInputTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ctx = make_ctx(self.tmp)
        os.makedirs(os.path.join(self.ctx.cfg.worktree, "ref"))
        self.real_run_phase = phases.run_phase

        def restore():
            phases.run_phase = self.real_run_phase

        self.addCleanup(restore)

    def write_ref(self, name, body):
        path = os.path.join(self.ctx.cfg.worktree, "ref", name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        return path

    def test_must_read_file_contents_reach_the_prompt(self):
        self.write_ref("Nav.tsx", "export const RAIL_WIDTH = 80;\n<Icon name={item.icon} />\n")
        rec = Recorder([verdict_json([{"name": "nav", "observation": "icon rail present"}])])
        phases.run_phase = rec
        c = make_contract(["ref/Nav.tsx"], ["nav"])
        result, verdict = phases.run_evaluator(
            self.ctx, c, "diff", ["gate ok"],
            [{"name": "nav", "path": "/loop/artifacts/T8/nav.png"}],
        )
        self.assertEqual(verdict["verdict"], "PASS")
        self.assertEqual(len(rec.prompts), 1)
        self.assertIn("RAIL_WIDTH = 80", rec.prompts[0])
        self.assertIn("ref/Nav.tsx", rec.prompts[0])

    def test_long_must_read_file_is_capped_at_400_lines_with_a_marker(self):
        self.write_ref("Big.tsx", "".join("const l%d = %d;\n" % (n, n) for n in range(500)))
        rec = Recorder([verdict_json([])])
        phases.run_phase = rec
        c = make_contract(["ref/Big.tsx"], [])
        phases.run_evaluator(self.ctx, c, "diff", [], [])
        prompt = rec.prompts[0]
        self.assertIn("const l399 = 399;", prompt)
        self.assertNotIn("const l400 = 400;", prompt)
        self.assertIn("[truncated]", prompt)
        self.assertEqual(phases.MUST_READ_MAX_LINES, 400)

    def test_a_missing_must_read_file_is_reported_not_crashed(self):
        rec = Recorder([verdict_json([])])
        phases.run_phase = rec
        c = make_contract(["ref/Gone.tsx"], [])
        phases.run_evaluator(self.ctx, c, "diff", [], [])
        self.assertIn("ref/Gone.tsx", rec.prompts[0])
        self.assertIn("missing", rec.prompts[0])

    def test_screenshot_paths_and_the_read_instruction_reach_the_prompt(self):
        rec = Recorder([verdict_json([
            {"name": "nav", "observation": "icons"},
            {"name": "rise-nav", "observation": "icons"},
        ])])
        phases.run_phase = rec
        c = make_contract([], ["nav"])
        phases.run_evaluator(
            self.ctx, c, "diff", [],
            [{"name": "nav", "path": "/a/nav.png"},
             {"name": "rise-nav", "path": "/b/rise.png", "kind": "reference"}],
        )
        prompt = rec.prompts[0]
        self.assertIn("/a/nav.png", prompt)
        self.assertIn("/b/rise.png", prompt)
        self.assertIn("reference", prompt)
        self.assertIn("Read", prompt)
        self.assertIn("views[]", prompt)

    def test_a_verdict_missing_a_required_view_is_re_asked_once_then_accepted(self):
        rec = Recorder([
            verdict_json([]),
            verdict_json([{"name": "nav", "observation": "80px rail, icons, no labels"}]),
        ])
        phases.run_phase = rec
        c = make_contract([], ["nav"])
        result, verdict = phases.run_evaluator(
            self.ctx, c, "diff", [], [{"name": "nav", "path": "/a/nav.png"}])
        self.assertEqual(len(rec.prompts), 2)
        self.assertIn("nav", rec.prompts[1])
        self.assertIn("rejected", rec.prompts[1].lower())
        self.assertEqual(verdict["verdict"], "PASS")

    def test_two_misses_become_needs_work_with_reason_missing_views(self):
        rec = Recorder([verdict_json([]), verdict_json([])])
        phases.run_phase = rec
        c = make_contract([], ["nav", "header"])
        result, verdict = phases.run_evaluator(
            self.ctx, c, "diff", [], [{"name": "nav", "path": "/a/nav.png"}])
        self.assertEqual(len(rec.prompts), 2)
        self.assertEqual(verdict["verdict"], "NEEDS_WORK")
        self.assertEqual(verdict["reason"], "missing-views")
        self.assertIn("nav", verdict["summary"])
        self.assertIn("header", verdict["summary"])

    def test_usage_from_both_attempts_is_summed_onto_the_returned_result(self):
        rec = Recorder([verdict_json([]), verdict_json([{"name": "nav", "observation": "ok"}])])
        phases.run_phase = rec
        c = make_contract([], ["nav"])
        result, _ = phases.run_evaluator(
            self.ctx, c, "diff", [], [{"name": "nav", "path": "/a/nav.png"}])
        self.assertAlmostEqual(result.usage_by_model["sonnet"].cost_usd, 2.0)
        self.assertEqual(result.tool_calls, 6)

    def test_malformed_json_is_re_asked_once_then_reported(self):
        rec = Recorder(["no json here at all", "still no json"])
        phases.run_phase = rec
        c = make_contract([], [])
        result, verdict = phases.run_evaluator(self.ctx, c, "diff", [], [])
        self.assertEqual(len(rec.prompts), 2)
        self.assertEqual(verdict["reason"], "malformed-output")
        self.assertEqual(verdict["verdict"], "NEEDS_WORK")


class EvaluatorStubClaudeTest(unittest.TestCase):
    """The same re-ask, end to end through the scripted `claude` stub."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ctx = make_ctx(self.tmp)

    def stream(self, text):
        return "\n".join([
            json.dumps({"type": "assistant", "message": {
                "id": "msg_1", "role": "assistant",
                "content": [{"type": "text", "text": text}],
                "usage": {"input_tokens": 10, "output_tokens": 5,
                          "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}}),
            json.dumps({"type": "result", "subtype": "success", "result": text,
                        "session_id": "sess-eval", "total_cost_usd": 0.02}),
        ]) + "\n"

    def test_stub_claude_is_invoked_twice_and_the_second_verdict_is_used(self):
        script = os.path.join(self.tmp, "script")
        os.makedirs(script)
        with open(os.path.join(script, "001.jsonl"), "w") as fh:
            fh.write(self.stream(verdict_json([])))
        with open(os.path.join(script, "002.jsonl"), "w") as fh:
            fh.write(self.stream(verdict_json([{"name": "nav", "observation": "icon rail"}])))

        fixtures = os.path.join(PLUGIN_ROOT, "tests", "fixtures")
        self.assertTrue(os.access(os.path.join(fixtures, "claude"), os.X_OK),
                        "tests/fixtures/claude must be executable (plan A)")
        stub_log = os.path.join(self.tmp, "stub.log")
        os.environ["PATH"] = fixtures + os.pathsep + os.environ["PATH"]
        os.environ["STUB_SCRIPT"] = script
        os.environ["STUB_DELAY"] = "0"
        os.environ["STUB_LOG"] = stub_log

        c = make_contract([], ["nav"])
        result, verdict = phases.run_evaluator(
            self.ctx, c, "diff", [], [{"name": "nav", "path": "/a/nav.png"}])
        self.assertEqual(verdict["verdict"], "PASS")
        self.assertEqual([v["name"] for v in verdict["views"]], ["nav"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest tests.runner.test_phases_evaluator -v`
Expected: FAIL — `AttributeError: module 'runner.phases' has no attribute 'MUST_READ_MAX_LINES'`, and the prompt assertions fail because plan A's `run_evaluator` passes empty strings for `{{must_read_blocks}}`/`{{screenshots}}`.

- [ ] **Step 3: Add the helpers to `phases.py`**

Insert above `run_evaluator` in `runner/phases.py`:

```python
MUST_READ_MAX_LINES = 400


def _must_read_blocks(worktree, paths):
    """Inline the reference files the contract says the Evaluator must read.

    Pre-loading beats policing tool calls: in the 2026-09-11 run both Evaluators
    were *asked* to open the reference tree and neither did (spec 5.2).
    """
    blocks = []
    for rel in (paths or []):
        full = rel if os.path.isabs(rel) else os.path.join(worktree, rel)
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except (IOError, OSError):
            blocks.append("### %s\n[missing: no such file in the worktree]" % rel)
            continue
        total = len(lines)
        body = "\n".join(lines[:MUST_READ_MAX_LINES])
        if total > MUST_READ_MAX_LINES:
            body += "\n[truncated] showing %d of %d lines" % (MUST_READ_MAX_LINES, total)
        blocks.append("### %s (%d lines)\n```\n%s\n```" % (rel, total, body))
    if not blocks:
        return "(no reference files declared)"
    return "\n\n".join(blocks)


def _screenshots_block(screenshots):
    if not screenshots:
        return "(no screenshots for this task)"
    lines = []
    for shot in screenshots:
        lines.append("- %s [%s]: %s" % (
            shot.get("name", ""), shot.get("kind", "new"), shot.get("path", "")))
    lines.append("")
    lines.append(
        "Use the Read tool on every path above before you answer — they are PNG files "
        "and you can see them. Your verdict MUST carry one views[] entry per name "
        "listed, each with a concrete observation of what the image shows (layout, "
        "icons vs text labels, rail width, empty/error state). Entries marked "
        "[reference] are what the result is supposed to look like: say how the new "
        "image differs. A views[] entry for an image you did not open is a false claim."
    )
    return "\n".join(lines)


def _missing_views(contract, verdict):
    seen = set()
    for view in (verdict.get("views") or []):
        if isinstance(view, dict) and view.get("name"):
            seen.add(view["name"])
    return [n for n in (contract.evaluator_must_view or []) if n not in seen]


def _merge_usage(first, second):
    """Fold an earlier attempt's usage into the returned PhaseResult."""
    if first is None:
        return second
    for model, usage in first.usage_by_model.items():
        current = second.usage_by_model.get(model)
        if current is None:
            second.usage_by_model[model] = usage
        else:
            current.cost_usd += usage.cost_usd
            current.input_tokens += usage.input_tokens
            current.output_tokens += usage.output_tokens
            current.cache_read_tokens += usage.cache_read_tokens
            current.cache_creation_tokens += usage.cache_creation_tokens
    second.tool_calls += first.tool_calls
    return second
```

- [ ] **Step 4: Replace the body of `run_evaluator`**

Replace the whole body with this. The tier selection and `run_phase` keyword values are plan A's; **if plan A's differ, keep plan A's lines** — the contract is the prompt variables, the `views[]` check, and the single re-ask.

```python
def run_evaluator(ctx, contract, diff_text, gate_outputs, screenshots):
    cfg = ctx.cfg
    if ctx.task.class_flag == "complex":
        tier = "most-capable"
    else:
        tier = cfg.role_tiers.get("evaluator") or "standard"
    model = model_for(cfg, tier)

    base_prompt = render_prompt(
        "evaluator",
        loop_dir=ctx.loop_dir,
        worktree=cfg.worktree,
        task_row=ctx.task.raw,
        contract_json=json.dumps(contract_to_dict(contract), indent=2),
        must_read_blocks=_must_read_blocks(cfg.worktree, contract.evaluator_must_read),
        screenshots=_screenshots_block(screenshots),
        gate_outputs="\n\n".join(gate_outputs or []),
        diff=diff_text,
    )

    prompt = base_prompt
    merged = None
    verdict = {}
    for attempt in (1, 2):
        result = run_phase(
            phase="evaluator", model=model, prompt=prompt, cwd=cfg.worktree,
            timeout_s=phase_limit(cfg, "evaluator"), max_turns=EVALUATOR_MAX_TURNS,
            max_budget_usd=None, env=os.environ.copy(), events=ctx.events,
            tick=ctx.tick, role="evaluator",
            activity_path=os.path.join(ctx.runtime_dir, "last-activity"),
        )
        merged = _merge_usage(merged, result)

        correction = None
        try:
            verdict = parse_json_block(result.result_text) or {}
        except ValueError as exc:
            verdict = {}
            correction = (
                "Your reply had no parsable ```json block (%s). Answer again and end "
                "your reply with exactly one fenced json block in the shape the brief "
                "gives." % exc
            )
        if correction is None:
            missing = _missing_views(contract, verdict)
            if not missing:
                return merged, verdict
            correction = (
                "Your verdict is rejected: it carries no views[] observation for %s. "
                "Read each of those screenshots with the Read tool and answer again "
                "with one views[] entry per name. Do not restate your previous verdict "
                "without looking." % ", ".join(missing)
            )
        if attempt == 2:
            break
        prompt = base_prompt + "\n\n## Correction (attempt 2 of 2)\n" + correction

    missing = _missing_views(contract, verdict)
    if missing:
        return merged, {
            "verdict": "NEEDS_WORK",
            "reason": "missing-views",
            "findings": verdict.get("findings", []),
            "views": verdict.get("views", []),
            "summary": "harness: the Evaluator gave no observation for %s after a re-ask"
                       % ", ".join(missing),
        }
    return merged, {
        "verdict": "NEEDS_WORK",
        "reason": "malformed-output",
        "findings": [],
        "views": [],
        "summary": "harness: the Evaluator returned no parsable JSON block after a re-ask",
    }
```

Add `EVALUATOR_MAX_TURNS = 40` beside `MUST_READ_MAX_LINES`. `contract_to_dict` is plan A's contract serializer used by `save_contract`; if plan A exposes it under another name (e.g. `contract.to_dict`), use that.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd plugins/agent-loop && python3 -m unittest tests.runner.test_phases_evaluator -v`
Expected: PASS, 9 tests.

- [ ] **Step 6: Commit**

```bash
git add plugins/agent-loop/runner/phases.py plugins/agent-loop/tests/runner/test_phases_evaluator.py
git commit -m "$(cat <<'MSG'
agent-loop: hand the Evaluator the reference code and the images

evaluator_must_read files are inlined into the prompt (400-line cap with a
[truncated] marker) and every screenshot path is listed with an instruction to
Read it and report one views[] observation per name. A verdict missing a
required view is rejected and re-asked once; a second miss is NEEDS_WORK with
reason missing-views. Usage from both attempts is summed onto the returned
PhaseResult so a re-ask cannot go unattributed.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
MSG
)"
```

---

### Task 7: Scout prompt — instantiate the recipe, declare fidelity and evaluator inputs

**Files:**
- Modify: `plugins/agent-loop/runner/prompts/scout.md`
- Modify: `plugins/agent-loop/runner/phases.py` (`run_scout` gains one prompt variable)
- Test: `plugins/agent-loop/tests/runner/test_prompts_scout_render.py`

**Interfaces:**
- Produces: a new prompt placeholder `{{render_recipe}}` (**not** in the interfaces doc's placeholder list — plan B adds it; plan D's `prompts.contract.sh` must allow it), filled by `phases.recipe_text(cfg) -> str`.

- [ ] **Step 1: Write the failing test**

Create `plugins/agent-loop/tests/runner/test_prompts_scout_render.py`:

```python
"""Scout prompt carries the render recipe and the contract rules (plan B task 7)."""
from __future__ import annotations

import os
import sys
import types
import unittest

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from runner import phases  # noqa: E402

SCOUT = os.path.join(PLUGIN_ROOT, "runner", "prompts", "scout.md")


class ScoutPromptTest(unittest.TestCase):
    def setUp(self):
        with open(SCOUT, encoding="utf-8") as fh:
            self.text = fh.read()

    def test_recipe_placeholder_is_present(self):
        self.assertIn("{{render_recipe}}", self.text)

    def test_prompt_tells_the_scout_how_to_instantiate_render_gate(self):
        for needle in ("render_gate", "{route}", "{screenshot}", "ui_globs", "| no-ui"):
            self.assertIn(needle, self.text, "scout.md must mention %r" % needle)

    def test_prompt_covers_fidelity_source(self):
        for needle in ("fidelity_source", "copy_of", "min_similarity"):
            self.assertIn(needle, self.text, "scout.md must mention %r" % needle)

    def test_prompt_covers_evaluator_inputs(self):
        for needle in ("evaluator_must_read", "evaluator_must_view"):
            self.assertIn(needle, self.text, "scout.md must mention %r" % needle)

    def test_prompt_forbids_annotation_as_evidence(self):
        self.assertIn("comment", self.text.lower())
        self.assertRegex(self.text, r"(?i)grep.*(comment|TODO)|TODO.*not (a|evidence)")


class RecipeTextTest(unittest.TestCase):
    def test_no_recipe_says_so_plainly(self):
        cfg = types.SimpleNamespace(render={}, worktree="/wt")
        self.assertIn("no render recipe", phases.recipe_text(cfg).lower())

    def test_recipe_is_rendered_with_every_subkey(self):
        cfg = types.SimpleNamespace(worktree="/wt", render={
            "start": "pnpm dev",
            "ready": "http://127.0.0.1:5273/",
            "command": "cypress run --spec {route} --env screenshot={screenshot}",
            "ui_globs": ["apps/*/src/**/*.tsx"],
            "reference": [{"name": "rise-nav", "path": "docs/rise-nav.png"}],
        })
        text = phases.recipe_text(cfg)
        self.assertIn("pnpm dev", text)
        self.assertIn("http://127.0.0.1:5273/", text)
        self.assertIn("{route}", text)
        self.assertIn("apps/*/src/**/*.tsx", text)
        self.assertIn("rise-nav", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd plugins/agent-loop && python3 -m unittest tests.runner.test_prompts_scout_render -v`
Expected: FAIL — `AssertionError: '{{render_recipe}}' not found` and `AttributeError: module 'runner.phases' has no attribute 'recipe_text'`.

- [ ] **Step 3: Append the section to `runner/prompts/scout.md`**

Append verbatim, at the end of the file:

````markdown
## The render recipe, fidelity, and what the Evaluator will be given

This repo's render recipe:

{{render_recipe}}

**`render_gate`.** If a recipe exists and any path in your `allow_list` matches one of
its `ui_globs`, you MUST fill `render_gate` — the harness rejects the contract otherwise,
unless the plan row is tagged `| no-ui`. Instantiate the recipe's `command` once per route
this task changes: substitute `{route}` with the route or spec path, and `{screenshot}`
with the file the command will write. Then declare each image in `screenshots` as
`{"name": "<short-id>", "path": "<exactly where the command writes it>"}`. The harness
runs the commands and then looks for those files; a command that exits 0 and writes no
image fails the tick, so check the path against the tool's own output directory (Cypress
writes `cypress/screenshots/<spec>/<title>.png`; a Playwright script writes wherever you
told it to). Do not invent a path you have not verified. Prefer one screenshot per visual
state the success criteria mention (list, empty, error), not one per file changed.

**`fidelity_source`.** Fill it when the plan row's verb is copy, port or replicate, or the
row carries `| copy_of: T<n>` — one entry per reference→target file pair,
`{"src": "<the reference file>", "dst": "<the file this task writes>", "min_similarity":
0.6}`. Use 0.8 when the instruction is "port verbatim" and 0.6 when it is "port and adapt".
The harness computes a normalized line-similarity ratio and fails the tick below the
threshold. This exists because a previous run's "copy" of four layout components was a
six-line `TODO(…): copied from …` comment that passed a grep-for-the-comment gate. A
comment that says a file was copied is not evidence that it was; never write a
`verification` entry that greps for such a comment.

**`evaluator_must_read`.** List the reference implementation files a human would open to
judge "does this look like the original" — the exact files named in the task row, the plan
row, `copy_of`'s target, or the spec excerpt. The harness inlines their contents into the
Evaluator's prompt (400 lines each), so the Evaluator cannot skip them. Six files is
plenty; pick the ones that carry the structure (the component, its prop/type signature,
its constants), not barrel files.

**`evaluator_must_view`.** Every `name` you declared in `render_gate.screenshots`. The
harness rejects a verdict that has no observation for one of these names and re-asks once,
so a name you list here is a guarantee that someone looked. Leave it `[]` only when
`render_gate` is absent.
````

- [ ] **Step 4: Add `recipe_text` and pass it from `run_scout`**

Add to `runner/phases.py`:

```python
def recipe_text(cfg):
    """The `Render:` recipe as prompt text for the Scout."""
    recipe = getattr(cfg, "render", None) or {}
    if not recipe:
        return ("(no render recipe is configured for this repo — leave render_gate null "
                "and say so in scout_notes)")
    lines = []
    for key in ("start", "ready", "command"):
        if recipe.get(key):
            lines.append("- %s: %s" % (key, recipe[key]))
    if recipe.get("ui_globs"):
        lines.append("- ui_globs: %s" % " ".join(recipe["ui_globs"]))
    for ref in recipe.get("reference", []):
        lines.append("- reference screenshot %s: %s" % (ref.get("name", ""), ref.get("path", "")))
    return "\n".join(lines)
```

In `run_scout`, add `render_recipe=recipe_text(ctx.cfg),` to the `render_prompt("scout", …)` call.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd plugins/agent-loop && python3 -m unittest tests.runner.test_prompts_scout_render -v`
Expected: PASS, 7 tests.

- [ ] **Step 6: Commit**

```bash
git add plugins/agent-loop/runner/prompts/scout.md plugins/agent-loop/runner/phases.py \
        plugins/agent-loop/tests/runner/test_prompts_scout_render.py
git commit -m "$(cat <<'MSG'
agent-loop: the Scout instantiates the render recipe per task

scout.md gains a {{render_recipe}} section: how to turn the recipe's command
template into render_gate for this task's routes, when fidelity_source is
mandatory and what threshold to pick, and how to choose evaluator_must_read
(the reference files) and evaluator_must_view (every screenshot name). It also
names the anti-pattern that produced the fake copy: a comment saying a file was
copied is not evidence, and must never be a verification entry.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
MSG
)"
```

---

### Task 8: Setup skill asks for the render recipe

**Files:**
- Modify: `plugins/agent-loop/skills/agent-loop-setup/SKILL.md`
- Modify: `plugins/agent-loop/templates/LOOP_CONFIG.md`
- Modify: `plugins/agent-loop/tests/setup.contract.sh`

**Interfaces:**
- Consumes: the `Render:` grammar from Task 1 — the block the skill writes must be the block `config.load_config` parses.
- Produces: nothing importable; this is the human-facing half of the recipe.

- [ ] **Step 1: Write the failing assertions**

Append to `plugins/agent-loop/tests/setup.contract.sh`, immediately before the final `assert_summary`:

```bash
# Plan B Task 8: the wizard asks for a render recipe, writes the Render: block, and warns
# when the repo has UI files but the user declined one.
has 'render recipe'
grep -qE '^1?[0-9]\. \*\*Render recipe\*\*' "$F"; assert_true $? "setup wizard has a numbered Render recipe question"
has 'ui_globs'
has 'screenshot'
has '{route}'
has 'cypress'
has 'playwright'
grep -qiE 'no render recipe|without a recipe' "$F"; assert_true $? "setup warns when a UI repo has no recipe"
grep -qF 'artifacts/' "$F"; assert_true $? "setup gitignores the artifacts dir"

# The template ships the block commented out, with every sub-key documented.
grep -qE '^# Render:' "$TMPL"; assert_true $? "template ships a commented Render: block"
for k in start ready command ui_globs reference; do
  grep -qE "^#[[:space:]]+$k:" "$TMPL"; assert_true $? "template documents the Render sub-key $k"
done
grep -qE '^Render:' "$TMPL"; assert_false $? "template must not ship an ACTIVE Render: block"
grep -qE '^Decision policy:' "$TMPL"; assert_true $? "template ships Decision policy (spec 4.6)"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/mitcsutt/Documents/Projects/claude-toolbelt && bash plugins/agent-loop/tests/setup.contract.sh`
Expected: FAIL — `setup must mention: render recipe`, `template ships a commented Render: block`, and the rest of the new block.

- [ ] **Step 3: Add the wizard question to the setup skill**

In `skills/agent-loop-setup/SKILL.md`, **Step 2: Config wizard**, add after item 11 (Medic):

````markdown
12. **Render recipe** — how a headless tick can *see* the product. Ask this with one
    `AskUserQuestion` whose question text explains what a recipe is and shows both shapes,
    with options `Browser test runner (Cypress/Playwright spec per route)`, `Standalone
    screenshot script`, and `No recipe — I accept UI tasks ship unseen`.

    Explain, in the question: without a recipe the loop's only proof that a page works is
    lint/tsc/build/jsdom, and those all pass on a page that renders nothing. In one
    observed run the loop shipped a broken app shell and a human found it 30 hours and 88
    ticks later, because no tick ever opened a browser.

    Then collect the four parts as free text and write them under `Render:` in
    `$LOOP_DIR/LOOP_CONFIG.md`. Sub-keys are indented two spaces:

    - `start:` (optional) — the command that starts the app. The harness runs it **once**,
      in the background, records the pid in `runtime/render-app.pid`, and kills it when the
      loop exits. Leave blank if the render command starts its own server.
    - `ready:` (optional) — a URL the harness polls until it returns HTTP 200 (max 120 s)
      before the first render. Leave blank if `start:` is blank.
    - `command:` — the render command template, with `{route}` and `{screenshot}`
      placeholders the Scout substitutes per task.
    - `ui_globs:` — space-separated globs for the repo's UI source. **This is the field
      that makes the gate mandatory:** a task whose `allow_list` touches one of these globs
      must carry a `render_gate` or the contract is rejected (unless its plan row is tagged
      `| no-ui`).
    - `reference:` (optional) — space-separated `name=path` pairs pointing at screenshots
      of what the result should look like (e.g. the existing app a rebuild is copying).
      The harness hands these to the Evaluator alongside the new screenshots.

    A Cypress repo:

    ```
    Render:
      start: pnpm --filter @repo/internal dev --port 5273
      ready: http://127.0.0.1:5273/
      command: pnpm --filter @repo/integration cypress run --spec {route}
      ui_globs: apps/*/src/**/*.tsx packages/ui/**/*.tsx
      reference: rise-customers=docs/reference/rise-customers.png
    ```

    Cypress writes to `cypress/screenshots/<spec>/<title>.png`; tell the user the Scout
    will declare that path, so their specs must call `cy.screenshot()`.

    A standalone Playwright script (`scripts/shot.mjs` taking a URL and an output path):

    ```
    Render:
      start: pnpm dev --port 5273
      ready: http://127.0.0.1:5273/
      command: node scripts/shot.mjs http://127.0.0.1:5273{route} {screenshot}
      ui_globs: src/**/*.tsx src/**/*.css
    ```

    **Warn when the repo has UI files but no recipe was given.** Check:

    ```bash
    git ls-files '*.tsx' '*.jsx' '*.vue' '*.svelte' '*.html' | head -n 1
    ```

    If that prints anything and the user chose "No recipe", say plainly: **this repo has
    UI source but no render recipe, so no tick will ever look at the product** — the gate
    is skipped, `evaluator_must_view` will be empty, and a page that renders a blank screen
    will pass lint, tsc, build and jsdom tests. Offer to leave `Render:` out and continue
    (spec §11 decision 6: a missing recipe is a warning, not a refusal), and record the
    warning in `$LOOP_DIR/LOOP_LEARNINGS.md` `## Patterns` as "no render recipe — UI tasks
    are verified by code presence only."
````

- [ ] **Step 4: Bring the skill's `Limits:` prose up to the v3 defaults**

Still in **Step 2: Config wizard**, item 5 currently documents one key and the default
`1200`. Replace its body so it matches spec §4.2 and the template you write in Step 6:

> 5. **Limits** — the harness reads one `Limits:` line of `key=value` pairs. `tick_timeout`
>    (default `1800`) is the outer per-tick sanity cap and the value the dashboard displays;
>    the real budgets are per phase: `scout_timeout=480 worker_timeout=1500 wrapup_timeout=300 eval_timeout=480
>    judge_timeout=360 planner_timeout=900 gate_cmd_timeout=600`, plus `worker_resume_max=1` (how many times a
>    timed-out Worker is resumed from its checkpoint before the Judge escalates or splits),
>    `worker_budget_usd=6`, and `max_attempts=3` (the hard cap on attempts for one task,
>    resumes and escalations included). All seconds except the last two. Omitted keys take
>    those defaults, so most users leave the line as the template ships it.
>
>    There is still **no cost, iteration, or wall-clock budget for the run**. The loop runs
>    until the plan is done or it hits your subscription's usage window — at which point the
>    harness reads the `rate_limit_event` reset time and auto-waits, then resumes.

- [ ] **Step 5: Add `artifacts/` to the run-dir `.gitignore` in Step 1.5**

In **Step 1.5**, inside the `$LOOP_DIR/.gitignore` code block, add after the `runtime/` line:

```gitignore
# Screenshots from the render gate. Durable (the postmortem reads them from
# disk) but binary and bulky, so not on the branch — and, because they live
# inside the worktree, gitignoring them is also what keeps the SANDBOX stray
# check from reverting them between phases.
artifacts/
```

- [ ] **Step 6: Add the summary line**

In the Step 6 summary block, after the `Medic:` line, add:

```
Render:       <recipe | none>   (none = no tick ever opens the product; UI tasks are code-presence checks only)
```

- [ ] **Step 7: Update `templates/LOOP_CONFIG.md`**

Replace the `Orchestrator model:` paragraph and the per-role tier lines with the v3 shape, and add the commented recipe. **Boundary note:** plan D also touches this template (README/version pass) and the spec gives the tier-map and `Decision policy:` wizard questions to plan D — this task writes those *lines* so the file parses and so `Render:` has somewhere to live, but the wizard questions for them are plan D's. If plan D landed first, keep its lines and add only the `Render:` block. The template becomes:

```markdown
# Loop Config
Started: <ISO8601>
Goal: <one sentence>
Loop type: refactor | new-feature | test-sweep | custom
Granularity: single | segmented
TDD mode: none | tdd-per-task
Verification pipeline: lint tsc build test
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
# autonomous = the Judge may widen scout-authored constraints, escalate tiers, split a task
# and pick a default the spec is silent on, logging each to LOOP_DECISIONS.md.
# conservative = same, minus the spec-silent defaults, which are deferred for a human.
Decision policy: autonomous

## Model tiers. The ONLY place a concrete model is named.
Tiers: cheap=haiku standard=sonnet most-capable=opus
# Per-role tier: cheap | standard | most-capable. Blank = the harness default from the
# design doc (planner/judge/reviewer most-capable, scout/worker standard, learner cheap).
Planner tier: most-capable
Scout tier: standard
Worker tier: standard
# Evaluator tier is class-governed: mechanical -> skipped, `| complex` -> most-capable,
# default -> standard. Setting a tier here is a ceiling for `| complex` work only.
Evaluator tier:
# Ignored by v3; kept so a v2 config still parses.
Orchestrator model:

## Render recipe (optional). Uncomment and fill to turn on the render gate.
# Without it nothing in the loop ever looks at the product: lint, tsc, build and jsdom
# all pass on a page that renders a blank screen. `ui_globs` is what makes the gate
# mandatory — a task touching those paths must carry a render_gate unless its plan row
# is tagged `| no-ui`. `{route}` and `{screenshot}` are substituted per task by the Scout.
# Render:
#   start: pnpm --filter @repo/internal dev --port 5273
#   ready: http://127.0.0.1:5273/
#   command: pnpm --filter @repo/integration cypress run --spec {route}
#   ui_globs: apps/*/src/**/*.tsx packages/ui/**/*.tsx
#   reference: rise-customers=docs/reference/rise-customers.png
```

- [ ] **Step 8: Run the contract test to verify it passes**

Run: `cd /Users/mitcsutt/Documents/Projects/claude-toolbelt && bash plugins/agent-loop/tests/setup.contract.sh`
Expected: PASS (the existing `Evaluator tier` assertions still hold — the line ships blank).

- [ ] **Step 9: Run the whole suite**

Run: `cd /Users/mitcsutt/Documents/Projects/claude-toolbelt && bash scripts/test-all.sh`
Expected: PASS. Paste the output — per `CLAUDE.md`, work is not done without it.

- [ ] **Step 10: Commit**

```bash
git add plugins/agent-loop/skills/agent-loop-setup/SKILL.md \
        plugins/agent-loop/templates/LOOP_CONFIG.md \
        plugins/agent-loop/tests/setup.contract.sh
git commit -m "$(cat <<'MSG'
agent-loop: setup asks for a render recipe once per repo

One wizard question with a Cypress example and a Playwright-script example,
writing the Render: block the harness parses. When the repo has UI source and
the user declines, setup says plainly that no tick will ever look at the product
and records it as a learning — a missing recipe is a warning, not a refusal.
artifacts/ joins the run-dir gitignore, which also keeps the SANDBOX stray check
from reverting the screenshots.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018ZowfSSwVewPWpuzbeayUy
MSG
)"
```

---

## Hand-off to plans C and D

- **Plan C** (Judge): a render or fidelity failure arrives on the failure path with `vis.failure_text` already in `gate_outputs`, and `runtime/fidelity-<T>.json` on disk. `verdict["reason"] == "missing-views"` is an Evaluator failure the Judge should treat as *unverified*, never as *task failed*.
- **Plan D** (surface): `serve.py` renders the `artifact` event as a thumbnail from `path`; `/agent-loop-postmortem` reads `$LOOP_DIR/artifacts/` and `runtime/fidelity-*.json`; `tests/prompts.contract.sh` must accept the `{{render_recipe}}` placeholder in `scout.md`; the plugin README gains the `Render:` recipe in its config section.
