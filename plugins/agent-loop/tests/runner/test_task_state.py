import _path  # noqa: F401
import importlib.util
import os
import shutil
import tempfile
import unittest
from dataclasses import dataclass
from typing import Optional

from runner import task_state, util
from runner.task_state import TaskState

# Gate on the module's absence, not on any ImportError: once claude_proc exists,
# an import error raised from inside it must reach us, not be swallowed into a
# green run against the stand-in.
if importlib.util.find_spec("runner.claude_proc") is None:  # pragma: no cover
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
else:
    from runner.claude_proc import PhaseResult


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


class TestHostileFiles(Base):
    """Well-formed JSON carrying the wrong types, i.e. what `load` exists for."""

    HOSTILE = (
        {"attempt": "abc"}, {"attempt": "2.5"}, {"attempt": [1]},
        {"updated": "soon"}, {"attempts": 5}, {"artifacts": 4},
        {"session_ids": [1, 2]}, {"session_ids": "x"},
        {"phase": 3}, {"phase": ["WORK"]}, {"phase": True},
        {"attempts": "abc"}, {"attempts": {"a": 1}}, {"artifacts": "a/b.png"},
        {"contract": 9}, {"attempts": ["junk", 5]},
    )

    def test_wrong_types_load_empty_rather_than_raising(self):
        for doc in self.HOSTILE:
            util.write_json(task_state.state_path(self.rt, "T60"), doc)
            state = TaskState.load(self.rt, "T60")          # must not raise
            self.assertEqual([], state.attempts, doc)
            self.assertEqual([], state.artifacts, doc)
            self.assertEqual({}, state.session_ids, doc)
            self.assertEqual("", state.contract, doc)
            self.assertIsInstance(state.attempt, int, doc)
            self.assertIsInstance(state.updated, int, doc)

    def test_wrong_types_never_reach_boot_resume_as_a_non_string(self):
        for doc in self.HOSTILE:
            util.write_json(task_state.state_path(self.rt, "T60"), doc)
            state = TaskState.load(self.rt, "T60")
            self.assertEqual("scout", task_state.boot_resume(state), doc)

    def test_a_mutator_on_a_hostile_file_still_works(self):
        util.write_json(task_state.state_path(self.rt, "T60"),
                        {"attempts": ["junk"], "session_ids": {"worker": 5}})
        state = TaskState.load(self.rt, "T60")
        self.assertEqual({"worker": ""}, state.session_ids)
        state.end_attempt("pass")                            # must not raise
        state.begin_attempt("standard")
        state.begin_phase("WORK")
        state.end_phase(self.result())                       # must not raise
        self.assertEqual(1, state.attempt)
        self.assertEqual(1, len(state.attempts[0]["phase_results"]))

    def test_a_numeric_attempt_still_floors(self):
        util.write_json(task_state.state_path(self.rt, "T60"), {"attempt": 2.9})
        self.assertEqual(2, TaskState.load(self.rt, "T60").attempt)

    def test_the_requested_task_id_wins_over_the_one_in_the_file(self):
        util.write_json(task_state.state_path(self.rt, "T60"),
                        {"task": "T59", "phase": "WORK:done"})
        state = TaskState.load(self.rt, "T60")
        self.assertEqual("T60", state.task)
        self.assertEqual(task_state.state_path(self.rt, "T60"), state.path)
        state.begin_phase("GATE")
        self.assertFalse(os.path.exists(task_state.state_path(self.rt, "T59")))
        self.assertEqual(["task-T60.json"], os.listdir(self.rt))

    def test_a_non_string_task_field_cannot_redirect_the_write(self):
        util.write_json(task_state.state_path(self.rt, "T60"), {"task": 7})
        state = TaskState.load(self.rt, "T60")
        state.begin_phase("GATE")
        self.assertEqual(["task-T60.json"], os.listdir(self.rt))


class TestDurability(Base):
    def test_saving_goes_through_the_atomic_writer(self):
        calls = []
        real = util.atomic_write

        def spy(path, text):
            calls.append(path)
            real(path, text)

        util.atomic_write = spy
        try:
            TaskState.load(self.rt, "T60").save()
        finally:
            util.atomic_write = real
        self.assertEqual([task_state.state_path(self.rt, "T60")], calls)

    def test_add_artifact_dedups_and_ignores_the_empty_path(self):
        state = TaskState.load(self.rt, "T60")
        state.add_artifact("artifacts/T60/a.png")
        state.add_artifact("artifacts/T60/a.png")
        state.add_artifact("")
        self.assertEqual(["artifacts/T60/a.png"], state.artifacts)
        self.assertEqual(["artifacts/T60/a.png"], self.raw()["artifacts"])

    def test_set_contract_persists_without_waiting_for_another_mutator(self):
        state = TaskState.load(self.rt, "T60")
        state.set_contract("runtime/sprint-T60.json")
        self.assertEqual("runtime/sprint-T60.json", self.raw()["contract"])
        self.assertEqual("runtime/sprint-T60.json",
                         TaskState.load(self.rt, "T60").contract)


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


class TestResumeBound(Base):
    """`worker_resume_max` is the only thing bounding a resumed Worker.

    A resume continues an attempt rather than opening one, so `max_attempts`
    never sees it. Spec §14 fixes this document's keys, so the count is derived
    from the WORK entries already in the current attempt: the first is the
    original dispatch, every one after it is a resume.
    """

    def work(self, state):
        state.begin_phase("WORK")
        state.end_phase(self.result(phase="WORK"))

    def test_the_original_dispatch_is_not_counted_as_a_resume(self):
        state = TaskState.load(self.rt, "T60")
        state.begin_attempt("standard")
        self.work(state)
        self.assertEqual(0, state.resumes_spent())

    def test_each_further_worker_in_the_same_attempt_is_a_resume(self):
        state = TaskState.load(self.rt, "T60")
        state.begin_attempt("standard")
        self.work(state)
        self.work(state)
        self.assertEqual(1, state.resumes_spent())
        self.work(state)
        self.assertEqual(2, state.resumes_spent())

    def test_a_new_attempt_starts_the_resume_budget_again(self):
        """A fresh attempt is a fresh Worker budget; `max_attempts` bounds those."""
        state = TaskState.load(self.rt, "T60")
        state.begin_attempt("standard")
        self.work(state)
        self.work(state)
        state.end_attempt("needs-work")
        state.begin_attempt("standard")
        self.work(state)
        self.assertEqual(0, state.resumes_spent())

    def test_it_survives_a_reload_because_it_is_read_off_disk(self):
        state = TaskState.load(self.rt, "T60")
        state.begin_attempt("standard")
        self.work(state)
        self.work(state)
        self.assertEqual(1, TaskState.load(self.rt, "T60").resumes_spent())

    def test_a_garbled_phase_results_entry_does_not_raise(self):
        """This is read on the crash path, so it must not add a failure mode."""
        state = TaskState.load(self.rt, "T60")
        state.begin_attempt("standard")
        state.attempts[-1]["phase_results"] = ["not a dict", {"phase": None}]
        state.save()
        self.assertEqual(0, TaskState.load(self.rt, "T60").resumes_spent())


class TestResumesSpentCountsRealWorkerPhases(unittest.TestCase):
    """Regression: the count must match what the harness actually records.

    `end_phase(result)` stores `result.phase` — the PhaseResult's name, which is
    `worker`, not the state-machine's `WORK`. Matching only `WORK` made
    `resumes_spent()` return 0 for every real dispatch, so `worker_resume_max`
    bounded nothing in production while the unit tests passed on a hand-built
    `phase="WORK"` no production path ever produces.

    `worker-wrapup` is excluded on purpose: spec §6 step 1's wrap-up is an
    automatic continuation, and step 2's `worker_resume_max` bounds the Judge's
    resume decisions.
    """

    def setUp(self):
        self.rt = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.rt)

    def dispatch(self, state, phase):
        state.begin_phase("WORK")
        state.end_phase(PhaseResult(phase=phase, model="sonnet"))

    def test_production_phase_names_are_counted(self):
        state = TaskState.load(self.rt, "T60")
        state.begin_attempt("standard")
        self.dispatch(state, "worker")
        self.assertEqual(0, state.resumes_spent(), "the first dispatch is not a resume")
        self.dispatch(state, "worker")
        self.assertEqual(1, state.resumes_spent())

    def test_the_automatic_wrapup_does_not_spend_a_resume(self):
        """Spec §6: step 1's wrap-up is automatic; step 2's resume is the Judge's
        decision, and `worker_resume_max` bounds that. Counting the wrap-up
        would leave the resume path unreachable at the default of 1."""
        state = TaskState.load(self.rt, "T60")
        state.begin_attempt("standard")
        self.dispatch(state, "worker")
        self.dispatch(state, "worker-wrapup")
        self.assertEqual(0, state.resumes_spent())
        self.dispatch(state, "worker")
        self.assertEqual(1, state.resumes_spent())

    def test_other_phases_are_not_counted(self):
        state = TaskState.load(self.rt, "T60")
        state.begin_attempt("standard")
        for phase in ("scout", "evaluator", "judge", "learner"):
            state.begin_phase(phase.upper())
            state.end_phase(PhaseResult(phase=phase, model="sonnet"))
        self.assertEqual(0, state.resumes_spent())
