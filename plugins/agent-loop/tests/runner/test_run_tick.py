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

# Every test here builds a real throwaway git repo and then deliberately destroys
# things inside it. It must never be able to reach the real worktree: honour the
# session scratchpad when one is exported, and fall back to the system temp dir.
SCRATCH = os.environ.get("CLAUDE_SCRATCHPAD") or None
if SCRATCH and not os.path.isdir(SCRATCH):
    SCRATCH = None

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
        self.wt = tempfile.mkdtemp(dir=SCRATCH)
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

    def judge_says(self, decision="defer", classification="open", n=1, **over):
        """`n` scripted Judge replies. Every failure path runs a Judge now, so a
        test that drives one has to say what it decided."""
        payload = {"decision": decision, "classification": classification,
                   "rationale": "scripted for the test",
                   "instruction": "", "alternatives": ["the other thing"],
                   "reversal": "undo it", "changes": {}}
        payload.update(over)
        return [{"text": block(payload)} for _ in range(n)]

    def rename(self, src, dst):
        """A side effect that renames one tracked file to another name."""
        def go():
            subprocess.run(["git", "mv", src, dst], cwd=self.wt, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return go

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
            "learner": [{"text": block({"patterns": [], "log": "", "invariants": []})}],
            "judge": self.judge_says("retry", "capability")})
        outcome = run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        self.assertEqual("continue", outcome.verdict)
        workers = [c for c in rec.calls if c["phase"] == "worker"]
        self.assertEqual(["mid", "mid"], [c["model"] for c in workers])
        roles = [e["model"] for e in self.emitted("role_start") if e["role"] == "Worker"]
        self.assertEqual(["mid", "mid"], roles)
        self.assertNotIn("still a stub", workers[0]["prompt"])
        self.assertIn("the parser is still a stub", workers[1]["prompt"])
        self.assertEqual("retry", self.emitted("decision")[0]["decision"])
        self.assertEqual("capability", self.emitted("decision")[0]["classification"],
                         "a real §7 classification, because a Judge made the call")
        doc = util.read_json(os.path.join(self.runtime, "task-T1.json"))
        self.assertEqual(["standard", "standard"], [a["tier"] for a in doc["attempts"]],
                         "the Judge named no tier, so the same one runs again")
        self.assertEqual(["needs-work:the parser is still a stub", "pass"],
                         [a["outcome"] for a in doc["attempts"]])

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
                                          "summary": "stubbed the requirement"})}],
            "judge": self.judge_says("defer", "capability")})
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
                    "evaluator": [],
                    "judge": self.judge_says("defer", "capability")})
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
            "worker": [],
            "judge": self.judge_says("defer", "self-imposed")})
        outcome = run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        self.assertEqual(2, len([c for c in rec.calls if c["phase"] == "scout"]))
        self.assertEqual(0, len([c for c in rec.calls if c["phase"] == "worker"]))
        self.assertEqual(1, len([c for c in rec.calls if c["phase"] == "judge"]),
                         "a Scout that wrote an impossible contract is the "
                         "self-imposed case §7 exists for; the loop does not "
                         "mark [!] on its own")
        self.assertEqual("blocked", plan_mod.Plan.load(self.plan_path).task("T1").state)
        self.assertIn("allow_list", util.read_text(
            os.path.join(self.loop_dir, "LOOP_CLEANUP.md")))
        self.assertEqual("continue", outcome.verdict)

    def test_a_retry_on_an_invalid_contract_still_writes_a_cleanup_entry(self):
        """When the Judge answers retry/resume to an invalid contract the harness
        overrides to a deferral (the Scout has had both tries). That override must
        still write the LOOP_CLEANUP entry — otherwise a task is blocked with an
        empty human follow-up list."""
        rec = self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer(allow_list=[])},
                      {"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer(allow_list=[])}],
            "worker": [],
            "judge": self.judge_says("retry", "capability")})
        outcome = run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        self.assertEqual(1, len([c for c in rec.calls if c["phase"] == "judge"]))
        self.assertEqual("blocked",
                         plan_mod.Plan.load(self.plan_path).task("T1").state)
        cleanup = util.read_text(os.path.join(self.loop_dir, "LOOP_CLEANUP.md"))
        self.assertIn("T1", cleanup,
                      "the blocked task must name itself in LOOP_CLEANUP")
        self.assertIn("allow_list", cleanup,
                      "the cleanup entry must carry the validation evidence")
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
            "evaluator": [],
            "judge": self.judge_says("retry", "capability")
                     + self.judge_says("defer", "capability")})
        run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        self.assertEqual(2, len([c for c in rec.calls if c["phase"] == "worker"]))
        self.assertEqual(0, len([c for c in rec.calls if c["phase"] == "evaluator"]))
        self.assertEqual("blocked", plan_mod.Plan.load(self.plan_path).task("T1").state)
        cleanup = util.read_text(os.path.join(self.loop_dir, "LOOP_CLEANUP.md"))
        self.assertIn("exit 1", cleanup,
                      "the entry carries what actually failed, not only the "
                      "Judge's summary of it")
        # The second failure has the same signature as the first, so the Judge is
        # told so rather than being asked the same question twice over (spec §7).
        judges = [c for c in rec.calls if c["phase"] == "judge"]
        self.assertEqual(2, len(judges))
        self.assertIn("same failure signature", judges[1]["prompt"])


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
            "learner": [{"text": block({"patterns": [], "log": "", "invariants": []})}],
            "judge": self.judge_says("retry", "capability")})
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

    def test_no_state_file_asks_the_judge_which_keeps_the_tree_on_a_retry(self):
        """Spec §14 decision 13: nothing records how far the task got, so the
        ambiguous step goes to an agent. `retry` keeps every uncommitted change
        as a starting point and restarts from SCOUT."""
        self.mark_doing()
        self.contract_writer()()                 # allow_list = ["src/a.ts"]
        self.worker_edit()()
        self.worker_edit("src/stray.ts", "stray\n")()
        self.patch({"judge": self.judge_says("retry", "capability")})
        run.boot_reconcile(self.h)
        self.assertEqual(("T1", "scout"), (self.h.resume_task, self.h.resume_phase))
        self.assertTrue(os.path.exists(os.path.join(self.wt, "src", "stray.ts")),
                        "retry keeps the tree; only revert-and-retry throws it away")
        with open(os.path.join(self.wt, "src", "a.ts")) as f:
            self.assertIn("parse", f.read())
        self.assertIn("boot-reconcile",
                      util.read_text(os.path.join(self.loop_dir, "harness.log")))
        ev = self.emitted("boot_reconcile")[0]
        self.assertEqual("retry", ev["decision"])
        self.assertEqual("T1", ev["task"])

    def test_the_judge_sees_the_dirty_tree_before_anything_is_reverted(self):
        """The whole point of asking: plan A reverted the strays and THEN
        returned, so a Judge in its place would have judged a cleaned tree."""
        self.mark_doing()
        self.contract_writer()()
        self.worker_edit("src/stray.ts", "stray\n")()
        rec = self.patch({"judge": self.judge_says("revert-and-retry", "open")})
        run.boot_reconcile(self.h)
        prompt = [c for c in rec.calls if c["phase"] == "judge"][0]["prompt"]
        self.assertIn("src/stray.ts", prompt)
        self.assertIn("outside allow_list", prompt)
        self.assertFalse(os.path.exists(os.path.join(self.wt, "src", "stray.ts")),
                         "and the decision it made is then carried out")
        self.assertEqual("scout", self.h.resume_phase)

    def test_a_resume_decision_re_dispatches_the_worker_over_the_kept_tree(self):
        self.mark_doing()
        self.contract_writer()()
        self.worker_edit()()
        util.write_json(os.path.join(self.runtime, "worker-result.json"),
                        {"task": "T1", "status": "partial",
                         "checkpoint": "half of parse() is written"})
        self.patch({"judge": self.judge_says("resume", "capability")})
        run.boot_reconcile(self.h)
        # `work` maps onto plan A's `wrapup` branch: a fresh Worker with the
        # checkpoint in front of it, over a tree that still holds the work. No
        # session survives a boot, so there is nothing to `--resume`.
        self.assertEqual(("T1", "wrapup"), (self.h.resume_task, self.h.resume_phase))
        self.assertTrue(os.path.exists(os.path.join(self.wt, "src", "a.ts")))

    def test_a_defer_decision_leaves_the_task_blocked_rather_than_pending(self):
        from runner import plan as pm
        self.mark_doing()
        self.contract_writer()()
        self.patch({"judge": self.judge_says("defer", "open")})
        run.boot_reconcile(self.h)
        self.assertEqual("blocked", pm.Plan.load(self.plan_path).task("T1").state,
                         "a deferred task must not be set back to pending")
        self.assertEqual("", self.h.resume_task)
        self.assertIn("## T1", util.read_text(
            os.path.join(self.loop_dir, "LOOP_CLEANUP.md")))

    def test_no_state_file_and_no_contract_re_scouts_and_keeps_the_tree(self):
        """With no readable contract there is nothing to split the tree against,
        so there is no question to put to the Judge — and no licence to delete
        uncommitted work to recover from a missing file. Plan A reverted here,
        but only ever in tests: in production the Harness captures its
        pre-existing baseline AFTER the crash, so the dirty tree was already the
        baseline and `_minus_preexisting` spared all of it anyway."""
        self.mark_doing()
        self.worker_edit("src/whatever.ts", "half a task\n")()
        rec = self.patch({})
        run.boot_reconcile(self.h)
        self.assertTrue(os.path.exists(os.path.join(self.wt, "src", "whatever.ts")))
        self.assertEqual("scout", self.h.resume_phase)
        self.assertEqual([], [c for c in rec.calls if c["phase"] == "judge"])

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


class TestBlastRadius(Base):
    """The revert on the blocked path may only touch what the loop was
    sanctioned to write. A human's file is not the loop's to delete."""

    def human_work(self):
        """Three shapes of work the loop did not create, in one dirty tree."""
        with open(os.path.join(self.wt, "HUMAN.md"), "w") as f:
            f.write("a human wrote this by hand\n")
        os.makedirs(os.path.join(self.wt, "humandir", "deep"))
        with open(os.path.join(self.wt, "humandir", "deep", "idea.txt"), "w") as f:
            f.write("half an idea\n")
        with open(os.path.join(self.wt, "src", "a.ts"), "a") as f:
            f.write("// and edited this tracked file\n")

    def test_a_pre_worker_block_does_not_touch_anything_the_loop_did_not_write(self):
        """`invalid-contract`: two Scouts failed, NO Worker ran, so the loop
        produced none of the changes in the tree and may destroy none of them."""
        self.human_work()
        self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer(allow_list=[])},
                      {"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer(allow_list=[])}],
            "worker": []})
        run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        self.assertEqual("blocked", plan_mod.Plan.load(self.plan_path).task("T1").state)
        self.assertTrue(os.path.exists(os.path.join(self.wt, "HUMAN.md")))
        self.assertTrue(os.path.exists(
            os.path.join(self.wt, "humandir", "deep", "idea.txt")))
        self.assertIn("no contract was ever in force on this tick",
                      util.read_text(os.path.join(self.loop_dir, "harness.log")))
        with open(os.path.join(self.wt, "src", "a.ts")) as f:
            self.assertIn("edited this tracked file", f.read())

    def test_a_post_worker_block_reverts_the_loops_work_and_nothing_further(self):
        """After a Worker, the allow_list work IS the loop's and goes back — and
        `mark_blocked` adds nothing to what the sandbox already took.

        Note this harness was constructed BEFORE `human_work()` ran, so its
        pre-existing baseline is empty and the sandbox still reverts those
        files here. That ordering is the test rig's, not production's --
        `TestSandboxBaseline` below builds the harness after the human's work,
        the way a real boot does, and pins the behaviour that actually ships.
        """
        self.human_work()
        self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer(
                           allow_list=["src/w.ts"],
                           success_criteria=["src/w.ts exists"])}],
            "worker": [{"text": block({"status": "complete", "summary": ""}),
                        "side_effect": self.worker_edit("src/w.ts", "loop wrote this\n")}],
            "evaluator": [{"text": block({"verdict": "BLOCKER", "summary": "no"})}]})
        run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        self.assertEqual("blocked", plan_mod.Plan.load(self.plan_path).task("T1").state)
        self.assertFalse(os.path.exists(os.path.join(self.wt, "src", "w.ts")))
        log = util.read_text(os.path.join(self.loop_dir, "harness.log"))
        # mark_blocked took exactly the loop's own sanctioned path, and no more.
        self.assertIn("blocked T1: reverted 1 path(s): src/w.ts", log)
        self.assertNotIn("blocked T1: reverted 2", log)
        # …and the sandbox, not mark_blocked, is what took the human's work.
        self.assertIn("sandbox: reverted 3 path(s): src/a.ts, HUMAN.md", log)
        self.assertFalse(os.path.exists(os.path.join(self.wt, "HUMAN.md")))


class TestDirectoryStray(Base):
    """A nested repository is one porcelain entry git refuses to descend into.
    The sandbox may not claim to have reverted it."""

    def nested_repo(self, rel="vendor/nested"):
        full = os.path.join(self.wt, rel)
        os.makedirs(full)
        sh(full, "git", "init", "-q")
        with open(os.path.join(full, "someones-work.txt"), "w") as f:
            f.write("uncommitted, and not ours\n")
        return full

    def test_it_survives_and_the_log_says_so_instead_of_claiming_a_revert(self):
        full = self.nested_repo()
        contract = cmod.Contract(task="T1", allow_list=["src/a.ts"])
        stray = run.sandbox(self.h, contract)
        self.assertIn("vendor/nested/", stray)
        self.assertTrue(os.path.exists(os.path.join(full, "someones-work.txt")))
        log = util.read_text(os.path.join(self.loop_dir, "harness.log"))
        self.assertIn("COULD NOT revert", log)
        self.assertIn("vendor/nested/", log)

    def test_a_file_stray_beside_it_is_still_reverted_and_reported_as_reverted(self):
        self.nested_repo()
        self.worker_edit("src/b.ts", "stray\n")()
        contract = cmod.Contract(task="T1", allow_list=["src/a.ts"])
        run.sandbox(self.h, contract)
        self.assertFalse(os.path.exists(os.path.join(self.wt, "src", "b.ts")))
        log = util.read_text(os.path.join(self.loop_dir, "harness.log"))
        self.assertIn("sandbox: reverted 1 path(s): src/b.ts", log)


class TestAttemptCap(Base):
    """`Limits: max_attempts=` is documented and hand-edited. It may not be
    parsed, defaulted and then ignored."""

    def scripted(self, workers=4):
        return {
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer()}],
            "worker": [{"text": block({"status": "complete", "summary": ""}),
                        "side_effect": self.worker_edit()} for _ in range(workers)],
            "evaluator": [{"text": block({"verdict": "NEEDS_WORK", "summary": "thin"})}
                          for _ in range(workers)]}

    def test_the_default_is_spec_4_2s_three(self):
        """Plan A clamped this to two because nothing would have differed
        between attempt two and three except the bill. The Judge can now change
        the tier, widen the contract or split, so the third attempt buys
        something and `max_attempts` is honoured as written."""
        self.assertEqual(3, run.attempt_cap(self.cfg))

    def test_a_lower_ceiling_is_honoured_and_costs_one_worker_not_two(self):
        self.h.cfg.limits["max_attempts"] = 1
        script = self.scripted()
        script["judge"] = self.judge_says("retry", "capability")
        rec = self.patch(script)
        run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        self.assertEqual(1, len([c for c in rec.calls if c["phase"] == "worker"]),
                         "the cap bites even though the Judge asked to retry")
        self.assertEqual("blocked", plan_mod.Plan.load(self.plan_path).task("T1").state)
        self.assertIn("attempt 1 of 1 is the last",
                      util.read_text(os.path.join(self.loop_dir, "harness.log"))
                      + util.read_text(os.path.join(self.loop_dir,
                                                    "LOOP_DECISIONS.md")))

    def test_a_higher_ceiling_is_now_honoured_rather_than_clamped(self):
        self.h.cfg.limits["max_attempts"] = 3
        script = self.scripted(workers=3)
        script["judge"] = self.judge_says("retry", "capability", n=2)
        rec = self.patch(script)
        run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        self.assertEqual(3, len([c for c in rec.calls if c["phase"] == "worker"]),
                         "three attempts, because a decision can change something")
        self.assertEqual(3, run.attempt_cap(self.h.cfg))

    def test_a_garbage_ceiling_falls_back_to_the_documented_default(self):
        self.h.cfg.limits["max_attempts"] = "three"
        self.assertEqual(3, run.attempt_cap(self.h.cfg))


class TestClassificationVocabulary(Base):
    """Spec §7 names exactly four. Emitting a fifth makes the field unreadable."""

    def test_every_decision_event_uses_only_spec_7s_enum(self):
        self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer()}],
            "worker": [{"text": block({"status": "complete", "summary": ""}),
                        "side_effect": self.worker_edit()},
                       {"text": block({"status": "complete", "summary": ""}),
                        "side_effect": self.worker_edit()}],
            "evaluator": [{"text": block({"verdict": "NEEDS_WORK", "summary": "a"})},
                          {"text": block({"verdict": "NEEDS_WORK", "summary": "b"})}],
            "judge": self.judge_says("retry", "spec-answered")
                     + self.judge_says("defer", "open")})
        run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        events = self.emitted("decision")
        self.assertEqual(["retry", "defer"], [e["decision"] for e in events])
        for ev in events:
            self.assertIn(ev["classification"], run.CLASSIFICATIONS)
        self.assertEqual(["spec-answered", "open"],
                         [e["classification"] for e in events],
                         "a judged decision carries the Judge's own reading; "
                         "`open` is no longer the only answer the loop can give")

    def test_boot_reconcile_keeps_its_own_fact_in_cause_not_in_classification(self):
        self.h.plan.set_state("T1", "doing")
        self.h.plan.save()
        run.boot_reconcile(self.h)
        ev = self.emitted("decision")[0]
        self.assertEqual(run.UNJUDGED, ev["classification"])
        self.assertEqual("boot-reconcile", ev["cause"])


class TestFailureSignature(Base):
    def test_it_keys_on_the_kind_and_what_failed_not_on_the_return_code(self):
        """This is what an attempt records as its `outcome`, and what spots a
        repeat. It is deliberately not the medic's `kind + phase + task` key
        (spec §8), which lives with the incident records."""
        self.assertEqual(run.failure_signature("gate", "tsc failed\nTS2339"),
                         run.failure_signature("gate", "tsc failed\nelsewhere"))
        self.assertNotEqual(run.failure_signature("gate", "tsc failed"),
                            run.failure_signature("gate", "eslint failed"))
        self.assertNotEqual(run.failure_signature("needs-work", "tsc failed"),
                            run.failure_signature("gate", "tsc failed"))
        self.assertEqual("needs-work:", run.failure_signature("needs-work", ""))
        self.assertLessEqual(
            len(run.failure_signature("gate", "x" * 400)), 5 + 120)


if __name__ == "__main__":
    unittest.main()


class TestSandboxBaseline(Base):
    """The sandbox contains the Worker; it does not tidy the human's tree.

    `sandbox` runs after EVERY Worker phase, so this fires on ordinary
    successful ticks, not only on failures. Without a baseline every path the
    human had already dirtied is outside the allow_list and therefore a
    "stray", and the first green tick of the night silently destroys it.

    The harness is built AFTER the human's work here, which is the real
    ordering: a human edits their tree, then starts the loop.
    """

    def boot_after_human_work(self):
        """Re-make the Harness so its baseline sees the tree as a boot would."""
        with open(os.path.join(self.wt, "KEEP.md"), "w") as f:
            f.write("an untracked file the human left lying about\n")
        with open(os.path.join(self.wt, "src", "a.ts"), "a") as f:
            f.write("// an uncommitted edit the human made before the loop ran\n")
        self.h = run.Harness(
            cfg=self.cfg, plugin_root="/plugin", loop_dir=self.loop_dir,
            runtime_dir=self.runtime, worktree=self.wt,
            config_path=os.path.join(self.loop_dir, "LOOP_CONFIG.md"),
            plan_path=self.plan_path,
            events=EventLog(self.events_path, os.path.join(self.runtime, "eventseq")),
            usage=UsageLog(os.path.join(self.loop_dir, "LOOP_USAGE.jsonl")),
            log=run.Log(os.path.join(self.loop_dir, "harness.log")),
            plan=plan_mod.Plan.load(self.plan_path),
            medic=incidents.MedicState())

    def test_a_green_tick_does_not_destroy_work_the_human_left_in_the_tree(self):
        self.boot_after_human_work()
        self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer(
                           allow_list=["src/w.ts"],
                           success_criteria=["src/w.ts exists"])}],
            "worker": [{"text": block({"status": "complete", "summary": ""}),
                        "side_effect": self.worker_edit("src/w.ts", "loop wrote this\n")}],
            "evaluator": [{"text": block({"verdict": "PASS", "summary": "ok"})}],
            "learner": [{"text": block({"learnings": []})}]})
        run.execute_tick(self.h, 1, self.h.plan.task("T1"))

        self.assertTrue(os.path.exists(os.path.join(self.wt, "KEEP.md")),
                        "the human's untracked file was deleted by the sandbox")
        with open(os.path.join(self.wt, "src", "a.ts")) as f:
            self.assertIn("before the loop ran", f.read(),
                          "the human's uncommitted edit was reverted by the sandbox")

    def test_the_spared_paths_are_named_in_the_log_not_silently_skipped(self):
        """Sparing is a judgement the operator must be able to see and argue
        with -- a Worker edit hiding behind a human's dirty file stays in the
        tree, and the log is the only place that says so."""
        self.boot_after_human_work()
        self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer(
                           allow_list=["src/w.ts"],
                           success_criteria=["src/w.ts exists"])}],
            "worker": [{"text": block({"status": "complete", "summary": ""}),
                        "side_effect": self.worker_edit("src/w.ts", "loop wrote this\n")}],
            "evaluator": [{"text": block({"verdict": "PASS", "summary": "ok"})}],
            "learner": [{"text": block({"learnings": []})}]})
        run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        log = util.read_text(os.path.join(self.loop_dir, "harness.log"))
        self.assertIn("already modified before this run started", log)
        self.assertIn("KEEP.md", log)

    def test_the_loops_own_stray_is_still_reverted(self):
        """The baseline must not become an amnesty: a path the Worker dirtied
        that the human had not touched is still the sandbox's to take."""
        self.boot_after_human_work()
        self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer(
                           allow_list=["src/w.ts"],
                           success_criteria=["src/w.ts exists"])}],
            "worker": [{"text": block({"status": "complete", "summary": ""}),
                        "side_effect": self.worker_edit("src/stray.ts", "not sanctioned\n")}],
            "evaluator": [{"text": block({"verdict": "PASS", "summary": "ok"})}],
            "learner": [{"text": block({"learnings": []})}]})
        run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        self.assertFalse(os.path.exists(os.path.join(self.wt, "src", "stray.ts")),
                         "a genuine Worker stray survived the sandbox")


class TestSandboxIsARecordedPhase(Base):
    """A kill during the sandbox must not read as a kill inside WORK.

    Spec §14 lists SANDBOX in PHASES and `execute_tick`'s own sequence names
    it, but nothing recorded it — so `phase` stayed `WORK` for the whole
    containment step. The boot rule sends a `WORK`-in-flight task to `wrapup`,
    which spends a second Worker budget, and it would do so on top of a tree
    whose stray-revert never finished.
    """

    def test_the_state_file_shows_work_finishing_before_the_sandbox_starts(self):
        seen = []
        real = run.sandbox

        def watched(h, contract):
            seen.append(task_state.TaskState.load(self.runtime, "T1").phase)
            return real(h, contract)

        run.sandbox = watched
        try:
            self.patch({
                "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                           "side_effect": self.contract_writer(
                               allow_list=["src/w.ts"],
                               success_criteria=["src/w.ts exists"])}],
                "worker": [{"text": block({"status": "complete", "summary": ""}),
                            "side_effect": self.worker_edit("src/w.ts", "x\n")}],
                "evaluator": [{"text": block({"verdict": "PASS", "summary": "ok"})}],
                "learner": [{"text": block({"learnings": []})}]})
            run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        finally:
            run.sandbox = real

        self.assertEqual(["SANDBOX"], seen,
                         "the sandbox ran while the state file still said %r" % seen)

    def test_the_worker_and_the_sandbox_are_both_recorded(self):
        self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer(
                           allow_list=["src/w.ts"],
                           success_criteria=["src/w.ts exists"])}],
            "worker": [{"text": block({"status": "complete", "summary": ""}),
                        "side_effect": self.worker_edit("src/w.ts", "x\n")}],
            "evaluator": [{"text": block({"verdict": "PASS", "summary": "ok"})}],
            "learner": [{"text": block({"learnings": []})}]})
        run.execute_tick(self.h, 1, self.h.plan.task("T1"))
        doc = json.loads(util.read_text(
            os.path.join(self.runtime, "task-T1.json")))
        phases_seen = [pr["phase"] for pr in doc["attempts"][-1]["phase_results"]]
        self.assertIn("worker", [p.lower() for p in phases_seen])


class TestRenameContainment(Base):
    """A rename is one operation; containment must undo all of it.

    `changed_paths` reports both names so each can be judged against the
    allow_list separately -- correct for deciding what is a stray, wrong for
    deciding what to undo. A Worker renaming a sanctioned file to an
    unsanctioned name makes only the destination a stray, and reverting that
    alone leaves the tree with neither name: the destination deleted, the
    source still renamed away.
    """

    def test_renaming_a_sanctioned_file_out_of_scope_restores_it(self):
        self.patch({
            "scout": [{"text": block({"contract_path": "x", "notes": ""}),
                       "side_effect": self.contract_writer(
                           allow_list=["src/a.ts"],
                           success_criteria=["src/a.ts exists"])}],
            "worker": [{"text": block({"status": "complete", "summary": ""}),
                        "side_effect": self.rename("src/a.ts", "src/escaped.ts")}],
            "evaluator": [{"text": block({"verdict": "PASS", "summary": "ok"})}],
            "learner": [{"text": block({"learnings": []})}]})
        run.execute_tick(self.h, 1, self.h.plan.task("T1"))

        self.assertFalse(os.path.exists(os.path.join(self.wt, "src", "escaped.ts")),
                         "the unsanctioned destination survived the sandbox")
        self.assertTrue(os.path.exists(os.path.join(self.wt, "src", "a.ts")),
                        "the sanctioned source was left renamed away — the tree "
                        "has neither name, which the Worker never asked for")
