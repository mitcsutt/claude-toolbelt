import _path  # noqa: F401
import json
import os
import tempfile
import time
import unittest

from runner import harness, util


class TestLock(unittest.TestCase):
    def setUp(self):
        self.rt = tempfile.mkdtemp()

    def test_a_free_dir_is_acquired_and_the_payload_names_the_owner(self):
        self.assertEqual("acquired",
                         harness.lock_acquire(self.rt, 4242, "/loop", "3.0.0",
                                              alive=lambda pid: False))
        rec = util.read_json(os.path.join(self.rt, "harness.json"))
        self.assertEqual(4242, rec["pid"])
        self.assertEqual("/loop", rec["loop_dir"])
        self.assertEqual("3.0.0", rec["plugin_version"])
        self.assertIsInstance(rec["start_epoch"], int)
        self.assertTrue(rec["host"])

    def test_a_live_owner_is_held_with_its_pid_and_start(self):
        harness.lock_acquire(self.rt, 111, "/loop", "3.0.0", alive=lambda pid: False)
        out = harness.lock_acquire(self.rt, 222, "/loop", "3.0.0", alive=lambda pid: True)
        self.assertTrue(out.startswith("held:111:"), out)
        self.assertEqual(111, util.read_json(os.path.join(self.rt, "harness.json"))["pid"])

    def test_a_dead_owner_is_taken_over(self):
        harness.lock_acquire(self.rt, 111, "/loop", "3.0.0", alive=lambda pid: False)
        out = harness.lock_acquire(self.rt, 222, "/loop", "3.0.0", alive=lambda pid: False)
        self.assertEqual("takeover:111", out)
        self.assertEqual(222, util.read_json(os.path.join(self.rt, "harness.json"))["pid"])

    def test_an_unreadable_lock_is_taken_over_too(self):
        util.atomic_write(os.path.join(self.rt, "harness.json"), "{garbage")
        out = harness.lock_acquire(self.rt, 222, "/loop", "3.0.0", alive=lambda pid: False)
        self.assertTrue(out.startswith("takeover:"), out)

    def test_the_lock_and_its_payload_appear_together(self):
        """There is no instant at which the lock exists without its owner record:
        it is link(2)ed into place from a fully written private file."""
        seen = {}

        def alive(pid):
            seen["at_check"] = util.read_json(os.path.join(self.rt, "harness.json"))
            return True

        harness.lock_acquire(self.rt, 111, "/loop", "3.0.0", alive=lambda p: False)
        harness.lock_acquire(self.rt, 222, "/loop", "3.0.0", alive=alive)
        self.assertEqual(111, seen["at_check"]["pid"])

    def test_release_only_removes_our_own_lock(self):
        harness.lock_acquire(self.rt, 111, "/loop", "3.0.0", alive=lambda pid: False)
        harness.lock_release(self.rt, 999)
        self.assertTrue(os.path.exists(os.path.join(self.rt, "harness.json")))
        harness.lock_release(self.rt, 111)
        self.assertFalse(os.path.exists(os.path.join(self.rt, "harness.json")))

    def test_release_is_safe_when_there_is_no_lock(self):
        harness.lock_release(self.rt, 111)

    def test_no_private_temp_files_are_left_behind(self):
        harness.lock_acquire(self.rt, 111, "/loop", "3.0.0", alive=lambda pid: False)
        harness.lock_acquire(self.rt, 222, "/loop", "3.0.0", alive=lambda pid: True)
        leftovers = [f for f in os.listdir(self.rt) if f.startswith(".harness.")]
        self.assertEqual([], leftovers)


class TestHarnessAlive(unittest.TestCase):
    def test_it_calls_process_alive_with_the_run_sh_token(self):
        original = util.process_alive
        self.addCleanup(setattr, util, "process_alive", original)
        calls = []

        def stub_true(pid, must_contain=None):
            calls.append((pid, must_contain))
            return True

        util.process_alive = stub_true
        self.assertTrue(harness.harness_alive(1234))
        self.assertEqual([(1234, "run.sh")], calls)

        calls.clear()

        def stub_false(pid, must_contain=None):
            calls.append((pid, must_contain))
            return False

        util.process_alive = stub_false
        self.assertFalse(harness.harness_alive(1234))
        self.assertEqual([(1234, "run.sh")], calls)


class TestHeartbeat(unittest.TestCase):
    def test_it_stamps_an_epoch_and_stops_on_request(self):
        rt = tempfile.mkdtemp()
        hb = harness.Heartbeat(rt, interval=0.05)
        hb.start()
        try:
            deadline = time.time() + 3
            while time.time() < deadline and not os.path.exists(os.path.join(rt, "HEARTBEAT")):
                time.sleep(0.02)
            first = util.read_int(os.path.join(rt, "HEARTBEAT"))
            self.assertGreater(first, 0)
        finally:
            hb.stop()
        self.assertFalse(hb.thread.is_alive())

    def test_stop_is_idempotent(self):
        hb = harness.Heartbeat(tempfile.mkdtemp(), interval=0.05)
        hb.start()
        hb.stop()
        hb.stop()

    def test_a_write_failure_skips_a_beat_instead_of_killing_the_thread(self):
        parent = tempfile.mkdtemp()
        blocked = os.path.join(parent, "runtime")
        with open(blocked, "w") as f:
            f.write("not a directory")
        hb = harness.Heartbeat(blocked, interval=0.05)
        hb.start()
        try:
            time.sleep(0.2)
            self.assertTrue(hb.thread.is_alive())
        finally:
            hb.stop()
        self.assertFalse(hb.thread.is_alive())


class TestTickJson(unittest.TestCase):
    def test_written_while_a_tick_runs_and_removed_after(self):
        rt = tempfile.mkdtemp()
        harness.write_tick_json(rt, 7, 4242, 1700000000, 1800)
        rec = util.read_json(os.path.join(rt, "tick.json"))
        self.assertEqual({"tick": 7, "pid": 4242, "started_at": 1700000000,
                          "timeout_s": 1800}, rec)
        harness.clear_tick_json(rt)
        self.assertFalse(os.path.exists(os.path.join(rt, "tick.json")))

    def test_garbage_inputs_degrade_to_zero_rather_than_raising(self):
        rt = tempfile.mkdtemp()
        harness.write_tick_json(rt, "x", None, "y", None)
        self.assertEqual({"tick": 0, "pid": 0, "started_at": 0, "timeout_s": 0},
                         util.read_json(os.path.join(rt, "tick.json")))


class TestRotatingLog(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.path = os.path.join(self.d, "harness.log")

    def test_lines_are_appended_with_a_newline(self):
        log = harness.RotatingLog(self.path)
        log.write("first")
        log.write("second")
        with open(self.path) as f:
            self.assertEqual(["first\n", "second\n"], f.readlines())

    def test_it_rotates_at_the_size_cap_and_keeps_n_backups(self):
        log = harness.RotatingLog(self.path, max_bytes=100, backups=2)
        for i in range(60):
            log.write("x" * 20)
        self.assertTrue(os.path.exists(self.path + ".1"))
        self.assertTrue(os.path.exists(self.path + ".2"))
        self.assertFalse(os.path.exists(self.path + ".3"))
        self.assertLessEqual(os.path.getsize(self.path), 200)

    def test_the_newest_content_stays_in_the_live_file(self):
        log = harness.RotatingLog(self.path, max_bytes=50, backups=1)
        log.write("old" * 30)
        log.write("newest")
        with open(self.path) as f:
            self.assertIn("newest", f.read())


class TestMemoryGuard(unittest.TestCase):
    def test_headroom_returns_two_non_negative_integers(self):
        free_mb, swap_pct = harness.mem_headroom()
        self.assertIsInstance(free_mb, int)
        self.assertIsInstance(swap_pct, int)
        self.assertGreaterEqual(free_mb, 0)
        self.assertGreaterEqual(swap_pct, 0)

    def test_both_conditions_must_hold_before_it_delays(self):
        self.assertEqual("delay", harness.mem_guard_action(100, 95, 1024, 90))
        self.assertEqual("proceed", harness.mem_guard_action(100, 10, 1024, 90))
        self.assertEqual("proceed", harness.mem_guard_action(8000, 95, 1024, 90))

    def test_a_zero_ceiling_always_trips(self):
        self.assertEqual("delay", harness.mem_guard_action(0, 0, 999999999, 0))


class TestRateLimitAction(unittest.TestCase):
    def test_no_info_is_ok(self):
        self.assertEqual("ok", harness.ratelimit_action(None, 1000, 21600))
        self.assertEqual("ok", harness.ratelimit_action({}, 1000, 21600))

    def test_any_allowed_status_is_ok(self):
        for status in ("allowed", "allowed_warning"):
            self.assertEqual("ok", harness.ratelimit_action(
                {"status": status, "resetsAt": 9999999999}, 1000, 21600))

    def test_a_rejected_status_with_a_near_reset_waits(self):
        out = harness.ratelimit_action({"status": "rejected", "resetsAt": 1300}, 1000, 21600)
        self.assertEqual("wait 360", out)

    def test_a_reset_already_past_waits_the_floor(self):
        self.assertEqual("wait 5", harness.ratelimit_action(
            {"status": "rejected", "resetsAt": 1}, 1000, 21600))

    def test_a_reset_beyond_max_wait_exits(self):
        self.assertEqual("exit", harness.ratelimit_action(
            {"status": "rejected", "resetsAt": 9999999999}, 1000, 21600))

    def test_a_rejection_without_a_reset_exits(self):
        self.assertEqual("exit", harness.ratelimit_action({"status": "rejected"}, 1000, 21600))


class TestBackoff(unittest.TestCase):
    def test_it_doubles_and_caps(self):
        self.assertEqual(2, harness.backoff_delay(0))
        self.assertEqual(4, harness.backoff_delay(1))
        self.assertEqual(8, harness.backoff_delay(2))
        self.assertEqual(300, harness.backoff_delay(20))

    def test_a_negative_attempt_still_returns_at_least_one_second(self):
        self.assertEqual(1, harness.backoff_delay(-5))


if __name__ == "__main__":
    unittest.main()
