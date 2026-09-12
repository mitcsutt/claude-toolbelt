import _path  # noqa: F401
import json
import os
import tempfile
import unittest

from runner import util


class TestAtomicWrite(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_writes_content_and_leaves_no_tmp(self):
        p = os.path.join(self.d, "sub", "f.txt")
        util.atomic_write(p, "hello")
        with open(p) as f:
            self.assertEqual("hello", f.read())
        self.assertFalse(os.path.exists(p + ".tmp"))

    def test_replaces_existing(self):
        p = os.path.join(self.d, "f.txt")
        util.atomic_write(p, "one")
        util.atomic_write(p, "two")
        with open(p) as f:
            self.assertEqual("two", f.read())

    def test_write_json_is_compact_and_roundtrips(self):
        p = os.path.join(self.d, "f.json")
        util.write_json(p, {"a": 1, "b": [2, 3]})
        with open(p) as f:
            raw = f.read()
        self.assertNotIn(" ", raw)
        self.assertEqual({"a": 1, "b": [2, 3]}, json.loads(raw))


class TestReaders(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_read_json_returns_none_for_missing_and_garbage(self):
        self.assertIsNone(util.read_json(os.path.join(self.d, "nope.json")))
        p = os.path.join(self.d, "bad.json")
        util.atomic_write(p, "{not json")
        self.assertIsNone(util.read_json(p))

    def test_read_text_default(self):
        self.assertEqual("fallback", util.read_text(os.path.join(self.d, "x"), "fallback"))

    def test_read_int_handles_missing_garbage_and_float(self):
        self.assertEqual(7, util.read_int(os.path.join(self.d, "x"), 7))
        p = os.path.join(self.d, "n")
        util.atomic_write(p, "42\n")
        self.assertEqual(42, util.read_int(p))
        util.atomic_write(p, "nope")
        self.assertEqual(0, util.read_int(p))

    def test_stamp_epoch_writes_an_integer(self):
        p = os.path.join(self.d, "HEARTBEAT")
        util.stamp_epoch(p, now=1700000000.9)
        self.assertEqual(1700000000, util.read_int(p))


class TestProcessAlive(unittest.TestCase):
    def test_self_is_alive(self):
        self.assertTrue(util.process_alive(os.getpid()))

    def test_bogus_pids_are_dead(self):
        self.assertFalse(util.process_alive(0))
        self.assertFalse(util.process_alive(-1))
        self.assertFalse(util.process_alive("nope"))
        self.assertFalse(util.process_alive(None))

    def test_must_contain_matches_our_own_command_line(self):
        self.assertTrue(util.process_alive(os.getpid(), "python") or
                        util.process_alive(os.getpid(), "Python"))
        self.assertFalse(util.process_alive(os.getpid(), "definitely-not-in-argv"))


if __name__ == "__main__":
    unittest.main()
