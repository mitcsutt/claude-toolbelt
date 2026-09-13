"""The harness: one Python process per loop.

The harness owns the state machine. It reads the plan, picks the task, dispatches
one `claude -p` subprocess per phase, runs the gate, commits, and decides what
happens next. No model is asked what to do — every phase returns JSON and this
file acts on it.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional

from . import (config, contract as contract_mod, gate, git_ops, harness,
               incidents, phases, plan as plan_mod, task_state, util)

EXIT_OK = 0
EXIT_HALT = 1
EXIT_NEEDS_HUMAN = 2
EXIT_LOCK = 3
EXIT_SIGNAL = 130

PAUSE_FILE = "PAUSE"
STOP_FILE = "STOP"

# Severities that earn a row in the plan. A nit is written down in the review
# and nowhere else: turning every nit into a task is how a finished segment
# grows a tail longer than the segment.
FOLLOW_UP_SEVERITIES = ("should-fix", "must-fix")


class Log(object):
    """harness.log plus the operator's terminal. Never the loop's state source."""

    def __init__(self, path: str):
        self.rotating = harness.RotatingLog(path)

    def __call__(self, message: str) -> None:
        self._write("%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                               message))

    def feed(self, message: str) -> None:
        """A pretty, timestamp-free line for the human."""
        self._write(message)

    def _write(self, line: str) -> None:
        self.rotating.write(line)
        try:
            sys.stderr.write(line + "\n")
            sys.stderr.flush()
        except (OSError, ValueError):
            pass


@dataclass
class Harness:
    cfg: Any
    plugin_root: str
    loop_dir: str
    runtime_dir: str
    worktree: str
    config_path: str
    plan_path: str
    events: Any
    usage: Any
    log: Any
    plan: Any = None
    medic: Any = field(default_factory=incidents.MedicState)
    tick_lines: List[str] = field(default_factory=list)
    exit_reason: str = ""
    exit_detail: str = ""
    loop_start_epoch: int = 0
    dashboard_url: str = ""
    resume_task: str = ""
    resume_phase: str = ""

    def take_resume(self, task_id: str) -> str:
        """The boot rule's answer for this task — `gate`, `wrapup` or `scout`.

        Consumed once: a resume is a fact about the tick that follows the boot,
        not a standing property of the task.
        """
        if self.resume_task != task_id:
            return ""
        where = self.resume_phase
        self.resume_task = ""
        self.resume_phase = ""
        return where


@dataclass
class TickOutcome:
    verdict: str = "continue"          # continue | done | halt | retry | paused
    cause: str = "ok"
    task_id: str = ""
    sha: str = ""
    gates: str = ""
    results: List[Any] = field(default_factory=list)


def pause_state(h: Harness) -> str:
    """`stop` | `pause` | `""`. STOP wins: it is the more urgent request."""
    if os.path.exists(os.path.join(h.runtime_dir, STOP_FILE)):
        return "stop"
    if os.path.exists(os.path.join(h.runtime_dir, PAUSE_FILE)):
        return "pause"
    return ""


def _protected(h: Harness) -> List[str]:
    """Paths the sandbox must never revert: the loop's own dir, inside the worktree."""
    rel = os.path.relpath(os.path.abspath(h.loop_dir), h.worktree)
    return [rel, os.path.join(rel, "**")]


def sandbox(h: Harness, contract) -> List[str]:
    """Revert everything the contract did not sanction. Runs after EVERY Worker.

    Including a killed one (spec §6.4) — that is what keeps a timed-out phase
    from leaving the tree ambiguous, and a Worker killed mid-edit is exactly when
    a stray is most likely. The stray set comes from `git_ops.strays` over
    `git_ops.changed_paths`, never from a hand-built list: a rename reports both
    of its names there, and a list assembled anywhere else drops one of them.
    """
    changed = git_ops.changed_paths(h.worktree)
    stray = git_ops.strays(changed, list(contract.allow_list) + _protected(h))
    if stray:
        git_ops.revert(h.worktree, stray)
        h.log("sandbox: reverted %d path(s) outside allow_list: %s"
              % (len(stray), ", ".join(stray[:5])))
    return stray


def checkpoint_note(h: Harness, task) -> str:
    """The killed Worker's own checkpoint, rendered for the next Worker's prompt.

    # plan C: this is the wrap-up/resume path of spec §6 — SIGTERM, a bounded
    # `--resume <session>` wrap-up turn, then `claude -p --resume` with the
    # minutes left. Plan A cannot resume a session, so it does the next best
    # thing: a fresh Worker with the checkpoint in front of it, over a tree that
    # still holds the partial work. The checkpoint finally has a consumer either
    # way, which is the point of writing it first thing.
    """
    doc = util.read_json(os.path.join(h.runtime_dir, "worker-result.json")) or {}
    if not isinstance(doc, dict):
        doc = {}
    if doc.get("task") and doc.get("task") != task.id:
        return ""
    parts = []
    if doc.get("checkpoint"):
        parts.append("Checkpoint: %s" % doc["checkpoint"])
    if doc.get("summary"):
        parts.append("What it said it had done: %s" % doc["summary"])
    if doc.get("files_touched"):
        parts.append("Files it had already touched: %s"
                     % ", ".join(str(p) for p in doc["files_touched"][:20]))
    if doc.get("next_steps"):
        parts.append("Next steps it recorded: %s"
                     % "; ".join(str(s) for s in doc["next_steps"][:10]))
    if not parts:
        return ("A previous Worker on this task was interrupted and left no usable "
                "checkpoint. Re-establish the state from the working tree before "
                "you change anything.")
    return ("A previous Worker on this task was interrupted before it finished. Its "
            "own checkpoint follows, and the work it had already done is still in "
            "the tree — continue from there rather than starting over.\n\n"
            + "\n".join(parts))


def boot_reconcile(h: Harness) -> None:
    """Decide where a `[~]` task picks up. Runs once, before the first tick (spec §14).

    A `[~]` row means a harness died inside the task. `eligible()` only returns
    pending rows, so without this the task is never selected again and the plan
    reads as `stuck` — which is how a v2 dir stopped mid-task needed a human just
    to restart.
    """
    h.plan = plan_mod.Plan.load(h.plan_path)
    doing = [t for t in h.plan.tasks() if t.state == "doing"]
    if not doing:
        return
    task = doing[0]
    for extra in doing[1:]:
        h.log("boot-reconcile: %s is also [~]; resetting it to pending so it re-runs "
              "from the top on a later tick" % extra.id)
        h.plan.set_state(extra.id, "pending")

    if task_state.has_state(h.runtime_dir, task.id):
        state = task_state.TaskState.load(h.runtime_dir, task.id)
        h.resume_phase = task_state.boot_resume(state)
        h.log("boot-reconcile: %s was recorded in phase %r; resuming at %s"
              % (task.id, state.phase or "?", h.resume_phase))
        h.events.emit("decision", task=task.id, decision="resume",
                      classification="boot-reconcile")
    else:
        # No state file: a 2.x dir, or a crash before the first write.
        # plan C: hand this to the Judge with failure="boot-reconcile" and the
        # evidence the 2.x medic used to read by hand (the dirty tree against
        # allow_list, worker-result.json, the commit trailers, sprint-<T>.json);
        # it answers resume | retry | revert-and-retry | defer. Plan A takes the
        # mechanical branch: keep what the contract sanctioned, revert the rest,
        # and re-run the task from SCOUT.
        allow: List[str] = []
        try:
            allow = list(contract_mod.load_contract(
                os.path.join(h.runtime_dir, "sprint-%s.json" % task.id)).allow_list)
        except contract_mod.ContractError:
            pass
        changed = git_ops.changed_paths(h.worktree)
        stray = git_ops.strays(changed, allow + _protected(h))
        kept = [p for p in changed if p not in stray]
        if stray:
            git_ops.revert(h.worktree, stray)
        h.resume_phase = "scout"
        h.log("boot-reconcile WARNING: %s is [~] but runtime/task-%s.json does not "
              "exist, so nothing records how far it got. Kept %d change(s) inside "
              "allow_list, reverted %d stray path(s), and re-running the task from "
              "SCOUT." % (task.id, task.id, len(kept), len(stray)))
        h.events.emit("decision", task=task.id, decision="retry",
                      classification="boot-reconcile")

    h.resume_task = task.id
    h.plan.set_state(task.id, "pending")
    h.plan.save()


def verification_summary(cfg, ok: bool) -> str:
    tokens = cfg.verification or ["gate"]
    return " ".join("%s=%s" % (t, "pass" if ok else "fail") for t in tokens)


def commit_task(h: Harness, task, contract) -> str:
    """Commit exactly the allow_list paths the Worker touched, with the trailers."""
    changed = git_ops.changed_paths(h.worktree)
    outside = set(git_ops.strays(changed, contract.allow_list))
    paths = [p for p in changed if p not in outside]
    if not paths:
        return ""
    shown = ", ".join(paths[:20])
    if len(paths) > 20:
        shown += " +%d more" % (len(paths) - 20)
    return git_ops.commit(h.worktree, paths,
                          "loop(%s): %s" % (task.id, task.title),
                          {"Loop-Status": "done",
                           "Loop-Verification": verification_summary(h.cfg, True),
                           "Loop-Files": shown})


def failure_signature(reason: str, phase: str, task_id: str) -> str:
    """Keyed on what actually repeated, not on `rc=124 after 1800s` (spec §8)."""
    return "%s|%s|%s" % (reason, phase, task_id)


def mark_blocked(h: Harness, task, reason: str, evidence: str) -> None:
    """`[!]`, a LOOP_CLEANUP entry with the evidence, and the blocked-upstream fan-out.

    Reverts first: a half-applied workaround left in the tree is worse than no
    attempt at all. The worktree belongs to the loop and to nothing else, so
    everything outside the loop's own dir is this task's partial work.
    """
    changed = git_ops.changed_paths(h.worktree)
    to_revert = git_ops.strays(changed, _protected(h))   # everything but the loop dir
    if to_revert:
        git_ops.revert(h.worktree, to_revert)

    h.plan = plan_mod.Plan.load(h.plan_path)
    h.plan.set_state(task.id, "blocked")
    downstream = [t.id for t in h.plan.dependents(task.id)]
    for tid in downstream:
        h.plan.set_state(tid, "blocked-upstream")
    h.plan.save()

    cleanup_path = os.path.join(h.loop_dir, "LOOP_CLEANUP.md")
    entry = [
        "",
        "## %s — %s" % (task.id, reason),
        "",
        "- when: %s" % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "- task: %s" % task.raw.strip(),
        "- evidence:",
        "",
        "```",
        (evidence or "(none)").strip()[:4000],
        "```",
        "",
        "- decision a person must make: review the evidence above and either widen "
        "the contract, restate the task, or drop it. The loop will not retry %s "
        "until this entry is resolved." % task.id,
    ]
    if downstream:
        entry.append("- blocked downstream: %s" % ", ".join(downstream))
    entry.append("")
    existing = util.read_text(cleanup_path)
    header = "" if existing.strip() else "# Loop Cleanup\n"
    with open(cleanup_path, "a") as f:
        f.write(header + "\n".join(entry) + "\n")

    h.events.emit("task_status", id=task.id, status="blocked", sha="")
    h.events.emit("decision", task=task.id, decision="defer", classification="open")
    git_ops.commit(h.worktree, [h.plan_path, cleanup_path],
                   "loop(%s): blocked — %s" % (task.id, reason),
                   {"Loop-Status": "halted",
                    "Loop-Verification": verification_summary(h.cfg, False)})
    h.log("blocked %s (%s); %d downstream task(s) marked blocked-upstream"
          % (task.id, reason, len(downstream)))


def handle_failure(h: Harness, task, reason: str, evidence: str) -> str:
    """What to do when a task cannot pass. Returns the tick verdict.

    # plan C: this is where `judge.decide(ctx, contract, results, verdict,
    # task_state.TaskState.load(...))` is called, and its decision (retry |
    # escalate | widen | resume | split | defer | halt) replaces everything below.
    # In plan A the stand-in is the last two lines: defer, then apply the blocker
    # policy. Note what is NOT here: nothing consults config.next_tier.
    """
    h.log("failure signature %s" % failure_signature(reason, "execute", task.id))
    mark_blocked(h, task, reason, evidence)
    return "halt" if h.cfg.blocker_policy == "halt" else "continue"


def run_worker_attempt(h: Harness, ctx, contract, attempt: int, findings: str = "",
                       tier: Optional[str] = None):
    """One Worker dispatch plus the sandbox that always follows it.

    `tier` is the seam plan C's Judge writes through (`changes.tier`); plan A
    never passes it, so every attempt runs at the configured tier.
    """
    ctx.attempt = attempt
    result, data = phases.run_worker(ctx, contract, tier=tier, findings=findings)
    sandbox(h, contract)
    return result, data


def execute_tick(h: Harness, tick: int, task) -> TickOutcome:
    """SELECT -> SCOUT -> VALIDATE -> WORK -> SANDBOX -> GATE -> EVALUATE ->
    COMMIT -> LEARN, with one same-tier re-dispatch on failure.

    `runtime/task-<T>.json` is rewritten at every transition, so a kill costs the
    phase in flight and nothing more: the next boot reads it and picks up where
    this left off instead of paying for the whole task again.
    """
    out = TickOutcome(task_id=task.id)
    ctx = phases.TickContext(cfg=h.cfg, plan=h.plan, task=task, loop_dir=h.loop_dir,
                             runtime_dir=h.runtime_dir, events=h.events, tick=tick,
                             attempt=1)
    state = task_state.TaskState.load(h.runtime_dir, task.id)
    # Write-through, not a bare assignment: a crash between here and the first
    # phase transition would otherwise leave a state file with no pointer to the
    # contract the attempt was working to, which is the one thing that makes a
    # dirty tree legible to the boot rule.
    state.set_contract(os.path.join("runtime", "sprint-%s.json" % task.id))
    resume = h.take_resume(task.id)
    contract_path = os.path.join(h.runtime_dir, "sprint-%s.json" % task.id)

    h.plan.set_state(task.id, "doing")
    h.plan.save()
    base_sha = git_ops.head_sha(h.worktree)
    git_ops.commit(h.worktree, [h.plan_path], "loop: start %s" % task.id,
                   {"Loop-Status": "progress"})

    contract = None
    if resume in ("gate", "wrapup"):
        # The contract the killed attempt was working to is still on disk, and it
        # is the only thing that makes the partial work in the tree legible.
        try:
            contract = contract_mod.load_contract(contract_path)
        except contract_mod.ContractError:
            h.log("%s: no readable contract survived, so the resume falls back to a "
                  "fresh Scout" % task.id)
            resume = "scout"

    if contract is None:
        state.begin_attempt(phases.worker_tier(h.cfg))
        errors: List[str] = []
        for scout_try in (1, 2):
            state.begin_phase("SCOUT")
            res, contract, errors = phases.run_scout(ctx, validation_errors=errors)
            out.results.append(res)
            state.end_phase(res)
            if contract is not None and not errors:
                break
            if pause_state(h):
                out.verdict = "paused"
                return out
            h.log("contract for %s rejected (try %d): %s"
                  % (task.id, scout_try, "; ".join(errors)))
        if contract is None or errors:
            state.end_attempt("invalid-contract")
            out.verdict = handle_failure(h, task, "invalid-contract",
                                         "\n".join(errors) or "the Scout wrote no contract")
            out.cause = "invalid-contract"
            return out

    attempt = max(1, state.attempt)
    findings = checkpoint_note(h, task) if resume == "wrapup" else ""
    skip_worker = (resume == "gate")
    while True:
        ctx.attempt = attempt
        if pause_state(h):
            out.verdict = "paused"
            return out

        if skip_worker:
            # The previous harness recorded a Worker that returned, so its work is
            # already in the tree. Re-running it would spend a whole Worker budget
            # reproducing what is on disk.
            skip_worker = False
            h.log("%s: resuming at GATE — the recorded Worker had already returned"
                  % task.id)
        else:
            state.begin_phase("WORK")
            wres, _ = run_worker_attempt(h, ctx, contract, attempt, findings=findings)
            out.results.append(wres)
            state.end_phase(wres)
            if wres.stopped:
                # STOP, not a timeout: the operator asked for the loop to stop,
                # so the tick ends where it stands. The sandbox has already run,
                # and the row stays `[~]` for the boot rule to pick up.
                out.verdict = "paused"
                out.cause = "stop"
                return out
            if pause_state(h):
                out.verdict = "paused"
                return out
        findings = ""

        state.begin_phase("GATE")
        gate_results = gate.run_commands(
            contract.verification, h.worktree, config.phase_limit(h.cfg, "gate_cmd"),
            h.runtime_dir, "%s-%d" % (task.id, attempt))
        gate_ok = gate.all_ok(gate_results)
        gate_text = gate.outputs_text(gate_results)
        out.gates = verification_summary(h.cfg, gate_ok)
        state.end_phase()
        # plan B: run_visual_checks(h, ctx, contract) goes here, between GATE and
        # EVALUATE; a render or fidelity failure joins the gate_ok branch below.

        diff = git_ops.diff_text(h.worktree, base_sha, contract.allow_list)
        if task.class_flag == "mechanical":
            verdict = "PASS" if gate_ok else "NEEDS_WORK"
            summary = "mechanical task: the gate is the only judge"
        elif not gate_ok:
            verdict = "NEEDS_WORK"
            summary = "the verification gate failed"
        else:
            state.begin_phase("EVALUATE")
            eres, vdata = phases.run_evaluator(ctx, contract, diff, gate_text, [])
            out.results.append(eres)
            state.end_phase(eres)
            verdict = vdata["verdict"]
            summary = vdata.get("summary", "")

        if gate_ok and verdict == "PASS":
            state.begin_phase("COMMIT")
            sha = commit_task(h, task, contract)
            out.sha = sha
            h.plan = plan_mod.Plan.load(h.plan_path)
            h.plan.set_state(task.id, "done", sha=sha[:7] if sha else None)
            h.plan.save()
            h.events.emit("task_status", id=task.id, status="done", sha=sha[:7])
            state.end_phase()
            state.end_attempt("pass")
            state.begin_phase("LEARN")
            lres = phases.run_learner(ctx, contract, gate_text, summary)
            out.results.append(lres)
            state.end_phase(lres)
            out.verdict = "continue"
            return out

        reason = ("blocker" if verdict == "BLOCKER"
                  else "gate-failed" if not gate_ok else "needs-work")
        state.end_attempt(reason)
        # plan C: judge.decide(...) replaces this branch entirely, and its bound is
        # cfg.limits["max_attempts"] rather than the literal two below.
        if attempt < 2 and verdict != "BLOCKER":
            # Same tier, with the Evaluator's findings in front of it. Spec §11
            # item 4: a re-attempt's tier is a judgement from the checkpoint, not
            # a ladder, and most overruns are one extra iteration — paying the top
            # tier for every one of them is how a cheap retry becomes expensive.
            attempt += 1
            findings = summary or "the previous attempt did not satisfy the contract"
            state.begin_attempt(phases.worker_tier(h.cfg))
            h.events.emit("decision", task=task.id, decision="retry",
                          classification="capability")
            h.log("%s %s on attempt %d — re-dispatching the Worker at the same tier "
                  "with the Evaluator's findings" % (task.id, reason, attempt - 1))
            continue
        evidence = "%s\n\n%s" % (summary, gate_text)
        out.verdict = handle_failure(h, task, reason, evidence)
        out.cause = reason
        return out


def plan_tick(h: Harness, tick: int, segment: str) -> TickOutcome:
    """Expand one unplanned segment into rows, then validate what landed."""
    out = TickOutcome(verdict="continue", task_id="")
    ctx = phases.TickContext(cfg=h.cfg, plan=h.plan, task=None, loop_dir=h.loop_dir,
                             runtime_dir=h.runtime_dir, events=h.events, tick=tick,
                             attempt=1)
    before = len(h.plan.tasks())
    res, _ = phases.run_planner(ctx, segment)
    out.results.append(res)
    h.plan = plan_mod.Plan.load(h.plan_path)
    added = len(h.plan.tasks()) - before
    if added <= 0:
        h.log("the Planner added no rows to %r — nothing left to do here" % segment)
        out.verdict = "halt"
        out.cause = "plan-empty"
        return out
    git_ops.commit(h.worktree, [h.plan_path], "loop: plan %s" % segment,
                   {"Loop-Status": "progress"})
    h.log("planned %s: %d row(s)" % (segment, added))
    return out


def _follow_up_rows(plan, findings) -> List[str]:
    """The rows a review earns, with ids the harness owns rather than the model's.

    `prompts/reviewer.md` asks for `- [ ] T<n>: …` and says nothing about which
    `n` is free, so a Reviewer reusing a live id is a normal reply, not a
    malfunction. Two rows sharing an id is a corrupt plan — `Plan.task()` returns
    the first of them, so every later `set_state` flips the wrong row — and the
    numbering rule is the same one `Plan.split` follows: NUMERIC ids, assigned
    here. A proposed id that is genuinely free is kept, so the Reviewer's own
    cross-references still resolve.
    """
    rows: List[str] = []
    taken = set(t.id for t in plan.tasks() if t.id)
    for finding in findings or []:
        if not isinstance(finding, dict):
            continue
        if finding.get("severity") not in FOLLOW_UP_SEVERITIES:
            continue
        row = (finding.get("follow_up_row") or "").strip()
        if not row.lstrip().startswith("- ["):
            continue
        # `plan._parse_task` reads a row's id as the FIRST `T\d+` token in it, so
        # that is exactly what has to be checked here — anything else would call
        # a row unique that the plan will go on to read as a duplicate.
        proposed = plan_mod.ID_RE.search(row)
        if proposed is not None and proposed.group(0) not in taken:
            taken.add(proposed.group(0))
            rows.append(row)
            continue
        new_id = _next_free_id(taken)
        taken.add(new_id)
        rows.append(plan_mod._retitle(row, new_id))
    return rows


def _next_free_id(taken) -> str:
    """The lowest `T<n>` not in `taken`. Numeric only — serve.py greps `\\bT\\d+\\b`."""
    used = [int(tid[1:]) for tid in taken if tid.startswith("T") and tid[1:].isdigit()]
    n = (max(used) + 1) if used else 1
    while ("T%d" % n) in taken:
        n += 1
    return "T%d" % n


def review_tick(h: Harness, tick: int, segment: str) -> TickOutcome:
    """Grade a finished segment, append its follow-ups, stamp it reviewed."""
    out = TickOutcome(verdict="continue", task_id="")
    ctx = phases.TickContext(cfg=h.cfg, plan=h.plan, task=None, loop_dir=h.loop_dir,
                             runtime_dir=h.runtime_dir, events=h.events, tick=tick,
                             attempt=1)
    seg = None
    for candidate in h.plan.segments():
        if candidate.name == segment:
            seg = candidate
    base = ""
    if seg is not None and seg.tasks:
        first_sha = next((t.sha for t in seg.tasks if t.sha), "")
        base = "%s~1" % first_sha if first_sha else ""
    if not base:
        h.log("no task in %r carries a sha — reviewing against the working tree only"
              % segment)
    diff = git_ops.diff_text(h.worktree, base, [])
    res, data = phases.run_reviewer(ctx, segment, diff)
    out.results.append(res)

    h.plan = plan_mod.Plan.load(h.plan_path)
    rows = _follow_up_rows(h.plan, data.get("findings"))
    if rows:
        h.plan.append_tasks(segment, rows)
    h.plan.stamp_reviewed(segment, git_ops.head_sha(h.worktree)[:7] or "none")
    h.plan.save()
    git_ops.commit(h.worktree, [h.plan_path], "loop: review %s" % segment,
                   {"Loop-Status": "reviewed"})
    h.log("reviewed %s: %d follow-up row(s)" % (segment, len(rows)))
    return out
