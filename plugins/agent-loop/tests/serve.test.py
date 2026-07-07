#!/usr/bin/env python3
"""Unit tests for web/serve.py (stdlib unittest; no pip)."""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "web"))
import serve  # noqa: E402


class TestPct(unittest.TestCase):
    def test_rounds_to_nearest(self):
        self.assertEqual(serve.pct(26, 42), 62)

    def test_zero_total(self):
        self.assertEqual(serve.pct(0, 0), 0)

    def test_full(self):
        self.assertEqual(serve.pct(42, 42), 100)


PLAN_SAMPLE = """# Loop Plan

## Segment A: Bootstrap
- [x] T1: scaffold | model: sonnet
- [x] T2: config
Reviewed: a1b2c3
- [~] T3: forms | depends_on: T2
- [ ] T4: tables | mechanical

## Segment B: Polish
Goal: Make it shine before ship
- [ ] T5: toasts
- [-] T6: dropped
"""


class TestCountTasks(unittest.TestCase):
    def test_counts(self):
        c = serve.count_tasks(PLAN_SAMPLE)
        self.assertEqual(c["total"], 6)
        self.assertEqual(c["done"], 2)
        self.assertEqual(c["remaining"], 2)

    def test_reviewed_marker_not_counted(self):
        # "Reviewed: a1b2c3" is plain text, not a - [.] line.
        c = serve.count_tasks(PLAN_SAMPLE + "\nReviewed: deadbee\n")
        self.assertEqual(c["total"], 6)


class TestParsePlan(unittest.TestCase):
    def test_task_fields(self):
        p = serve.parse_plan(PLAN_SAMPLE)
        t3 = next(t for t in p["tasks"] if t["id"] == "T3")
        self.assertEqual(t3["status"], "doing")
        self.assertEqual(t3["segment"], "Segment A: Bootstrap")
        self.assertFalse(t3["mechanical"])
        t1 = next(t for t in p["tasks"] if t["id"] == "T1")
        self.assertEqual(t1["status"], "done")
        self.assertEqual(t1["model"], "sonnet")
        t4 = next(t for t in p["tasks"] if t["id"] == "T4")
        self.assertTrue(t4["mechanical"])

    def test_segments_breakdown(self):
        p = serve.parse_plan(PLAN_SAMPLE)
        segs = {s["name"]: s for s in p["segments"]}
        self.assertEqual(segs["Segment A: Bootstrap"]["total"], 4)
        self.assertEqual(segs["Segment A: Bootstrap"]["done"], 2)
        self.assertEqual(segs["Segment B: Polish"]["total"], 2)

    def test_segment_goal_extracted(self):
        # A "Goal:" line under a segment header is captured; segments without
        # one keep goal None. Goal lines are not tasks (counts unaffected).
        p = serve.parse_plan(PLAN_SAMPLE)
        segs = {s["name"]: s for s in p["segments"]}
        self.assertEqual(segs["Segment B: Polish"]["goal"], "Make it shine before ship")
        self.assertIsNone(segs["Segment A: Bootstrap"]["goal"])

    def test_progress(self):
        p = serve.parse_plan(PLAN_SAMPLE)
        self.assertEqual(p["progress"]["total"], 6)
        self.assertEqual(p["progress"]["done"], 2)
        self.assertEqual(p["progress"]["pct"], 33)


USAGE_SAMPLE = "\n".join([
    '{"tick":1,"mode":"plan","cost_usd":0.5,"input_tokens":10,"output_tokens":5,'
    '"duration_s":40,"by_model":{"opus":{"cost_usd":0.5,"input_tokens":10,'
    '"output_tokens":5,"cache_read_tokens":0,"cache_creation_tokens":0}}}',
    '{"tick":2,"mode":"execute","cost_usd":1.0,"input_tokens":20,"output_tokens":8,'
    '"duration_s":120,"by_model":{"opus":{"cost_usd":0.7,"input_tokens":12,'
    '"output_tokens":4,"cache_read_tokens":0,"cache_creation_tokens":0},'
    '"sonnet":{"cost_usd":0.3,"input_tokens":8,"output_tokens":4,'
    '"cache_read_tokens":0,"cache_creation_tokens":0}}}',
    'garbage-not-json',
])


class TestParseUsage(unittest.TestCase):
    def test_by_model_rollup(self):
        u = serve.parse_usage(USAGE_SAMPLE)
        self.assertAlmostEqual(u["by_model"]["opus"]["cost_usd"], 1.2)
        self.assertEqual(u["by_model"]["opus"]["input_tokens"], 22)
        self.assertAlmostEqual(u["by_model"]["sonnet"]["cost_usd"], 0.3)

    def test_total_cost(self):
        u = serve.parse_usage(USAGE_SAMPLE)
        self.assertAlmostEqual(u["total_cost_usd"], 1.5)

    def test_ticks_recent(self):
        u = serve.parse_usage(USAGE_SAMPLE)
        self.assertEqual(len(u["ticks"]), 2)
        self.assertEqual(u["ticks"][-1]["tick"], 2)
        self.assertEqual(u["ticks"][-1]["mode"], "execute")

    def test_tolerates_garbage_and_empty(self):
        self.assertEqual(serve.parse_usage("")["total_cost_usd"], 0)


class TestUsageEffort(unittest.TestCase):
    def test_tokens_and_per_task(self):
        # 2 ticks, 2 tasks done -> per-task averages over completed tasks
        u = serve.usage_effort(USAGE_SAMPLE, tasks_done=2)
        self.assertEqual(u["by_model"]["opus"]["tokens"], 22 + 9)   # in+out summed (22 in + 9 out)
        self.assertAlmostEqual(u["total_cost_usd"], 1.5)
        self.assertAlmostEqual(u["per_task"]["cost_usd"], 0.75)     # 1.5 / 2

    def test_burn_over_active_compute_time(self):
        # Burn is cost/tokens over *active* compute seconds (sum of tick
        # duration_s = 40 + 120 = 160s), NOT wall-clock — so it does not
        # decay while the loop is paused or idle.
        u = serve.usage_effort(USAGE_SAMPLE, tasks_done=2)
        self.assertAlmostEqual(u["burn"]["usd_per_hr"], 1.5 / (160 / 3600), places=3)   # 33.75
        self.assertAlmostEqual(u["burn"]["tok_per_min"], 43 / (160 / 60), places=3)     # 16.125

    def test_zero_safe(self):
        u = serve.usage_effort("", tasks_done=0)
        self.assertEqual(u["total_cost_usd"], 0)
        self.assertEqual(u["per_task"]["cost_usd"], 0)
        self.assertEqual(u["burn"]["usd_per_hr"], 0)

    def test_usage_effort_counts_full_billed_surface_and_cache_share(self):
        # Two ticks, one model under two region-variant ids, with cache.
        jsonl = "\n".join([
            '{"tick":1,"mode":"execute","cost_usd":1.0,"duration_s":60,"by_model":{'
            '"claude-sonnet-4-6":{"cost_usd":1.0,"input_tokens":100,"output_tokens":200,'
            '"cache_read_tokens":9000,"cache_creation_tokens":700}}}',
            '{"tick":2,"mode":"execute","cost_usd":1.0,"duration_s":60,"by_model":{'
            '"us.anthropic.claude-sonnet-4-6":{"cost_usd":1.0,"input_tokens":0,"output_tokens":0,'
            '"cache_read_tokens":1000,"cache_creation_tokens":0}}}',
        ])
        u = serve.usage_effort(jsonl, tasks_done=2)
        # one canonical model only
        self.assertEqual(list(u["by_model"].keys()), ["claude-sonnet-4-6"])
        # full surface: 100+200+9000+700 + 1000 = 11000
        self.assertEqual(u["total_tokens"], 11000)
        self.assertEqual(u["cache_read_tokens"], 10000)
        self.assertEqual(u["cache_read_pct"], 90)  # 10000/11000 -> 90


class TestRoadmap(unittest.TestCase):
    def test_future_segments_visible(self):
        plan = (
            "## Segment 1: Done\n- [x] T1: a\n"
            "## Segment 2: Current\n- [~] T2: b\n- [ ] T3: c\n"
            "## Segment 3: Future   (unplanned)\nGoal: later stuff\n"  # no task lines
        )
        r = serve.roadmap(serve.parse_plan(plan))
        by = {s["name"].split(":")[0].strip(): s for s in r}
        self.assertEqual(by["Segment 1"]["state"], "done")
        self.assertEqual(by["Segment 2"]["state"], "current")
        # the future segment with NO task lines is still present and marked future/unplanned
        self.assertEqual(by["Segment 3"]["state"], "future")
        self.assertFalse(by["Segment 3"]["planned"])
        self.assertEqual(by["Segment 3"]["total"], 0)


CONFIG_SAMPLE = """# Loop Config
Worktree: /repo/.claude/worktrees/feat
Orchestrator model: sonnet
Planner tier: most-capable
Worker tier: standard
Limits: tick_timeout=1200
"""


class TestParseQuota(unittest.TestCase):
    def test_pending_reset(self):
        # A future resetsAt is surfaced (drives the live countdown); the label
        # comes from rateLimitType. utilization/pct is not part of the dashboard
        # quota contract — the terminal header shows %, the web card shows the
        # reset countdown / "within limits".
        q = serve.parse_quota(
            {"resetsAt": 2000, "rateLimitType": "five_hour"}, now=1000)
        self.assertEqual(q["label"], "5h")
        self.assertEqual(q["type"], "five_hour")
        self.assertEqual(q["resets_at"], 2000)

    def test_weekly_label(self):
        q = serve.parse_quota({"utilization": 0.1, "rateLimitType": "weekly"}, now=0)
        self.assertEqual(q["label"], "wk")

    def test_none_only_when_no_file(self):
        # No ratelimit.json -> _read_json returns None -> parse_quota returns None
        # (renders "—"). A present-but-clear object is a "within limits" dict,
        # not None: resets_at is None and the label falls back to "quota".
        self.assertIsNone(serve.parse_quota(None, now=0))
        q = serve.parse_quota({}, now=0)
        self.assertIsNone(q["resets_at"])
        self.assertEqual(q["label"], "quota")


class TestParseConfig(unittest.TestCase):
    def test_fields(self):
        c = serve.parse_config(CONFIG_SAMPLE)
        self.assertEqual(c["worktree"], "/repo/.claude/worktrees/feat")
        self.assertEqual(c["orchestrator_model"], "sonnet")
        self.assertEqual(c["tiers"]["Planner"], "most-capable")
        self.assertEqual(c["tiers"]["Worker"], "standard")
        self.assertEqual(c["limits"]["tick_timeout"], "1200")


RUNLOG_SAMPLE = "\n".join([
    "2026-06-16T00:00:00Z tick 26 starting",
    '{"type":"assistant","message":{"content":[{"type":"text","text":"done"}]}}',
    "2026-06-16T00:05:00Z tick 27 starting",
    '{"type":"assistant","message":{"model":"claude-opus","content":'
    '[{"type":"text","text":"EXECUTE TICK now"}]}}',
    '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Agent",'
    '"input":{"subagent_type":"Worker","model":"sonnet"}}]}}',
    '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash",'
    '"input":{"command":"pnpm lint"}}]}}',
    "not json at all",
])


class TestSliceCurrentTick(unittest.TestCase):
    def test_finds_last_tick(self):
        s = serve.slice_current_tick(RUNLOG_SAMPLE)
        self.assertEqual(s["tick"], 27)
        self.assertTrue(any("EXECUTE TICK" in ln for ln in s["lines"]))
        self.assertFalse(any("tick 26 starting" in ln for ln in s["lines"]))

    def test_no_marker(self):
        s = serve.slice_current_tick("just some text\n{}")
        self.assertIsNone(s["tick"])


class TestParseCurrentActivity(unittest.TestCase):
    def test_fields(self):
        s = serve.slice_current_tick(RUNLOG_SAMPLE)
        a = serve.parse_current_activity(s["lines"])
        self.assertEqual(a["mode"], "execute")
        self.assertEqual(a["subagent"], "Worker")
        self.assertEqual(a["model"], "sonnet")
        self.assertEqual(a["tools"], 2)

    def test_mode_default_execute(self):
        a = serve.parse_current_activity(['{"type":"assistant","message":{"content":[]}}'])
        self.assertEqual(a["mode"], "execute")

    def test_tolerates_garbage(self):
        a = serve.parse_current_activity(["nonsense", ""])
        self.assertEqual(a["tools"], 0)


EVENTS_SAMPLE = "\n".join([
    '{"t":100,"type":"tick_start","tick":7}',
    '{"t":101,"type":"role_start","role":"Scout","model":"sonnet"}',
    '{"t":102,"type":"tool","role":"Scout","name":"Read","count":1}',
    '{"t":103,"type":"role_end","role":"Scout"}',
    '{"t":103,"type":"handoff","from":"Scout","to":"Worker"}',
    '{"t":104,"type":"role_start","role":"Worker","model":"opus"}',
    '{"t":105,"type":"tool","role":"Worker","name":"Edit","count":2}',
    'garbage-not-json',
]) + "\n"


class TestTailEvents(unittest.TestCase):
    def test_offset_advances_and_parses(self):
        import io
        evs, off = serve.tail_events(io.StringIO(EVENTS_SAMPLE), 0)
        self.assertEqual(off, len(EVENTS_SAMPLE))
        self.assertEqual(evs[0]["type"], "tick_start")
        self.assertEqual([e for e in evs if e["type"] == "tool"][-1]["name"], "Edit")

    def test_resume_from_offset_returns_nothing_new(self):
        import io
        _, off = serve.tail_events(io.StringIO(EVENTS_SAMPLE), 0)
        evs2, off2 = serve.tail_events(io.StringIO(EVENTS_SAMPLE), off)
        self.assertEqual(evs2, [])
        self.assertEqual(off2, off)

    def test_partial_final_line_is_buffered_not_lost(self):
        import io
        # A complete line followed by a partial (un-terminated) line still being written.
        partial = '{"t":1,"type":"tick_start","tick":1}\n{"t":2,"type":"to'
        evs, off = serve.tail_events(io.StringIO(partial), 0)
        self.assertEqual(len(evs), 1)                 # only the complete line parsed
        self.assertEqual(off, len('{"t":1,"type":"tick_start","tick":1}\n'))  # offset stops at last newline
        # When the rest arrives, reading from the returned offset yields the now-complete line.
        full = partial + 'ol","role":"Worker","name":"Edit","count":1}\n'
        evs2, off2 = serve.tail_events(io.StringIO(full), off)
        self.assertEqual(len(evs2), 1)
        self.assertEqual(evs2[0]["name"], "Edit")
        self.assertEqual(off2, len(full))


class TestDeriveCurrent(unittest.TestCase):
    def test_latest_tick_activity(self):
        evs, _ = serve.tail_events_from_text(EVENTS_SAMPLE)
        cur = serve.derive_current(evs)
        self.assertEqual(cur["tick"], 7)
        self.assertEqual(cur["role"], "Worker")     # active = most recent role_start
        self.assertEqual(cur["model"], "opus")
        self.assertEqual(cur["tools"], 2)           # cumulative tool count this tick

    def test_pipeline_states(self):
        evs, _ = serve.tail_events_from_text(EVENTS_SAMPLE)
        pipe = serve.derive_pipeline(evs)
        # The pipeline is a now-playing tree: the orchestrator spine is the
        # parent, dispatched subagents are children in handoff order, and the
        # single currently-working actor is `active`.
        self.assertEqual(pipe["role"], "orchestrator")
        self.assertEqual(pipe["state"], "done")             # handed off to a subagent
        self.assertEqual(pipe["active"]["role"], "Worker")  # most recent role_start
        child_states = {c["role"]: c["state"] for c in pipe["children"]}
        self.assertEqual([c["role"] for c in pipe["children"]], ["Scout", "Worker"])
        self.assertEqual(child_states["Scout"], "done")     # role_end seen
        self.assertEqual(child_states["Worker"], "active")  # role_start, no role_end
        # Never-dispatched roles are NOT invented — no fixed taxonomy.
        self.assertNotIn("Planner", child_states)
        self.assertNotIn("Evaluator", child_states)

    def test_pipeline_orchestrator_only_phase(self):
        # Early in a tick the spine works alone before dispatching any subagent;
        # it must still show as the live node (the bug: it showed nothing).
        evs, _ = serve.tail_events_from_text("\n".join([
            '{"t":1,"type":"tick_start","tick":22}',
            '{"t":2,"type":"tool","role":"orchestrator","name":"Read","count":1}',
            '{"t":3,"type":"tool","role":"orchestrator","name":"Bash","count":2}',
        ]) + "\n")
        pipe = serve.derive_pipeline(evs)
        # Spine works alone: parent is the active node, no children yet.
        self.assertEqual(pipe["role"], "orchestrator")
        self.assertEqual(pipe["state"], "active")
        self.assertEqual(pipe["active"]["role"], "orchestrator")
        self.assertEqual(pipe["children"], [])

    def test_pipeline_roles_are_opaque_strings(self):
        # The role identifiers are whatever the provider's stream emits (here
        # model tiers). No model/provider literals are assumed anywhere.
        evs, _ = serve.tail_events_from_text("\n".join([
            '{"t":1,"type":"tick_start","tick":22}',
            '{"t":2,"type":"tool","role":"orchestrator","name":"Write","count":1}',
            '{"t":3,"type":"role_start","role":"sonnet"}',
            '{"t":4,"type":"tool","role":"sonnet","name":"Edit","count":2}',
            '{"t":5,"type":"role_end","role":"sonnet"}',
            '{"t":6,"type":"handoff","from":"sonnet","to":"opus"}',
            '{"t":7,"type":"role_start","role":"opus"}',
            '{"t":8,"type":"tool","role":"opus","name":"Bash","count":3}',
        ]) + "\n")
        pipe = serve.derive_pipeline(evs)
        child_states = {c["role"]: c["state"] for c in pipe["children"]}
        self.assertEqual(pipe["role"], "orchestrator")
        self.assertEqual([c["role"] for c in pipe["children"]], ["sonnet", "opus"])
        self.assertEqual(child_states["sonnet"], "done")
        self.assertEqual(child_states["opus"], "active")
        self.assertEqual(pipe["active"]["role"], "opus")


class TestDetectLoopStatus(unittest.TestCase):
    def test_done(self):
        self.assertEqual(
            serve.detect_loop_status(pause=False, lock=False, child_alive=False,
                                     last_log="LOOP_DONE after 5 ticks"), "done")

    def test_halted(self):
        self.assertEqual(
            serve.detect_loop_status(pause=False, lock=True, child_alive=True,
                                     last_log="HALT: no progress"), "halted")

    def test_paused(self):
        self.assertEqual(
            serve.detect_loop_status(pause=True, lock=True, child_alive=True,
                                     last_log="tick 3 starting"), "paused")

    def test_running(self):
        self.assertEqual(
            serve.detect_loop_status(pause=False, lock=True, child_alive=False,
                                     last_log="tick 3 starting"), "running")

    def test_idle(self):
        self.assertEqual(
            serve.detect_loop_status(pause=False, lock=False, child_alive=False,
                                     last_log=""), "idle")

    def test_stopped(self):
        self.assertEqual(
            serve.detect_loop_status(pause=False, lock=False, child_alive=False,
                                     last_log="tick 3 starting"), "stopped")


class TestEventVerdict(unittest.TestCase):
    def test_running_recent_event(self):
        v = serve.event_verdict(last_event_t=1000, now=1010, lock=True, pause=False,
                                last_log="tick 7 starting", quota=None, stall_s=45)
        self.assertEqual(v, "running")

    def test_stalled_lock_but_no_recent_event(self):
        v = serve.event_verdict(last_event_t=1000, now=1100, lock=True, pause=False,
                                last_log="tick 7 starting", quota=None, stall_s=45)
        self.assertEqual(v, "stalled")

    def test_rate_limited_when_quota_resetting(self):
        v = serve.event_verdict(last_event_t=1000, now=1100, lock=False, pause=False,
                                last_log="usage limit hit — sleeping",
                                quota={"resets_at": 9999}, stall_s=45)
        self.assertEqual(v, "rate-limited")

    def test_paused(self):
        v = serve.event_verdict(last_event_t=1000, now=1001, lock=False, pause=True,
                                last_log="tick 7 starting", quota=None, stall_s=45)
        self.assertEqual(v, "paused")

    def test_done_and_halted_win(self):
        self.assertEqual(serve.event_verdict(0, 1, False, False, "LOOP_DONE after 7", None, 45), "done")
        self.assertEqual(serve.event_verdict(0, 1, True, False, "HALT: no progress", None, 45), "halted")


class TestBuildSnapshot(unittest.TestCase):
    def _loop_dir(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "runtime"), exist_ok=True)
        for name, body in [("LOOP_PLAN.md", PLAN_SAMPLE), ("LOOP_USAGE.jsonl", USAGE_SAMPLE),
                           ("LOOP_CONFIG.md", CONFIG_SAMPLE), ("events.jsonl", EVENTS_SAMPLE)]:
            with open(os.path.join(d, name), "w") as f:
                f.write(body)
        return d

    def test_composes_sections(self):
        d = self._loop_dir()
        snap = serve.build_snapshot(d, now=200, status="running")
        self.assertEqual(snap["loop"]["status"], "running")
        self.assertEqual(snap["loop"]["tick"], 7)               # continuous tick from events
        self.assertEqual(snap["current"]["role"], "Worker")     # from events, not run.log
        self.assertEqual(snap["pipeline"]["active"]["role"], "Worker")
        self.assertIn("per_task", snap["usage"])
        # PLAN_SAMPLE: Segment A (2/4 done -> holds in-progress task -> current),
        # Segment B (0/2 done, after current is marked -> future). Mirrors TestRoadmap.
        self.assertEqual([s["state"] for s in snap["roadmap"]], ["current", "future"])

    def test_no_runlog_required(self):
        # The hot path must not depend on run.log existing (the 14 MB scrape is gone).
        d = self._loop_dir()
        os.remove(os.path.join(d, "events.jsonl"))  # even with no events yet
        snap = serve.build_snapshot(d, now=0, status="idle")
        self.assertEqual(snap["current"]["role"], None)
        self.assertIn("roadmap", snap)

    def test_missing_files_safe(self):
        d = tempfile.mkdtemp()
        snap = serve.build_snapshot(d, now=0, status="idle")
        self.assertEqual(snap["progress"]["total"], 0)
        self.assertIsNone(snap["quota"])


class TestSupervisor(unittest.TestCase):
    def _sup(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "runtime"), exist_ok=True)
        sup = serve.Supervisor(loop_dir=d, plugin_root="/plugin", worktree=d)
        sup.spawned = []
        sup._spawn = lambda: sup.spawned.append(True)  # stub the subprocess
        return sup, d

    def test_pause_creates_file(self):
        sup, d = self._sup()
        sup.pause()
        self.assertTrue(os.path.exists(os.path.join(d, "runtime", "PAUSE")))

    def test_resume_removes_file_and_spawns(self):
        sup, d = self._sup()
        sup.pause()
        sup.resume()
        self.assertFalse(os.path.exists(os.path.join(d, "runtime", "PAUSE")))
        self.assertEqual(len(sup.spawned), 1)

    def test_stop_creates_pause(self):
        sup, d = self._sup()
        sup.stop()
        self.assertTrue(os.path.exists(os.path.join(d, "runtime", "PAUSE")))

    def test_pause_path(self):
        sup, d = self._sup()
        self.assertEqual(sup.pause_path, os.path.join(d, "runtime", "PAUSE"))

    def test_lock_and_pause_detection(self):
        sup, d = self._sup()
        self.assertFalse(sup.pause_exists())
        sup.pause()
        self.assertTrue(sup.pause_exists())

    def test_no_spawn_resume_does_not_spawn(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "runtime"), exist_ok=True)
        sup = serve.Supervisor(loop_dir=d, plugin_root="/plugin", worktree=d,
                               no_spawn=True)
        sup.spawned = []
        sup._spawn = lambda: sup.spawned.append(True)
        sup.pause()
        sup.resume()
        self.assertFalse(os.path.exists(os.path.join(d, "runtime", "PAUSE")))
        self.assertEqual(len(sup.spawned), 0)


class TestLoopLiveness(unittest.TestCase):
    def _sup(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "runtime"), exist_ok=True)
        return serve.Supervisor(loop_dir=d, plugin_root="/plugin", worktree=d), d

    def test_lock_path_is_uppercase(self):
        sup, d = self._sup()
        # Must match the harness's $LOOP_DIR/runtime/LOCK (tick-prompt.md §14).
        self.assertEqual(sup.lock_path, os.path.join(d, "runtime", "LOCK"))

    def test_active_when_lock_present(self):
        sup, d = self._sup()
        open(os.path.join(d, "runtime", "LOCK"), "w").close()
        self.assertTrue(sup.loop_seems_active())

    def test_inactive_without_runlog(self):
        sup, _ = self._sup()
        self.assertFalse(sup.loop_seems_active())

    def test_active_with_recent_runlog(self):
        sup, d = self._sup()
        with open(os.path.join(d, "run.log"), "w") as f:
            f.write("2026-06-16T00:00:00Z tick 5 starting\n")
        self.assertTrue(sup.loop_seems_active())

    def test_inactive_with_stale_runlog(self):
        sup, d = self._sup()
        p = os.path.join(d, "run.log")
        with open(p, "w") as f:
            f.write("tick 5 starting\n")
        old = time.time() - 600
        os.utime(p, (old, old))
        self.assertFalse(sup.loop_seems_active())

    def test_inactive_when_done_even_if_recent(self):
        sup, d = self._sup()
        with open(os.path.join(d, "run.log"), "w") as f:
            f.write("LOOP_DONE after 5 ticks\n")
        self.assertFalse(sup.loop_seems_active())

    def test_status_running_between_ticks(self):
        sup, d = self._sup()
        with open(os.path.join(d, "run.log"), "w") as f:
            f.write("tick 5 starting\n")
        # No LOCK (between ticks) but recent run.log -> still 'running'.
        self.assertEqual(sup.status(), "running")


class TestCanonModel(unittest.TestCase):
    def test_canon_model_collapses_region_and_window_variants(self):
        self.assertEqual(serve._canon_model("claude-sonnet-4-6"), "claude-sonnet-4-6")
        self.assertEqual(serve._canon_model("us.anthropic.claude-sonnet-4-6"), "claude-sonnet-4-6")
        self.assertEqual(serve._canon_model("claude-opus-4-8[1m]"), "claude-opus-4-8")
        self.assertEqual(serve._canon_model("us.anthropic.claude-opus-4-8"), "claude-opus-4-8")
        self.assertEqual(serve._canon_model("claude-haiku-4-5-20251001"), "claude-haiku-4-5-20251001")
        self.assertEqual(serve._canon_model("us.anthropic.claude-haiku-4-5-20251001-v1:0"), "claude-haiku-4-5-20251001")


class TestTailLines(unittest.TestCase):
    def test_returns_last_n(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "f.log")
        with open(p, "w") as f:
            f.write("\n".join("line%d" % i for i in range(50)) + "\n")
        self.assertEqual(serve._tail_lines(p, 3), ["line47", "line48", "line49"])

    def test_missing_file(self):
        self.assertEqual(serve._tail_lines("/no/such/file.log"), [])

    def test_window_drops_partial_leading_line(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "big.log")
        with open(p, "w") as f:
            f.write("X" * 1000 + "\n" + "last\n")
        # tiny window forces a mid-file start -> leading partial dropped
        self.assertEqual(serve._tail_lines(p, 5, window=10), ["last"])


if __name__ == "__main__":
    unittest.main()
