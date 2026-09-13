import _path  # noqa: F401
import json
import os
import tempfile
import unittest

from runner import calibrate, config

FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "fixtures", "events-calibrate.jsonl")


class TestRoleSpans(unittest.TestCase):
    def setUp(self):
        self.spans = calibrate.role_spans(FIXTURE)

    def test_each_role_end_is_paired_with_its_own_start_in_order(self):
        self.assertEqual([100, 200], self.spans["scout"])
        self.assertEqual([400, 1000], self.spans["worker"])
        self.assertEqual([200], self.spans["evaluator"])

    def test_a_killed_phase_contributes_no_span(self):
        """Tick 3's Worker never emitted role_end. Its wall clock is the cap, not
        the work, so counting it would calibrate the budget up to the budget."""
        self.assertEqual(2, len(self.spans["worker"]))

    def test_a_role_that_never_ran_is_absent(self):
        self.assertNotIn("judge", self.spans)
        self.assertNotIn("planner", self.spans)

    def test_a_missing_or_empty_file_yields_nothing(self):
        self.assertEqual({}, calibrate.role_spans("/nonexistent/events.jsonl"))

    def test_a_garbage_line_is_skipped(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "events.jsonl")
        with open(path, "w") as f:
            f.write("not json\n")
            f.write('{"t":10,"type":"role_start","role":"Scout"}\n')
            f.write('{"t":40,"type":"role_end","role":"Scout"}\n')
        self.assertEqual([30], calibrate.role_spans(path)["scout"])


class TestArithmetic(unittest.TestCase):
    def test_p90_is_nearest_rank(self):
        self.assertEqual(10, calibrate.p90([10]))
        self.assertEqual(200, calibrate.p90([100, 200]))
        self.assertEqual(9, calibrate.p90(list(range(1, 11))))   # nearest rank 9
        self.assertEqual(0, calibrate.p90([]))

    def test_round_up_60(self):
        self.assertEqual(60, calibrate.round_up_60(1))
        self.assertEqual(60, calibrate.round_up_60(60))
        self.assertEqual(120, calibrate.round_up_60(61))
        self.assertEqual(60, calibrate.round_up_60(0))


class TestSuggest(unittest.TestCase):
    def setUp(self):
        self.values = calibrate.suggest(calibrate.role_spans(FIXTURE))

    def test_an_observed_role_gets_p90_times_one_and_a_half_rounded_up(self):
        self.assertEqual(300, self.values["scout_timeout"])       # 200 * 1.5
        self.assertEqual(1500, self.values["worker_timeout"])     # 1000 * 1.5
        self.assertEqual(300, self.values["eval_timeout"])        # 200 * 1.5

    def test_an_unobserved_role_keeps_its_default(self):
        self.assertEqual(config.DEFAULT_LIMITS["judge_timeout"],
                         self.values["judge_timeout"])
        self.assertEqual(config.DEFAULT_LIMITS["planner_timeout"],
                         self.values["planner_timeout"])

    def test_the_non_budget_keys_are_passed_through_untouched(self):
        for key in ("tick_timeout", "gate_cmd_timeout", "worker_resume_max",
                    "worker_budget_usd", "max_attempts", "wrapup_timeout"):
            self.assertEqual(config.DEFAULT_LIMITS[key], self.values[key], key)


class TestLimitsLine(unittest.TestCase):
    def test_it_renders_every_documented_key_once(self):
        line = calibrate.limits_line(calibrate.suggest(calibrate.role_spans(FIXTURE)))
        self.assertTrue(line.startswith("Limits: "))
        keys = [tok.split("=")[0] for tok in line[len("Limits: "):].split()]
        self.assertEqual(list(calibrate.DOC_KEYS), keys)

    def test_the_line_round_trips_through_the_config_parser(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "LOOP_CONFIG.md")
        line = calibrate.limits_line(calibrate.suggest(calibrate.role_spans(FIXTURE)))
        with open(path, "w") as f:
            f.write("Worktree: /tmp/wt\n%s\n" % line)
        cfg = config.load_config(path)
        self.assertEqual(1500, config.phase_limit(cfg, "worker"))
        self.assertEqual(300, config.phase_limit(cfg, "evaluator"))


class TestMain(unittest.TestCase):
    def test_a_loop_dir_and_a_file_are_both_accepted(self):
        self.assertEqual(FIXTURE, calibrate.events_path(FIXTURE))
        d = os.path.dirname(FIXTURE)
        self.assertEqual(os.path.join(d, "events.jsonl"), calibrate.events_path(d))

    def test_json_mode_reports_the_sample_counts(self, ):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = calibrate.main([FIXTURE, "--json"])
        payload = json.loads(buf.getvalue())
        self.assertEqual(0, rc)
        self.assertEqual(2, payload["samples"]["worker"])
        self.assertEqual(1500, payload["suggested"]["worker_timeout"])

    def test_a_missing_events_file_is_an_error_not_a_traceback(self):
        self.assertEqual(1, calibrate.main(["/nonexistent"]))


if __name__ == "__main__":
    unittest.main()
