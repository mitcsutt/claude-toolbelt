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
                        "TaskState writes runtime/task-<T>.json (spec 14)")
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
