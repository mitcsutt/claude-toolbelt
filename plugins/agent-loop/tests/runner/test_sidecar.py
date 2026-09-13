import _path  # noqa: F401
import os
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

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
        proc = subprocess.Popen(["bash", "-c", "exec -a serve.py sleep 60"],
                                start_new_session=True)
        self.spawned.append(proc)
        time.sleep(0.2)
        return proc

    def stranger(self):
        """A live process standing in for whatever the OS reassigns a stale
        recorded pid to -- something that is definitely not our sidecar.
        Given its own session/process group (real orphans and pid-reuse
        victims always are one), so that if a bug under test ever does call
        killpg on it, the blast radius is this one process, never ours."""
        proc = subprocess.Popen(["bash", "-c", "exec -a totally-unrelated-process sleep 60"],
                                start_new_session=True)
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

    def test_stop_never_kills_a_pid_reused_by_an_unrelated_process(self):
        """sidecar.pid is read from disk, not held live -- across a harness
        crash and restart the OS is free to hand that same number to some
        other process entirely. A stale record pointing at it must never be
        signalled just because the pid still happens to be alive."""
        victim = self.stranger()
        util.atomic_write(os.path.join(self.rt, "sidecar.pid"), str(victim.pid))
        util.atomic_write(os.path.join(self.rt, "sidecar.marker"), "dashboard-stub")

        sidecar.stop(self.rt)
        time.sleep(0.3)

        self.assertTrue(util.process_alive(victim.pid),
                         "stop() killed a process whose identity it never verified")
        self.assertFalse(os.path.exists(os.path.join(self.rt, "sidecar.pid")),
                         "an unverifiable record should still be dropped")

    def test_stop_refuses_to_signal_when_the_marker_is_missing(self):
        """A pid file with no matching marker (e.g. a record predating this
        check, or simply lost) is unverifiable -- fail closed, never signal."""
        victim = self.stranger()
        util.atomic_write(os.path.join(self.rt, "sidecar.pid"), str(victim.pid))
        # deliberately no sidecar.marker written

        sidecar.stop(self.rt)
        time.sleep(0.3)

        self.assertTrue(util.process_alive(victim.pid))

    def test_stop_still_kills_a_genuinely_matching_sidecar(self):
        """The identity check must not be so strict it refuses a real one."""
        url = sidecar.start("/plugin", "ld", self.rt, cmd_override=STUB, wait_s=10)
        self.assertIsNotNone(url)
        pid = util.read_int(os.path.join(self.rt, "sidecar.pid"))
        sidecar.stop(self.rt)
        time.sleep(0.3)
        self.assertFalse(util.process_alive(pid))

    def test_start_does_not_touch_a_stranger_holding_the_recorded_pid(self):
        """The exact scenario start()'s stop()-first call must survive: the
        pid it inherits from a stale record has been reassigned to something
        else entirely. That something else must be left running, and a fresh
        sidecar must still come up."""
        victim = self.stranger()
        util.atomic_write(os.path.join(self.rt, "sidecar.pid"), str(victim.pid))
        util.atomic_write(os.path.join(self.rt, "sidecar.marker"), "dashboard-stub")

        url = sidecar.start("/plugin", "ld", self.rt, cmd_override=STUB, wait_s=10)

        self.assertIsNotNone(url)
        self.assertTrue(util.process_alive(victim.pid),
                         "start()'s stop()-first call killed an unrelated process")
        new_pid = util.read_int(os.path.join(self.rt, "sidecar.pid"))
        self.assertNotEqual(victim.pid, new_pid)
        self.assertTrue(util.process_alive(new_pid))


class TestSupervisor(Base):
    def test_it_respawns_a_dead_sidecar(self):
        sidecar.start("/plugin", "ld", self.rt, cmd_override=STUB, wait_s=10)
        first = util.read_int(os.path.join(self.rt, "sidecar.pid"))
        sup = sidecar.Supervisor(self.rt, "/plugin", "ld", lambda n: None,
                                 interval=0.2, cmd_override=STUB)
        sup.start()
        try:
            os.kill(first, 9)
            # Read-then-check-liveness has a TOCTOU: under load the
            # supervisor can already be a cycle or two ahead by the time this
            # thread gets scheduled again, so a pid observed once may already
            # have been superseded (and killed) by the next respawn before
            # the liveness check runs. Require the SAME pid on two
            # consecutive polls, both alive, before trusting it -- a real
            # respawned sidecar stays up for the rest of the test (STUB sleeps
            # 3600s), so it will always pass that bar; a fleeting intermediate
            # one won't.
            deadline = time.time() + 10
            previous = None
            confirmed = None
            while time.time() < deadline and confirmed is None:
                time.sleep(0.2)
                candidate = util.read_int(os.path.join(self.rt, "sidecar.pid"))
                if (candidate != first and candidate == previous
                        and util.process_alive(candidate)):
                    confirmed = candidate
                previous = candidate
            self.assertIsNotNone(confirmed,
                                 "no stable respawned pid observed within the deadline")
            self.assertNotEqual(first, confirmed)
            self.assertTrue(util.process_alive(confirmed))
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

    def test_a_periodically_dying_sidecar_still_trips_a_windowed_ceiling(self):
        """The consecutive counter resets whenever a cycle outlives
        stable_after -- exactly gameable: a sidecar that reliably dies just
        after that window, forever, would keep `restarts` pinned low and
        `on_crashloop` would never fire, spinning fresh processes all night
        with zero visibility. A ceiling on restarts within a fixed trailing
        window can't be reset by the same trick, since it doesn't care
        whether any individual cycle looked stable. `max_restarts` is set far
        out of reach here so only the windowed path can plausibly fire,
        isolating it; the assertion is a bound (>= max_in_window + 1, the
        earliest it could possibly trip), never an exact timing."""
        seen = []
        dying = self.make_stub(0.15)
        sup = sidecar.Supervisor(self.rt, "/plugin", "ld", seen.append,
                                 interval=0.05, max_restarts=100,
                                 cmd_override=dying, stable_after=0.1,
                                 window_s=5.0, max_in_window=3)
        sup.start()
        try:
            deadline = time.time() + 20
            while time.time() < deadline and not seen:
                time.sleep(0.1)
        finally:
            sup.stop()
        self.assertTrue(seen, "the windowed ceiling never tripped -- the reset is gameable")
        self.assertGreaterEqual(seen[0], 4)
        self.assertLessEqual(sup.restarts, 2,
                             "the consecutive counter climbed -- this wasn't the window path")

    def test_run_survives_a_transient_exception_in_tick(self):
        """Regression for the same bug class already fixed in harness.py's
        Heartbeat: an uncaught exception inside one supervision check must
        not silently end the thread, or the harness keeps believing a
        dashboard is supervised when nothing is watching it any more.
        Removing _run's try/except turns this into a straight failure: the
        thread dies on the injected fault and never ticks again."""
        sidecar.start("/plugin", "ld", self.rt, cmd_override=STUB, wait_s=10)
        first = util.read_int(os.path.join(self.rt, "sidecar.pid"))
        sup = sidecar.Supervisor(self.rt, "/plugin", "ld", lambda n: None,
                                 interval=0.1, cmd_override=STUB)

        real_read_int = util.read_int
        calls = {"n": 0}
        fired = threading.Event()

        def flaky_read_int(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                fired.set()
                raise RuntimeError("injected fault")
            return real_read_int(*args, **kwargs)

        try:
            with mock.patch.object(util, "read_int", side_effect=flaky_read_int):
                sup.start()
                self.assertTrue(fired.wait(timeout=5), "the injected fault was never hit")
                # A guarded thread survives an exception inside one tick and
                # keeps running; an unguarded one dies on it. Assert directly
                # on thread liveness (a bound), not on a sleep followed by a
                # hopeful check.
                time.sleep(0.3)
                self.assertTrue(sup.thread.is_alive(),
                                "the supervisor thread died on the injected fault")
            # The mock is gone here; util.read_int is real again. Kill the
            # sidecar and require an actual respawn -- proof the thread is
            # still doing its job, not merely still technically alive.
            os.kill(first, 9)
            deadline = time.time() + 10
            previous = None
            confirmed = None
            while time.time() < deadline and confirmed is None:
                time.sleep(0.1)
                candidate = util.read_int(os.path.join(self.rt, "sidecar.pid"))
                if (candidate != first and candidate == previous
                        and util.process_alive(candidate)):
                    confirmed = candidate
                previous = candidate
            self.assertIsNotNone(
                confirmed, "the supervisor thread never recovered from the injected fault")
        finally:
            sup.stop()


if __name__ == "__main__":
    unittest.main()
