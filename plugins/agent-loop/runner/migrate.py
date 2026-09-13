"""LOOP_SCHEMA — the version of a loop dir's LAYOUT, not of the plugin.

Prompt, UI and harness-internal changes never bump it. `runtime/schema` holds
the stamp and the harness is its only writer. A dir with no stamp but a tick
counter predates stamping (schema 1); a dir with neither is new. Each step's
own effects land on disk, then its migration event is recorded, and only then
does the stamp advance -- the stamp is the last write of the step, not the
first, so a kill anywhere in that sequence leaves the next boot able to
re-run (and, worst case, re-emit an already-idempotent event for) the same
step rather than either repeating destructive work or losing the audit trail
for a step that in fact completed.
"""
from __future__ import annotations

import glob
import os
import time
from typing import Any, Dict, List, Optional

from . import util

LOOP_SCHEMA = 3

# The same "pid alive AND heartbeat younger than 3x its interval" rule
# runner/harness.py's Heartbeat and web/serve.py's derive_status already use
# to decide a harness is gone (web/serve.py: `hb is None or hb > 3 *
# hb_interval`); reused here rather than invented, so "the harness is alive"
# has exactly one definition in this plugin. Not imported from runner.harness:
# migrate must keep importing nothing but util, so it can run before anything
# else is sane.
_HEARTBEAT_INTERVAL_S = 10.0
_HEARTBEAT_STALE_S = 3 * _HEARTBEAT_INTERVAL_S

# The v3 shape of runtime/sprint-<T>.json (spec §5). A 2.x contract predates the
# last five keys and wrote `forbidden` as bare strings; both make contract.validate
# fail on the first tick after an upgrade, which is a needless needs-human.
# Spelled out here rather than imported from contract.py: migrate must keep
# importing nothing but util, so it can run before anything else is sane.
_CONTRACT_DEFAULTS = (
    ("task", ""),
    ("success_criteria", []),
    ("allow_list", []),
    ("forbidden", []),
    ("verification", []),
    ("render_gate", None),
    ("fidelity_source", []),
    ("evaluator_must_read", []),
    ("evaluator_must_view", []),
    ("estimated_diff_lines", 0),
    ("scout_notes", ""),
    ("relevant_learnings", []),
)

_CLEAN_EXIT_MARKERS = ("LOOP_DONE after", "HALT:", "PAUSE present;",
                       "re-run run.sh", "NEEDS HUMAN:")
_TIMESTAMP_LEN = len("2026-01-01T00:00:00Z ")


def schema_read(runtime_dir: str, current: int) -> int:
    path = os.path.join(runtime_dir, "schema")
    if os.path.exists(path):
        n = util.read_int(path, 0)
        if n > 0:
            return n
    if os.path.exists(os.path.join(runtime_dir, "tickseq")):
        return 1
    return current


def legacy_harness_live(loop_dir: str, recent_s: int = 120) -> bool:
    """Evidence (never proof) that a pre-2.0 harness is still running.

    1.x wrote no pid file. The signal is: run.log was modified within recent_s
    AND its last timestamped harness line is not one of 1.x's clean-exit lines.
    Raw stream lines carry no leading timestamp and are ignored, so a subagent
    that merely mentions LOOP_DONE cannot make a live harness look stopped.
    Every ambiguous case (no timestamped line at all, an unreadable file with a
    recent mtime, or one that fails to decode as text) falls through to True:
    migrating under a live 2.x harness can corrupt a running loop, so the guard
    must err toward blocking, never toward proceeding. Must be evaluated before
    this process writes anything to run.log.
    """
    path = os.path.join(loop_dir, "run.log")
    try:
        age = time.time() - os.path.getmtime(path)
    except OSError:
        return False
    if age >= recent_s:
        return False
    last = ""
    try:
        body = util.read_text(path)
    except UnicodeDecodeError:
        # util.read_text only guards OSError; a binary/garbled run.log is not
        # an error it degrades for us, and this function's whole job is never
        # to raise on the boot path -- an undecodable file is exactly the kind
        # of thing we cannot read a clean-exit marker out of, so it reads the
        # same as "no timestamped line at all": live.
        return True
    for line in body.splitlines():
        if len(line) > _TIMESTAMP_LEN and line[4] == "-" and line[10] == "T" \
                and line[_TIMESTAMP_LEN - 1] == " ":
            last = line
    for marker in _CLEAN_EXIT_MARKERS:
        if marker in last:
            return False
    return True


def _heartbeat_age_s(runtime_dir: str, now: float) -> Optional[float]:
    """Seconds since runtime/HEARTBEAT's epoch stamp; None when unreadable.

    Mirrors web/serve.py's `_epoch_age` exactly (same one-line epoch file,
    same parse), so a HEARTBEAT this plugin already writes is read the same
    way everywhere it matters.
    """
    txt = util.read_text(os.path.join(runtime_dir, "HEARTBEAT")).strip()
    if not txt:
        return None
    try:
        stamp = int(float(txt.split()[0]))
    except (ValueError, IndexError):
        return None
    return max(0.0, now - stamp)


def v2_harness_live(runtime_dir: str) -> bool:
    """True iff a 2.x-or-later harness currently holds this dir's lock.

    Unlike 1.x, every 2.x+ harness writes `runtime/harness.json` (with its
    pid) and keeps `runtime/HEARTBEAT` fresh for as long as it runs -- so,
    unlike `legacy_harness_live`, this signal is proof, not inference, of the
    same "alive" definition `runner/harness.py`'s `Heartbeat` and
    `web/serve.py`'s `derive_status` already use: pid alive AND heartbeat no
    older than 3x its interval. A dead pid or a missing/stale heartbeat means
    the recorded lock is stale (the owner crashed without releasing it, which
    `harness.lock_release` already treats as reapable) -- not "unknown,
    therefore live"; run.log's mtime-based inference is the ambiguous one
    that needs that conservative fallback, not this one.
    """
    lock = util.read_json(os.path.join(runtime_dir, "harness.json"))
    if not isinstance(lock, dict):
        return False
    try:
        owner = int(lock.get("pid"))
    except (TypeError, ValueError):
        return False
    # Our own lock is not another harness. `main` takes the lock before it
    # migrates -- it has to, or two harnesses could migrate the same dir at
    # once -- so without this the harness reads its own pid and fresh
    # heartbeat as "a 2.x harness is live" and refuses to upgrade its own dir.
    if owner == os.getpid():
        return False
    if not util.process_alive(owner, "run.sh"):
        return False
    age = _heartbeat_age_s(runtime_dir, time.time())
    return age is not None and age <= _HEARTBEAT_STALE_S


def migrate_1_to_2(loop_dir: str, runtime_dir: str) -> str:
    """1.x -> 2.0: the tick prompt's runtime/LOCK is dead; the harness owns the mutex."""
    actions: List[str] = []
    lock = os.path.join(runtime_dir, "LOCK")
    if os.path.exists(lock):
        try:
            os.unlink(lock)
            actions.append("legacy-lock-removed")
        except OSError:
            pass
    return ",".join(actions) or "none"


def _ensure_gitignore(loop_dir: str) -> bool:
    """`artifacts/` must be ignored or the sandbox's stray-revert deletes screenshots."""
    path = os.path.join(loop_dir, ".gitignore")
    body = util.read_text(path)
    wanted = [line for line in ("runtime/", "artifacts/")
              if line not in body.splitlines()]
    if not wanted:
        return False
    parts = [body.rstrip("\n")] if body.strip() else []
    parts.extend(wanted)
    util.atomic_write(path, "\n".join(parts) + "\n")
    return True


def _normalise_contract(doc: Dict[str, Any]) -> Dict[str, Any]:
    """One 2.x sprint contract in the v3 shape. Field order follows spec §5."""
    out: Dict[str, Any] = {}
    for key, default in _CONTRACT_DEFAULTS:
        value = doc.get(key, default)
        if value is None and default is not None:
            value = default
        out[key] = value
    forbidden = []
    for item in out["forbidden"] or []:
        if isinstance(item, dict):
            forbidden.append({"path": item.get("path", ""),
                              "source": item.get("source") or "scout"})
        elif isinstance(item, str):
            # A 2.x Scout wrote bare paths. Provenance decides whether the Judge
            # may widen the constraint later, and the only honest answer for a
            # constraint the Scout invented is "scout".
            forbidden.append({"path": item, "source": "scout"})
    out["forbidden"] = forbidden
    for key in doc:
        out.setdefault(key, doc[key])
    return out


def _normalise_contracts(runtime_dir: str) -> int:
    """Rewrite every runtime/sprint-*.json in the v3 shape; return how many.

    A file that will not parse is left exactly as it is: it is evidence for the
    boot rule, and a migration that mangles evidence is worse than one that
    skips a file.
    """
    count = 0
    for path in sorted(glob.glob(os.path.join(runtime_dir, "sprint-*.json"))):
        doc = util.read_json(path)
        if not isinstance(doc, dict):
            continue
        util.write_json(path, _normalise_contract(doc))
        count += 1
    return count


def ensure_layout(loop_dir: str) -> List[str]:
    """Create the schema-3 files this loop dir is missing; name what was made.

    Shared by the 2 -> 3 migration and by the harness's own start-up, because a
    dir created fresh at schema 3 never runs a migration and would otherwise
    never get these at all -- `artifacts/` in particular, which is where plan
    B writes screenshots and which the sandbox would delete as a stray if it
    were not also in the per-run `.gitignore`. Idempotent: re-running it on a
    complete dir creates nothing and returns an empty list.
    """
    actions: List[str] = []
    artifacts = os.path.join(loop_dir, "artifacts")
    if not os.path.isdir(artifacts):
        os.makedirs(artifacts, exist_ok=True)
        actions.append("artifacts-dir")
    decisions = os.path.join(loop_dir, "LOOP_DECISIONS.md")
    if not os.path.exists(decisions):
        util.atomic_write(decisions, "# Loop Decisions\n\n"
                          "Autonomous choices the loop made, with their alternatives "
                          "and how to reverse them.\n")
        actions.append("decisions-file")
    log = os.path.join(loop_dir, "harness.log")
    if not os.path.exists(log):
        util.atomic_write(log, "")
        actions.append("harness-log")
    if _ensure_gitignore(loop_dir):
        actions.append("gitignore-artifacts")
    return actions


def migrate_2_to_3(loop_dir: str, runtime_dir: str) -> str:
    """2.x -> 3.0: the phase runner's new durable files, plus the contract shape.

    LOOP_PLAN.md is not touched, so a `[~]` task survives the upgrade untouched
    and is reconciled by the boot rule (spec §14) on the first tick.
    """
    actions = ensure_layout(loop_dir)
    normalised = _normalise_contracts(runtime_dir)
    if normalised:
        actions.append("contracts-normalised:%d" % normalised)
    return ",".join(actions) or "none"


_STEPS = {1: migrate_1_to_2, 2: migrate_2_to_3}


def migrate_loop_dir(loop_dir: str, runtime_dir: str, events, target: int,
                     legacy_live: bool, force: bool, version: str) -> str:
    """`ok:<n>` | `migrated:<from>:<to>:<actions>` | `blocked:<detail>` | `newer:<n>`.

    Per step: run the step's effects, record its migration event, THEN advance
    the stamp. That order (not effects-then-stamp-then-event) matters on a
    kill: if the stamp advanced first and the process died before the event
    was written, the next boot would see the new stamp, never revisit this
    step, and permanently lose the audit entry for a step that already
    happened. Emitting first means a kill between the event and the stamp
    instead produces, at worst, a re-run of an idempotent step and a harmless
    duplicate event -- detectable, unlike a silent gap.

    The live-harness guard applies to BOTH the 1.x case (`legacy_live`, which
    the caller must compute -- 1.x left no pid to check) and the 2.x+ case
    (`v2_harness_live`, computed here from `runtime_dir` and never from the
    caller): spec §14 requires refusing while a 2.x harness is live "exactly
    as 1->2 did," and this signature cannot grow a second boolean parameter
    (Task 18 already calls it), so the only way this guard cannot be forgotten
    by a future caller is for migrate.py to check it itself.
    """
    start = schema_read(runtime_dir, target)
    if start > target:
        return "newer:%d" % start
    if start == target:
        stamp = os.path.join(runtime_dir, "schema")
        if not os.path.exists(stamp):
            util.atomic_write(stamp, str(target))
        return "ok:%d" % target
    if not force:
        if start == 1 and legacy_live:
            return ("blocked:run.log was written in the last 2 minutes and its last "
                    "harness line is not a 1.x exit line, so the pre-2.0 harness may "
                    "still be running. Pause it (touch %s/PAUSE and wait for its "
                    "terminal to exit), kill its dashboard, then re-run; or re-run with "
                    "LOOP_MIGRATE_FORCE=1 if you are certain it is dead" % runtime_dir)
        if start >= 2 and v2_harness_live(runtime_dir):
            return ("blocked:runtime/harness.json names a pid that is alive and "
                    "runtime/HEARTBEAT is fresh, so a 2.x harness may still be running "
                    "against this dir. Pause it (touch %s/PAUSE and wait for its "
                    "terminal to exit), kill its dashboard, then re-run; or re-run with "
                    "LOOP_MIGRATE_FORCE=1 if you are certain it is dead" % runtime_dir)
    current = start
    actions = "none"
    while current < target:
        step = _STEPS.get(current)
        actions = step(loop_dir, runtime_dir) if step else "none"
        nxt = current + 1
        events.emit("migration", **{"from": current, "to": nxt,
                                    "actions": actions, "plugin_version": version})
        util.atomic_write(os.path.join(runtime_dir, "schema"), str(nxt))
        current = nxt
    return "migrated:%d:%d:%s" % (start, target, actions)
