import _path  # noqa: F401
import os
import subprocess
import tempfile
import unittest

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUN_SH = os.path.join(PLUGIN_ROOT, "run.sh")


class TestShim(unittest.TestCase):
    def run_shim(self, cwd, env_extra=None, args=None):
        env = dict(os.environ)
        env["LOOP_NOTIFY"] = "0"
        env.update(env_extra or {})
        return subprocess.run(["bash", RUN_SH] + (args or []), cwd=cwd, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_it_reaches_python_and_reports_the_missing_config(self):
        d = tempfile.mkdtemp()
        proc = self.run_shim(d, {"LOOP_DIR": os.path.join(d, "loop")})
        self.assertEqual(1, proc.returncode)
        self.assertIn("no readable LOOP_CONFIG", proc.stderr.decode())

    def test_the_package_imports_without_the_plugin_root_on_the_callers_path(self):
        d = tempfile.mkdtemp()
        proc = self.run_shim(d, {"LOOP_DIR": os.path.join(d, "loop"), "PYTHONPATH": ""})
        self.assertNotIn("ImportError", proc.stderr.decode())
        self.assertNotIn("ModuleNotFoundError", proc.stderr.decode())

    def test_the_shim_path_is_on_the_command_line_so_liveness_still_works(self):
        with open(RUN_SH) as f:
            body = f.read()
        self.assertIn("--shim", body)
        self.assertIn("run.sh", body.split("--shim", 1)[1])

    def test_run_sh_is_executable(self):
        self.assertTrue(os.access(RUN_SH, os.X_OK))

    def test_the_deleted_bash_harness_is_really_gone(self):
        for stale in ("lib/loop.sh", "lib/harness.sh", "lib/events.sh",
                      "lib/migrate.sh", "tick-prompt.md",
                      "tests/lib.test.sh", "tests/harness.test.sh",
                      "tests/events.test.sh", "tests/migrate.test.sh",
                      "tests/tick-prompt.contract.sh"):
            self.assertFalse(os.path.exists(os.path.join(PLUGIN_ROOT, stale)), stale)


if __name__ == "__main__":
    unittest.main()
