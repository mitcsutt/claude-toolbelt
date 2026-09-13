"""Render app lifecycle (plan B task 3)."""
from __future__ import annotations

import os
import shutil
import socket
import sys
import tempfile
import time
import types
import unittest

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

from runner import render as render_mod  # noqa: E402


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class RenderAppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.runtime = os.path.join(self.tmp, "runtime")
        os.makedirs(self.runtime)
        self.worktree = os.path.join(self.tmp, "wt")
        os.makedirs(self.worktree)
        self.addCleanup(render_mod.stop_app, self.runtime)

    def cfg(self, render):
        return types.SimpleNamespace(worktree=self.worktree, render=render, limits={})

    def test_no_start_command_is_a_no_op(self):
        self.assertIsNone(render_mod.ensure_app(self.cfg({}), self.runtime))
        self.assertFalse(os.path.exists(os.path.join(self.runtime, "render-app.pid")))

    def test_start_backgrounds_the_app_records_the_pid_and_waits_for_ready(self):
        port = free_port()
        cfg = self.cfg({
            "start": "python3 -m http.server %d --bind 127.0.0.1" % port,
            "ready": "http://127.0.0.1:%d/" % port,
        })
        pid = render_mod.ensure_app(cfg, self.runtime, ready_timeout_s=30, poll_s=0.2)
        self.assertIsNotNone(pid)
        self.assertTrue(alive(pid))
        with open(os.path.join(self.runtime, "render-app.pid")) as fh:
            self.assertEqual(int(fh.read().strip()), pid)

    def test_second_call_reuses_the_running_app(self):
        port = free_port()
        cfg = self.cfg({
            "start": "python3 -m http.server %d --bind 127.0.0.1" % port,
            "ready": "http://127.0.0.1:%d/" % port,
        })
        first = render_mod.ensure_app(cfg, self.runtime, ready_timeout_s=30, poll_s=0.2)
        second = render_mod.ensure_app(cfg, self.runtime, ready_timeout_s=30, poll_s=0.2)
        self.assertEqual(first, second)

    def test_stop_app_kills_it_and_removes_the_pid_file(self):
        port = free_port()
        cfg = self.cfg({"start": "python3 -m http.server %d --bind 127.0.0.1" % port})
        pid = render_mod.ensure_app(cfg, self.runtime)
        render_mod.stop_app(self.runtime)
        deadline = time.time() + 10
        while alive(pid) and time.time() < deadline:
            time.sleep(0.1)
        self.assertFalse(alive(pid))
        self.assertFalse(os.path.exists(os.path.join(self.runtime, "render-app.pid")))

    def test_ready_url_that_never_answers_raises_with_the_url_in_the_message(self):
        port = free_port()  # nothing listens here
        cfg = self.cfg({
            "start": "sleep 60",
            "ready": "http://127.0.0.1:%d/" % port,
        })
        with self.assertRaises(render_mod.RenderAppError) as caught:
            render_mod.ensure_app(cfg, self.runtime, ready_timeout_s=2, poll_s=0.2)
        self.assertIn(str(port), str(caught.exception))

    def test_default_ready_timeout_is_120_seconds(self):
        self.assertEqual(render_mod.READY_TIMEOUT_S, 120)


if __name__ == "__main__":
    unittest.main()
