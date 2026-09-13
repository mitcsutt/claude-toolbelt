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
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import claude_proc, config, contract as contract_mod, util
from .claude_proc import PhaseResult, Usage

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

    The first eight fields are plan A's and are never renamed or reordered —
    plan B's re-ask path depends on that shape. The five after them are plan C's
    (interfaces doc, Cross-plan notes): they are defaulted and trailing, so every
    existing construction site still works. `tier` is the tier THIS attempt runs
    at, armed by the Judge's `changes.tier` and never derived from the attempt
    number (spec §11 item 4); `resume_count` is assigned from
    `TaskState.resumes_spent()` when the attempt opens, never counted
    independently, or `worker_resume_max` would bound nothing.
    """
    cfg: Any
    plan: Any
    task: Any
    loop_dir: str
    runtime_dir: str
    events: Any
    tick: int
    attempt: int = 1
    resume_session: Optional[str] = None
    resume_count: int = 0
    last_session: Optional[str] = None
    tier: Optional[str] = None
    extend_cap_s: int = 0


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


DIGEST_CAP = 2048                      # bytes; spec §9 keeps the 2 KB digest cap
_REASK = ("\n\n---\n\nYour previous reply could not be parsed as JSON: %s\n"
          "Reply again with the SAME content, ending in exactly one fenced ```json "
          "block matching the shape above. Add no commentary after it.")
_TDD_NOTE = ("**TDD is on for this run.** Invoke the "
             "`superpowers:test-driven-development` skill: write the failing test "
             "first, watch it fail, then make it pass.")
_FIRST_ATTEMPT = "(nothing — this is the first attempt at this task)"
_VERDICTS = ("PASS", "NEEDS_WORK", "BLOCKER")


def _env() -> Dict[str, str]:
    return dict(os.environ)


def _stall_s() -> int:
    try:
        return int(os.environ.get("STALL_S", "300"))
    except ValueError:
        return 300


def _read_section(text: str, heading: str) -> str:
    """The body under `## <heading>` up to the next `## `."""
    out = []
    collecting = False
    for line in text.splitlines():
        if line.startswith("## "):
            collecting = line[3:].strip().lower().startswith(heading.lower())
            continue
        if collecting:
            out.append(line)
    return "\n".join(out).strip()


def learnings_digest(loop_dir: str) -> str:
    """The `## Patterns` digest — the only part of the learnings on the hot path."""
    text = util.read_text(os.path.join(loop_dir, "LOOP_LEARNINGS.md"))
    return _read_section(text, "Patterns") or "(nothing learned yet)"


def knowledge_text(loop_dir: str, cap: int = DIGEST_CAP) -> str:
    """`.claude/loop/KNOWLEDGE.md` — durable, cross-run, sibling of the loop dir."""
    path = os.environ.get("LOOP_KNOWLEDGE") or os.path.join(
        os.path.dirname(os.path.abspath(loop_dir)), "KNOWLEDGE.md")
    body = _read_section(util.read_text(path), "Patterns")
    if not body:
        return "(no cross-run knowledge yet)"
    return body[:cap]


def spec_excerpt(cfg, task, cap: int = 4000) -> str:
    """The spec lines that mention this task's id, or the spec's opening."""
    if not cfg.spec_path:
        return "(no spec configured)"
    path = cfg.spec_path
    if not os.path.isabs(path):
        path = os.path.join(cfg.worktree, path)
    text = util.read_text(path)
    if not text:
        return "(spec not readable at %s)" % path
    lines = text.splitlines()
    pattern = re.compile(r"\b%s\b" % re.escape(task.id)) if task is not None else None
    hits = [ln for ln in lines if pattern and pattern.search(ln)]
    body = "\n".join(hits) if hits else "\n".join(lines[:60])
    return body[:cap]


def _result_with(base: PhaseResult, extra: PhaseResult) -> PhaseResult:
    """`extra` (the re-ask) as the phase's result, carrying both attempts' spend."""
    extra.usage_by_model = _merge_usage(base.usage_by_model, extra.usage_by_model)
    extra.tool_calls += base.tool_calls
    extra.started = base.started
    return extra


def _dispatch(ctx: TickContext, *, phase: str, role: str, prompt_name: str,
              tier: str, variables: Dict[str, Any], desc: str,
              max_turns: int = 0, max_budget_usd: Optional[float] = None,
              timeout_s: Optional[int] = None,
              resume_session: Optional[str] = None,
              correction: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None
              ) -> Tuple[PhaseResult, Optional[Dict[str, Any]]]:
    """Render, run, parse. One re-ask on a parse failure; none after a kill.

    `timeout_s` is normally derived from the phase name, but a caller may name
    it: the Worker's cap carries the Judge's one extension, and `worker-wrapup`
    is not a `Limits:` key at all (its budget is `wrapup_timeout`), so deriving
    it would silently hand the wrap-up the gate's default instead.

    `correction` inspects a *parsed* reply and returns the text to re-ask with,
    or None to accept it. It shares the ONE re-ask above rather than adding a
    second subprocess: a malformed block and an unusable-but-parseable reply are
    both "ask again, once". Callers never re-implement this loop — a hand-rolled
    second dispatch silently drops `stop_check`, the rate-limit capture and the
    stall watchdog that only live in these kwargs.
    """
    model = config.model_for(ctx.cfg, tier)
    if timeout_s is None:
        timeout_s = config.phase_limit(ctx.cfg, phase)
    prompt = render_prompt(prompt_name, **variables)
    kwargs = dict(phase=phase, model=model, cwd=ctx.cfg.worktree,
                  timeout_s=timeout_s, max_turns=max_turns,
                  max_budget_usd=max_budget_usd, env=_env(), events=ctx.events,
                  tick=ctx.tick, role=role, resume_session=resume_session,
                  activity_path=os.path.join(ctx.runtime_dir, "last-activity"),
                  ratelimit_path=os.path.join(ctx.runtime_dir, "ratelimit.json"),
                  stall_s=_stall_s(), desc=desc,
                  # Spec §4.4: STOP has to land "now", and the harness never holds
                  # the child's pid. Polled once a second inside run_phase.
                  stop_check=lambda: os.path.exists(
                      os.path.join(ctx.runtime_dir, "STOP")))
    res = claude_proc.run_phase(prompt=prompt, **kwargs)
    first = None
    try:
        first = parse_json_block(res.result_text, phase=phase)
    except JsonBlockError as exc:
        # A killed phase is never re-asked: the second dispatch would run into the
        # same wall. `stopped` implies `killed` today; naming it keeps the rule
        # true if that ever stops being so, because a STOP must spend nothing.
        if res.killed or res.stopped:
            return res, None
        # Python 3 unbinds the `as` name at the end of the except block, so the
        # message has to be carried out of it by hand.
        note = _REASK % str(exc)
    else:
        note = correction(first) if correction is not None else None
        if not note:
            return res, first
        if res.killed or res.stopped:
            return res, first
    again = claude_proc.run_phase(prompt=prompt + note, **kwargs)
    merged = _result_with(res, again)
    try:
        return merged, parse_json_block(again.result_text, phase=phase)
    except JsonBlockError:
        # The re-ask lost the thread. The first reply parsed, so it is still the
        # better answer of the two — the caller decides what to do with a reply
        # its correction rejected.
        return merged, first


# Spec §6 step 1: the wrap-up gets 8 turns. It is the load-bearing number --
# enough to read the tree and rewrite the checkpoint, not enough to restart
# the work the phase was just stopped for.
_WRAPUP_MAX_TURNS = 8


def worker_tier(ctx, tier: Optional[str] = None) -> str:
    """The tier to dispatch the Worker at. A lookup, not a ladder.

    Spec §11 item 4 (Mitch, parallel session): the tier of a re-attempt is judged
    from the Worker's checkpoint, not stepped up on a schedule. Most overruns are
    one extra iteration, and paying the top tier for them is how a cheap retry
    becomes an expensive one. So the attempt number is not an input here, and
    there is no ladder anywhere in the runner.

    Precedence: an explicit `tier` argument, then whatever the Judge armed on the
    context (`changes.tier`, applied by `judge.apply`), then the configured
    worker tier. Reading `ctx.tier` here is what makes the Judge's decision reach
    the dispatch without every call site having to thread it through.
    """
    chosen = (tier or getattr(ctx, "tier", None)
              or ctx.cfg.role_tiers.get("worker") or "standard")
    if chosen not in config.TIER_ORDER:
        chosen = "standard"
    return chosen


def worker_cap(ctx) -> int:
    """The Worker's wall clock: `worker_timeout` plus the Judge's one extension.

    `judge.validate_decision` has already bounded the extension at the phase
    default and refused a second one, so this is a sum, not a policy.
    """
    return config.phase_limit(ctx.cfg, "worker") + max(
        0, int(getattr(ctx, "extend_cap_s", 0) or 0))


def worker_checkpoint(ctx) -> str:
    """The Worker's own last checkpoint, for the `{{checkpoint}}` placeholder."""
    doc = util.read_json(os.path.join(ctx.runtime_dir, "worker-result.json"))
    if not isinstance(doc, dict):
        return "(no checkpoint written)"
    return doc.get("checkpoint") or "(no checkpoint written)"


def evaluator_tier(cfg, task) -> str:
    """Class-governed: `complex` earns the top tier, everything else standard.

    A configured `Evaluator tier:` is the ceiling for `complex` only. A flat
    config pin used to drag every ordinary clone onto the most capable model.
    """
    if getattr(task, "class_flag", None) == "complex":
        configured = cfg.role_tiers.get("evaluator") or ""
        return configured if configured in config.TIER_ORDER else "most-capable"
    return "standard"


def recipe_text(cfg) -> str:
    """The `Render:` recipe as prompt text for the Scout."""
    recipe = getattr(cfg, "render", None) or {}
    if not recipe:
        return ("(no render recipe is configured for this repo — leave render_gate null "
                "and say so in scout_notes)")
    lines = []  # type: List[str]
    for key in ("start", "ready", "command"):
        if recipe.get(key):
            lines.append("- %s: %s" % (key, recipe[key]))
    if recipe.get("ui_globs"):
        lines.append("- ui_globs: %s" % " ".join(recipe["ui_globs"]))
    for ref in recipe.get("reference", []):
        lines.append("- reference screenshot %s: %s" % (ref.get("name", ""), ref.get("path", "")))
    return "\n".join(lines)


def run_scout(ctx: TickContext, validation_errors: Optional[List[str]] = None
              ) -> Tuple[PhaseResult, Optional[contract_mod.Contract], List[str]]:
    """Dispatch the Scout, then load and validate the contract it wrote."""
    errors_block = "\n".join("- %s" % e for e in (validation_errors or [])) or "(none)"
    res, _ = _dispatch(
        ctx, phase="scout", role="Scout", prompt_name="scout",
        tier=ctx.cfg.role_tiers.get("scout") or "standard",
        desc="Scout %s" % ctx.task.id,
        variables={"loop_dir": ctx.loop_dir, "worktree": ctx.cfg.worktree,
                   "task_row": ctx.task.raw.strip(),
                   "spec_excerpt": spec_excerpt(ctx.cfg, ctx.task),
                   "learnings_digest": learnings_digest(ctx.loop_dir),
                   "knowledge": knowledge_text(ctx.loop_dir),
                   "render_recipe": recipe_text(ctx.cfg),
                   "validation_errors": errors_block})
    path = os.path.join(ctx.runtime_dir, "sprint-%s.json" % ctx.task.id)
    try:
        contract = contract_mod.load_contract(path)
    except contract_mod.ContractError:
        return res, None, ["the Scout wrote no readable contract at %s"
                           % os.path.basename(path)]
    cleanup = util.read_text(os.path.join(ctx.loop_dir, "LOOP_CLEANUP.md"))
    ui_globs = (ctx.cfg.render or {}).get("ui_globs", [])
    return res, contract, contract_mod.validate(contract, ctx.task, ctx.cfg,
                                                cleanup, ui_globs)


def run_worker(ctx: TickContext, contract, resume_session: Optional[str] = None,
               wrapup: bool = False, tier: Optional[str] = None,
               findings: str = "") -> Tuple[PhaseResult, Dict[str, Any]]:
    """Dispatch the Worker. Three shapes, one dispatch (spec §6).

    - **fresh** — a new session against the contract, with the Evaluator's
      findings (or the Judge's instruction, threaded through `findings`) in front
      of it. This branch is plan A's and is unchanged.
    - **wrap-up** — the phase was killed at its deadline; 8 turns on the SAME
      session to write an honest `worker-result.json` and stop. The checkpoint
      finally has a consumer.
    - **resume** — the Judge read the checkpoint and said continue: the same
      session again, with the minutes left, over a tree that still holds the
      partial work.

    Returns `(PhaseResult, payload)`. For the fresh branch the payload is the
    phase's own JSON block; for the other two it is `worker-result.json` as the
    Worker left it, because that file — not the reply — is what the next attempt
    reads.
    """
    tdd = _TDD_NOTE if ctx.cfg.tdd_mode not in ("", "none") else ""
    if wrapup or resume_session:
        name = "worker_wrapup" if wrapup else "worker_resume"
        variables = {"loop_dir": ctx.loop_dir, "worktree": ctx.cfg.worktree,
                     "task_row": ctx.task.raw.strip(),
                     "contract_json": json.dumps(contract_mod.to_dict(contract),
                                                 indent=2),
                     "checkpoint": worker_checkpoint(ctx)}
        if not wrapup:
            variables["minutes_left"] = str(max(1, worker_cap(ctx) // 60))
        res, _ = _dispatch(
            ctx,
            # A wrap-up is its own phase NAME so a reader can tell the two apart
            # in events and in the attempt record -- but it is still a Worker
            # turn against the same session and the same budget line, so
            # `TaskState.resumes_spent` counts it (spec §14 has no WRAPUP phase).
            phase="worker-wrapup" if wrapup else "worker",
            role="worker-wrapup" if wrapup else "Worker",
            prompt_name=name,
            tier=worker_tier(ctx, tier),
            desc=("Wrap up %s" % ctx.task.id if wrapup
                  else "Resume %s attempt %d" % (ctx.task.id, ctx.attempt)),
            timeout_s=(config.phase_limit(ctx.cfg, "wrapup") if wrapup
                       else worker_cap(ctx)),
            max_turns=_WRAPUP_MAX_TURNS if wrapup else 0,
            max_budget_usd=float(ctx.cfg.limits.get("worker_budget_usd", 6)),
            resume_session=resume_session,
            variables=variables)
        return res, worker_payload(ctx)

    res, data = _dispatch(
        ctx, phase="worker", role="Worker", prompt_name="worker",
        tier=worker_tier(ctx, tier),
        desc="Worker %s attempt %d" % (ctx.task.id, ctx.attempt),
        timeout_s=worker_cap(ctx),
        max_budget_usd=float(ctx.cfg.limits.get("worker_budget_usd", 6)),
        variables={"loop_dir": ctx.loop_dir, "worktree": ctx.cfg.worktree,
                   "contract_json": json.dumps(contract_mod.to_dict(contract),
                                               indent=2),
                   "tdd_note": tdd,
                   "evaluator_findings": findings or _FIRST_ATTEMPT})
    if data is None:
        data = {"status": "partial", "summary": "the Worker returned no usable JSON"}
    return res, data


def worker_payload(ctx) -> Dict[str, Any]:
    """`runtime/worker-result.json` as the Worker left it, `{}` if it wrote none."""
    doc = util.read_json(os.path.join(ctx.runtime_dir, "worker-result.json"))
    return doc if isinstance(doc, dict) else {}


# Spec §5.2. The Evaluator is handed its evidence instead of being trusted to
# fetch it: the 2026-09-11 run's T8 Evaluator made twelve Reads and none of them
# were in the reference tree, and the R3 one cited a `TODO(copied from …)`
# comment as proof a file had been copied.
MUST_READ_MAX_LINES = 400

_VIEW_REASK = ("\n\n---\n\nYour verdict was rejected: it reports no `views` entry "
               "for %s. Use the `Read` tool on the screenshot path(s) above, then "
               "reply again with the SAME verdict shape, one `views` entry per "
               "required view, each describing what you actually saw. Add no "
               "commentary after the JSON block.")


def _must_read_blocks(worktree: str, paths) -> str:
    """The reference files the Scout named, inlined, each capped and labelled."""
    out = []  # type: List[str]
    for rel in (paths or []):
        path = rel if os.path.isabs(rel) else os.path.join(worktree, rel)
        if not os.path.isfile(path):
            out.append("### %s\n\n(missing — no file at %s)" % (rel, path))
            continue
        lines = util.read_text(path).splitlines()
        body = "\n".join(lines[:MUST_READ_MAX_LINES])
        if len(lines) > MUST_READ_MAX_LINES:
            body += "\n[truncated] %d of %d lines shown" % (MUST_READ_MAX_LINES, len(lines))
        out.append("### %s\n\n```\n%s\n```" % (rel, body))
    return "\n\n".join(out) or "(none)"


def _screenshots_block(screenshots) -> str:
    """The archived images, by name and absolute path, with what to do with them."""
    shots = list(screenshots or [])
    if not shots:
        return "(none)"
    lines = []  # type: List[str]
    for shot in shots:
        # `render.evaluator_screenshots` hands over dicts; a bare path string is
        # accepted too, so a caller with nothing but paths cannot crash the
        # phase that is supposed to be looking at the images.
        if not isinstance(shot, dict):
            shot = {"name": os.path.splitext(os.path.basename(str(shot)))[0],
                    "path": str(shot)}
        kind = shot.get("kind", "new")
        lines.append("- **%s** (%s): %s" % (shot.get("name", "?"), kind,
                                            shot.get("path", "?")))
    lines.append("")
    lines.append("Read every path above with the `Read` tool before you judge, and "
                 "report one views[] entry per image describing what you actually "
                 "saw. A `reference` image is what the result is supposed to look "
                 "like; compare it to the new one.")
    return "\n".join(lines)


def _missing_views(data, required) -> List[str]:
    """Required view names the verdict did not report on."""
    seen = set()
    for view in (data or {}).get("views") or []:
        if isinstance(view, dict) and view.get("name"):
            seen.add(str(view["name"]))
    return [name for name in (required or []) if name not in seen]


def run_evaluator(ctx: TickContext, contract, diff_text: str, gate_outputs: str,
                  screenshots) -> Tuple[PhaseResult, Dict[str, Any]]:
    """Grade the diff against the contract, holding the evidence in its hands.

    The reference files the Scout marked `evaluator_must_read` are inlined and
    the archived screenshots are listed by path; a verdict that skips a required
    view is re-asked once and then fails the tick. The re-ask is `_dispatch`'s
    one budget (R4), shared with the malformed-JSON case — not a second
    subprocess per phase.
    """
    required = [str(v) for v in (contract.evaluator_must_view or [])]

    def correction(data):
        missing = _missing_views(data, required)
        return (_VIEW_REASK % ", ".join(missing)) if missing else None

    res, data = _dispatch(
        ctx, phase="evaluator", role="Evaluator", prompt_name="evaluator",
        tier=evaluator_tier(ctx.cfg, ctx.task),
        desc="Evaluate %s" % ctx.task.id,
        variables={"task_row": ctx.task.raw.strip(),
                   "contract_json": json.dumps(contract_mod.to_dict(contract), indent=2),
                   "diff": diff_text or "(empty diff)",
                   "gate_outputs": gate_outputs or "(none)",
                   "must_read_blocks": _must_read_blocks(
                       ctx.cfg.worktree, contract.evaluator_must_read),
                   "screenshots": _screenshots_block(screenshots)},
        correction=correction if required else None)
    if data is None:
        return res, {"verdict": "NEEDS_WORK", "findings": [], "views": [],
                     "reason": "malformed-output",
                     "summary": "the Evaluator returned malformed output twice"}
    if data.get("verdict") not in _VERDICTS:
        data["verdict"] = "NEEDS_WORK"
    data.setdefault("findings", [])
    data.setdefault("views", [])
    data.setdefault("summary", "")
    missing = _missing_views(data, required)
    if missing:
        # Spec §5.2: an unseen view is not a pass. This takes the ordinary
        # failure path, so the Judge sees it like any other NEEDS_WORK.
        data["verdict"] = "NEEDS_WORK"
        data["reason"] = "missing-views"
        data["summary"] = (
            "the Evaluator reported no view for %s even after being asked again; "
            "its verdict cannot be trusted on what it did not look at. %s"
            % (", ".join(missing), data.get("summary", ""))).strip()
    return res, data


def run_learner(ctx: TickContext, contract, gate_outputs: str, verdict: str
                ) -> PhaseResult:
    """Record what this task taught, with evidence, and rewrite the learnings."""
    res, data = _dispatch(
        ctx, phase="learner", role="Learner", prompt_name="learner", tier="cheap",
        desc="Learn from %s" % ctx.task.id,
        variables={"task_row": ctx.task.raw.strip(), "verdict": verdict or "(none)",
                   "gate_outputs": gate_outputs or "(none)",
                   "learnings_digest": learnings_digest(ctx.loop_dir)})
    if data:
        apply_learnings(ctx.loop_dir, data)
    return res


def run_planner(ctx: TickContext, segment: str) -> Tuple[PhaseResult, Dict[str, Any]]:
    """Write the rows for one segment. The harness re-parses and validates them."""
    res, data = _dispatch(
        ctx, phase="planner", role="Planner", prompt_name="planner",
        tier=ctx.cfg.role_tiers.get("planner") or "most-capable",
        desc="Plan %s" % segment,
        variables={"loop_dir": ctx.loop_dir, "worktree": ctx.cfg.worktree,
                   "segment": segment,
                   "spec_excerpt": spec_excerpt(ctx.cfg, ctx.task),
                   "learnings_digest": learnings_digest(ctx.loop_dir)})
    return res, (data or {"tasks_added": 0})


def run_reviewer(ctx: TickContext, segment: str, diff_text: str
                 ) -> Tuple[PhaseResult, Dict[str, Any]]:
    """Grade a finished segment as a whole."""
    rows = [t.raw.strip() for t in ctx.plan.tasks() if t.segment == segment]
    res, data = _dispatch(
        ctx, phase="reviewer", role="Reviewer", prompt_name="reviewer",
        tier=ctx.cfg.role_tiers.get("reviewer") or "most-capable",
        desc="Review %s" % segment,
        variables={"loop_dir": ctx.loop_dir, "segment": segment,
                   "task_row": "\n".join(rows) or "(no rows)",
                   "spec_excerpt": spec_excerpt(ctx.cfg, ctx.task),
                   "diff": diff_text or "(empty diff)"})
    return res, (data or {})


def _trim_digest(lines: List[str], cap: int = DIGEST_CAP) -> List[str]:
    """Drop oldest-first until the rendered digest fits the cap."""
    kept = list(lines)
    while kept and len(("\n".join(kept)).encode("utf-8")) > cap:
        kept.pop(0)
    return kept


def apply_learnings(loop_dir: str, data: Dict[str, Any]) -> None:
    """Rewrite LOOP_LEARNINGS.md: evidenced patterns up top, everything else below.

    A pattern with no evidence goes to the log and never to the digest. One run
    promoted a false "genuinely copied" claim into its patterns and every later
    Scout inlined the lie into a fresh contract.
    """
    path = os.path.join(loop_dir, "LOOP_LEARNINGS.md")
    text = util.read_text(path)
    patterns = [ln for ln in _read_section(text, "Patterns").splitlines() if ln.strip()]
    invariants = [ln for ln in _read_section(text, "Invariants").splitlines() if ln.strip()]
    log = _read_section(text, "Log")

    demoted = []
    for item in data.get("patterns") or []:
        rule = (item.get("rule") or "").strip()
        evidence = (item.get("evidence") or "").strip()
        if not rule:
            continue
        if not evidence:
            demoted.append("- (no evidence, not promoted) %s" % rule)
            continue
        row = "- %s — evidence: %s" % (rule, evidence)
        if not any(rule in existing for existing in patterns):
            patterns.append(row)

    for item in data.get("invariants") or []:
        rule = (item.get("rule") or "").strip()
        check = (item.get("check") or "").strip()
        if not rule or not check:
            continue
        row = "- %s — check: `%s`" % (rule, check)
        if not any(rule in existing for existing in invariants):
            invariants.append(row)

    entry = (data.get("log") or "").strip()
    new_log = [log] if log else []
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if entry:
        new_log.append("- %s %s" % (stamp, entry))
    new_log.extend(demoted)

    util.atomic_write(path, "\n".join([
        "# Loop Learnings", "",
        "## Patterns", "\n".join(_trim_digest(patterns)), "",
        "## Invariants", "\n".join(invariants), "",
        "## Log", "\n".join(new_log), "",
    ]))
