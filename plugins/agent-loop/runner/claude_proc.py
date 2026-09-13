"""One `claude -p` subprocess per phase, with its stream parsed in this process.

This is where v2's per-line `jq` forks and its FIFO go away. Everything the
harness needs about a phase — the session id (so a killed Worker is resumable),
the usage (so a killed phase still reports what it spent), the outstanding tool
calls (so "in a 20-minute test run" is not mistaken for "stalled"), and the
activity stamp — comes out of this one pass over stdout.

Two rules shape every branch below. A `PhaseResult` is returned for *every* way
a phase can end — clean exit, non-zero exit, timeout, STOP, signal, a `claude`
that will not exec, a stream this parser cannot make sense of — because a caller
that gets an exception instead has no usage to record and no session to resume.
And the usage seen so far is never dropped on the way out, because spend that
vanishes with a killed phase is spend nobody ever attributes.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from . import util


@dataclass
class Usage:
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


@dataclass
class PhaseResult:
    phase: str
    model: str
    rc: Optional[int] = None
    killed: bool = False
    timed_out: bool = False
    session_id: Optional[str] = None
    transcript_path: Optional[str] = None
    started: int = 0
    ended: int = 0
    result_text: str = ""
    usage_by_model: Dict[str, Usage] = field(default_factory=dict)
    tool_calls: int = 0
    last_activity: int = 0
    is_error: bool = False
    api_error_status: str = ""
    rate_limit: Optional[Dict[str, Any]] = None
    stalled: bool = False
    # Killed specifically because STOP was requested. `killed` alone cannot
    # say why — it is also true for a timeout, an external SIGTERM and a
    # stream error — and the caller's answer differs: a STOP ends the tick
    # cleanly as a pause, a timeout is a failed attempt.
    stopped: bool = False
    stream_error: bool = False


def build_argv(claude_bin: str, model: str, resume_session: Optional[str],
               max_turns: int, max_budget_usd: Optional[float],
               allowed_tools: Optional[List[str]]) -> List[str]:
    """The exact command line for one phase. Flags are omitted, never empty."""
    argv = [claude_bin, "-p", "--output-format", "stream-json", "--verbose",
            "--dangerously-skip-permissions"]
    if model:
        argv.extend(["--model", model])
    if resume_session:
        argv.extend(["--resume", resume_session])
    if max_turns:
        argv.extend(["--max-turns", str(int(max_turns))])
    if max_budget_usd is not None:
        argv.extend(["--max-budget-usd", str(max_budget_usd)])
    if allowed_tools:
        argv.extend(["--allowedTools", ",".join(allowed_tools)])
    return argv


def is_stalled(last_activity: float, outstanding: int, stall_s: int, now: float) -> bool:
    """A phase is stalled only when it is quiet AND not inside a tool call.

    A 20-minute `pnpm turbo test` emits nothing while it runs; v2's detector
    called that a stall seven times in one run and was wrong every time.
    """
    return outstanding == 0 and (now - last_activity) > stall_s


def _cwd_encodings(cwd: str) -> List[str]:
    """The project-directory names Claude Code may have used for `cwd`.

    It encodes every non-alphanumeric byte as `-`, so a loop worktree under
    `<repo>/.claude-worktrees/<branch>` lands in `…-repo--claude-worktrees-…`.
    The `/`-only form is kept as a second candidate so a future change to that
    encoding degrades to "transcript not found" rather than to a wrong path.
    """
    path = os.path.abspath(cwd)
    return [re.sub(r"[^A-Za-z0-9]", "-", path), path.replace("/", "-")]


def transcript_path(cwd: str, session_id: Optional[str]) -> Optional[str]:
    """<config dir>/projects/<encoded cwd>/<session>.jsonl, if it exists."""
    if not session_id:
        return None
    bases = []
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        bases.append(override)
    bases.append(os.path.join(os.path.expanduser("~"), ".claude"))
    for base in bases:
        for encoded in _cwd_encodings(cwd):
            path = os.path.join(base, "projects", encoded, "%s.jsonl" % session_id)
            if os.path.exists(path):
                return path
    return None


def _terminate(proc: subprocess.Popen) -> None:
    """SIGTERM the phase's process group, then SIGKILL what survives 30 s.

    A `claude` wedged under memory pressure can ignore SIGTERM entirely, which
    is how a "timed out" tick used to keep holding RAM.
    """
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        return
    for sig, grace in ((signal.SIGTERM, 30), (signal.SIGKILL, 10)):
        try:
            os.killpg(pgid, sig)
        except OSError:
            return
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


class _Killer(object):
    """Serialises the kill path and refuses to signal a reaped child.

    `_terminate` signals a process *group* derived from the child's pid. Once
    the child has been reaped that pid can be recycled, and the group it names
    then belongs to something else entirely — so the last thing the main thread
    does before `wait()` is close this.
    """

    def __init__(self, proc: subprocess.Popen):
        self._proc = proc
        self._lock = threading.Lock()
        self._closed = False

    def kill(self) -> None:
        with self._lock:
            if self._closed:
                return
            _terminate(self._proc)

    def close(self) -> None:
        with self._lock:
            self._closed = True


def _stamp(path: str, when: float) -> None:
    """Best-effort activity stamp: a full disk must not abandon a live phase."""
    if not path:
        return
    try:
        util.stamp_epoch(path, when)
    except OSError:
        return


def _obj(value: Any) -> Dict[str, Any]:
    """`value` when it is a JSON object, an empty one otherwise.

    Every field below comes off a model's stream, where a tool result can carry
    anything; one AttributeError here would throw away a whole phase.
    """
    return value if isinstance(value, dict) else {}


def _usage_from_message(u: Dict[str, Any]) -> Usage:
    return Usage(cost_usd=0.0,
                 input_tokens=_int(u.get("input_tokens")),
                 output_tokens=_int(u.get("output_tokens")),
                 cache_read_tokens=_int(u.get("cache_read_input_tokens")),
                 cache_creation_tokens=_int(u.get("cache_creation_input_tokens")))


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _usage_total(u: Usage) -> int:
    return u.input_tokens + u.output_tokens + u.cache_read_tokens + u.cache_creation_tokens


def _fold(per_message: Dict[str, Any]) -> Dict[str, Usage]:
    """Sum the kept-max-per-message-id readings into one Usage per model."""
    out: Dict[str, Usage] = {}
    for model, usage in per_message.values():
        acc = out.setdefault(model, Usage())
        acc.input_tokens += usage.input_tokens
        acc.output_tokens += usage.output_tokens
        acc.cache_read_tokens += usage.cache_read_tokens
        acc.cache_creation_tokens += usage.cache_creation_tokens
    return out


def _from_model_usage(raw: Any) -> Dict[str, Usage]:
    out: Dict[str, Usage] = {}
    for model, u in _obj(raw).items():
        if not isinstance(u, dict):
            continue
        out[model] = Usage(cost_usd=_float(u.get("costUSD")),
                           input_tokens=_int(u.get("inputTokens")),
                           output_tokens=_int(u.get("outputTokens")),
                           cache_read_tokens=_int(u.get("cacheReadInputTokens")),
                           cache_creation_tokens=_int(u.get("cacheCreationInputTokens")))
    return out


def _tool_desc(inp: Dict[str, Any]) -> str:
    for key in ("description", "file_path", "path"):
        val = inp.get(key)
        if val:
            return str(val)[:120]
    cmd = inp.get("command")
    return str(cmd)[:60] if cmd else ""


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)


def run_phase(*, phase: str, model: str, prompt: str, cwd: str, timeout_s: int,
              max_turns: int, max_budget_usd: Optional[float], env: Dict[str, str],
              events, tick: int, role: str, activity_path: str,
              resume_session: Optional[str] = None,
              allowed_tools: Optional[List[str]] = None,
              stall_s: int = 300, ratelimit_path: Optional[str] = None,
              desc: str = "", claude_bin: str = "claude",
              stop_check: Optional[Callable[[], bool]] = None) -> PhaseResult:
    """Run one phase to completion (or to its timeout) and return what happened.

    A PhaseResult is ALWAYS returned, with whatever usage was seen — that is the
    fix for the ~$61 of unattributed spend in the 2026-09-11 run, where a killed
    tick wrote no by_model at all.

    `stop_check` is polled once a second; when it returns true the phase is
    SIGTERMed exactly as a timeout would, with `killed` set and `timed_out`
    clear. `runtime/STOP` has to land "now" (spec §4.4) and the caller never
    holds the child's pid, so this watchdog is the only thing that can honour it.

    `claude_bin` exists because `subprocess.Popen(env=…)` does not use
    `env["PATH"]` to resolve the program name on POSIX: a test cannot redirect
    `claude` through `env` alone.
    """
    argv = build_argv(claude_bin, model, resume_session, max_turns,
                      max_budget_usd, allowed_tools)
    started = int(time.time())
    # A phase that dies before its init line still belongs to the session it was
    # resuming; forgetting that is how a resume silently pays for a fresh one.
    result = PhaseResult(phase=phase, model=model, started=started, ended=started,
                         last_activity=started, session_id=resume_session or None)
    events.emit("role_start", role=role, model=model or "default", desc=desc, tick=tick)

    try:
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                start_new_session=True, universal_newlines=True,
                                errors="replace", bufsize=1)
    except OSError:
        result.rc = 127
        result.ended = int(time.time())
        events.emit("role_end", role=role)
        events.emit("phase_end", phase=phase, rc=127, dur=result.ended - result.started,
                    session_id=result.session_id or "", transcript="")
        return result

    state: Dict[str, Any] = {"last": float(started), "outstanding": set(),
                             "timed_out": False, "stopped": False}
    per_message: Dict[str, Any] = {}
    anon = [0]

    def feed_stdin():
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except (OSError, ValueError):
            pass

    writer = threading.Thread(target=feed_stdin, name="phase-stdin")
    writer.daemon = True
    writer.start()

    stop = threading.Event()
    killer = _Killer(proc)
    # Monotonic, not wall clock: an NTP step during an overnight run must not
    # extend a phase forever or kill one early.
    deadline = time.monotonic() + timeout_s

    def watchdog():
        while not stop.wait(1.0):
            if stop_check is not None:
                try:
                    requested = bool(stop_check())
                except Exception:
                    requested = False
                if requested:
                    state["stopped"] = True
                    killer.kill()
                    return
            if time.monotonic() >= deadline:
                state["timed_out"] = True
                killer.kill()
                return

    watch = threading.Thread(target=watchdog, name="phase-watchdog")
    watch.daemon = True
    watch.start()

    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            state["last"] = time.time()
            _stamp(activity_path, state["last"])
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            kind = rec.get("type")
            if kind == "system" and rec.get("subtype") == "init":
                result.session_id = rec.get("session_id") or result.session_id
            elif kind == "assistant":
                msg = _obj(rec.get("message"))
                mid = msg.get("id")
                if not isinstance(mid, str) or not mid:
                    # No id means no way to tell a re-reading from a new message.
                    # Accumulate rather than keep the max: under-counting spend
                    # is the worse of the two mistakes.
                    anon[0] += 1
                    mid = "\x00anon-%d" % anon[0]
                mmodel = msg.get("model") or model or "unknown"
                if not isinstance(mmodel, str):
                    mmodel = str(mmodel)
                usage = _usage_from_message(_obj(msg.get("usage")))
                prev = per_message.get(mid)
                if prev is None or _usage_total(usage) >= _usage_total(prev[1]):
                    per_message[mid] = (mmodel, usage)
                content = msg.get("content")
                for block in content if isinstance(content, list) else []:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    result.tool_calls += 1
                    if block.get("id"):
                        state["outstanding"].add(block["id"])
                    events.emit("tool", role=role, name=block.get("name") or "tool",
                                count=result.tool_calls,
                                desc=_tool_desc(_obj(block.get("input"))))
            elif kind == "user":
                content = _obj(rec.get("message")).get("content")
                for block in content if isinstance(content, list) else []:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        state["outstanding"].discard(block.get("tool_use_id"))
            elif kind == "rate_limit_event":
                info = rec.get("rate_limit_info")
                if isinstance(info, dict):
                    result.rate_limit = info
                    if ratelimit_path:
                        try:
                            util.write_json(ratelimit_path, info)
                        except OSError:
                            pass
            elif kind == "result":
                result.result_text = _as_text(rec.get("result"))
                result.is_error = bool(rec.get("is_error"))
                status = rec.get("api_error_status")
                result.api_error_status = "" if status is None else str(status)
                model_usage = _from_model_usage(rec.get("modelUsage"))
                if model_usage:
                    result.usage_by_model = model_usage
    except Exception:
        # A parser that fell over must not strand a live `claude` holding RAM,
        # and must still hand the caller the usage it had already seen.
        result.stream_error = True
        killer.kill()
    except BaseException:
        killer.kill()
        raise
    finally:
        stop.set()
        killer.close()
        rc = proc.wait()
        watch.join(timeout=5)
        writer.join(timeout=5)
        # One loop is hundreds of phases in one process: a pipe left open per
        # phase is a file-descriptor leak with a hard ceiling at the end of it.
        for stream in (proc.stdin, proc.stdout):
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass

    if rc < 0:                                   # killed by a signal
        rc = 128 - rc
    result.rc = rc
    result.timed_out = bool(state["timed_out"])
    result.stopped = bool(state["stopped"])
    result.killed = (result.timed_out or result.stopped or result.stream_error
                     or rc in (137, 143))
    result.ended = int(time.time())
    result.last_activity = int(state["last"])
    # The end-of-run condition, not a latch: a phase that went quiet for ten
    # minutes and then finished is not an incident, and calling it one is
    # exactly what v2's detector got wrong.
    result.stalled = is_stalled(state["last"], len(state["outstanding"]),
                                stall_s, time.time())
    if not result.usage_by_model:
        result.usage_by_model = _fold(per_message)
    result.transcript_path = transcript_path(cwd, result.session_id)

    events.emit("role_end", role=role)
    events.emit("phase_end", phase=phase, rc=result.rc,
                dur=result.ended - result.started,
                session_id=result.session_id or "",
                transcript=result.transcript_path or "")
    return result
