import _path  # noqa: F401
import os
import re
import tempfile
import unittest

from runner import plan as planmod
from runner.plan import Plan

SAMPLE = """# Loop Plan

Legend: [ ] pending | [~] in-progress | [x] done(+SHA) | [!] blocked | [-] skipped

## Segment A: bootstrap
Reviewed: abc1234
- [x] T1: Scaffold the package done(+deadbee)
- [-] T2: Drop the old shim

## Segment B: wiring
Goal: wire it up
- [ ] T3: Add the parser | depends_on: T1 | complex
- [ ] T4: Add the writer | depends_on: T3 | model: mid
- [ ] T5: Copy the header from the reference | copy_of: T3 | no-ui
- [~] T6: In flight | mechanical

## Segment C: polish
"""


def write(text):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "LOOP_PLAN.md")
    with open(p, "w") as f:
        f.write(text)
    return p


class TestParsing(unittest.TestCase):
    def setUp(self):
        self.p = Plan.load(write(SAMPLE))

    def test_every_task_row_is_found_in_order(self):
        self.assertEqual(["T1", "T2", "T3", "T4", "T5", "T6"],
                         [t.id for t in self.p.tasks()])

    def test_states_come_from_the_glyph(self):
        states = dict((t.id, t.state) for t in self.p.tasks())
        self.assertEqual("done", states["T1"])
        self.assertEqual("skipped", states["T2"])
        self.assertEqual("pending", states["T3"])
        self.assertEqual("doing", states["T6"])

    def test_segment_membership_and_headings(self):
        self.assertEqual(["Segment A: bootstrap", "Segment B: wiring", "Segment C: polish"],
                         [s.name for s in self.p.segments()])
        self.assertEqual("Segment B: wiring", self.p.task("T4").segment)
        self.assertEqual([], self.p.segments()[2].tasks)

    def test_reviewed_sha_is_read_from_under_the_heading(self):
        self.assertEqual("abc1234", self.p.segments()[0].reviewed_sha)
        self.assertIsNone(self.p.segments()[1].reviewed_sha)

    def test_metadata_flags(self):
        t3, t4, t5, t6 = (self.p.task(i) for i in ("T3", "T4", "T5", "T6"))
        self.assertEqual(["T1"], t3.depends_on)
        self.assertEqual("complex", t3.class_flag)
        self.assertEqual("mid", t4.model)
        self.assertEqual("T3", t5.copy_of)
        self.assertTrue(t5.no_ui)
        self.assertFalse(t3.no_ui)
        self.assertEqual("mechanical", t6.class_flag)
        self.assertIsNone(t4.class_flag)

    def test_title_is_the_text_before_the_first_divider_without_the_id(self):
        self.assertEqual("Add the parser", self.p.task("T3").title)
        self.assertEqual("Scaffold the package done(+deadbee)", self.p.task("T1").title)

    def test_sha_is_read_back_off_a_done_row(self):
        self.assertEqual("deadbee", self.p.task("T1").sha)
        self.assertIsNone(self.p.task("T3").sha)

    def test_blocked_by_and_split_of_parse(self):
        p = Plan.load(write("## S\n- [ ] T74: sub one | blocked_by: T7,T8 | split_of: T60\n"))
        t = p.tasks()[0]
        self.assertEqual("T74", t.id)
        self.assertEqual(["T7", "T8"], t.blocked_by)
        self.assertEqual("T60", t.split_of)

    def test_a_lettered_id_is_not_an_id(self):
        # serve.py greps \bT\d+\b; T60a would vanish from every dashboard count,
        # so this module must not invent one either.
        p = Plan.load(write("## S\n- [ ] T60a: never written by this module\n"))
        self.assertEqual("", p.tasks()[0].id)

    def test_blocked_upstream_marker_becomes_a_state(self):
        p = Plan.load(write("## S\n- [ ] T9: later [blocked-upstream]\n"))
        self.assertEqual("blocked-upstream", p.tasks()[0].state)

    def test_rows_before_the_first_heading_belong_to_the_empty_segment(self):
        p = Plan.load(write("- [ ] T1: flat\n- [ ] T2: also flat\n"))
        self.assertEqual(["T1", "T2"], [t.id for t in p.tasks()])
        self.assertEqual("", p.tasks()[0].segment)
        self.assertEqual([], p.segments())


class TestEligibility(unittest.TestCase):
    def setUp(self):
        self.p = Plan.load(write(SAMPLE))

    def test_pending_with_satisfied_depends_on_is_eligible(self):
        self.assertEqual(["T3", "T5"], [t.id for t in self.p.eligible()])

    def test_skipped_dependency_counts_as_satisfied(self):
        p = Plan.load(write("## S\n- [-] T1: dropped\n- [ ] T2: next | depends_on: T1\n"))
        self.assertEqual(["T2"], [t.id for t in p.eligible()])

    def test_blocked_by_suppresses_eligibility(self):
        p = Plan.load(write("## S\n- [ ] T1: a\n- [ ] T2: b | blocked_by: T1\n"))
        self.assertEqual(["T1"], [t.id for t in p.eligible()])

    def test_blocked_upstream_is_never_eligible(self):
        p = Plan.load(write("## S\n- [ ] T1: a [blocked-upstream]\n- [ ] T2: b\n"))
        self.assertEqual(["T2"], [t.id for t in p.eligible()])

    def test_dependents_are_transitive(self):
        p = Plan.load(write("## S\n- [ ] T1: a\n- [ ] T2: b | depends_on: T1\n"
                            "- [ ] T3: c | depends_on: T2\n- [ ] T4: d\n"))
        self.assertEqual(["T2", "T3"], [t.id for t in p.dependents("T1")])


class TestMode(unittest.TestCase):
    def test_execute_when_something_is_eligible(self):
        self.assertEqual("execute", Plan.load(write(SAMPLE)).mode())

    def test_review_when_a_segment_is_complete_and_unstamped(self):
        p = Plan.load(write("## S\n- [x] T1: a\n- [-] T2: b\n\n## T\n- [ ] T3: c\n"))
        self.assertEqual("review", p.mode())
        self.assertEqual("S", p.next_review_segment().name)

    def test_plan_when_a_segment_is_empty_and_nothing_is_eligible(self):
        p = Plan.load(write("## S\nReviewed: aaa\n- [x] T1: a\n\n## T\n"))
        self.assertEqual("plan", p.mode())
        self.assertEqual("T", p.next_plan_segment().name)

    def test_execute_beats_plan_while_work_remains(self):
        p = Plan.load(write("## S\nReviewed: aaa\n- [ ] T1: a\n\n## T\n"))
        self.assertEqual("execute", p.mode())

    def test_done_when_nothing_is_open_and_nothing_is_unplanned(self):
        p = Plan.load(write("## S\nReviewed: aaa\n- [x] T1: a\n- [-] T2: b\n"))
        self.assertEqual("done", p.mode())

    def test_flat_plan_reaches_done_without_review(self):
        p = Plan.load(write("- [x] T1: a\n- [x] T2: b\n"))
        self.assertEqual("done", p.mode())

    def test_stuck_when_work_remains_but_nothing_can_run(self):
        p = Plan.load(write("## S\nReviewed: aaa\n- [!] T1: blocked\n- [ ] T2: b | depends_on: T1\n"))
        self.assertEqual("stuck", p.mode())


class TestEdits(unittest.TestCase):
    def setUp(self):
        self.path = write(SAMPLE)
        self.p = Plan.load(self.path)

    def test_set_state_flips_the_glyph_in_place_without_touching_the_text(self):
        self.p.set_state("T3", "doing")
        self.assertEqual("doing", self.p.task("T3").state)
        self.assertIn("- [~] T3: Add the parser | depends_on: T1 | complex",
                      "\n".join(self.p.lines))

    def test_set_state_done_appends_the_sha_exactly_once(self):
        self.p.set_state("T3", "done", sha="feed123")
        self.p.set_state("T3", "done", sha="feed123")
        row = [ln for ln in self.p.lines if "T3:" in ln][0]
        self.assertEqual(1, row.count("done(+feed123)"))
        self.assertTrue(row.startswith("- [x] T3: Add the parser"))
        self.assertEqual("feed123", self.p.task("T3").sha)

    def test_set_state_blocked_upstream_keeps_the_pending_glyph(self):
        self.p.set_state("T4", "blocked-upstream")
        row = [ln for ln in self.p.lines if "T4:" in ln][0]
        self.assertTrue(row.startswith("- [ ] "))
        self.assertTrue(row.endswith(planmod.BLOCKED_UPSTREAM))
        self.assertEqual("blocked-upstream", self.p.task("T4").state)

    def test_set_state_clears_the_marker_when_the_task_unblocks(self):
        self.p.set_state("T4", "blocked-upstream")
        self.p.set_state("T4", "pending")
        self.assertNotIn(planmod.BLOCKED_UPSTREAM, "\n".join(self.p.lines))
        self.assertEqual("pending", self.p.task("T4").state)

    def test_set_state_rejects_an_unknown_task_or_state(self):
        with self.assertRaises(KeyError):
            self.p.set_state("T99", "done")
        with self.assertRaises(ValueError):
            self.p.set_state("T3", "elsewhere")

    def test_append_tasks_lands_at_the_end_of_the_named_segment(self):
        self.p.append_tasks("Segment B: wiring", ["- [ ] T7: follow-up | depends_on: T3"])
        text = "\n".join(self.p.lines)
        self.assertLess(text.index("T7: follow-up"), text.index("## Segment C"))
        self.assertGreater(text.index("T7: follow-up"), text.index("T6: In flight"))
        self.assertEqual("Segment B: wiring", self.p.task("T7").segment)

    def test_append_tasks_into_an_empty_segment(self):
        self.p.append_tasks("Segment C: polish", ["- [ ] T8: tidy"])
        self.assertEqual("Segment C: polish", self.p.task("T8").segment)

    def test_stamp_reviewed_inserts_under_the_heading_and_replaces_an_old_stamp(self):
        self.p.stamp_reviewed("Segment B: wiring", "cafe999")
        self.assertEqual("cafe999", self.p.segments()[1].reviewed_sha)
        self.p.stamp_reviewed("Segment A: bootstrap", "0000111")
        self.assertEqual("0000111", self.p.segments()[0].reviewed_sha)
        self.assertEqual(1, "\n".join(self.p.lines).count("Reviewed: 0000111"))

    def test_stamp_reviewed_is_not_a_task_row(self):
        self.p.stamp_reviewed("Segment B: wiring", "cafe999")
        before = len(self.p.tasks())
        self.assertEqual(6, before)

    def test_split_allocates_the_next_free_numeric_ids(self):
        ids = self.p.split("T4", ["- [ ] first half | depends_on: T3",
                                  "- [ ] second half"])
        self.assertEqual(["T7", "T8"], ids)
        parent = [ln for ln in self.p.lines if ln.startswith("- [-] T4:")][0]
        self.assertIn("split→T7,T8", parent)
        text = "\n".join(self.p.lines)
        self.assertLess(text.index("first half"), text.index("T5: Copy the header"))
        self.assertEqual("Segment B: wiring", self.p.task("T8").segment)

    def test_split_rewrites_whatever_id_the_caller_proposed(self):
        # The Judge's changes.sub_rows carry ids it invented; the harness owns
        # numbering, so they are replaced rather than trusted.
        ids = self.p.split("T4", ["- [ ] T4a: first half | depends_on: T3"])
        self.assertEqual(["T7"], ids)
        row = [ln for ln in self.p.lines if "first half" in ln][0]
        self.assertTrue(row.startswith("- [ ] T7: first half | depends_on: T3"), row)
        self.assertNotIn("T4a", "\n".join(self.p.lines))

    def test_each_sub_row_records_its_parent(self):
        self.p.split("T4", ["- [ ] first half", "- [ ] second half"])
        self.assertEqual("T4", self.p.task("T7").split_of)
        self.assertEqual("T4", self.p.task("T8").split_of)

    def test_next_ids_counts_from_the_highest_id_in_the_whole_plan(self):
        self.assertEqual(["T7", "T8", "T9"], self.p.next_ids(3))
        p = Plan.load(write("## S\n- [ ] T41: only one\n"))
        self.assertEqual(["T42"], p.next_ids(1))
        self.assertEqual(["T1"], Plan.load(write("## S\n")).next_ids(1))

    def test_save_is_a_byte_exact_round_trip_when_nothing_changed(self):
        self.p.save()
        with open(self.path) as f:
            self.assertEqual(SAMPLE, f.read())

    def test_every_row_this_module_writes_still_matches_the_serve_py_regex(self):
        serve_re = re.compile(r"^\s*- \[(.)\] (.*)$")
        self.p.set_state("T3", "done", sha="feed123")
        self.p.set_state("T4", "blocked-upstream")
        self.p.split("T5", ["- [ ] half"])
        self.p.append_tasks("Segment C: polish", ["- [ ] T20: tidy"])
        rows = [ln for ln in self.p.lines if ln.lstrip().startswith("- [")]
        self.assertEqual(8, len(rows))
        for row in rows:
            self.assertTrue(serve_re.match(row), row)
            self.assertIn(serve_re.match(row).group(1), " ~x!-")
            self.assertTrue(planmod.ID_RE.search(row), "every row keeps a T<n> id: %s" % row)


if __name__ == "__main__":
    unittest.main()
