"""The harness: one Python process per loop.

The harness owns the state machine. It reads the plan, picks the task, dispatches
one `claude -p` subprocess per phase, runs the gate, commits, and decides what
happens next. No model is asked what to do — every phase returns JSON and this
file acts on it.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from . import (config, contract as contract_mod, events as events_mod, gate,
               git_ops, harness, incidents, migrate, phases, plan as plan_mod,
               sidecar, status, task_state, util)

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

# How many Worker attempts one task gets from plan A's stand-in: one dispatch
# plus one same-tier re-dispatch. Spec §4.2's ceiling is 3, but the third attempt
# is the Judge's to spend — it exists so a decision can change something, and in
# plan A nothing would change between attempt 2 and attempt 3. `attempt_bound`
# lets `max_attempts` lower this and says so in the log when it cannot raise it.
PLAN_A_MAX_ATTEMPTS = 2

# Spec §7's classification vocabulary, in full. Nothing else may be emitted:
# `classification` is the Judge's read of WHY a task failed, and plan A has no
# Judge, so every site here answers `open` — the one value that claims nothing.
CLASSIFICATIONS = ("self-imposed", "spec-answered", "capability", "open")
UNJUDGED = "open"


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
    preexisting: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Snapshot what was already dirty before this run wrote anything.

        The sandbox reverts what the Worker put outside its allow_list. Without
        a baseline it cannot tell that from what the human left in the tree
        before the loop started, so an ordinary uncommitted edit -- or an
        untracked scratch file -- reads as a stray and is destroyed on the first
        successful tick, not merely on a failure. Captured here, at
        construction, because every later point is after a Worker has run.
        """
        try:
            self.preexisting = git_ops.changed_paths(self.worktree)
        except Exception:
            self.preexisting = []

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


def _revert_reporting(h: Harness, paths: List[str], label: str) -> List[str]:
    """Revert `paths`, then RE-READ the tree and say what actually came back.

    The log used to assert the revert rather than observe it: `git_ops.revert`
    cannot remove a directory-shaped stray (a nested repository, a dirty
    submodule — git reports each as one porcelain entry and refuses to descend),
    and the failure was swallowed while the harness logged "reverted N path(s)".
    A containment step that silently fails is worse than one that refuses out
    loud, because only the second leaves evidence. One extra `git status` per
    revert buys an observation instead of a claim.

    Returns the paths that survived.
    """
    if not paths:
        return []
    git_ops.revert(h.worktree, paths)
    still = set(git_ops.changed_paths(h.worktree))
    survived = [p for p in paths if p in still]
    reverted = [p for p in paths if p not in still]
    if reverted:
        h.log("%s: reverted %d path(s): %s"
              % (label, len(reverted), ", ".join(reverted[:5])))
    if survived:
        h.log("%s: COULD NOT revert %d path(s) and did not try harder — git reports "
              "each as a directory it will not descend into (a nested repository or "
              "a dirty submodule), and deleting one recursively would destroy "
              "uncommitted work this loop did not create. They are still in the "
              "tree: %s" % (label, len(survived), ", ".join(survived[:5])))
    return survived


def _whole_renames(h: Harness, stray: List[str]) -> List[str]:
    """Pull in the other side of any rename that is partly a stray.

    A rename is one operation, but the allow_list judges each name separately.
    Renaming a sanctioned file to an unsanctioned name makes only the
    destination a stray, and reverting that alone leaves the tree with NEITHER
    name -- the destination deleted and the source still renamed away. Undoing
    both sides restores the source from HEAD and removes the destination, which
    is the state before the Worker touched it.
    """
    if not stray:
        return stray
    out = list(stray)
    for src, dst in git_ops.rename_pairs(h.worktree):
        if dst in out and src not in out:
            out.append(src)
        elif src in out and dst not in out:
            out.append(dst)
    return out


def _minus_preexisting(h: Harness, stray: List[str],
                       label: str) -> Tuple[List[str], List[str]]:
    """Drop paths that were already dirty before this run started.

    A path the human had already edited, or an untracked file they left lying
    about, is not something the Worker put there, and reverting it destroys
    work the loop never created. If the Worker also touched such a path the two
    edits are indistinguishable in the tree, so it is spared and named: a dirty
    gate is recoverable, a deleted file is not.
    """
    if not h.preexisting:
        return stray, []
    base = set(h.preexisting)
    spared = [p for p in stray if p in base]
    if spared:
        h.log("%s: spared %d path(s) that were already modified before this run "
              "started, so they are not this loop's to revert: %s"
              % (label, len(spared), ", ".join(sorted(spared))))
    return [p for p in stray if p not in base], spared


def sandbox(h: Harness, contract) -> List[str]:
    """Revert everything the contract did not sanction. Runs after EVERY Worker.

    Including a killed one (spec §6.4) — that is what keeps a timed-out phase
    from leaving the tree ambiguous, and a Worker killed mid-edit is exactly when
    a stray is most likely. The stray set comes from `git_ops.strays` over
    `git_ops.changed_paths`, never from a hand-built list: a rename reports both
    of its names there, and a list assembled anywhere else drops one of them.

    Returns every stray FOUND. What was actually reverted, and what survived, is
    in the log — a directory stray cannot be reverted and is refused by name.
    """
    changed = git_ops.changed_paths(h.worktree)
    stray = git_ops.strays(changed, list(contract.allow_list) + _protected(h))
    stray = _whole_renames(h, stray)
    stray, spared = _minus_preexisting(h, stray, "sandbox")
    _revert_reporting(h, stray, "sandbox")
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
        # §7's enum is the Judge's read of WHY a task failed; a harness that died
        # is none of self-imposed, spec-answered or capability, and plan A has no
        # Judge to say more. `open` claims nothing, which is the truth. The
        # boot-reconcile fact rides in `cause`, a field of our own event.
        h.events.emit("decision", task=task.id, decision="resume",
                      classification=UNJUDGED, cause="boot-reconcile")
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
        stray, _spared = _minus_preexisting(h, stray, "boot-reconcile")
        kept = [p for p in changed if p not in stray]
        survived = _revert_reporting(h, stray, "boot-reconcile")
        h.resume_phase = "scout"
        h.log("boot-reconcile WARNING: %s is [~] but runtime/task-%s.json does not "
              "exist, so nothing records how far it got. Kept %d change(s) inside "
              "allow_list, reverted %d stray path(s) (%d could not be reverted and "
              "are named above), and re-running the task from SCOUT."
              % (task.id, task.id, len(kept), len(stray) - len(survived), len(survived)))
        h.events.emit("decision", task=task.id, decision="retry",
                      classification=UNJUDGED, cause="boot-reconcile")

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


def attempt_bound(cfg) -> int:
    """How many Worker attempts one task actually gets. Never more than plan A's.

    `Limits: max_attempts=` is a hand-edited, documented key that `/agent-loop-setup`
    writes, and until now it was parsed, defaulted to 3 and then ignored — an
    operator who raised it got no extra attempt and no warning, which is a trap
    rather than a deferral. So it is honoured here as a CEILING that can only
    lower the bound: `max_attempts=1` genuinely means "no retry" today, and a
    value above plan A's two is clamped with a log line saying why. It cannot
    raise the bound because the third attempt exists so that a DECISION can change
    something — the Judge's tier, a widened allow_list, a split (spec §7) — and in
    plan A nothing would differ between attempt two and attempt three except the
    bill.
    """
    try:
        configured = int(cfg.limits.get("max_attempts", PLAN_A_MAX_ATTEMPTS))
    except (TypeError, ValueError):
        configured = PLAN_A_MAX_ATTEMPTS
    return max(1, min(PLAN_A_MAX_ATTEMPTS, configured))


def failure_signature(reason: str, phase: str, task_id: str) -> str:
    """Keyed on what actually repeated, not on `rc=124 after 1800s` (spec §8)."""
    return "%s|%s|%s" % (reason, phase, task_id)


def _loop_written(h: Harness, contract) -> List[str]:
    """The changed paths this loop was SANCTIONED to write, and only those.

    The old rule here was "everything outside the loop's own dir is this task's
    partial work". It is not, and the assumption is destructive in a way that
    cannot be undone: on the `invalid-contract` path no Worker has run at all, so
    every changed path belongs to the human — and a hand-written untracked file
    was deleted, an unrelated modified file reset to HEAD, to recover from the
    loop's OWN Scout failing validation twice.

    What the loop actually wrote is what the contract permitted it to write. The
    sandbox has already run after every Worker, so anything still outside the
    allow_list is either the human's or something the sandbox could not revert
    (a directory stray, reported by name in the log) — and neither is this
    function's to destroy. With no contract in force, the loop wrote nothing and
    this list is empty.
    """
    changed = git_ops.changed_paths(h.worktree)
    outside_loop = git_ops.strays(changed, _protected(h))
    if contract is None:
        return []
    sanctioned = set(changed) - set(git_ops.strays(changed, list(contract.allow_list)))
    # A path the human had already dirtied before this run is not the loop's
    # work even when the contract later allow-listed it: the two edits are
    # indistinguishable in the tree, and only one of them can be recovered.
    base = set(h.preexisting)
    return [p for p in outside_loop if p in sanctioned and p not in base]


def mark_blocked(h: Harness, task, reason: str, evidence: str, contract=None) -> None:
    """`[!]`, a LOOP_CLEANUP entry with the evidence, and the blocked-upstream fan-out.

    Reverts the loop's own work first: a half-applied workaround left in the tree
    is worse than no attempt at all. `contract` is what bounds "own" — see
    `_loop_written`. Passing None (no Worker ran; no contract was ever in force)
    leaves the tree exactly as it was found.
    """
    changed = git_ops.changed_paths(h.worktree)
    to_revert = _loop_written(h, contract)
    _revert_reporting(h, to_revert, "blocked %s" % task.id)
    spared = [p for p in git_ops.strays(changed, _protected(h))
              if p not in set(to_revert)]
    if spared:
        h.log("blocked %s: left %d changed path(s) alone — %s, so they are not this "
              "loop's to revert: %s"
              % (task.id, len(spared),
                 "no contract was ever in force on this tick" if contract is None
                 else "they are outside the contract's allow_list",
                 ", ".join(spared[:5])))

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
    h.events.emit("decision", task=task.id, decision="defer",
                  classification=UNJUDGED, cause=reason)
    git_ops.commit(h.worktree, [h.plan_path, cleanup_path],
                   "loop(%s): blocked — %s" % (task.id, reason),
                   {"Loop-Status": "halted",
                    "Loop-Verification": verification_summary(h.cfg, False)})
    h.log("blocked %s (%s); %d downstream task(s) marked blocked-upstream"
          % (task.id, reason, len(downstream)))


def handle_failure(h: Harness, task, reason: str, evidence: str, contract=None) -> str:
    """What to do when a task cannot pass. Returns the tick verdict.

    # plan C: this is where `judge.decide(ctx, contract, results, verdict,
    # task_state.TaskState.load(...))` is called, and its decision (retry |
    # escalate | widen | resume | split | defer | halt) replaces everything below.
    # In plan A the stand-in is the last two lines: defer, then apply the blocker
    # policy. Note what is NOT here: nothing consults config.next_tier.
    """
    h.log("failure signature %s" % failure_signature(reason, "execute", task.id))
    mark_blocked(h, task, reason, evidence, contract=contract)
    return "halt" if h.cfg.blocker_policy == "halt" else "continue"


def run_worker_attempt(h: Harness, ctx, contract, attempt: int, findings: str = "",
                       tier: Optional[str] = None, state=None):
    """One Worker dispatch plus the sandbox that always follows it.

    `tier` is the seam plan C's Judge writes through (`changes.tier`); plan A
    never passes it, so every attempt runs at the configured tier.

    `state` is optional only so the brief's signature still holds; pass it. The
    Worker and the sandbox are separate phases in spec §14's PHASES list, and
    recording the boundary is what stops a kill DURING the sandbox reading as a
    kill inside WORK -- which would send the boot rule to `wrapup` and spend a
    second Worker budget on top of a half-reverted tree.
    """
    ctx.attempt = attempt
    if state is not None:
        state.begin_phase("WORK")
    result, data = phases.run_worker(ctx, contract, tier=tier, findings=findings)
    if state is not None:
        state.end_phase(result)
        state.begin_phase("SANDBOX")
    sandbox(h, contract)
    if state is not None:
        state.end_phase()
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
    if resume == "wrapup":
        # A wrap-up resume spends a real Worker (below, with the checkpoint note
        # as its findings), so it is bounded by `worker_resume_max` -- spec §11
        # item 4 puts that limit in the harness's hands. `gate` is exempt: it
        # skips the Worker entirely, which is the whole point of that branch.
        cap = int(h.cfg.limits.get("worker_resume_max",
                                   config.DEFAULT_LIMITS["worker_resume_max"]))
        if state.resumes_spent() >= cap:
            h.log("%s: worker_resume_max=%d already spent, so this kill starts a "
                  "fresh attempt from SCOUT rather than resuming a Worker again — "
                  "resuming forever inside one attempt would bound nothing."
                  % (task.id, cap))
            resume = "scout"
        else:
            h.log("%s: resuming a killed Worker (resume %d of %d)"
                  % (task.id, state.resumes_spent() + 1, cap))
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
            # contract=None on purpose: two Scouts failed validation, no Worker
            # ran, and nothing in the tree is the loop's to revert.
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
            # The sandbox is local, idempotent and costs no model, and the kill
            # may have landed in the middle of the previous one -- so re-run it
            # rather than gate a tree whose containment never finished.
            state.begin_phase("SANDBOX")
            sandbox(h, contract)
            state.end_phase()
            h.log("%s: resuming at GATE — the recorded Worker had already returned"
                  % task.id)
        else:
            wres, _ = run_worker_attempt(h, ctx, contract, attempt,
                                         findings=findings, state=state)
            out.results.append(wres)
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
        # plan C: judge.decide(...) replaces this branch entirely, and it may spend
        # up to cfg.limits["max_attempts"] where plan A's stand-in is capped at
        # PLAN_A_MAX_ATTEMPTS by attempt_bound().
        bound = attempt_bound(h.cfg)
        if attempt >= bound and int(h.cfg.limits.get("max_attempts", bound)) > bound:
            h.log("%s: max_attempts=%s is configured but plan A's stand-in retries "
                  "once at the same tier, so the effective ceiling is %d. The third "
                  "attempt is the Judge's to spend (plan C) — it exists so a decision "
                  "can change something, and nothing here would change."
                  % (task.id, h.cfg.limits.get("max_attempts"), bound))
        if attempt < bound and verdict != "BLOCKER":
            # Same tier, with the Evaluator's findings in front of it. Spec §11
            # item 4: a re-attempt's tier is a judgement from the checkpoint, not
            # a ladder, and most overruns are one extra iteration — paying the top
            # tier for every one of them is how a cheap retry becomes expensive.
            attempt += 1
            findings = summary or "the previous attempt did not satisfy the contract"
            state.begin_attempt(phases.worker_tier(h.cfg))
            # NOT `capability`: a first NEEDS_WORK or a failed gate is no evidence
            # the model was incapable, and claiming it would put the wrong cause in
            # front of whoever reads the run back. Classifying a failure is the
            # Judge's job, and plan A has not asked one.
            h.events.emit("decision", task=task.id, decision="retry",
                          classification=UNJUDGED, cause=reason)
            h.log("%s %s on attempt %d — re-dispatching the Worker at the same tier "
                  "with the Evaluator's findings" % (task.id, reason, attempt - 1))
            continue
        evidence = "%s\n\n%s" % (summary, gate_text)
        out.verdict = handle_failure(h, task, reason, evidence, contract=contract)
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


class Stopped(Exception):
    """A signal asked the harness to stop."""


def parse_args(argv: List[str]) -> dict:
    """`--shim <path>` records the bash entry point. Everything else is ignored.

    The shim passes its own path so that `ps -o command= -p <pid>` still contains
    `run.sh` after the exec — which is the liveness test serve.py and all four
    skills use. Without it every live harness reads as dead.
    """
    shim = ""
    rest: List[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--shim" and i + 1 < len(argv):
            shim = argv[i + 1]
            i += 2
            continue
        rest.append(argv[i])
        i += 1
    return {"shim": shim, "rest": rest}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def plugin_version(plugin_root: str) -> str:
    record = util.read_json(os.path.join(plugin_root, ".claude-plugin", "plugin.json"))
    if isinstance(record, dict) and record.get("version"):
        return str(record["version"])
    return "0.0.0"


def resolve_paths() -> dict:
    loop_dir = os.environ.get("LOOP_DIR") or os.path.join(".claude", "loop", "run")
    return {"loop_dir": loop_dir,
            "runtime_dir": os.path.join(loop_dir, "runtime"),
            "config": os.environ.get("CONFIG_PATH") or os.path.join(loop_dir, "LOOP_CONFIG.md"),
            "plan": os.environ.get("PLAN_PATH") or os.path.join(loop_dir, "LOOP_PLAN.md"),
            "events": os.environ.get("EVENTS") or os.path.join(loop_dir, "events.jsonl"),
            "usage": os.path.join(loop_dir, "LOOP_USAGE.jsonl"),
            "status": os.environ.get("STATUS_FILE") or os.path.join(loop_dir, "LOOP_STATUS.md")}


def write_checkpoint(h: Harness, tick: int, reason: str) -> None:
    eligible = h.plan.eligible() if h.plan else []
    segments = h.plan.segments() if h.plan else []
    # A task left `[~]` is what resumes first -- the boot rule settles it before
    # anything eligible is picked up -- but `eligible()` only returns pending
    # rows, so reporting its head here answers the wrong question. A pause
    # mid-task would name "?" for the very task the loop was working on, which
    # is the one thing the operator and the Resume button need to know.
    doing = [t for t in (h.plan.tasks() if h.plan else []) if t.state == "doing"]
    next_task = (doing or eligible)
    util.write_json(os.path.join(h.runtime_dir, "CHECKPOINT.json"),
                    {"t": int(time.time()), "stopped_after_tick": tick,
                     "next_task": next_task[0].id if next_task else "?",
                     "segment": segments[-1].name if segments else "?",
                     "note": "%s requested; resume by re-running the launch command "
                             "or clicking Resume" % reason})


def memory_guard(h: Harness, tick: int) -> None:
    """Advisory: wait for headroom, then start the tick anyway.

    Refusing to work is worse than risking a retry, and the memory_pressure +
    sleep events make the wait legible instead of looking like a stall.
    """
    min_mb = env_int("MEM_MIN_MB", 1024)
    max_swap = env_int("SWAP_MAX_PCT", 90)
    backoff = env_int("MEM_BACKOFF", 60)
    max_delays = env_int("MEM_MAX_DELAYS", 5)
    delays = 0
    while True:
        free_mb, swap_pct = harness.mem_headroom()
        if harness.mem_guard_action(free_mb, swap_pct, min_mb, max_swap) == "proceed":
            return
        if delays >= max_delays:
            h.events.emit("memory_pressure", free_mb=free_mb, swap_used_pct=swap_pct,
                          action="proceed")
            h.log.feed("⚠ low memory headroom (%dMB free, swap %d%%) — starting tick "
                       "%d anyway after %d delays" % (free_mb, swap_pct, tick, delays))
            return
        h.events.emit("memory_pressure", free_mb=free_mb, swap_used_pct=swap_pct,
                      action="delay")
        h.events.emit("sleep", tick=tick, until=int(time.time()) + backoff,
                      reason="memory")
        h.log.feed("◌ waiting for memory headroom (%dMB free, swap %d%%) — retry in %ds"
                   % (free_mb, swap_pct, backoff))
        delays += 1
        time.sleep(backoff)


def sum_usage(results: List[Any]):
    total = {}
    for result in results:
        total = phases._merge_usage(total, getattr(result, "usage_by_model", {}) or {})
    return total


def usage_payload(by_model) -> dict:
    return dict((model, {"cost_usd": u.cost_usd, "input_tokens": u.input_tokens,
                         "output_tokens": u.output_tokens,
                         "cache_read_tokens": u.cache_read_tokens,
                         "cache_creation_tokens": u.cache_creation_tokens})
                for model, u in (by_model or {}).items())


def render_status(h: Harness, tick: int, mode: str, outcome: TickOutcome,
                  dur: int) -> None:
    """The session header plus one permanent line per tick, into LOOP_STATUS.md."""
    tasks = h.plan.tasks()
    done = len([t for t in tasks if t.state == "done"])
    total = len(tasks)
    segments = h.plan.segments()
    seg_done = len([s for s in segments
                    if s.tasks and all(t.state in plan_mod.DONE_STATES for t in s.tasks)])
    unplanned = len([s for s in segments if not s.tasks])
    display = outcome.verdict
    if display == "continue" and mode in ("plan", "review"):
        display = mode
    plan_usage = ""
    rl = util.read_json(os.path.join(h.runtime_dir, "ratelimit.json"))
    if isinstance(rl, dict) and rl.get("utilization") is not None:
        util.write_json(os.path.join(h.runtime_dir, "plan-usage.json"), rl)
    header = status.session_header(
        done, total, int(time.time()) - h.loop_start_epoch, 0, plan_usage,
        seg_done if unplanned else 0, len(segments) if unplanned else 0)
    line = status.tick_line(display, tick, outcome.task_id, dur, outcome.gates,
                            outcome.sha[:7], done, total, outcome.cause)
    h.tick_lines.append(line)
    h.log.feed(header)
    h.log.feed(line)
    status.write_status(resolve_paths()["status"], header, h.tick_lines)


def flush_plan(h: Harness) -> None:
    """Commit any plan edit still sitting in the tree (the last `done(+sha)` row)."""
    changed = git_ops.changed_paths(h.worktree)
    rel = os.path.relpath(os.path.abspath(h.plan_path), h.worktree)
    if rel in changed:
        git_ops.commit(h.worktree, [h.plan_path], "loop: checkpoint plan",
                       {"Loop-Status": "progress"})


def dispatch_postmortem(h: Harness) -> None:
    if os.environ.get("AGENT_LOOP_SKIP_POSTMORTEM") == "1":
        return
    try:
        subprocess.run(["claude", "--print", "--dangerously-skip-permissions",
                        "/agent-loop-postmortem"], cwd=h.worktree,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=env_int("POSTMORTEM_TIMEOUT", 900))
    except (OSError, subprocess.SubprocessError):
        h.log("the postmortem dispatch failed; the loop artefacts are all on disk")


def setup_dashboard(h: Harness):
    """(supervisor, adopted). Adoption ignores the setting — it governs spawning only.

    `LOOP_DASHBOARD` overrides `Dashboard:`, because serve.py spawns the harness
    with `LOOP_DASHBOARD=off` when it is already the dashboard (`w10` §4).
    """
    url = sidecar.adoptable(h.runtime_dir)
    if url:
        h.dashboard_url = url
        h.log.feed("◉ dashboard %s (adopted)" % url)
        return None, True
    mode = os.environ.get("LOOP_DASHBOARD") or h.cfg.dashboard
    if mode != "auto":
        return None, False
    url = sidecar.start(h.plugin_root, h.loop_dir, h.runtime_dir)
    if url:
        h.dashboard_url = url
        h.log.feed("◉ dashboard %s" % url)
    else:
        h.log("dashboard sidecar did not announce a URL — see %s/dashboard.out"
              % h.runtime_dir)
    supervisor = sidecar.Supervisor(
        h.runtime_dir, h.plugin_root, h.loop_dir,
        lambda n: incidents.incident_new(h.runtime_dir, h.events, "dashboard-crashloop",
                                         "warn", "sidecar died %d times — not "
                                         "restarting" % n, 0),
        interval=env_int("HB_INTERVAL", 10),
        max_restarts=env_int("DASH_MAX_RESTARTS", 5))
    supervisor.start()
    return supervisor, False


def run_loop(h: Harness) -> int:
    """Tick until the plan is finished, a human is needed, or someone says stop."""
    h.loop_start_epoch = int(time.time())
    tickseq = os.path.join(h.runtime_dir, "tickseq")
    pause_between = env_int("PAUSE_BETWEEN", 5)
    np_max = env_int("NP_MAX", 3)
    fail_streak = 0
    rl_streak = 0
    no_progress = 0
    remaining_prev = None

    # Spec §14: settle any [~] task BEFORE the first mode() call, or a dir
    # stopped mid-task reads as `stuck` and exits 1 without touching the work.
    h.plan = plan_mod.Plan.load(h.plan_path)
    boot_reconcile(h)

    while True:
        requested = pause_state(h)
        h.plan = plan_mod.Plan.load(h.plan_path)
        if requested:
            tick = util.read_int(tickseq)
            write_checkpoint(h, tick, requested)
            h.events.emit("paused", tick=tick)
            h.log("%s present; checkpoint written, exiting cleanly" % requested.upper())
            h.exit_reason = "paused"
            return EXIT_OK

        mode = h.plan.mode()
        if mode == "done":
            flush_plan(h)
            h.log("every task is done or skipped after %d ticks" % util.read_int(tickseq))
            h.exit_reason = "done"
            dispatch_postmortem(h)
            return EXIT_OK
        if mode == "stuck":
            flush_plan(h)
            h.exit_detail = ("work remains but nothing is eligible, plannable or "
                             "reviewable — see LOOP_CLEANUP.md")
            h.exit_reason = "halt"
            h.log("HALT: %s" % h.exit_detail)
            return EXIT_HALT

        tick = util.read_int(tickseq) + 1
        util.atomic_write(tickseq, str(tick))
        memory_guard(h, tick)

        started = int(time.time())
        tick_timeout = config.phase_limit(h.cfg, "tick_timeout")
        harness.write_tick_json(h.runtime_dir, tick, os.getpid(), started, tick_timeout)
        h.events.emit("tick_start", tick=tick, pid=os.getpid(),
                      timeout_at=started + tick_timeout)
        h.log("tick %d starting (%s)" % (tick, mode))
        try:
            if mode == "review":
                outcome = review_tick(h, tick, h.plan.next_review_segment().name)
            elif mode == "plan":
                outcome = plan_tick(h, tick, h.plan.next_plan_segment().name)
            else:
                outcome = execute_tick(h, tick, h.plan.eligible()[0])
        finally:
            harness.clear_tick_json(h.runtime_dir)

        dur = int(time.time()) - started
        by_model = sum_usage(outcome.results)
        rcs = [r.rc for r in outcome.results if getattr(r, "rc", None)]
        h.usage.write_tick(tick, mode, dur, by_model)
        h.events.emit("tick_end", tick=tick, verdict=outcome.verdict,
                      cause=outcome.cause, rc=max(rcs) if rcs else 0, dur=dur,
                      by_model=usage_payload(by_model))
        h.plan = plan_mod.Plan.load(h.plan_path)
        render_status(h, tick, mode, outcome, dur)

        for result in outcome.results:
            if result.stalled:
                incidents.handle_incident(h.runtime_dir, h.events, h.cfg,
                                          "tick-stalled", "warn",
                                          "the %s phase emitted nothing for %ds with no "
                                          "tool call outstanding"
                                          % (result.phase, env_int("STALL_S", 300)),
                                          tick, h.medic, h.log)
        timed_out = [r for r in outcome.results if r.timed_out]
        if timed_out:
            detail = "the %s phase exceeded its budget" % timed_out[0].phase
            if not incidents.handle_incident(h.runtime_dir, h.events, h.cfg,
                                             "phase-timeout", "error", detail, tick,
                                             h.medic, h.log):
                incidents.escalate(h.runtime_dir, h.events, "phase-timeout", detail,
                                   tick, h.worktree, h.loop_dir,
                                   os.path.join(h.plugin_root, "run.sh"),
                                   h.medic.last_id)
                h.exit_reason = "needs-human"
                h.exit_detail = detail
                return EXIT_NEEDS_HUMAN

        rate_limit = next((r.rate_limit for r in outcome.results if r.rate_limit), None)
        action = harness.ratelimit_action(rate_limit, int(time.time()),
                                          env_int("MAX_WAIT", 21600))
        if action == "exit":
            h.log("usage limit hit — state is saved on disk; re-run after your window "
                  "resets to resume")
            h.exit_reason = "rate-limit-exit"
            return EXIT_OK
        if action.startswith("wait "):
            secs = int(action.split()[1])
            h.events.emit("sleep", tick=tick, until=int(time.time()) + secs,
                          reason="rate-limit")
            h.log("usage limit hit — sleeping %ds until the window resets, then resuming"
                  % secs)
            time.sleep(secs)
            h.log.feed("▶ resumed · running tick %d" % (tick + 1))
            continue
        if any(r.api_error_status == "429" for r in outcome.results):
            rl_streak += 1
            if rl_streak >= env_int("RL_MAX_STRIKES", 5):
                h.log("rate-limited %dx with no reset info — state saved; re-run later"
                      % rl_streak)
                h.exit_reason = "rate-limit-exit"
                return EXIT_OK
            backoff = env_int("RL_BACKOFF", 60)
            h.events.emit("sleep", tick=tick, until=int(time.time()) + backoff,
                          reason="rate-limit")
            time.sleep(backoff)
            continue
        rl_streak = 0

        if outcome.verdict == "paused":
            write_checkpoint(h, tick, "pause")
            h.events.emit("paused", tick=tick)
            h.exit_reason = "paused"
            h.log("paused between phases; checkpoint written")
            return EXIT_OK
        if outcome.verdict == "halt":
            flush_plan(h)
            h.exit_reason = "halt"
            h.exit_detail = outcome.cause or "the loop cannot continue"
            h.log("HALT: %s" % h.exit_detail)
            return EXIT_HALT

        if outcome.cause in ("", "ok"):
            fail_streak = 0
        else:
            fail_streak += 1
            if fail_streak >= 3:
                detail = "3 consecutive failed ticks (last cause: %s)" % outcome.cause
                if not incidents.handle_incident(h.runtime_dir, h.events, h.cfg,
                                                 "garbage-ticks", "error", detail, tick,
                                                 h.medic, h.log):
                    incidents.escalate(h.runtime_dir, h.events, "garbage-ticks", detail,
                                       tick, h.worktree, h.loop_dir,
                                       os.path.join(h.plugin_root, "run.sh"),
                                       h.medic.last_id)
                    h.exit_reason = "needs-human"
                    h.exit_detail = detail
                    return EXIT_NEEDS_HUMAN
                fail_streak = 0

        remaining = len([t for t in h.plan.tasks() if t.state == "pending"])
        if mode != "execute" or remaining != remaining_prev:
            no_progress = 0
        else:
            no_progress += 1
            if no_progress >= np_max:
                detail = ("no progress in %d consecutive execute ticks (remaining stuck "
                          "at %d)" % (np_max, remaining))
                if not incidents.handle_incident(h.runtime_dir, h.events, h.cfg,
                                                 "no-progress", "error", detail, tick,
                                                 h.medic, h.log):
                    incidents.escalate(h.runtime_dir, h.events, "no-progress", detail,
                                       tick, h.worktree, h.loop_dir,
                                       os.path.join(h.plugin_root, "run.sh"),
                                       h.medic.last_id)
                    h.exit_reason = "needs-human"
                    h.exit_detail = detail
                    return EXIT_NEEDS_HUMAN
                no_progress = 0
        remaining_prev = remaining

        h.events.emit("sleep", tick=tick, until=int(time.time()) + pause_between,
                      reason="between-ticks")
        if pause_between:
            time.sleep(pause_between)


def teardown(h: Harness, hb, supervisor, adopted: bool, code: int) -> None:
    """One exit path: stop what we spawned, narrate why, drop the lock.

    loop_end is the only thing that makes a state terminal for an observer, so it
    is emitted on every exit — including a signal. An ADOPTED dashboard is left
    running; it outlives any one harness.
    """
    def _quietly(what: str, fn) -> None:
        """Run one teardown step; a failure in it must not skip the rest.

        Every step below is independent, and the last two are the ones that
        matter to anybody else: without `loop_end` every observer renders a
        finished loop as still running, and without `lock_release` the next
        harness has to reap a lock instead of taking it. An exception in
        stopping a dashboard is not a reason to lose either.
        """
        try:
            fn()
        except Exception as exc:                      # noqa: BLE001 - see above
            try:
                h.log("teardown: %s failed (%s); continuing" % (what, exc))
            except Exception:
                pass

    if supervisor is not None:
        _quietly("stopping the dashboard supervisor", supervisor.stop)
    elif not adopted:
        _quietly("stopping the sidecar", lambda: sidecar.stop(h.runtime_dir))
    _quietly("stopping the heartbeat", hb.stop)
    _quietly("emitting loop_end",
             lambda: h.events.emit("loop_end", reason=h.exit_reason or "error",
                                   detail=h.exit_detail, exit_code=code))
    _quietly("releasing the lock",
             lambda: harness.lock_release(h.runtime_dir, os.getpid()))
    _quietly("clearing tick.json", lambda: harness.clear_tick_json(h.runtime_dir))


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    paths = resolve_paths()
    loop_dir = paths["loop_dir"]
    runtime_dir = paths["runtime_dir"]
    os.makedirs(runtime_dir, exist_ok=True)
    # A dir created fresh at schema 3 runs no migration, so nothing else would
    # ever create artifacts/, LOOP_DECISIONS.md or the per-run .gitignore.
    migrate.ensure_layout(loop_dir)
    log = Log(os.path.join(loop_dir, "harness.log"))
    # Read the 1.x liveness evidence BEFORE this process writes anything.
    legacy_live = migrate.legacy_harness_live(loop_dir)

    try:
        cfg = config.load_config(paths["config"])
    except ValueError as exc:
        log("HALT: %s" % exc)
        return EXIT_HALT

    worktree = os.path.realpath(cfg.worktree) if cfg.worktree else ""
    if worktree != os.path.realpath(os.getcwd()):
        log("HALT: cwd %r is not the configured Worktree %r"
            % (os.path.realpath(os.getcwd()), worktree))
        return EXIT_HALT

    plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    version = plugin_version(plugin_root)
    events = events_mod.EventLog(paths["events"], os.path.join(runtime_dir, "eventseq"))

    lock = harness.lock_acquire(runtime_dir, os.getpid(), loop_dir, version)
    if lock.startswith("held:"):
        _, owner, since = lock.split(":", 2)
        log("another harness already owns %s: pid %s (since %s). Stop it with "
            "'kill %s', or use a different LOOP_DIR." % (loop_dir, owner, since, owner))
        incidents.incident_new(runtime_dir, events, "lock-conflict", "warn",
                               "pid %s alive since %s; refused second harness"
                               % (owner, since), 0)
        return EXIT_LOCK
    crashed_pid = lock.split(":", 1)[1] if lock.startswith("takeover:") else ""

    os.environ["LOOP_DIR"] = loop_dir
    os.environ["RUNTIME_DIR"] = runtime_dir
    os.environ.setdefault("LOOP_KNOWLEDGE",
                          os.path.join(os.path.dirname(os.path.abspath(loop_dir)),
                                       "KNOWLEDGE.md"))

    h = Harness(cfg=cfg, plugin_root=plugin_root, loop_dir=loop_dir,
                runtime_dir=runtime_dir, worktree=worktree,
                config_path=paths["config"], plan_path=paths["plan"], events=events,
                usage=events_mod.UsageLog(paths["usage"]), log=log,
                plan=plan_mod.Plan.load(paths["plan"]),
                medic=incidents.MedicState(env_int("MEDIC_MAX_PER_RUN", 3)))
    if args["shim"]:
        h.log("launched via %s" % args["shim"])

    def _signal(signum, frame):
        raise Stopped()

    signal.signal(signal.SIGINT, _signal)
    signal.signal(signal.SIGTERM, _signal)

    hb = harness.Heartbeat(runtime_dir, env_int("HB_INTERVAL", 10))
    hb.start()
    supervisor = None
    adopted = False
    code = EXIT_HALT
    try:
        supervisor, adopted = setup_dashboard(h)
        resume = 1 if util.read_int(os.path.join(runtime_dir, "tickseq")) > 0 else 0
        fields = {"pid": os.getpid(), "host": os.uname().nodename,
                  "plugin_version": version, "resume": resume}
        if h.dashboard_url:
            fields["dashboard_url"] = h.dashboard_url
        events.emit("loop_start", **fields)

        outcome = migrate.migrate_loop_dir(
            loop_dir, runtime_dir, events, migrate.LOOP_SCHEMA, legacy_live,
            os.environ.get("LOOP_MIGRATE_FORCE") == "1", version)
        if outcome.startswith("blocked:") or outcome.startswith("newer:"):
            if outcome.startswith("newer:"):
                kind = "schema-newer"
                detail = ("this loop dir is schema %s but plugin v%s only knows schema "
                          "%d — update the plugin on this machine, then re-run"
                          % (outcome.split(":", 1)[1], version, migrate.LOOP_SCHEMA))
            else:
                kind = "migration-blocked"
                detail = outcome.split(":", 1)[1]
            incidents.escalate(runtime_dir, events, kind, detail, 0, worktree, loop_dir,
                               os.path.join(plugin_root, "run.sh"))
            h.exit_reason = "needs-human"
            h.exit_detail = detail
            code = EXIT_NEEDS_HUMAN
            return code
        if outcome.startswith("migrated:"):
            steps = outcome.split(":")
            h.log("loop dir migrated: schema %s -> %s (%s)"
                  % (steps[1], steps[2], steps[3]))
            h.log.feed("⇡ schema %s → %s · %s" % (steps[1], steps[2], steps[3]))

        if crashed_pid:
            detail = "previous harness pid %s died without cleanup" % crashed_pid
            h.log("stale harness lock (pid %s dead) — taking over" % crashed_pid)
            if cfg.medic == "auto":
                if not incidents.handle_incident(runtime_dir, events, cfg,
                                                 "harness-crash", "error", detail, 0,
                                                 h.medic, h.log):
                    incidents.escalate(runtime_dir, events, "harness-crash", detail, 0,
                                       worktree, loop_dir,
                                       os.path.join(plugin_root, "run.sh"),
                                       h.medic.last_id)
                    h.exit_reason = "needs-human"
                    h.exit_detail = detail
                    code = EXIT_NEEDS_HUMAN
                    return code
            else:
                incidents.handle_incident(runtime_dir, events, cfg, "harness-crash",
                                          "warn", detail + "; the next tick "
                                          "re-evaluates any in-progress task", 0,
                                          h.medic, h.log)

        code = run_loop(h)
        return code
    except Stopped:
        h.exit_reason = "signal"
        code = EXIT_SIGNAL
        return code
    finally:
        teardown(h, hb, supervisor, adopted, code)


if __name__ == "__main__":
    sys.exit(main())
