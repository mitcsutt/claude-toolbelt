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
        self.assertEqual(render_mod.failure_kind(vis), "")

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
        self.assertIn("render", vis.failure_text.lower())
        self.assertIn("gate-render-T60-1.txt", vis.failure_text)
        self.assertEqual(render_mod.failure_kind(vis), "render")

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
        self.assertEqual(render_mod.failure_kind(vis), "fidelity")

    def test_a_render_app_that_never_becomes_ready_fails_without_running_commands(self):
        ctx = make_ctx(self.tmp, render={"start": "sleep 60",
                                         "ready": "http://127.0.0.1:9/"})
        self.addCleanup(render_mod.stop_app, ctx.runtime_dir)
        c = make_contract(contract_mod.RenderGate(
            commands=[WRITE_PNG], screenshots=[{"name": "a", "path": "shots/a.png"}]))
        vis = render_mod.run_visual_checks(ctx, c, ready_timeout_s=1, poll_s=0.2)
        self.assertFalse(vis.ok)
        self.assertEqual(render_mod.failure_kind(vis), "render")
        self.assertEqual(vis.render_results, [])
        self.assertFalse(os.path.exists(os.path.join(ctx.cfg.worktree, "shots", "a.png")))


class RunWiringTest(unittest.TestCase):
    """run.py must call the checks between the gate and the evaluator."""

    def setUp(self):
        with open(os.path.join(PLUGIN_ROOT, "runner", "run.py")) as fh:
            self.src = fh.read()

    def test_run_imports_render_and_calls_the_three_entry_points(self):
        self.assertIn("render,", self.src)               # the package import
        self.assertIn("run_visual_checks(", self.src)
        self.assertIn("stop_app(", self.src)

    def test_visual_checks_sit_between_the_gate_and_the_evaluator(self):
        gate_at = self.src.index("run_commands(")
        visual_at = self.src.index("run_visual_checks(")
        evaluator_at = self.src.index("run_evaluator(")
        self.assertLess(gate_at, visual_at)
        self.assertLess(visual_at, evaluator_at)

    def test_a_visual_failure_keeps_its_own_failure_kind(self):
        # R1: folding render/fidelity into `gate` hides "it built and looks
        # wrong" behind "it did not build" — the confusion spec 1 defect 1 is
        # about. The kind is half of failure_signature and reaches the Judge.
        self.assertIn("render.failure_kind(vis)", self.src)
        self.assertIn('"render", "fidelity"', self.src)   # both in FAILURE_KINDS

    def test_the_visual_failure_path_falls_through_to_handle_failure(self):
        # Never `return` out of execute_tick on a visual failure: that would skip
        # handle_failure, the attempt cap and the retry/escalate/resume block.
        body = self.src[self.src.index("run_visual_checks("):self.src.index("handle_failure(")]
        self.assertNotIn("\n            return out", body)

    def test_stop_app_runs_in_teardown(self):
        teardown = self.src[self.src.index("def teardown("):]
        self.assertIn("stop_app(", teardown)


if __name__ == "__main__":
    unittest.main()
