import _path  # noqa: F401
import os
import tempfile
import unittest

from runner import config

FULL = """# Loop Config
Started: 2026-09-13T00:00:00Z
Goal: port the internal app
Loop type: refactor
Granularity: segmented
TDD mode: tdd-per-task
Verification pipeline: lint tsc build test
Limits: tick_timeout=900 worker_timeout=1200 worker_resume_max=2
Tiers: cheap=tiny standard=mid most-capable=big
Decision policy: conservative
Blocker policy: halt
Branch: agent-loop-port
Worktree: /tmp/wt
Segment count: 4
Spec: docs/spec.md
Plan: docs/plan.md
Dashboard: off   # no sidecar
Medic: notify
Medic model: mid
Render: ui_globs=apps/*/src/**,packages/ui/**
Planner tier: most-capable
Scout tier: standard
Worker tier: standard
Evaluator tier:
"""

MINIMAL = "Worktree: /tmp/wt\n"


def write(text):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "LOOP_CONFIG.md")
    with open(p, "w") as f:
        f.write(text)
    return p


class TestLoadConfig(unittest.TestCase):
    def test_scalar_fields(self):
        c = config.load_config(write(FULL))
        self.assertEqual("/tmp/wt", c.worktree)
        self.assertEqual("agent-loop-port", c.branch)
        self.assertEqual("port the internal app", c.goal)
        self.assertEqual("segmented", c.granularity)
        self.assertEqual("tdd-per-task", c.tdd_mode)
        self.assertEqual(["lint", "tsc", "build", "test"], c.verification)
        self.assertEqual("halt", c.blocker_policy)
        self.assertEqual("conservative", c.decision_policy)
        self.assertEqual("docs/spec.md", c.spec_path)
        self.assertEqual("docs/plan.md", c.plan_path)

    def test_first_word_wins_for_mode_fields_with_trailing_comments(self):
        c = config.load_config(write(FULL))
        self.assertEqual("off", c.dashboard)
        self.assertEqual("notify", c.medic)
        self.assertEqual("mid", c.medic_model)

    def test_limits_merge_over_defaults(self):
        c = config.load_config(write(FULL))
        self.assertEqual(900, c.limits["tick_timeout"])
        self.assertEqual(1200, c.limits["worker_timeout"])
        self.assertEqual(2, c.limits["worker_resume_max"])
        self.assertEqual(480, c.limits["scout_timeout"])   # default survives
        self.assertEqual(600, c.limits["gate_cmd_timeout"])
        self.assertEqual(3, c.limits["max_attempts"])
        self.assertEqual(6, c.limits["worker_budget_usd"])

    def test_the_documented_limits_keys_are_exactly_the_interface_contract(self):
        keys = set(config.load_config(write(MINIMAL)).limits)
        self.assertEqual(set(), {
            "tick_timeout", "scout_timeout", "worker_timeout", "wrapup_timeout",
            "eval_timeout", "judge_timeout", "planner_timeout", "gate_cmd_timeout",
            "worker_resume_max", "worker_budget_usd", "max_attempts"} - keys)

    def test_a_v2_style_bare_role_key_is_carried_but_never_read(self):
        c = config.load_config(write(MINIMAL + "Limits: worker=99\n"))
        self.assertEqual(1500, config.phase_limit(c, "worker"))

    def test_tiers_and_role_tiers(self):
        c = config.load_config(write(FULL))
        self.assertEqual({"cheap": "tiny", "standard": "mid", "most-capable": "big"}, c.tiers)
        self.assertEqual("most-capable", c.role_tiers["planner"])
        self.assertEqual("standard", c.role_tiers["scout"])
        self.assertEqual("", c.role_tiers["evaluator"])
        self.assertEqual("cheap", c.role_tiers["learner"])

    def test_render_line_yields_ui_globs(self):
        c = config.load_config(write(FULL))
        self.assertEqual(["apps/*/src/**", "packages/ui/**"], c.render["ui_globs"])
        self.assertIn("ui_globs=", c.render["raw"])

    def test_defaults_when_everything_is_absent(self):
        c = config.load_config(write(MINIMAL))
        self.assertEqual("autonomous", c.decision_policy)
        self.assertEqual("continue-independent", c.blocker_policy)
        self.assertEqual("auto", c.dashboard)
        self.assertEqual("auto", c.medic)
        self.assertEqual({}, c.tiers)
        self.assertEqual({}, c.render)
        self.assertEqual(1800, c.limits["tick_timeout"])
        self.assertEqual(1500, c.limits["worker_timeout"])
        self.assertEqual(300, c.limits["wrapup_timeout"])
        self.assertEqual(360, c.limits["judge_timeout"])
        self.assertEqual(900, c.limits["planner_timeout"])
        self.assertEqual(1, c.limits["worker_resume_max"])
        self.assertEqual([], c.verification)

    def test_v2_config_with_orchestrator_model_still_parses(self):
        c = config.load_config(write(MINIMAL + "Orchestrator model: mid\nLimits: tick_timeout=1200\n"))
        self.assertEqual(1200, c.limits["tick_timeout"])
        self.assertFalse(hasattr(c, "orchestrator_model"))

    def test_missing_file_raises(self):
        with self.assertRaises(ValueError):
            config.load_config("/nonexistent/LOOP_CONFIG.md")


class TestTiers(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load_config(write(FULL))

    def test_model_for_resolves_through_the_tier_map(self):
        self.assertEqual("mid", config.model_for(self.cfg, "standard"))
        self.assertEqual("big", config.model_for(self.cfg, "most-capable"))

    def test_model_for_unmapped_tier_means_inherit_the_default(self):
        cfg = config.load_config(write(MINIMAL))
        self.assertEqual("", config.model_for(cfg, "standard"))

    def test_model_for_rejects_an_unknown_tier(self):
        with self.assertRaises(ValueError):
            config.model_for(self.cfg, "turbo")

    def test_next_tier_steps_up_and_clamps(self):
        # A helper for plan C's Judge `escalate` decision. Nothing in plan A's
        # failure path calls it: there is no ladder.
        self.assertEqual("standard", config.next_tier("cheap"))
        self.assertEqual("most-capable", config.next_tier("standard"))
        self.assertEqual("most-capable", config.next_tier("most-capable"))

    def test_next_tier_rejects_an_unknown_tier(self):
        with self.assertRaises(ValueError):
            config.next_tier("")


class TestPhaseLimit(unittest.TestCase):
    def test_configured_value_wins_and_defaults_fill_in(self):
        cfg = config.load_config(write(FULL))
        self.assertEqual(1200, config.phase_limit(cfg, "worker"))
        self.assertEqual(480, config.phase_limit(cfg, "scout"))
        self.assertEqual(360, config.phase_limit(cfg, "judge"))

    def test_every_phase_maps_onto_its_documented_limits_key(self):
        cfg = config.load_config(write(MINIMAL))
        for phase, secs in (("scout", 480), ("worker", 1500), ("wrapup", 300),
                            ("evaluator", 480), ("judge", 360), ("planner", 900),
                            ("reviewer", 900), ("learner", 180),
                            ("gate_cmd", 600), ("tick_timeout", 1800)):
            self.assertEqual(secs, config.phase_limit(cfg, phase), phase)

    def test_the_evaluator_phase_reads_eval_timeout(self):
        cfg = config.load_config(write(MINIMAL + "Limits: eval_timeout=42\n"))
        self.assertEqual(42, config.phase_limit(cfg, "evaluator"))

    def test_unknown_phase_falls_back_to_the_gate_budget(self):
        cfg = config.load_config(write(MINIMAL))
        self.assertEqual(600, config.phase_limit(cfg, "something-else"))


if __name__ == "__main__":
    unittest.main()
