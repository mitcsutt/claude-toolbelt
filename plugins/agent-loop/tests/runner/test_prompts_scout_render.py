"""Scout prompt carries the render recipe and the contract rules (plan B task 7)."""
from __future__ import annotations

import os
import sys
import types
import unittest

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from runner import phases  # noqa: E402

SCOUT = os.path.join(PLUGIN_ROOT, "runner", "prompts", "scout.md")


class ScoutPromptTest(unittest.TestCase):
    def setUp(self):
        with open(SCOUT, encoding="utf-8") as fh:
            self.text = fh.read()

    def test_recipe_placeholder_is_present(self):
        self.assertIn("{{render_recipe}}", self.text)

    def test_prompt_tells_the_scout_how_to_instantiate_render_gate(self):
        for needle in ("render_gate", "{route}", "{screenshot}", "ui_globs", "| no-ui"):
            self.assertIn(needle, self.text, "scout.md must mention %r" % needle)

    def test_prompt_covers_fidelity_source(self):
        for needle in ("fidelity_source", "copy_of", "min_similarity"):
            self.assertIn(needle, self.text, "scout.md must mention %r" % needle)

    def test_prompt_covers_evaluator_inputs(self):
        for needle in ("evaluator_must_read", "evaluator_must_view"):
            self.assertIn(needle, self.text, "scout.md must mention %r" % needle)

    def test_prompt_forbids_annotation_as_evidence(self):
        self.assertIn("comment", self.text.lower())
        self.assertRegex(self.text, r"(?i)grep.*(comment|TODO)|TODO.*not (a|evidence)")

    def test_the_recipe_placeholder_is_actually_filled_by_run_scout(self):
        # A placeholder nothing fills raises KeyError inside render_prompt, so
        # this is the assertion that the prompt and the phase agree.
        with open(os.path.join(PLUGIN_ROOT, "runner", "phases.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('"render_recipe": recipe_text(', src)


class RecipeTextTest(unittest.TestCase):
    def test_no_recipe_says_so_plainly(self):
        cfg = types.SimpleNamespace(render={}, worktree="/wt")
        self.assertIn("no render recipe", phases.recipe_text(cfg).lower())

    def test_recipe_is_rendered_with_every_subkey(self):
        cfg = types.SimpleNamespace(worktree="/wt", render={
            "start": "pnpm dev",
            "ready": "http://127.0.0.1:5273/",
            "command": "cypress run --spec {route} --env screenshot={screenshot}",
            "ui_globs": ["apps/*/src/**/*.tsx"],
            "reference": [{"name": "rise-nav", "path": "docs/rise-nav.png"}],
        })
        text = phases.recipe_text(cfg)
        self.assertIn("pnpm dev", text)
        self.assertIn("http://127.0.0.1:5273/", text)
        self.assertIn("{route}", text)
        self.assertIn("apps/*/src/**/*.tsx", text)
        self.assertIn("rise-nav", text)


if __name__ == "__main__":
    unittest.main()
