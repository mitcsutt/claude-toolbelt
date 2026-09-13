#!/usr/bin/env python3
"""agent-loop live dashboard server (Python 3 stdlib only).

Reads the loop's existing artefacts under $LOOP_DIR, tails events.jsonl
incrementally into a bounded EventStore, serves a single-page dashboard over
HTTP + SSE from one shared 1 Hz snapshot, and drives Start/Pause/Resume/Stop
via the runtime/PAUSE file. Headless `bash run.sh` is unaffected. The server
creates no scratch dir; it reuses $LOOP_DIR and writes only runtime/PAUSE,
runtime/STOP + runtime/dashboard.json.

Status is derived from events.jsonl + PID liveness only. harness.log (run.log
on an unmigrated v2 dir) is forensic: it is read for the 12-line display tail
and for nothing else. Nothing here greps it for HALT:/LOOP_DONE — a subagent
prompt quoting those tokens used to flip the dashboard to HALTED mid-run.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# Roles whose tier overrides may appear in LOOP_CONFIG.md.
ROLES = ("Planner", "Scout", "Worker", "Evaluator")


def pct(done, total):
    """Integer percent 0..100, rounded to nearest; 0 when total is 0."""
    return (done * 100 + total // 2) // total if total > 0 else 0


# Mirror lib/loop.sh grep semantics exactly. A task line is "- [<one char>] ".
_TASK_RE = re.compile(r"^\s*- \[(.)\] (.*)$")
_STATUS = {" ": "pending", "~": "doing", "x": "done", "!": "blocked", "-": "skipped"}


def count_tasks(plan_text):
    """{'done','total','remaining'} counted the same way lib/loop.sh greps."""
    done = total = remaining = 0
    for line in plan_text.splitlines():
        m = _TASK_RE.match(line)
        if not m:
            continue
        total += 1
        if m.group(1) == "x":
            done += 1
        elif m.group(1) == " ":
            remaining += 1
    return {"done": done, "total": total, "remaining": remaining}


def parse_plan(plan_text):
    """Structured tasks + per-segment breakdown + overall progress."""
    tasks = []
    segments = []
    seg_index = {}
    current_seg = None
    for line in plan_text.splitlines():
        if line.startswith("## "):
            current_seg = line[3:].strip()
            if current_seg not in seg_index:
                seg = {"name": current_seg, "done": 0, "total": 0, "goal": None}
                seg_index[current_seg] = seg
                segments.append(seg)
            continue
        m = _TASK_RE.match(line)
        if not m:
            # A "Goal:" line under a segment header describes the segment (used
            # for future/unplanned segments that have no tasks yet). First wins.
            if current_seg is not None:
                seg = seg_index[current_seg]
                stripped = line.strip()
                if seg.get("goal") is None and stripped.startswith("Goal:"):
                    seg["goal"] = stripped[len("Goal:"):].strip()
            continue
        flag, rest = m.group(1), m.group(2)
        tid_m = re.search(r"\bT\d+\b", rest)
        model_m = re.search(r"\|\s*model:\s*(\S+)", rest)
        # description = text before the first " | " metadata divider, id stripped.
        desc = rest.split(" | ")[0]
        desc = re.sub(r"^T\d+:\s*", "", desc).strip()
        task = {
            "id": tid_m.group(0) if tid_m else None,
            "status": _STATUS.get(flag, "pending"),
            "segment": current_seg,
            "desc": desc,
            "model": model_m.group(1) if model_m else None,
            "mechanical": bool(re.search(r"\|\s*mechanical\b", rest)),
        }
        tasks.append(task)
        if current_seg is not None:
            seg = seg_index[current_seg]
            seg["total"] += 1
            if flag == "x":
                seg["done"] += 1
    counts = count_tasks(plan_text)
    progress = {
        "done": counts["done"],
        "total": counts["total"],
        "remaining": counts["remaining"],
        "pct": pct(counts["done"], counts["total"]),
    }
    return {"tasks": tasks, "segments": segments, "progress": progress}


_MODEL_FIELDS = ("cost_usd", "input_tokens", "output_tokens",
                 "cache_read_tokens", "cache_creation_tokens")


def _canon_model(model_id):
    """Collapse provider/region/context-window variants to one key.

    The same model surfaces under several ids in a run's modelUsage:
    `claude-sonnet-4-6`, `us.anthropic.claude-sonnet-4-6`,
    `claude-opus-4-8[1m]`, `...-v1:0`. Grouping on the raw id double-lists
    them and corrupts per-model attribution. Strip the known affixes.
    Introduces no literal model alias — it only removes prefixes/suffixes.
    """
    if not model_id:
        return model_id
    mid = str(model_id)
    for prefix in ("us.anthropic.", "anthropic."):
        if mid.startswith(prefix):
            mid = mid[len(prefix):]
    mid = mid.replace("[1m]", "")
    if mid.endswith("-v1:0"):
        mid = mid[:-len("-v1:0")]
    return mid.strip()


def parse_usage(jsonl_text, recent=8):
    """Roll up LOOP_USAGE.jsonl: by_model totals, total cost, recent ticks.

    Tolerant of malformed lines (matches the harness's jq 2>/dev/null parsing).
    """
    by_model = {}
    total_cost = 0.0
    active_s = 0.0
    ticks = []
    for line in jsonl_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue
        total_cost += float(rec.get("cost_usd", 0) or 0)
        active_s += float(rec.get("duration_s", 0) or 0)
        ticks.append({
            "tick": rec.get("tick"),
            "mode": rec.get("mode"),
            "cost_usd": float(rec.get("cost_usd", 0) or 0),
            "duration_s": rec.get("duration_s", 0),
            "by_model": rec.get("by_model") or {},
        })
        for model, m in (rec.get("by_model") or {}).items():
            key = _canon_model(model)
            agg = by_model.setdefault(key, {f: 0 for f in _MODEL_FIELDS})
            for f in _MODEL_FIELDS:
                agg[f] += (m.get(f, 0) or 0)
    return {
        "by_model": by_model,
        "total_cost_usd": total_cost,
        "active_s": active_s,
        "ticks": ticks[-recent:],
    }


def usage_effort(jsonl_text, tasks_done, recent=8):
    """Effort-first usage: tokens by model, per-task averages, live burn rate.

    Burn is measured over *active compute time* (the sum of tick durations),
    not wall-clock elapsed time. This keeps the rate honest while the loop is
    paused or idle — no tick runs, so neither the numerator (cost/tokens) nor
    the denominator (active seconds) advances, and the displayed burn holds
    steady instead of decaying toward zero.
    """
    base = parse_usage(jsonl_text, recent=recent)
    by_model = {}
    for m, v in base["by_model"].items():
        # billed surface = every token we are charged for, cache included.
        # input+output alone hid 93% of a real run (the cache_read tail).
        tokens = (v.get("input_tokens", 0) + v.get("output_tokens", 0)
                  + v.get("cache_read_tokens", 0) + v.get("cache_creation_tokens", 0))
        by_model[m] = dict(v, tokens=tokens)
    total_tokens = sum(m["tokens"] for m in by_model.values())
    cache_read = sum(v.get("cache_read_tokens", 0) for v in base["by_model"].values())
    cache_pct = (cache_read * 100 // total_tokens) if total_tokens else 0
    cost = base["total_cost_usd"]
    per_task = {
        "cost_usd": (cost / tasks_done) if tasks_done else 0,
        "tokens": (total_tokens // tasks_done) if tasks_done else 0,
    }
    active_s = base["active_s"]
    burn = {
        "usd_per_hr": (cost / (active_s / 3600)) if active_s else 0,
        "tok_per_min": (total_tokens / (active_s / 60)) if active_s else 0,
    }
    return {
        "by_model": by_model,
        "total_cost_usd": cost,
        "total_tokens": total_tokens,
        "cache_read_tokens": cache_read,
        "cache_read_pct": cache_pct,
        "per_task": per_task,
        "burn": burn,
        "ticks": base["ticks"],
    }


def roadmap(plan):
    """Segments with a coarse state for the roadmap track. A segment with task lines
    and any not-done task is 'current' if it holds the in-progress/next task; fully-done
    segments are 'done'; segments with zero task lines (unplanned future) are 'future'.
    """
    segs = plan["segments"]
    out = []
    current_marked = False
    for s in segs:
        total, done = s["total"], s["done"]
        planned = total > 0
        if not planned:
            state = "future"
        elif done >= total:
            state = "done"
        elif not current_marked:
            state, current_marked = "current", True
        else:
            state = "future"
        out.append({**s, "state": state, "planned": planned})
    return out


_QUOTA_LABELS = {"five_hour": "5h", "weekly": "wk"}


def parse_quota(rl_info, now):
    """Rate-limit summary from runtime/ratelimit.json (written from rate_limit_event).

    Fields: `rateLimitType` (e.g. "five_hour"/"weekly") and `resetsAt` (epoch s).
    Shows the limit type + a live countdown to `resetsAt` when one is pending;
    otherwise a neutral "within limits". Returns None only when no file exists.
    """
    if not isinstance(rl_info, dict):
        return None
    typ = rl_info.get("rateLimitType", "") or ""
    resets = rl_info.get("resetsAt")
    resets_at = resets if isinstance(resets, int) and resets > now else None
    return {
        "label": _QUOTA_LABELS.get(typ, typ or "quota"),
        "type": typ,
        "resets_at": resets_at,
    }


def parse_config(config_text):
    """Worktree, orchestrator model, per-role tiers, limits, sidecar/medic modes.

    `Dashboard:` and `Medic:` default to "auto" when absent — the same default
    the harness applies, so an older LOOP_CONFIG.md reads the way it behaves.
    """
    def field(name):
        m = re.search(r"^%s:\s*(.+)$" % re.escape(name), config_text, re.MULTILINE)
        return m.group(1).strip() if m else None

    tiers = {}
    for role in ROLES:
        v = field("%s tier" % role)
        if v:
            tiers[role] = v
    limits = {}
    lim = field("Limits") or ""
    for tok in lim.split():
        if "=" in tok:
            k, val = tok.split("=", 1)
            limits[k] = val
    return {
        "worktree": field("Worktree"),
        "orchestrator_model": field("Orchestrator model"),
        "tiers": tiers,
        "limits": limits,
        "dashboard": _first_word(field("Dashboard"), "auto"),
        "medic": _first_word(field("Medic"), "auto"),
    }


def _first_word(value, default):
    """First whitespace-delimited token of a config value, else `default`.

    Tolerates a trailing inline comment (`auto   # the sidecar`) and a key
    written with an empty value.
    """
    parts = (value or "").split()
    return parts[0] if parts else default


def tail_events(fp, offset):
    """Read JSONL events from a file object starting at byte/char `offset`.

    Returns (events, new_offset). Only consumes up to the last newline: a trailing
    partial line (an event still being written) is left unconsumed so the next read
    re-reads it once complete — no event is lost. Complete-but-unparseable lines are
    skipped (matches the harness's jq 2>/dev/null tolerance). `fp` is any object with
    seek()/read(); the server passes a real file, tests pass StringIO.
    """
    fp.seek(offset)
    chunk = fp.read()
    nl = chunk.rfind("\n")
    if nl == -1:
        return [], offset            # nothing complete yet; don't advance
    consumed = chunk[:nl + 1]
    new_offset = offset + len(consumed)
    events = []
    for line in consumed.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except (ValueError, TypeError):
            continue
    return events, new_offset


def tail_events_from_text(text):
    """Convenience for tests / one-shot reads: parse an events string from offset 0."""
    import io
    return tail_events(io.StringIO(text), 0)


def _events_this_tick(events):
    """Slice the event list to those at/after the last tick_start."""
    start = 0
    tick = None
    for i, e in enumerate(events):
        if e.get("type") == "tick_start":
            start, tick = i, e.get("tick")
    return tick, events[start:]


def derive_current(events):
    """Fold the current tick's events into {tick, role, model, tools}.

    `role` follows whichever actor most recently emitted activity — the
    orchestrator spine during its own prep, then each dispatched subagent. Role
    and model strings are treated as opaque: they are whatever the underlying
    provider's stream carries (model tier, agent type, ...), never matched
    against a hard-coded Claude-specific taxonomy.
    """
    tick, evs = _events_this_tick(events)
    role = model = None
    tools = 0
    models = {}
    for e in evs:
        typ, r = e.get("type"), e.get("role")
        if typ == "role_start" and r:
            if e.get("model"):
                models[r] = e.get("model")
            role, model = r, models.get(r)
        elif typ == "tool":
            tools = e.get("count", tools)
            if r:
                role, model = r, models.get(r)
    return {"tick": tick, "role": role, "model": model, "tools": tools}


def derive_pipeline(events):
    """Role timeline for the current tick, shaped for a now-playing view.

    The orchestrator spine is the implicit always-running parent; dispatched
    roles (Planner/Scout/Worker/Evaluator, or whatever opaque `role` the stream
    carries — a model tier, an agent type, the generic "claude" fallback) appear
    as `children` in first-seen (handoff) order. The actor that most recently
    emitted activity is `active`; actors that have handed control back are `done`.

    Returns::

        {"role": "orchestrator", "state": idle|active|done,
         "active": {"role", "model", "tools", "desc", "since"} | None,
         "children": [{"role", "model", "state", "tools", "desc"}, ...]}

    `active` is the single currently-working actor — the frontend's now-playing
    card renders it (its `since` epoch drives the live elapsed timer, `tools` its
    per-turn tool count, `desc` the dispatch description). `children` feeds the
    compact per-tick stepper. The frontend treats `active` as the parked node when
    the loop isn't actually running (paused/stopped/done).

    Backwards-compatible: an old event with no `role`, or a `role` of an
    unexpected value, is rendered verbatim as a child node. The orchestrator's
    own tools (`role == "orchestrator"`) keep the parent active rather than
    spawning a self-child.
    """
    _, evs = _events_this_tick(events)
    order = []                  # child roles, first-seen order
    child_state = {}            # role -> idle|active|done
    child_model = {}            # role -> model (last seen on role_start)
    child_desc = {}             # role -> dispatch description (from role_start)
    child_tools = {}            # role -> tool-call count this tick
    orch_state = "idle"
    orch_tools = 0
    active = None               # None | "orchestrator" | <child role>
    since = None                # epoch when `active` last took over (now-playing timer)

    def ensure(r):
        if r not in child_state:
            child_state[r] = "idle"
            child_tools[r] = 0
            order.append(r)

    if evs:
        orch_state = "active"
        active = "orchestrator"
        since = evs[0].get("t")
    for e in evs:
        typ, role, t = e.get("type"), e.get("role"), e.get("t")
        if not role:
            continue
        if role == "orchestrator":
            # spine activity: keep the parent active; demote any active child
            if typ in ("role_start", "tool"):
                if active and active != "orchestrator" and child_state.get(active) == "active":
                    child_state[active] = "done"
                if active != "orchestrator":
                    since = t
                orch_state = "active"
                active = "orchestrator"
            if typ == "tool":
                orch_tools += 1
            continue
        ensure(role)
        if e.get("model"):
            child_model[role] = e.get("model")
        # Only the dispatch (role_start) description names the agent's *task*; tool
        # events also carry a desc (the file/action) but that feeds the activity feed,
        # not the roster's task line — don't let it overwrite the dispatch intent.
        if typ == "role_start" and e.get("desc"):
            child_desc[role] = e.get("desc")
        if typ in ("role_start", "tool"):
            if active and active != role:
                if active == "orchestrator":
                    orch_state = "done"
                elif child_state.get(active) == "active":
                    child_state[active] = "done"
                since = t
            child_state[role] = "active"
            active = role
            if typ == "tool":
                child_tools[role] += 1
        elif typ == "role_end":
            if child_state.get(role) == "active":
                child_state[role] = "done"
            orch_state = "active"          # control returns to the spine
            active = "orchestrator"
            since = t
    children = [{"role": r, "model": child_model.get(r), "state": child_state[r],
                 "tools": child_tools[r], "desc": child_desc.get(r)}
                for r in order]
    if active == "orchestrator":
        act = {"role": "orchestrator", "model": None, "tools": orch_tools,
               "desc": None, "since": since}
    elif active:
        act = {"role": active, "model": child_model.get(active),
               "tools": child_tools.get(active, 0), "desc": child_desc.get(active),
               "since": since}
    else:
        act = None
    return {"role": "orchestrator", "state": orch_state, "active": act,
            "children": children}


def derive_activity(events, limit=60):
    """Recent tool actions for the current tick, oldest->newest, tagged with role.

    Each item is {role, name, desc, t}: the role is the now-correct actor
    (orchestrator spine, or a dispatched Scout/Worker/Evaluator), `name` the tool,
    `desc` the action label (a description or the file touched). Feeds the dashboard's
    activity feed — a live tail, capped to the most recent `limit` actions so it stays
    bounded; the frontend groups consecutive same-role rows under a handoff divider.
    """
    _, evs = _events_this_tick(events)
    acts = [{"role": e.get("role") or "orchestrator", "name": e.get("name"),
             "desc": e.get("desc"), "t": e.get("t")}
            for e in evs if e.get("type") == "tool"]
    return acts[-limit:]


def _read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _tail_lines(path, n=12, window=65536):
    """Last `n` lines of a (possibly huge) file without reading it whole.

    Seeks to the end and reads only a bounded window so the dashboard hot path
    never loads the full multi-MB run.log. If the window starts mid-file the
    first (possibly partial) line is dropped.
    """
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            start = max(0, size - window)
            f.seek(start)
            data = f.read()
    except OSError:
        return []
    lines = data.decode("utf-8", "replace").splitlines()
    if start > 0 and lines:
        lines = lines[1:]  # drop the partial leading line
    return lines[-n:]


def _log_tail(loop_dir, n=12):
    """Last `n` lines of the harness's own log, and the basename they came from.

    v3 does not tee a run.log — one v2 run left a 105 MB file nobody read. The
    harness writes its own lines to harness.log (rotated 10 MB x 3) and each
    phase's full transcript stays in the session JSONL its phase_end event
    names. A v2 loop dir has only run.log, so the fallback keeps a dir that has
    not been migrated yet readable from a v3 dashboard.
    """
    for name in ("harness.log", "run.log"):
        path = os.path.join(loop_dir, name)
        if os.path.exists(path):
            return _tail_lines(path, n), name
    return [], "harness.log"


# --------------------------------------------------------------- liveness
# Every liveness answer comes from runtime/ + the kernel. `pgrep` and
# `ps | grep run.sh` are banned here and in the skills: they match the
# observing agent's own tool calls and report a harness that does not exist.


def process_alive(pid, must_contain=None):
    """True when `pid` exists — and, when `must_contain` is given, when its
    command line contains that string.

    The command check defeats PID reuse: a recycled pid now belonging to some
    unrelated program must not read as a live harness.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass                      # alive, just owned by another user
    except OSError:
        return False
    if not must_contain:
        return True
    try:
        proc = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                              stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return must_contain in proc.stdout.decode("utf-8", "replace")


def read_harness(runtime_dir):
    """runtime/harness.json — {pid,start_epoch,host,loop_dir,plugin_version}."""
    return _read_json(os.path.join(runtime_dir, "harness.json"))


def read_schema(runtime_dir):
    """runtime/schema — the loop dir's layout version (int), or None when absent/garbage.
    The harness (lib/migrate.sh) is the only writer; a missing stamp on a dir that has
    ticked means schema 1, but that inference is the harness's to make, not ours."""
    try:
        with open(os.path.join(runtime_dir, "schema")) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _last_migration(store):
    """{from,to,actions,at} for the newest `migration` event, or None."""
    ev = store.last("migration")
    if not ev:
        return None
    return {"from": ev.get("from"), "to": ev.get("to"),
            "actions": ev.get("actions"), "at": _t(ev)}


def read_tick(runtime_dir):
    """runtime/tick.json — {tick,pid,started_at,timeout_s}; absent between ticks."""
    return _read_json(os.path.join(runtime_dir, "tick.json"))


def _epoch_age(path, now):
    """Seconds since the epoch stamp in a one-line file; None when unreadable."""
    txt = _read(path).strip()
    if not txt:
        return None
    try:
        stamp = int(float(txt.split()[0]))
    except (ValueError, IndexError):
        return None
    return max(0, now - stamp)


def heartbeat_age(runtime_dir, now):
    """Age of runtime/HEARTBEAT in seconds; None when the file is absent."""
    return _epoch_age(os.path.join(runtime_dir, "HEARTBEAT"), now)


def activity_age(runtime_dir, now):
    """Age of runtime/last-activity (written per stream line); None when absent."""
    return _epoch_age(os.path.join(runtime_dir, "last-activity"), now)


def liveness(loop_dir, now):
    """Everything derive_status needs about the *processes*, from runtime/.

    Pure I/O, no interpretation: the harness pid and whether it is a live
    `run.sh`, heartbeat age, the in-flight tick's pid/number/deadline, the
    last stream-activity age, and whether PAUSE was requested.
    """
    rt = os.path.join(loop_dir, "runtime")
    harness = read_harness(rt) or {}
    tick = read_tick(rt) or {}
    hpid = harness.get("pid")
    tpid = tick.get("pid")
    return {
        "pause": (os.path.exists(os.path.join(rt, "PAUSE"))
                  or os.path.exists(os.path.join(rt, "STOP"))),
        "stop": os.path.exists(os.path.join(rt, "STOP")),
        "harness_pid": hpid,
        "harness_pid_alive": process_alive(hpid, "run.sh") if hpid else False,
        "harness_started_at": harness.get("start_epoch"),
        "heartbeat_age_s": heartbeat_age(rt, now),
        "tick_pid": tpid,
        "tick_pid_alive": process_alive(tpid) if tpid else False,
        "tick_no": tick.get("tick"),
        "tick_started_at": tick.get("started_at"),
        "tick_timeout_s": tick.get("timeout_s"),
        "activity_age_s": activity_age(rt, now),
    }


# ------------------------------------------------------------- event store

# Small, unbounded-in-count-but-tiny-in-size events kept forever (one per tick
# or per exceptional moment). Everything else (tool/role_*/handoff) is kept
# only for the tick in flight and folded to an aggregate when the tick ends.
LIFECYCLE_TYPES = frozenset((
    "loop_start", "loop_end", "tick_start", "tick_end", "sleep",
    "memory_pressure", "incident", "medic_start", "medic_end", "paused",
    "task_status", "plan_oversize", "migration",
))

_CHUNK = 1 << 20            # read the backlog a megabyte at a time
_MAX_PARTIAL = 1 << 20      # a single line longer than this is corrupt: drop it
_MAX_LIFECYCLE = 4000
_MAX_CURRENT = 5000
_MAX_TICKS = 400
_MAX_INCIDENTS = 200
_MAX_ARTIFACTS = 400
_MAX_DECISIONS = 200


def _cap(lst, limit):
    """Trim a list in place to its last `limit` entries."""
    if len(lst) > limit:
        del lst[:len(lst) - limit]
    return lst


class EventStore:
    """Incremental, bounded reader for events.jsonl.

    Memory is O(current tick + number of ticks), not O(run length): each
    refresh consumes only the bytes appended since the last one, the current
    tick's tool/role events are dropped when the next tick starts, and
    finished ticks survive as one small aggregate dict each. A file shorter
    than the last offset means truncation or a fresh run — reset and re-read.
    """

    def __init__(self, path):
        self.path = path
        self._reset()

    def _reset(self):
        self._offset = 0
        self._buf = b""
        self.lifecycle = []
        self.current = []
        self.ticks = []
        self.incidents = []
        self.count = 0
        self.cur_tick = None
        self.cur_task = None
        self.cur_sha = None
        self._last = {}
        self._tick_tools = 0
        self._tick_roles = []
        self._incident_ix = {}
        self.artifacts = []          # the tick in flight only
        self.artifact_ix = {}        # (tick, name) -> path, across ticks
        self.decisions = []          # run-level audit trail (LOOP_DECISIONS.md)

    def refresh(self):
        """Consume everything appended since the last call."""
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return
        if size < self._offset:
            self._reset()
        if size == self._offset:
            return
        try:
            fp = open(self.path, "rb")
        except OSError:
            return
        with fp:
            fp.seek(self._offset)
            while True:
                chunk = fp.read(_CHUNK)
                if not chunk:
                    break
                self._offset += len(chunk)
                self._buf += chunk
                nl = self._buf.rfind(b"\n")
                if nl == -1:
                    if len(self._buf) > _MAX_PARTIAL:
                        self._buf = b""
                    continue
                block, self._buf = self._buf[:nl], self._buf[nl + 1:]
                for line in block.split(b"\n"):
                    if not line.strip():
                        continue
                    try:
                        ev = json.loads(line.decode("utf-8", "replace"))
                    except (ValueError, TypeError):
                        continue        # matches the harness's jq tolerance
                    if isinstance(ev, dict):
                        self._ingest(ev)

    def _ingest(self, ev):
        typ = ev.get("type")
        self.count += 1
        if typ == "tick_start":
            self.current = []
            self._tick_tools = 0
            self._tick_roles = []
            self.cur_tick = ev.get("tick")
            self.cur_task = None
            self.cur_sha = None
            self.artifacts = []
        else:
            self.current.append(ev)
            _cap(self.current, _MAX_CURRENT)
        if typ == "tool":
            self._tick_tools += 1
        elif typ == "role_start":
            role = ev.get("role")
            if role and role not in self._tick_roles:
                self._tick_roles.append(role)
        elif typ == "task_status":
            self.cur_task = ev.get("id") or self.cur_task
            self.cur_sha = ev.get("sha") or self.cur_sha
        elif typ == "artifact":
            name, path = ev.get("name"), ev.get("path")
            if name and path:
                self.artifacts.append({"name": name, "path": path})
                _cap(self.artifacts, _MAX_ARTIFACTS)
                # Addressable after the tick rolls over: a browser tab still
                # showing tick 4 must keep loading tick 4's screenshots.
                self.artifact_ix[(ev.get("tick"), name)] = path
                if len(self.artifact_ix) > _MAX_ARTIFACTS:
                    self.artifact_ix.pop(next(iter(self.artifact_ix)))
        elif typ in ("decision", "resume", "split"):
            self.decisions.append({
                "t": ev.get("t"), "type": typ, "task": ev.get("task"),
                "decision": ev.get("decision") or typ,
                "classification": ev.get("classification"),
                "attempt": ev.get("attempt"), "into": ev.get("into"),
            })
            _cap(self.decisions, _MAX_DECISIONS)
        elif typ == "tick_end":
            self._fold_tick(ev)
        elif typ == "incident":
            self._add_incident(ev)
        elif typ == "medic_end":
            self._fold_medic(ev)
        if typ in LIFECYCLE_TYPES:
            self._last[typ] = ev
            self.lifecycle.append(ev)
            _cap(self.lifecycle, _MAX_LIFECYCLE)

    def _fold_tick(self, ev):
        self.ticks.append({
            "tick": ev.get("tick", self.cur_tick),
            "verdict": ev.get("verdict"),
            "cause": ev.get("cause"),
            "dur": ev.get("dur"),
            "tools": self._tick_tools,
            "roles": list(self._tick_roles),
            "task": self.cur_task,
            "sha": self.cur_sha,
            "by_model": ev.get("by_model") or {},
        })
        _cap(self.ticks, _MAX_TICKS)

    def _add_incident(self, ev):
        inc = {"id": ev.get("id"), "t": ev.get("t"), "kind": ev.get("kind"),
               "severity": ev.get("severity"), "detail": ev.get("detail"),
               "tick": ev.get("tick"), "medic": None}
        self.incidents.append(inc)
        if inc["id"]:
            self._incident_ix[inc["id"]] = inc
        _cap(self.incidents, _MAX_INCIDENTS)

    def _fold_medic(self, ev):
        inc = self._incident_ix.get(ev.get("id"))
        if inc is not None:
            inc["medic"] = {"outcome": ev.get("outcome"),
                            "summary": ev.get("summary")}

    def last(self, type_):
        """The most recent lifecycle event of `type_`, or None."""
        return self._last.get(type_)


# ------------------------------------------------------------ status model

TERMINAL_STATES = {
    "done": ("done", None),
    "halt": ("halted", None),
    "error": ("halted", None),
    "needs-human": ("needs-human", None),
    "paused": ("paused", None),
    "signal": ("stopped", "harness received a signal"),
    "lock-conflict": ("stopped", "another harness owns this loop dir"),
    "rate-limit-exit": ("paused", "usage window — re-run to resume"),
}


def _dur(seconds):
    """Compact duration: 9s, 4m, 4m10s, 2h05m. None -> '?'."""
    if seconds is None:
        return "?"
    s = int(max(0, seconds))
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm%02ds" % (s // 60, s % 60) if s % 60 else "%dm" % (s // 60)
    return "%dh%02dm" % (s // 3600, (s % 3600) // 60)


def _t(ev):
    return (ev or {}).get("t") or 0


def derive_status(store, live, now, stall_s=300, hb_interval=10):
    """{state, since, why, phase} from events + process liveness. Pure.

    It never sees run.log. A terminal state requires a `loop_end` event the
    harness actually emitted, so no amount of "HALT:" or "LOOP_DONE" text in
    an LLM stream can stop the dashboard mid-run. First match wins; the order
    is the contract, and every branch sets a `why` the operator can act on.
    """
    ls, le = store.last("loop_start"), store.last("loop_end")
    ts, te = store.last("tick_start"), store.last("tick_end")
    sl = store.last("sleep")
    li = store.incidents[-1] if store.incidents else None
    hb = live.get("heartbeat_age_s")

    def out(state, since=None, why="", phase=None):
        return {"state": state, "since": since, "why": why, "phase": phase}

    # 1. nothing has ever happened here
    if not store.count:
        return out("idle", None, "no events yet")

    # 2. the harness said it was finished
    if le is not None and (ls is None or _t(le) >= _t(ls)):
        reason = le.get("reason") or "error"
        state, canned = TERMINAL_STATES.get(reason, ("halted", None))
        why = le.get("detail") or canned or reason
        if canned and reason == "rate-limit-exit":
            why = canned
        return out(state, _t(le) or None, why)

    # 3. an incident nobody can fix without a human, with no restart since
    if li is not None and li.get("severity") == "needs-human" and \
            (ls is None or _t(ls) <= (li.get("t") or 0)):
        medic = li.get("medic") or {}
        why = medic.get("summary") or li.get("detail") or li.get("kind") or ""
        return out("needs-human", li.get("t"), why)

    # 4. the harness is gone (dead pid, or a heartbeat older than 3 intervals)
    if not live.get("harness_pid_alive") or hb is None or hb > 3 * hb_interval:
        if live.get("pause"):
            what = "STOP" if live.get("stop") else "PAUSE"
            return out("paused", None, "%s present, harness not running" % what)
        pid = live.get("harness_pid")
        why = "harness pid %s not running" % (pid if pid else "?")
        if hb is not None:
            why += " · last heartbeat %s ago" % _dur(hb)
        else:
            why += " · no heartbeat file"
        return out("crashed", (now - hb) if hb is not None else None, why)

    # 5. PAUSE or STOP requested, harness still finishing the phase
    if live.get("pause"):
        if live.get("stop"):
            return out("pausing", live.get("tick_started_at"),
                       "stop requested — ends at the next phase boundary")
        tick = live.get("tick_no") or store.cur_tick
        return out("pausing", live.get("tick_started_at"),
                   "stops after tick %s" % (tick if tick is not None else "?"))

    # 6. a sleep the harness has not woken from yet
    if sl is not None and _t(sl) >= _t(te) and _t(sl) >= _t(ts) \
            and (sl.get("until") or 0) > now:
        left = _dur((sl.get("until") or now) - now)
        reason = sl.get("reason")
        if reason == "rate-limit":
            return out("rate-limited", _t(sl), "usage window · resumes in %s" % left)
        if reason == "memory":
            return out("memory-wait", _t(sl), "waiting for memory headroom · retry in %s" % left)
        if reason == "backoff":
            return out("between-ticks", _t(sl), "backoff · next tick in %s" % left)
        return out("between-ticks", _t(sl), "next tick in %s" % left)

    # 7. a tick is in flight
    if ts is not None and (te is None or _t(ts) > _t(te)):
        age = live.get("activity_age_s")
        started = live.get("tick_started_at") or _t(ts)
        elapsed = max(0, now - started) if started else None
        if age is not None and age >= stall_s:
            timeout = live.get("tick_timeout_s")
            why = "no activity %s" % _dur(age)
            if timeout and elapsed is not None:
                why += " · tick killed in %s" % _dur(int(timeout) - elapsed)
            return out("stalled", _t(ts), why, _phase(store))
        cur = derive_current(store.current)
        phase = cur.get("role") or "orchestrator"
        bits = phase
        if cur.get("model"):
            bits += " (%s)" % cur["model"]
        if store.cur_task:
            bits += " on %s" % store.cur_task
        if elapsed is not None:
            bits += " · %s" % _dur(elapsed)
        if age is not None:
            bits += " · last activity %s ago" % _dur(age)
        return out("running", _t(ts), bits, phase)

    # 8. harness alive, no tick, no sleep we can name
    return out("between-ticks", _t(te) or None, "waiting for next tick")


def _phase(store):
    return derive_current(store.current).get("role") or "orchestrator"


def segment_stats(plan):
    """(total, done, unplanned) segments.

    A segment is *done* when it has at least one task line and every one of
    them is closed (`[x]` or `[-]`); *unplanned* when it has no task lines yet.
    Same definition as lib/loop.sh's segments_done/segments_unplanned, so the
    terminal header and the dashboard cannot disagree.
    """
    per = {}
    for t in plan["tasks"]:
        agg = per.setdefault(t["segment"], {"total": 0, "closed": 0})
        agg["total"] += 1
        if t["status"] in ("done", "skipped"):
            agg["closed"] += 1
    total = len(plan["segments"])
    done = 0
    for seg in plan["segments"]:
        agg = per.get(seg["name"])
        if agg and agg["total"] > 0 and agg["closed"] == agg["total"]:
            done += 1
    unplanned = sum(1 for s in plan["segments"] if s["total"] == 0)
    return total, done, unplanned


def _env_int(name, default):
    """An integer env knob, falling back to `default` when unset or garbage."""
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def resume_command(loop_dir, worktree):
    """The exact command that restarts this loop, for the UI to show verbatim.

    Harmless while the loop runs — it is the same line NEEDS_HUMAN.md and the
    medic print, so an operator never has to reconstruct it from memory.
    """
    if not worktree:
        # Standard layout: <worktree>/.claude/loop/<name>.
        worktree = os.path.abspath(os.path.join(loop_dir, "..", "..", ".."))
    return 'cd %s && LOOP_DIR=.claude/loop/%s bash "%s/run.sh"' % (
        worktree, _loop_name(loop_dir), os.path.dirname(HERE))


def build_snapshot(loop_dir, store, live, status, now, stall_s=300):
    """Compose the dashboard snapshot from the store, liveness, and the files.

    run.log is touched exactly once, for the 12-line display tail. Everything
    the UI colours or decides on comes from `store` (events.jsonl) and `live`
    (pids + heartbeat). `stall_s` is echoed into health so the page colours its
    activity pill on the same threshold derive_status judged it by.
    """
    plan_text = _read(os.path.join(loop_dir, "LOOP_PLAN.md"))
    usage_text = _read(os.path.join(loop_dir, "LOOP_USAGE.jsonl"))
    config_text = _read(os.path.join(loop_dir, "LOOP_CONFIG.md"))
    quota_obj = _read_json(os.path.join(loop_dir, "runtime", "ratelimit.json"))

    plan = parse_plan(plan_text)
    config = parse_config(config_text)
    current = dict(derive_current(store.current), tick=store.cur_tick,
                   task=store.cur_task, artifacts=list(store.artifacts))
    pipeline = derive_pipeline(store.current)
    activity = derive_activity(store.current)
    usage = usage_effort(usage_text, tasks_done=plan["progress"]["done"],
                         recent=_MAX_TICKS)

    seg_total, seg_done, seg_unplanned = segment_stats(plan)
    basis = "segments" if seg_unplanned > 0 else "tasks"
    progress_pct = (pct(seg_done, seg_total) if basis == "segments"
                    else plan["progress"]["pct"])
    ls = store.last("loop_start")
    started = live.get("harness_started_at") or _t(ls) or None
    dash = _read_json(os.path.join(loop_dir, "runtime", "dashboard.json")) or {}
    log_lines, log_file = _log_tail(loop_dir, 12)
    dash_pid = dash.get("pid")

    return {
        "now": now,
        "loop": {"status": status["state"], "worktree": config.get("worktree"),
                 "name": _loop_name(loop_dir), "tick": store.cur_tick,
                 "plugin_version": (read_harness(os.path.join(loop_dir, "runtime"))
                                    or {}).get("plugin_version"),
                 "schema": read_schema(os.path.join(loop_dir, "runtime")),
                 "migration": _last_migration(store)},
        "status": dict(status, resume_cmd=resume_command(loop_dir,
                                                         config.get("worktree"))),
        "health": {
            "harness": {"alive": bool(live.get("harness_pid_alive")),
                        "pid": live.get("harness_pid"),
                        "heartbeat_age_s": live.get("heartbeat_age_s"),
                        "started_at": live.get("harness_started_at")},
            "tick": {"alive": bool(live.get("tick_pid_alive")),
                     "pid": live.get("tick_pid"),
                     "tick": live.get("tick_no"),
                     "elapsed_s": (max(0, now - live["tick_started_at"])
                                   if live.get("tick_started_at") else None),
                     "timeout_s": live.get("tick_timeout_s"),
                     "activity_age_s": live.get("activity_age_s"),
                     "stall_s": stall_s},
            "dashboard": {"pid": dash_pid or DASHBOARD["pid"],
                          "alive": process_alive(dash_pid) if dash_pid else False,
                          "uptime_s": max(0, now - DASHBOARD["started_at"]),
                          "sidecar": DASHBOARD["sidecar"]},
        },
        "current": current,
        "pipeline": pipeline,
        "activity": activity,
        "progress": {**plan["progress"], "pct": progress_pct,
                     "pct_basis": basis, "segments_total": seg_total,
                     "segments_done": seg_done,
                     "segments_unplanned": seg_unplanned},
        "roadmap": roadmap(plan),
        "plan": plan["tasks"],
        "ticks": _merge_tick_costs(store.ticks[-30:], usage.get("ticks") or []),
        "incidents": list(store.incidents),
        "decisions": list(store.decisions[-20:]),
        "usage": usage,
        "quota": parse_quota(quota_obj, now),
        "config": config,
        "elapsed_s": max(0, now - started) if started else 0,
        "log": log_lines,
        "log_file": log_file,
    }


def _merge_tick_costs(ticks, usage_ticks):
    """Decorate the event-store tick aggregates with mode + cost from the ledger.

    `mode` and `cost_usd` live in LOOP_USAGE.jsonl (written per tick by the
    harness), the rest in events.jsonl; the tick number joins them.
    """
    ledger = {u.get("tick"): u for u in usage_ticks if u.get("tick") is not None}
    out = []
    for t in ticks:
        u = ledger.get(t.get("tick")) or {}
        out.append(dict(t, mode=u.get("mode"), cost_usd=u.get("cost_usd", 0)))
    return out


def _loop_name(loop_dir):
    return os.path.basename(os.path.normpath(loop_dir))


# What the dashboard is allowed to render from $LOOP_DIR/artifacts/.
_ARTIFACT_TYPES = {".png": "image/png", ".jpg": "image/jpeg",
                   ".jpeg": "image/jpeg", ".webp": "image/webp"}
_MAX_ARTIFACT_BYTES = 8 << 20


def artifact_path(loop_dir, store, tick, name):
    """Absolute path of a recorded screenshot, or None. Pure except for stat().

    The query string never becomes a path: (tick, name) is a key into the index
    the `artifact` events built. The path that comes back is still untrusted --
    the Scout writes screenshot paths into the contract (spec 5.3) -- so it is
    realpath-resolved (collapsing `..` and following symlinks) and must land
    inside $LOOP_DIR/artifacts/, with an extension the dashboard can render.
    """
    rel = store.artifact_ix.get((tick, name))
    if not rel:
        return None
    root = os.path.realpath(os.path.join(loop_dir, "artifacts"))
    full = os.path.realpath(rel if os.path.isabs(rel)
                            else os.path.join(loop_dir, rel))
    if full != root and not full.startswith(root + os.sep):
        return None
    if os.path.splitext(full)[1].lower() not in _ARTIFACT_TYPES:
        return None
    return full if os.path.isfile(full) else None


class Supervisor:
    """Owns the run.sh lifecycle and the PAUSE-file control surface.

    Control works whether or not this process spawned run.sh: pause/stop
    write the PAUSE file (the harness exits cleanly at the next tick boundary),
    and resume removes it and (re)spawns run.sh, which resumes from disk state.
    """

    def __init__(self, loop_dir, plugin_root, worktree, no_spawn=False):
        self.loop_dir = loop_dir
        self.runtime_dir = os.path.join(loop_dir, "runtime")
        self.plugin_root = plugin_root
        self.worktree = worktree
        self.no_spawn = no_spawn
        self.proc = None
        self._lock = threading.Lock()

    @property
    def pause_path(self):
        return os.path.join(self.runtime_dir, "PAUSE")

    @property
    def stop_path(self):
        return os.path.join(self.runtime_dir, "STOP")

    def pause_exists(self):
        return os.path.exists(self.pause_path)

    def child_alive(self):
        return self.proc is not None and self.proc.poll() is None

    def harness_pid(self):
        """The pid in runtime/harness.json when it is a live `run.sh`, else None.

        This is the exclusivity check: a dashboard must never spawn a second
        harness over a live one, whoever started it.
        """
        pid = (read_harness(self.runtime_dir) or {}).get("pid")
        if pid and process_alive(pid, "run.sh"):
            return int(pid)
        return None

    def _refuse_if_live(self):
        pid = self.harness_pid()
        if pid is not None:
            return {"error": "harness pid %d is alive" % pid}
        return None

    def _refuse_unless_live(self):
        if self.harness_pid() is None:
            return {"error": "no live harness to stop"}
        return None

    def _spawn(self):
        env = dict(os.environ)
        env["LOOP_DIR"] = self.loop_dir
        # This process IS the loop's dashboard: the harness must not open a sidecar.
        # (run.sh would adopt us anyway via runtime/dashboard.json; the env makes
        # the intent explicit and survives a dashboard.json race.)
        env["LOOP_DASHBOARD"] = "off"
        run_sh = os.path.join(self.plugin_root, "run.sh")
        # Own session: a dashboard crash or Ctrl-C must never take the loop down.
        # Stop still works — it writes runtime/STOP, which the harness reads
        # between phases whether or not it is our child.
        self.proc = subprocess.Popen(
            ["bash", run_sh], cwd=self.worktree, env=env, start_new_session=True)

    def _clear_sentinels(self):
        """Remove both stop sentinels before (re)starting a harness.

        A STOP left by the previous run would otherwise make the new harness
        exit at its first phase boundary, which reads as "the loop died again".
        """
        for path in (self.pause_path, self.stop_path):
            try:
                os.remove(path)
            except OSError:
                pass

    def start(self):
        with self._lock:
            refusal = self._refuse_if_live()
            if refusal:
                return refusal
            if self.child_alive():
                return None
            self._clear_sentinels()
            if not self.no_spawn:
                self._spawn()
            return None

    def pause(self):
        with self._lock:
            os.makedirs(self.runtime_dir, exist_ok=True)
            open(self.pause_path, "w").close()
            return None

    def resume(self):
        with self._lock:
            refusal = self._refuse_if_live()
            if refusal:
                return refusal
            self._clear_sentinels()
            if not self.no_spawn and not self.child_alive():
                self._spawn()
            return None

    def stop(self):
        """Request an immediate stop by writing runtime/STOP.

        The v3 runner checks STOP between phases: it SIGTERMs the phase in
        flight (a Worker gets its wrap-up continuation first), writes
        CHECKPOINT.json and exits 0 — so a stop lands within minutes instead
        of at the end of a 30-minute tick, and no signal is sent from here.
        Refused when no harness is alive: a sentinel nobody reads only arms a
        trap for the next launch, and the button is disabled in that state.
        """
        with self._lock:
            refusal = self._refuse_unless_live()
            if refusal:
                return refusal
            os.makedirs(self.runtime_dir, exist_ok=True)
            open(self.stop_path, "w").close()
            return None


HERE = os.path.dirname(os.path.abspath(__file__))

# This process, as the snapshot reports it. main() flips `sidecar` when the
# harness launched us with --sidecar.
DASHBOARD = {"pid": os.getpid(), "sidecar": False, "started_at": int(time.time())}


class _State:
    """One shared snapshot for every client.

    The store is refreshed and the snapshot rebuilt once per second by
    SnapshotThread; HTTP and SSE handlers only ever serve the cached bytes.
    Before this, every SSE client re-parsed the whole of events.jsonl twice a
    second on its own thread — the dashboard's memory grew with the run and
    competed with the tick for RAM.
    """

    def __init__(self, supervisor, stall_s=300):
        self.sup = supervisor
        self.stall_s = stall_s
        self.store = EventStore(os.path.join(supervisor.loop_dir, "events.jsonl"))
        self._lock = threading.Lock()
        self.latest = b"{}"
        self.digest = ""
        self.status = {"state": "idle", "since": None, "why": "", "phase": None}

    def rebuild(self):
        """Refresh the store and republish the snapshot. Never raises."""
        try:
            now = int(time.time())
            self.store.refresh()
            live = liveness(self.sup.loop_dir, now)
            status = derive_status(self.store, live, now, stall_s=self.stall_s)
            snap = build_snapshot(self.sup.loop_dir, self.store, live, status, now,
                                  stall_s=self.stall_s)
            body = json.dumps(snap).encode("utf-8")
        except Exception:                       # keep serving the last good one
            return
        with self._lock:
            self.latest = body
            self.digest = hashlib.sha1(body).hexdigest()
            self.status = status

    def read(self):
        with self._lock:
            return self.latest, self.digest

    def state_name(self):
        with self._lock:
            return self.status.get("state", "idle")


class SnapshotThread(threading.Thread):
    """Daemon that rebuilds the shared snapshot at 1 Hz."""

    def __init__(self, state, interval=1.0):
        super().__init__(daemon=True)
        self.state = state
        self.interval = interval

    def run(self):
        while True:
            self.state.rebuild()
            time.sleep(self.interval)


def _make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence default stderr noise
            pass

        def _send_json(self, obj, code=200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/" or self.path.startswith("/index"):
                body = _read(os.path.join(HERE, "dashboard.html")).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/state":
                body, _ = state.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                last = None
                try:
                    while True:
                        body, digest = state.read()
                        if digest != last:
                            self.wfile.write(b"data: " + body + b"\n\n")
                            self.wfile.flush()
                            last = digest
                        time.sleep(1)
                except (BrokenPipeError, ConnectionResetError):
                    return
            elif self.path.startswith("/api/artifact"):
                q = parse_qs(urlparse(self.path).query)
                try:
                    tick = int((q.get("tick") or [""])[0])
                except ValueError:
                    tick = None
                name = (q.get("name") or [""])[0]
                full = artifact_path(state.sup.loop_dir, state.store, tick, name)
                if full is None:
                    self._send_json({"error": "not found"}, 404)
                    return
                try:
                    size = os.path.getsize(full)
                    if size > _MAX_ARTIFACT_BYTES:
                        self._send_json({"error": "artifact too large"}, 413)
                        return
                    with open(full, "rb") as f:
                        blob = f.read()
                except OSError:
                    self._send_json({"error": "not found"}, 404)
                    return
                ctype = _ARTIFACT_TYPES[os.path.splitext(full)[1].lower()]
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(blob)))
                # A retried tick can rewrite the same (tick, name).
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(blob)
            else:
                self._send_json({"error": "not found"}, 404)

        def do_POST(self):
            actions = {"/api/start": state.sup.start, "/api/pause": state.sup.pause,
                       "/api/resume": state.sup.resume, "/api/stop": state.sup.stop}
            fn = actions.get(self.path)
            if fn is None:
                self._send_json({"error": "not found"}, 404)
                return
            result = fn()
            if result and result.get("error"):
                # 409: a live harness owns this loop dir. Never race it.
                self._send_json(result, 409)
                return
            state.rebuild()
            self._send_json({"status": state.state_name()})

    return Handler


class _QuietServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that does not dump a traceback when a client just
    drops the connection. Browsers routinely close the SSE (`/events`) stream
    and keep-alive sockets — that surfaces as ConnectionResetError/BrokenPipe
    mid-request, which the default handle_error prints as an alarming (but
    harmless) stack trace. Swallow those; let real errors through.
    """

    daemon_threads = True

    def handle_error(self, request, client_address):
        if issubclass(sys.exc_info()[0],
                       (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


def _bind_server(port, handler, server_cls=None):
    """Bind on 127.0.0.1:<port>, falling back to an ephemeral port when <port>
    is taken. run.sh asks for the port recorded in runtime/dashboard.json so an
    open tab's SSE reconnects after a respawn; if a previous (e.g. 1.x) dashboard
    still holds it, refusing to start would leave the loop with no observer at all
    and the supervisor crash-looping on the same collision. Ephemeral is the
    right degradation: the new URL is announced on stdout like any other."""
    cls = server_cls or _QuietServer
    try:
        return cls(("127.0.0.1", port), handler)
    except OSError:
        if not port:
            raise
        print(json.dumps({"type": "dashboard-port-busy", "requested": port,
                          "fallback": "ephemeral"}), file=sys.stderr, flush=True)
        return cls(("127.0.0.1", 0), handler)


def existing_dashboard(runtime_dir):
    """runtime/dashboard.json when it names a live serve.py, else None."""
    d = _read_json(os.path.join(runtime_dir, "dashboard.json")) or {}
    if d.get("pid") and process_alive(d.get("pid"), "serve.py"):
        return d
    return None


def _launch_detached(argv, out_path):
    """Re-exec this server in a NEW SESSION so it outlives whatever launched it
    (an interactive Claude session, a terminal). stdio goes to out_path."""
    out = open(out_path, "ab")
    proc = subprocess.Popen([sys.executable, os.path.abspath(__file__)] + list(argv),
                            stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                            cwd=os.getcwd(), start_new_session=True, close_fds=True)
    return proc.pid


def run_detached(loop_dir, runtime_dir, argv, launcher=None, timeout=10.0):
    """`--detach`: ensure exactly one dashboard for loop_dir and print its banner.

    A live dashboard is reused (nothing launched). Otherwise the server is
    launched detached and we wait until runtime/dashboard.json names the child.
    Prints one JSON line to stdout; returns the process exit code."""
    launcher = launcher or _launch_detached
    os.makedirs(runtime_dir, exist_ok=True)
    live = existing_dashboard(runtime_dir)
    if live:
        print(json.dumps({"type": "dashboard-started", "url": live.get("url"),
                          "loop_dir": loop_dir, "pid": live.get("pid"),
                          "reused": True}), flush=True)
        return 0
    child_argv = [a for a in argv if a != "--detach"]
    out_path = os.path.join(runtime_dir, "dashboard.out")
    pid = launcher(child_argv, out_path)
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = _read_json(os.path.join(runtime_dir, "dashboard.json")) or {}
        if d.get("pid") == pid and d.get("url"):
            print(json.dumps({"type": "dashboard-started", "url": d["url"],
                              "loop_dir": loop_dir, "pid": pid, "reused": False}),
                  flush=True)
            return 0
        time.sleep(0.1)
    tail = ""
    try:
        with open(out_path, "rb") as f:
            tail = f.read()[-800:].decode("utf-8", "replace")
    except OSError:
        pass
    print(json.dumps({"type": "dashboard-failed", "loop_dir": loop_dir, "pid": pid,
                      "detail": "no dashboard.json from the child within %.0fs" % timeout,
                      "log_tail": tail}), flush=True)
    return 1


def main():
    ap = argparse.ArgumentParser(description="agent-loop dashboard server")
    ap.add_argument("--loop-dir", default=os.environ.get("LOOP_DIR", ".claude/loop/run"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("LOOP_DASH_PORT", "0")))
    ap.add_argument("--no-spawn", action="store_true",
                    help="read-only observer: the Start/Resume buttons will not "
                         "spawn run.sh (launch never auto-starts regardless)")
    ap.add_argument("--sidecar", action="store_true",
                    help="launched and supervised by run.sh; recorded in "
                         "runtime/dashboard.json so an attaching session reuses "
                         "this server instead of starting another")
    ap.add_argument("--detach", action="store_true",
                    help="ensure one dashboard for this loop dir: reuse a live one, "
                         "else launch the server in its own session (survives the "
                         "launching shell/session), print the banner, and exit")
    args = ap.parse_args()
    DASHBOARD["sidecar"] = bool(args.sidecar)

    loop_dir = os.path.abspath(args.loop_dir)
    if args.detach:
        sys.exit(run_detached(loop_dir, os.path.join(loop_dir, "runtime"), sys.argv[1:]))
    config = parse_config(_read(os.path.join(loop_dir, "LOOP_CONFIG.md")))
    worktree = config.get("worktree") or os.getcwd()
    sup = Supervisor(loop_dir=loop_dir, plugin_root=os.path.dirname(HERE),
                     worktree=worktree, no_spawn=args.no_spawn)
    # One reading of STALL_S for the whole process: the same number decides
    # `stalled` here and colours the activity pill in the page.
    state = _State(sup, stall_s=_env_int("STALL_S", 300))

    httpd = _bind_server(args.port, _make_handler(state))
    port = httpd.server_address[1]
    url = "http://127.0.0.1:%d" % port

    os.makedirs(sup.runtime_dir, exist_ok=True)
    with open(os.path.join(sup.runtime_dir, "dashboard.json"), "w") as f:
        json.dump({"pid": os.getpid(), "port": port, "url": url,
                   "sidecar": bool(args.sidecar)}, f)

    # Build once synchronously so the first /api/state is never empty, then let
    # the 1 Hz thread own every rebuild from here on.
    state.rebuild()
    SnapshotThread(state).start()

    # Launching never auto-starts the loop: the dashboard opens at the loop's
    # current state (idle, paused, running, or done) so you can review history,
    # then drive lifecycle with the Start/Resume buttons. An already-live
    # headless loop is simply observed. `--no-spawn` further makes this a pure
    # read-only observer (the buttons won't spawn run.sh either).

    print(json.dumps({"type": "dashboard-started", "url": url, "loop_dir": loop_dir}),
          flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
