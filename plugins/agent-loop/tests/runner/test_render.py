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
        # The event's path is relative to the loop dir, NOT the absolute/cwd
        # path this process happens to have. A real launch uses a RELATIVE
        # LOOP_DIR (`LOOP_DIR=.claude/loop/<run-id> bash run.sh`), so an
        # emitter-relative path is meaningless to serve.py, which abspaths its
        # own loop_dir -- every screenshot 404s. See test_artifact_path_reads
        # _what_render_writes in tests/serve.test.py for the other half.
        self.assertEqual(artifacts[0]["path"],
                         os.path.join("artifacts", "T60", "orgunits-list.png"))
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
