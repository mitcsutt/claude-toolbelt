import os
import shutil
import subprocess
import tempfile
import unittest

import cfixtures  # noqa: F401
from runner import judge
from runner.plan import Plan

# Titles only: the Judge never names an id, `Plan.split` allocates them.
SUB_ROWS = ["- [ ] Build the widgets mixin",
            "- [ ] Register the mixin in the composition root"]


class TestPlanSplit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cfg, self.plan, self.loop_dir, self.runtime = cfixtures.make_loop(self.tmp)
        self.plan_path = os.path.join(self.loop_dir, "LOOP_PLAN.md")

    def test_round_trip(self):
        new_ids = self.plan.split("T60", SUB_ROWS)
        self.plan.save()
        self.assertEqual(new_ids, ["T63", "T64"],
                         "the next free numeric ids, never T60a: serve.py's id "
                         "regex is \\bT\\d+\\b and a lettered id vanishes from "
                         "the dashboard")

        text = cfixtures.read(self.plan_path)
        self.assertIn("- [-] T60 ", text)
        self.assertIn("split→T63,T64", text)
        self.assertEqual(text.count("| split_of: T60"), 2)

        reloaded = Plan.load(self.plan_path)
        self.assertEqual([t.id for t in reloaded.tasks()],
                         ["T60", "T63", "T64", "T61", "T62"])
        by_id = dict((t.id, t) for t in reloaded.tasks())
        self.assertEqual(by_id["T60"].state, "skipped")
        self.assertEqual(by_id["T63"].state, "pending")
        self.assertEqual(by_id["T63"].segment, by_id["T60"].segment)
        eligible = [t.id for t in reloaded.eligible()]
        self.assertIn("T63", eligible)
        self.assertIn("T64", eligible)
        self.assertNotIn("T60", eligible)


class TestApplySplit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cfg, self.plan, self.loop_dir, self.runtime = cfixtures.make_loop(self.tmp)
        self.ctx = cfixtures.make_ctx(self.cfg, self.plan, self.loop_dir, self.runtime)
        self.contract = cfixtures.make_contract()
        cfixtures.write_contract(self.ctx, self.contract)
        cfixtures.git_init(self.cfg.worktree)

    def test_split_rewrites_the_plan_commits_and_emits(self):
        action = judge.apply(self.ctx, self.contract, judge._normalize({
            "decision": "split", "classification": "capability",
            "rationale": "resume did not finish; the task is two verifiable pieces",
            "alternatives": ["escalate to the next tier again"],
            "reversal": "revert the split commit",
            "changes": {"sub_rows": SUB_ROWS}}))
        self.assertEqual(action, "split")

        text = cfixtures.read(os.path.join(self.loop_dir, "LOOP_PLAN.md"))
        self.assertIn("split→T63,T64", text)
        self.assertNotIn("T60a", text)

        log = subprocess.check_output(
            ["git", "-C", self.cfg.worktree, "log", "-1", "--pretty=%B"]
        ).decode()
        self.assertIn("loop: split T60 → T63,T64", log)
        self.assertIn("Loop-Status: skipped", log)

        evs = [e for e in cfixtures.read_events(self.loop_dir) if e["type"] == "split"]
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["task"], "T60")
        self.assertEqual(evs[0]["into"], ["T63", "T64"])

        decisions = cfixtures.read(os.path.join(self.loop_dir, "LOOP_DECISIONS.md"))
        self.assertIn("— split (capability)", decisions)


if __name__ == "__main__":
    unittest.main()
