import _path  # noqa: F401
import json
import os
import stat
import tempfile
import unittest
from unittest import mock

from runner import incidents, util
from runner.config import LoopConfig
from runner.events import EventLog


def medic_script(d, outcome):
    path = os.path.join(d, "medic.sh")
    with open(path, "w") as f:
        f.write(
            "#!/usr/bin/env bash\n"
            'id="${MEDIC_INCIDENT_ID:-}"\n'
            'printf \'{"id":"%%s","kind":"mock","outcome":"%s","actions":[],'
            '"summary":"mock medic %s","human_next_step":"look at the box"}\' '
            '"$id" > "$RUNTIME_DIR/medic-$id.json"\n' % (outcome, outcome))
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.rt = os.path.join(self.d, "runtime")
        os.makedirs(self.rt)
        self.events = EventLog(os.path.join(self.d, "events.jsonl"),
                               os.path.join(self.rt, "eventseq"))
        self.cfg = LoopConfig(worktree=self.d, medic="auto")
        os.environ["RUNTIME_DIR"] = self.rt
        os.environ["LOOP_NOTIFY"] = "0"
        for var in ("LOOP_MEDIC_CMD", "MEDIC_TIMEOUT"):
            os.environ.pop(var, None)

    def tearDown(self):
        for var in ("RUNTIME_DIR", "LOOP_NOTIFY", "LOOP_MEDIC_CMD", "MEDIC_TIMEOUT"):
            os.environ.pop(var, None)

    def emitted(self, type_):
        out = []
        with open(os.path.join(self.d, "events.jsonl")) as f:
            for line in f:
                rec = json.loads(line)
                if rec["type"] == type_:
                    out.append(rec)
        return out


class TestIncidentNew(Base):
    def test_ids_are_sequential_and_persisted(self):
        self.assertEqual("i-001", incidents.incident_new(
            self.rt, self.events, "tick-killed", "error", "rc=137", 3))
        self.assertEqual("i-002", incidents.incident_new(
            self.rt, self.events, "tick-killed", "error", "rc=137", 4))
        self.assertEqual(2, util.read_int(os.path.join(self.rt, "incidentseq")))

    def test_the_file_keeps_the_v2_shape_plus_phases(self):
        incidents.incident_new(self.rt, self.events, "tick-timeout", "error", "slow", 7)
        rec = util.read_json(os.path.join(self.rt, "incident-i-001.json"))
        self.assertEqual("i-001", rec["id"])
        self.assertEqual("tick-timeout", rec["kind"])
        self.assertEqual("error", rec["severity"])
        self.assertEqual("slow", rec["detail"])
        self.assertEqual(7, rec["tick"])
        self.assertIsInstance(rec["t"], int)
        self.assertEqual([], rec["phases"])

    def test_the_event_carries_the_same_fields(self):
        incidents.incident_new(self.rt, self.events, "lock-conflict", "warn", "pid 9", 0)
        ev = self.emitted("incident")[0]
        self.assertEqual(["t", "seq", "type", "id", "kind", "severity", "detail", "tick"],
                         list(ev.keys()))

    def test_a_phase_timeline_is_stored_when_given(self):
        incidents.incident_new(self.rt, self.events, "k", "warn", "d", 1,
                               phases=[{"phase": "worker", "rc": 143}])
        rec = util.read_json(os.path.join(self.rt, "incident-i-001.json"))
        self.assertEqual("worker", rec["phases"][0]["phase"])


class TestNextIdRobustness(Base):
    """Point 5: the counter file is untrusted input, not a source of truth."""

    def test_missing_counter_starts_at_one(self):
        self.assertEqual("i-001", incidents.next_id(self.rt))

    def test_empty_counter_starts_at_one(self):
        open(os.path.join(self.rt, "incidentseq"), "w").close()
        self.assertEqual("i-001", incidents.next_id(self.rt))

    def test_zero_byte_counter_starts_at_one(self):
        with open(os.path.join(self.rt, "incidentseq"), "wb") as f:
            f.write(b"")
        self.assertEqual("i-001", incidents.next_id(self.rt))

    def test_garbage_counter_does_not_collide_with_existing_incidents(self):
        # Two real incidents already on disk (i-001, i-002), counter intact.
        incidents.incident_new(self.rt, self.events, "k", "warn", "d", 1)
        incidents.incident_new(self.rt, self.events, "k", "warn", "d", 1)
        # The counter file is now corrupted back to garbage (util.read_int
        # reads garbage as 0, same as "missing") -- a real failure mode, e.g.
        # a restored backup or a torn write.
        util.atomic_write(os.path.join(self.rt, "incidentseq"), "not-a-number")
        third = incidents.next_id(self.rt)
        self.assertEqual("i-003", third)
        self.assertFalse(os.path.exists(os.path.join(self.rt, "incident-%s.json.tmp" % third)))

    def test_two_calls_in_the_same_run_never_collide(self):
        seen = set()
        for _ in range(5):
            ident = incidents.next_id(self.rt)
            self.assertNotIn(ident, seen)
            seen.add(ident)


class TestMedic(Base):
    def test_a_resumed_medic_lets_the_loop_continue(self):
        os.environ["LOOP_MEDIC_CMD"] = "bash %s" % medic_script(self.d, "resumed")
        state = incidents.MedicState(max_per_run=3)
        ok = incidents.handle_incident(self.rt, self.events, self.cfg,
                                       "tick-killed", "error", "rc=137", 3, state)
        self.assertTrue(ok)
        self.assertEqual("resumed", self.emitted("medic_end")[0]["outcome"])
        self.assertEqual(1, state.count)

    def test_a_paused_medic_stops_the_loop(self):
        os.environ["LOOP_MEDIC_CMD"] = "bash %s" % medic_script(self.d, "paused")
        self.assertFalse(incidents.handle_incident(
            self.rt, self.events, self.cfg, "tick-killed", "error", "rc=137", 3,
            incidents.MedicState()))

    def test_a_noop_medic_still_stops_the_loop(self):
        # Point 1: only a "resumed" verdict continues an error. "noop" means
        # the medic found nothing to fix, which for an error-severity incident
        # is not evidence the run is safe to keep going.
        os.environ["LOOP_MEDIC_CMD"] = "bash %s" % medic_script(self.d, "noop")
        self.assertFalse(incidents.handle_incident(
            self.rt, self.events, self.cfg, "tick-killed", "error", "rc=137", 3,
            incidents.MedicState()))

    def test_a_medic_that_writes_nothing_counts_as_escalated(self):
        os.environ["LOOP_MEDIC_CMD"] = "true"
        self.assertFalse(incidents.handle_incident(
            self.rt, self.events, self.cfg, "tick-killed", "error", "x", 3,
            incidents.MedicState()))
        self.assertEqual("escalated", self.emitted("medic_end")[0]["outcome"])

    def test_a_medic_writing_garbage_json_counts_as_escalated(self):
        path = os.path.join(self.d, "garbage.sh")
        with open(path, "w") as f:
            f.write('#!/usr/bin/env bash\necho "not json" > "$RUNTIME_DIR/medic-$MEDIC_INCIDENT_ID.json"\n')
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        os.environ["LOOP_MEDIC_CMD"] = "bash %s" % path
        self.assertFalse(incidents.handle_incident(
            self.rt, self.events, self.cfg, "tick-killed", "error", "x", 3,
            incidents.MedicState()))
        self.assertEqual("escalated", self.emitted("medic_end")[0]["outcome"])

    def test_a_warn_never_runs_a_medic(self):
        os.environ["LOOP_MEDIC_CMD"] = "bash %s" % medic_script(self.d, "resumed")
        self.assertTrue(incidents.handle_incident(
            self.rt, self.events, self.cfg, "tick-stalled", "warn", "quiet", 3,
            incidents.MedicState()))
        self.assertEqual([], self.emitted("medic_start"))

    def test_medic_off_escalates_immediately(self):
        self.cfg.medic = "off"
        self.assertFalse(incidents.handle_incident(
            self.rt, self.events, self.cfg, "tick-killed", "error", "x", 3,
            incidents.MedicState()))
        self.assertEqual([], self.emitted("medic_start"))

    def test_medic_notify_escalates_without_dispatching(self):
        self.cfg.medic = "notify"
        self.assertFalse(incidents.handle_incident(
            self.rt, self.events, self.cfg, "halt-sentinel", "error", "x", 3,
            incidents.MedicState()))
        self.assertEqual([], self.emitted("medic_start"))

    def test_the_budget_is_a_hard_ceiling(self):
        os.environ["LOOP_MEDIC_CMD"] = "bash %s" % medic_script(self.d, "resumed")
        state = incidents.MedicState(max_per_run=1)
        self.assertTrue(incidents.handle_incident(self.rt, self.events, self.cfg,
                                                  "k", "error", "x", 1, state))
        self.assertFalse(incidents.handle_incident(self.rt, self.events, self.cfg,
                                                   "k", "error", "x", 2, state))
        self.assertEqual(1, len(self.emitted("medic_start")))

    def test_a_garbage_medic_timeout_env_does_not_crash_the_harness(self):
        # MEDIC_TIMEOUT is operator-editable env; garbage in it must not
        # blow up run_medic with an uncaught ValueError.
        os.environ["LOOP_MEDIC_CMD"] = "bash %s" % medic_script(self.d, "resumed")
        os.environ["MEDIC_TIMEOUT"] = "not-a-number"
        self.assertTrue(incidents.handle_incident(
            self.rt, self.events, self.cfg, "tick-killed", "error", "x", 3,
            incidents.MedicState()))


class TestEscalate(Base):
    def test_it_writes_needs_human_with_the_resume_command(self):
        incidents.escalate(self.rt, self.events, "tick-timeout", "rc=124", 9,
                           "/tmp/wt", ".claude/loop/run", "/plugin/run.sh")
        body = util.read_text(os.path.join(self.rt, "NEEDS_HUMAN.md"))
        self.assertIn("tick-timeout", body)
        self.assertIn("rc=124", body)
        self.assertIn("tick: 9", body)
        self.assertIn("/plugin/run.sh", body)
        self.assertIn("LOOP_DIR=.claude/loop/run", body)

    def test_the_escalation_is_itself_a_needs_human_incident(self):
        incidents.escalate(self.rt, self.events, "no-progress", "stuck", 9,
                           "/tmp/wt", "ld", "/plugin/run.sh")
        self.assertEqual("needs-human", self.emitted("incident")[-1]["severity"])

    def test_a_medic_summary_is_carried_into_the_file(self):
        util.write_json(os.path.join(self.rt, "medic-i-007.json"),
                        {"summary": "disk was full", "human_next_step": "free space"})
        incidents.escalate(self.rt, self.events, "k", "d", 1, "/w", "ld",
                           "/plugin/run.sh", incident_id="i-007")
        body = util.read_text(os.path.join(self.rt, "NEEDS_HUMAN.md"))
        self.assertIn("disk was full", body)
        self.assertIn("free space", body)

    def test_escalate_degrades_instead_of_raising_when_the_handover_cannot_be_written(self):
        # Point 4: a full disk (simulated here by NEEDS_HUMAN.md being a
        # directory, so the write can never succeed) must not raise out of
        # escalate -- this is the terminal path with no backstop.
        os.makedirs(os.path.join(self.rt, "NEEDS_HUMAN.md"))
        try:
            incidents.escalate(self.rt, self.events, "tick-timeout", "rc=124", 9,
                               "/tmp/wt", "ld", "/plugin/run.sh")
        except OSError:
            self.fail("escalate() raised OSError instead of degrading")
        # Even though the file could not be written, the operator still gets
        # a record: the incident itself must exist.
        self.assertEqual("needs-human", self.emitted("incident")[-1]["severity"])

    def test_escalate_never_raises_even_with_a_read_only_runtime_dir(self):
        os.chmod(self.rt, 0o500)
        try:
            incidents.escalate(self.rt, self.events, "k", "d", 1, "/w", "ld",
                               "/plugin/run.sh")
        except OSError:
            self.fail("escalate() raised OSError on a read-only runtime dir")
        finally:
            os.chmod(self.rt, 0o700)


class TestNotifyDesktop(Base):
    """Point 6: notify_desktop shells out; it must never be able to break the
    harness or inject a command, and must be inert under LOOP_NOTIFY=0."""

    def test_notify_is_a_noop_under_loop_notify_0(self):
        os.environ["LOOP_NOTIFY"] = "0"
        with mock.patch("runner.incidents.subprocess.run") as run:
            incidents.notify_desktop("t", "b")
        run.assert_not_called()

    def test_a_missing_notifier_binary_does_not_raise(self):
        os.environ["LOOP_NOTIFY"] = "1"
        with mock.patch("runner.incidents.subprocess.run", side_effect=FileNotFoundError()):
            incidents.notify_desktop("t", "b")  # must not raise

    def test_a_nonzero_notifier_exit_does_not_raise(self):
        os.environ["LOOP_NOTIFY"] = "1"
        with mock.patch("runner.incidents.subprocess.run") as run:
            run.return_value = mock.Mock(returncode=1)
            incidents.notify_desktop("t", "b")  # must not raise
        run.assert_called_once()

    def test_a_notifier_timeout_does_not_raise(self):
        import subprocess as sp
        os.environ["LOOP_NOTIFY"] = "1"
        with mock.patch("runner.incidents.subprocess.run",
                        side_effect=sp.TimeoutExpired(cmd="osascript", timeout=10)):
            incidents.notify_desktop("t", "b")  # must not raise

    def test_hostile_title_and_body_cannot_break_out_of_the_applescript_string(self):
        os.environ["LOOP_NOTIFY"] = "1"
        hostile_title = "title\\"  # a lone trailing backslash
        hostile_body = 'body" & (do shell script "touch /tmp/pwned") & "\\'
        with mock.patch("runner.incidents.os.uname") as uname, \
                mock.patch("runner.incidents.subprocess.run") as run:
            uname.return_value.sysname = "Darwin"
            incidents.notify_desktop(hostile_title, hostile_body)
        self.assertTrue(run.called)
        args, kwargs = run.call_args
        argv = args[0]
        script = argv[argv.index("-e") + 1]
        # Quotes are stripped outright, so none of the caller's own quotes
        # survive into the script.
        self.assertNotIn('"', hostile_body.replace('"', "'"))
        # After removing every correctly-escaped (doubled) backslash, no bare
        # backslash may remain immediately before a quote -- that combination
        # is what lets AppleScript read a delimiter quote as an escaped
        # literal instead of the string terminator, breaking the string open.
        self.assertNotRegex(script.replace("\\\\", ""), r'\\"')
        self.assertNotIn("\n", script)

    def test_argv_is_a_list_not_a_shell_string(self):
        # subprocess.run must be called with a list (no shell=True anywhere),
        # so shell metacharacters in title/body can never reach a shell.
        os.environ["LOOP_NOTIFY"] = "1"
        with mock.patch("runner.incidents.subprocess.run") as run:
            incidents.notify_desktop("t; rm -rf /", "$(whoami) `id`")
        args, kwargs = run.call_args
        self.assertIsInstance(args[0], list)
        self.assertNotIn("shell", kwargs)


if __name__ == "__main__":
    unittest.main()
