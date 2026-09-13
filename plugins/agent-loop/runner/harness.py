"""Exclusivity, liveness, the rotating log, and the two guards that decide
whether the harness may start a tick at all.

`runtime/harness.json` IS the lock. It is created with `link(2)` from a fully
written private file, so the lock can never exist without its owner payload —
there is no instant at which a racer can read a stale pid out of a fresh lock.
A dead owner is cleared by rename, which exactly one racer can win.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from . import util

LOCK_NAME = "harness.json"


def harness_alive(pid: Any) -> bool:
    """Live pid whose command line contains `run.sh` — the only liveness test.

    The shim passes its own path to python as `--shim …/run.sh`, so the token is
    on the command line of the exec'd process too.
    """
    return util.process_alive(pid, "run.sh")


def _lock_payload(pid: int, loop_dir: str, version: str) -> Dict[str, Any]:
    try:
        host = socket.gethostname()
    except OSError:
        host = "unknown"
    return {"pid": int(pid), "start_epoch": int(time.time()), "host": host,
            "loop_dir": loop_dir, "plugin_version": version}


def lock_acquire(runtime_dir: str, pid: int, loop_dir: str, version: str,
                 alive: Optional[Callable[[Any], bool]] = None,
                 sleep: Callable[[float], None] = time.sleep) -> str:
    """`acquired` | `takeover:<oldpid>` | `held:<pid>:<start_epoch>`."""
    if alive is None:
        alive = harness_alive
    os.makedirs(runtime_dir, exist_ok=True)
    lock = os.path.join(runtime_dir, LOCK_NAME)
    tmp = os.path.join(runtime_dir, ".harness.%d.tmp" % pid)
    util.write_json(tmp, _lock_payload(pid, loop_dir, version))
    old_pid = ""
    cleared = 0
    cur_pid = "?"
    cur_start = 0
    try:
        for _ in range(10):
            try:
                os.link(tmp, lock)
            except OSError:
                rec = util.read_json(lock) or {}
                cur_pid = rec.get("pid", "")
                cur_start = rec.get("start_epoch", 0) or 0
                if cur_pid and alive(cur_pid):
                    return "held:%s:%s" % (cur_pid, cur_start)
                # Dead or unreadable owner. Clear by rename: exactly one racer
                # wins; the others see ENOENT, pause, and retry the link.
                if cur_pid:
                    old_pid = str(cur_pid)
                stale = os.path.join(runtime_dir, ".harness.stale.%d" % pid)
                try:
                    os.rename(lock, stale)
                    os.unlink(stale)
                    cleared += 1
                except OSError:
                    sleep(0.1)
                continue
            # Settle, then confirm: a racer that read the dead pid a microsecond
            # before our link could have renamed our fresh lock away.
            sleep(0.2)
            rec = util.read_json(lock) or {}
            if rec.get("pid") != pid:
                return "held:%s:%s" % (rec.get("pid", "?"), rec.get("start_epoch", 0))
            return ("takeover:%s" % (old_pid or "none")) if cleared else "acquired"
        return "held:%s:%s" % (cur_pid, cur_start)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def lock_release(runtime_dir: str, pid: int) -> None:
    """Remove the lock, but only when this pid still owns it. Never raises."""
    lock = os.path.join(runtime_dir, LOCK_NAME)
    rec = util.read_json(lock) or {}
    owner = rec.get("pid")
    if owner is not None and owner != pid:
        return
    try:
        os.unlink(lock)
    except OSError:
        pass


class Heartbeat(object):
    """A daemon thread stamping runtime/HEARTBEAT.

    Observers treat the harness as alive only when the pid is live AND this file
    is younger than 3x the interval, so a wedged-but-not-exited harness is still
    detectable. As a thread it cannot outlive the process it reports on, which
    is what the v2 background job needed an explicit parent check for.
    """

    def __init__(self, runtime_dir: str, interval: float = 10.0):
        self.runtime_dir = runtime_dir
        self.interval = interval
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="heartbeat")
        self.thread.daemon = True

    def _run(self) -> None:
        while True:
            try:
                os.makedirs(self.runtime_dir, exist_ok=True)
                util.stamp_epoch(os.path.join(self.runtime_dir, "HEARTBEAT"))
            except Exception:
                # A transient write failure (disk full, runtime dir removed or
                # made unwritable underneath us) must skip this beat, not kill
                # the thread: a dead heartbeat reads as a dead harness to every
                # liveness consumer, which is the opposite of this class's job.
                pass
            if self._stop.wait(self.interval):
                return

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def write_tick_json(runtime_dir: str, tick: Any, pid: Any, started_at: Any,
                    timeout_s: Any) -> None:
    """The in-flight tick's liveness record; its presence means a tick is running."""
    util.write_json(os.path.join(runtime_dir, "tick.json"),
                    {"tick": _int_or_zero(tick), "pid": _int_or_zero(pid),
                     "started_at": _int_or_zero(started_at),
                     "timeout_s": _int_or_zero(timeout_s)})


def clear_tick_json(runtime_dir: str) -> None:
    try:
        os.unlink(os.path.join(runtime_dir, "tick.json"))
    except OSError:
        pass


class RotatingLog(object):
    """harness.log: the harness's own narration, bounded at max_bytes x backups.

    v2's run.log also carried every raw stream line and reached 105 MB in one
    run. The per-phase transcripts live in ~/.claude/projects/ and are referenced
    by path from `phase_end`, so nothing needs to be duplicated here.
    """

    def __init__(self, path: str, max_bytes: int = 10 * 1024 * 1024, backups: int = 3):
        self.path = path
        self.max_bytes = max_bytes
        self.backups = backups
        self._lock = threading.Lock()
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _rotate(self) -> None:
        for i in range(self.backups, 0, -1):
            src = self.path if i == 1 else "%s.%d" % (self.path, i - 1)
            dst = "%s.%d" % (self.path, i)
            if os.path.exists(src):
                try:
                    os.replace(src, dst)
                except OSError:
                    return
        oldest = "%s.%d" % (self.path, self.backups + 1)
        try:
            os.unlink(oldest)
        except OSError:
            pass

    def write(self, line: str) -> None:
        with self._lock:
            try:
                if os.path.exists(self.path) and os.path.getsize(self.path) >= self.max_bytes:
                    self._rotate()
                with open(self.path, "a") as f:
                    f.write(line.rstrip("\n") + "\n")
            except OSError:
                return


def _darwin_headroom() -> Tuple[int, int]:
    free_mb = 0
    swap_pct = 0
    try:
        out = subprocess.run(["vm_stat"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=5).stdout.decode()
        page = 4096
        pages = 0
        for line in out.splitlines():
            if "page size of" in line:
                for tok in line.split():
                    if tok.isdigit():
                        page = int(tok)
                        break
            for label in ("Pages free:", "Pages inactive:", "Pages speculative:"):
                if line.startswith(label):
                    pages += int(line.split()[-1].rstrip("."))
        free_mb = (pages * page) // (1024 * 1024)
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        pass
    try:
        out = subprocess.run(["sysctl", "-n", "vm.swapusage"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=5).stdout.decode()
        parts = out.replace("M", " ").split()
        total = used = 0.0
        for i, tok in enumerate(parts):
            if tok == "total" and i + 2 < len(parts):
                total = float(parts[i + 2])
            if tok == "used" and i + 2 < len(parts):
                used = float(parts[i + 2])
        if total > 0:
            swap_pct = int((used * 100) / total)
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        pass
    return free_mb, swap_pct


def _linux_headroom() -> Tuple[int, int]:
    free_mb = 0
    swap_total = swap_free = 0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    free_mb = int(line.split()[1]) // 1024
                elif line.startswith("SwapTotal:"):
                    swap_total = int(line.split()[1])
                elif line.startswith("SwapFree:"):
                    swap_free = int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return 0, 0
    swap_pct = int(((swap_total - swap_free) * 100) / swap_total) if swap_total > 0 else 0
    return free_mb, swap_pct


def mem_headroom() -> Tuple[int, int]:
    """(free_mb, swap_used_pct); (0, 0) on an unknown platform."""
    try:
        system = os.uname().sysname
    except AttributeError:
        return 0, 0
    if system == "Darwin":
        return _darwin_headroom()
    if system == "Linux":
        return _linux_headroom()
    return 0, 0


def mem_guard_action(free_mb: int, swap_pct: int, min_mb: int, max_swap_pct: int) -> str:
    """`delay` only when BOTH conditions hold.

    A low free figure alone is normal on a healthy box (the OS caches
    aggressively); it only matters once the machine is also paging. `>=` on the
    ceiling so an explicit ceiling of 0 always trips.
    """
    if free_mb < min_mb and swap_pct >= max_swap_pct:
        return "delay"
    return "proceed"


def ratelimit_action(info: Optional[Dict[str, Any]], now: int, max_wait: int) -> str:
    """`ok` | `wait <seconds>` | `exit` from a rate_limit_info payload.

    Any `allowed*` status (including `allowed_warning`, emitted near the
    threshold but still serving) is ok; only a genuinely throttled status waits.
    """
    if not info or not isinstance(info, dict):
        return "ok"
    status = info.get("status", "allowed")
    if isinstance(status, str) and status.startswith("allowed"):
        return "ok"
    resets = info.get("resetsAt")
    try:
        resets = int(resets)
    except (TypeError, ValueError):
        return "exit"
    wait = resets - int(now) + 60
    if wait < 5:
        wait = 5
    if wait > max_wait:
        return "exit"
    return "wait %d" % wait


def backoff_delay(attempt: int) -> int:
    """2^(attempt+1) seconds, at least 1, capped at 300."""
    try:
        d = 2 ** (int(attempt) + 1)
    except (TypeError, ValueError):
        d = 2
    if d < 1:
        d = 1
    return min(int(d), 300)
