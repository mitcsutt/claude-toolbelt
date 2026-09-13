"""LOOP_CONFIG.md — the loop's durable settings, parsed once per harness run.

Keys are `Key: value`, exactly as v2 wrote and serve.py reads them. `Limits:`
and `Tiers:` are space-separated `k=v` tokens. `Tiers:` is the ONLY place a
concrete model name appears anywhere in this plugin; everything in code names a
tier and resolves it through `model_for`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

from . import util

TIER_ORDER = ("cheap", "standard", "most-capable")

# Spec §4.2/§4.6. Key names are the interfaces doc's, verbatim: every budget is
# `<role>_timeout`, and the last three are not seconds at all. `reviewer_timeout`
# and `learner_timeout` are not part of the documented `Limits:` line but are
# accepted and defaulted, because phase_limit is called for both roles.
DEFAULT_LIMITS = {
    "tick_timeout": 1800,
    "scout_timeout": 480,
    "worker_timeout": 1500,
    "wrapup_timeout": 300,
    "eval_timeout": 480,
    "judge_timeout": 360,
    "planner_timeout": 900,
    "reviewer_timeout": 900,
    "learner_timeout": 180,
    "gate_cmd_timeout": 600,
    "worker_resume_max": 1,
    "worker_budget_usd": 6,
    "max_attempts": 3,
}

# The phase name the harness uses -> the `Limits:` key that budgets it. The two
# differ wherever the spec's key is shorter than the phase (`evaluator` reads
# `eval_timeout`), so nothing may index cfg.limits by phase name directly.
PHASE_LIMIT_KEY = {
    "scout": "scout_timeout",
    "worker": "worker_timeout",
    "wrapup": "wrapup_timeout",
    "evaluator": "eval_timeout",
    "judge": "judge_timeout",
    "planner": "planner_timeout",
    "reviewer": "reviewer_timeout",
    "learner": "learner_timeout",
    "gate_cmd": "gate_cmd_timeout",
    "tick_timeout": "tick_timeout",
}

# Spec §4.2 "model tier (default)" column. An empty string means "no override":
# the Evaluator's tier is class-governed by the task's flag, not by config.
DEFAULT_ROLE_TIERS = {
    "planner": "most-capable",
    "reviewer": "most-capable",
    "judge": "most-capable",
    "scout": "standard",
    "worker": "standard",
    "evaluator": "",
    "learner": "cheap",
}


@dataclass
class LoopConfig:
    worktree: str = ""
    branch: str = ""
    goal: str = ""
    granularity: str = "single"
    tdd_mode: str = "none"
    verification: List[str] = field(default_factory=list)
    blocker_policy: str = "continue-independent"
    dashboard: str = "auto"
    medic: str = "auto"
    medic_model: str = ""
    tiers: Dict[str, str] = field(default_factory=dict)
    role_tiers: Dict[str, str] = field(default_factory=dict)
    limits: Dict[str, int] = field(default_factory=dict)
    decision_policy: str = "autonomous"
    render: Dict[str, Any] = field(default_factory=dict)
    spec_path: str = ""
    plan_path: str = ""


def _field_value(text: str, name: str) -> Any:
    m = re.search(r"^%s:[ \t]*(.*)$" % re.escape(name), text, re.MULTILINE)
    return m.group(1).strip() if m else None


def _first_word(value: Any, default: str) -> str:
    """First whitespace-delimited token, tolerating a trailing inline comment."""
    parts = (value or "").split()
    return parts[0] if parts else default


def _kv_tokens(value: Any) -> Dict[str, str]:
    out = {}
    for tok in (value or "").split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = v
    return out


# Spec §5.3. The `Render:` recipe is the one multi-line field in LOOP_CONFIG.md:
# every other key is a flat `Key: value` line read by `_field_value`. Sub-keys
# are indented, so the flat scan cannot see them and cannot be confused by them.
_RENDER_KEYS = ("start", "ready", "command", "ui_globs", "reference")


def _parse_render_block(lines: List[str], i: int) -> Any:
    """Parse the `Render:` block whose header is lines[i].

    Returns (render_dict, index_of_first_line_after_the_block). The block ends
    at the first non-indented, non-blank line or EOF; blank lines and indented
    `#` comments are skipped; unknown sub-keys are ignored. An inline value on
    the header line is the `command`, except a `<placeholder>` from the
    template, which means "no recipe".
    """
    render: Dict[str, Any] = {}
    inline = lines[i].split(":", 1)[1].strip()
    if inline and not inline.startswith("<"):
        render["command"] = inline
    j = i + 1
    while j < len(lines):
        line = lines[j].rstrip("\n")
        if line.strip() == "":
            j += 1
            continue
        if not (line.startswith(" ") or line.startswith("\t")):
            break
        body = line.strip()
        if body.startswith("#"):
            j += 1
            continue
        if ":" not in body:
            break
        key, _, val = body.partition(":")
        key = key.strip()
        val = val.strip()
        if key in _RENDER_KEYS and val:
            render[key] = val
        j += 1
    if "ui_globs" in render:
        render["ui_globs"] = render["ui_globs"].split()
    if "reference" in render:
        refs = []
        for token in render["reference"].split():
            name, sep, path = token.partition("=")
            if sep and name and path:
                refs.append({"name": name, "path": path})
        render["reference"] = refs
    return render, j


def load_config(path: str) -> LoopConfig:
    """Parse LOOP_CONFIG.md. Raises ValueError when the file is missing or empty."""
    text = util.read_text(path)
    if not text.strip():
        raise ValueError("no readable LOOP_CONFIG at %s" % path)

    limits = dict(DEFAULT_LIMITS)
    for k, v in _kv_tokens(_field_value(text, "Limits")).items():
        try:
            limits[k] = int(v)
        except ValueError:
            pass                      # a garbage token keeps the default

    tiers = {}
    for k, v in _kv_tokens(_field_value(text, "Tiers")).items():
        if k in TIER_ORDER:
            tiers[k] = v

    role_tiers = dict(DEFAULT_ROLE_TIERS)
    for role in list(role_tiers):
        v = _field_value(text, "%s tier" % role.capitalize())
        if v is not None:
            role_tiers[role] = v.strip()

    render: Dict[str, Any] = {}
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if line.startswith("Render:"):
            render, _ = _parse_render_block(lines, idx)
            break

    return LoopConfig(
        worktree=_field_value(text, "Worktree") or "",
        branch=_field_value(text, "Branch") or "",
        goal=_field_value(text, "Goal") or "",
        granularity=_first_word(_field_value(text, "Granularity"), "single"),
        tdd_mode=_first_word(_field_value(text, "TDD mode"), "none"),
        verification=(_field_value(text, "Verification pipeline") or "").split(),
        blocker_policy=_first_word(_field_value(text, "Blocker policy"),
                                   "continue-independent"),
        dashboard=_first_word(_field_value(text, "Dashboard"), "auto"),
        medic=_first_word(_field_value(text, "Medic"), "auto"),
        medic_model=_first_word(_field_value(text, "Medic model"), ""),
        tiers=tiers,
        role_tiers=role_tiers,
        limits=limits,
        decision_policy=_first_word(_field_value(text, "Decision policy"), "autonomous"),
        render=render,
        spec_path=_field_value(text, "Spec") or "",
        plan_path=_field_value(text, "Plan") or "",
    )


def model_for(cfg: LoopConfig, tier: str) -> str:
    """The model alias for a tier.

    An unknown tier is a programming error and raises. A *known* tier with no
    mapping returns "", which callers turn into "pass no --model flag" — the
    user's Claude default. That is the only reason no DEFAULT_TIERS constant
    exists here: naming a model in Python would break the one-place rule.
    """
    if tier not in TIER_ORDER:
        raise ValueError("unknown tier %r (expected one of %s)" % (tier, ", ".join(TIER_ORDER)))
    return cfg.tiers.get(tier, "")


def next_tier(tier: str) -> str:
    """One tier up, clamped at most-capable.

    A helper, not a policy. Spec §11 item 4: the tier of a re-attempt is the
    Judge's call from the Worker checkpoint, never a fixed ladder — so nothing in
    the failure path calls this. Plan C's `escalate` decision maps onto it.
    """
    if tier not in TIER_ORDER:
        raise ValueError("unknown tier %r" % tier)
    i = TIER_ORDER.index(tier)
    return TIER_ORDER[min(i + 1, len(TIER_ORDER) - 1)]


def phase_limit(cfg: LoopConfig, phase: str) -> int:
    """Wall-clock seconds for one phase; an unlisted phase gets the gate budget.

    The phase name is mapped through PHASE_LIMIT_KEY first, so `worker` reads
    `worker_timeout` and `evaluator` reads `eval_timeout`. A v2 config carrying a
    bare `worker=1200` token parses (it is kept in `limits`) but is never read —
    the key it would have to use is `worker_timeout`.
    """
    key = PHASE_LIMIT_KEY.get(phase, phase)
    if key in cfg.limits:
        return int(cfg.limits[key])
    return int(DEFAULT_LIMITS.get(key, DEFAULT_LIMITS["gate_cmd_timeout"]))
