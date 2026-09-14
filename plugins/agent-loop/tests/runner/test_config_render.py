"""Render: recipe block parsing (plan B task 1)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from runner import config as config_mod  # noqa: E402

BASE = """# Loop Config
Started: 2026-09-13T00:00:00Z
Goal: rebuild the webapp
Loop type: new-feature
Granularity: segmented
TDD mode: none
Verification pipeline: lint tsc build test
Limits: tick_timeout=1800 scout_timeout=480
Blocker policy: continue-independent
Branch: agent-loop-webapp
Worktree: /tmp/wt
Spec: docs/superpowers/specs/x-design.md
Plan: docs/superpowers/plans/x.md
Dashboard: auto
Medic: auto
Medic model:
Tiers: cheap=haiku standard=sonnet most-capable=opus
Decision policy: autonomous
"""

RECIPE = """Render:
  start: pnpm --filter @repo/webapp dev --port 5273
  ready: http://127.0.0.1:5273/
  # a comment inside the block is ignored
  command: cypress run --spec {route} --env screenshot={screenshot}
  ui_globs: apps/*/src/**/*.tsx packages/ui/**/*.tsx
  reference: app-customers=docs/reference/app-customers.png app-header=docs/ref/h.png

Segment count: 3
"""


def write_cfg(body):
    fd, path = tempfile.mkstemp(suffix="-LOOP_CONFIG.md")
    with os.fdopen(fd, "w") as fh:
        fh.write(body)
    return path


class RenderBlockTest(unittest.TestCase):
    def test_absent_render_block_gives_empty_dict(self):
        cfg = config_mod.load_config(write_cfg(BASE))
        self.assertEqual(cfg.render, {})

    def test_full_block_parses_every_subkey(self):
        cfg = config_mod.load_config(write_cfg(BASE + RECIPE))
        self.assertEqual(cfg.render["start"], "pnpm --filter @repo/webapp dev --port 5273")
        self.assertEqual(cfg.render["ready"], "http://127.0.0.1:5273/")
        self.assertEqual(
            cfg.render["command"],
            "cypress run --spec {route} --env screenshot={screenshot}",
        )
        self.assertEqual(
            cfg.render["ui_globs"],
            ["apps/*/src/**/*.tsx", "packages/ui/**/*.tsx"],
        )
        self.assertEqual(
            cfg.render["reference"],
            [
                {"name": "app-customers", "path": "docs/reference/app-customers.png"},
                {"name": "app-header", "path": "docs/ref/h.png"},
            ],
        )

    def test_block_ends_at_the_next_unindented_key(self):
        cfg = config_mod.load_config(write_cfg(BASE + RECIPE))
        self.assertNotIn("Segment count", cfg.render)
        self.assertEqual(cfg.branch, "agent-loop-webapp")

    def test_inline_value_is_taken_as_command(self):
        cfg = config_mod.load_config(write_cfg(BASE + "Render: cypress run --spec {route}\n"))
        self.assertEqual(cfg.render, {"command": "cypress run --spec {route}"})

    def test_placeholder_inline_value_is_ignored(self):
        cfg = config_mod.load_config(write_cfg(BASE + "Render: <command recipe, see 5.3>\n"))
        self.assertEqual(cfg.render, {})

    def test_shipped_template_parses_with_no_recipe(self):
        tmpl = os.path.join(PLUGIN_ROOT, "templates", "LOOP_CONFIG.md")
        cfg = config_mod.load_config(tmpl)
        self.assertEqual(cfg.render, {})


class UiGlobsFeedValidationTest(unittest.TestCase):
    def test_ui_glob_match_without_render_gate_is_a_validation_error(self):
        from runner import contract as contract_mod
        from runner import plan as plan_mod

        cfg = config_mod.load_config(write_cfg(BASE + RECIPE))
        c = contract_mod.Contract(
            task="T60",
            success_criteria=["the customers page renders"],
            allow_list=["apps/webapp/src/pages/Customers.tsx"],
            forbidden=[],
            verification=["pnpm lint"],
            render_gate=None,
            fidelity_source=[],
            evaluator_must_read=[],
            evaluator_must_view=[],
            estimated_diff_lines=120,
            scout_notes="",
            relevant_learnings=[],
        )
        task = plan_mod.Task(
            id="T60", segment="Segment 1", title="build the customers page", state="doing",
            sha=None, class_flag=None, depends_on=[], clone_of=None, copy_of=None,
            blocked_by=[], no_ui=False, model=None, line_no=10,
            raw="- [ ] T60 build the customers page",
        )
        errs = contract_mod.validate(c, task, cfg, "", cfg.render["ui_globs"])
        self.assertTrue(
            any("render_gate" in e for e in errs),
            "expected a render_gate-required error, got %r" % (errs,),
        )


if __name__ == "__main__":
    unittest.main()
