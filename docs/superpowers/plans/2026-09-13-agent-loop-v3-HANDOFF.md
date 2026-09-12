# agent-loop v3 — implementation handoff

Written 2026-09-13 by the Fable design session for the Opus implementation session. Read this first; it tells you what exists, in what order to execute, and what not to re-decide.

## 1. State of the work

| Artefact | Path | Status |
|---|---|---|
| Design spec | `docs/superpowers/specs/2026-09-13-agent-loop-v3-phase-runner-design.md` | committed on branch `agent-loop-v3` (5 commits); §11 lists every decision and who made it |
| Interface contract | `docs/superpowers/plans/2026-09-13-agent-loop-v3-00-interfaces.md` | authoritative for every name/signature/path; its "Cross-plan notes" override any plan text that disagrees |
| Plan A runner core | `docs/superpowers/plans/2026-09-13-agent-loop-v3-A-runner-core.md` | ~20 tasks, reconciled to the merged spec |
| Plan B verification gates | `…-v3-B-verification.md` | 8 tasks |
| Plan C judgement / resume / split | `…-v3-C-judgement.md` | 8 tasks, reconciled |
| Plan D dashboard / skills / docs | `…-v3-D-surface.md` | 11 tasks |
| Evidence | `~/Downloads/EXTRACT/` (`AGENTS.md`, `07-index/q`, `08-analysis/00-SYNTHESIS.md`) | read-only reference; never `cat` a transcript, query with `q` |
| Parallel session's handoff | `docs/superpowers/plans/2026-09-13-agent-loop-v3-alt-handoff.md` | already merged into spec §11 items 12–18, §14, §15; historical |

The current loop on the other machine is PAUSED and will resume only on v3. There is no 2.1.1 patch; the stall-race fix lands as part of v3 (`claude_proc.py` stamps activity atomically and never falls back to tick start).

## 2. Execution order and skills

1. Plan A, then plan C (C replaces A's stand-in failure path), then plan B, then plan D. B and C are independent of each other but both edit `run.py`'s execute path; do them serially, C first, because the Judge is what B's failures route to.
2. Use `superpowers:subagent-driven-development` per plan: one fresh subagent per task, TDD as written, review between tasks. Every task in the plans already has the failing test, the implementation, and the commit.
3. Work on branch `agent-loop-v3` in a worktree (`superpowers:using-git-worktrees`). Never push to `main`.
4. `bash scripts/test-all.sh` must be green at the end of every plan; paste its tail in the completion message. `scripts/lint.sh` exit 2 means shellcheck is missing, which is a SKIP, not a pass.
5. When all four plans are done: bump `plugin.json` to 3.0.0 (plan D does it), walk `CLAUDE.md` "Changing a plugin", update the plugin README "Upgrading" section, then run a real loop on a throwaway repo before the loop machine pulls it.

## 3. Model routing for the implementing session

- **Opus subagents** for: `claude_proc.py` (stream parsing, kill/wrap-up, `--resume`), `run.py` main loop and boot reconciliation, `judge.py`, `task_state.py`, the e2e test rewrite, and any task whose test fails twice.
- **Sonnet subagents** for: `config.py`, `plan.py`, `contract.py`, `gate.py`, `git_ops.py`, `events.py`, `render.py`, `sidecar.py`, `migrate.py`, `calibrate.py`, prompts, skills/README/template edits, dashboard edits.
- **Fable** for design questions only. Two ways to reach it: (a) the original Fable session may still be open on this machine, so `ListAgents` then `SendMessage` to it with the question and the spec section; (b) dispatch an `Agent` with `model: "fable"` and give it the spec, the interfaces doc, and the exact contradiction. Do not change an interface or a §11 decision on Opus's own judgement; ask.

## 4. Rules that are easy to forget

- Every runtime file write is atomic (`.tmp` + `os.replace`). No exceptions.
- No model name in plugin code; only the `Tiers:` config line and the template default name models.
- Python 3.9: `Optional[X]`, no `match`, no `X | Y` at runtime.
- bash 3.2 in `run.sh`, the stub `claude`, and every `.sh` test; commit bodies via `git commit -F - <<'EOF'`.
- `run.sh` must keep the string `run.sh` in the harness argv (`--shim`), or the dashboard and skills will declare a live harness dead.
- Sub-task ids from a split are numeric (`T74`), never `T60a`.
- `artifacts/` must be in the per-run `.gitignore` or SANDBOX deletes screenshots.
- The Evaluator is synchronous and stays; async evaluation is deferred (spec §15) and must not be started.
- Re-attempt tier is the Judge's decision; the harness enforces bounds only (`max_attempts`, one cap extension, two failed Judge decisions → defer/halt).
- `LOOP_SCHEMA = 3`; migration must leave a `[~]` task resumable (spec §14).

## 5. What to escalate to Mitch (not to Fable)

- Anything that would change the dashboard's status derivation or the plan glyphs.
- Any new dependency beyond python3 stdlib and git.
- A render recipe design that cannot be expressed as start/ready/command/ui_globs/reference.
- Cap defaults if `calibrate.py` on the EXTRACT events disagrees with spec §4.2 by more than 2×.

## 6. Definition of done for the whole effort

- All four plans executed, `bash scripts/test-all.sh` PASS pasted.
- A throwaway loop (small repo, two or three tasks, one deliberately timed-out Worker, one UI task with a render recipe) runs end to end: resume works, screenshot lands in `artifacts/`, Judge writes `LOOP_DECISIONS.md`, dashboard shows Stop-now and thumbnails.
- The paused loop's dir migrates 2→3 in place and resumes its `[~]` task.
- Postmortem compares $/task, wall/task, human touches, needs-human count against `EXTRACT/08-analysis/cost-effort.md`.
