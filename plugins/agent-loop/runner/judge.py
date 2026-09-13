"""Judge phase: assemble the failure dossier, ask the Judge, apply its decision.

`judge.py` may import `phases`; `phases` must never import `judge` (spec §7 puts
the Judge downstream of every phase it reviews). `run.py` imports both.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import contract as contract_mod, util
from .config import phase_limit
from .task_state import TaskState

MAX_GATE_LINES = 80
MAX_EXCERPT_LINES = 40
PARITY_RE = re.compile(r"\bP-[A-Z]+-\d+\b")


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def one_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def tail_lines(text: str, n: int) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def json_safe(obj: Any) -> Any:
    """Drop `_`-prefixed keys so a decision dict can be written to disk."""
    if isinstance(obj, dict):
        return dict((k, json_safe(v)) for k, v in obj.items()
                    if not str(k).startswith("_"))
    if isinstance(obj, list):
        return [json_safe(v) for v in obj]
    return obj


def grep_excerpts(paths: List[str], ids: List[str],
                  max_lines: int = MAX_EXCERPT_LINES) -> List[str]:
    """One block per document, listing every line that names one of `ids`."""
    out = []
    for path in paths:
        if not path or not os.path.exists(path):
            continue
        lines = util.read_text(path).splitlines()
        hits = []
        for n, line in enumerate(lines):
            for ident in ids:
                if re.search(r"\b%s\b" % re.escape(ident), line):
                    hits.append((n, line))
                    break
        if not hits:
            continue
        block = ["### %s" % path]
        for n, line in hits[:max_lines]:
            block.append("%s:%d: %s" % (os.path.basename(path), n + 1, line.strip()))
        if len(hits) > max_lines:
            block.append("... %d more matching lines" % (len(hits) - max_lines))
        out.append("\n".join(block))
    return out


# --------------------------------------------------------------------------
# attempt history — plan A's runtime/task-<T>.json (spec §14). This module reads
# that state and closes attempts through TaskState; it owns no file of its own.
# --------------------------------------------------------------------------
def attempts_for(runtime_dir: str, task_id: str) -> List[dict]:
    """The task's attempt list, `[]` when it has no state file yet."""
    try:
        return list(TaskState.load(runtime_dir, task_id).attempts or [])
    except (IOError, OSError, ValueError):
        return []


def judge_decision_count(attempts: List[dict]) -> int:
    return len([a for a in attempts if a.get("judge")])


def cap_extended(attempts: List[dict]) -> bool:
    """True once the Judge has spent this task's one cap extension (spec §4.2)."""
    for a in attempts or []:
        changes = (a.get("judge") or {}).get("changes") or {}
        try:
            if int(changes.get("extend_cap_s") or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def last_tier(attempts: List[dict], default: str) -> str:
    """The tier the most recent recorded attempt ran at."""
    for a in reversed(attempts or []):
        if a.get("tier"):
            return a["tier"]
    return default


def close_attempt(ctx, outcome: str, decision: Optional[dict] = None,
                  judge_phase=None) -> None:
    """Close the current attempt: the Judge's phase result, the outcome, the decision.

    Every other phase was already recorded by the harness at its own transition
    (spec §14). The Judge's result is appended WITHOUT a `begin_phase` of its own:
    §14's `PHASES` list has no JUDGE, and `task_state.boot_resume` sends any
    unrecognised `phase` name to `scout` — so stamping one here would make a
    crash during the Judge throw away a completed Worker's work and re-run the
    whole task. Leaving `phase` at the last real phase (GATE/EVALUATE, already
    `:done`) boots to `gate` instead, which is where a judged task belongs.
    """
    state = TaskState.load(ctx.runtime_dir, ctx.task.id)
    if judge_phase is not None:
        state.end_phase(judge_phase)
    state.end_attempt(outcome, judge=json_safe(decision) if decision else None)
    state.save()


# --------------------------------------------------------------------------
# the dossier
# --------------------------------------------------------------------------
@dataclass
class Failure:
    kind: str          # invalid-contract|gate|render|fidelity|needs-work|
                       # blocker|worker-incomplete|repeat|boot-reconcile
    detail: str = ""
    verdict: Dict[str, Any] = field(default_factory=dict)
    signature: str = ""
    repeat: bool = False


@dataclass
class JudgeInput:
    task_id: str
    task_row: str
    contract_json: str
    gate_outputs: List[str]
    verdict: Dict[str, Any]
    checkpoint: str
    cleanup_text: str
    excerpts: List[str]
    forbidden: List[Dict[str, str]]
    attempts: List[dict]
    policy: str
    failure: str
    # the bounds the harness enforces on whatever the Judge decides (spec §7)
    tier: str = "standard"            # the tier this attempt ran at
    attempt: int = 1
    max_attempts: int = 3
    cap_default_s: int = 1500         # worker_timeout: the extension ceiling
    cap_extended: bool = False        # the one extension is already spent
    resume_session: Optional[str] = None
    resumes_left: int = 0
    boot_evidence: str = ""           # spec §14; filled for boot-reconcile only


def contract_path(ctx) -> str:
    return os.path.join(ctx.runtime_dir, "sprint-%s.json" % ctx.task.id)


def build_input(ctx, contract, failure: Failure) -> JudgeInput:
    """Everything on disk that bears on this failure, capped for one prompt."""
    gate_outputs = []
    prefix = "gate-%s-" % ctx.task.id
    if os.path.isdir(ctx.runtime_dir):
        for name in sorted(os.listdir(ctx.runtime_dir)):
            if name.startswith(prefix) and name.endswith(".txt"):
                body = tail_lines(util.read_text(os.path.join(ctx.runtime_dir, name)),
                                  MAX_GATE_LINES)
                gate_outputs.append("### %s (last %d lines)\n%s"
                                    % (name, MAX_GATE_LINES, body))

    doc = util.read_json(os.path.join(ctx.runtime_dir, "worker-result.json"))
    checkpoint = one_line(doc.get("checkpoint", "")) if isinstance(doc, dict) else ""

    ids = [ctx.task.id] + PARITY_RE.findall(ctx.task.raw or "")
    excerpts = grep_excerpts([ctx.cfg.plan_path, ctx.cfg.spec_path], ids)

    attempts = attempts_for(ctx.runtime_dir, ctx.task.id)
    limits = ctx.cfg.limits or {}
    configured = ctx.cfg.role_tiers.get("worker") or "standard"
    resume_max = int(limits.get("worker_resume_max", 1))
    used = int(getattr(ctx, "resume_count", 0) or 0)

    return JudgeInput(
        task_id=ctx.task.id,
        task_row=(ctx.task.raw or "").rstrip("\n"),
        contract_json=json.dumps(contract_mod.to_dict(contract), indent=2,
                                 sort_keys=True),
        gate_outputs=gate_outputs,
        verdict=failure.verdict or {},
        checkpoint=checkpoint,
        cleanup_text=util.read_text(os.path.join(ctx.loop_dir, "LOOP_CLEANUP.md")),
        excerpts=excerpts,
        forbidden=[{"path": f.path, "source": f.source} for f in contract.forbidden],
        attempts=attempts,
        policy=ctx.cfg.decision_policy or "autonomous",
        failure=("%s: %s" % (failure.kind, one_line(failure.detail))).strip().rstrip(":"),
        tier=(getattr(ctx, "tier", None) or last_tier(attempts, configured)),
        attempt=int(getattr(ctx, "attempt", 1) or 1),
        max_attempts=int(limits.get("max_attempts", 3)),
        cap_default_s=phase_limit(ctx.cfg, "worker"),
        cap_extended=cap_extended(attempts),
        resume_session=getattr(ctx, "last_session", None),
        resumes_left=max(0, resume_max - used),
        boot_evidence="",          # Task 8 fills this for boot-reconcile
    )
