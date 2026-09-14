import _path  # noqa: F401
import json
import os
import tempfile
import unittest

from runner import contract as cmod
from runner.config import LoopConfig
from runner.contract import Contract, FidelitySource, Forbidden, RenderGate
from runner.plan import Task

CFG = LoopConfig(worktree="/tmp/wt")
UI_GLOBS = ["apps/*/src/**", "packages/ui/**"]


def task(**kw):
    base = dict(id="T60", segment="S", title="Add the org-unit request", state="pending")
    base.update(kw)
    return Task(**base)


def contract(**kw):
    base = dict(
        task="T60",
        success_criteria=["packages/api/src/requests/widgets.ts exports listWidgets"],
        allow_list=["packages/api/src/requests/widgets.ts"],
        forbidden=[Forbidden(path="apps/frontend/**", source="plan")],
        verification=["pnpm turbo run lint --filter=@repo/api"],
        render_gate=None,
        fidelity_source=[],
        evaluator_must_read=[],
        evaluator_must_view=[],
        estimated_diff_lines=120,
        scout_notes="inline everything the worker needs",
        relevant_learnings=[],
    )
    base.update(kw)
    return Contract(**base)


class TestValidateHappyPath(unittest.TestCase):
    def test_a_well_formed_contract_has_no_errors(self):
        self.assertEqual([], cmod.validate(contract(), task(), CFG, "", UI_GLOBS))


class TestCriteriaAreNotPathScanned(unittest.TestCase):
    """success_criteria is prose and verification is shell; neither is scanned
    for paths outside allow_list. A criterion that genuinely needs a file
    outside allow_list is caught at runtime by the gate + Judge-widen (proven in
    run.e2e's widen scenario) — auditable and self-healing, unlike a static scan
    that refused a legal contract on every task it was measured against
    (braces in a prose example, a JSDoc delimiter, a read-only path in a grep)."""

    def _clean(self, **kw):
        self.assertEqual([], cmod.validate(contract(**kw), task(), CFG, "", UI_GLOBS))

    def test_a_criterion_naming_a_path_outside_allow_list_is_allowed(self):
        self._clean(
            success_criteria=["apps/frontend/src/Header.tsx gains the nav item"])

    def test_prose_braces_and_dotted_identifiers_are_allowed(self):
        self._clean(success_criteria=[
            'an in-file array of objects, e.g. { name: "Coffees", count: 12 }',
            "the flag board.dataset.ready is set once counters.length rows exist"])

    def test_a_jsdoc_delimiter_in_prose_is_allowed(self):
        self._clean(success_criteria=["each export gains a /** ... */ block"])

    def test_read_only_repo_paths_inside_a_verification_grep_are_allowed(self):
        self._clean(verification=[
            "grep -q appendChild public/app.js && grep -q parse src/format.js"])


class TestForbiddenProvenance(unittest.TestCase):
    def test_an_unknown_source_is_an_error(self):
        c = contract(forbidden=[Forbidden(path="apps/**", source="vibes")])
        errs = cmod.validate(c, task(), CFG, "", UI_GLOBS)
        self.assertEqual(1, len(errs))
        self.assertIn("source", errs[0])

    def test_an_empty_source_is_an_error(self):
        c = contract(forbidden=[Forbidden(path="apps/**", source="")])
        self.assertEqual(1, len(cmod.validate(c, task(), CFG, "", UI_GLOBS)))

    def test_all_three_provenances_are_accepted(self):
        c = contract(forbidden=[Forbidden(path="a/b", source="plan"),
                                Forbidden(path="c/d", source="spec"),
                                Forbidden(path="e/f", source="scout")])
        self.assertEqual([], cmod.validate(c, task(), CFG, "", UI_GLOBS))


class TestBlockedByCleanup(unittest.TestCase):
    CLEANUP = ("# Cleanup\n\n"
               "- T60: blocked on whether org units are a tab or a page — needs a human\n"
               "- T99: unrelated\n")

    def test_a_task_named_in_cleanup_is_an_error(self):
        errs = cmod.validate(contract(), task(), CFG, self.CLEANUP, UI_GLOBS)
        self.assertEqual(1, len(errs))
        self.assertIn("blocked-by-cleanup", errs[0])

    def test_another_task_in_cleanup_is_not_our_problem(self):
        errs = cmod.validate(contract(task="T61"), task(id="T61"), CFG,
                             self.CLEANUP, UI_GLOBS)
        self.assertEqual([], errs)

    def test_a_prefix_match_does_not_count(self):
        errs = cmod.validate(contract(task="T6"), task(id="T6"), CFG,
                             self.CLEANUP, UI_GLOBS)
        self.assertEqual([], errs)

    def test_headings_are_ignored(self):
        errs = cmod.validate(contract(), task(), CFG, "## T60 notes\n", UI_GLOBS)
        self.assertEqual([], errs)


class TestRenderRequirement(unittest.TestCase):
    UI = ["apps/webapp/src/pages/Widgets.tsx"]
    CRITERIA = ["apps/webapp/src/pages/Widgets.tsx lists the org units"]

    def test_a_ui_touching_allow_list_needs_a_render_gate(self):
        c = contract(allow_list=self.UI, success_criteria=self.CRITERIA)
        errs = cmod.validate(c, task(), CFG, "", ["apps/*/src/**"])
        self.assertEqual(1, len(errs))
        self.assertIn("render_gate", errs[0])

    def test_the_no_ui_flag_waives_it(self):
        c = contract(allow_list=self.UI, success_criteria=self.CRITERIA)
        self.assertEqual([], cmod.validate(c, task(no_ui=True), CFG, "", ["apps/*/src/**"]))

    def test_a_declared_render_gate_satisfies_it(self):
        c = contract(allow_list=self.UI, success_criteria=self.CRITERIA,
                     render_gate=RenderGate(commands=["pnpm cypress run"],
                                            screenshots=[{"name": "widgets",
                                                          "path": "shots/widgets.png"}]))
        self.assertEqual([], cmod.validate(c, task(), CFG, "", ["apps/*/src/**"]))

    def test_no_recipe_means_no_requirement(self):
        c = contract(allow_list=self.UI, success_criteria=self.CRITERIA)
        self.assertEqual([], cmod.validate(c, task(), CFG, "", []))

    def test_a_screenshot_needs_a_name_and_a_path(self):
        c = contract(render_gate=RenderGate(commands=["x"], screenshots=[{"name": "a"}]))
        errs = cmod.validate(c, task(), CFG, "", UI_GLOBS)
        self.assertEqual(1, len(errs))
        self.assertIn("screenshot", errs[0])

    def test_a_render_gate_needs_at_least_one_command(self):
        c = contract(render_gate=RenderGate(commands=[], screenshots=[]))
        errs = cmod.validate(c, task(), CFG, "", UI_GLOBS)
        self.assertEqual(1, len(errs))
        self.assertIn("command", errs[0])


class TestFidelityRequirement(unittest.TestCase):
    def test_a_copy_of_row_needs_a_fidelity_source(self):
        errs = cmod.validate(contract(), task(copy_of="T12"), CFG, "", UI_GLOBS)
        self.assertEqual(1, len(errs))
        self.assertIn("fidelity_source", errs[0])

    def test_a_copy_verb_in_the_title_needs_one_too(self):
        for verb in ("Copy", "port", "Replicate"):
            errs = cmod.validate(contract(), task(title="%s the header" % verb),
                                 CFG, "", UI_GLOBS)
            self.assertEqual(1, len(errs), verb)

    def test_a_declared_pair_satisfies_it(self):
        c = contract(fidelity_source=[FidelitySource(src="a/b.tsx", dst="c/d.tsx",
                                                     min_similarity=0.6)])
        self.assertEqual([], cmod.validate(c, task(copy_of="T12"), CFG, "", UI_GLOBS))

    def test_min_similarity_must_be_a_ratio_above_zero(self):
        for bad in (0.0, -1.0, 1.5):
            c = contract(fidelity_source=[FidelitySource(src="a/b", dst="c/d",
                                                         min_similarity=bad)])
            errs = cmod.validate(c, task(copy_of="T12"), CFG, "", UI_GLOBS)
            self.assertEqual(1, len(errs), bad)
            self.assertIn("min_similarity", errs[0])

    def test_an_ordinary_task_needs_nothing(self):
        self.assertEqual([], cmod.validate(contract(), task(title="Add the parser"),
                                           CFG, "", UI_GLOBS))


class TestStructuralErrors(unittest.TestCase):
    def test_a_task_id_mismatch_is_an_error(self):
        errs = cmod.validate(contract(task="T7"), task(id="T60"), CFG, "", UI_GLOBS)
        self.assertEqual(1, len(errs))
        self.assertIn("T7", errs[0])

    def test_empty_required_arrays_are_errors(self):
        errs = cmod.validate(contract(success_criteria=[], allow_list=[], verification=[]),
                             task(), CFG, "", UI_GLOBS)
        self.assertEqual(3, len(errs))

    def test_errors_accumulate_rather_than_short_circuiting(self):
        c = contract(task="T7", verification=[],
                     forbidden=[Forbidden(path="a/b", source="guess")])
        self.assertEqual(3, len(cmod.validate(c, task(), CFG, "", UI_GLOBS)))


class TestRoundTrip(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "sprint-T60.json")

    def test_save_then_load_preserves_every_field(self):
        c = contract(render_gate=RenderGate(commands=["pnpm cypress run"],
                                            screenshots=[{"name": "a", "path": "b.png"}]),
                     fidelity_source=[FidelitySource(src="a/b.tsx", dst="c/d.tsx",
                                                     min_similarity=0.6)],
                     evaluator_must_read=["a/b.tsx"], evaluator_must_view=["a"],
                     relevant_learnings=["always run x"])
        cmod.save_contract(c, self.p)
        back = cmod.load_contract(self.p)
        self.assertEqual(c, back)

    def test_the_on_disk_pair_uses_the_spec_key_names(self):
        cmod.save_contract(contract(fidelity_source=[
            FidelitySource(src="a/b", dst="c/d", min_similarity=0.6)]), self.p)
        with open(self.p) as f:
            raw = json.load(f)
        self.assertEqual({"from": "a/b", "to": "c/d", "min_similarity": 0.6},
                         raw["fidelity_source"][0])

    def test_src_dst_keys_are_also_accepted_on_load(self):
        with open(self.p, "w") as f:
            json.dump({"task": "T60", "fidelity_source":
                       [{"src": "a/b", "dst": "c/d", "min_similarity": 0.5}]}, f)
        self.assertEqual("a/b", cmod.load_contract(self.p).fidelity_source[0].src)

    def test_missing_optional_fields_default(self):
        with open(self.p, "w") as f:
            json.dump({"task": "T60", "success_criteria": ["x"],
                       "allow_list": ["a/b"], "verification": ["true"]}, f)
        c = cmod.load_contract(self.p)
        self.assertEqual([], c.forbidden)
        self.assertIsNone(c.render_gate)
        self.assertEqual(0, c.estimated_diff_lines)

    def test_a_missing_or_malformed_file_raises_contract_error(self):
        with self.assertRaises(cmod.ContractError):
            cmod.load_contract(os.path.join(self.d, "nope.json"))
        with open(self.p, "w") as f:
            f.write("{not json")
        with self.assertRaises(cmod.ContractError):
            cmod.load_contract(self.p)


class TestLoadListValidation(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "sprint-T60.json")

    def _write(self, obj):
        with open(self.p, "w") as f:
            json.dump(obj, f)

    def test_a_string_success_criteria_raises_contract_error(self):
        self._write({"task": "T60", "success_criteria": "just do it",
                     "allow_list": ["a/b"], "verification": ["true"]})
        with self.assertRaises(cmod.ContractError):
            cmod.load_contract(self.p)

    def test_a_scalar_allow_list_raises_contract_error_not_type_error(self):
        self._write({"task": "T60", "success_criteria": ["x"],
                     "allow_list": 5, "verification": ["true"]})
        with self.assertRaises(cmod.ContractError):
            cmod.load_contract(self.p)

    def test_a_dict_verification_raises_contract_error(self):
        self._write({"task": "T60", "success_criteria": ["x"],
                     "allow_list": ["a/b"], "verification": {"cmd": "true"}})
        with self.assertRaises(cmod.ContractError):
            cmod.load_contract(self.p)

    def test_a_non_list_forbidden_raises_contract_error(self):
        self._write({"task": "T60", "success_criteria": ["x"],
                     "allow_list": ["a/b"], "verification": ["true"],
                     "forbidden": "apps/**"})
        with self.assertRaises(cmod.ContractError):
            cmod.load_contract(self.p)

    def test_a_non_list_fidelity_source_raises_contract_error(self):
        self._write({"task": "T60", "success_criteria": ["x"],
                     "allow_list": ["a/b"], "verification": ["true"],
                     "fidelity_source": {"from": "a", "to": "b"}})
        with self.assertRaises(cmod.ContractError):
            cmod.load_contract(self.p)

    def test_missing_list_keys_default_to_empty(self):
        self._write({"task": "T60", "success_criteria": ["x"],
                     "allow_list": ["a/b"], "verification": ["true"]})
        c = cmod.load_contract(self.p)
        self.assertEqual([], c.forbidden)
        self.assertEqual([], c.fidelity_source)
        self.assertEqual([], c.evaluator_must_read)
        self.assertEqual([], c.evaluator_must_view)
        self.assertEqual([], c.relevant_learnings)


if __name__ == "__main__":
    unittest.main()
