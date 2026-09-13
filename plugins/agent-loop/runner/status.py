"""LOOP_STATUS.md — the glanceable, human-facing view of the run.

Pure string builders plus one writer. Every format here is byte-identical to
v2's lib/loop.sh: a human reads this file over someone's shoulder, /agent-loop
quotes it, and the e2e greps it.
"""
from __future__ import annotations

from typing import List, Optional

from . import util

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧"

_CAUSE_TEXT = {
    "ok": "ok",
    "timeout": "timeout (phase exceeded its wall-clock budget)",
    "killed": "killed (SIGKILL — likely OS memory pressure)",
    "terminated": "terminated (SIGTERM)",
    "api_error": "api error",
    "rate_limit": "rate limited (429)",
    "malformed-output": "the phase returned no usable JSON",
    "crashed": "crashed (no result payload)",
}

_GLYPHS = {"done": "✓", "continue": "✓", "plan": "✦", "review": "◆",
           "skip": "⏭", "retry": "↻"}


def pct(done: int, total: int) -> int:
    return (done * 100 + total // 2) // total if total > 0 else 0


def fmt_dur(secs: int) -> str:
    s = int(secs or 0)
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm%02ds" % (s // 60, s % 60)
    return "%dh%02dm" % (s // 3600, (s % 3600) // 60)


def progress_bar(done: int, total: int, width: int) -> str:
    filled = (done * width // total) if total > 0 else 0
    filled = max(0, min(filled, width))
    return "█" * filled + "░" * (width - filled)


def gates_compact(verification_line: str) -> str:
    """`Loop-Verification: lint=pass test=fail` -> `lint✓ test✗`."""
    line = verification_line or ""
    if "Loop-Verification:" in line:
        line = line.split("Loop-Verification:", 1)[1]
    out = []
    for tok in line.split():
        if "=" not in tok:
            continue
        key, val = tok.split("=", 1)
        out.append(key + ("✓" if val == "pass" else "✗"))
    return " ".join(out)


def cause_human(cause: str) -> str:
    return _CAUSE_TEXT.get(cause or "", cause or "")


def verdict_glyph(verdict: str) -> str:
    return _GLYPHS.get(verdict or "", "✗")


def tick_line(verdict: str, tick: int, task: str, dur: int, gates: str, sha: str,
              done: int, total: int, cause: str = "") -> str:
    """One permanent per-tick summary line."""
    mid = ("%s · → %s" % (gates, sha)) if (sha and gates) else \
          ("→ %s" % sha if sha else "(no commit)")
    why = ""
    if cause and cause != "ok":
        why = " · %s" % cause_human(cause)
    return "%s t%s %s · %s · %s   %d%% (%d/%d)%s" % (
        verdict_glyph(verdict), tick, task or "?", fmt_dur(dur), mid,
        pct(done, total), done, total, why)


def session_header(done: int, total: int, elapsed: int, eta: int, plan_usage: str,
                   seg_done: int = 0, seg_total: int = 0) -> str:
    """The one-line run header.

    While any segment is still unplanned the percent switches to the
    segment-weighted estimate: "48/48 tasks" is not progress when nine segments
    have yet to be written.
    """
    quota = (" · %s" % plan_usage) if plan_usage else ""
    if seg_total > 0 and seg_done < seg_total:
        return ("── loop · %d%% %s seg %d/%d · %d/%d planned tasks · ⏱ %s · ~%s left%s ──"
                % (pct(seg_done, seg_total), progress_bar(seg_done, seg_total, 12),
                   seg_done, seg_total, done, total, fmt_dur(elapsed), fmt_dur(eta), quota))
    return ("── loop · %d%% %s %d/%d · ⏱ %s · ~%s left%s ──"
            % (pct(done, total), progress_bar(done, total, 12), done, total,
               fmt_dur(elapsed), fmt_dur(eta), quota))


def write_status(path: str, header: str, lines: List[str], tail: int = 10,
                 banner: Optional[str] = None) -> None:
    """Header, an optional banner, then the last `tail` per-tick lines."""
    body = list(lines)[-tail:] if tail > 0 else list(lines)
    parts = [header, ""]
    if banner:
        parts.extend([banner, ""])
    parts.extend(body)
    util.atomic_write(path, "\n".join(parts) + "\n")
