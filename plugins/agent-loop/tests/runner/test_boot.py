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


def read_state(runtime, task_id="T60"):
    with open(os.path.join(runtime, "task-%s.json" % task_id)) as fh:
        return json.load(fh)


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
        self.h = cfixtures.make_harness(self.cfg, self.plan, self.loop_dir,
                                        self.runtime)
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

    def test_the_dossier_is_gathered_before_anything_is_reverted(self):
        """Conflict row 3: plan A's mechanical branch reverted the strays and
        THEN returned, so a Judge put in its place would have judged a tree the
        harness had already cleaned."""
        seen = {}

        def capture(ctx, inp):
            seen["evidence"] = inp.boot_evidence
            return judge._normalize(decision(decision="revert-and-retry"))

        with mock.patch.object(judge, "decide", side_effect=capture):
            judge.boot_reconcile(self.ctx, self.contract)
        self.assertIn("apps/frontend/App.tsx", seen["evidence"])
        self.assertIn("organisationalUnits.ts", seen["evidence"])
        self.assertFalse(os.path.exists(self.stray), "and only then reverted")


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

        state = read_state(self.runtime)
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

    def test_revert_and_retry_never_touches_the_loops_own_dir(self):
        """The loop dir sits inside the worktree. Reverting it would destroy the
        plan, the contract and the state file this recovery depends on."""
        with judge_says(decision(decision="revert-and-retry", rationale="r")):
            judge.boot_reconcile(self.ctx, self.contract)
        self.assertTrue(os.path.exists(
            os.path.join(self.loop_dir, "LOOP_PLAN.md")))
        self.assertTrue(os.path.exists(
            os.path.join(self.runtime, "sprint-T60.json")))

    def test_resume_keeps_the_tree_and_routes_to_work(self):
        from runner import run as runner_run
        with judge_says(decision(decision="resume", classification="capability",
                                 rationale="the checkpoint matches the tree")):
            self.assertEqual(
                runner_run.boot_task(self.h, self.ctx, self.contract), "work")
        self.assertTrue(os.path.exists(self.kept))
        self.assertTrue(os.path.exists(
            os.path.join(self.runtime, "task-T60.json")),
            "reconciliation creates the state file that was missing")
        state = read_state(self.runtime)
        self.assertEqual(state["phase"], "WORK",
                         "a crash during the reconcile boots back to the same "
                         "branch instead of falling through to SCOUT")

    def test_retry_routes_to_scout_and_keeps_the_tree(self):
        from runner import run as runner_run
        with judge_says(decision(decision="retry", classification="capability",
                                 rationale="the tree is worth keeping")):
            self.assertEqual(
                runner_run.boot_task(self.h, self.ctx, self.contract), "scout")
        self.assertTrue(os.path.exists(self.kept))
        state = read_state(self.runtime)
        self.assertEqual(state["phase"], "SCOUT")

    def test_defer_marks_the_task_and_writes_cleanup(self):
        from runner import run as runner_run
        from runner.plan import Plan
        with judge_says(decision(decision="defer",
                                 rationale="the tree contradicts HEAD",
                                 changes={"default_choice": "revert and re-run",
                                          "blocks": []})):
            self.assertEqual(
                runner_run.boot_task(self.h, self.ctx, self.contract), "defer")
        reloaded = Plan.load(os.path.join(self.loop_dir, "LOOP_PLAN.md"))
        self.assertEqual(
            [t for t in reloaded.tasks() if t.id == "T60"][0].state, "blocked")
        self.assertIn("## T60", cfixtures.read(
            os.path.join(self.loop_dir, "LOOP_CLEANUP.md")))


if __name__ == "__main__":
    unittest.main()
