"""Harness-side visual verification: render gate, screenshot archival, fidelity.

Nothing in this module calls a model. The harness runs the commands, keeps the
images, and computes the ratios; the Evaluator is handed the results (spec 5.2).
"""
from __future__ import annotations

import os
import shutil
from typing import Dict, List, Tuple

from . import gate as gate_mod
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
