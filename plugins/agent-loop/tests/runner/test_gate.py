import _path  # noqa: F401
import os
import tempfile
import unittest

from runner import gate


class TestRunCommands(unittest.TestCase):
    def setUp(self):
        self.cwd = tempfile.mkdtemp()
        self.out = tempfile.mkdtemp()

    def test_a_passing_command_returns_rc_zero_and_captures_output(self):
        res = gate.run_commands(["echo hello"], self.cwd, 30, self.out, "T1")
        self.assertEqual(1, len(res))
        self.assertEqual(0, res[0].rc)
        self.assertFalse(res[0].timed_out)
        self.assertTrue(gate.all_ok(res))
        with open(res[0].output_path) as f:
            body = f.read()
        self.assertIn("hello", body)
        self.assertIn("$ echo hello", body)

    def test_output_files_are_named_gate_tag_n(self):
        res = gate.run_commands(["true", "true"], self.cwd, 30, self.out, "render-T60")
        self.assertEqual(os.path.join(self.out, "gate-render-T60-1.txt"), res[0].output_path)
        self.assertEqual(os.path.join(self.out, "gate-render-T60-2.txt"), res[1].output_path)

    def test_stderr_is_captured_too(self):
        res = gate.run_commands(["echo oops >&2"], self.cwd, 30, self.out, "T1")
        with open(res[0].output_path) as f:
            self.assertIn("oops", f.read())

    def test_a_failing_command_stops_the_run(self):
        res = gate.run_commands(["exit 3", "echo never"], self.cwd, 30, self.out, "T1")
        self.assertEqual(1, len(res))
        self.assertEqual(3, res[0].rc)
        self.assertFalse(gate.all_ok(res))

    def test_commands_run_in_the_given_cwd(self):
        with open(os.path.join(self.cwd, "marker.txt"), "w") as f:
            f.write("x")
        res = gate.run_commands(["ls marker.txt"], self.cwd, 30, self.out, "T1")
        self.assertEqual(0, res[0].rc)

    def test_a_hanging_command_times_out_with_rc_124(self):
        res = gate.run_commands(["sleep 30"], self.cwd, 1, self.out, "T1")
        self.assertTrue(res[0].timed_out)
        self.assertEqual(124, res[0].rc)
        self.assertLess(res[0].duration_s, 20)
        with open(res[0].output_path) as f:
            self.assertIn("timed out", f.read())

    def test_a_timeout_kills_the_whole_process_group(self):
        marker = os.path.join(self.cwd, "child-alive")
        cmd = "(sleep 20; touch %s) & sleep 20" % marker
        gate.run_commands([cmd], self.cwd, 1, self.out, "T1")
        self.assertFalse(os.path.exists(marker))

    def test_an_empty_command_list_is_vacuously_ok(self):
        res = gate.run_commands([], self.cwd, 30, self.out, "T1")
        self.assertEqual([], res)
        self.assertTrue(gate.all_ok(res))

    def test_the_out_dir_is_created_when_missing(self):
        target = os.path.join(self.out, "deep", "er")
        gate.run_commands(["true"], self.cwd, 30, target, "T1")
        self.assertTrue(os.path.isdir(target))


class TestOutputsText(unittest.TestCase):
    def setUp(self):
        self.cwd = tempfile.mkdtemp()
        self.out = tempfile.mkdtemp()

    def test_renders_command_rc_and_a_bounded_tail(self):
        res = gate.run_commands(["echo one", "echo two"], self.cwd, 30, self.out, "T1")
        text = gate.outputs_text(res)
        self.assertIn("$ echo one", text)
        self.assertIn("rc=0", text)
        self.assertIn("two", text)

    def test_the_tail_is_capped_per_command(self):
        res = gate.run_commands(["head -c 9000 /dev/zero | tr '\\0' 'x'"],
                                self.cwd, 30, self.out, "T1")
        text = gate.outputs_text(res, limit=500)
        self.assertLess(len(text), 1500)
        self.assertIn("truncated", text)

    def test_no_results_renders_a_placeholder(self):
        self.assertIn("none", gate.outputs_text([]))


if __name__ == "__main__":
    unittest.main()
