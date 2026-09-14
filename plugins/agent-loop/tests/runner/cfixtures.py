"""On-disk loop fixture shared by the plan-C runner tests."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(os.path.dirname(HERE))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from runner import phases  # noqa: E402
from runner.config import LoopConfig  # noqa: E402
from runner.contract import Contract, Forbidden, save_contract  # noqa: E402
from runner.events import EventLog  # noqa: E402
from runner.plan import Plan  # noqa: E402
from runner.task_state import TaskState  # noqa: E402

LOOP_PLAN_TEXT = """# Loop Plan

## Segment 1 - API
- [~] T60 Add widgets REST mixin (P-WID-001)
- [ ] T61 Wire widgets into the customers page
- [ ] T62 Cypress coverage for widgets
"""

DESIGN_PLAN_TEXT = """# Rebuild plan

- T60 widgets REST: register the mixin in the composition root (P-WID-001).
- T61 depends on T60 landing first.
"""

SPEC_TEXT = """# Design

REST via AcmeApi internal mixins; ~12 mixins.
T60 widgets is REST, composed as a dedicated internal mixin set.
P-WID-001 is resolved by registering the mixin in the composition root.
"""


def make_loop(tmp, policy="autonomous", blocker_policy="continue-independent"):
    """Build a loop dir inside a worktree. Returns (cfg, plan, loop_dir, runtime_dir)."""
    worktree = os.path.join(tmp, "wt")
    loop_dir = os.path.join(worktree, ".claude", "loop", "run-1")
    runtime = os.path.join(loop_dir, "runtime")
    os.makedirs(runtime)
    os.makedirs(os.path.join(worktree, "docs"))
    loop_plan_path = os.path.join(loop_dir, "LOOP_PLAN.md")
    _write(loop_plan_path, LOOP_PLAN_TEXT)
    _write(os.path.join(loop_dir, "LOOP_CLEANUP.md"), "# Cleanup\n")
    design_plan = os.path.join(worktree, "docs", "plan.md")
    _write(design_plan, DESIGN_PLAN_TEXT)
    spec = os.path.join(worktree, "docs", "spec.md")
    _write(spec, SPEC_TEXT)
    cfg = LoopConfig(
        worktree=worktree, branch="agent-loop-test", goal="test loop",
        granularity="segmented", tdd_mode="none", verification=["lint"],
        blocker_policy=blocker_policy, dashboard="off", medic="off", medic_model="",
        tiers={"cheap": "haiku", "standard": "sonnet", "most-capable": "opus"},
        role_tiers={"planner": "most-capable", "scout": "standard",
                    "worker": "standard", "evaluator": ""},
        limits={"tick_timeout": 1800, "scout_timeout": 480,
                "worker_timeout": 1500, "wrapup_timeout": 300,
                "eval_timeout": 480, "judge_timeout": 360,
                "planner_timeout": 900, "gate_cmd_timeout": 600,
                "worker_resume_max": 1, "worker_budget_usd": 6,
                "max_attempts": 3},
        decision_policy=policy, render={}, spec_path=spec, plan_path=design_plan,
    )
    return cfg, Plan.load(loop_plan_path), loop_dir, runtime


def make_contract(task="T60"):
    return Contract(
        task=task,
        success_criteria=[
            "packages/api/src/requests/acme/internal/widgets.ts exists"],
        allow_list=["packages/api/src/requests/acme/internal/**"],
        forbidden=[
            Forbidden(path="apps/frontend/**", source="plan"),
            Forbidden(path="packages/api/src/requests/acme/index.ts", source="scout"),
        ],
        verification=["pnpm turbo run lint --filter=@repo/api"],
        render_gate=None, fidelity_source=[], evaluator_must_read=[],
        evaluator_must_view=[], estimated_diff_lines=300,
        scout_notes="follow T59's precedent", relevant_learnings=[],
    )


def make_ctx(cfg, plan, loop_dir, runtime, task_id="T60", attempt=1):
    task = [t for t in plan.tasks() if t.id == task_id][0]
    events = EventLog(os.path.join(loop_dir, "events.jsonl"),
                      os.path.join(runtime, "tickseq"))
    return phases.TickContext(cfg=cfg, plan=plan, task=task, loop_dir=loop_dir,
                              runtime_dir=runtime, events=events, tick=1,
                              attempt=attempt, tier="standard")


def seed_attempt(runtime, task_id, n, outcome, tier="standard", judge=None,
                 phase_results=()):
    """Drive plan A's TaskState the way the harness does, for test setup."""
    state = TaskState.load(runtime, task_id)
    while len(state.attempts) < n - 1:
        state.begin_attempt(tier)
        state.end_attempt("seeded")
    state.begin_attempt(tier)
    for p in phase_results:
        state.begin_phase(p.phase)
        state.end_phase(p)
    state.end_attempt(outcome, judge=judge)
    state.save()
    return state


def write_contract(ctx, contract):
    path = os.path.join(ctx.runtime_dir, "sprint-%s.json" % contract.task)
    save_contract(contract, path)
    return path


def read_events(loop_dir):
    path = os.path.join(loop_dir, "events.jsonl")
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def read(path):
    with open(path, "r") as fh:
        return fh.read()


def git_init(worktree):
    subprocess.check_call(["git", "init", "-q", worktree])
    for k, v in (("user.email", "loop@test"), ("user.name", "Loop Test")):
        subprocess.check_call(["git", "-C", worktree, "config", k, v])
    subprocess.check_call(["git", "-C", worktree, "add", "-A"])
    subprocess.check_call(["git", "-C", worktree, "commit", "-q", "-m", "base"])


def _write(path, text):
    with open(path, "w") as fh:
        fh.write(text)


class RecordingLog(object):
    """Stands in for `run.Log`: callable, with a `feed`, and quiet in a test run."""

    def __init__(self):
        self.lines = []

    def __call__(self, message):
        self.lines.append(message)

    def feed(self, message):
        self.lines.append(message)

    def text(self):
        return "\n".join(self.lines)


def make_harness(cfg, plan, loop_dir, runtime):
    """A real `run.Harness` over the fixture loop.

    `sandbox` and every other containment path needs the Harness, not a bare
    TickContext: it is what carries the pre-existing-work baseline (captured in
    `__post_init__`), the protected loop-dir paths and the log. Building a real
    one here is what keeps the tests honest about that.
    """
    from runner import run as runner_run
    from runner.events import UsageLog
    events = EventLog(os.path.join(loop_dir, "events.jsonl"),
                      os.path.join(runtime, "tickseq"))
    return runner_run.Harness(
        cfg=cfg,
        plugin_root=PLUGIN_ROOT,
        loop_dir=loop_dir,
        runtime_dir=runtime,
        worktree=cfg.worktree,
        config_path=os.path.join(loop_dir, "LOOP_CONFIG.md"),
        plan_path=os.path.join(loop_dir, "LOOP_PLAN.md"),
        events=events,
        usage=UsageLog(os.path.join(loop_dir, "LOOP_USAGE.jsonl")),
        log=RecordingLog(),
        plan=plan,
    )


def stub_result(payload, model="sonnet"):
    """A stream-json transcript whose `result` text carries a fenced json block."""
    text = "```json\n" + json.dumps(payload) + "\n```"
    lines = [
        json.dumps({"type": "assistant",
                    "message": {"id": "msg_1", "model": model,
                                "content": [{"type": "text", "text": "ok"}],
                                "usage": {"input_tokens": 10, "output_tokens": 5}}}),
        json.dumps({"type": "result", "subtype": "success", "is_error": False,
                    "result": text}),
    ]
    return "\n".join(lines) + "\n"


def stub_claude(tmp, files):
    """Put the scripted stub on PATH as `claude`. Returns env overrides.

    `files` maps script file names ("001.jsonl", "001.sleep", "002.exit",
    "001.sh") to their contents, per the stub protocol in the interfaces doc.
    The stub and its invocation counter live under `tmp`, which is NOT the
    worktree (that is `tmp/wt`): a counter inside the worktree is a stray the
    sandbox reverts, and the stub then replays reply 001 forever.
    """
    bindir = os.path.join(tmp, "bin")
    script = os.path.join(tmp, "stub-script")
    os.makedirs(bindir)
    os.makedirs(script)
    os.symlink(os.path.join(PLUGIN_ROOT, "tests", "fixtures", "claude"),
               os.path.join(bindir, "claude"))
    for name, body in files.items():
        _write(os.path.join(script, name), body)
    return {"PATH": bindir + os.pathsep + os.environ.get("PATH", ""),
            "STUB_SCRIPT": script,
            "STUB_LOG": os.path.join(tmp, "stub.log"),
            "STUB_DELAY": "0"}
