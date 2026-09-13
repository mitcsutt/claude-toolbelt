import _path  # noqa: F401
import os
import tempfile
import unittest

from runner import status


class TestPrimitives(unittest.TestCase):
    def test_pct_rounds_to_nearest_and_handles_zero(self):
        self.assertEqual(0, status.pct(0, 0))
        self.assertEqual(62, status.pct(26, 42))
        self.assertEqual(100, status.pct(3, 3))
        self.assertEqual(50, status.pct(1, 2))

    def test_fmt_dur_has_three_shapes(self):
        self.assertEqual("45s", status.fmt_dur(45))
        self.assertEqual("3m12s", status.fmt_dur(192))
        self.assertEqual("1h18m", status.fmt_dur(4680))
        self.assertEqual("0s", status.fmt_dur(0))

    def test_progress_bar_fills_proportionally(self):
        self.assertEqual("████████████", status.progress_bar(3, 3, 12))
        self.assertEqual("░" * 12, status.progress_bar(0, 5, 12))
        self.assertEqual(12, len(status.progress_bar(1, 3, 12)))
        self.assertEqual("░" * 12, status.progress_bar(1, 0, 12))

    def test_gates_compact_marks_pass_and_fail(self):
        line = "Loop-Verification: lint=pass tsc=pass build=pass test=fail"
        self.assertEqual("lint✓ tsc✓ build✓ test✗", status.gates_compact(line))
        self.assertEqual("", status.gates_compact(""))

    def test_cause_human_spells_out_the_kill_signals(self):
        self.assertEqual("ok", status.cause_human("ok"))
        self.assertIn("SIGKILL", status.cause_human("killed"))
        self.assertIn("timeout", status.cause_human("timeout"))
        self.assertEqual("weird", status.cause_human("weird"))

    def test_verdict_glyphs(self):
        self.assertEqual("✓", status.verdict_glyph("done"))
        self.assertEqual("✓", status.verdict_glyph("continue"))
        self.assertEqual("✦", status.verdict_glyph("plan"))
        self.assertEqual("◆", status.verdict_glyph("review"))
        self.assertEqual("↻", status.verdict_glyph("retry"))
        self.assertEqual("✗", status.verdict_glyph("halt"))


class TestTickLine(unittest.TestCase):
    def test_a_committed_tick_shows_gates_and_the_sha(self):
        line = status.tick_line("done", 3, "T26", 192,
                                "lint✓ tsc✓ build✓ test✓", "a1b2c3", 26, 42)
        self.assertEqual("✓ t3 T26 · 3m12s · lint✓ tsc✓ build✓ test✓ · → a1b2c3   62% (26/42)",
                         line)

    def test_no_sha_reads_no_commit(self):
        line = status.tick_line("retry", 4, "T27", 60, "", "", 26, 42)
        self.assertIn("(no commit)", line)
        self.assertTrue(line.startswith("↻ t4 T27"))

    def test_an_unknown_task_renders_a_question_mark(self):
        self.assertIn("t5 ?", status.tick_line("plan", 5, "", 10, "", "", 0, 3))

    def test_a_non_ok_cause_is_appended_in_words(self):
        line = status.tick_line("retry", 12, "T34", 1800, "", "", 26, 42, "killed")
        self.assertTrue(line.endswith("· killed (SIGKILL — likely OS memory pressure)"))

    def test_an_ok_cause_adds_nothing(self):
        self.assertEqual(status.tick_line("done", 1, "T1", 5, "", "s", 1, 2),
                         status.tick_line("done", 1, "T1", 5, "", "s", 1, 2, "ok"))


class TestSessionHeader(unittest.TestCase):
    def test_the_task_weighted_form(self):
        head = status.session_header(26, 42, 4680, 2700, "5h 90% ↺2h12m")
        self.assertTrue(head.startswith("── loop · 62% "))
        self.assertIn("26/42", head)
        self.assertIn("⏱ 1h18m", head)
        self.assertIn("~45m00s left", head)
        self.assertIn("5h 90%", head)
        self.assertTrue(head.endswith(" ──"))

    def test_the_quota_segment_is_dropped_when_empty(self):
        self.assertNotIn(" · ·", status.session_header(1, 2, 10, 10, ""))

    def test_the_segment_weighted_form_while_segments_are_unplanned(self):
        head = status.session_header(48, 48, 10, 0, "", 3, 12)
        self.assertIn("25%", head)
        self.assertIn("seg 3/12", head)
        self.assertIn("48/48 planned tasks", head)

    def test_a_fully_planned_run_uses_the_task_form(self):
        head = status.session_header(48, 48, 10, 0, "", 12, 12)
        self.assertIn("48/48", head)
        self.assertNotIn("planned tasks", head)


class TestWriteStatus(unittest.TestCase):
    def test_it_writes_the_header_then_the_last_n_lines(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "LOOP_STATUS.md")
        status.write_status(p, "── loop · 10% ──", ["t%d line" % i for i in range(25)], tail=10)
        with open(p) as f:
            body = f.read()
        self.assertTrue(body.startswith("── loop · 10% ──\n\n"))
        self.assertIn("t24 line", body)
        self.assertNotIn("t14 line", body)
        self.assertIn("t15 line", body)

    def test_fewer_lines_than_the_tail_is_fine(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "LOOP_STATUS.md")
        status.write_status(p, "head", ["only"], tail=10)
        with open(p) as f:
            self.assertEqual("head\n\nonly\n", f.read())

    def test_a_banner_can_be_inserted_between_header_and_tail(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "LOOP_STATUS.md")
        status.write_status(p, "head", ["a"], tail=10, banner="⏸ usage limit")
        with open(p) as f:
            self.assertEqual("head\n\n⏸ usage limit\n\na\n", f.read())


if __name__ == "__main__":
    unittest.main()
