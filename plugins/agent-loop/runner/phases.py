"""One function per LLM phase: build the prompt, run it, validate what came back.

Every phase is a `claude -p` subprocess with a small role brief plus exactly the
context it needs. No phase is handed the plan, and no phase decides what happens
next — it returns JSON and the harness decides.

This module (Task 13) provides only the two functions and the two types every
phase is bracketed by: `render_prompt` fills a role's template, `parse_json_block`
reads its answer back out, `TickContext` carries the tick's identity, and
`_merge_usage` sums spend across a re-ask so a retried phase never drops cost
from the ledger. The role functions themselves (`run_scout`, `run_worker`, …)
are Task 14.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .claude_proc import Usage

PROMPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")
_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

# How much of a phase's raw reply to fold into a JsonBlockError message. Enough
# for an operator to recognise what the phase actually said; not so much that
# a runaway reply turns the error log into a second transcript.
_SNIPPET_LIMIT = 200


class JsonBlockError(ValueError):
    """The phase's reply carried no usable JSON document.

    Carries the phase name (when the caller supplies one) and a snippet of the
    raw text so an operator reading the harness log can see which phase said
    what, without having to go find the transcript first.
    """


@dataclass
class TickContext:
    """Everything a phase function needs about the tick it belongs to.

    Fields are exactly these eight — plan B's re-ask path and plan C's resume
    path both depend on this shape, so nothing is added, renamed or reordered
    here; plan C appends `resume_session`, `resume_count`, `last_session` on
    its own subclass or call site, not here.
    """
    cfg: Any
    plan: Any
    task: Any
    loop_dir: str
    runtime_dir: str
    events: Any
    tick: int
    attempt: int = 1


def render_prompt(name: str, **variables: Any) -> str:
    """Fill `{{placeholders}}` in `prompts/<name>.md`.

    An unfilled placeholder raises: shipping the literal `{{diff}}` to a model is
    a prompt that lies about what it was given, and the model will dutifully
    answer the literal text instead of the thing that was supposed to be there.
    """
    path = os.path.join(PROMPT_DIR, "%s.md" % name)
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        raise KeyError("no prompt named %r at %s" % (name, path))
    out = text
    for key, value in variables.items():
        out = out.replace("{{%s}}" % key, "" if value is None else str(value))
    missing = _PLACEHOLDER_RE.findall(out)
    if missing:
        raise KeyError("prompt %r left %s unfilled" % (name, ", ".join(sorted(set(missing)))))
    return out


def _snippet(text: str) -> str:
    text = (text or "").strip()
    if len(text) > _SNIPPET_LIMIT:
        return "%s…(%d more chars)" % (text[:_SNIPPET_LIMIT], len(text) - _SNIPPET_LIMIT)
    return text


def parse_json_block(text: str, phase: Optional[str] = None) -> Dict[str, Any]:
    """The last fenced JSON object in a phase's reply.

    Real model output wraps JSON in prose, fences it as ```json or a bare ```,
    sometimes emits two blocks (the last one wins — it is the final answer),
    and sometimes drops the fence entirely (falls back to the last bare `{…}`).
    Anything else raises `JsonBlockError` carrying the phase name (when given)
    and a snippet of the offending reply, so the caller can re-ask once with a
    clear idea of what went wrong instead of guessing from a bare "no JSON".
    """
    label = "phase %r" % phase if phase else "phase"
    candidates = [m.strip() for m in _FENCE_RE.findall(text or "")]
    if not candidates:
        start = (text or "").rfind("{")
        end = (text or "").rfind("}")
        if start >= 0 and end > start:
            candidates = [text[start:end + 1]]
    if not candidates:
        raise JsonBlockError("%s: no JSON block in the reply: %r" % (label, _snippet(text)))
    last_error: Any = None
    for raw in reversed(candidates):
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            last_error = exc
            continue
        if isinstance(parsed, dict):
            return parsed
        last_error = ValueError("expected a JSON object, got %s" % type(parsed).__name__)
    raise JsonBlockError(
        "%s: could not parse the JSON block (%s): %r" % (label, last_error, _snippet(text)))


def _merge_usage(a: Dict[str, Usage], b: Dict[str, Usage]) -> Dict[str, Usage]:
    """Sum two phases' usage per model without mutating either input.

    A re-ask is a second subprocess; dropping its usage is exactly how spend
    goes missing from the ledger.
    """
    out: Dict[str, Usage] = {}
    for source in (a or {}, b or {}):
        for model, usage in source.items():
            acc = out.setdefault(model, Usage())
            acc.cost_usd += usage.cost_usd
            acc.input_tokens += usage.input_tokens
            acc.output_tokens += usage.output_tokens
            acc.cache_read_tokens += usage.cache_read_tokens
            acc.cache_creation_tokens += usage.cache_creation_tokens
    return out
