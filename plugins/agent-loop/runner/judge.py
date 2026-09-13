"""Judge phase: assemble the failure dossier, ask the Judge, apply its decision.

`judge.py` may import `phases`; `phases` must never import `judge` (spec §7 puts
the Judge downstream of every phase it reviews). `run.py` imports both.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import claude_proc, contract as contract_mod, phases, util
from .config import TIER_ORDER, model_for, next_tier, phase_limit
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


# --------------------------------------------------------------------------
# the decision: vocabulary, policy validation, dispatch
# --------------------------------------------------------------------------
DECISIONS = ("retry", "escalate", "widen", "resume", "revert-and-retry",
             "split", "defer", "halt")
BOOT_DECISIONS = ("resume", "retry", "revert-and-retry", "defer")
# Spec §7's classification enum, in full. This is the single definition in the
# runner: `run.CLASSIFICATIONS` aliases it, so the two can never disagree.
CLASSIFICATIONS = ("self-imposed", "spec-answered", "capability", "open")
# The tier vocabulary is config's, not a second copy: a tier list that drifted
# from `config.TIER_ORDER` would validate decisions `model_for` then refuses.
TIERS = TIER_ORDER
SUB_ROW_RE = re.compile(r"^- \[ \] (?P<title>\S.*)$")
SUB_ROW_ID_RE = re.compile(r"^T\d+[a-z]?\b")


def path_covered(path: str, pattern: str) -> bool:
    """True when `pattern` (a contract forbidden entry) covers `path`."""
    if path == pattern:
        return True
    if fnmatch.fnmatch(path, pattern):
        return True
    base = pattern.rstrip("*").rstrip("/")
    return bool(base) and path.startswith(base + "/")


def validate_sub_rows(parent_id: str, rows: List[str]) -> List[str]:
    """Sub rows are titles; `Plan.split` allocates the ids (interfaces doc).

    serve.py's id regex is `\\bT\\d+\\b`, so a lettered id like `T60a` would
    vanish from the dashboard. The harness therefore allocates the next free
    numeric ids and appends `| split_of: <parent>` itself, and the Judge is not
    allowed to name an id at all.
    """
    errors = []
    rows = rows or []
    if len(rows) < 2:
        errors.append("split needs at least two sub_rows, got %d" % len(rows))
    seen = []
    for row in rows:
        m = SUB_ROW_RE.match((row or "").strip())
        if not m:
            errors.append("sub row breaks the plan grammar "
                          "`- [ ] <title>`: %r" % row)
            continue
        title = m.group("title").strip()
        if SUB_ROW_ID_RE.match(title):
            errors.append("sub row must not name a task id (%s): the harness "
                          "allocates the next free numeric ids and appends "
                          "`| split_of: %s`" % (title.split()[0], parent_id))
            continue
        if "split_of:" in title:
            errors.append("sub row must not carry `| split_of:`; the harness "
                          "appends it: %r" % row)
        key = title.lower()
        if key in seen:
            errors.append("duplicate sub row title %r" % title)
        seen.append(key)
    return errors


def validate_decision(decision: dict, inp: JudgeInput) -> List[str]:
    """[] means the harness will do what the Judge said. Spec §7 policy."""
    errors = []
    d = (decision or {}).get("decision")
    if d not in DECISIONS:
        return ["unknown decision %r (expected one of %s)" % (d, "|".join(DECISIONS))]
    if decision.get("classification") not in CLASSIFICATIONS:
        errors.append("missing or unknown classification %r (expected one of %s)"
                      % (decision.get("classification"), "|".join(CLASSIFICATIONS)))

    boot = inp.failure.startswith("boot-reconcile")
    if boot and d not in BOOT_DECISIONS:
        errors.append("boot reconciliation accepts only %s, not %r (spec §14)"
                      % ("|".join(BOOT_DECISIONS), d))
    if d == "revert-and-retry" and not boot:
        errors.append("revert-and-retry exists only for boot reconciliation "
                      "(spec §14); this failure is %s" % inp.failure)

    changes = decision.get("changes") or {}
    by_path = dict((f["path"], f["source"]) for f in inp.forbidden)

    for p in changes.get("forbidden_remove") or []:
        if p not in by_path:
            errors.append("forbidden_remove %s is not in the contract's "
                          "forbidden list" % p)
        elif by_path[p] != "scout":
            errors.append("forbidden_remove %s is %s-sourced; only scout-sourced "
                          "constraints may be widened" % (p, by_path[p]))

    for p in changes.get("allow_list_add") or []:
        for entry in inp.forbidden:
            if entry["source"] in ("plan", "spec") and path_covered(p, entry["path"]):
                errors.append("allow_list_add %s is covered by the %s-sourced "
                              "forbidden entry %s"
                              % (p, entry["source"], entry["path"]))

    if changes.get("default_choice") and inp.policy != "autonomous":
        errors.append("default_choice is only allowed under "
                      "`Decision policy: autonomous` (this loop is %s)" % inp.policy)

    if d == "widen" and not (changes.get("allow_list_add")
                             or changes.get("forbidden_remove")):
        errors.append("widen with neither allow_list_add nor forbidden_remove "
                      "changes nothing")
    if d == "split":
        errors.extend(validate_sub_rows(inp.task_id, changes.get("sub_rows")))

    # --- the judged tier, and the bounds the harness keeps (spec §6, §7) ---
    tier = changes.get("tier") or ""
    if tier and tier not in TIERS:
        errors.append("unknown tier %r (expected one of %s); `changes.tier` "
                      "names a tier, never a model" % (tier, "|".join(TIERS)))
    if d == "escalate":
        if not tier:
            errors.append("escalate must name changes.tier; this attempt ran "
                          "at %s" % inp.tier)
        elif tier in TIERS and inp.tier in TIERS:
            if next_tier(inp.tier) == inp.tier:
                errors.append("there is no tier above %s; split or defer "
                              "instead" % inp.tier)
            elif TIERS.index(tier) <= TIERS.index(inp.tier):
                errors.append("escalate must name a tier above %s; %s is not"
                              % (inp.tier, tier))

    if d == "resume" and not boot:
        if not inp.resume_session:
            errors.append("resume needs a session to resume; none was captured")
        if inp.resumes_left <= 0:
            errors.append("the resume budget (worker_resume_max) is spent for %s"
                          % inp.task_id)

    try:
        extend = int(changes.get("extend_cap_s") or 0)
    except (TypeError, ValueError):
        extend = -1
        errors.append("extend_cap_s must be a whole number of seconds")
    if extend > 0:
        if extend > inp.cap_default_s:
            errors.append("extend_cap_s %ds exceeds the phase default %ds"
                          % (extend, inp.cap_default_s))
        if inp.cap_extended:
            errors.append("%s has already used its one cap extension"
                          % inp.task_id)
        if d not in ("retry", "widen", "escalate", "resume"):
            errors.append("extend_cap_s means nothing on a %s decision" % d)
    elif extend < 0:
        errors.append("extend_cap_s must not be negative")

    if inp.attempt >= inp.max_attempts and d not in ("split", "defer", "halt"):
        errors.append("attempt %d of %d is the last (max_attempts); only split, "
                      "defer or halt remain" % (inp.attempt, inp.max_attempts))

    if judge_decision_count(inp.attempts) >= 2 and d not in ("defer", "halt"):
        errors.append("two Judge decisions on %s have already failed; "
                      "only defer or halt remain" % inp.task_id)
    return errors


def _normalize(decision: dict) -> dict:
    out = dict(decision or {})
    out.setdefault("rationale", "")
    out.setdefault("instruction", "")
    out.setdefault("alternatives", [])
    out.setdefault("reversal", "")
    out.setdefault("rejected", [])
    changes = dict(out.get("changes") or {})
    for key, empty in (("allow_list_add", []), ("forbidden_remove", []),
                       ("default_choice", ""), ("blocks", []), ("sub_rows", []),
                       ("tier", ""), ("extend_cap_s", 0)):
        changes.setdefault(key, empty)
    out["changes"] = changes
    return out


def _defer(reasons: List[str], original: dict) -> dict:
    """A refused decision is recorded verbatim and downgraded, never obeyed."""
    original = original or {}
    classification = original.get("classification")
    return _normalize({
        "decision": "defer",
        "classification": classification if classification in CLASSIFICATIONS else "open",
        "rationale": "Judge decision refused by policy: %s. Original rationale: %s"
                     % ("; ".join(reasons), one_line(original.get("rationale"))),
        "instruction": "",
        "alternatives": list(original.get("alternatives") or []),
        "reversal": "reverse nothing - no file was changed; decide the question "
                    "in LOOP_CLEANUP.md and re-run the task",
        "rejected": reasons,
        "changes": {"blocks": list((original.get("changes") or {}).get("blocks") or [])},
    })


def _budget_text(inp: JudgeInput) -> str:
    """The bounds, in one sentence, so the Judge never proposes a refused call."""
    bits = ["attempt %d of %d" % (inp.attempt, inp.max_attempts),
            "the last Worker ran at tier `%s`" % inp.tier,
            "the worker cap default is %d s" % inp.cap_default_s,
            ("this task's one cap extension is already spent" if inp.cap_extended
             else "one cap extension of up to %d s is still available"
                  % inp.cap_default_s),
            "%d resume(s) left in the budget" % inp.resumes_left,
            ("session %s can be resumed" % inp.resume_session
             if inp.resume_session else "there is no session to resume")]
    if inp.attempt >= inp.max_attempts:
        bits.append("this is the last attempt, so only split, defer or halt "
                    "will be accepted")
    return "; ".join(bits) + "."


def decide(ctx, inp: JudgeInput) -> dict:
    """Run the Judge phase and return a decision the harness is allowed to apply."""
    prompt = phases.render_prompt(
        "judge",
        loop_dir=ctx.loop_dir,
        worktree=ctx.cfg.worktree,
        policy=inp.policy,
        tier=inp.tier,
        budget=_budget_text(inp),
        boot_evidence=(inp.boot_evidence
                       or "(not a boot reconciliation — ignore this section)"),
        failure=inp.failure,
        task_row=inp.task_row,
        contract_json=inp.contract_json,
        forbidden=json.dumps(inp.forbidden, indent=2),
        gate_outputs="\n\n".join(inp.gate_outputs) or "(no gate output)",
        verdict=json.dumps(inp.verdict, indent=2) if inp.verdict else "{}",
        checkpoint=inp.checkpoint or "(no checkpoint)",
        spec_excerpt="\n\n".join(inp.excerpts)
                     or "(no plan or spec line names this task)",
        cleanup=inp.cleanup_text.strip() or "(empty)",
        attempts=json.dumps(inp.attempts, indent=2),
    )
    tier = ctx.cfg.role_tiers.get("judge") or "most-capable"
    result = claude_proc.run_phase(
        phase="judge",
        model=model_for(ctx.cfg, tier),
        prompt=prompt,
        cwd=ctx.cfg.worktree,
        timeout_s=phase_limit(ctx.cfg, "judge"),
        max_turns=20,
        max_budget_usd=None,
        env=dict(os.environ),
        events=ctx.events,
        tick=ctx.tick,
        role="judge",
        activity_path=os.path.join(ctx.runtime_dir, "last-activity"),
        allowed_tools=["Read", "Grep", "Glob"],
    )

    # `parse_json_block` RAISES on a reply with no usable block -- it does not
    # return None. A Judge that produced nothing usable must defer, not take the
    # harness down with it: this phase runs precisely when the loop is already
    # in trouble, and an uncaught exception here would lose the tick as well.
    try:
        raw = phases.parse_json_block(result.result_text, phase="judge") or {}
    except phases.JsonBlockError as exc:
        raw = {}
        malformed = str(exc)
    else:
        malformed = ""
    if not raw.get("decision"):
        out = _defer(["the Judge returned malformed output (no decision json "
                      "block)%s" % (": %s" % malformed if malformed else "")], raw)
    else:
        errors = validate_decision(raw, inp)
        out = _defer(errors, raw) if errors else _normalize(raw)
    out["_phase"] = result
    return out
