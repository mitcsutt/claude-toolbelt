#!/usr/bin/env python3
"""agent-loop live dashboard server (Python 3 stdlib only).

Reads the loop's existing artefacts under $LOOP_DIR and tails run.log for
in-flight activity, serves a single-page dashboard over HTTP + SSE, and
drives Start/Pause/Resume/Stop via the runtime/PAUSE file. Headless
`bash run.sh` is unaffected. The server creates no scratch dir; it reuses
$LOOP_DIR and writes only runtime/PAUSE + runtime/dashboard.json.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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


def usage_effort(jsonl_text, tasks_done):
    """Effort-first usage: tokens by model, per-task averages, live burn rate.

    Burn is measured over *active compute time* (the sum of tick durations),
    not wall-clock elapsed time. This keeps the rate honest while the loop is
    paused or idle — no tick runs, so neither the numerator (cost/tokens) nor
    the denominator (active seconds) advances, and the displayed burn holds
    steady instead of decaying toward zero.
    """
    base = parse_usage(jsonl_text)
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
    """Worktree, orchestrator model, per-role tiers, and limits from LOOP_CONFIG.md."""
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
    }


_TICK_START_RE = re.compile(r"\btick (\d+) starting\b")
_MODE_TOKENS = (("PLAN TICK", "plan"), ("REVIEW TICK", "review"),
                ("EXECUTE TICK", "execute"))


def slice_current_tick(runlog_text):
    """Return {'tick','lines'} for the most recent 'tick N starting' marker.

    run.log interleaves timestamped harness log lines with the raw stream-json.
    The current tick's stream is everything after the last start marker.
    """
    lines = runlog_text.splitlines()
    last_idx = None
    last_tick = None
    for i, line in enumerate(lines):
        m = _TICK_START_RE.search(line)
        if m:
            last_idx = i
            last_tick = int(m.group(1))
    if last_idx is None:
        return {"tick": None, "lines": []}
    return {"tick": last_tick, "lines": lines[last_idx + 1:]}


def parse_current_activity(lines):
    """In-flight view from a tick's stream lines: mode, subagent, model, tools."""
    mode = "execute"
    subagent = None
    model = None
    tools = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except (ValueError, TypeError):
            continue
        if ev.get("type") != "assistant":
            continue
        msg = ev.get("message") or {}
        if msg.get("model"):
            model = msg["model"]
        for block in (msg.get("content") or []):
            btype = block.get("type")
            if btype == "text":
                text = block.get("text", "") or ""
                for token, name in _MODE_TOKENS:
                    if token.lower() in text.lower():
                        mode = name
            elif btype == "tool_use":
                tools += 1
                inp = block.get("input") or {}
                if inp.get("subagent_type"):
                    subagent = inp["subagent_type"]
                    if inp.get("model"):
                        model = inp["model"]
    return {"mode": mode, "subagent": subagent, "model": model, "tools": tools}


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


def detect_loop_status(pause, lock, child_alive, last_log):
    """idle | running | paused | done | halted | stopped."""
    if "LOOP_DONE" in last_log:
        return "done"
    if "HALT:" in last_log:
        return "halted"
    if pause:
        return "paused"
    if child_alive or lock:
        return "running"
    if last_log.strip():
        return "stopped"
    return "idle"


def event_verdict(last_event_t, now, lock, pause, last_log, quota, stall_s=45,
                  pausing=False):
    """Operator-facing status, terminal states first.

    done/halted (terminal, from the log tail) > pausing (PAUSE requested but the
    loop is still finishing the current tick) > paused > rate-limited (quota
    resetting + the harness's sleep line) > stalled (lock held but no event in
    stall_s) > running (recent event or lock) > stopped/idle.
    """
    if "LOOP_DONE" in last_log:
        return "done"
    if "HALT:" in last_log:
        return "halted"
    if pause:
        return "pausing" if pausing else "paused"
    waiting = quota is not None and quota.get("resets_at")
    if waiting and "usage limit" in last_log:
        return "rate-limited"
    fresh = last_event_t is not None and (now - last_event_t) <= stall_s
    if lock and not fresh:
        return "stalled"
    if fresh or lock:
        return "running"
    if last_log.strip():
        return "stopped"
    return "idle"


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


def build_snapshot(loop_dir, now, status):
    """Compose dashboard state from plan/usage/config + the tail of events.jsonl.

    The 14 MB run.log is no longer parsed on the hot path; only a short tail is kept
    for the log panel.
    """
    plan_text = _read(os.path.join(loop_dir, "LOOP_PLAN.md"))
    usage_text = _read(os.path.join(loop_dir, "LOOP_USAGE.jsonl"))
    config_text = _read(os.path.join(loop_dir, "LOOP_CONFIG.md"))
    events_text = _read(os.path.join(loop_dir, "events.jsonl"))
    quota_obj = _read_json(os.path.join(loop_dir, "runtime", "ratelimit.json"))

    plan = parse_plan(plan_text)
    config = parse_config(config_text)
    events, _ = tail_events_from_text(events_text)
    current = derive_current(events)
    pipeline = derive_pipeline(events)
    activity = derive_activity(events)
    elapsed = _elapsed_s(events, now)
    usage = usage_effort(usage_text, tasks_done=plan["progress"]["done"])

    seg_total = len(plan["segments"])
    seg_unplanned = sum(1 for s in plan["segments"] if s["total"] == 0)
    log_tail = _tail_lines(os.path.join(loop_dir, "run.log"), 12)

    return {
        "loop": {"status": status, "worktree": config.get("worktree"),
                 "name": _loop_name(loop_dir), "tick": current["tick"]},
        "current": current,
        "pipeline": pipeline,
        "activity": activity,
        "progress": {**plan["progress"], "segments_total": seg_total,
                     "segments_unplanned": seg_unplanned},
        "roadmap": roadmap(plan),
        "plan": plan["tasks"],
        "usage": usage,
        "narrative": _narrative(plan, usage),
        "quota": parse_quota(quota_obj, now),
        "config": config,
        "elapsed_s": elapsed,
        "log": log_tail,
    }


def _elapsed_s(events, now):
    """Seconds since the first tick_start we can see (0 if none)."""
    for e in events:
        if e.get("type") == "tick_start":
            return max(0, now - e.get("t", now))
    return 0


def _loop_name(loop_dir):
    return os.path.basename(os.path.normpath(loop_dir))


def _model_tier(model):
    """Collapse a full model id (e.g. "claude-opus-4-8") to a short tier label
    ("opus"/"sonnet"/"haiku") for the narrative split; pass through anything else."""
    s = str(model or "")
    for tier in ("opus", "sonnet", "haiku"):
        if tier in s:
            return tier
    return s


def _narrative(plan, usage):
    """One terse line per recent completed tick, plus the per-model cost split.

    Each entry carries `by_model`: a list of {model, cost_usd} (short tier label,
    descending cost) plumbed from the tick's `by_model` so the row can expand to
    show e.g. "opus $1.40 · sonnet $0.30 · haiku $0.06".
    """
    out = []
    for t in usage.get("ticks", []):
        split = []
        for model, m in (t.get("by_model") or {}).items():
            split.append({"model": _model_tier(model),
                          "cost_usd": float((m or {}).get("cost_usd", 0) or 0)})
        split.sort(key=lambda x: x["cost_usd"], reverse=True)
        out.append({"tick": t.get("tick"), "mode": t.get("mode"),
                    "cost_usd": t.get("cost_usd", 0), "dur": t.get("duration_s", 0),
                    "by_model": split})
    return out[-8:]


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
    def lock_path(self):
        # The harness's single-tick mutex is $LOOP_DIR/runtime/LOCK (uppercase;
        # tick-prompt.md §2/§14). It only exists *during* a tick — the tick
        # deletes it on exit — so absence of LOCK does NOT mean the loop is dead
        # between ticks. See loop_seems_active() for the liveness signal.
        return os.path.join(self.runtime_dir, "LOCK")

    def pause_exists(self):
        return os.path.exists(self.pause_path)

    def lock_exists(self):
        return os.path.exists(self.lock_path)

    def child_alive(self):
        return self.proc is not None and self.proc.poll() is None

    def last_log(self):
        path = os.path.join(self.loop_dir, "run.log")
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                start = max(0, f.tell() - 4000)
                f.seek(start)
                return f.read().decode("utf-8", "replace")
        except OSError:
            return ""

    def loop_seems_active(self, within_s=30):
        """True if a loop appears live — ours OR an external headless one.

        LOCK is only held mid-tick, so between ticks we fall back to run.log
        recency: a loop actively ticking writes the streamed JSON to run.log
        continuously, and the between-tick gap is only a few seconds. A run.log
        whose tail already shows LOOP_DONE/HALT is terminal, not active. This is
        the guard that stops an attaching dashboard from spawning a second loop
        on a dir that already has one running.
        """
        if self.child_alive() or self.lock_exists():
            return True
        try:
            mtime = os.path.getmtime(os.path.join(self.loop_dir, "run.log"))
        except OSError:
            return False
        if time.time() - mtime > within_s:
            return False
        tail = self.last_log()
        return "LOOP_DONE" not in tail and "HALT:" not in tail

    def status(self):
        return detect_loop_status(self.pause_exists(), self.loop_seems_active(),
                                  self.child_alive(), self.last_log())

    def _spawn(self):  # pragma: no cover (exercised in web.contract.sh)
        env = dict(os.environ)
        env["LOOP_DIR"] = self.loop_dir
        run_sh = os.path.join(self.plugin_root, "run.sh")
        self.proc = subprocess.Popen(
            ["bash", run_sh], cwd=self.worktree, env=env)

    def start(self):
        with self._lock:
            if self.child_alive():
                return
            try:
                os.remove(self.pause_path)
            except OSError:
                pass
            if not self.no_spawn:
                self._spawn()

    def pause(self):
        with self._lock:
            os.makedirs(self.runtime_dir, exist_ok=True)
            open(self.pause_path, "w").close()

    def resume(self):
        with self._lock:
            try:
                os.remove(self.pause_path)
            except OSError:
                pass
            if not self.no_spawn and not self.child_alive():
                self._spawn()

    def stop(self):
        with self._lock:
            os.makedirs(self.runtime_dir, exist_ok=True)
            open(self.pause_path, "w").close()
            if self.child_alive():
                self.proc.terminate()


HERE = os.path.dirname(os.path.abspath(__file__))


class _State:
    """Holds the supervisor and builds a fresh snapshot per request.

    Each SSE/HTTP request reads independently; snapshot reads are stateless
    file reads and Supervisor guards its own mutations, so no extra lock here.
    """

    def __init__(self, supervisor):
        self.sup = supervisor

    def snapshot(self):
        now = int(time.time())
        loop_dir = self.sup.loop_dir
        events, _ = tail_events_from_text(_read(os.path.join(loop_dir, "events.jsonl")))
        last_t = events[-1].get("t") if events else None
        quota = parse_quota(_read_json(os.path.join(loop_dir, "runtime", "ratelimit.json")), now)
        status = event_verdict(last_t, now, self.sup.lock_exists(), self.sup.pause_exists(),
                               self.sup.last_log(), quota,
                               pausing=self.sup.pause_exists() and self.sup.loop_seems_active())
        return build_snapshot(loop_dir, now, status)


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
                self._send_json(state.snapshot())
            elif self.path == "/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                last = None
                try:
                    while True:
                        payload = json.dumps(state.snapshot())
                        if payload != last:
                            self.wfile.write(b"data: " + payload.encode("utf-8") + b"\n\n")
                            self.wfile.flush()
                            last = payload
                        time.sleep(1)
                except (BrokenPipeError, ConnectionResetError):
                    return
            else:
                self._send_json({"error": "not found"}, 404)

        def do_POST(self):
            actions = {"/api/start": state.sup.start, "/api/pause": state.sup.pause,
                       "/api/resume": state.sup.resume, "/api/stop": state.sup.stop}
            fn = actions.get(self.path)
            if fn is None:
                self._send_json({"error": "not found"}, 404)
                return
            fn()
            self._send_json({"status": state.sup.status()})

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


def main():
    ap = argparse.ArgumentParser(description="agent-loop dashboard server")
    ap.add_argument("--loop-dir", default=os.environ.get("LOOP_DIR", ".claude/loop/run"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("LOOP_DASH_PORT", "0")))
    ap.add_argument("--no-spawn", action="store_true",
                    help="read-only observer: the Start/Resume buttons will not "
                         "spawn run.sh (launch never auto-starts regardless)")
    args = ap.parse_args()

    loop_dir = os.path.abspath(args.loop_dir)
    config = parse_config(_read(os.path.join(loop_dir, "LOOP_CONFIG.md")))
    worktree = config.get("worktree") or os.getcwd()
    sup = Supervisor(loop_dir=loop_dir, plugin_root=os.path.dirname(HERE),
                     worktree=worktree, no_spawn=args.no_spawn)
    state = _State(sup)

    httpd = _QuietServer(("127.0.0.1", args.port), _make_handler(state))
    port = httpd.server_address[1]
    url = "http://127.0.0.1:%d" % port

    os.makedirs(sup.runtime_dir, exist_ok=True)
    with open(os.path.join(sup.runtime_dir, "dashboard.json"), "w") as f:
        json.dump({"pid": os.getpid(), "port": port, "url": url}, f)

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
