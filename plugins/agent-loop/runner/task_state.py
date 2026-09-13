"""runtime/task-<T>.json — the durable record of ONE task's state machine (spec §14).

The plan file is the graph. `events.jsonl` is the stream the dashboard reads and
is never used to reconstruct control state. This file is the only thing that says
where inside a task the harness was when it died, and it is rewritten atomically
at every phase transition — so a kill costs at most the phase in flight, never
the task.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import util

# The tick's phases, in order. `phase` holds one of these while it is in flight
# and "<NAME>:done" once it has returned; boot_resume needs exactly that
# distinction to tell a killed Worker from a Worker-complete task.
PHASES = ("SELECT", "SCOUT", "VALIDATE", "WORK", "SANDBOX", "GATE",
          "RENDER", "FIDELITY", "EVALUATE", "COMMIT", "LEARN")
DONE_SUFFIX = ":done"
_WORK_INDEX = PHASES.index("WORK")


# `load` is the one function here whose whole contract is tolerating a file this
# module did not write — a hand-edit by the medic, a foreign writer, a future
# version with a changed type. A wrong type must degrade to the empty value, not
# raise: this runs on the crash path, where an unhandled TypeError is the harness
# dying at the moment it is supposed to be recovering.
def _as_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_records(value: Any) -> List[Dict[str, Any]]:
    """A list of attempt records; anything that is not a record is dropped."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _as_strs(value: Any) -> List[str]:
    """A list of paths. A bare string is NOT iterated into characters."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _as_str_map(value: Any) -> Dict[str, str]:
    """role -> session id. A non-string id becomes "", i.e. nothing to resume."""
    if not isinstance(value, dict):
        return {}
    return {str(key): _as_str(item) for key, item in value.items()}


def state_path(runtime_dir: str, task_id: str) -> str:
    return os.path.join(runtime_dir, "task-%s.json" % task_id)


def has_state(runtime_dir: str, task_id: str) -> bool:
    """True when a previous harness got far enough to record anything at all."""
    return os.path.exists(state_path(runtime_dir, task_id))


@dataclass
class TaskState:
    task: str
    runtime_dir: str
    phase: str = ""
    attempt: int = 0
    attempts: List[Dict[str, Any]] = field(default_factory=list)
    session_ids: Dict[str, str] = field(default_factory=dict)
    contract: str = ""
    artifacts: List[str] = field(default_factory=list)
    updated: int = 0

    # ---------------------------------------------------------------- loading
    @classmethod
    def load(cls, runtime_dir: str, task_id: str) -> "TaskState":
        """The recorded state, or an empty one.

        A malformed file is an empty one — unparseable, or well-formed JSON
        carrying the wrong types. `task_id` is authoritative: we located the file
        by that name, so a `task` field disagreeing with it is stale data about a
        file we already found, and must never redirect where `save()` writes.
        """
        raw = util.read_json(state_path(runtime_dir, task_id))
        if not isinstance(raw, dict):
            return cls(task=task_id, runtime_dir=runtime_dir)
        return cls(
            task=task_id,
            runtime_dir=runtime_dir,
            phase=_as_str(raw.get("phase")),
            attempt=_as_int(raw.get("attempt")),
            attempts=_as_records(raw.get("attempts")),
            session_ids=_as_str_map(raw.get("session_ids")),
            contract=_as_str(raw.get("contract")),
            artifacts=_as_strs(raw.get("artifacts")),
            updated=_as_int(raw.get("updated")),
        )

    @property
    def path(self) -> str:
        return state_path(self.runtime_dir, self.task)

    def to_dict(self) -> Dict[str, Any]:
        """Spec §14's key order, so a human diffing two of these can read them."""
        return {"task": self.task, "phase": self.phase, "attempt": self.attempt,
                "attempts": self.attempts, "session_ids": self.session_ids,
                "contract": self.contract, "artifacts": self.artifacts,
                "updated": self.updated}

    def save(self) -> None:
        self.updated = int(time.time())
        util.write_json(self.path, self.to_dict())

    # -------------------------------------------------------------- attempts
    def last_attempt(self) -> Optional[Dict[str, Any]]:
        return self.attempts[-1] if self.attempts else None

    def resumes_spent(self) -> int:
        """How many times the CURRENT attempt has already re-run its Worker.

        Derived, not stored: spec §14 fixes this document's keys and the shape
        of an attempt record, so the count is read back out of the WORK entries
        already in `phase_results` -- the first is the original dispatch, every
        one after it is a resume. A resume continues an attempt whose budget was
        partly spent, so it is bounded by `worker_resume_max` rather than by
        `max_attempts`; without that bound the two limits together bound
        nothing, because a crash loop resumes forever inside one attempt.
        """
        record = self.last_attempt() or {}
        works = [pr for pr in record.get("phase_results", [])
                 if isinstance(pr, dict)
                 and str(pr.get("phase", "")).upper().startswith("WORK")]
        return max(0, len(works) - 1)

    def begin_attempt(self, tier: str) -> Dict[str, Any]:
        """Open attempt n+1 at `tier`. The tier is recorded, never derived.

        Whatever chose the tier — config on attempt 1, plan C's Judge on a
        re-attempt — this is the only place it is written down, so a postmortem
        can see what was actually dispatched rather than what a ladder implies.
        """
        self.attempt += 1
        record = {"n": self.attempt, "tier": tier, "phase_results": [],
                  "outcome": "", "judge": {}}
        self.attempts.append(record)
        self.save()
        return record

    def end_attempt(self, outcome: str, judge: Optional[Dict[str, Any]] = None) -> None:
        record = self.last_attempt()
        if record is None:
            return
        record["outcome"] = outcome
        record["judge"] = dict(judge or {})
        self.save()

    # ---------------------------------------------------------------- phases
    def begin_phase(self, name: str) -> None:
        self.phase = name
        self.save()

    def end_phase(self, result: Any = None) -> None:
        """Stamp the phase finished and, for an LLM phase, record what it did.

        `result` is None for a harness phase (SANDBOX, GATE, COMMIT): there is no
        subprocess, no session and no usage, only the fact that it completed.
        """
        if self.phase and not self.phase.endswith(DONE_SUFFIX):
            self.phase = self.phase + DONE_SUFFIX
        if result is not None:
            record = self.last_attempt()
            if record is None:
                record = self.begin_attempt("")
            record["phase_results"].append({
                "phase": result.phase, "model": result.model, "rc": result.rc,
                "started": result.started, "ended": result.ended,
                "dur": max(0, int(result.ended) - int(result.started)),
                "session": result.session_id or "", "killed": bool(result.killed)})
            if result.session_id:
                self.session_ids[result.phase] = result.session_id
        self.save()

    # ------------------------------------------------------- document fields
    def set_contract(self, path: str) -> None:
        """Point the record at its contract and persist it immediately.

        §14 names `contract` a document field, so a plain `state.contract = …`
        that waits for the next mutator to flush loses the pointer to a crash in
        between. Write-through, exactly like `add_artifact`.
        """
        self.contract = path
        self.save()

    def add_artifact(self, path: str) -> None:
        """Plan B's screenshots land here so the Evaluator's inputs are replayable."""
        if path and path not in self.artifacts:
            self.artifacts.append(path)
            self.save()


def boot_resume(state: TaskState) -> str:
    """`gate` | `wrapup` | `scout` — where a `[~]` task picks up (spec §14).

    A Worker that returned means the tree already holds its work, so re-running
    it would burn a Worker budget to redo what is on disk: resume at GATE. A
    Worker killed mid-phase has a checkpoint and a session id, so it gets the
    wrap-up/resume path. Anything earlier has no work to preserve.
    """
    name = (state.phase or "").split(DONE_SUFFIX)[0]
    finished = (state.phase or "").endswith(DONE_SUFFIX)
    if name not in PHASES:
        return "scout"
    index = PHASES.index(name)
    if index < _WORK_INDEX:
        return "scout"
    if index == _WORK_INDEX and not finished:
        return "wrapup"
    return "gate"
