import _path  # noqa: F401
import json
import os
import subprocess
import tempfile
import unittest

from runner import claude_proc, config, harness, migrate, run, util


def sh(cwd, *args):
    subprocess.run(list(args), cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Base(unittest.TestCase):
    def setUp(self):
        self.wt = os.path.realpath(tempfile.mkdtemp())
        sh(self.wt, "git", "init", "-q")
        sh(self.wt, "git", "config", "user.email", "loop@example.com")
        sh(self.wt, "git", "config", "user.name", "Loop")
        sh(self.wt, "git", "config", "commit.gpgsign", "false")
        util.atomic_write(os.path.join(self.wt, "README.md"), "seed\n")
        sh(self.wt, "git", "add", "-A")
        sh(self.wt, "git", "commit", "-q", "-m", "seed")

        self.ld = ".claude/loop/test-run"
        self.loop_dir = os.path.join(self.wt, self.ld)
        self.runtime = os.path.join(self.loop_dir, "runtime")
        os.makedirs(self.runtime)
        util.atomic_write(os.path.join(self.loop_dir, "LOOP_CONFIG.md"),
                          "Worktree: %s\nDashboard: off\nMedic: off\n" % self.wt)
        self.write_plan("## S\nReviewed: aaa\n- [x] T1: done already\n")
        self.old_cwd = os.getcwd()
        self.old_env = dict(os.environ)
        os.chdir(self.wt)
        os.environ["LOOP_DIR"] = self.ld
        os.environ["LOOP_NOTIFY"] = "0"
        os.environ["AGENT_LOOP_SKIP_POSTMORTEM"] = "1"
        os.environ["PAUSE_BETWEEN"] = "0"
        self._real = claude_proc.run_phase
        claude_proc.run_phase = self._never_called

    def tearDown(self):
        claude_proc.run_phase = self._real
        os.chdir(self.old_cwd)
        os.environ.clear()
        os.environ.update(self.old_env)

    def _never_called(self, **kw):
        raise AssertionError("no phase should have run: %s" % kw.get("phase"))

    def write_plan(self, text):
        util.atomic_write(os.path.join(self.loop_dir, "LOOP_PLAN.md"), text)

    def events(self, type_=None):
        path = os.path.join(self.loop_dir, "events.jsonl")
        out = []
        if not os.path.exists(path):
            return out
        with open(path) as f:
            for line in f:
                rec = json.loads(line)
                if type_ is None or rec["type"] == type_:
                    out.append(rec)
        return out


class TestParseArgs(unittest.TestCase):
    def test_shim_is_captured_and_everything_else_is_ignored(self):
        args = run.parse_args(["--shim", "/p/run.sh", "--whatever"])
        self.assertEqual("/p/run.sh", args["shim"])
        self.assertEqual(["--whatever"], args["rest"])

    def test_no_arguments_is_fine(self):
        self.assertEqual("", run.parse_args([])["shim"])


class TestStartupRefusals(Base):
    def test_a_missing_config_exits_one_and_writes_no_events(self):
        os.unlink(os.path.join(self.loop_dir, "LOOP_CONFIG.md"))
        self.assertEqual(run.EXIT_HALT, run.main([]))
        self.assertEqual([], self.events())

    def test_running_outside_the_worktree_exits_one(self):
        os.chdir(tempfile.mkdtemp())
        os.environ["CONFIG_PATH"] = os.path.join(self.loop_dir, "LOOP_CONFIG.md")
        os.environ["LOOP_DIR"] = self.loop_dir
        self.assertEqual(run.EXIT_HALT, run.main([]))

    def test_a_live_owner_exits_three_with_an_incident_and_no_loop_end(self):
        harness.lock_acquire(self.runtime, 4242, self.ld, "3.0.0",
                             alive=lambda pid: False)
        original = harness.harness_alive
        harness.harness_alive = lambda pid: True
        try:
            self.assertEqual(run.EXIT_LOCK, run.main([]))
        finally:
            harness.harness_alive = original
        self.assertEqual("lock-conflict", self.events("incident")[0]["kind"])
        self.assertEqual("warn", self.events("incident")[0]["severity"])
        self.assertEqual([], self.events("loop_end"))
        self.assertEqual(4242, util.read_json(
            os.path.join(self.runtime, "harness.json"))["pid"])


class TestTerminalModes(Base):
    def test_a_finished_plan_exits_zero_with_loop_end_done(self):
        self.assertEqual(run.EXIT_OK, run.main([]))
        end = self.events("loop_end")[-1]
        self.assertEqual("done", end["reason"])
        self.assertEqual(0, end["exit_code"])
        self.assertEqual("loop_start", self.events()[0]["type"])
        self.assertFalse(os.path.exists(os.path.join(self.runtime, "harness.json")))
        self.assertFalse(os.path.exists(os.path.join(self.runtime, "tick.json")))

    def test_a_stuck_plan_exits_one(self):
        self.write_plan("## S\nReviewed: aaa\n- [!] T1: blocked\n"
                        "- [ ] T2: next | depends_on: T1\n")
        self.assertEqual(run.EXIT_HALT, run.main([]))
        self.assertEqual("halt", self.events("loop_end")[-1]["reason"])

    def test_pause_at_the_top_of_the_loop_exits_zero_with_a_checkpoint(self):
        self.write_plan("## S\n- [ ] T1: work to do\n")
        util.atomic_write(os.path.join(self.runtime, "PAUSE"), "")
        self.assertEqual(run.EXIT_OK, run.main([]))
        self.assertEqual("paused", self.events("loop_end")[-1]["reason"])
        self.assertEqual(1, len(self.events("paused")))
        check = util.read_json(os.path.join(self.runtime, "CHECKPOINT.json"))
        self.assertEqual("T1", check["next_task"])
        self.assertEqual("S", check["segment"])
        self.assertIn("resume", check["note"])

    def test_stop_is_treated_like_pause_at_the_top_of_the_loop(self):
        self.write_plan("## S\n- [ ] T1: work to do\n")
        util.atomic_write(os.path.join(self.runtime, "STOP"), "")
        self.assertEqual(run.EXIT_OK, run.main([]))
        self.assertEqual("paused", self.events("loop_end")[-1]["reason"])


class TestSchema(Base):
    def test_a_fresh_dir_is_stamped_with_the_current_schema(self):
        run.main([])
        self.assertEqual(migrate.LOOP_SCHEMA,
                         util.read_int(os.path.join(self.runtime, "schema")))

    def test_a_dir_newer_than_the_plugin_exits_two(self):
        util.atomic_write(os.path.join(self.runtime, "schema"), "99")
        self.assertEqual(run.EXIT_NEEDS_HUMAN, run.main([]))
        body = util.read_text(os.path.join(self.runtime, "NEEDS_HUMAN.md"))
        self.assertIn("update the plugin", body)
        self.assertEqual("needs-human", self.events("loop_end")[-1]["reason"])

    def test_a_live_one_x_harness_blocks_the_start(self):
        util.atomic_write(os.path.join(self.runtime, "tickseq"), "41")
        util.atomic_write(os.path.join(self.loop_dir, "run.log"),
                          "2026-01-01T00:00:00Z tick 41 starting\n")
        self.assertEqual(run.EXIT_NEEDS_HUMAN, run.main([]))
        self.assertIn("LOOP_MIGRATE_FORCE",
                      util.read_text(os.path.join(self.runtime, "NEEDS_HUMAN.md")))
        self.assertFalse(os.path.exists(os.path.join(self.runtime, "schema")))


class TestResumeAndTakeover(Base):
    def test_a_second_run_reports_resume(self):
        util.atomic_write(os.path.join(self.runtime, "tickseq"), "7")
        run.main([])
        self.assertEqual(1, self.events("loop_start")[-1]["resume"])

    def test_a_dead_owner_is_taken_over_and_recorded(self):
        harness.lock_acquire(self.runtime, 999999, self.ld, "2.1.0",
                             alive=lambda pid: False)
        self.assertEqual(run.EXIT_OK, run.main([]))
        self.assertIn("harness-crash", [e["kind"] for e in self.events("incident")])


class TestBootRule(Base):
    """Spec §14 at the outer-loop level: a dir stopped mid-task resumes itself."""

    def _malformed(self, **kw):
        from runner.claude_proc import PhaseResult
        return PhaseResult(phase=kw["phase"], model=kw["model"], rc=0,
                           session_id="s", result_text="no json here")

    def test_an_in_flight_task_is_reconciled_instead_of_reading_as_stuck(self):
        # A `[~]` row is what a dir stopped mid-task looks like. eligible() never
        # returns it, so before the boot rule this exited 1 having touched nothing.
        self.write_plan("## S\nReviewed: aaa\n- [~] T1: in flight\n")
        claude_proc.run_phase = self._malformed
        run.main([])
        self.assertTrue(self.events("tick_start"), "the task was actually attempted")
        self.assertIn("retry", [e["decision"] for e in self.events("decision")])
        self.assertIn("boot-reconcile",
                      util.read_text(os.path.join(self.loop_dir, "harness.log")))

    def test_a_finished_plan_never_triggers_the_boot_rule(self):
        run.main([])
        self.assertNotIn("boot-reconcile",
                         util.read_text(os.path.join(self.loop_dir, "harness.log")))


class TestHelpers(Base):
    def test_sum_usage_folds_every_phase(self):
        class R(object):
            def __init__(self, usage):
                self.usage_by_model = usage
        from runner.claude_proc import Usage
        total = run.sum_usage([R({"m": Usage(cost_usd=1.0, input_tokens=2)}),
                               R({"m": Usage(cost_usd=2.0, input_tokens=3)})])
        self.assertEqual(3.0, total["m"].cost_usd)
        self.assertEqual(5, total["m"].input_tokens)

    def test_usage_payload_is_plain_json(self):
        from runner.claude_proc import Usage
        payload = run.usage_payload({"m": Usage(cost_usd=1.5)})
        self.assertEqual(1.5, payload["m"]["cost_usd"])
        json.dumps(payload)

    def test_env_int_falls_back_on_garbage(self):
        os.environ["SOME_KNOB"] = "not a number"
        self.assertEqual(5, run.env_int("SOME_KNOB", 5))
        os.environ["SOME_KNOB"] = "9"
        self.assertEqual(9, run.env_int("SOME_KNOB", 5))

    def test_plugin_version_reads_the_manifest(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(run.__file__)))
        self.assertNotEqual("0.0.0", run.plugin_version(root))
        self.assertEqual("0.0.0", run.plugin_version("/nowhere"))


if __name__ == "__main__":
    unittest.main()


class TestTeardownIsUnskippable(Base):
    """`loop_end` and the lock release are what the rest of the world reads.

    Teardown runs in a `finally:`, so it always starts -- but its steps were
    sequential and unguarded, and a raise in any of them skipped every later
    one. Stopping a dashboard is the least important thing teardown does;
    losing `loop_end` leaves every observer rendering a finished loop as still
    running, and losing the release leaves the next harness reaping a lock.
    """

    class Boom(object):
        def stop(self):
            raise RuntimeError("dashboard supervisor blew up on the way out")

    def harness_for_teardown(self):
        """A Harness holding a real lock, as `main` would have left one."""
        from runner import events as events_mod
        harness.lock_acquire(self.runtime, os.getpid(), self.loop_dir, "3.0.0")
        h = run.Harness(
            cfg=config.load_config(os.path.join(self.loop_dir, "LOOP_CONFIG.md")),
            plugin_root="/plugin", loop_dir=self.loop_dir, runtime_dir=self.runtime,
            worktree=self.wt,
            config_path=os.path.join(self.loop_dir, "LOOP_CONFIG.md"),
            plan_path=os.path.join(self.loop_dir, "LOOP_PLAN.md"),
            events=events_mod.EventLog(os.path.join(self.loop_dir, "events.jsonl"),
                                       os.path.join(self.runtime, "eventseq")),
            usage=events_mod.UsageLog(os.path.join(self.loop_dir, "LOOP_USAGE.jsonl")),
            log=run.Log(os.path.join(self.loop_dir, "harness.log")))
        h.exit_reason = "done"
        return h, self.Boom()

    def test_a_failure_stopping_the_dashboard_still_ends_the_loop_cleanly(self):
        h, _boom = self.harness_for_teardown()
        run.teardown(h, self.Boom(), self.Boom(), False, run.EXIT_OK)

        kinds = [e.get("type") for e in self.events()]
        self.assertIn("loop_end", kinds,
                      "loop_end was lost, so observers still see a live loop")
        self.assertFalse(
            os.path.exists(os.path.join(h.runtime_dir, harness.LOCK_NAME)),
            "the lock survived teardown and the next harness must reap it")
        self.assertIn("teardown: stopping the dashboard supervisor failed",
                      util.read_text(os.path.join(h.loop_dir, "harness.log")))

    def test_a_failure_in_the_heartbeat_does_not_cost_the_lock(self):
        h, _boom = self.harness_for_teardown()
        run.teardown(h, self.Boom(), None, True, run.EXIT_OK)
        self.assertIn("loop_end", [e.get("type") for e in self.events()])
        self.assertFalse(
            os.path.exists(os.path.join(h.runtime_dir, harness.LOCK_NAME)))
