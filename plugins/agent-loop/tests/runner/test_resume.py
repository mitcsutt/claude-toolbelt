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
        self.h = cfixtures.make_harness(self.cfg, self.plan, self.loop_dir,
                                        self.runtime)
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
        results, payload = runner_run.run_worker_attempt(self.h, self.ctx,
                                                         self.contract)

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

    def test_the_wrapup_is_a_worker_turn_and_spends_a_resume(self):
        """Row 12 of the pre-flight scan: §14's PHASES has no WRAPUP, so the
        wrap-up is recorded as a WORK phase and `worker_resume_max` counts it."""
        from runner.task_state import TaskState
        state = TaskState.load(self.runtime, "T60")
        state.begin_attempt("standard")
        runner_run.run_worker_attempt(self.h, self.ctx, self.contract, state=state)
        self.assertEqual(state.resumes_spent(), 1)

    def test_sandbox_reverts_strays_after_a_killed_worker(self):
        stray = os.path.join(self.cfg.worktree, "apps", "frontend")
        os.makedirs(stray)
        with open(os.path.join(stray, "oops.tsx"), "w") as fh:
            fh.write("// written outside the allow_list\n")
        runner_run.run_worker_attempt(self.h, self.ctx, self.contract)
        self.assertFalse(os.path.exists(os.path.join(stray, "oops.tsx")),
                         "SANDBOX runs after a killed Worker too (spec §6.4)")

    def test_the_sandbox_spares_work_the_human_had_already_left_in_the_tree(self):
        """Conflict row 5: a stray the Worker made goes; a file the human was
        already editing when the loop started is not the loop's to revert."""
        theirs = os.path.join(self.cfg.worktree, "HUMAN.md")
        with open(theirs, "w") as fh:
            fh.write("notes I have not committed yet\n")
        h = cfixtures.make_harness(self.cfg, self.plan, self.loop_dir, self.runtime)
        self.assertIn("HUMAN.md", h.preexisting)
        runner_run.run_worker_attempt(h, self.ctx, self.contract)
        self.assertTrue(os.path.exists(theirs),
                        "the loop must never revert work it did not create")


class TestNoTimeout(_StubBase):
    def script(self):
        return {"001.sh": writes_worker_result(self.runtime, "complete", ""),
                "001.jsonl": cfixtures.stub_result(
                    {"status": "complete", "summary": "done"})}

    def test_a_clean_worker_runs_no_wrapup(self):
        results, payload = runner_run.run_worker_attempt(self.h, self.ctx,
                                                         self.contract)
        self.assertEqual([r.phase for r in results], ["worker"])
        self.assertEqual(payload["status"], "complete")
        # The shipped stub logs "<NNN> <resumed-sid|none>" on EVERY invocation,
        # so "no wrap-up ran" is "no invocation was given a session to resume".
        self.assertEqual(self.log_text().split(), ["001", "none"])


if __name__ == "__main__":
    unittest.main()
