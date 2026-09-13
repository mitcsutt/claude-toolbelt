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

    def test_the_wrapup_is_recorded_as_work_but_spends_no_resume(self):
        """§14's PHASES has no WRAPUP, so the wrap-up is bracketed as WORK and a
        kill during it still boots to the wrap-up path. It is not a resume
        though: §6 step 1 makes it automatic, and `worker_resume_max` bounds the
        Judge's step-2 resumes, which must still be affordable after it."""
        from runner.task_state import TaskState
        state = TaskState.load(self.runtime, "T60")
        state.begin_attempt("standard")
        runner_run.run_worker_attempt(self.h, self.ctx, self.contract, state=state)
        phases_seen = [p["phase"] for p in state.attempts[-1]["phase_results"]]
        self.assertEqual(phases_seen, ["worker", "worker-wrapup"])
        self.assertEqual(state.resumes_spent(), 0)
        self.assertEqual(self.ctx.resume_count, 0,
                         "a resume is still affordable after the wrap-up")

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
        results, payload = runner_run.run_worker_attempt(self.h, self.ctx,
                                                         self.contract, attempt=2)

        self.assertEqual(len(results), 1)
        self.assertIn("sid-prev", self.log_text())
        self.assertEqual(payload["status"], "partial",
                         "the stub cannot write worker-result.json; the file is "
                         "still the seeded one")

        starts = [e for e in cfixtures.read_events(self.loop_dir)
                  if e["type"] == "role_start" and e["role"] == "Worker"]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]["model"], "opus",
                         "the tier the Judge armed, not a function of the "
                         "attempt number (spec §6 step 2)")
        self.assertEqual(results[0].model, "opus")

if __name__ == "__main__":
    unittest.main()
