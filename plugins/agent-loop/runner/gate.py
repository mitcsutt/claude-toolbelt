"""The parent-side verification gate: the harness runs the commands, not a model.

Never trust a Worker's self-reported pass. These commands come from the
contract's `verification` array (which the Scout also seeds with any invariant
`check` from the learnings), and a non-zero exit is a gate failure exactly like
a failing test. Each command gets its own wall-clock budget and its own output
file, so a learning may cite `gate-<T>-<n>.txt` as evidence.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import List


@dataclass
class CommandResult:
    cmd: str
    rc: int
    duration_s: int
    output_path: str
    timed_out: bool


def _kill_group(proc: subprocess.Popen) -> None:
    """SIGTERM the command's own process group, then SIGKILL what survives.

    `start_new_session=True` put the shell in its own group, so a build tool
    that forked workers goes down with it instead of being orphaned holding RAM.
    """
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        return
    for sig, grace in ((signal.SIGTERM, 5), (signal.SIGKILL, 5)):
        try:
            os.killpg(pgid, sig)
        except OSError:
            return
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


def run_commands(cmds: List[str], cwd: str, timeout_s: int, out_dir: str,
                 tag: str) -> List[CommandResult]:
    """Run each command under its own timeout; stop at the first failure."""
    results: List[CommandResult] = []
    if not cmds:
        return results
    os.makedirs(out_dir, exist_ok=True)
    for n, cmd in enumerate(cmds, 1):
        path = os.path.join(out_dir, "gate-%s-%d.txt" % (tag, n))
        started = time.time()
        timed_out = False
        with open(path, "w") as out:
            out.write("$ %s\n" % cmd)
            out.flush()
            proc = subprocess.Popen(cmd, shell=True, cwd=cwd, stdout=out,
                                    stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL,
                                    start_new_session=True)
            try:
                rc = proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_group(proc)
                rc = 124
                out.write("\n[timed out after %ds]\n" % timeout_s)
        results.append(CommandResult(cmd=cmd, rc=rc,
                                     duration_s=int(time.time() - started),
                                     output_path=path, timed_out=timed_out))
        if rc != 0:
            break
    return results


def all_ok(results: List[CommandResult]) -> bool:
    return all(r.rc == 0 for r in results)


def outputs_text(results: List[CommandResult], limit: int = 2000) -> str:
    """A bounded rendering of the gate for a prompt: command, rc, output tail."""
    if not results:
        return "(none)"
    chunks = []
    for r in results:
        try:
            with open(r.output_path) as f:
                body = f.read()
        except OSError:
            body = ""
        if len(body) > limit:
            body = "[... truncated, full output in %s ...]\n%s" % (
                r.output_path, body[-limit:])
        chunks.append("$ %s\nrc=%d (%ds)\n%s" % (r.cmd, r.rc, r.duration_s, body.rstrip()))
    return "\n\n".join(chunks)
