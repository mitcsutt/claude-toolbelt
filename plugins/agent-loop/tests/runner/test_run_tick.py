import _path  # noqa: F401
import json
import os
import subprocess
import tempfile
import unittest

from runner import claude_proc, config, contract as cmod, incidents, phases
from runner import plan as plan_mod, run, status, task_state, util
from runner.claude_proc import PhaseResult
from runner.events import EventLog, UsageLog

PLAN = """## Segment A: wiring
- [ ] T1: Add the parser
- [ ] T2: Add the writer | depends_on: T1
- [ ] T3: Rename the import | mechanical
"""


def sh(cwd, *args):
    subprocess.run(list(args), cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def block(obj):
    return "ok\n```json\n%s\n```" % json.dumps(obj)


class Replies(object):
    """Replays scripted phase replies and records the kwargs each phase got."""

    def __init__(self, by_phase):
        self.by_phase = dict(by_phase)
        self.calls = []

    def __call__(self, **kw):
        self.calls.append(kw)
        # claude_proc.run_phase is what emits role_start/role_end in production and
        # this stands in for it — without these two lines every role_start
        # assertion below passes vacuously against an empty list.
        kw["events"].emit("role_start", role=kw["role"], model=kw["model"],
                          desc=kw.get("desc", ""), tick=kw.get("tick", 0))
        script = self.by_phase.get(kw["phase"], [])
        reply = script.pop(0) if script else {}
        if reply.get("side_effect"):
            reply["side_effect"]()
        kw["events"].emit("role_end", role=kw["role"])
        return PhaseResult(phase=kw["phase"], model=kw["model"], rc=0,
                           killed=reply.get("killed", False),
                           stopped=reply.get("stopped", False),
                           session_id="sess-%s" % kw["phase"],
                           result_text=reply.get("text", ""),
                           usage_by_model=reply.get("usage", {}))


class Base(unittest.TestCase):
    def setUp(self):
        self.wt = tempfile.mkdtemp()
        sh(self.wt, "git", "init", "-q")
        sh(self.wt, "git", "config", "user.email", "loop@example.com")
        sh(self.wt, "git", "config", "user.name", "Loop")
        sh(self.wt, "git", "config", "commit.gpgsign", "false")
        os.makedirs(os.path.join(self.wt, "src"))
        with open(os.path.join(self.wt, "src", "a.ts"), "w") as f:
            f.write("export const a = 0\n")
        sh(self.wt, "git", "add", "-A")
        sh(self.wt, "git", "commit", "-q", "-m", "seed")

        self.loop_dir = os.path.join(self.wt, ".claude", "loop", "run")
        self.runtime = os.path.join(self.loop_dir, "runtime")
        os.makedirs(self.runtime)
        util.atomic_write(os.path.join(self.loop_dir, ".gitignore"),
                          "runtime/\nartifacts/\n")
        self.plan_path = os.path.join(self.loop_dir, "LOOP_PLAN.md")
        util.atomic_write(self.plan_path, PLAN)
        cfg_path = os.path.join(self.loop_dir, "LOOP_CONFIG.md")
        util.atomic_write(cfg_path,
                          "Worktree: %s\nVerification pipeline: lint test\n"
                          "Tiers: cheap=tiny standard=mid most-capable=big\n" % self.wt)
        self.cfg = config.load_config(cfg_path)
        self.events_path = os.path.join(self.loop_dir, "events.jsonl")
        self.h = run.Harness(
            cfg=self.cfg, plugin_root="/plugin", loop_dir=self.loop_dir,
            runtime_dir=self.runtime, worktree=self.wt, config_path=cfg_path,
            plan_path=self.plan_path,
            events=EventLog(self.events_path, os.path.join(self.runtime, "eventseq")),
            usage=UsageLog(os.path.join(self.loop_dir, "LOOP_USAGE.jsonl")),
            log=run.Log(os.path.join(self.loop_dir, "harness.log")),
            plan=plan_mod.Plan.load(self.plan_path),
            medic=incidents.MedicState())
        self._real = claude_proc.run_phase

    def tearDown(self):
        claude_proc.run_phase = self._real

    def patch(self, by_phase):
        rec = Replies(by_phase)
        claude_proc.run_phase = rec
        return rec

    def emitted(self, type_):
        out = []
        if not os.path.exists(self.events_path):
            return out
        with open(self.events_path) as f:
            for line in f:
                rec = json.loads(line)
                if rec["type"] == type_:
                    out.append(rec)
        return out

    def contract_writer(self, task_id="T1", **over):
        def write():
            data = {"task": task_id, "success_criteria": ["src/a.ts exports parse"],
                    "allow_list": ["src/a.ts"], "verification": ["true"],
                    "forbidden": [], "scout_notes": "n"}
            data.update(over)
            util.write_json(os.path.join(self.runtime, "sprint-%s.json" % task_id), data)
        return write

    def worker_edit(self, path="src/a.ts", text="export const parse = () => 1\n"):
        def write():
            full = os.path.join(self.wt, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as f:
                f.write(text)
        return write


class TestPauseState(Base):
    def test_it_reports_stop_pause_or_nothing(self):
        self.assertEqual("", run.pause_state(self.h))
        util.atomic_write(os.path.join(self.runtime, "PAUSE"), "")
        self.assertEqual("pause", run.pause_state(self.h))
        util.atomic_write(os.path.join(self.runtime, "STOP"), "")
        self.assertEqual("stop", run.pause_state(self.h))


class TestSandbox(Base):
    def test_a_stray_outside_allow_list_is_reverted(self):
        contract = cmod.Contract(task="T1", allow_list=["src/a.ts"])
        self.worker_edit()()
        self.worker_edit("src/b.ts", "stray\n")()
        stray = run.sandbox(self.h, contract)
        self.assertEqual(["src/b.ts"], stray)
        self.assertFalse(os.path.exists(os.path.join(self.wt, "src", "b.ts")))
        self.assertTrue(os.path.exists(os.path.join(self.wt, "src", "a.ts")))

    def test_the_loop_dir_is_never_treated_as_a_stray(self):
        contract = cmod.Contract(task="T1", allow_list=["src/a.ts"])
        util.atomic_write(os.path.join(self.loop_dir, "LOOP_LEARNINGS.md"), "x\n")
        self.assertEqual([], run.sandbox(self.h, contract))
        self.assertTrue(os.path.exists(os.path.join(self.loop_dir, "LOOP_LEARNINGS.md")))


class TestExecuteTickHappyPath(Base):
    def setUp(self):
        Base.setUp(self)
        self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer()}],
            "worker": [{"text": block({"status": "complete", "summary": "did it"}),
                        "side_effect": self.worker_edit()}],
            "evaluator": [{"text": block({"verdict": "PASS", "findings": [],
                                          "views": [], "summary": "good"})}],
            "learner": [{"text": block({"patterns": [], "log": "fine",
                                        "invariants": []})}]})
        self.outcome = run.execute_tick(self.h, 1, self.h.plan.task("T1"))

    def test_the_verdict_is_continue_and_the_work_is_committed(self):
        self.assertEqual("continue", self.outcome.verdict)
        self.assertEqual(40, len(self.outcome.sha))
        body = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=self.wt,
                              stdout=subprocess.PIPE).stdout.decode()
        self.assertIn("loop(T1): Add the parser", body)
        self.assertIn("Loop-Status: done", body)
        self.assertIn("Loop-Verification: lint=pass test=pass", body)
        self.assertIn("Loop-Files: src/a.ts", body)

    def test_the_row_is_marked_done_with_the_sha(self):
        reloaded = plan_mod.Plan.load(self.plan_path)
        self.assertEqual("done", reloaded.task("T1").state)
        self.assertEqual(self.outcome.sha[:7], reloaded.task("T1").sha)

    def test_a_task_status_event_carries_the_sha(self):
        ev = self.emitted("task_status")[0]
        self.assertEqual("T1", ev["id"])
        self.assertEqual("done", ev["status"])
        self.assertEqual(self.outcome.sha[:7], ev["sha"])

    def test_the_task_state_file_records_the_attempt(self):
        doc = util.read_json(os.path.join(self.runtime, "task-T1.json"))
        self.assertEqual("T1", doc["task"])
        self.assertEqual(1, doc["attempt"])
        self.assertEqual(1, len(doc["attempts"]))
        self.assertEqual("pass", doc["attempts"][0]["outcome"])
        self.assertEqual("standard", doc["attempts"][0]["tier"])
        self.assertEqual("runtime/sprint-T1.json", doc["contract"])
        phases_seen = [p["phase"] for p in doc["attempts"][0]["phase_results"]]
        self.assertEqual(["scout", "worker", "evaluator", "learner"], phases_seen)

    def test_the_recorded_phase_ends_on_the_last_transition(self):
        self.assertEqual("LEARN:done",
                         util.read_json(os.path.join(self.runtime, "task-T1.json"))["phase"])

    def test_nothing_writes_the_old_attempts_file(self):
        self.assertFalse(os.path.exists(os.path.join(self.runtime, "attempts-T1.json")))

    def test_the_learner_ran_after_the_commit(self):
        self.assertTrue(os.path.exists(os.path.join(self.loop_dir, "LOOP_LEARNINGS.md")))


class TestExecuteTickRetry(Base):
    def test_needs_work_re_dispatches_at_the_same_tier_with_the_findings(self):
        rec = self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer()}],
            "worker": [{"text": block({"status": "complete", "summary": "one"}),
                        "side_effect": self.worker_edit()},
                       {"text": block({"status": "complete", "summary": "two"}),
                        "side_effect": self.worker_edit(text="export const parse = () => 2\n")}],
            "evaluator": [{"text": block({"verdict": "NEEDS_WORK",
                                          "summary": "the parser is still a stub"})},
                          {"text": block({"verdict": "PASS", "summary": "good"})}],
            "learner": [{"text": block({"patterns": [], "log": "", "invariants": []})}]})
        outcome = run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        self.assertEqual("continue", outcome.verdict)
        workers = [c for c in rec.calls if c["phase"] == "worker"]
        self.assertEqual(["mid", "mid"], [c["model"] for c in workers])
        roles = [e["model"] for e in self.emitted("role_start") if e["role"] == "Worker"]
        self.assertEqual(["mid", "mid"], roles)
        self.assertNotIn("still a stub", workers[0]["prompt"])
        self.assertIn("the parser is still a stub", workers[1]["prompt"])
        self.assertEqual("retry", self.emitted("decision")[0]["decision"])
        doc = util.read_json(os.path.join(self.runtime, "task-T1.json"))
        self.assertEqual(["standard", "standard"], [a["tier"] for a in doc["attempts"]])
        self.assertEqual(["needs-work", "pass"], [a["outcome"] for a in doc["attempts"]])

    def test_a_second_failure_blocks_the_task(self):
        self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer()}],
            "worker": [{"text": block({"status": "partial", "summary": "one"}),
                        "side_effect": self.worker_edit()},
                       {"text": block({"status": "partial", "summary": "two"}),
                        "side_effect": self.worker_edit()}],
            "evaluator": [{"text": block({"verdict": "NEEDS_WORK", "summary": "thin"})},
                          {"text": block({"verdict": "NEEDS_WORK", "summary": "still thin"})}]})
        outcome = run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        self.assertEqual("continue", outcome.verdict)      # continue-independent
        reloaded = plan_mod.Plan.load(self.plan_path)
        self.assertEqual("blocked", reloaded.task("T1").state)
        self.assertEqual("blocked-upstream", reloaded.task("T2").state)

    def test_a_blocker_verdict_blocks_immediately_without_a_second_worker(self):
        rec = self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer()}],
            "worker": [{"text": block({"status": "complete", "summary": "one"}),
                        "side_effect": self.worker_edit()}],
            "evaluator": [{"text": block({"verdict": "BLOCKER",
                                          "summary": "the test was weakened"})}]})
        run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        self.assertEqual(1, len([c for c in rec.calls if c["phase"] == "worker"]))
        self.assertEqual("blocked", plan_mod.Plan.load(self.plan_path).task("T1").state)


class TestBlockedBookkeeping(Base):
    def setUp(self):
        Base.setUp(self)
        self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer()}],
            "worker": [{"text": block({"status": "complete", "summary": ""}),
                        "side_effect": self.worker_edit()}],
            "evaluator": [{"text": block({"verdict": "BLOCKER",
                                          "summary": "stubbed the requirement"})}]})
        self.outcome = run.execute_tick(self.h, 1, self.h.plan.task("T1"))

    def test_the_cleanup_entry_names_the_task_and_the_evidence(self):
        body = util.read_text(os.path.join(self.loop_dir, "LOOP_CLEANUP.md"))
        self.assertIn("T1", body)
        self.assertIn("stubbed the requirement", body)
        self.assertIn("decision", body.lower())

    def test_the_tree_is_returned_to_clean(self):
        with open(os.path.join(self.wt, "src", "a.ts")) as f:
            self.assertEqual("export const a = 0\n", f.read())

    def test_the_halt_commit_carries_the_trailer(self):
        body = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=self.wt,
                              stdout=subprocess.PIPE).stdout.decode()
        self.assertIn("Loop-Status: halted", body)

    def test_a_decision_event_records_the_deferral(self):
        ev = self.emitted("decision")[-1]
        self.assertEqual("T1", ev["task"])
        self.assertEqual("defer", ev["decision"])

    def test_halt_policy_turns_the_block_into_a_halt_verdict(self):
        # T3 is `| mechanical`, so the gate is its only judge: only a gate that
        # fails on both attempts drives it into handle_failure. With a passing
        # gate this task is a PASS and the assertion below never sees a halt.
        self.h.cfg.blocker_policy = "halt"
        self.h.plan = plan_mod.Plan.load(self.plan_path)
        self.patch({"scout": [{"text": block({"contract_path": "x", "notes": ""}),
                               "side_effect": self.contract_writer(
                                   "T3", verification=["exit 1"])}],
                    "worker": [{"text": block({"status": "complete", "summary": ""})},
                               {"text": block({"status": "complete", "summary": ""})}],
                    "evaluator": []})
        outcome = run.execute_tick(self.h, 2, self.h.plan.task("T3"))
        self.assertEqual("halt", outcome.verdict)
        self.assertEqual("blocked", plan_mod.Plan.load(self.plan_path).task("T3").state)


class TestContractRejection(Base):
    def test_an_invalid_contract_is_re_scouted_once_then_blocks(self):
        rec = self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer(allow_list=[])},
                      {"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer(allow_list=[])}],
            "worker": []})
        outcome = run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        self.assertEqual(2, len([c for c in rec.calls if c["phase"] == "scout"]))
        self.assertEqual(0, len([c for c in rec.calls if c["phase"] == "worker"]))
        self.assertEqual("blocked", plan_mod.Plan.load(self.plan_path).task("T1").state)
        self.assertIn("allow_list", util.read_text(
            os.path.join(self.loop_dir, "LOOP_CLEANUP.md")))
        self.assertEqual("continue", outcome.verdict)

    def test_the_second_scout_is_told_what_was_wrong(self):
        rec = self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer(allow_list=[])},
                      {"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer()}],
            "worker": [{"text": block({"status": "complete", "summary": ""}),
                        "side_effect": self.worker_edit()}],
            "evaluator": [{"text": block({"verdict": "PASS", "summary": ""})}],
            "learner": [{"text": block({"patterns": [], "log": "", "invariants": []})}]})
        run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        second = [c for c in rec.calls if c["phase"] == "scout"][1]
        self.assertIn("allow_list is empty", second["prompt"])


class TestGateFailure(Base):
    def test_a_failing_gate_re_dispatches_then_blocks(self):
        rec = self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer(verification=["exit 1"])}],
            "worker": [{"text": block({"status": "complete", "summary": ""}),
                        "side_effect": self.worker_edit()},
                       {"text": block({"status": "complete", "summary": ""}),
                        "side_effect": self.worker_edit()}],
            "evaluator": []})
        run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        self.assertEqual(2, len([c for c in rec.calls if c["phase"] == "worker"]))
        self.assertEqual(0, len([c for c in rec.calls if c["phase"] == "evaluator"]))
        self.assertEqual("blocked", plan_mod.Plan.load(self.plan_path).task("T1").state)
        self.assertIn("exit 1", util.read_text(
            os.path.join(self.loop_dir, "LOOP_CLEANUP.md")))


class TestMechanicalTask(Base):
    def test_a_mechanical_task_skips_the_evaluator(self):
        rec = self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer("T3")}],
            "worker": [{"text": block({"status": "complete", "summary": ""}),
                        "side_effect": self.worker_edit()}],
            "learner": [{"text": block({"patterns": [], "log": "", "invariants": []})}]})
        outcome = run.execute_tick(self.h, 1, self.h.plan.task("T3"))
        self.assertEqual("continue", outcome.verdict)
        self.assertEqual(0, len([c for c in rec.calls if c["phase"] == "evaluator"]))


class TestPauseBetweenPhases(Base):
    def test_pause_after_the_scout_stops_the_tick_cleanly(self):
        def pause_then_write():
            self.contract_writer()()
            util.atomic_write(os.path.join(self.runtime, "PAUSE"), "")
        rec = self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": pause_then_write}],
            "worker": []})
        outcome = run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        self.assertEqual("paused", outcome.verdict)
        self.assertEqual(0, len([c for c in rec.calls if c["phase"] == "worker"]))

    def test_a_stopped_worker_pauses_the_tick_without_running_the_gate(self):
        """A STOP mid-Worker ends the tick; the sandbox still ran (spec §6.4)."""
        rec = self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer()}],
            "worker": [{"killed": True, "stopped": True, "text": "",
                        "side_effect": self.worker_edit("src/stray.ts", "stray\n")}],
            "evaluator": []})
        outcome = run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        self.assertEqual("paused", outcome.verdict)
        self.assertEqual(0, len([c for c in rec.calls if c["phase"] == "evaluator"]))
        self.assertFalse(os.path.exists(os.path.join(self.wt, "src", "stray.ts")))


class TestPlanAndReviewTicks(Base):
    def test_a_plan_tick_commits_the_new_rows(self):
        util.atomic_write(self.plan_path, PLAN + "\n## Segment B: polish\n")
        self.h.plan = plan_mod.Plan.load(self.plan_path)

        def add_rows():
            with open(self.plan_path, "a") as f:
                f.write("- [ ] T4: Tidy the exports\n")
        self.patch({"planner": [{"text": block({"tasks_added": 1}),
                                 "side_effect": add_rows}]})
        outcome = run.plan_tick(self.h, 1, "Segment B: polish")
        self.assertEqual("continue", outcome.verdict)
        body = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=self.wt,
                              stdout=subprocess.PIPE).stdout.decode()
        self.assertIn("loop: plan Segment B: polish", body)
        self.assertEqual("Segment B: polish",
                         plan_mod.Plan.load(self.plan_path).task("T4").segment)

    def test_a_plan_tick_that_adds_nothing_is_stuck_not_a_loop(self):
        util.atomic_write(self.plan_path, PLAN + "\n## Segment B: polish\n")
        self.h.plan = plan_mod.Plan.load(self.plan_path)
        self.patch({"planner": [{"text": block({"tasks_added": 0})}]})
        self.assertEqual("halt", run.plan_tick(self.h, 1, "Segment B: polish").verdict)

    def test_a_review_tick_stamps_the_segment_and_appends_follow_ups(self):
        util.atomic_write(self.plan_path,
                          "## Segment A: wiring\n- [x] T1: Add the parser\n")
        self.h.plan = plan_mod.Plan.load(self.plan_path)
        self.patch({"reviewer": [{"text": block({"findings": [
            {"severity": "must-fix", "title": "no tests", "detail": "none at all",
             "follow_up_row": "- [ ] T9: Add tests for the parser"},
            {"severity": "nit", "title": "naming", "detail": "meh",
             "follow_up_row": "- [ ] T10: rename"}]})}]})
        outcome = run.review_tick(self.h, 1, "Segment A: wiring")
        self.assertEqual("continue", outcome.verdict)
        reloaded = plan_mod.Plan.load(self.plan_path)
        self.assertTrue(reloaded.segments()[0].reviewed_sha)
        self.assertIsNotNone(reloaded.task("T9"))
        self.assertIsNone(reloaded.task("T10"))          # nits are not rows
        body = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=self.wt,
                              stdout=subprocess.PIPE).stdout.decode()
        self.assertIn("Loop-Status: reviewed", body)

    def test_a_follow_up_row_that_reuses_a_live_id_is_renumbered(self):
        """The Reviewer prompt hands it `T<n>`: nothing tells it which n is free.

        Two rows sharing an id is a corrupt plan — `Plan.task()` returns the
        first, so every later set_state flips the wrong row.
        """
        util.atomic_write(self.plan_path,
                          "## Segment A: wiring\n- [x] T1: Add the parser\n"
                          "- [x] T2: Add the writer\n")
        self.h.plan = plan_mod.Plan.load(self.plan_path)
        self.patch({"reviewer": [{"text": block({"findings": [
            {"severity": "must-fix", "follow_up_row": "- [ ] T2: Retest the writer"},
            {"severity": "should-fix", "follow_up_row": "- [ ] no id at all here"}]})}]})
        run.review_tick(self.h, 1, "Segment A: wiring")
        reloaded = plan_mod.Plan.load(self.plan_path)
        ids = [t.id for t in reloaded.tasks()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(["T1", "T2", "T3", "T4"], ids)
        self.assertEqual("Retest the writer", reloaded.task("T3").title)
        self.assertEqual("no id at all here", reloaded.task("T4").title)


class TestBootReconcile(Base):
    """Spec §14: a `[~]` row means a harness died inside the task. eligible()
    only returns pending rows, so without this the plan reads as `stuck` forever."""

    def mark_doing(self, task_id="T1"):
        self.h.plan.set_state(task_id, "doing")
        self.h.plan.save()
        self.h.plan = plan_mod.Plan.load(self.plan_path)

    def state_for(self, task_id="T1"):
        state = task_state.TaskState.load(self.runtime, task_id)
        state.begin_attempt("standard")
        return state

    def test_a_clean_plan_is_left_alone(self):
        run.boot_reconcile(self.h)
        self.assertEqual("", self.h.resume_task)
        self.assertEqual("pending", plan_mod.Plan.load(self.plan_path).task("T1").state)

    def test_a_worker_complete_task_resumes_at_the_gate(self):
        self.mark_doing()
        self.contract_writer()()
        state = self.state_for()
        state.begin_phase("WORK")
        state.end_phase(PhaseResult(phase="worker", model="mid", rc=0,
                                    session_id="sess-w"))
        run.boot_reconcile(self.h)
        self.assertEqual(("T1", "gate"), (self.h.resume_task, self.h.resume_phase))
        self.assertEqual("pending", plan_mod.Plan.load(self.plan_path).task("T1").state)

        self.worker_edit()()                      # the killed Worker's work
        rec = self.patch({
            "evaluator": [{"text": block({"verdict": "PASS", "findings": [],
                                          "views": [], "summary": "good"})}],
            "learner": [{"text": block({"patterns": [], "log": "", "invariants": []})}]})
        outcome = run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        self.assertEqual("continue", outcome.verdict)
        self.assertEqual([], [c for c in rec.calls if c["phase"] in ("scout", "worker")])

    def test_a_task_killed_inside_work_re_dispatches_with_its_checkpoint(self):
        self.mark_doing()
        self.contract_writer()()
        util.write_json(os.path.join(self.runtime, "worker-result.json"),
                        {"task": "T1", "status": "partial", "files_touched": ["src/a.ts"],
                         "summary": "half done",
                         "checkpoint": "renamed the export, tests not updated yet",
                         "next_steps": ["update the tests"]})
        self.state_for().begin_phase("WORK")
        run.boot_reconcile(self.h)
        self.assertEqual(("T1", "wrapup"), (self.h.resume_task, self.h.resume_phase))

        rec = self.patch({
            "worker": [{"text": block({"status": "complete", "summary": "finished it"}),
                        "side_effect": self.worker_edit()}],
            "evaluator": [{"text": block({"verdict": "PASS", "findings": [],
                                          "views": [], "summary": "good"})}],
            "learner": [{"text": block({"patterns": [], "log": "", "invariants": []})}]})
        run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        workers = [c for c in rec.calls if c["phase"] == "worker"]
        self.assertEqual(1, len(workers))
        self.assertIn("renamed the export", workers[0]["prompt"])
        self.assertEqual([], [c for c in rec.calls if c["phase"] == "scout"])

    def test_no_state_file_keeps_allow_list_work_reverts_strays_and_re_scouts(self):
        self.mark_doing()
        self.contract_writer()()                 # allow_list = ["src/a.ts"]
        self.worker_edit()()                     # inside it: kept
        self.worker_edit("src/stray.ts", "stray\n")()   # outside it: reverted
        run.boot_reconcile(self.h)
        self.assertEqual(("T1", "scout"), (self.h.resume_task, self.h.resume_phase))
        self.assertFalse(os.path.exists(os.path.join(self.wt, "src", "stray.ts")))
        with open(os.path.join(self.wt, "src", "a.ts")) as f:
            self.assertIn("parse", f.read())
        self.assertIn("boot-reconcile",
                      util.read_text(os.path.join(self.loop_dir, "harness.log")))
        self.assertEqual("boot-reconcile", self.emitted("decision")[0]["classification"])

    def test_no_state_file_and_no_contract_reverts_everything_outside_the_loop_dir(self):
        self.mark_doing()
        self.worker_edit("src/whatever.ts", "half a task\n")()
        run.boot_reconcile(self.h)
        self.assertFalse(os.path.exists(os.path.join(self.wt, "src", "whatever.ts")))
        self.assertEqual("scout", self.h.resume_phase)

    def test_a_second_in_flight_row_is_reset_to_pending(self):
        self.mark_doing("T1")
        self.mark_doing("T2")
        run.boot_reconcile(self.h)
        self.assertEqual("pending", plan_mod.Plan.load(self.plan_path).task("T2").state)

    def test_the_resume_answer_is_consumed_once(self):
        self.h.resume_task = "T1"
        self.h.resume_phase = "gate"
        self.assertEqual("gate", self.h.take_resume("T1"))
        self.assertEqual("", self.h.take_resume("T1"))

    def test_the_resume_answer_belongs_to_one_task_only(self):
        self.h.resume_task = "T1"
        self.h.resume_phase = "gate"
        self.assertEqual("", self.h.take_resume("T2"))


class TestFailureSignature(Base):
    def test_it_keys_on_kind_phase_and_task_not_on_the_return_code(self):
        self.assertEqual(run.failure_signature("gate-failed", "gate", "T1"),
                         run.failure_signature("gate-failed", "gate", "T1"))
        self.assertNotEqual(run.failure_signature("gate-failed", "gate", "T1"),
                            run.failure_signature("gate-failed", "gate", "T2"))
        self.assertNotEqual(run.failure_signature("needs-work", "evaluator", "T1"),
                            run.failure_signature("gate-failed", "gate", "T1"))


if __name__ == "__main__":
    unittest.main()
