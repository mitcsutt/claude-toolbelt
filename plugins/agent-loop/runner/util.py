"""Primitives every runner module needs: atomic writes, JSON/text I/O, pid liveness.

This module imports nothing from `runner`, so every other module may import it
without risking a cycle.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any, Optional


def atomic_write(path: str, text: str) -> None:
    """Write `text` to `path` via `<path>.tmp` + `os.replace`.

    The runtime-file contract: an observer (the dashboard, the medic, a second
    harness) must never read a half-written file, so the rename is the only
    moment the new content becomes visible.
    """
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def write_json(path: str, obj: Any) -> None:
    """One compact JSON document plus a trailing newline, written atomically."""
    atomic_write(path, json.dumps(obj, separators=(",", ":")) + "\n")


def read_json(path: str) -> Optional[Any]:
    """Parsed JSON, or None when the file is missing, unreadable or malformed."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def read_text(path: str, default: str = "") -> str:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return default


def read_int(path: str, default: int = 0) -> int:
    """First whitespace-delimited number in a one-line file, floored to an int."""
    txt = read_text(path).strip()
    if not txt:
        return default
    try:
        return int(float(txt.split()[0]))
    except (ValueError, IndexError):
        return 0


def stamp_epoch(path: str, now: Optional[float] = None) -> None:
    atomic_write(path, "%d\n" % int(time.time() if now is None else now))


def process_alive(pid: Any, must_contain: Optional[str] = None) -> bool:
    """True when `pid` exists and, when given, its command line contains `must_contain`.

    The command check defeats PID reuse: a recycled pid belonging to some other
    program must not read as a live harness. Deliberately not `pgrep` and not
    `ps | grep` — both match the observing agent's own tool call.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass                       # alive, just owned by another user
    except OSError:
        return False
    if not must_contain:
        return True
    try:
        proc = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return must_contain in proc.stdout.decode("utf-8", "replace")
