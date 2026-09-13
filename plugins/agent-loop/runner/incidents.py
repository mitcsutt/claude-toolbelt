"""Incidents, the budgeted medic, and the one file a human is left with.

An incident is a fact about the run that an operator or the medic needs to see.
`warn` never stops the loop. `error` runs the medic when policy and budget
allow, and anything short of a `resumed` verdict is the caller's cue to
escalate -- which writes NEEDS_HUMAN.md. `escalate` is the terminal path: it
must degrade, never raise, because there is nothing downstream of it to catch
a failure.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from . import util

_MEDIC_OUTCOMES = ("resumed", "paused", "escalated", "noop")


@dataclass
class MedicState:
    """Per-harness-run medic budget and the last outcome, for the caller."""
    max_per_run: int = 3
    count: int = 0
    last_id: str = ""
    last_outcome: str = ""
    ran: bool = False


def _sanitize_for_applescript(text: str) -> str:
    """Make `text` safe to embed inside a double-quoted AppleScript string.

    Order matters: backslashes are doubled *before* quotes are stripped, so a
    literal backslash the caller supplied can never pair up with the quote
    character we insert as the string delimiter and read as an escaped quote
    (which would let the string keep consuming script text past where it was
    meant to end). Newlines would terminate the `osascript -e` argument early.
    """
    text = text.replace("\\", "\\\\")
    text = text.replace('"', "'")
    text = text.replace("\n", " ").replace("\r", " ")
    return text[:500]


def notify_desktop(title: str, body: str) -> None:
    """Best-effort push to the human at the keyboard. Never blocks, never fails."""
    if os.environ.get("LOOP_NOTIFY", "1") == "0":
        return
    title = _sanitize_for_applescript(title)
    body = _sanitize_for_applescript(body)
    try:
        system = os.uname().sysname
    except AttributeError:
        return
    try:
        if system == "Darwin":
            subprocess.run(["osascript", "-e",
                            'display notification "%s" with title "%s"' % (body, title)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        elif system == "Linux":
            subprocess.run(["notify-send", title, body], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return


def next_id(runtime_dir: str) -> str:
    """i-NNN, counted per loop dir (the counter persists under runtime/).

    The counter file is untrusted: missing, empty, zero-byte or garbage all
    read back as 0 via `util.read_int`, which is correct for a fresh run but
    would collide with real incidents already on disk if the counter itself
    were ever corrupted (a restored backup, a torn write) after some incidents
    had already been recorded. So the candidate id is also checked against
    `incident-<id>.json` on disk and bumped past any that already exist.
    """
    path = os.path.join(runtime_dir, "incidentseq")
    n = util.read_int(path, 0)
    while True:
        n += 1
        ident = "i-%03d" % n
        if not os.path.exists(os.path.join(runtime_dir, "incident-%s.json" % ident)):
            break
    util.atomic_write(path, str(n))
    return ident


def incident_new(runtime_dir: str, events, kind: str, severity: str, detail: str,
                 tick: int, phases: Optional[List[Dict[str, Any]]] = None) -> str:
    """Write incident-<id>.json for the medic and narrate one `incident` event."""
    ident = next_id(runtime_dir)
    util.write_json(os.path.join(runtime_dir, "incident-%s.json" % ident),
                    {"id": ident, "kind": kind, "severity": severity, "detail": detail,
                     "tick": int(tick or 0), "t": int(time.time()),
                     "phases": list(phases or [])})
    events.emit("incident", id=ident, kind=kind, severity=severity, detail=detail,
                tick=int(tick or 0))
    return ident


def phase_timeline(phase_results) -> List[Dict[str, Any]]:
    """Spec §8's timeline rows, from the tick's PhaseResults."""
    return [{"phase": p.phase, "model": p.model, "started": p.started,
             "ended": p.ended, "rc": p.rc, "session": p.session_id}
            for p in (phase_results or [])]


def raise_incident(ctx, kind: str, severity: str, detail: str,
                   phase_results=None) -> str:
    """Record an incident from a TickContext, with the tick's phase timeline.

    A thin adapter over `incident_new`, not a second writer: it converts the
    PhaseResults the caller already holds into §8's rows and unpacks the
    context. Raising an incident is all it does -- no medic is dispatched and
    nothing escalates, because the callers in plan C's failure path have already
    decided what happens next and only want the human to have the record.
    """
    return incident_new(ctx.runtime_dir, ctx.events, kind, severity, detail,
                        ctx.tick, phases=phase_timeline(phase_results))


def run_medic(runtime_dir: str, events, cfg, incident_id: str, state: MedicState,
              log=None) -> str:
    """Dispatch one budgeted medic tick; return its outcome token.

    No file, an unparseable file, or a timeout all mean the same thing: the
    medic did not conclude it fixed anything, so it is an escalation.
    """
    path = os.path.join(runtime_dir, "medic-%s.json" % incident_id)
    try:
        os.unlink(path)
    except OSError:
        pass
    state.ran = True
    state.count += 1
    state.last_id = incident_id
    events.emit("medic_start", id=incident_id)
    if log:
        log("medic %s starting (%d/%d this run)"
            % (incident_id, state.count, state.max_per_run))

    override = os.environ.get("LOOP_MEDIC_CMD")
    if override:
        argv = shlex.split(override)
    else:
        argv = ["claude", "--print", "--dangerously-skip-permissions"]
        if cfg.medic_model:
            argv.extend(["--model", cfg.medic_model])
    argv.append("/agent-loop-medic %s" % incident_id)

    env = dict(os.environ)
    env["MEDIC_INCIDENT_ID"] = incident_id
    env["RUNTIME_DIR"] = runtime_dir
    try:
        timeout = int(os.environ.get("MEDIC_TIMEOUT", "600"))
    except ValueError:
        timeout = 600
    try:
        subprocess.run(argv, env=env, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        pass

    record = util.read_json(path) or {}
    outcome = record.get("outcome")
    if outcome not in _MEDIC_OUTCOMES:
        outcome = "escalated"
    summary = record.get("summary", "") or ""
    events.emit("medic_end", id=incident_id, outcome=outcome, summary=summary)
    state.last_outcome = outcome
    if log:
        log("medic %s -> %s%s" % (incident_id, outcome, (" · " + summary) if summary else ""))
    return outcome


def handle_incident(runtime_dir: str, events, cfg, kind: str, severity: str,
                    detail: str, tick: int, state: MedicState, log=None) -> bool:
    """Raise the incident; return True when the loop may carry on.

    `warn` always may continue -- a medic never runs for one. `error` may
    continue only when a medic actually ran this call AND reported `resumed`;
    `paused`, `escalated`, `noop`, a missing/garbage medic file, `medic: off`,
    `medic: notify`, and a budget already spent for this run all fall through
    to `return False`, which is the caller's cue to call `escalate`. The
    permissive case is narrow on purpose: a wedged loop that keeps going on a
    fault nobody looked at is worse than one that stops on a fault that was
    actually fine.
    """
    state.ran = False
    state.last_outcome = ""
    state.last_id = incident_new(runtime_dir, events, kind, severity, detail, tick)
    if log:
        log("incident %s · %s · %s" % (state.last_id, kind, detail))
    if cfg.medic in ("auto", "notify"):
        notify_desktop("agent-loop: %s" % kind, detail)
    if severity == "warn":
        return True
    if cfg.medic == "auto" and state.count < state.max_per_run:
        return run_medic(runtime_dir, events, cfg, state.last_id, state, log) == "resumed"
    if cfg.medic == "auto" and log:
        log("medic budget exhausted (%d) — escalating %s" % (state.max_per_run, kind))
    return False


def escalate(runtime_dir: str, events, kind: str, detail: str, tick: int,
             worktree: str, loop_dir: str, run_sh: str, incident_id: str = "") -> None:
    """The loop cannot continue and no automation will fix it.

    Leave the human one file that says what broke, which worktree/loop dir it
    happened in and the exact command to resume. The caller exits 2
    immediately after. This function must never raise: it is the last thing
    between a wedged loop and a human's morning, so a write failure here (a
    full disk, a read-only runtime dir) degrades to "still record the
    incident and try to notify" rather than blowing up uncaught.
    """
    summary = ""
    next_step = ""
    if incident_id:
        record = util.read_json(os.path.join(runtime_dir,
                                             "medic-%s.json" % incident_id)) or {}
        summary = record.get("summary", "") or ""
        next_step = record.get("human_next_step", "") or ""
    lines = ["# agent-loop needs a human", "",
             "- incident: %s (%s)" % (incident_id or "none", kind),
             "- detail: %s" % detail,
             "- tick: %s" % tick,
             "- worktree: %s" % worktree,
             "- loop dir: %s" % loop_dir,
             "- when: %s" % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())]
    if summary:
        lines.append("- medic summary: %s" % summary)
    if next_step:
        lines.append("- medic next step: %s" % next_step)
    lines.extend(["", "## Resume", "",
                  '    cd %s && LOOP_DIR=%s bash "%s"' % (worktree, loop_dir, run_sh), ""])
    body = "\n".join(lines)
    handover = os.path.join(runtime_dir, "NEEDS_HUMAN.md")
    try:
        util.atomic_write(handover, body)
    except OSError:
        # Degrade rather than raise: an append is worse than an atomic
        # replace (a reader could see a half-written file) but a half-written
        # handover beats none, and either way the incident record below and
        # the notification are the backstop.
        try:
            with open(handover, "a") as f:
                f.write("\n" + body + "\n")
        except OSError:
            pass
    try:
        incident_new(runtime_dir, events, kind, "needs-human", detail, tick)
    except OSError:
        pass
    notify_desktop("agent-loop needs you", "%s: %s" % (kind, detail))
