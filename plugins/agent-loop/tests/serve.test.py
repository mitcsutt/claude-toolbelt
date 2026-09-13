#!/usr/bin/env python3
"""Unit tests for web/serve.py (stdlib unittest; no pip)."""
import json
import os
import sys
import tempfile
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


NOW = 1000


def mk(events, loop_dir=None):
    """Build an EventStore over a temp events.jsonl holding `events`."""
    d = loop_dir or tempfile.mkdtemp()
    path = os.path.join(d, "events.jsonl")
    with open(path, "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    store = serve.EventStore(path)
    store.refresh()
    return store


def live(**kw):
    """Default liveness: harness up, heartbeat fresh, a tick in flight."""
    base = {"pause": False, "stop": False, "harness_pid": 4812,
            "harness_pid_alive": True,
            "harness_started_at": 900, "heartbeat_age_s": 1,
            "tick_pid": 4999, "tick_pid_alive": True, "tick_no": 7,
            "tick_started_at": None, "tick_timeout_s": 1800,
            "activity_age_s": 2}
    base.update(kw)
    return base


def status_of(events, now=NOW, **livekw):
    return serve.derive_status(mk(events), live(**livekw), now)


class TestDeriveStatus(unittest.TestCase):
    """The status table. Pure function of (store, liveness, now) — never run.log."""

    def test_idle_no_events(self):
        self.assertEqual(status_of([])["state"], "idle")

    def test_running_with_tick_in_flight(self):
        s = status_of([
            {"t": 90, "type": "loop_start", "pid": 4812},
            {"t": 100, "type": "tick_start", "tick": 7, "pid": 4999},
            {"t": 101, "type": "role_start", "role": "Worker", "model": "sonnet"},
        ], tick_started_at=100)
        self.assertEqual(s["state"], "running")
        self.assertEqual(s["phase"], "Worker")
        self.assertEqual(s["since"], 100)
        self.assertIn("Worker (sonnet)", s["why"])
        self.assertIn("last activity 2s ago", s["why"])

    def test_running_names_the_current_task(self):
        s = status_of([
            {"t": 90, "type": "loop_start"},
            {"t": 100, "type": "tick_start", "tick": 7},
            {"t": 101, "type": "task_status", "id": "T34", "status": "doing"},
            {"t": 102, "type": "role_start", "role": "Worker", "model": "sonnet"},
        ])
        self.assertIn("on T34", s["why"])

    def test_between_ticks_from_sleep(self):
        s = status_of([
            {"t": 90, "type": "loop_start"},
            {"t": 100, "type": "tick_start", "tick": 7},
            {"t": 200, "type": "tick_end", "tick": 7, "verdict": "continue", "cause": "ok"},
            {"t": 200, "type": "sleep", "tick": 7, "until": NOW + 5,
             "reason": "between-ticks"},
        ])
        self.assertEqual(s["state"], "between-ticks")
        self.assertIn("next tick in 5s", s["why"])

    def test_sleep_in_the_same_second_as_tick_end_still_wins(self):
        # tick_end and the between-ticks sleep are emitted back to back; a
        # strict `sleep.t > tick_end.t` would call this "waiting for next tick"
        # with no countdown for the whole gap.
        s = status_of([
            {"t": 100, "type": "tick_start", "tick": 7},
            {"t": 200, "type": "tick_end", "tick": 7, "verdict": "continue"},
            {"t": 200, "type": "sleep", "until": NOW + 9, "reason": "backoff"},
        ])
        self.assertEqual(s["state"], "between-ticks")
        self.assertIn("backoff", s["why"])

    def test_rate_limited(self):
        s = status_of([
            {"t": 100, "type": "tick_start", "tick": 7},
            {"t": 200, "type": "tick_end", "tick": 7, "verdict": "retry"},
            {"t": 200, "type": "sleep", "until": NOW + 600, "reason": "rate-limit"},
        ])
        self.assertEqual(s["state"], "rate-limited")
        self.assertIn("usage window", s["why"])

    def test_memory_wait(self):
        s = status_of([
            {"t": 100, "type": "tick_start", "tick": 7},
            {"t": 200, "type": "tick_end", "tick": 7, "verdict": "continue"},
            {"t": 210, "type": "memory_pressure", "free_mb": 500, "swap_used_pct": 95,
             "action": "delay"},
            {"t": 210, "type": "sleep", "until": NOW + 60, "reason": "memory"},
        ])
        self.assertEqual(s["state"], "memory-wait")
        self.assertIn("memory", s["why"])

    def test_expired_sleep_does_not_stick(self):
        s = status_of([
            {"t": 100, "type": "tick_start", "tick": 7},
            {"t": 200, "type": "tick_end", "tick": 7, "verdict": "continue"},
            {"t": 200, "type": "sleep", "until": NOW - 1, "reason": "between-ticks"},
        ])
        self.assertEqual(s["state"], "between-ticks")
        self.assertEqual(s["why"], "waiting for next tick")

    def test_stalled(self):
        s = status_of([
            {"t": 90, "type": "loop_start"},
            {"t": 100, "type": "tick_start", "tick": 7},
        ], activity_age_s=400, tick_started_at=100, tick_timeout_s=1800)
        self.assertEqual(s["state"], "stalled")
        self.assertIn("no activity", s["why"])
        self.assertIn("killed in", s["why"])

    def test_pausing_while_harness_alive(self):
        s = status_of([
            {"t": 90, "type": "loop_start"},
            {"t": 100, "type": "tick_start", "tick": 7},
        ], pause=True)
        self.assertEqual(s["state"], "pausing")
        self.assertIn("stops after tick 7", s["why"])

    def test_stop_file_is_pausing_like_pause(self):
        s = status_of([
            {"t": 90, "type": "loop_start"},
            {"t": 100, "type": "tick_start", "tick": 7},
        ], pause=True, stop=True)
        self.assertEqual(s["state"], "pausing")
        self.assertIn("stop requested", s["why"])

    def test_stop_file_outliving_a_dead_harness_is_paused(self):
        s = status_of([
            {"t": 90, "type": "loop_start"},
            {"t": 100, "type": "tick_start", "tick": 7},
        ], pause=True, stop=True, harness_pid_alive=False)
        self.assertEqual(s["state"], "paused")
        self.assertIn("STOP present", s["why"])

    def test_paused_terminal(self):
        s = status_of([
            {"t": 90, "type": "loop_start"},
            {"t": 300, "type": "loop_end", "reason": "paused", "exit_code": 0},
        ])
        self.assertEqual(s["state"], "paused")

    def test_paused_when_pause_file_outlives_a_dead_harness(self):
        s = status_of([
            {"t": 90, "type": "loop_start"},
            {"t": 100, "type": "tick_start", "tick": 7},
        ], pause=True, harness_pid_alive=False)
        self.assertEqual(s["state"], "paused")
        self.assertIn("PAUSE present", s["why"])

    def test_done(self):
        s = status_of([
            {"t": 90, "type": "loop_start"},
            {"t": 300, "type": "loop_end", "reason": "done", "exit_code": 0},
        ])
        self.assertEqual(s["state"], "done")
        self.assertEqual(s["since"], 300)

    def test_halted_why_is_the_detail(self):
        s = status_of([
            {"t": 90, "type": "loop_start"},
            {"t": 300, "type": "loop_end", "reason": "halt",
             "detail": "blocked on T2", "exit_code": 1},
        ])
        self.assertEqual(s["state"], "halted")
        self.assertEqual(s["why"], "blocked on T2")

    def test_needs_human_from_loop_end(self):
        s = status_of([
            {"t": 90, "type": "loop_start"},
            {"t": 300, "type": "loop_end", "reason": "needs-human",
             "detail": "medic paused the run", "exit_code": 2},
        ])
        self.assertEqual(s["state"], "needs-human")
        self.assertEqual(s["why"], "medic paused the run")

    def test_needs_human_from_incident_severity(self):
        s = status_of([
            {"t": 90, "type": "loop_start"},
            {"t": 100, "type": "tick_start", "tick": 7},
            {"t": 150, "type": "incident", "id": "i-004", "kind": "tick-killed",
             "severity": "needs-human", "detail": "rc=137 twice", "tick": 7},
        ])
        self.assertEqual(s["state"], "needs-human")
        self.assertEqual(s["why"], "rc=137 twice")
        self.assertEqual(s["since"], 150)

    def test_stopped_on_signal_and_lock_conflict(self):
        for reason in ("signal", "lock-conflict"):
            s = status_of([
                {"t": 90, "type": "loop_start"},
                {"t": 300, "type": "loop_end", "reason": reason, "exit_code": 3},
            ])
            self.assertEqual(s["state"], "stopped", reason)

    def test_rate_limit_exit_is_paused(self):
        s = status_of([
            {"t": 90, "type": "loop_start"},
            {"t": 300, "type": "loop_end", "reason": "rate-limit-exit", "exit_code": 0},
        ])
        self.assertEqual(s["state"], "paused")
        self.assertIn("re-run to resume", s["why"])

    def test_crashed_when_harness_pid_is_gone(self):
        s = status_of([
            {"t": 90, "type": "loop_start", "pid": 4812},
            {"t": 100, "type": "tick_start", "tick": 7},
        ], harness_pid_alive=False, heartbeat_age_s=9)
        self.assertEqual(s["state"], "crashed")
        self.assertIn("4812", s["why"])

    def test_crashed_when_heartbeat_is_stale(self):
        s = status_of([
            {"t": 90, "type": "loop_start", "pid": 4812},
            {"t": 100, "type": "tick_start", "tick": 7},
        ], heartbeat_age_s=100)
        self.assertEqual(s["state"], "crashed")
        self.assertIn("last heartbeat", s["why"])

    def test_crashed_when_no_heartbeat_file(self):
        s = status_of([
            {"t": 90, "type": "loop_start", "pid": 4812},
            {"t": 100, "type": "tick_start", "tick": 7},
        ], heartbeat_age_s=None)
        self.assertEqual(s["state"], "crashed")

    def test_resume_after_terminal_is_running_again(self):
        # A loop_start newer than the loop_end means the operator restarted.
        s = status_of([
            {"t": 10, "type": "loop_end", "reason": "done", "exit_code": 0},
            {"t": 20, "type": "loop_start", "pid": 4812, "resume": 1},
            {"t": 21, "type": "tick_start", "tick": 8},
        ])
        self.assertEqual(s["state"], "running")

    def test_between_ticks_when_nothing_else_applies(self):
        s = status_of([
            {"t": 90, "type": "loop_start"},
            {"t": 100, "type": "tick_start", "tick": 7},
            {"t": 200, "type": "tick_end", "tick": 7, "verdict": "continue"},
        ])
        self.assertEqual(s["state"], "between-ticks")


class TestRunLogNeverDecidesStatus(unittest.TestCase):
    """The 1.2 regression: run.log text must not be able to stop the loop."""

    def test_halt_and_done_text_in_runlog_with_a_live_tick_is_running(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "runtime"), exist_ok=True)
        with open(os.path.join(d, "LOOP_PLAN.md"), "w") as f:
            f.write(PLAN_SAMPLE)
        # Exactly the poison that used to flip the badge: a subagent prompt
        # quoting the halt sentinel, and the word LOOP_DONE, in the tail.
        with open(os.path.join(d, "run.log"), "w") as f:
            f.write("dispatching Worker: if you cannot proceed report HALT: x\n")
            f.write("...and print LOOP_DONE when the plan is empty\n")
        store = mk([
            {"t": 90, "type": "loop_start", "pid": 4812},
            {"t": 100, "type": "tick_start", "tick": 12, "pid": 4999},
            {"t": 101, "type": "role_start", "role": "Worker", "model": "sonnet"},
            {"t": 102, "type": "tool", "role": "Worker", "name": "Edit", "count": 1},
        ], loop_dir=d)
        lv = live(tick_started_at=100)
        st = serve.derive_status(store, lv, NOW)
        snap = serve.build_snapshot(d, store, lv, st, NOW)
        self.assertEqual(snap["status"]["state"], "running")
        self.assertEqual(snap["loop"]["status"], "running")
        # ...and run.log is still there for the operator, purely as text.
        self.assertTrue(any("HALT:" in ln for ln in snap["log"]))


class TestEventStore(unittest.TestCase):
    def _path(self):
        return os.path.join(tempfile.mkdtemp(), "events.jsonl")

    def _append(self, path, events):
        with open(path, "a") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")

    def test_incremental_refresh_only_reads_the_tail(self):
        path = self._path()
        self._append(path, [
            {"t": 1, "type": "loop_start"},
            {"t": 2, "type": "tick_start", "tick": 1},
            {"t": 3, "type": "tool", "role": "Worker", "name": "Read", "count": 1},
        ])
        store = serve.EventStore(path)
        store.refresh()
        first_offset = store._offset
        self.assertEqual(first_offset, os.path.getsize(path))
        self.assertEqual(len(store.current), 1)
        self._append(path, [
            {"t": 4, "type": "tool", "role": "Worker", "name": "Edit", "count": 2},
            {"t": 5, "type": "tool", "role": "Worker", "name": "Bash", "count": 3},
        ])
        store.refresh()
        self.assertEqual(len(store.current), 3)
        self.assertGreater(store._offset, first_offset)
        self.assertEqual(store._offset, os.path.getsize(path))
        # A refresh with nothing appended re-parses nothing at all.
        store.refresh()
        self.assertEqual(len(store.current), 3)

    def test_partial_trailing_line_is_not_lost(self):
        path = self._path()
        with open(path, "a") as f:
            f.write(json.dumps({"t": 1, "type": "tick_start", "tick": 1}) + "\n")
            f.write('{"t":2,"type":"to')
        store = serve.EventStore(path)
        store.refresh()
        self.assertEqual(len(store.current), 0)
        with open(path, "a") as f:
            f.write('ol","role":"Worker","name":"Edit","count":1}\n')
        store.refresh()
        self.assertEqual(len(store.current), 1)
        self.assertEqual(store.current[0]["name"], "Edit")

    def test_truncation_resets_the_store(self):
        path = self._path()
        self._append(path, [{"t": i, "type": "tick_start", "tick": i} for i in range(1, 6)])
        store = serve.EventStore(path)
        store.refresh()
        self.assertEqual(len(store.lifecycle), 5)
        with open(path, "w") as f:            # a fresh run rewrites the file
            f.write(json.dumps({"t": 9, "type": "loop_start"}) + "\n")
        store.refresh()
        self.assertEqual(len(store.lifecycle), 1)
        self.assertEqual(store.lifecycle[0]["type"], "loop_start")
        self.assertEqual(store._offset, os.path.getsize(path))

    def test_memory_is_bounded_to_the_current_tick(self):
        # 3 ticks x 200 tool events: only the live tick's events are retained,
        # finished ticks survive as one aggregate dict each.
        path = self._path()
        evs = []
        t = 0
        for tick in (1, 2, 3):
            evs.append({"t": t, "type": "tick_start", "tick": tick})
            evs.append({"t": t, "type": "role_start", "role": "Worker", "model": "sonnet"})
            for i in range(200):
                t += 1
                evs.append({"t": t, "type": "tool", "role": "Worker",
                            "name": "Edit", "count": i + 1, "desc": "src/x.ts"})
            if tick < 3:
                evs.append({"t": t, "type": "task_status", "id": "T%d" % tick,
                            "status": "done", "sha": "abc123%d" % tick})
                evs.append({"t": t, "type": "tick_end", "tick": tick,
                            "verdict": "continue", "cause": "ok", "dur": 200})
        self._append(path, evs)
        store = serve.EventStore(path)
        store.refresh()
        tools = [e for e in store.current if e.get("type") == "tool"]
        self.assertEqual(len(tools), 200)
        self.assertEqual(len(store.ticks), 2)
        self.assertEqual(store.ticks[0]["tools"], 200)
        self.assertEqual(store.ticks[0]["roles"], ["Worker"])
        self.assertEqual(store.ticks[0]["task"], "T1")
        self.assertEqual(store.ticks[0]["cause"], "ok")
        self.assertEqual(store.cur_tick, 3)

    def test_incidents_fold_the_medic_outcome(self):
        store = mk([
            {"t": 10, "type": "incident", "id": "i-001", "kind": "tick-killed",
             "severity": "error", "detail": "rc=137", "tick": 11},
            {"t": 11, "type": "medic_start", "id": "i-001"},
            {"t": 90, "type": "medic_end", "id": "i-001", "outcome": "resumed",
             "summary": "memory pressure; retrying"},
        ])
        self.assertEqual(len(store.incidents), 1)
        self.assertEqual(store.incidents[0]["medic"]["outcome"], "resumed")
        self.assertEqual(store.incidents[0]["kind"], "tick-killed")

    def test_last_returns_the_newest_of_a_type(self):
        store = mk([
            {"t": 1, "type": "tick_start", "tick": 1},
            {"t": 2, "type": "tick_end", "tick": 1, "verdict": "continue"},
            {"t": 3, "type": "tick_start", "tick": 2},
        ])
        self.assertEqual(store.last("tick_start")["tick"], 2)
        self.assertIsNone(store.last("loop_end"))

    def test_missing_file_is_empty_not_an_error(self):
        store = serve.EventStore("/no/such/dir/events.jsonl")
        store.refresh()
        self.assertEqual(store.count, 0)


class TestArtifactAndDecisionFold(unittest.TestCase):
    """v3 event types: artifacts belong to the tick, decisions to the run."""

    def test_artifacts_fold_into_the_current_tick(self):
        store = mk([
            {"t": 1, "seq": 1, "type": "tick_start", "tick": 4},
            {"t": 2, "seq": 2, "type": "artifact", "tick": 4, "task": "T60",
             "name": "orgunits-list", "path": "artifacts/T60/orgunits-list.png"},
            {"t": 3, "seq": 3, "type": "artifact", "tick": 4, "task": "T60",
             "name": "orgunits-empty", "path": "artifacts/T60/orgunits-empty.png"},
        ])
        self.assertEqual(store.artifacts, [
            {"name": "orgunits-list", "path": "artifacts/T60/orgunits-list.png"},
            {"name": "orgunits-empty", "path": "artifacts/T60/orgunits-empty.png"},
        ])

    def test_a_new_tick_clears_the_previous_tick_artifacts(self):
        store = mk([
            {"t": 1, "seq": 1, "type": "tick_start", "tick": 4},
            {"t": 2, "seq": 2, "type": "artifact", "tick": 4, "task": "T60",
             "name": "a", "path": "artifacts/T60/a.png"},
            {"t": 3, "seq": 3, "type": "tick_start", "tick": 5},
        ])
        self.assertEqual(store.artifacts, [])
        # ...but the index keeps them addressable for a page still showing tick 4
        self.assertEqual(store.artifact_ix[(4, "a")], "artifacts/T60/a.png")

    def test_artifact_without_a_name_or_path_is_ignored(self):
        store = mk([
            {"t": 1, "seq": 1, "type": "tick_start", "tick": 4},
            {"t": 2, "seq": 2, "type": "artifact", "tick": 4, "name": "a"},
            {"t": 3, "seq": 3, "type": "artifact", "tick": 4, "path": "x.png"},
        ])
        self.assertEqual(store.artifacts, [])

    def test_decisions_survive_tick_boundaries(self):
        store = mk([
            {"t": 1, "seq": 1, "type": "tick_start", "tick": 4},
            {"t": 2, "seq": 2, "type": "decision", "task": "T60",
             "decision": "widen", "classification": "self-imposed"},
            {"t": 3, "seq": 3, "type": "resume", "task": "T60", "attempt": 2},
            {"t": 4, "seq": 4, "type": "tick_start", "tick": 5},
            {"t": 5, "seq": 5, "type": "split", "task": "T60",
             "into": ["T74", "T75"]},
        ])
        self.assertEqual([d["type"] for d in store.decisions],
                         ["decision", "resume", "split"])
        self.assertEqual(store.decisions[0]["decision"], "widen")
        self.assertEqual(store.decisions[0]["classification"], "self-imposed")
        self.assertEqual(store.decisions[1]["decision"], "resume")   # type is the default
        self.assertEqual(store.decisions[1]["attempt"], 2)
        self.assertEqual(store.decisions[2]["into"], ["T74", "T75"])

    def test_decisions_are_capped(self):
        evs = [{"t": 1, "seq": 1, "type": "tick_start", "tick": 1}]
        for i in range(serve._MAX_DECISIONS + 25):
            evs.append({"t": 2, "seq": i + 2, "type": "decision",
                        "task": "T%d" % i, "decision": "retry"})
        store = mk(evs)
        self.assertEqual(len(store.decisions), serve._MAX_DECISIONS)
        self.assertEqual(store.decisions[-1]["task"],
                         "T%d" % (serve._MAX_DECISIONS + 24))

    def test_none_of_the_new_types_becomes_a_lifecycle_event(self):
        for typ in ("artifact", "decision", "resume", "split"):
            self.assertNotIn(typ, serve.LIFECYCLE_TYPES)

    def test_snapshot_carries_artifacts_and_decisions(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "runtime"), exist_ok=True)
        store = mk([
            {"t": 1, "seq": 1, "type": "loop_start"},
            {"t": 2, "seq": 2, "type": "tick_start", "tick": 4},
            {"t": 3, "seq": 3, "type": "artifact", "tick": 4, "task": "T60",
             "name": "a", "path": "artifacts/T60/a.png"},
            {"t": 4, "seq": 4, "type": "decision", "task": "T60",
             "decision": "defer", "classification": "open"},
        ], loop_dir=d)
        snap = serve.build_snapshot(d, store, live(), {"state": "running"}, NOW)
        self.assertEqual(snap["current"]["artifacts"],
                         [{"name": "a", "path": "artifacts/T60/a.png"}])
        self.assertEqual(snap["decisions"][-1]["decision"], "defer")


class TestArtifactPath(unittest.TestCase):
    """The query string is a lookup key, never a path. Recorded paths are
    untrusted: the Scout writes them into the contract."""

    def _dir(self, events):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "artifacts", "T60"), exist_ok=True)
        with open(os.path.join(d, "artifacts", "T60", "shot.png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
        return d, mk(events, loop_dir=d)

    def test_resolves_a_recorded_relative_path(self):
        d, store = self._dir([
            {"t": 1, "seq": 1, "type": "tick_start", "tick": 4},
            {"t": 2, "seq": 2, "type": "artifact", "tick": 4, "task": "T60",
             "name": "shot", "path": "artifacts/T60/shot.png"},
        ])
        got = serve.artifact_path(d, store, 4, "shot")
        self.assertEqual(got, os.path.realpath(
            os.path.join(d, "artifacts", "T60", "shot.png")))

    def test_unknown_key_is_none(self):
        d, store = self._dir([{"t": 1, "seq": 1, "type": "tick_start", "tick": 4}])
        self.assertIsNone(serve.artifact_path(d, store, 4, "shot"))
        self.assertIsNone(serve.artifact_path(d, store, 99, "shot"))

    def test_traversal_out_of_the_artifacts_dir_is_refused(self):
        d, store = self._dir([
            {"t": 1, "seq": 1, "type": "tick_start", "tick": 4},
            {"t": 2, "seq": 2, "type": "artifact", "tick": 4, "task": "T60",
             "name": "escape", "path": "artifacts/T60/../../LOOP_CONFIG.md"},
        ])
        with open(os.path.join(d, "LOOP_CONFIG.md"), "w") as f:
            f.write("Worktree: /x\n")
        self.assertIsNone(serve.artifact_path(d, store, 4, "escape"))

    def test_absolute_path_outside_the_loop_dir_is_refused(self):
        d, store = self._dir([
            {"t": 1, "seq": 1, "type": "tick_start", "tick": 4},
            {"t": 2, "seq": 2, "type": "artifact", "tick": 4, "task": "T60",
             "name": "abs", "path": "/etc/hosts"},
        ])
        self.assertIsNone(serve.artifact_path(d, store, 4, "abs"))

    def test_symlink_out_of_the_artifacts_dir_is_refused(self):
        d, store = self._dir([
            {"t": 1, "seq": 1, "type": "tick_start", "tick": 4},
            {"t": 2, "seq": 2, "type": "artifact", "tick": 4, "task": "T60",
             "name": "link", "path": "artifacts/T60/link.png"},
        ])
        outside = os.path.join(tempfile.mkdtemp(), "secret.png")
        with open(outside, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
        os.symlink(outside, os.path.join(d, "artifacts", "T60", "link.png"))
        self.assertIsNone(serve.artifact_path(d, store, 4, "link"))

    def test_non_image_extension_is_refused(self):
        d, store = self._dir([
            {"t": 1, "seq": 1, "type": "tick_start", "tick": 4},
            {"t": 2, "seq": 2, "type": "artifact", "tick": 4, "task": "T60",
             "name": "sh", "path": "artifacts/T60/evil.sh"},
        ])
        with open(os.path.join(d, "artifacts", "T60", "evil.sh"), "w") as f:
            f.write("rm -rf /\n")
        self.assertIsNone(serve.artifact_path(d, store, 4, "sh"))

    def test_artifact_path_reads_what_render_writes(self):
        """The two halves must agree on what `path` is relative to.

        A real launch is `LOOP_DIR=.claude/loop/<run-id> bash run.sh`, so the
        runner's loop_dir is RELATIVE while serve.py abspaths its own. If the
        artifact event carried an emitter-relative path, joining it onto the
        absolute loop_dir would double the prefix and every screenshot in the
        dashboard would 404. render.py records `artifacts/<T>/<name>.png`.
        """
        wt = tempfile.mkdtemp()
        rel_loop = os.path.join(".claude", "loop", "run-x")
        loop_abs = os.path.join(wt, rel_loop)
        os.makedirs(os.path.join(loop_abs, "artifacts", "T1"))
        shot = os.path.join(loop_abs, "artifacts", "T1", "home.png")
        with open(shot, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
        store = mk([
            {"t": 1, "seq": 1, "type": "tick_start", "tick": 1},
            {"t": 2, "seq": 2, "type": "artifact", "tick": 1, "task": "T1",
             "name": "home",
             # exactly what render.py emits, for a relative LOOP_DIR
             "path": os.path.join("artifacts", "T1", "home.png")},
        ], loop_dir=loop_abs)
        self.assertEqual(serve.artifact_path(loop_abs, store, 1, "home"),
                         os.path.realpath(shot))

    def test_recorded_but_deleted_file_is_none(self):
        d, store = self._dir([
            {"t": 1, "seq": 1, "type": "tick_start", "tick": 4},
            {"t": 2, "seq": 2, "type": "artifact", "tick": 4, "task": "T60",
             "name": "gone", "path": "artifacts/T60/gone.png"},
        ])
        self.assertIsNone(serve.artifact_path(d, store, 4, "gone"))


class TestBuildSnapshot(unittest.TestCase):
    def test_snapshot_names_the_log_file_it_tailed(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "runtime"), exist_ok=True)
        with open(os.path.join(d, "harness.log"), "w") as f:
            f.write("tick 3 phase evaluator rc=0\n")
        store = mk([{"t": 1, "type": "loop_start"}], loop_dir=d)
        snap = serve.build_snapshot(d, store, live(), {"state": "idle"}, NOW)
        self.assertEqual(snap["log_file"], "harness.log")
        self.assertEqual(snap["log"], ["tick 3 phase evaluator rc=0"])

    def _loop_dir(self, events=EVENTS_SAMPLE):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "runtime"), exist_ok=True)
        for name, body in [("LOOP_PLAN.md", PLAN_SAMPLE), ("LOOP_USAGE.jsonl", USAGE_SAMPLE),
                           ("LOOP_CONFIG.md", CONFIG_SAMPLE), ("events.jsonl", events)]:
            with open(os.path.join(d, name), "w") as f:
                f.write(body)
        return d

    def _snap(self, d, now=200, stall_s=300, **livekw):
        store = serve.EventStore(os.path.join(d, "events.jsonl"))
        store.refresh()
        lv = live(**livekw)
        st = serve.derive_status(store, lv, now, stall_s=stall_s)
        return serve.build_snapshot(d, store, lv, st, now, stall_s=stall_s)

    def test_composes_sections(self):
        snap = self._snap(self._loop_dir())
        self.assertEqual(snap["loop"]["tick"], 7)               # continuous tick from events
        self.assertEqual(snap["current"]["role"], "Worker")     # from events, not run.log
        self.assertEqual(snap["pipeline"]["active"]["role"], "Worker")
        self.assertIn("per_task", snap["usage"])
        # PLAN_SAMPLE: Segment A (2/4 done -> holds in-progress task -> current),
        # Segment B (0/2 done, after current is marked -> future). Mirrors TestRoadmap.
        self.assertEqual([s["state"] for s in snap["roadmap"]], ["current", "future"])

    def test_contract_keys_present_and_narrative_gone(self):
        snap = self._snap(self._loop_dir())
        for key in ("status", "health", "ticks", "incidents", "progress",
                    "roadmap", "plan", "usage", "config", "log"):
            self.assertIn(key, snap)
        self.assertNotIn("narrative", snap)
        self.assertEqual(snap["loop"]["status"], snap["status"]["state"])
        self.assertIn("harness", snap["health"])
        self.assertIn("tick", snap["health"])
        self.assertIn("dashboard", snap["health"])
        self.assertEqual(snap["health"]["harness"]["pid"], 4812)
        self.assertEqual(snap["config"]["dashboard"], "auto")   # absent line -> auto
        self.assertEqual(snap["config"]["medic"], "auto")
        self.assertEqual(snap["now"], 200)                      # server clock

    def test_stall_s_is_echoed_from_the_same_setting_derive_status_used(self):
        d = self._loop_dir()
        self.assertEqual(self._snap(d)["health"]["tick"]["stall_s"], 300)
        snap = self._snap(d, stall_s=45, activity_age_s=60)
        self.assertEqual(snap["health"]["tick"]["stall_s"], 45)
        # ...and the page's threshold is the one the verdict was made with.
        self.assertEqual(snap["status"]["state"], "stalled")

    def test_dashboard_alive_reads_runtime_dashboard_json(self):
        d = self._loop_dir()
        # No dashboard.json yet -> not alive, but we still name our own pid.
        snap = self._snap(d)
        self.assertFalse(snap["health"]["dashboard"]["alive"])
        self.assertEqual(snap["health"]["dashboard"]["pid"], os.getpid())
        with open(os.path.join(d, "runtime", "dashboard.json"), "w") as f:
            json.dump({"pid": os.getpid(), "port": 1, "url": "http://x",
                       "sidecar": True}, f)
        snap = self._snap(d)
        self.assertTrue(snap["health"]["dashboard"]["alive"])
        self.assertEqual(snap["health"]["dashboard"]["pid"], os.getpid())
        # A dead pid in the file is reported dead, not assumed live.
        with open(os.path.join(d, "runtime", "dashboard.json"), "w") as f:
            json.dump({"pid": 999999, "port": 1, "url": "http://x"}, f)
        snap = self._snap(d)
        self.assertFalse(snap["health"]["dashboard"]["alive"])

    def test_resume_cmd_is_always_present_and_runnable(self):
        d = self._loop_dir()
        cmd = self._snap(d)["status"]["resume_cmd"]
        # CONFIG_SAMPLE's Worktree wins over the loop dir's ancestry.
        self.assertTrue(cmd.startswith("cd /repo/.claude/worktrees/feat &&"), cmd)
        self.assertIn("LOOP_DIR=.claude/loop/%s" % os.path.basename(d), cmd)
        self.assertIn("run.sh", cmd)
        self.assertNotIn("None", cmd)

    def test_resume_cmd_falls_back_to_the_loop_dir_ancestry(self):
        # No Worktree line: <worktree>/.claude/loop/<name> is the layout.
        d = tempfile.mkdtemp()
        nested = os.path.join(d, ".claude", "loop", "run-1")
        os.makedirs(os.path.join(nested, "runtime"))
        cmd = self._snap(nested)["status"]["resume_cmd"]
        self.assertTrue(cmd.startswith("cd %s &&" % d), cmd)
        self.assertIn("LOOP_DIR=.claude/loop/run-1", cmd)

    def test_config_modes_are_read_from_the_file(self):
        d = self._loop_dir()
        with open(os.path.join(d, "LOOP_CONFIG.md"), "a") as f:
            f.write("Dashboard: off\nMedic: notify\nMedic model: haiku\n")
        snap = self._snap(d)
        self.assertEqual(snap["config"]["dashboard"], "off")
        self.assertEqual(snap["config"]["medic"], "notify")

    def test_ticks_carry_aggregates_and_ledger_cost(self):
        d = self._loop_dir(events="\n".join([
            '{"t":100,"type":"tick_start","tick":1}',
            '{"t":101,"type":"role_start","role":"Scout","model":"haiku"}',
            '{"t":102,"type":"tool","role":"Scout","name":"Read","count":1}',
            '{"t":103,"type":"task_status","id":"T1","status":"done","sha":"a1b2c3d"}',
            '{"t":104,"type":"tick_end","tick":1,"verdict":"continue","cause":"ok","dur":4}',
            '{"t":105,"type":"tick_start","tick":2}',
        ]) + "\n")
        snap = self._snap(d)
        self.assertEqual(len(snap["ticks"]), 1)
        row = snap["ticks"][0]
        self.assertEqual(row["tick"], 1)
        self.assertEqual(row["cause"], "ok")
        self.assertEqual(row["tools"], 1)
        self.assertEqual(row["roles"], ["Scout"])
        self.assertEqual(row["task"], "T1")
        self.assertEqual(row["sha"], "a1b2c3d")
        self.assertEqual(row["mode"], "plan")            # joined from LOOP_USAGE
        self.assertAlmostEqual(row["cost_usd"], 0.5)

    def test_incidents_surface_in_the_snapshot(self):
        d = self._loop_dir(events="\n".join([
            '{"t":90,"type":"loop_start"}',
            '{"t":95,"type":"incident","id":"i-001","kind":"tick-killed",'
            '"severity":"error","detail":"rc=137","tick":1}',
            '{"t":96,"type":"medic_end","id":"i-001","outcome":"resumed","summary":"ok"}',
            '{"t":100,"type":"tick_start","tick":2}',
        ]) + "\n")
        snap = self._snap(d)
        self.assertEqual(len(snap["incidents"]), 1)
        self.assertEqual(snap["incidents"][0]["id"], "i-001")
        self.assertEqual(snap["incidents"][0]["medic"]["outcome"], "resumed")

    def test_no_runlog_required(self):
        # The hot path must not depend on run.log existing (the 14 MB scrape is gone).
        d = self._loop_dir()
        os.remove(os.path.join(d, "events.jsonl"))  # even with no events yet
        snap = self._snap(d, now=0)
        self.assertEqual(snap["current"]["role"], None)
        self.assertEqual(snap["status"]["state"], "idle")
        self.assertIn("roadmap", snap)

    def test_missing_files_safe(self):
        d = tempfile.mkdtemp()
        snap = self._snap(d, now=0)
        self.assertEqual(snap["progress"]["total"], 0)
        self.assertIsNone(snap["quota"])
        self.assertEqual(snap["log"], [])


    def test_schema_stamp_and_migration_event_surface(self):
        d = self._loop_dir(events="\n".join([
            '{"t":90,"type":"loop_start"}',
            '{"t":91,"type":"migration","from":1,"to":2,"actions":"legacy-lock-removed",'
            '"plugin_version":"2.0.0"}',
            '{"t":100,"type":"tick_start","tick":42}',
        ]) + "\n")
        with open(os.path.join(d, "runtime", "schema"), "w") as f:
            f.write("2")
        snap = self._snap(d)
        self.assertEqual(snap["loop"]["schema"], 2)
        self.assertEqual(snap["loop"]["migration"],
                         {"from": 1, "to": 2, "actions": "legacy-lock-removed", "at": 91})

    def test_schema_absent_is_null_not_a_crash(self):
        snap = self._snap(self._loop_dir())
        self.assertIsNone(snap["loop"]["schema"])
        self.assertIsNone(snap["loop"]["migration"])
        with open(os.path.join(self._loop_dir(), "runtime", "schema"), "w") as f:
            f.write("junk")  # a garbage stamp is null, never an exception

class TestProgressSegments(unittest.TestCase):
    PLAN = (
        "## Segment 1: Done\n- [x] T1: a\n- [-] T2: dropped\n"
        "## Segment 2: Current\n- [~] T3: b\n- [ ] T4: c\n"
        "## Segment 3: Later\nGoal: unplanned\n"
    )

    def test_segment_stats(self):
        plan = serve.parse_plan(self.PLAN)
        self.assertEqual(serve.segment_stats(plan), (3, 1, 1))

    def test_pct_basis_is_segments_when_any_are_unplanned(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "runtime"), exist_ok=True)
        with open(os.path.join(d, "LOOP_PLAN.md"), "w") as f:
            f.write(self.PLAN)
        store = mk([], loop_dir=d)
        snap = serve.build_snapshot(d, store, live(), serve.derive_status(store, live(), NOW), NOW)
        p = snap["progress"]
        self.assertEqual(p["segments_total"], 3)
        self.assertEqual(p["segments_done"], 1)
        self.assertEqual(p["segments_unplanned"], 1)
        self.assertEqual(p["pct_basis"], "segments")
        self.assertEqual(p["pct"], 33)          # 1 of 3 segments, not 2 of 4 tasks

    def test_pct_basis_is_tasks_when_every_segment_is_planned(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "runtime"), exist_ok=True)
        with open(os.path.join(d, "LOOP_PLAN.md"), "w") as f:
            f.write("## S1\n- [x] T1: a\n- [ ] T2: b\n")
        store = mk([], loop_dir=d)
        snap = serve.build_snapshot(d, store, live(), serve.derive_status(store, live(), NOW), NOW)
        self.assertEqual(snap["progress"]["pct_basis"], "tasks")
        self.assertEqual(snap["progress"]["pct"], 50)


class TestSupervisorGuards(unittest.TestCase):
    """start/resume must never race a live harness, whoever started it."""

    def _sup(self, pid=4242):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "runtime"), exist_ok=True)
        with open(os.path.join(d, "runtime", "harness.json"), "w") as f:
            json.dump({"pid": pid, "start_epoch": 1, "host": "x", "loop_dir": d,
                       "plugin_version": "2.0.0"}, f)
        sup = serve.Supervisor(loop_dir=d, plugin_root="/plugin", worktree=d)
        sup.spawned = []
        sup._spawn = lambda: sup.spawned.append(True)
        return sup, d

    def test_start_refuses_when_harness_pid_is_alive(self):
        sup, _ = self._sup()
        orig = serve.process_alive
        serve.process_alive = lambda pid, must_contain=None: True
        try:
            res = sup.start()
        finally:
            serve.process_alive = orig
        self.assertEqual(res, {"error": "harness pid 4242 is alive"})
        self.assertEqual(sup.spawned, [])

    def test_resume_refuses_when_harness_pid_is_alive(self):
        sup, d = self._sup()
        sup.pause()
        orig = serve.process_alive
        serve.process_alive = lambda pid, must_contain=None: True
        try:
            res = sup.resume()
        finally:
            serve.process_alive = orig
        self.assertIn("error", res)
        self.assertEqual(sup.spawned, [])
        self.assertTrue(os.path.exists(os.path.join(d, "runtime", "PAUSE")))

    def test_start_spawns_when_the_recorded_pid_is_dead(self):
        sup, _ = self._sup()
        orig = serve.process_alive
        serve.process_alive = lambda pid, must_contain=None: False
        try:
            res = sup.start()
        finally:
            serve.process_alive = orig
        self.assertIsNone(res)
        self.assertEqual(len(sup.spawned), 1)

    def test_stop_writes_the_stop_sentinel_for_a_live_harness(self):
        sup, d = self._sup(pid=777)
        killed = []
        orig_alive, orig_kill = serve.process_alive, os.kill
        serve.process_alive = lambda pid, must_contain=None: True
        os.kill = lambda pid, sig: killed.append((pid, sig))
        try:
            res = sup.stop()
        finally:
            serve.process_alive, os.kill = orig_alive, orig_kill
        self.assertIsNone(res)
        self.assertEqual(killed, [])          # the runner stops itself; no signal from here
        self.assertTrue(os.path.exists(os.path.join(d, "runtime", "STOP")))
        self.assertFalse(os.path.exists(os.path.join(d, "runtime", "PAUSE")))

    def test_stop_refuses_when_no_harness_is_alive(self):
        sup, d = self._sup(pid=777)
        orig = serve.process_alive
        serve.process_alive = lambda pid, must_contain=None: False
        try:
            res = sup.stop()
        finally:
            serve.process_alive = orig
        self.assertEqual(res, {"error": "no live harness to stop"})
        self.assertFalse(os.path.exists(os.path.join(d, "runtime", "STOP")))

    def test_resume_clears_a_stop_left_by_a_previous_run(self):
        sup, d = self._sup(pid=777)
        os.makedirs(os.path.join(d, "runtime"), exist_ok=True)
        open(os.path.join(d, "runtime", "STOP"), "w").close()
        orig = serve.process_alive
        serve.process_alive = lambda pid, must_contain=None: False
        try:
            self.assertIsNone(sup.resume())
        finally:
            serve.process_alive = orig
        self.assertFalse(os.path.exists(os.path.join(d, "runtime", "STOP")))
        self.assertEqual(len(sup.spawned), 1)


class TestSpawnEnvironment(unittest.TestCase):
    """The dashboard that spawns run.sh IS the dashboard: the child must not open
    a sidecar, and must live in its own session so a dashboard crash cannot take
    the loop down with it."""

    def test_spawn_disables_sidecar_and_detaches_session(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "runtime"), exist_ok=True)
        sup = serve.Supervisor(loop_dir=d, plugin_root="/plugin", worktree=d)
        calls = []

        class FakeProc:
            def poll(self):
                return None

        def fake_popen(argv, **kw):
            calls.append((argv, kw))
            return FakeProc()
        orig = serve.subprocess.Popen
        serve.subprocess.Popen = fake_popen
        try:
            sup._spawn()
        finally:
            serve.subprocess.Popen = orig
        self.assertEqual(len(calls), 1)
        argv, kw = calls[0]
        self.assertEqual(argv, ["bash", "/plugin/run.sh"])
        self.assertEqual(kw["cwd"], d)
        self.assertEqual(kw["env"]["LOOP_DIR"], d)
        self.assertEqual(kw["env"]["LOOP_DASHBOARD"], "off")
        self.assertTrue(kw.get("start_new_session"))


class TestRunDetached(unittest.TestCase):
    """--detach: reuse a live dashboard, else launch one in a new session and
    hand back its banner once runtime/dashboard.json names the child."""

    def _dir(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "runtime"), exist_ok=True)
        return d, os.path.join(d, "runtime")

    def _run(self, d, rt, launcher, alive, timeout=2.0):
        import io, contextlib
        buf = io.StringIO()
        orig = serve.process_alive
        serve.process_alive = lambda pid, must_contain=None: alive
        try:
            with contextlib.redirect_stdout(buf):
                rc = serve.run_detached(d, rt, ["--loop-dir", d, "--detach"],
                                        launcher=launcher, timeout=timeout)
        finally:
            serve.process_alive = orig
        return rc, json.loads(buf.getvalue().strip().splitlines()[-1])

    def test_reuses_a_live_dashboard_without_launching(self):
        d, rt = self._dir()
        with open(os.path.join(rt, "dashboard.json"), "w") as f:
            json.dump({"pid": 4242, "port": 61000, "url": "http://127.0.0.1:61000",
                       "sidecar": False}, f)

        def never(argv, out_path):
            raise AssertionError("must not launch over a live dashboard")
        rc, banner = self._run(d, rt, never, alive=True)
        self.assertEqual(rc, 0)
        self.assertEqual(banner["type"], "dashboard-started")
        self.assertEqual(banner["url"], "http://127.0.0.1:61000")
        self.assertEqual(banner["pid"], 4242)
        self.assertTrue(banner["reused"])

    def test_launches_and_waits_for_the_child_to_register(self):
        d, rt = self._dir()
        seen = {}

        def launcher(argv, out_path):
            seen["argv"] = argv
            seen["out"] = out_path
            with open(os.path.join(rt, "dashboard.json"), "w") as f:
                json.dump({"pid": 555, "port": 61001, "url": "http://127.0.0.1:61001",
                           "sidecar": False}, f)
            return 555
        rc, banner = self._run(d, rt, launcher, alive=False)
        self.assertEqual(rc, 0)
        self.assertEqual(banner["url"], "http://127.0.0.1:61001")
        self.assertEqual(banner["pid"], 555)
        self.assertFalse(banner["reused"])
        self.assertNotIn("--detach", seen["argv"])          # the child must not re-detach
        self.assertIn("--loop-dir", seen["argv"])
        self.assertEqual(seen["out"], os.path.join(rt, "dashboard.out"))

    def test_child_that_never_registers_is_reported_not_hung_on(self):
        d, rt = self._dir()
        rc, banner = self._run(d, rt, lambda argv, out: 777, alive=False, timeout=0.3)
        self.assertEqual(rc, 1)
        self.assertEqual(banner["type"], "dashboard-failed")
        self.assertEqual(banner["pid"], 777)


class TestProcessAlive(unittest.TestCase):
    def test_self_is_alive(self):
        self.assertTrue(serve.process_alive(os.getpid()))

    def test_command_filter_rejects_this_process(self):
        # This interpreter is not a run.sh; the filter is what defeats PID reuse.
        self.assertFalse(serve.process_alive(os.getpid(), "run.sh"))

    def test_bad_pids(self):
        self.assertFalse(serve.process_alive(None))
        self.assertFalse(serve.process_alive(0))
        self.assertFalse(serve.process_alive("nonsense"))
        self.assertFalse(serve.process_alive(999999))


class TestLivenessFiles(unittest.TestCase):
    def _dir(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "runtime"), exist_ok=True)
        return d

    def test_empty_runtime(self):
        d = self._dir()
        lv = serve.liveness(d, NOW)
        self.assertFalse(lv["pause"])
        self.assertIsNone(lv["harness_pid"])
        self.assertFalse(lv["harness_pid_alive"])
        self.assertIsNone(lv["heartbeat_age_s"])
        self.assertIsNone(lv["activity_age_s"])
        self.assertIsNone(lv["tick_no"])

    def test_reads_runtime_files(self):
        d = self._dir()
        rt = os.path.join(d, "runtime")
        with open(os.path.join(rt, "harness.json"), "w") as f:
            json.dump({"pid": os.getpid(), "start_epoch": 900}, f)
        with open(os.path.join(rt, "tick.json"), "w") as f:
            json.dump({"tick": 12, "pid": os.getpid(), "started_at": 950,
                       "timeout_s": 1800}, f)
        with open(os.path.join(rt, "HEARTBEAT"), "w") as f:
            f.write("%d\n" % (NOW - 3))
        with open(os.path.join(rt, "last-activity"), "w") as f:
            f.write("%d\n" % (NOW - 4))
        open(os.path.join(rt, "PAUSE"), "w").close()
        lv = serve.liveness(d, NOW)
        self.assertTrue(lv["pause"])
        self.assertEqual(lv["heartbeat_age_s"], 3)
        self.assertEqual(lv["activity_age_s"], 4)
        self.assertEqual(lv["tick_no"], 12)
        self.assertEqual(lv["tick_timeout_s"], 1800)
        self.assertTrue(lv["tick_pid_alive"])          # no command filter on the tick
        self.assertFalse(lv["harness_pid_alive"])      # ...but this is not a run.sh
        self.assertEqual(lv["harness_started_at"], 900)

    def test_stop_sentinel_sets_both_flags(self):
        d = tempfile.mkdtemp()
        rt = os.path.join(d, "runtime")
        os.makedirs(rt, exist_ok=True)
        open(os.path.join(rt, "STOP"), "w").close()
        lv = serve.liveness(d, 1000)
        self.assertTrue(lv["stop"])
        self.assertTrue(lv["pause"])       # STOP is a strictly stronger PAUSE

    def test_garbage_heartbeat_is_none(self):
        d = self._dir()
        with open(os.path.join(d, "runtime", "HEARTBEAT"), "w") as f:
            f.write("not-an-epoch\n")
        self.assertIsNone(serve.heartbeat_age(os.path.join(d, "runtime"), NOW))


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

    def test_stop_refuses_without_a_harness_json(self):
        sup, d = self._sup()
        self.assertEqual(sup.stop(), {"error": "no live harness to stop"})
        self.assertFalse(os.path.exists(os.path.join(d, "runtime", "STOP")))

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


class TestCanonModel(unittest.TestCase):
    def test_canon_model_collapses_region_and_window_variants(self):
        self.assertEqual(serve._canon_model("claude-sonnet-4-6"), "claude-sonnet-4-6")
        self.assertEqual(serve._canon_model("us.anthropic.claude-sonnet-4-6"), "claude-sonnet-4-6")
        self.assertEqual(serve._canon_model("claude-opus-4-8[1m]"), "claude-opus-4-8")
        self.assertEqual(serve._canon_model("us.anthropic.claude-opus-4-8"), "claude-opus-4-8")
        self.assertEqual(serve._canon_model("claude-haiku-4-5-20251001"), "claude-haiku-4-5-20251001")
        self.assertEqual(serve._canon_model("us.anthropic.claude-haiku-4-5-20251001-v1:0"), "claude-haiku-4-5-20251001")


class TestLogTail(unittest.TestCase):
    """v3 writes harness.log; a v2 dir still has only run.log."""

    def test_prefers_harness_log(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "harness.log"), "w") as f:
            f.write("phase scout ok\n")
        with open(os.path.join(d, "run.log"), "w") as f:
            f.write("old v2 line\n")
        lines, name = serve._log_tail(d)
        self.assertEqual(lines, ["phase scout ok"])
        self.assertEqual(name, "harness.log")

    def test_falls_back_to_run_log(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "run.log"), "w") as f:
            f.write("old v2 line\n")
        lines, name = serve._log_tail(d)
        self.assertEqual(lines, ["old v2 line"])
        self.assertEqual(name, "run.log")

    def test_neither_file_is_not_an_error(self):
        self.assertEqual(serve._log_tail(tempfile.mkdtemp()), ([], "harness.log"))


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


class TestBindServerFallback(unittest.TestCase):
    """A busy requested port (a 1.x dashboard still up during migration, or any
    other listener) must not stop the sidecar: fall back to an ephemeral port."""

    def test_falls_back_to_ephemeral_when_requested_port_is_taken(self):
        import socket
        from http.server import BaseHTTPRequestHandler
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        busy = blocker.getsockname()[1]
        try:
            httpd = serve._bind_server(busy, BaseHTTPRequestHandler)
            try:
                self.assertNotEqual(httpd.server_address[1], busy)
                self.assertGreater(httpd.server_address[1], 0)
            finally:
                httpd.server_close()
        finally:
            blocker.close()

    def test_binds_requested_port_when_free(self):
        from http.server import BaseHTTPRequestHandler
        probe = serve._bind_server(0, BaseHTTPRequestHandler)
        free = probe.server_address[1]
        probe.server_close()
        httpd = serve._bind_server(free, BaseHTTPRequestHandler)
        try:
            self.assertEqual(httpd.server_address[1], free)
        finally:
            httpd.server_close()


class TestMockMatchesFixture(unittest.TestCase):
    """`dashboard.html`'s MOCK claims byte-identity with the checked-in fixture.

    Nothing asserted that claim, and it had drifted in two fields by 3.0.0
    (`loop.plugin_version` and `loop.migration`). The fixture is referenced by
    nothing else in the repo -- it exists *only* to be the thing MOCK matches --
    so an unasserted claim is the whole of its value. Parse both and compare.
    """

    HERE = os.path.dirname(os.path.abspath(__file__))

    def _mock(self):
        path = os.path.join(self.HERE, "..", "web", "dashboard.html")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        marker = "const MOCK = "
        start = text.index(marker) + len(marker)
        depth, i = 0, start
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
            i += 1
        self.fail("unbalanced MOCK object in dashboard.html")

    def _fixture(self):
        path = os.path.join(self.HERE, "fixtures", "snapshot-sample.json")
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_mock_equals_snapshot_fixture(self):
        self.assertEqual(self._mock(), self._fixture())

    def test_both_name_the_current_plugin_version(self):
        """A stale version in the mock reads as a real dashboard showing 2.x."""
        path = os.path.join(self.HERE, "..", ".claude-plugin", "plugin.json")
        with open(path, encoding="utf-8") as fh:
            version = json.load(fh)["version"]
        self.assertEqual(self._mock()["loop"]["plugin_version"], version)
        self.assertEqual(self._fixture()["loop"]["plugin_version"], version)


if __name__ == "__main__":
    unittest.main()
