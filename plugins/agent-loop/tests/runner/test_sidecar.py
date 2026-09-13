import _path  # noqa: F401
import os
import stat
import subprocess
import tempfile
import time
import unittest

from runner import sidecar, util

STUB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "fixtures", "dashboard-stub")


class Base(unittest.TestCase):
    def setUp(self):
        self.rt = tempfile.mkdtemp()
        self.spawned = []

    def tearDown(self):
        for proc in self.spawned:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
        sidecar.stop(self.rt)

    def fake_serve(self):
        """A live process whose command line contains serve.py."""
        proc = subprocess.Popen(["bash", "-c", "exec -a serve.py sleep 60"])
        self.spawned.append(proc)
        time.sleep(0.2)
        return proc

    def make_stub(self, sleep_s):
        """A tiny standalone script: announce dashboard-started, stay up sleep_s,
        then exit -- lets tests control exactly how long a "healthy" sidecar runs
        without depending on wall-clock luck."""
        path = os.path.join(self.rt, "stub-%s.sh" % sleep_s)
        script = ("#!/usr/bin/env bash\n"
                  "printf '%%s\\n' "
                  "'{\"type\":\"dashboard-started\",\"url\":\"http://127.0.0.1:1\"}'\n"
                  "sleep %s\n" % sleep_s)
        with open(path, "w") as f:
            f.write(script)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        return path


class TestAdoptable(Base):
    def test_a_live_standalone_dashboard_is_adopted(self):
        proc = self.fake_serve()
        util.write_json(os.path.join(self.rt, "dashboard.json"),
                        {"pid": proc.pid, "port": 7, "url": "http://127.0.0.1:7",
                         "sidecar": False})
        self.assertEqual("http://127.0.0.1:7", sidecar.adoptable(self.rt))

    def test_a_sidecar_record_is_never_adopted(self):
        proc = self.fake_serve()
        util.write_json(os.path.join(self.rt, "dashboard.json"),
                        {"pid": proc.pid, "url": "http://127.0.0.1:9", "sidecar": True})
        self.assertIsNone(sidecar.adoptable(self.rt))

    def test_a_dead_pid_is_not_adopted(self):
        util.write_json(os.path.join(self.rt, "dashboard.json"),
                        {"pid": 999999, "url": "http://x", "sidecar": False})
        self.assertIsNone(sidecar.adoptable(self.rt))

    def test_a_pid_that_is_not_serve_py_is_not_adopted(self):
        util.write_json(os.path.join(self.rt, "dashboard.json"),
                        {"pid": os.getpid(), "url": "http://x", "sidecar": False})
        self.assertIsNone(sidecar.adoptable(self.rt))

    def test_no_record_and_a_garbage_record_are_both_none(self):
        self.assertIsNone(sidecar.adoptable(self.rt))
        util.atomic_write(os.path.join(self.rt, "dashboard.json"), "{nope")
        self.assertIsNone(sidecar.adoptable(self.rt))

    def test_a_record_missing_a_url_is_not_adopted(self):
        proc = self.fake_serve()
        util.write_json(os.path.join(self.rt, "dashboard.json"),
                        {"pid": proc.pid, "sidecar": False})
        self.assertIsNone(sidecar.adoptable(self.rt))


class TestStartStop(Base):
    def test_it_spawns_the_override_records_the_pid_and_returns_the_url(self):
        url = sidecar.start("/plugin", "ld", self.rt, cmd_override=STUB, wait_s=10)
        self.assertEqual("http://127.0.0.1:1", url)
        pid = util.read_int(os.path.join(self.rt, "sidecar.pid"))
        self.assertTrue(util.process_alive(pid))
        sidecar.stop(self.rt)
        time.sleep(0.3)
        self.assertFalse(util.process_alive(pid))
        self.assertFalse(os.path.exists(os.path.join(self.rt, "sidecar.pid")))

    def test_a_command_that_announces_nothing_returns_none(self):
        self.assertIsNone(sidecar.start("/plugin", "ld", self.rt,
                                        cmd_override="sleep 5", wait_s=1))

    def test_the_previous_port_is_reused_so_an_open_tab_reconnects(self):
        util.write_json(os.path.join(self.rt, "dashboard.json"), {"port": 8899})
        argv = sidecar.build_argv("/plugin", "ld", self.rt, None)
        self.assertEqual("8899", argv[argv.index("--port") + 1])
        self.assertIn("--sidecar", argv)
        self.assertIn("--no-spawn", argv)
        self.assertTrue(argv[1].endswith(os.path.join("web", "serve.py")))

    def test_stop_is_safe_with_no_sidecar(self):
        sidecar.stop(self.rt)

    def test_start_kills_a_stale_sidecar_left_by_a_crashed_harness(self):
        """sidecar.pid can outlive an unclean harness exit (no stop() call). The
        next start() for the same runtime dir must not leave that orphan running
        alongside the fresh one -- exactly-one-dashboard has to survive a crash,
        not just a clean shutdown."""
        first_url = sidecar.start("/plugin", "ld", self.rt, cmd_override=STUB, wait_s=10)
        self.assertIsNotNone(first_url)
        first_pid = util.read_int(os.path.join(self.rt, "sidecar.pid"))
        self.assertTrue(util.process_alive(first_pid))

        second_url = sidecar.start("/plugin", "ld", self.rt, cmd_override=STUB, wait_s=10)
        self.assertIsNotNone(second_url)
        second_pid = util.read_int(os.path.join(self.rt, "sidecar.pid"))
        self.assertNotEqual(first_pid, second_pid)

        deadline = time.time() + 5
        while time.time() < deadline and util.process_alive(first_pid):
            time.sleep(0.1)
        self.assertFalse(util.process_alive(first_pid),
                         "the orphaned sidecar from the first start() was never killed")
        self.assertTrue(util.process_alive(second_pid))


class TestSupervisor(Base):
    def test_it_respawns_a_dead_sidecar(self):
        sidecar.start("/plugin", "ld", self.rt, cmd_override=STUB, wait_s=10)
        first = util.read_int(os.path.join(self.rt, "sidecar.pid"))
        sup = sidecar.Supervisor(self.rt, "/plugin", "ld", lambda n: None,
                                 interval=0.2, cmd_override=STUB)
        sup.start()
        try:
            os.kill(first, 9)
            deadline = time.time() + 10
            second = first
            while time.time() < deadline and second == first:
                time.sleep(0.2)
                second = util.read_int(os.path.join(self.rt, "sidecar.pid"))
            self.assertNotEqual(first, second)
            self.assertTrue(util.process_alive(second))
        finally:
            sup.stop()

    def test_the_restart_ceiling_reports_a_crashloop_and_gives_up(self):
        seen = []
        sup = sidecar.Supervisor(self.rt, "/plugin", "ld", seen.append,
                                 interval=0.05, max_restarts=2, cmd_override="true")
        sup.start()
        deadline = time.time() + 10
        while time.time() < deadline and not seen:
            time.sleep(0.1)
        sup.stop()
        self.assertEqual([3], seen)

    def test_stop_takes_the_sidecar_down_with_it(self):
        sup = sidecar.Supervisor(self.rt, "/plugin", "ld", lambda n: None,
                                 interval=0.2, cmd_override=STUB)
        sidecar.start("/plugin", "ld", self.rt, cmd_override=STUB, wait_s=10)
        pid = util.read_int(os.path.join(self.rt, "sidecar.pid"))
        sup.start()
        sup.stop()
        time.sleep(0.3)
        self.assertFalse(util.process_alive(pid))

    def test_stop_is_idempotent_and_returns_promptly_even_unstarted(self):
        sup = sidecar.Supervisor(self.rt, "/plugin", "ld", lambda n: None,
                                 interval=0.2, cmd_override=STUB)
        started_at = time.time()
        sup.stop()   # never started
        sup.stop()   # repeat call
        self.assertLess(time.time() - started_at, 5)

    def test_a_sidecar_that_ran_a_good_while_does_not_feed_an_old_crashloop(self):
        """restarts must not accumulate across a healthy run: a dashboard that
        announces, stays up well past the stability window, and only then dies
        is a fresh failure, not the (n+1)th strike of an old crash loop. The
        stub always sleeps a full real second after announcing, so every cycle
        clears the (deliberately much smaller) stability window regardless of
        machine speed; the test only asserts the invariant (restarts pinned at
        <= 1 across arbitrarily many cycles), never a cycle count or timing."""
        seen = []
        healthy = self.make_stub(1.0)
        sup = sidecar.Supervisor(self.rt, "/plugin", "ld", seen.append,
                                 interval=0.1, max_restarts=1, cmd_override=healthy,
                                 stable_after=0.3)
        sup.start()
        try:
            time.sleep(3.5)
        finally:
            sup.stop()
        self.assertEqual([], seen, "the old restart count leaked across a healthy run")
        self.assertLessEqual(sup.restarts, 1)


if __name__ == "__main__":
    unittest.main()
