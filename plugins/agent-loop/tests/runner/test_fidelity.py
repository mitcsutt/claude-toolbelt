"""Fidelity ratios and thresholds (plan B task 4)."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import types
import unittest

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from runner import contract as contract_mod  # noqa: E402
from runner import git_ops  # noqa: E402
from runner import render as render_mod  # noqa: E402

REFERENCE = """import { Icon } from '@repo/ui';
import { Text } from '@repo/ui';

export interface MenuItem {
  label: string;
  icon: IconName;
  to: string;
  gate?: Gate;
}

export function AppNavigationItem({ item }: { item: MenuItem }) {
  return (
    <Link to={item.to} className="flex items-center gap-2 px-3 py-2">
      <Icon name={item.icon} size="md" />
      <Text className="xl:hidden">{item.label}</Text>
    </Link>
  );
}
"""

# The real thing: same file, ported, with the import path swapped and one class changed.
PORTED = REFERENCE.replace("@repo/ui", "@webapp/ui").replace("gap-2", "gap-3")

# What the 2026-09-11 run actually shipped for the same task (w5 s5).
COMMENT_ONLY = """// TODO(app-regression): copied from apps/frontend/src/layouts/Header/Header.tsx
// Keep in sync with the App implementation.
// See the parity matrix for the expected props.
// This file intentionally reimplements the shape.
// Regression note: icons are deferred.
// Regression note: rail width is deferred.
"""


def write(dirpath, name, body):
    path = os.path.join(dirpath, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def make_ctx(tmp):
    worktree = os.path.join(tmp, "wt")
    loop_dir = os.path.join(worktree, ".claude", "loop", "run")
    runtime_dir = os.path.join(loop_dir, "runtime")
    os.makedirs(runtime_dir)
    cfg = types.SimpleNamespace(worktree=worktree, render={}, limits={})
    task = types.SimpleNamespace(id="R3", no_ui=False, copy_of="T8")
    return types.SimpleNamespace(
        cfg=cfg, task=task, loop_dir=loop_dir, runtime_dir=runtime_dir,
        events=types.SimpleNamespace(emit=lambda *a, **k: None), tick=42, attempt=1,
    )


def contract_with(pairs):
    return contract_mod.Contract(
        task="R3", success_criteria=[], allow_list=[], forbidden=[], verification=[],
        render_gate=None,
        fidelity_source=[contract_mod.FidelitySource(src=s, dst=d, min_similarity=m)
                         for (s, d, m) in pairs],
        evaluator_must_read=[], evaluator_must_view=[],
        estimated_diff_lines=0, scout_notes="", relevant_learnings=[],
    )


class SimilarityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_real_port_clears_the_specs_threshold_by_a_wide_margin(self):
        # Spec 5.4's metric is *line* similarity, and spec 5.1's own
        # fidelity_source example puts min_similarity at 0.6 for exactly this
        # Header.tsx port. A renamed import path costs the whole line, so three
        # ported lines out of sixteen land at ~0.81 — far above the threshold,
        # and the fake copy below scores 0.0.
        a = write(self.tmp, "ref.tsx", REFERENCE)
        b = write(self.tmp, "ported.tsx", PORTED)
        ported = git_ops.similarity(a, b)
        self.assertGreaterEqual(ported, 0.8)
        fake = git_ops.similarity(a, write(self.tmp, "fake.tsx", COMMENT_ONLY))
        self.assertGreater(ported - fake, 0.5, "the ratio must separate a port from a note")

    def test_a_six_line_comment_scores_under_0_2(self):
        a = write(self.tmp, "ref.tsx", REFERENCE)
        b = write(self.tmp, "fake.tsx", COMMENT_ONLY)
        self.assertLess(git_ops.similarity(a, b), 0.2)


class RunFidelityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ctx = make_ctx(self.tmp)
        os.makedirs(os.path.join(self.ctx.cfg.worktree, "src"))

    def place(self, name, body):
        return write(os.path.join(self.ctx.cfg.worktree, "src"), name, body)

    def test_a_real_port_passes_its_threshold(self):
        self.place("ref.tsx", REFERENCE)
        self.place("dst.tsx", PORTED)
        checks = render_mod.run_fidelity(self.ctx, contract_with([("src/ref.tsx", "src/dst.tsx", 0.6)]))
        self.assertEqual(len(checks), 1)
        self.assertTrue(checks[0]["ok"])
        self.assertEqual(checks[0]["src"], "src/ref.tsx")
        self.assertEqual(checks[0]["dst"], "src/dst.tsx")
        self.assertEqual(checks[0]["min"], 0.6)
        self.assertGreaterEqual(checks[0]["ratio"], 0.8)   # see SimilarityTest

    def test_the_comment_only_copy_fails_its_threshold(self):
        self.place("ref.tsx", REFERENCE)
        self.place("dst.tsx", COMMENT_ONLY)
        checks = render_mod.run_fidelity(self.ctx, contract_with([("src/ref.tsx", "src/dst.tsx", 0.6)]))
        self.assertFalse(checks[0]["ok"])
        self.assertLess(checks[0]["ratio"], 0.2)

    def test_a_missing_destination_scores_zero_and_fails(self):
        self.place("ref.tsx", REFERENCE)
        checks = render_mod.run_fidelity(self.ctx, contract_with([("src/ref.tsx", "src/gone.tsx", 0.6)]))
        self.assertEqual(checks[0]["ratio"], 0.0)
        self.assertFalse(checks[0]["ok"])

    def test_results_are_written_to_runtime_fidelity_json(self):
        self.place("ref.tsx", REFERENCE)
        self.place("dst.tsx", PORTED)
        render_mod.run_fidelity(self.ctx, contract_with([("src/ref.tsx", "src/dst.tsx", 0.6)]))
        path = os.path.join(self.ctx.runtime_dir, "fidelity-R3.json")
        self.assertTrue(os.path.exists(path))
        with open(path) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["task"], "R3")
        self.assertEqual(len(doc["checks"]), 1)
        self.assertTrue(doc["checks"][0]["ok"])
        self.assertFalse(os.path.exists(path + ".tmp"))

    def test_no_fidelity_source_writes_nothing(self):
        checks = render_mod.run_fidelity(self.ctx, contract_with([]))
        self.assertEqual(checks, [])
        self.assertFalse(os.path.exists(os.path.join(self.ctx.runtime_dir, "fidelity-R3.json")))

    def test_fidelity_text_names_the_failing_pair_and_both_numbers(self):
        self.place("ref.tsx", REFERENCE)
        self.place("dst.tsx", COMMENT_ONLY)
        checks = render_mod.run_fidelity(self.ctx, contract_with([("src/ref.tsx", "src/dst.tsx", 0.6)]))
        text = render_mod.fidelity_text(checks)
        self.assertIn("src/ref.tsx", text)
        self.assertIn("src/dst.tsx", text)
        self.assertIn("0.6", text)
        self.assertIn("FAIL", text)


if __name__ == "__main__":
    unittest.main()
