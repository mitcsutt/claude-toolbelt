import _path  # noqa: F401
import json
import os
import tempfile
import unittest

from runner.events import EventLog, UsageLog


def read_lines(path):
    with open(path) as f:
        return [json.loads(ln) for ln in f if ln.strip()]


class TestEventLog(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.ev = os.path.join(self.d, "events.jsonl")
        self.seq = os.path.join(self.d, "eventseq")

    def test_envelope_has_t_seq_type_then_payload(self):
        log = EventLog(self.ev, self.seq)
        log.emit("loop_start", pid=42, host="box")
        rec = read_lines(self.ev)[0]
        self.assertEqual(["t", "seq", "type", "pid", "host"], list(rec.keys()))
        self.assertEqual("loop_start", rec["type"])
        self.assertEqual(42, rec["pid"])
        self.assertIsInstance(rec["t"], int)

    def test_seq_is_monotonic_within_a_run(self):
        log = EventLog(self.ev, self.seq)
        for _ in range(3):
            log.emit("tick_start", tick=1)
        self.assertEqual([1, 2, 3], [r["seq"] for r in read_lines(self.ev)])

    def test_seq_continues_across_harness_runs(self):
        EventLog(self.ev, self.seq).emit("tick_start", tick=1)
        EventLog(self.ev, self.seq).emit("tick_start", tick=2)
        self.assertEqual([1, 2], [r["seq"] for r in read_lines(self.ev)])

    def test_one_line_per_event_even_with_newlines_in_a_field(self):
        log = EventLog(self.ev, self.seq)
        log.emit("incident", detail="line one\nline two")
        with open(self.ev) as f:
            self.assertEqual(1, len(f.readlines()))
        self.assertEqual("line one\nline two", read_lines(self.ev)[0]["detail"])

    def test_nested_payload_survives(self):
        log = EventLog(self.ev, self.seq)
        log.emit("tick_end", tick=1, by_model={"m": {"cost_usd": 0.5}})
        self.assertEqual(0.5, read_lines(self.ev)[0]["by_model"]["m"]["cost_usd"])

    def test_empty_path_is_a_silent_no_op(self):
        EventLog("", self.seq).emit("loop_start")   # must not raise

    def test_emit_does_not_raise_when_seq_file_cannot_be_written(self):
        # seq_path's parent is a regular file, not a directory, so
        # util.atomic_write's os.makedirs/open/replace will raise OSError.
        blocker = os.path.join(self.d, "blocker")
        with open(blocker, "w") as f:
            f.write("not a directory")
        bad_seq = os.path.join(blocker, "eventseq")
        log = EventLog(self.ev, bad_seq)
        log.emit("loop_start", pid=1)   # must not raise
        self.assertEqual("loop_start", read_lines(self.ev)[0]["type"])


class TestUsageLog(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.path = os.path.join(self.d, "LOOP_USAGE.jsonl")

    def test_row_shape_matches_what_serve_py_reads(self):
        UsageLog(self.path).write_tick(
            3, "execute", 91,
            {"model-a": {"cost_usd": 1.5, "input_tokens": 10, "output_tokens": 4,
                         "cache_read_tokens": 100, "cache_creation_tokens": 2}})
        rec = read_lines(self.path)[0]
        self.assertEqual(3, rec["tick"])
        self.assertEqual("execute", rec["mode"])
        self.assertEqual(91, rec["duration_s"])
        self.assertEqual(1.5, rec["cost_usd"])
        self.assertEqual(10, rec["input_tokens"])
        self.assertEqual(4, rec["output_tokens"])
        self.assertEqual(100, rec["by_model"]["model-a"]["cache_read_tokens"])
        self.assertNotIn("cumulative_cost_usd", rec)

    def test_sums_across_models(self):
        UsageLog(self.path).write_tick(1, "execute", 5, {
            "a": {"cost_usd": 1.0, "input_tokens": 1, "output_tokens": 1,
                  "cache_read_tokens": 0, "cache_creation_tokens": 0},
            "b": {"cost_usd": 2.0, "input_tokens": 2, "output_tokens": 3,
                  "cache_read_tokens": 0, "cache_creation_tokens": 0}})
        rec = read_lines(self.path)[0]
        self.assertEqual(3.0, rec["cost_usd"])
        self.assertEqual(3, rec["input_tokens"])
        self.assertEqual(4, rec["output_tokens"])

    def test_empty_by_model_still_writes_a_row(self):
        UsageLog(self.path).write_tick(9, "execute", 0, {})
        rec = read_lines(self.path)[0]
        self.assertEqual({}, rec["by_model"])
        self.assertEqual(0, rec["cost_usd"])

    def test_accepts_dataclass_like_objects(self):
        class U(object):
            cost_usd = 0.25
            input_tokens = 7
            output_tokens = 8
            cache_read_tokens = 9
            cache_creation_tokens = 10

        UsageLog(self.path).write_tick(1, "execute", 1, {"m": U()})
        rec = read_lines(self.path)[0]
        self.assertEqual(0.25, rec["cost_usd"])
        self.assertEqual(9, rec["by_model"]["m"]["cache_read_tokens"])


if __name__ == "__main__":
    unittest.main()
