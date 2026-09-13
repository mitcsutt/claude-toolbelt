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


def _marker_for(argv: List[str]) -> str:
    """The substring that must appear in `ps -o command=` for `argv` once run.

    `LOOP_DASHBOARD_CMD` (and `cmd_override` generally) means the spawned
    command is not always `web/serve.py` -- the e2e suite points it at
    `tests/fixtures/dashboard-stub`, and unit tests use bare commands like
    `true` or `sleep 5`. Hardcoding "serve.py" would make the identity check
    vacuous in exactly the configuration Task 21 exercises, so the marker is
    derived from what was actually about to be launched: the script path for
    the real `[python, serve.py, ...]` shape (matching `adoptable()`'s own
    convention), else the basename of the program itself.
    """
    for arg in argv:
        if arg.endswith("serve.py"):
            return "serve.py"
    return os.path.basename(argv[0]) if argv else ""


def start(plugin_root: str, loop_dir: str, runtime_dir: str,
          cmd_override: Optional[str] = None, wait_s: float = 5.0) -> Optional[str]:
    """Spawn the observer and return the URL it announces, or None in time.

    Kills any sidecar this runtime dir already has recorded first. That record
    can outlive an unclean harness exit (a crash skips `stop()`), and without
    this a resumed harness would leave that orphan running while it spawns a
    second one -- exactly the two-dashboards-fighting-over-one-port failure
    this module exists to prevent. `stop()` only ever signals a pid whose
    identity it can verify, so this is safe even when the recorded pid has
    since been reassigned to an unrelated process: that process is left alone,
    and a fresh sidecar is spawned regardless.
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
    # The pid and the marker that proves it's still this exact process are
    # recorded together, at spawn time, from the argv actually launched --
    # `stop()` re-derives nothing and never guesses.
    util.atomic_write(os.path.join(runtime_dir, "sidecar.pid"), str(proc.pid))
    util.atomic_write(os.path.join(runtime_dir, "sidecar.marker"), _marker_for(argv))

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
    """Kill this runtime dir's sidecar, if it has one, and only if it still is
    what it claims to be. Never raises.

    The pid in `sidecar.pid` is read from disk, not held live in memory, so it
    can outlive its process across a harness crash and restart -- exactly the
    TOCTOU window in which the OS is free to hand that same number to an
    unrelated process. Signalling on the bare pid, the way `_signal_group`'s
    caller used to, would then kill a stranger. So this checks the recorded
    pid against the marker recorded alongside it at spawn time (`start()`,
    `_marker_for`) via `util.process_alive` -- the plugin's one liveness
    primitive, not a second hand-rolled rule -- and fails closed: no marker,
    or a marker that doesn't match the live process's command line, means the
    pid is never signalled. Either way the stale bookkeeping is dropped, so a
    reused or unverifiable pid is simply abandoned rather than adopted or
    killed.
    """
    pid_path = os.path.join(runtime_dir, "sidecar.pid")
    marker_path = os.path.join(runtime_dir, "sidecar.marker")
    pid = util.read_int(pid_path, 0)
    marker = util.read_text(marker_path).strip()
    if pid > 0 and marker and util.process_alive(pid, marker):
        _signal_group(pid, signal.SIGTERM)
        _reap(pid)
    for path in (pid_path, marker_path):
        try:
            os.unlink(path)
        except OSError:
            pass


class Supervisor(object):
    """Restarts a dead sidecar, up to a ceiling, then reports a crash loop.

    Two independent ceilings, because either alone is gameable:

    - `restarts` counts CONSECUTIVE failures and resets to zero whenever the
      most recently spawned instance ran for at least `stable_after` seconds
      before dying -- otherwise a dashboard that is perfectly healthy for
      hours and then dies once, long after any earlier trouble, would be
      treated as the next strike in a crash loop from days ago.
    - that same reset is exactly what a sidecar dying on a period just over
      `stable_after`, forever, would exploit: `restarts` would pin at 1
      forever and `on_crashloop` would never fire, while the harness spins a
      fresh process every `interval` all night with no visibility. So a
      SECOND counter -- restarts within a fixed trailing window -- cannot be
      reset by the same trick, because it doesn't care whether any individual
      cycle looked "stable"; it only cares how many restarts happened
      recently. Defaults: `window_s=3600.0`, `max_in_window=10` -- a healthy
      sidecar essentially never restarts, so more than ten times in an hour,
      by any pattern, is itself the signal, independent of the consecutive
      count.

    `stable_after` defaults to a healthy multiple of `interval` so normal
    restart-detection latency never reads as "it was stable."
    """

    def __init__(self, runtime_dir: str, plugin_root: str, loop_dir: str,
                 on_crashloop: Callable[[int], None], interval: float = 10.0,
                 max_restarts: int = 5, cmd_override: Optional[str] = None,
                 stable_after: Optional[float] = None,
                 window_s: float = 3600.0, max_in_window: int = 10):
        self.runtime_dir = runtime_dir
        self.plugin_root = plugin_root
        self.loop_dir = loop_dir
        self.on_crashloop = on_crashloop
        self.interval = interval
        self.max_restarts = max_restarts
        self.cmd_override = cmd_override
        self.stable_after = (stable_after if stable_after is not None
                             else max(60.0, interval * 6))
        self.window_s = window_s
        self.max_in_window = max_in_window
        self.restarts = 0
        self._last_spawn = None  # type: Optional[float]
        self._restart_times = []  # type: List[float]
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

        self._restart_times.append(now)
        cutoff = now - self.window_s
        self._restart_times = [t for t in self._restart_times if t >= cutoff]
        windowed_count = len(self._restart_times)

        crashlooping = self.restarts > self.max_restarts
        windowed = windowed_count > self.max_in_window
        if crashlooping or windowed:
            try:
                self.on_crashloop(self.restarts if crashlooping else windowed_count)
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
