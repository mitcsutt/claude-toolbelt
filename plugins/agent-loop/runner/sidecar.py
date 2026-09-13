"""One dashboard per loop dir: adopt a live standalone one, else spawn a sidecar.

A standalone dashboard (the one /agent-loop opened, or the one whose Start
button spawned this harness) outlives any single harness and is never killed on
exit. A sidecar belongs to this harness and dies with it. A dead sidecar is a
warn-level fact, never a reason to stop the loop.
"""
from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from typing import Callable, List, Optional

from . import util


def adoptable(runtime_dir: str) -> Optional[str]:
    """The URL of a live, non-sidecar dashboard for this loop dir, else None.

    Every state maps to a definite answer: no record or a garbage one -> None
    (nothing to adopt); a sidecar record -> None regardless of liveness (a
    sidecar belongs to whichever harness spawned it, never to this one, so
    `Dashboard:` deciding whether *this* harness spawns is a separate question
    from whether *that* one is still alive); a dead pid, a missing pid, or a
    pid reused by some unrelated process (defeated by `process_alive`'s
    command-line check) -> None; only a live, non-sidecar record with a URL
    and a pid whose command line still says `serve.py` is adopted.
    """
    record = util.read_json(os.path.join(runtime_dir, "dashboard.json"))
    if not isinstance(record, dict):
        return None
    if record.get("sidecar"):
        return None
    url = record.get("url")
    if not url:
        return None
    if not util.process_alive(record.get("pid"), "serve.py"):
        return None
    return url


def build_argv(plugin_root: str, loop_dir: str, runtime_dir: str,
               cmd_override: Optional[str]) -> List[str]:
    """The sidecar's command line, reusing the previous port when there was one."""
    if cmd_override:
        return shlex.split(cmd_override)
    record = util.read_json(os.path.join(runtime_dir, "dashboard.json")) or {}
    port = record.get("port") or 0
    return [sys.executable, os.path.join(plugin_root, "web", "serve.py"),
            "--sidecar", "--no-spawn", "--loop-dir", loop_dir, "--port", str(port)]


def start(plugin_root: str, loop_dir: str, runtime_dir: str,
          cmd_override: Optional[str] = None, wait_s: float = 5.0) -> Optional[str]:
    """Spawn the observer and return the URL it announces, or None in time.

    Kills any sidecar this runtime dir already has recorded first. That record
    can outlive an unclean harness exit (a crash skips `stop()`), and without
    this a resumed harness would leave that orphan running while it spawns a
    second one -- exactly the two-dashboards-fighting-over-one-port failure
    this module exists to prevent. A pid already confirmed dead costs nothing
    extra: `stop()` no-ops on it.
    """
    stop(runtime_dir)
    os.makedirs(runtime_dir, exist_ok=True)
    out_path = os.path.join(runtime_dir, "dashboard.out")
    argv = build_argv(plugin_root, loop_dir, runtime_dir,
                      cmd_override or os.environ.get("LOOP_DASHBOARD_CMD"))
    try:
        with open(out_path, "w") as out:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, start_new_session=True)
    except OSError:
        return None
    util.atomic_write(os.path.join(runtime_dir, "sidecar.pid"), str(proc.pid))

    stop_at = wait_s
    step = 0.25
    waited = 0.0
    while waited < stop_at:
        for line in util.read_text(out_path).splitlines():
            if "dashboard-started" not in line:
                continue
            try:
                url = json.loads(line).get("url")
            except ValueError:
                continue
            if url:
                return url
        time.sleep(step)
        waited += step
    return None


def _signal_group(pid: int, sig: int) -> None:
    """Signal `pid`'s whole process group, not just `pid` itself.

    `start()` always launches with `start_new_session=True`, which makes the
    sidecar the leader of its own new session and process group -- so this
    reaches any child it forked (a stub script's `sleep`, in production
    nothing) without touching the harness's own group. Falls back to
    signalling the bare pid when the group lookup fails (the pid is already
    gone), which is a safe no-op either way.
    """
    try:
        pgid = os.getpgid(pid)
    except OSError:
        return
    try:
        os.killpg(pgid, sig)
    except OSError:
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def _reap(pid: int, timeout: float = 5.0) -> None:
    """Collect `pid` once it exits, escalating to SIGKILL, never hanging.

    `os.kill(pid, 0)` (what `process_alive` uses) reports a zombie as alive,
    since the kernel keeps its pid until something waits on it -- so without
    this, a sidecar we just killed would keep reading as "alive" to every
    liveness check until some unrelated code happened to reap it. `waitpid`
    only works when this process is `pid`'s parent (the normal case: the same
    harness that spawned it stops it); on an adopted or already-orphaned pid
    it raises immediately, which is fine to ignore -- something else (usually
    init) owns reaping those.
    """
    deadline = time.time() + timeout
    escalated = False
    while True:
        try:
            done, _ = os.waitpid(pid, os.WNOHANG)
        except OSError:
            return
        if done == pid:
            return
        if time.time() >= deadline:
            return
        if not escalated and time.time() >= deadline - timeout / 2.0:
            _signal_group(pid, signal.SIGKILL)
            escalated = True
        time.sleep(0.05)


def stop(runtime_dir: str) -> None:
    """Kill this runtime dir's sidecar, if it has one. Never raises."""
    path = os.path.join(runtime_dir, "sidecar.pid")
    pid = util.read_int(path, 0)
    if pid > 0:
        _signal_group(pid, signal.SIGTERM)
        _reap(pid)
    try:
        os.unlink(path)
    except OSError:
        pass


class Supervisor(object):
    """Restarts a dead sidecar, up to a ceiling, then reports a crash loop.

    `restarts` counts consecutive failures. It resets to zero whenever the
    most recently spawned instance ran for at least `stable_after` seconds
    before dying -- otherwise a dashboard that is perfectly healthy for hours
    and then dies once, long after any earlier trouble, would be treated as
    the next strike in a crash loop from days ago and reported (or silenced)
    on evidence that no longer means anything. `stable_after` defaults to a
    healthy multiple of `interval` so normal restart-detection latency never
    reads as "it was stable."
    """

    def __init__(self, runtime_dir: str, plugin_root: str, loop_dir: str,
                 on_crashloop: Callable[[int], None], interval: float = 10.0,
                 max_restarts: int = 5, cmd_override: Optional[str] = None,
                 stable_after: Optional[float] = None):
        self.runtime_dir = runtime_dir
        self.plugin_root = plugin_root
        self.loop_dir = loop_dir
        self.on_crashloop = on_crashloop
        self.interval = interval
        self.max_restarts = max_restarts
        self.cmd_override = cmd_override
        self.stable_after = (stable_after if stable_after is not None
                             else max(60.0, interval * 6))
        self.restarts = 0
        self._last_spawn = None  # type: Optional[float]
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="sidecar-supervisor")
        self.thread.daemon = True

    def _tick(self) -> bool:
        """One supervision check. Returns True when the thread should stop."""
        pid = util.read_int(os.path.join(self.runtime_dir, "sidecar.pid"), 0)
        if pid > 0:
            # A pid this process itself forked (the normal case) stays
            # "alive" to `os.kill(pid, 0)` as a zombie until reaped, however
            # it died -- naturally, or killed out-of-band. Collect it first
            # (non-blocking; a no-op when it's still running, or isn't ours)
            # so the liveness check below sees a genuinely dead sidecar as
            # dead rather than as an unreaped corpse.
            try:
                os.waitpid(pid, os.WNOHANG)
            except OSError:
                pass
            if util.process_alive(pid):
                return False
        now = time.monotonic()
        if self._last_spawn is not None and (now - self._last_spawn) >= self.stable_after:
            self.restarts = 0
        self.restarts += 1
        if self.restarts > self.max_restarts:
            try:
                self.on_crashloop(self.restarts)
            except Exception:
                pass
            return True
        self._last_spawn = now
        start(self.plugin_root, self.loop_dir, self.runtime_dir,
              cmd_override=self.cmd_override, wait_s=2)
        return False

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                if self._tick():
                    return
            except Exception:
                # A transient failure here (a stat racing a rename, `ps`
                # hiccuping) must not silently end the thread: the harness
                # would keep believing a dashboard is supervised when nothing
                # is watching it any more -- the same class of bug fixed in
                # harness.py's Heartbeat thread. Skip this beat, try again.
                pass

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=5)
        stop(self.runtime_dir)
