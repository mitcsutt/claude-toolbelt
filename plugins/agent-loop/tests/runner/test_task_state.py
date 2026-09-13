import _path  # noqa: F401
import os
import tempfile
import unittest
from dataclasses import dataclass
from typing import Optional

from runner import task_state, util
from runner.task_state import TaskState

try:                                        # claude_proc lands in task 12
    from runner.claude_proc import PhaseResult
except ImportError:                         # pragma: no cover - pre-task-12
    @dataclass
    class PhaseResult:                      # type: ignore[no-redef]
        """The subset of task 12's PhaseResult that task_state duck-types."""
        phase: str
        model: str
        rc: Optional[int] = None
        killed: bool = False
        session_id: Optional[str] = None
        started: int = 0
        ended: int = 0


class Base(unittest.TestCase):
    def setUp(self):
        self.rt = tempfile.mkdtemp()

    def raw(self, task_id="T60"):
        return util.read_json(task_state.state_path(self.rt, task_id))

    def result(self, phase="worker", session="sess-1", rc=0):
        return PhaseResult(phase=phase, model="mid", rc=rc, session_id=session,
                           started=100, ended=160)


class TestFreshState(Base):
    def test_a_task_with_no_file_loads_empty_rather_than_raising(self):
        state = TaskState.load(self.rt, "T60")
        self.assertEqual("T60", state.task)
        self.assertEqual("", state.phase)
        self.assertEqual(0, state.attempt)
        self.assertEqual([], state.attempts)
        self.assertFalse(task_state.has_state(self.rt, "T60"))

    def test_a_malformed_file_loads_empty_rather_than_raising(self):
        util.atomic_write(task_state.state_path(self.rt, "T60"), "{not json")
        self.assertEqual(0, TaskState.load(self.rt, "T60").attempt)

    def test_the_path_is_task_id_json_under_runtime(self):
        self.assertEqual(os.path.join(self.rt, "task-T60.json"),
                         task_state.state_path(self.rt, "T60"))


class TestShape(Base):
    def test_the_document_carries_exactly_the_spec_keys(self):
        state = TaskState.load(self.rt, "T60")
        state.contract = "runtime/sprint-T60.json"
        state.begin_attempt("standard")
        state.begin_phase("WORK")
        state.end_phase(self.result())
        state.end_attempt("pass", judge={"decision": "none"})
        doc = self.raw()
        self.assertEqual(["task", "phase", "attempt", "attempts", "session_ids",
                          "contract", "artifacts", "updated"], list(doc.keys()))
        self.assertEqual(["n", "tier", "phase_results", "outcome", "judge"],
                         list(doc["attempts"][0].keys()))
        self.assertIsInstance(doc["updated"], int)
        self.assertEqual("runtime/sprint-T60.json", doc["contract"])

    def test_every_transition_writes_the_file(self):
        state = TaskState.load(self.rt, "T60")
        state.begin_attempt("standard")
        self.assertEqual(1, self.raw()["attempt"])
        state.begin_phase("SCOUT")
        self.assertEqual("SCOUT", self.raw()["phase"])
        state.end_phase(self.result(phase="scout"))
        self.assertEqual("SCOUT:done", self.raw()["phase"])

    def test_no_tmp_file_is_left_behind(self):
        TaskState.load(self.rt, "T60").save()
        self.assertEqual(["task-T60.json"], os.listdir(self.rt))

    def test_a_round_trip_preserves_everything(self):
        state = TaskState.load(self.rt, "T60")
        state.contract = "runtime/sprint-T60.json"
        state.begin_attempt("standard")
        state.begin_phase("WORK")
        state.end_phase(self.result(session="sess-w"))
        state.add_artifact("artifacts/T60/orgunits.png")
        state.end_attempt("needs-work")
        back = TaskState.load(self.rt, "T60")
        self.assertEqual(state.phase, back.phase)
        self.assertEqual(1, back.attempt)
        self.assertEqual("sess-w", back.session_ids["worker"])
        self.assertEqual(["artifacts/T60/orgunits.png"], back.artifacts)
        self.assertEqual("needs-work", back.attempts[0]["outcome"])


class TestAttempts(Base):
    def test_begin_attempt_numbers_from_one_and_records_the_tier(self):
        state = TaskState.load(self.rt, "T60")
        state.begin_attempt("standard")
        state.begin_attempt("most-capable")
        self.assertEqual(2, state.attempt)
        self.assertEqual([1, 2], [a["n"] for a in state.attempts])
        self.assertEqual(["standard", "most-capable"],
                         [a["tier"] for a in state.attempts])

    def test_end_attempt_stamps_the_open_attempt_only(self):
        state = TaskState.load(self.rt, "T60")
        state.begin_attempt("standard")
        state.end_attempt("needs-work")
        state.begin_attempt("standard")
        state.end_attempt("pass", judge={"decision": "retry"})
        self.assertEqual(["needs-work", "pass"], [a["outcome"] for a in state.attempts])
        self.assertEqual({}, state.attempts[0]["judge"])
        self.assertEqual("retry", state.attempts[1]["judge"]["decision"])

    def test_end_attempt_with_no_open_attempt_is_a_no_op(self):
        TaskState.load(self.rt, "T60").end_attempt("pass")   # must not raise

    def test_phase_results_accumulate_under_the_open_attempt(self):
        state = TaskState.load(self.rt, "T60")
        state.begin_attempt("standard")
        for name, phase in (("SCOUT", "scout"), ("WORK", "worker"),
                            ("EVALUATE", "evaluator")):
            state.begin_phase(name)
            state.end_phase(self.result(phase=phase))
        self.assertEqual(["scout", "worker", "evaluator"],
                         [p["phase"] for p in state.attempts[0]["phase_results"]])
        self.assertEqual(60, state.attempts[0]["phase_results"][0]["dur"])

    def test_a_phase_ending_before_any_attempt_opens_one(self):
        state = TaskState.load(self.rt, "T60")
        state.begin_phase("SCOUT")
        state.end_phase(self.result(phase="scout"))
        self.assertEqual(1, state.attempt)
        self.assertEqual(1, len(state.attempts[0]["phase_results"]))

    def test_a_harness_phase_records_no_phase_result(self):
        state = TaskState.load(self.rt, "T60")
        state.begin_attempt("standard")
        state.begin_phase("GATE")
        state.end_phase()
        self.assertEqual("GATE:done", state.phase)
        self.assertEqual([], state.attempts[0]["phase_results"])

    def test_session_ids_are_keyed_by_role_and_the_newest_wins(self):
        state = TaskState.load(self.rt, "T60")
        state.begin_attempt("standard")
        state.end_phase(self.result(phase="worker", session="sess-1"))
        state.end_phase(self.result(phase="worker", session="sess-2"))
        state.end_phase(self.result(phase="scout", session="sess-s"))
        self.assertEqual({"worker": "sess-2", "scout": "sess-s"}, state.session_ids)


class TestBootResume(Base):
    def resume_for(self, phase):
        state = TaskState.load(self.rt, "T60")
        state.phase = phase
        return task_state.boot_resume(state)

    def test_a_worker_complete_task_resumes_at_the_gate(self):
        for phase in ("WORK:done", "SANDBOX", "SANDBOX:done", "GATE",
                      "EVALUATE", "COMMIT:done"):
            self.assertEqual("gate", self.resume_for(phase), phase)

    def test_a_task_killed_inside_work_goes_to_the_wrap_up_path(self):
        self.assertEqual("wrapup", self.resume_for("WORK"))

    def test_anything_before_work_restarts_from_the_scout(self):
        for phase in ("", "SELECT", "SCOUT", "SCOUT:done", "VALIDATE", "nonsense"):
            self.assertEqual("scout", self.resume_for(phase), phase)


if __name__ == "__main__":
    unittest.main()
