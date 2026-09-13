import _path  # noqa: F401
import json
import os
import shlex
import stat
import tempfile
import time
import unittest

from runner import claude_proc
from runner.events import EventLog


def fake_claude(d, lines, exit_code=0, sleep_s=0):
    """A stand-in `claude` that prints canned stream-json lines."""
    path = os.path.join(d, "claude")
    body = ["#!/usr/bin/env bash", "cat >/dev/null 2>&1 || true"]
    if sleep_s:
        body.append("sleep %d" % sleep_s)
    for line in lines:
        body.append("printf '%%s\\n' %s" % shlex.quote(line))
    body.append("exit %d" % exit_code)
    with open(path, "w") as f:
        f.write("\n".join(body) + "\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def custom_claude(d, body, name="claude"):
    """A stand-in `claude` whose body is written verbatim (bytes or str)."""
    path = os.path.join(d, name)
    data = body if isinstance(body, bytes) else body.encode()
    with open(path, "wb") as f:
        f.write(b"#!/usr/bin/env bash\ncat >/dev/null 2>&1 || true\n" + data)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


INIT = json.dumps({"type": "system", "subtype": "init", "session_id": "sess-1"})
ASSISTANT = json.dumps({
    "type": "assistant",
    "message": {"id": "msg_1", "model": "model-a",
                "usage": {"input_tokens": 10, "output_tokens": 4,
                          "cache_read_input_tokens": 100,
                          "cache_creation_input_tokens": 2},
                "content": [{"type": "text", "text": "working"},
                            {"type": "tool_use", "id": "tu_1", "name": "Edit",
                             "input": {"file_path": "src/a.ts"}}]}})
TOOL_RESULT = json.dumps({
    "type": "user",
    "message": {"content": [{"type": "tool_result", "tool_use_id": "tu_1"}]}})
RESULT = json.dumps({
    "type": "result", "subtype": "success", "is_error": False,
    "result": "done\n```json\n{\"status\": \"complete\"}\n```",
    "modelUsage": {"model-a": {"costUSD": 0.5, "inputTokens": 10, "outputTokens": 4,
                               "cacheReadInputTokens": 100,
                               "cacheCreationInputTokens": 2}}})
OK_RESULT = json.dumps({"type": "result", "is_error": False, "result": "ok"})

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "fixtures")


class Harnessed(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = tempfile.mkdtemp()
        self.events_path = os.path.join(self.d, "events.jsonl")
        self.events = EventLog(self.events_path, os.path.join(self.d, "eventseq"))
        self.activity = os.path.join(self.d, "last-activity")

    def emitted(self, type_):
        out = []
        with open(self.events_path) as f:
            for line in f:
                rec = json.loads(line)
                if rec["type"] == type_:
                    out.append(rec)
        return out

    def run_it(self, binary, **kw):
        kwargs = dict(phase="worker", model="model-a", prompt="do the thing",
                      cwd=self.cwd, timeout_s=30, max_turns=0, max_budget_usd=None,
                      env=dict(os.environ), events=self.events, tick=3, role="Worker",
                      activity_path=self.activity, claude_bin=binary)
        kwargs.update(kw)
        return claude_proc.run_phase(**kwargs)


class TestBuildArgv(unittest.TestCase):
    def test_the_base_command(self):
        argv = claude_proc.build_argv("claude", "", None, 0, None, None)
        self.assertEqual(["claude", "-p", "--output-format", "stream-json", "--verbose",
                          "--dangerously-skip-permissions"], argv)

    def test_a_model_is_passed_when_the_tier_resolves(self):
        self.assertIn("--model", claude_proc.build_argv("claude", "model-a", None, 0, None, None))

    def test_an_empty_model_inherits_the_users_default(self):
        self.assertNotIn("--model", claude_proc.build_argv("claude", "", None, 0, None, None))

    def test_max_turns_zero_omits_the_flag(self):
        self.assertNotIn("--max-turns", claude_proc.build_argv("claude", "", None, 0, None, None))
        self.assertIn("--max-turns", claude_proc.build_argv("claude", "", None, 8, None, None))

    def test_budget_and_resume_and_allowed_tools(self):
        argv = claude_proc.build_argv("claude", "m", "sess-9", 8, 6.0, ["Read", "Edit"])
        self.assertEqual("sess-9", argv[argv.index("--resume") + 1])
        self.assertEqual("6.0", argv[argv.index("--max-budget-usd") + 1])
        self.assertEqual("Read,Edit", argv[argv.index("--allowedTools") + 1])


class TestIsStalled(unittest.TestCase):
    def test_quiet_with_no_outstanding_tool_is_stalled(self):
        self.assertTrue(claude_proc.is_stalled(1000, 0, 300, 1400))

    def test_quiet_but_inside_a_tool_call_is_not_stalled(self):
        self.assertFalse(claude_proc.is_stalled(1000, 1, 300, 1400))

    def test_recent_activity_is_not_stalled(self):
        self.assertFalse(claude_proc.is_stalled(1000, 0, 300, 1100))


class TestTranscriptPath(unittest.TestCase):
    def test_none_without_a_session_id(self):
        self.assertIsNone(claude_proc.transcript_path("/tmp/wt", None))

    def test_none_when_the_file_does_not_exist(self):
        self.assertIsNone(claude_proc.transcript_path("/tmp/wt", "sess-1"))

    def test_the_encoded_cwd_is_used_when_the_file_exists(self):
        home = tempfile.mkdtemp()
        cwd = "/Users/x/proj"
        d = os.path.join(home, ".claude", "projects", "-Users-x-proj")
        os.makedirs(d)
        open(os.path.join(d, "sess-1.jsonl"), "w").close()
        old = os.environ.get("HOME")
        os.environ["HOME"] = home
        try:
            self.assertEqual(os.path.join(d, "sess-1.jsonl"),
                             claude_proc.transcript_path(cwd, "sess-1"))
        finally:
            if old is not None:
                os.environ["HOME"] = old


class TestTranscriptPathEncoding(unittest.TestCase):
    """Claude Code encodes every non-alphanumeric byte of the cwd as `-`.

    An agent-loop worktree lives under a dotted directory
    (`<repo>/.claude-worktrees/<branch>`), so a `/`-only encoding finds no
    transcript for any real loop run.
    """

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.old_home = os.environ.get("HOME")
        self.old_cfg = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["HOME"] = self.home
        os.environ.pop("CLAUDE_CONFIG_DIR", None)

    def tearDown(self):
        if self.old_home is not None:
            os.environ["HOME"] = self.old_home
        if self.old_cfg is not None:
            os.environ["CLAUDE_CONFIG_DIR"] = self.old_cfg
        else:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)

    def seed(self, base, encoded, session="sess-1"):
        d = os.path.join(base, "projects", encoded)
        os.makedirs(d)
        path = os.path.join(d, "%s.jsonl" % session)
        open(path, "w").close()
        return path

    def test_a_dotted_worktree_path_is_encoded_like_claude_code_does(self):
        want = self.seed(os.path.join(self.home, ".claude"),
                         "-Users-x-proj--claude-worktrees-loop")
        self.assertEqual(want, claude_proc.transcript_path(
            "/Users/x/proj/.claude-worktrees/loop", "sess-1"))

    def test_the_slash_only_encoding_is_still_accepted(self):
        want = self.seed(os.path.join(self.home, ".claude"), "-Users-x-a.b")
        self.assertEqual(want, claude_proc.transcript_path("/Users/x/a.b", "sess-1"))

    def test_the_config_dir_override_is_honoured(self):
        cfg = tempfile.mkdtemp()
        os.environ["CLAUDE_CONFIG_DIR"] = cfg
        want = self.seed(cfg, "-Users-x-proj")
        self.assertEqual(want, claude_proc.transcript_path("/Users/x/proj", "sess-1"))


class TestHappyPhase(Harnessed):
    def setUp(self):
        Harnessed.setUp(self)
        self.binary = fake_claude(self.d, [INIT, ASSISTANT, TOOL_RESULT, RESULT])
        self.res = self.run_it(self.binary)

    def test_it_captures_rc_session_and_result_text(self):
        self.assertEqual(0, self.res.rc)
        self.assertFalse(self.res.killed)
        self.assertFalse(self.res.timed_out)
        self.assertEqual("sess-1", self.res.session_id)
        self.assertIn("status", self.res.result_text)

    def test_usage_comes_from_model_usage_on_the_result(self):
        self.assertEqual(["model-a"], list(self.res.usage_by_model))
        u = self.res.usage_by_model["model-a"]
        self.assertEqual(0.5, u.cost_usd)
        self.assertEqual(10, u.input_tokens)
        self.assertEqual(100, u.cache_read_tokens)
        self.assertEqual(2, u.cache_creation_tokens)

    def test_tool_calls_are_counted_and_narrated(self):
        self.assertEqual(1, self.res.tool_calls)
        tools = self.emitted("tool")
        self.assertEqual(1, len(tools))
        self.assertEqual("Edit", tools[0]["name"])
        self.assertEqual("Worker", tools[0]["role"])
        self.assertEqual("src/a.ts", tools[0]["desc"])

    def test_role_and_phase_events_bracket_the_run(self):
        self.assertEqual("model-a", self.emitted("role_start")[0]["model"])
        self.assertEqual("Worker", self.emitted("role_end")[0]["role"])
        end = self.emitted("phase_end")[0]
        self.assertEqual("worker", end["phase"])
        self.assertEqual(0, end["rc"])
        self.assertEqual("sess-1", end["session_id"])

    def test_activity_is_stamped_for_every_stream_line(self):
        self.assertTrue(os.path.exists(self.activity))
        with open(self.activity) as f:
            self.assertGreater(int(f.read().strip()), 0)

    def test_a_completed_phase_is_not_reported_as_stalled(self):
        self.assertFalse(self.res.stalled)
        self.assertFalse(self.res.stream_error)


class TestUsageFallback(Harnessed):
    def test_per_message_usage_is_folded_when_model_usage_is_absent(self):
        binary = fake_claude(self.d, [INIT, ASSISTANT, OK_RESULT])
        res = self.run_it(binary)
        self.assertEqual(10, res.usage_by_model["model-a"].input_tokens)
        self.assertEqual(100, res.usage_by_model["model-a"].cache_read_tokens)

    def test_the_largest_reading_per_message_id_wins(self):
        small = json.loads(ASSISTANT)
        big = json.loads(ASSISTANT)
        big["message"]["usage"]["output_tokens"] = 40
        binary = fake_claude(self.d, [INIT, json.dumps(small), json.dumps(big), OK_RESULT])
        res = self.run_it(binary)
        self.assertEqual(40, res.usage_by_model["model-a"].output_tokens)

    def test_two_message_ids_accumulate(self):
        second = json.loads(ASSISTANT)
        second["message"]["id"] = "msg_2"
        binary = fake_claude(self.d, [INIT, ASSISTANT, json.dumps(second), OK_RESULT])
        res = self.run_it(binary)
        self.assertEqual(20, res.usage_by_model["model-a"].input_tokens)

    def test_messages_with_no_id_accumulate_rather_than_collapse(self):
        """Two id-less readings are two messages, not one re-reading of one.

        Keeping the max under a shared "" key would under-count real spend,
        which is the failure this module exists to prevent.
        """
        anon = json.loads(ASSISTANT)
        anon["message"].pop("id")
        binary = fake_claude(self.d, [INIT, json.dumps(anon), json.dumps(anon), OK_RESULT])
        res = self.run_it(binary)
        self.assertEqual(20, res.usage_by_model["model-a"].input_tokens)


class TestFailurePaths(Harnessed):
    def test_a_timeout_kills_the_phase_and_still_reports_usage(self):
        binary = fake_claude(self.d, [INIT, ASSISTANT], sleep_s=0)
        # The script prints, then sleeps forever before exiting.
        with open(binary) as f:
            body = f.read()
        with open(binary, "w") as f:
            f.write(body.replace("exit 0", "sleep 60\nexit 0"))
        started = time.time()
        res = self.run_it(binary, timeout_s=1)
        self.assertTrue(res.timed_out)
        self.assertTrue(res.killed)
        self.assertLess(time.time() - started, 45)
        self.assertIn("model-a", res.usage_by_model)
        self.assertEqual(1, len(self.emitted("phase_end")))

    def test_an_api_error_result_is_recorded(self):
        result = json.dumps({"type": "result", "is_error": True,
                             "api_error_status": 429, "result": ""})
        binary = fake_claude(self.d, [INIT, result])
        res = self.run_it(binary)
        self.assertTrue(res.is_error)
        self.assertEqual("429", res.api_error_status)

    def test_a_rate_limit_event_is_captured_to_disk_and_to_the_result(self):
        rl = json.dumps({"type": "rate_limit_event",
                         "rate_limit_info": {"status": "rejected", "resetsAt": 42}})
        path = os.path.join(self.d, "ratelimit.json")
        res = self.run_it(fake_claude(self.d, [INIT, rl, OK_RESULT]), ratelimit_path=path)
        self.assertEqual("rejected", res.rate_limit["status"])
        with open(path) as f:
            self.assertEqual(42, json.load(f)["resetsAt"])

    def test_a_non_json_line_is_ignored(self):
        res = self.run_it(fake_claude(self.d, [INIT, "this is not json", OK_RESULT]))
        self.assertEqual(0, res.rc)
        self.assertEqual("ok", res.result_text)

    def test_a_nonzero_exit_is_carried_through(self):
        res = self.run_it(fake_claude(self.d, [INIT], exit_code=3))
        self.assertEqual(3, res.rc)
        self.assertEqual("", res.result_text)

    def test_a_missing_binary_returns_rc_127_rather_than_raising(self):
        res = self.run_it(os.path.join(self.d, "no-such-binary"))
        self.assertEqual(127, res.rc)
        self.assertEqual(1, len(self.emitted("phase_end")))

    def test_a_phase_that_dies_on_a_signal_reads_as_killed(self):
        binary = custom_claude(self.d, "kill -TERM $$\nsleep 5\n")
        res = self.run_it(binary)
        self.assertEqual(143, res.rc)
        self.assertTrue(res.killed)
        self.assertFalse(res.timed_out)


class TestHostileStream(Harnessed):
    """A malformed-but-valid-JSON line must never abort the phase.

    Everything on this stream is attacker-adjacent input: a tool result can
    contain anything, and one AttributeError in the parser would throw away a
    whole phase's work and its usage record.
    """

    def test_a_usage_field_that_is_not_an_object_is_ignored(self):
        bad = json.dumps({"type": "assistant",
                          "message": {"id": "m", "model": "model-a", "usage": [1, 2],
                                      "content": []}})
        res = self.run_it(fake_claude(self.d, [INIT, bad, ASSISTANT, OK_RESULT]))
        self.assertEqual(0, res.rc)
        self.assertEqual(10, res.usage_by_model["model-a"].input_tokens)

    def test_a_message_that_is_not_an_object_is_skipped(self):
        bad = json.dumps({"type": "assistant", "message": "nope"})
        bad_user = json.dumps({"type": "user", "message": ["nope"]})
        res = self.run_it(fake_claude(self.d, [INIT, bad, bad_user, OK_RESULT]))
        self.assertEqual(0, res.rc)
        self.assertFalse(res.stream_error)

    def test_a_model_usage_that_is_not_an_object_falls_back_to_per_message(self):
        result = json.dumps({"type": "result", "is_error": False, "result": "ok",
                             "modelUsage": ["model-a"]})
        res = self.run_it(fake_claude(self.d, [INIT, ASSISTANT, result]))
        self.assertEqual(10, res.usage_by_model["model-a"].input_tokens)

    def test_a_result_that_is_not_a_string_is_coerced_to_text(self):
        result = json.dumps({"type": "result", "is_error": False,
                             "result": {"status": "complete"}})
        res = self.run_it(fake_claude(self.d, [INIT, result]))
        self.assertIsInstance(res.result_text, str)
        self.assertIn("complete", res.result_text)

    def test_a_tool_use_with_no_id_is_still_counted(self):
        line = json.dumps({"type": "assistant",
                           "message": {"id": "m", "model": "model-a",
                                       "content": [{"type": "tool_use", "name": "Bash",
                                                    "input": {"command": "ls"}}]}})
        res = self.run_it(fake_claude(self.d, [INIT, line, OK_RESULT]))
        self.assertEqual(1, res.tool_calls)
        self.assertEqual("ls", self.emitted("tool")[0]["desc"])

    def test_invalid_utf8_bytes_do_not_abort_the_phase(self):
        body = b"printf '\\xff\\xfe garbage\\n'\n"
        body += ("printf '%s\\n' " + shlex.quote(OK_RESULT) + "\n").encode()
        res = self.run_it(custom_claude(self.d, body))
        self.assertEqual(0, res.rc)
        self.assertEqual("ok", res.result_text)
        self.assertFalse(res.stream_error)


class TestResumeAndSession(Harnessed):
    def test_the_resume_session_survives_a_phase_that_dies_before_init(self):
        """Otherwise the caller sees no session and pays for a fresh one."""
        res = self.run_it(fake_claude(self.d, [], exit_code=1), resume_session="sess-0")
        self.assertEqual(1, res.rc)
        self.assertEqual("sess-0", res.session_id)
        self.assertEqual("sess-0", self.emitted("phase_end")[0]["session_id"])

    def test_an_init_line_overrides_the_resume_session(self):
        res = self.run_it(fake_claude(self.d, [INIT, OK_RESULT]), resume_session="sess-0")
        self.assertEqual("sess-1", res.session_id)


class TestStallReporting(Harnessed):
    def test_a_phase_that_ends_quiet_with_no_outstanding_tool_reports_stalled(self):
        res = self.run_it(fake_claude(self.d, [INIT, OK_RESULT], sleep_s=0), stall_s=0)
        self.assertTrue(res.stalled)

    def test_a_phase_that_ends_inside_a_tool_call_is_not_stalled(self):
        res = self.run_it(fake_claude(self.d, [INIT, ASSISTANT]), stall_s=0)
        self.assertFalse(res.stalled)

    def test_a_quiet_stretch_that_recovers_is_not_reported_as_a_stall(self):
        """v2 called seven of these a stall and was wrong every time."""
        body = "printf '%s\\n' " + shlex.quote(INIT) + "\n"
        body += "sleep 2\n"
        body += "printf '%s\\n' " + shlex.quote(OK_RESULT) + "\n"
        res = self.run_it(custom_claude(self.d, body), stall_s=1)
        self.assertEqual(0, res.rc)
        self.assertFalse(res.stalled)


class TestStopCheck(Harnessed):
    def test_a_stop_request_kills_the_phase_and_keeps_its_usage(self):
        """`runtime/STOP` must SIGTERM the phase now (spec §4.4).

        The caller never sees the pid, so the only place that can honour STOP
        promptly is this watchdog.
        """
        binary = fake_claude(self.d, [INIT, ASSISTANT])
        with open(binary) as f:
            body = f.read()
        with open(binary, "w") as f:
            f.write(body.replace("exit 0", "sleep 60\nexit 0"))
        sentinel = os.path.join(self.d, "STOP")
        open(sentinel, "w").close()
        started = time.time()
        res = self.run_it(binary, timeout_s=120,
                          stop_check=lambda: os.path.exists(sentinel))
        self.assertLess(time.time() - started, 45)
        self.assertTrue(res.killed)
        self.assertFalse(res.timed_out)
        self.assertIn("model-a", res.usage_by_model)

    def test_no_stop_request_leaves_a_phase_alone(self):
        res = self.run_it(fake_claude(self.d, [INIT, OK_RESULT]),
                          stop_check=lambda: False)
        self.assertEqual(0, res.rc)
        self.assertFalse(res.killed)


class TestStubDrivenPhase(Harnessed):
    """run_phase against the real `tests/fixtures/claude`, the e2e's stub."""

    def setUp(self):
        Harnessed.setUp(self)
        self.stub = os.path.join(FIXTURES, "claude")
        self.script = tempfile.mkdtemp()
        self.env = dict(os.environ)
        self.env["STUB_SCRIPT"] = self.script

    def write(self, name, text):
        with open(os.path.join(self.script, name), "w") as f:
            f.write(text)

    def test_the_stub_init_line_is_captured_as_the_session_id(self):
        self.write("001.jsonl", OK_RESULT + "\n")
        res = self.run_it(self.stub, env=self.env)
        self.assertEqual(0, res.rc)
        self.assertEqual("stub-001", res.session_id)
        self.assertEqual("ok", res.result_text)

    def test_a_killed_phase_keeps_the_usage_already_on_the_wire(self):
        """`.sleep` hangs the phase AFTER its usage has been printed.

        Spend that vanishes with a killed phase is spend nobody ever attributes.
        """
        self.write("001.jsonl", ASSISTANT + "\n")
        self.write("001.sleep", "60")
        started = time.time()
        res = self.run_it(self.stub, env=self.env, timeout_s=2)
        self.assertTrue(res.timed_out)
        self.assertTrue(res.killed)
        self.assertLess(time.time() - started, 45)
        self.assertEqual(10, res.usage_by_model["model-a"].input_tokens)
        self.assertEqual("stub-001", res.session_id)
        self.assertEqual(1, res.tool_calls)

    def test_the_exit_file_reaches_the_phase_result(self):
        self.write("001.jsonl", OK_RESULT + "\n")
        self.write("001.exit", "2")
        self.assertEqual(2, self.run_it(self.stub, env=self.env).rc)


class TestStubFixture(unittest.TestCase):
    """The scripted stub the e2e drives; asserted here so a protocol change fails fast."""

    def setUp(self):
        self.stub = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "fixtures", "claude")
        self.script = tempfile.mkdtemp()
        self.cwd = tempfile.mkdtemp()

    def invoke(self, args=None, env_extra=None):
        import subprocess
        env = dict(os.environ)
        env["STUB_SCRIPT"] = self.script
        env.update(env_extra or {})
        proc = subprocess.run(["bash", self.stub] + (args or []), cwd=self.cwd,
                              env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, input=b"")
        return proc.returncode, proc.stdout.decode()

    def test_it_prints_an_init_line_then_the_numbered_transcript(self):
        with open(os.path.join(self.script, "001.jsonl"), "w") as f:
            f.write('{"type":"result","result":"first"}\n')
        rc, out = self.invoke()
        lines = [json.loads(ln) for ln in out.splitlines() if ln.strip()]
        self.assertEqual(0, rc)
        self.assertEqual("system", lines[0]["type"])
        self.assertTrue(lines[0]["session_id"])
        self.assertEqual("first", lines[1]["result"])

    def test_invocations_are_consumed_in_order(self):
        for n, text in (("001", "one"), ("002", "two")):
            with open(os.path.join(self.script, "%s.jsonl" % n), "w") as f:
                f.write('{"type":"result","result":"%s"}\n' % text)
        self.invoke()
        _, out = self.invoke()
        self.assertIn("two", out)

    def test_an_exit_file_sets_the_exit_code(self):
        with open(os.path.join(self.script, "001.exit"), "w") as f:
            f.write("7")
        rc, _ = self.invoke()
        self.assertEqual(7, rc)

    def test_a_side_effect_script_runs_in_the_invocation_cwd(self):
        with open(os.path.join(self.script, "001.sh"), "w") as f:
            f.write("echo written > side-effect.txt\n")
        with open(os.path.join(self.script, "001.jsonl"), "w") as f:
            f.write('{"type":"result","result":"ok"}\n')
        self.invoke()
        self.assertTrue(os.path.exists(os.path.join(self.cwd, "side-effect.txt")))

    def test_resume_is_recorded_to_the_stub_log(self):
        log = os.path.join(self.script, "log")
        self.invoke(["--resume", "sess-9"], {"STUB_LOG": log})
        with open(log) as f:
            self.assertIn("sess-9", f.read())

    def test_a_skill_invocation_consumes_no_transcript(self):
        with open(os.path.join(self.script, "001.jsonl"), "w") as f:
            f.write('{"type":"result","result":"one"}\n')
        rc, out = self.invoke(["--print", "/agent-loop-postmortem"])
        self.assertEqual(0, rc)
        self.assertEqual("", out.strip())
        _, out2 = self.invoke()
        self.assertIn("one", out2)

    def test_a_sleep_file_holds_the_invocation_open_after_printing(self):
        with open(os.path.join(self.script, "001.jsonl"), "w") as f:
            f.write('{"type":"result","result":"ok"}\n')
        with open(os.path.join(self.script, "001.sleep"), "w") as f:
            f.write("1")
        started = time.time()
        rc, out = self.invoke()
        self.assertEqual(0, rc)
        self.assertIn("ok", out)
        self.assertGreaterEqual(time.time() - started, 1.0)


class TestStubFixtureLegacyProtocol(unittest.TestCase):
    """Without STUB_SCRIPT the stub keeps v2's MOCK_CLAUDE_* behaviour.

    `tests/run.e2e.test.sh` still drives it that way and stays green until the
    spec §12 rewrite lands; this test is what tells that rewrite it may delete
    the second half of the fixture.
    """

    def setUp(self):
        self.stub = os.path.join(FIXTURES, "claude")
        self.d = tempfile.mkdtemp()

    def invoke(self, args=None, env_extra=None):
        import subprocess
        env = dict(os.environ)
        env.pop("STUB_SCRIPT", None)
        env["MOCK_CLAUDE_STATE"] = os.path.join(self.d, "state")
        env["MOCK_CLAUDE_SCRIPT"] = os.path.join(self.d, "script")
        env.update(env_extra or {})
        proc = subprocess.run(["bash", self.stub] + (args or []), cwd=self.d, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, input=b"")
        return proc.returncode, proc.stdout.decode()

    def test_blocks_are_consumed_in_order_and_rc_is_honoured(self):
        with open(os.path.join(self.d, "script"), "w") as f:
            f.write("first\n===\nRC=4\nsecond\n")
        rc, out = self.invoke()
        self.assertEqual(0, rc)
        self.assertEqual("first", out.strip())
        rc, out = self.invoke()
        self.assertEqual(4, rc)
        self.assertEqual("second", out.strip())


if __name__ == "__main__":
    unittest.main()
