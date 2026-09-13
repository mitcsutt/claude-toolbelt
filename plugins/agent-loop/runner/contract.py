"""runtime/sprint-<T>.json — the single document a Worker and an Evaluator see.

The Scout writes it; the harness validates it BEFORE dispatch and refuses an
invalid one. That refusal is the whole point: in the 2026-09-11 run the
contract for tick 85 named a success criterion about a file its own allow_list
forbade, and three Workers burned on it. Validation is cheap, mechanical, and
happens in Python — never asked of a model.
"""
from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import util

# A whitespace-delimited token that looks like a repo path. Built from
# characters a path (or a glob) legitimately contains, AND at least one of:
# a glob metachar, a dot-extension on its final segment, or two-or-more "/".
# A lone slash is not enough — "and/or", "24/7", "n/a" and "w/o" all have
# exactly one "/" and no extension, so none of them qualify. The extension
# check excludes an all-digit suffix ("0.6") so a bare decimal doesn't read
# as a path; "package.json" and "README.md" still qualify since their
# extensions aren't all-digit.
PATH_TOKEN_RE = re.compile(
    r"^(?=.*[*\[{]|(?:[^/]*/){2,}|.*\.(?!\d+$)\w+$)[\w.@#/\[\]{}*-]+$"
)
COPY_VERBS = ("copy", "port", "replicate")
VALID_SOURCES = ("plan", "spec", "scout")
READ_MARKER = "(read)"


class ContractError(ValueError):
    """The contract file is missing, unparseable, or not an object."""


@dataclass
class Forbidden:
    path: str
    source: str


@dataclass
class RenderGate:
    commands: List[str] = field(default_factory=list)
    screenshots: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class FidelitySource:
    src: str
    dst: str
    min_similarity: float


@dataclass
class Contract:
    task: str = ""
    success_criteria: List[str] = field(default_factory=list)
    allow_list: List[str] = field(default_factory=list)
    forbidden: List[Forbidden] = field(default_factory=list)
    verification: List[str] = field(default_factory=list)
    render_gate: Optional[RenderGate] = None
    fidelity_source: List[FidelitySource] = field(default_factory=list)
    evaluator_must_read: List[str] = field(default_factory=list)
    evaluator_must_view: List[str] = field(default_factory=list)
    estimated_diff_lines: int = 0
    scout_notes: str = ""
    relevant_learnings: List[str] = field(default_factory=list)


def _require_list(raw: Dict[str, Any], key: str) -> list:
    """The value at `key` as a list, defaulting to [] when the key is absent.

    A present-but-non-list value (a bare string, a number, a dict) must never
    be silently coerced — `list("outside allow_list")` explodes a string into
    single-character "criteria" and would let a lying contract pass
    `validate` as an empty, harmless list. Raise instead.
    """
    value = raw.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ContractError(
            "%s must be a list, got %s: %r" % (key, type(value).__name__, value))
    return value


def load_contract(path: str) -> Contract:
    raw = util.read_json(path)
    if not isinstance(raw, dict):
        raise ContractError("no readable contract at %s" % path)
    rg = raw.get("render_gate")
    render_gate = None
    if isinstance(rg, dict):
        render_gate = RenderGate(commands=list(rg.get("commands") or []),
                                 screenshots=list(rg.get("screenshots") or []))
    fidelity = []
    for item in _require_list(raw, "fidelity_source"):
        if not isinstance(item, dict):
            continue
        fidelity.append(FidelitySource(
            src=item.get("from", item.get("src", "")),
            dst=item.get("to", item.get("dst", "")),
            min_similarity=float(item.get("min_similarity", 0.0))))
    forbidden = []
    for item in _require_list(raw, "forbidden"):
        if isinstance(item, dict):
            forbidden.append(Forbidden(path=item.get("path", ""),
                                       source=item.get("source", "")))
    return Contract(
        task=raw.get("task", ""),
        success_criteria=list(_require_list(raw, "success_criteria")),
        allow_list=list(_require_list(raw, "allow_list")),
        forbidden=forbidden,
        verification=list(_require_list(raw, "verification")),
        render_gate=render_gate,
        fidelity_source=fidelity,
        evaluator_must_read=list(_require_list(raw, "evaluator_must_read")),
        evaluator_must_view=list(_require_list(raw, "evaluator_must_view")),
        estimated_diff_lines=int(raw.get("estimated_diff_lines") or 0),
        scout_notes=raw.get("scout_notes", "") or "",
        relevant_learnings=list(_require_list(raw, "relevant_learnings")),
    )


def to_dict(c: Contract) -> Dict[str, Any]:
    """The spec §5 JSON shape — `from`/`to` for a fidelity pair."""
    out: Dict[str, Any] = {
        "task": c.task,
        "success_criteria": c.success_criteria,
        "allow_list": c.allow_list,
        "forbidden": [{"path": f.path, "source": f.source} for f in c.forbidden],
        "verification": c.verification,
        "render_gate": None,
        "fidelity_source": [{"from": f.src, "to": f.dst,
                             "min_similarity": f.min_similarity}
                            for f in c.fidelity_source],
        "evaluator_must_read": c.evaluator_must_read,
        "evaluator_must_view": c.evaluator_must_view,
        "estimated_diff_lines": c.estimated_diff_lines,
        "scout_notes": c.scout_notes,
        "relevant_learnings": c.relevant_learnings,
    }
    if c.render_gate is not None:
        out["render_gate"] = {"commands": c.render_gate.commands,
                              "screenshots": c.render_gate.screenshots}
    return out


def save_contract(c: Contract, path: str) -> None:
    util.write_json(path, to_dict(c))


def _matches_any(path: str, patterns: List[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(path, pat):
            return True
    return False


def _path_allowed(token: str, allow_list: List[str]) -> bool:
    """True when the token names something inside the allow_list.

    Three ways to be inside: the token matches a pattern; the token sits under a
    directory the allow_list names; or the token IS a directory the allow_list
    writes into (a criterion may legitimately name the folder).
    """
    if _matches_any(token, allow_list):
        return True
    for pat in allow_list:
        base = pat.rstrip("/")
        if token.startswith(base + "/") or base.startswith(token.rstrip("/") + "/"):
            return True
    return False


def _scan_paths(text: str):
    """[(token, is_read_only)] for every repo-path-shaped token in `text`."""
    raw = text.split()
    out = []
    for i, tok in enumerate(raw):
        clean = tok.strip("`'\"(),;:")
        if not PATH_TOKEN_RE.match(clean):
            continue
        nxt = raw[i + 1].strip("`'\"") if i + 1 < len(raw) else ""
        out.append((clean, nxt == READ_MARKER))
    return out


def _cleanup_blocks(cleanup_text: str, task_id: str) -> bool:
    """True when LOOP_CLEANUP.md names this task on a content line."""
    pattern = re.compile(r"\b%s\b" % re.escape(task_id))
    for line in (cleanup_text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if pattern.search(stripped):
            return True
    return False


def _needs_fidelity(task) -> bool:
    if task.copy_of:
        return True
    first = (task.title or "").split()
    verb = first[0].lower().strip(".,:") if first else ""
    return verb in COPY_VERBS


def validate(c: Contract, task, cfg, cleanup_text: str,
             ui_globs: List[str]) -> List[str]:
    """[] when the contract may be dispatched; otherwise human-readable errors.

    Every rule accumulates — the Scout is re-dispatched once with the full list,
    so handing it one error at a time would waste a whole phase per problem.
    `cfg` is unused here; plan B reads `cfg.render` through this same signature.
    """
    errors: List[str] = []

    if c.task != task.id:
        errors.append("contract task %r does not match the plan row %r" % (c.task, task.id))
    if not c.success_criteria:
        errors.append("success_criteria is empty: nothing defines done for this task")
    if not c.allow_list:
        errors.append("allow_list is empty: the Worker would have nothing it may edit")
    if not c.verification:
        errors.append("verification is empty: the harness would have no gate to run")

    for text in list(c.success_criteria) + list(c.verification):
        for token, is_read in _scan_paths(text):
            if is_read or _path_allowed(token, c.allow_list):
                continue
            errors.append(
                "%r names %s, which is outside allow_list; add it, or mark the "
                "reference read-only by writing '%s (read)'" % (text, token, token))

    for f in c.forbidden:
        if f.source not in VALID_SOURCES:
            errors.append("forbidden %r has source %r; expected one of %s"
                          % (f.path, f.source, ", ".join(VALID_SOURCES)))

    if _cleanup_blocks(cleanup_text, task.id):
        errors.append("blocked-by-cleanup: LOOP_CLEANUP.md names %s as needing a "
                      "human decision, so it is not eligible" % task.id)

    if ui_globs and not task.no_ui and c.render_gate is None:
        touched = [p for p in c.allow_list if _matches_any(p, ui_globs)]
        if touched:
            errors.append("allow_list touches UI paths (%s) but the contract declares "
                          "no render_gate; add one or tag the plan row '| no-ui'"
                          % ", ".join(touched))

    if c.render_gate is not None:
        if not c.render_gate.commands:
            errors.append("render_gate declares no command to run")
        for shot in c.render_gate.screenshots:
            if not shot.get("name") or not shot.get("path"):
                errors.append("render_gate screenshot %r needs both a name and a path"
                              % (shot,))

    if _needs_fidelity(task) and not c.fidelity_source:
        errors.append("this is a copy/port task but the contract declares no "
                      "fidelity_source pair, so a six-line 'copy' would pass")
    for f in c.fidelity_source:
        if not (0.0 < f.min_similarity <= 1.0):
            errors.append("fidelity_source %s -> %s has min_similarity %r; expected a "
                          "ratio in (0, 1]" % (f.src, f.dst, f.min_similarity))

    return errors
