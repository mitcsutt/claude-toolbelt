"""Harness-side visual verification: render gate, screenshot archival, fidelity.

Nothing in this module calls a model. The harness runs the commands, keeps the
images, and computes the ratios; the Evaluator is handed the results (spec 5.2).
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import gate as gate_mod
from . import git_ops
from .config import phase_limit


def artifacts_dir(ctx) -> str:
    """`$LOOP_DIR/artifacts/<T>` — created on demand."""
    path = os.path.join(ctx.loop_dir, "artifacts", ctx.task.id)
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def _abs(worktree: str, path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(worktree, path)


def _write_failure(out_dir: str, tag: str, n: int, text: str) -> str:
    path = os.path.join(out_dir, "gate-%s-%d.txt" % (tag, n))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return path


def _copy_atomic(src: str, dst: str) -> None:
    tmp = dst + ".tmp"
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def run_render_gate(ctx, contract) -> Tuple[List, List[Dict]]:
    """Run the contract's render commands, then archive every declared screenshot.

    Returns (command results, [{"name","path"}]) where `path` is the archived
    absolute path. A declared screenshot the commands did not produce is a
    failure: a synthetic CommandResult with rc=1 joins the list so the caller's
    `gate.all_ok` sees it.
    """
    rg = getattr(contract, "render_gate", None)
    if rg is None:
        return [], []

    cfg = ctx.cfg
    tag = "render-%s" % ctx.task.id
    results = list(gate_mod.run_commands(
        list(rg.commands),
        cfg.worktree,
        phase_limit(cfg, "gate_cmd"),
        ctx.runtime_dir,
        tag,
    ))
    if not gate_mod.all_ok(results):
        return results, []

    dest_dir = artifacts_dir(ctx)
    shots = []  # type: List[Dict]
    n = len(results)
    for shot in rg.screenshots:
        name = shot.get("name", "")
        rel = shot.get("path", "")
        src = _abs(cfg.worktree, rel)
        n += 1
        if not name or not rel:
            results.append(gate_mod.CommandResult(
                cmd="screenshot:%s" % (name or "<unnamed>"), rc=1, duration_s=0,
                output_path=_write_failure(
                    ctx.runtime_dir, tag, n,
                    "render_gate.screenshots entry needs both a name and a path; got %r\n" % (shot,),
                ),
                timed_out=False,
            ))
            continue
        if not os.path.isfile(src):
            results.append(gate_mod.CommandResult(
                cmd="screenshot:%s" % name, rc=1, duration_s=0,
                output_path=_write_failure(
                    ctx.runtime_dir, tag, n,
                    "screenshot %r not found at %s (declared as %r).\n"
                    "The render commands ran and exited 0 but wrote no image there.\n"
                    "Fix the contract's screenshot path so it matches where the\n"
                    "render command actually writes, or fix the command.\n" % (name, src, rel),
                ),
                timed_out=False,
            ))
            continue
        dst = os.path.join(dest_dir, "%s.png" % name)
        _copy_atomic(src, dst)
        shots.append({"name": name, "path": dst})
        ctx.events.emit("artifact", tick=ctx.tick, task=ctx.task.id, name=name, path=dst)

    if not gate_mod.all_ok(results):
        return results, []
    return results, shots


def reference_screenshots(cfg) -> List[Dict]:
    """Reference images from the `Render:` recipe, absolute, existing only."""
    out = []  # type: List[Dict]
    for ref in (cfg.render or {}).get("reference", []):
        name = ref.get("name", "")
        path = _abs(cfg.worktree, ref.get("path", ""))
        if name and os.path.isfile(path):
            out.append({"name": name, "path": path, "kind": "reference"})
    return out


READY_TIMEOUT_S = 120
READY_POLL_S = 2.0
STOP_GRACE_S = 10


class RenderAppError(Exception):
    """The render recipe's app would not start or would not become ready."""


def _pid_path(runtime_dir: str) -> str:
    return os.path.join(runtime_dir, "render-app.pid")


def _read_pid(runtime_dir: str) -> Optional[int]:
    try:
        with open(_pid_path(runtime_dir)) as fh:
            return int(fh.read().strip())
    except (IOError, OSError, ValueError):
        return None


# Apps this process started, by pid. A dead child stays in the process table as
# a zombie until someone reaps it, and `os.kill(zombie, 0)` succeeds — so without
# this, stop_app would burn its full SIGTERM grace on an app that exited
# instantly, then SIGKILL a corpse. poll() is the reap.
_APPS = {}  # type: Dict[int, subprocess.Popen]


def _alive(pid: int) -> bool:
    proc = _APPS.get(pid)
    if proc is not None and proc.poll() is not None:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _wait_ready(url: str, timeout_s: int, poll_s: float) -> None:
    deadline = time.time() + timeout_s
    last = "no attempt made"
    while time.time() < deadline:
        try:
            resp = urllib.request.urlopen(url, timeout=5)
            code = resp.getcode()
            resp.close()
            if code == 200:
                return
            last = "HTTP %s" % code
        except urllib.error.HTTPError as exc:
            last = "HTTP %s" % exc.code
        except Exception as exc:  # URLError, socket.timeout, ConnectionRefused
            last = "%s: %s" % (type(exc).__name__, exc)
        time.sleep(poll_s)
    raise RenderAppError(
        "render app never became ready: %s did not return 200 within %ds (last: %s)"
        % (url, timeout_s, last)
    )


def ensure_app(cfg, runtime_dir: str, events=None,
               ready_timeout_s: int = READY_TIMEOUT_S,
               poll_s: float = READY_POLL_S) -> Optional[int]:
    """Start the recipe's app once per harness; return its pid (None if no recipe)."""
    start = (cfg.render or {}).get("start", "")
    if not start:
        return None

    pid = _read_pid(runtime_dir)
    if pid is not None and _alive(pid):
        ready = (cfg.render or {}).get("ready", "")
        if ready:
            _wait_ready(ready, ready_timeout_s, poll_s)
        return pid

    log_path = os.path.join(runtime_dir, "render-app.log")
    log = open(log_path, "ab")
    proc = subprocess.Popen(
        start, shell=True, cwd=cfg.worktree,
        stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    log.close()
    tmp = _pid_path(runtime_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("%d\n" % proc.pid)
    os.replace(tmp, _pid_path(runtime_dir))
    _APPS[proc.pid] = proc
    if events is not None:
        events.emit("render_app_start", pid=proc.pid, cmd=start)

    ready = (cfg.render or {}).get("ready", "")
    if ready:
        try:
            _wait_ready(ready, ready_timeout_s, poll_s)
        except RenderAppError:
            stop_app(runtime_dir)
            raise
    return proc.pid


def stop_app(runtime_dir: str) -> None:
    """Kill the backgrounded render app and drop its pid file. Never raises."""
    pid = _read_pid(runtime_dir)
    if pid is None:
        return
    for sig, wait in ((signal.SIGTERM, STOP_GRACE_S), (signal.SIGKILL, 0)):
        if not _alive(pid):
            break
        try:
            os.killpg(os.getpgid(pid), sig)
        except OSError:
            try:
                os.kill(pid, sig)
            except OSError:
                break
        deadline = time.time() + wait
        while wait and _alive(pid) and time.time() < deadline:
            time.sleep(0.1)
    _APPS.pop(pid, None)
    try:
        os.unlink(_pid_path(runtime_dir))
    except OSError:
        pass


def run_fidelity(ctx, contract) -> List[Dict]:
    """Line-similarity ratio for every declared copy/port pair (spec 5.4)."""
    pairs = list(getattr(contract, "fidelity_source", []) or [])
    if not pairs:
        return []

    checks = []  # type: List[Dict]
    for fs in pairs:
        src = _abs(ctx.cfg.worktree, fs.src)
        dst = _abs(ctx.cfg.worktree, fs.dst)
        entry = {"src": fs.src, "dst": fs.dst, "min": fs.min_similarity}
        if not os.path.isfile(src):
            entry["ratio"] = 0.0
            entry["error"] = "reference file not found: %s" % src
        elif not os.path.isfile(dst):
            entry["ratio"] = 0.0
            entry["error"] = "target file not found: %s" % dst
        else:
            entry["ratio"] = round(git_ops.similarity(src, dst), 4)
        entry["ok"] = entry["ratio"] >= fs.min_similarity
        checks.append(entry)

    path = os.path.join(ctx.runtime_dir, "fidelity-%s.json" % ctx.task.id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"task": ctx.task.id, "t": int(time.time()), "checks": checks}, fh, indent=2)
    os.replace(tmp, path)
    return checks


def fidelity_text(checks: List[Dict]) -> str:
    """Human/model-readable summary; goes into the gate outputs on failure."""
    if not checks:
        return ""
    lines = ["## Fidelity (line similarity vs the reference)"]
    for c in checks:
        lines.append(
            "%s  %s -> %s  ratio=%.2f  min=%.2f%s"
            % ("PASS" if c["ok"] else "FAIL", c["src"], c["dst"],
               c["ratio"], c["min"],
               "  (%s)" % c["error"] if c.get("error") else "")
        )
    if any(not c["ok"] for c in checks):
        lines.append(
            "A copy/port task whose target barely resembles its reference is not a copy. "
            "Port the reference file's structure, not a note that says you did."
        )
    return "\n".join(lines)


@dataclass
class VisualResult:
    ok: bool = True
    render_results: List = field(default_factory=list)
    screenshots: List[Dict] = field(default_factory=list)
    references: List[Dict] = field(default_factory=list)
    fidelity: List[Dict] = field(default_factory=list)
    failure_text: str = ""


def run_visual_checks(ctx, contract, ready_timeout_s: int = READY_TIMEOUT_S,
                      poll_s: float = READY_POLL_S) -> VisualResult:
    """RENDER then FIDELITY. Either failing is a gate failure (spec 3, 5.3, 5.4)."""
    vis = VisualResult()
    problems = []  # type: List[str]

    if getattr(contract, "render_gate", None) is not None:
        try:
            ensure_app(ctx.cfg, ctx.runtime_dir, events=ctx.events,
                       ready_timeout_s=ready_timeout_s, poll_s=poll_s)
        except RenderAppError as exc:
            vis.ok = False
            vis.failure_text = "## Render app FAILED\n%s" % exc
            return vis
        vis.render_results, vis.screenshots = run_render_gate(ctx, contract)
        failed = [r for r in vis.render_results if r.rc != 0]
        if failed:
            vis.ok = False
            problems.append(
                "## Render gate FAILED\n"
                + "\n".join("%s (rc=%s%s) -> %s"
                            % (r.cmd, r.rc, ", timed out" if r.timed_out else "", r.output_path)
                            for r in failed)
            )
        vis.references = reference_screenshots(ctx.cfg)

    vis.fidelity = run_fidelity(ctx, contract)
    if any(not c["ok"] for c in vis.fidelity):
        vis.ok = False
        problems.append(fidelity_text(vis.fidelity))

    vis.failure_text = "\n\n".join(problems)
    return vis


def failure_kind(vis: VisualResult) -> str:
    """Which first-class failure kind this result is, or "" when it passed.

    Render before fidelity: an app that would not render is why the copy could
    not be compared, not the other way round. The kind is half of
    `run.failure_signature` and reaches the Judge verbatim, so the two stay
    distinct — "it built and looks wrong" must not read as "it did not build".
    """
    if vis.ok:
        return ""
    if not vis.failure_text.startswith("## Fidelity"):
        return "render"
    return "fidelity"


def evaluator_screenshots(vis: VisualResult) -> List[Dict]:
    """New images first, then the recipe's reference images."""
    return list(vis.screenshots) + list(vis.references)
