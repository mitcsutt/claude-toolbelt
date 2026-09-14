"""Evaluator must-read / must-view inputs and the one re-ask (plan B task 6)."""
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
        spec_path="",
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
        # Conflict row 14: `phases.run_phase` does not exist. Dispatch goes
        # through `phases.claude_proc.run_phase`; patching the wrong name would
        # spawn the real subprocess and the test would pass for the wrong reason.
        self.real_run_phase = phases.claude_proc.run_phase

        def restore():
            phases.claude_proc.run_phase = self.real_run_phase

        self.addCleanup(restore)

    def patch(self, rec):
        phases.claude_proc.run_phase = rec
        return rec

    def write_ref(self, name, body):
        path = os.path.join(self.ctx.cfg.worktree, "ref", name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        return path

    def test_must_read_file_contents_reach_the_prompt(self):
        self.write_ref("Nav.tsx", "export const RAIL_WIDTH = 80;\n<Icon name={item.icon} />\n")
        rec = self.patch(Recorder([verdict_json([{"name": "nav", "observation": "icon rail present"}])]))
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
        rec = self.patch(Recorder([verdict_json([])]))
        c = make_contract(["ref/Big.tsx"], [])
        phases.run_evaluator(self.ctx, c, "diff", [], [])
        prompt = rec.prompts[0]
        self.assertIn("const l399 = 399;", prompt)
        self.assertNotIn("const l400 = 400;", prompt)
        self.assertIn("[truncated]", prompt)
        self.assertEqual(phases.MUST_READ_MAX_LINES, 400)

    def test_a_missing_must_read_file_is_reported_not_crashed(self):
        rec = self.patch(Recorder([verdict_json([])]))
        c = make_contract(["ref/Gone.tsx"], [])
        phases.run_evaluator(self.ctx, c, "diff", [], [])
        self.assertIn("ref/Gone.tsx", rec.prompts[0])
        self.assertIn("missing", rec.prompts[0])

    def test_screenshot_paths_and_the_read_instruction_reach_the_prompt(self):
        rec = self.patch(Recorder([verdict_json([
            {"name": "nav", "observation": "icons"},
            {"name": "app-nav", "observation": "icons"},
        ])]))
        c = make_contract([], ["nav"])
        phases.run_evaluator(
            self.ctx, c, "diff", [],
            [{"name": "nav", "path": "/a/nav.png"},
             {"name": "app-nav", "path": "/b/app.png", "kind": "reference"}],
        )
        prompt = rec.prompts[0]
        self.assertIn("/a/nav.png", prompt)
        self.assertIn("/b/app.png", prompt)
        self.assertIn("reference", prompt)

    def test_the_screenshot_block_itself_carries_the_read_instruction(self):
        # The shipped evaluator.md already says "Read" and "views", so asserting
        # those against the whole prompt would pass with an empty block.
        block = phases._screenshots_block(
            [{"name": "nav", "path": "/a/nav.png"},
             {"name": "app-nav", "path": "/b/app.png", "kind": "reference"}])
        self.assertIn("/a/nav.png", block)
        self.assertIn("nav", block)
        self.assertIn("reference", block)
        self.assertIn("Read", block)
        self.assertIn("views[]", block)
        self.assertEqual(phases._screenshots_block([]), "(none)")

    def test_a_verdict_missing_a_required_view_is_re_asked_once_then_accepted(self):
        rec = self.patch(Recorder([
            verdict_json([]),
            verdict_json([{"name": "nav", "observation": "80px rail, icons, no labels"}]),
        ]))
        c = make_contract([], ["nav"])
        result, verdict = phases.run_evaluator(
            self.ctx, c, "diff", [], [{"name": "nav", "path": "/a/nav.png"}])
        self.assertEqual(len(rec.prompts), 2)
        self.assertIn("nav", rec.prompts[1])
        self.assertIn("rejected", rec.prompts[1].lower())
        self.assertEqual(verdict["verdict"], "PASS")

    def test_two_misses_become_needs_work_with_reason_missing_views(self):
        rec = self.patch(Recorder([verdict_json([]), verdict_json([])]))
        c = make_contract([], ["nav", "header"])
        result, verdict = phases.run_evaluator(
            self.ctx, c, "diff", [], [{"name": "nav", "path": "/a/nav.png"}])
        self.assertEqual(len(rec.prompts), 2)
        self.assertEqual(verdict["verdict"], "NEEDS_WORK")
        self.assertEqual(verdict["reason"], "missing-views")
        self.assertIn("nav", verdict["summary"])
        self.assertIn("header", verdict["summary"])

    def test_usage_from_both_attempts_is_summed_onto_the_returned_result(self):
        self.patch(Recorder([verdict_json([]), verdict_json([{"name": "nav", "observation": "ok"}])]))
        c = make_contract([], ["nav"])
        result, _ = phases.run_evaluator(
            self.ctx, c, "diff", [], [{"name": "nav", "path": "/a/nav.png"}])
        self.assertAlmostEqual(result.usage_by_model["sonnet"].cost_usd, 2.0)
        self.assertEqual(result.tool_calls, 6)

    def test_malformed_json_is_re_asked_once_then_reported(self):
        rec = self.patch(Recorder(["no json here at all", "still no json"]))
        c = make_contract([], [])
        result, verdict = phases.run_evaluator(self.ctx, c, "diff", [], [])
        self.assertEqual(len(rec.prompts), 2)
        self.assertEqual(verdict["reason"], "malformed-output")
        self.assertEqual(verdict["verdict"], "NEEDS_WORK")

    def test_a_missing_view_costs_exactly_one_extra_subprocess(self):
        # R4: the malformed-JSON re-ask and the missing-views re-ask share one
        # budget. Two misses must not become three dispatches.
        rec = self.patch(Recorder([verdict_json([]), verdict_json([]), verdict_json([])]))
        c = make_contract([], ["nav"])
        phases.run_evaluator(self.ctx, c, "diff", [], [{"name": "nav", "path": "/a/nav.png"}])
        self.assertEqual(len(rec.prompts), 2)

    def test_the_dispatch_keeps_its_stop_check_and_watchdog_kwargs(self):
        # R4's other half: never re-implement dispatch in run_evaluator, or STOP
        # responsiveness, rate-limit capture and the stall watchdog vanish.
        seen = {}

        def spy(**kwargs):
            seen.update(kwargs)
            return fake_result(verdict_json([]))

        self.patch(spy)
        phases.run_evaluator(self.ctx, make_contract([], []), "diff", [], [])
        for key in ("stop_check", "ratelimit_path", "stall_s", "activity_path"):
            self.assertIn(key, seen)


class EvaluatorStubClaudeTest(unittest.TestCase):
    """The same re-ask, end to end through the scripted `claude` stub."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ctx = make_ctx(self.tmp)
        # Conflict row 21: `_env()` is `dict(os.environ)` and tests/all.sh runs
        # one discover process, so an unrestored mutation leaks into every later
        # test in the run.
        self.saved_env = dict(os.environ)

        def restore():
            os.environ.clear()
            os.environ.update(self.saved_env)

        self.addCleanup(restore)

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
