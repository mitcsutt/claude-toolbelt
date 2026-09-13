import _path  # noqa: F401
import json
import os
import tempfile
import unittest

from runner import claude_proc, config, contract as cmod, phases, plan as planmod, util
from runner.claude_proc import PhaseResult, Usage
from runner.events import EventLog

CONFIG = """Worktree: %s
Verification pipeline: lint test
Tiers: cheap=tiny standard=mid most-capable=big
Spec: %s
"""

PLAN = """## Segment A: wiring
- [ ] T3: Add the parser | depends_on: T1
- [ ] T4: Copy the header | copy_of: T3
- [ ] T5: Rename the import | mechanical
- [ ] T6: Design the cache | complex
"""


def block(obj):
    return "did it\n```json\n%s\n```" % json.dumps(obj)


class Recorder(object):
    """Stand-in for claude_proc.run_phase; records kwargs, replays scripted replies."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, **kw):
        self.calls.append(kw)
        reply = self.replies.pop(0) if self.replies else {}
        side_effect = reply.get("side_effect")
        if side_effect:
            side_effect()
        return PhaseResult(phase=kw["phase"], model=kw["model"], rc=0,
                           killed=reply.get("killed", False),
                           stopped=reply.get("stopped", False),
                           session_id="sess-1",
                           result_text=reply.get("text", ""),
                           usage_by_model=reply.get("usage", {}))


class Base(unittest.TestCase):
    def setUp(self):
        self.wt = tempfile.mkdtemp()
        self.loop_dir = os.path.join(self.wt, ".claude", "loop", "run")
        self.runtime = os.path.join(self.loop_dir, "runtime")
        os.makedirs(self.runtime)
        self.spec = os.path.join(self.wt, "spec.md")
        with open(self.spec, "w") as f:
            f.write("# Spec\n\nT3 parses the plan rows.\nT9 is unrelated.\n")
        cfg_path = os.path.join(self.loop_dir, "LOOP_CONFIG.md")
        with open(cfg_path, "w") as f:
            f.write(CONFIG % (self.wt, self.spec))
        plan_path = os.path.join(self.loop_dir, "LOOP_PLAN.md")
        with open(plan_path, "w") as f:
            f.write(PLAN)
        self.cfg = config.load_config(cfg_path)
        self.plan = planmod.Plan.load(plan_path)
        self.events = EventLog(os.path.join(self.loop_dir, "events.jsonl"),
                               os.path.join(self.runtime, "eventseq"))
        self._real = claude_proc.run_phase

    def tearDown(self):
        claude_proc.run_phase = self._real

    def ctx(self, task_id="T3", attempt=1):
        return phases.TickContext(cfg=self.cfg, plan=self.plan,
                                  task=self.plan.task(task_id),
                                  loop_dir=self.loop_dir, runtime_dir=self.runtime,
                                  events=self.events, tick=1, attempt=attempt)

    def patch(self, replies):
        rec = Recorder(replies)
        claude_proc.run_phase = rec
        return rec

    def write_contract(self, task_id="T3", **over):
        data = {"task": task_id, "success_criteria": ["src/a.ts exports parse"],
                "allow_list": ["src/a.ts"], "verification": ["true"],
                "forbidden": [], "scout_notes": "inline"}
        data.update(over)
        path = os.path.join(self.runtime, "sprint-%s.json" % task_id)
        util.write_json(path, data)
        return path


class TestTiers(Base):
    def test_worker_tier_is_the_configured_one_and_never_a_ladder(self):
        self.assertEqual("standard", phases.worker_tier(self.cfg))
        self.cfg.role_tiers["worker"] = "cheap"
        self.assertEqual("cheap", phases.worker_tier(self.cfg))

    def test_an_explicit_tier_overrides_the_configured_one(self):
        # The seam plan C's Judge uses (changes.tier). Nothing in plan A calls it.
        self.assertEqual("most-capable", phases.worker_tier(self.cfg, "most-capable"))

    def test_a_garbage_tier_from_either_source_falls_back_to_standard(self):
        self.cfg.role_tiers["worker"] = "turbo"
        self.assertEqual("standard", phases.worker_tier(self.cfg))
        self.assertEqual("standard", phases.worker_tier(self.cfg, "turbo"))

    def test_evaluator_tier_is_governed_by_the_task_class(self):
        self.assertEqual("standard", phases.evaluator_tier(self.cfg, self.plan.task("T3")))
        self.assertEqual("most-capable", phases.evaluator_tier(self.cfg, self.plan.task("T6")))

    def test_a_configured_evaluator_tier_is_the_ceiling_for_complex_only(self):
        self.cfg.role_tiers["evaluator"] = "most-capable"
        self.assertEqual("standard", phases.evaluator_tier(self.cfg, self.plan.task("T3")))
        self.cfg.role_tiers["evaluator"] = "standard"
        self.assertEqual("standard", phases.evaluator_tier(self.cfg, self.plan.task("T6")))


class TestScout(Base):
    def test_it_loads_and_validates_the_contract_the_scout_wrote(self):
        rec = self.patch([{"text": block({"contract_path": "x", "notes": "ok"}),
                           "side_effect": self.write_contract}])
        res, contract, errors = phases.run_scout(self.ctx())
        self.assertEqual([], errors)
        self.assertEqual("T3", contract.task)
        self.assertEqual(0, res.rc)
        self.assertEqual("mid", rec.calls[0]["model"])
        self.assertEqual("scout", rec.calls[0]["phase"])

    def test_the_prompt_carries_the_task_row_the_digest_and_the_spec_lines(self):
        util.atomic_write(os.path.join(self.loop_dir, "LOOP_LEARNINGS.md"),
                          "## Patterns\n- always run pnpm install first\n\n## Log\n")
        rec = self.patch([{"text": block({"contract_path": "x", "notes": ""}),
                           "side_effect": self.write_contract}])
        phases.run_scout(self.ctx())
        prompt = rec.calls[0]["prompt"]
        self.assertIn("T3: Add the parser", prompt)
        self.assertIn("always run pnpm install first", prompt)
        self.assertIn("T3 parses the plan rows", prompt)
        self.assertNotIn("{{", prompt)

    def test_validation_errors_are_reported_not_raised(self):
        self.patch([{"text": block({"contract_path": "x", "notes": ""}),
                     "side_effect": lambda: self.write_contract(allow_list=[])}])
        _, contract, errors = phases.run_scout(self.ctx())
        self.assertIsNotNone(contract)
        self.assertTrue(any("allow_list" in e for e in errors))

    def test_a_missing_contract_file_is_an_error_not_a_crash(self):
        self.patch([{"text": block({"contract_path": "x", "notes": ""})}])
        _, contract, errors = phases.run_scout(self.ctx())
        self.assertIsNone(contract)
        self.assertEqual(1, len(errors))
        self.assertIn("sprint-T3.json", errors[0])

    def test_a_second_attempt_injects_the_previous_errors(self):
        rec = self.patch([{"text": block({"contract_path": "x", "notes": ""}),
                           "side_effect": self.write_contract}])
        phases.run_scout(self.ctx(attempt=2), validation_errors=["allow_list is empty"])
        self.assertIn("allow_list is empty", rec.calls[0]["prompt"])

    def test_the_copy_rule_reaches_validation(self):
        self.patch([{"text": block({"contract_path": "x", "notes": ""}),
                     "side_effect": lambda: self.write_contract("T4")}])
        _, _, errors = phases.run_scout(self.ctx("T4"))
        self.assertTrue(any("fidelity_source" in e for e in errors))


class TestReask(Base):
    def test_a_malformed_reply_is_re_asked_once_with_the_parse_error(self):
        rec = self.patch([{"text": "no json here",
                           "usage": {"m": Usage(cost_usd=1.0)}},
                          {"text": block({"contract_path": "x", "notes": ""}),
                           "usage": {"m": Usage(cost_usd=2.0)},
                           "side_effect": self.write_contract}])
        res, contract, errors = phases.run_scout(self.ctx())
        self.assertEqual(2, len(rec.calls))
        self.assertIn("JSON", rec.calls[1]["prompt"])
        self.assertIsNotNone(contract)
        self.assertEqual(3.0, res.usage_by_model["m"].cost_usd)

    def test_two_malformed_replies_give_up(self):
        rec = self.patch([{"text": "nope"}, {"text": "still nope"}])
        res, data = phases.run_reviewer(self.ctx(), "Segment A: wiring", "diff")
        self.assertEqual(2, len(rec.calls))
        self.assertEqual({}, data)

    def test_a_killed_phase_is_not_re_asked(self):
        rec = self.patch([{"text": "", "killed": True}])
        phases.run_reviewer(self.ctx(), "Segment A: wiring", "diff")
        self.assertEqual(1, len(rec.calls))

    def test_a_stopped_phase_is_not_re_asked(self):
        """A STOP must spend nothing more; a re-ask is a second subprocess."""
        rec = self.patch([{"text": "", "killed": True, "stopped": True}])
        phases.run_reviewer(self.ctx(), "Segment A: wiring", "diff")
        self.assertEqual(1, len(rec.calls))


class TestStopCheckIsWired(Base):
    """Spec §4.4: STOP has to reach the phase in flight, and only run_phase's
    own watchdog can do it — the harness never holds the child's pid."""

    def test_every_dispatch_carries_a_stop_check_that_reads_runtime_stop(self):
        rec = self.patch([{"text": block({"findings": []})}])
        phases.run_reviewer(self.ctx(), "Segment A: wiring", "diff")
        check = rec.calls[0]["stop_check"]
        self.assertFalse(check())
        open(os.path.join(self.runtime, "STOP"), "w").close()
        self.assertTrue(check())


class TestWorker(Base):
    def test_a_re_attempt_dispatches_at_the_SAME_tier(self):
        self.write_contract()
        rec = self.patch([{"text": block({"status": "complete", "summary": "done"})}])
        contract = cmod.load_contract(os.path.join(self.runtime, "sprint-T3.json"))
        res, data = phases.run_worker(self.ctx(attempt=2), contract)
        self.assertEqual("mid", rec.calls[0]["model"])
        self.assertEqual("complete", data["status"])
        self.assertEqual(0, res.rc)

    def test_an_explicit_tier_reaches_the_command_line(self):
        self.write_contract()
        rec = self.patch([{"text": block({"status": "complete", "summary": ""})}])
        contract = cmod.load_contract(os.path.join(self.runtime, "sprint-T3.json"))
        phases.run_worker(self.ctx(attempt=2), contract, tier="most-capable")
        self.assertEqual("big", rec.calls[0]["model"])

    def test_the_previous_findings_are_injected_on_a_re_dispatch(self):
        self.write_contract()
        contract = cmod.load_contract(os.path.join(self.runtime, "sprint-T3.json"))
        rec = self.patch([{"text": block({"status": "complete", "summary": ""})}])
        phases.run_worker(self.ctx(), contract)
        self.assertIn("first attempt", rec.calls[0]["prompt"])
        rec = self.patch([{"text": block({"status": "complete", "summary": ""})}])
        phases.run_worker(self.ctx(attempt=2), contract,
                          findings="the copy is a six-line TODO comment")
        self.assertIn("six-line TODO comment", rec.calls[0]["prompt"])

    def test_the_contract_is_the_only_context_it_gets(self):
        self.write_contract()
        rec = self.patch([{"text": block({"status": "complete", "summary": ""})}])
        contract = cmod.load_contract(os.path.join(self.runtime, "sprint-T3.json"))
        phases.run_worker(self.ctx(), contract)
        prompt = rec.calls[0]["prompt"]
        self.assertIn("src/a.ts", prompt)
        self.assertNotIn("Segment A", prompt)

    def test_the_tdd_note_appears_only_when_configured(self):
        self.write_contract()
        contract = cmod.load_contract(os.path.join(self.runtime, "sprint-T3.json"))
        rec = self.patch([{"text": block({"status": "complete", "summary": ""})}])
        phases.run_worker(self.ctx(), contract)
        self.assertNotIn("test-driven-development", rec.calls[0]["prompt"])
        self.cfg.tdd_mode = "tdd-per-task"
        rec = self.patch([{"text": block({"status": "complete", "summary": ""})}])
        phases.run_worker(self.ctx(), contract)
        self.assertIn("test-driven-development", rec.calls[0]["prompt"])

    def test_wrapup_and_resume_are_plan_c(self):
        self.write_contract()
        contract = cmod.load_contract(os.path.join(self.runtime, "sprint-T3.json"))
        self.patch([{"text": ""}])
        with self.assertRaises(NotImplementedError):
            phases.run_worker(self.ctx(), contract, wrapup=True)
        with self.assertRaises(NotImplementedError):
            phases.run_worker(self.ctx(), contract, resume_session="sess-1")


class TestEvaluator(Base):
    def test_the_diff_gate_and_screenshots_reach_the_prompt(self):
        self.write_contract()
        contract = cmod.load_contract(os.path.join(self.runtime, "sprint-T3.json"))
        rec = self.patch([{"text": block({"verdict": "PASS", "findings": [],
                                          "views": [], "summary": "fine"})}])
        res, data = phases.run_evaluator(self.ctx(), contract, "--- a/src/a.ts",
                                         "$ true\nrc=0", ["shots/a.png"])
        prompt = rec.calls[0]["prompt"]
        self.assertIn("--- a/src/a.ts", prompt)
        self.assertIn("rc=0", prompt)
        self.assertIn("shots/a.png", prompt)
        self.assertEqual("PASS", data["verdict"])

    def test_an_unknown_verdict_is_normalised_to_needs_work(self):
        self.write_contract()
        contract = cmod.load_contract(os.path.join(self.runtime, "sprint-T3.json"))
        self.patch([{"text": block({"verdict": "looks good", "summary": ""})}])
        _, data = phases.run_evaluator(self.ctx(), contract, "", "", [])
        self.assertEqual("NEEDS_WORK", data["verdict"])

    def test_a_malformed_verdict_twice_is_needs_work_with_a_reason(self):
        self.write_contract()
        contract = cmod.load_contract(os.path.join(self.runtime, "sprint-T3.json"))
        self.patch([{"text": "nope"}, {"text": "nope"}])
        _, data = phases.run_evaluator(self.ctx(), contract, "", "", [])
        self.assertEqual("NEEDS_WORK", data["verdict"])
        self.assertIn("malformed", data["summary"])


class TestLearner(Base):
    def test_it_writes_evidenced_patterns_and_logs_the_rest(self):
        self.write_contract()
        contract = cmod.load_contract(os.path.join(self.runtime, "sprint-T3.json"))
        self.patch([{"text": block({
            "patterns": [{"rule": "run install first", "evidence": "gate-T3-1.txt"},
                         {"rule": "the layouts were genuinely copied", "evidence": ""}],
            "log": "T3 went fine",
            "invariants": [{"rule": "describe blocks are PascalCase",
                            "check": "! grep -n 'describe(.[a-z]' src/*.test.ts"}]})}])
        phases.run_learner(self.ctx(), contract, "$ true\nrc=0", "PASS")
        text = util.read_text(os.path.join(self.loop_dir, "LOOP_LEARNINGS.md"))
        self.assertIn("run install first", text.split("## Invariants")[0])
        self.assertNotIn("genuinely copied", text.split("## Invariants")[0])
        self.assertIn("genuinely copied", text)
        self.assertIn("PascalCase", text)
        self.assertIn("T3 went fine", text)

    def test_the_patterns_digest_is_capped_at_2kb_oldest_first(self):
        old = "\n".join("- rule %03d padded %s" % (i, "x" * 60) for i in range(60))
        util.atomic_write(os.path.join(self.loop_dir, "LOOP_LEARNINGS.md"),
                          "## Patterns\n%s\n\n## Invariants\n\n## Log\n" % old)
        phases.apply_learnings(self.loop_dir, {
            "patterns": [{"rule": "the newest rule", "evidence": "gate-T3-1.txt"}],
            "log": "", "invariants": []})
        text = util.read_text(os.path.join(self.loop_dir, "LOOP_LEARNINGS.md"))
        digest = text.split("## Invariants")[0]
        self.assertLessEqual(len(digest.encode("utf-8")), 2048 + 64)
        self.assertIn("the newest rule", digest)
        self.assertNotIn("rule 000", digest)

    def test_duplicate_rules_and_invariants_are_not_re_added(self):
        for _ in range(3):
            phases.apply_learnings(self.loop_dir, {
                "patterns": [{"rule": "same rule", "evidence": "gate-T3-1.txt"}],
                "log": "", "invariants": [{"rule": "same inv", "check": "true"}]})
        text = util.read_text(os.path.join(self.loop_dir, "LOOP_LEARNINGS.md"))
        self.assertEqual(1, text.count("same rule"))
        self.assertEqual(1, text.count("same inv"))

    def test_the_digest_reader_returns_only_the_patterns_section(self):
        util.atomic_write(os.path.join(self.loop_dir, "LOOP_LEARNINGS.md"),
                          "## Patterns\n- a rule\n\n## Log\n- a very long log entry\n")
        digest = phases.learnings_digest(self.loop_dir)
        self.assertIn("a rule", digest)
        self.assertNotIn("very long log entry", digest)

    def test_a_missing_learnings_file_is_created(self):
        phases.apply_learnings(self.loop_dir, {"patterns": [], "log": "first",
                                               "invariants": []})
        self.assertTrue(os.path.exists(os.path.join(self.loop_dir, "LOOP_LEARNINGS.md")))


class TestPlannerAndReviewer(Base):
    def test_planner_runs_at_the_top_tier_with_the_segment_named(self):
        rec = self.patch([{"text": block({"tasks_added": 4})}])
        res, data = phases.run_planner(self.ctx(), "Segment B: polish")
        self.assertEqual("big", rec.calls[0]["model"])
        self.assertIn("Segment B: polish", rec.calls[0]["prompt"])
        self.assertEqual(4, data["tasks_added"])

    def test_reviewer_gets_the_segment_diff_and_its_task_rows(self):
        rec = self.patch([{"text": block({"findings": [
            {"severity": "must-fix", "title": "t", "detail": "d",
             "follow_up_row": "- [ ] T9: fix it"}]})}])
        _, data = phases.run_reviewer(self.ctx(), "Segment A: wiring", "--- a/src/a.ts")
        prompt = rec.calls[0]["prompt"]
        self.assertIn("--- a/src/a.ts", prompt)
        self.assertIn("T3: Add the parser", prompt)
        self.assertEqual(1, len(data["findings"]))


if __name__ == "__main__":
    unittest.main()
