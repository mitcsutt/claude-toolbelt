"""The event substrate: one compact JSON object per line, and the usage ledger.

`events.jsonl` is what the dashboard tails and what the medic and postmortem
read back. The envelope is fixed — `t`, `seq`, `type`, then the payload — and the
lifecycle types `serve.py` understands keep their exact field names. `seq` is
persisted under `runtime/` so it stays monotonic across resume, which is what
lets a consumer tell a gap from a restart.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict

from . import util

_USAGE_FIELDS = ("cost_usd", "input_tokens", "output_tokens",
                 "cache_read_tokens", "cache_creation_tokens")


def _usage_dict(u: Any) -> Dict[str, Any]:
    """Normalise a Usage dataclass or a plain dict to the five ledger fields."""
    out = {}
    for name in _USAGE_FIELDS:
        if isinstance(u, dict):
            val = u.get(name, 0)
        else:
            val = getattr(u, name, 0)
        out[name] = val
    return out


class EventLog(object):
    """Append-only writer for events.jsonl. One instance per harness process."""

    def __init__(self, path: str, seq_path: str):
        self.path = path
        self.seq_path = seq_path
        self.seq = util.read_int(seq_path, 0) if seq_path else 0
        self._lock = threading.Lock()
        if self.path:
            parent = os.path.dirname(os.path.abspath(self.path))
            if parent:
                os.makedirs(parent, exist_ok=True)

    def emit(self, type: str, **fields: Any) -> None:
        """Append `{"t":…,"seq":…,"type":…, **fields}` as one line.

        An empty path is a silent no-op so a headless invocation that wants no
        events never errors. Thread-safe: the heartbeat and the sidecar
        supervisor both emit.
        """
        if not self.path:
            return
        with self._lock:
            self.seq += 1
            rec = {"t": int(time.time()), "seq": self.seq, "type": type}
            rec.update(fields)
            line = json.dumps(rec, separators=(",", ":"))
            try:
                with open(self.path, "a") as f:
                    f.write(line + "\n")
                if self.seq_path:
                    util.atomic_write(self.seq_path, str(self.seq))
            except OSError:
                return


class UsageLog(object):
    """Append-only writer for LOOP_USAGE.jsonl (postmortem + dashboard input).

    cost_usd is raw and informational: it is never summed into a budget, never
    projected, and never a control input.
    """

    def __init__(self, path: str):
        self.path = path

    def write_tick(self, tick: int, mode: str, duration_s: int,
                   by_model: Dict[str, Any]) -> None:
        models = dict((k, _usage_dict(v)) for k, v in (by_model or {}).items())
        rec = {
            "tick": tick,
            "mode": mode,
            "cost_usd": sum(m["cost_usd"] for m in models.values()),
            "input_tokens": sum(m["input_tokens"] for m in models.values()),
            "output_tokens": sum(m["output_tokens"] for m in models.values()),
            "duration_s": duration_s,
            "by_model": models,
        }
        try:
            with open(self.path, "a") as f:
                f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        except OSError:
            return
