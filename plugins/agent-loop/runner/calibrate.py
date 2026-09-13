"""Suggest `Limits:` values from a real run's events.jsonl (spec §4.2).

The shipped budgets are derived from one run (2026-09-11) and are explicitly
provisional. After a segment finishes, run

    python3 -m runner.calibrate .claude/loop/<run>

and paste the single line it prints into LOOP_CONFIG.md. v3's spans are exact —
one subprocess per phase, `role_start`/`role_end` around it — unlike 2.x's
description-regex role sniffing, so the input is trustworthy.
"""
from __future__ import annotations

import json
import math
import os
import sys
from typing import Any, Dict, List, Optional

from .config import DEFAULT_LIMITS

# The five roles spec §4.2 calibrates, keyed by the event's `role` lowercased.
ROLE_KEY = {"scout": "scout_timeout", "worker": "worker_timeout",
            "evaluator": "eval_timeout", "judge": "judge_timeout",
            "planner": "planner_timeout"}

# The documented `Limits:` line, in the interfaces doc's order.
DOC_KEYS = ("tick_timeout", "scout_timeout", "worker_timeout", "wrapup_timeout",
            "eval_timeout", "judge_timeout", "planner_timeout", "gate_cmd_timeout",
            "worker_resume_max", "worker_budget_usd", "max_attempts")

HEADROOM = 1.5


def events_path(target: str) -> str:
    """Accept either the events file or the loop dir that contains it."""
    if os.path.isdir(target):
        return os.path.join(target, "events.jsonl")
    return target


def role_spans(path: str) -> Dict[str, List[int]]:
    """{role: [seconds, …]} from paired role_start/role_end events, in order.

    An unpaired `role_start` is a phase that was killed: its wall clock is the
    budget, not the work, so counting it would calibrate every cap up to itself.
    `tick_start` closes the book on any start still open, which is exactly when a
    killed phase becomes visible.
    """
    spans: Dict[str, List[int]] = {}
    open_at: Dict[str, int] = {}
    try:
        handle = open(path)
    except OSError:
        return spans
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            kind = rec.get("type")
            if kind == "tick_start":
                open_at = {}
                continue
            role = str(rec.get("role") or "").lower()
            if not role:
                continue
            if kind == "role_start":
                open_at[role] = int(rec.get("t") or 0)
            elif kind == "role_end" and role in open_at:
                dur = int(rec.get("t") or 0) - open_at.pop(role)
                if dur >= 0:
                    spans.setdefault(role, []).append(dur)
    return spans


def p90(values: List[int]) -> int:
    """Nearest-rank p90. On a short sample that is the slowest run, which is the
    honest reading: a cap set from a median kills the tail every time."""
    ordered = sorted(values)
    if not ordered:
        return 0
    rank = int(math.ceil(0.9 * len(ordered)))
    return int(ordered[max(1, rank) - 1])


def round_up_60(secs: float) -> int:
    """Up to the next whole minute; never zero."""
    return max(60, int(math.ceil(float(secs) / 60.0) * 60))


def suggest(spans: Dict[str, List[int]]) -> Dict[str, Any]:
    """Every documented key: calibrated where observed, default where not.

    A role that never ran keeps its default. A run with no Judge must not
    silently shrink the Judge's budget to a minute.
    """
    values = dict(DEFAULT_LIMITS)
    for role, key in ROLE_KEY.items():
        observed = spans.get(role) or []
        if observed:
            values[key] = round_up_60(p90(observed) * HEADROOM)
    return dict((k, values[k]) for k in DOC_KEYS)


def limits_line(values: Dict[str, Any]) -> str:
    return "Limits: " + " ".join("%s=%s" % (k, values[k]) for k in DOC_KEYS)


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    targets = [a for a in args if not a.startswith("-")]
    if not targets:
        sys.stderr.write("usage: python3 -m runner.calibrate <loop-dir|events.jsonl> "
                         "[--json]\n")
        return 1
    path = events_path(targets[0])
    if not os.path.exists(path):
        sys.stderr.write("no events file at %s\n" % path)
        return 1
    spans = role_spans(path)
    values = suggest(spans)
    if as_json:
        print(json.dumps({"events": path,
                          "samples": dict((r, len(v)) for r, v in spans.items()),
                          "p90": dict((r, p90(v)) for r, v in spans.items()),
                          "suggested": values}, indent=2, sort_keys=True))
    else:
        print(limits_line(values))
    return 0


if __name__ == "__main__":
    sys.exit(main())
