import json
import os
import shutil
import tempfile
import unittest

import cfixtures  # noqa: F401  (also puts the plugin root on sys.path)
from runner import judge


class TestAttemptHistory(unittest.TestCase):
    """Plan C owns no attempt file: the record is plan A's runtime/task-<T>.json."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def _phase(self, phase="worker"):
        from runner.claude_proc import PhaseResult
        return PhaseResult(phase=phase, model="sonnet", rc=0, killed=False,
                           timed_out=False, session_id="sid-1", transcript_path=None,
                           started=100, ended=200, result_text="", usage_by_model={},
                           tool_calls=3, last_activity=200)

    def test_attempts_come_from_task_state(self):
        cfixtures.seed_attempt(self.tmp, "T60", 1, "gate:tsc failed",
                               tier="standard", judge={"decision": "retry"},
                               phase_results=[self._phase()])
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "task-T60.json")),
                        "TaskState writes runtime/task-<T>.json (spec 14)")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "attempts-T60.json")),
                         "attempts-<T>.json does not exist in v3")
        attempts = judge.attempts_for(self.tmp, "T60")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["n"], 1)
        self.assertEqual(attempts[0]["tier"], "standard")
        self.assertEqual(attempts[0]["outcome"], "gate:tsc failed")
        self.assertEqual(attempts[0]["judge"]["decision"], "retry")
        self.assertEqual(attempts[0]["phase_results"][0]["phase"], "worker")

    def test_judge_decision_count(self):
        self.assertEqual(judge.judge_decision_count([]), 0)
        self.assertEqual(judge.judge_decision_count(
            [{"n": 1, "judge": {"decision": "widen"}}, {"n": 2}]), 1)

    def test_attempts_for_a_task_with_no_state_file(self):
        self.assertEqual(judge.attempts_for(self.tmp, "T99"), [])

    def test_cap_extension_and_last_tier_read_the_history(self):
        self.assertFalse(judge.cap_extended([{"n": 1, "judge": {"decision": "retry"}}]))
        self.assertTrue(judge.cap_extended(
            [{"n": 1, "judge": {"changes": {"extend_cap_s": 600}}}]))
        self.assertEqual(judge.last_tier([], "standard"), "standard")
        self.assertEqual(judge.last_tier(
            [{"n": 1, "tier": "standard"}, {"n": 2, "tier": "most-capable"}],
            "standard"), "most-capable")


class TestBuildInput(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.cfg, self.plan, self.loop_dir, self.runtime = cfixtures.make_loop(self.tmp)
        self.ctx = cfixtures.make_ctx(self.cfg, self.plan, self.loop_dir, self.runtime)

    def test_dossier_contents(self):
        with open(os.path.join(self.runtime, "gate-T60-1.txt"), "w") as fh:
            fh.write("\n".join("line %d" % i for i in range(200)))
        with open(os.path.join(self.runtime, "worker-result.json"), "w") as fh:
            json.dump({"task": "T60", "status": "partial",
                       "checkpoint": "register the mixin in index.ts"}, fh)
        inp = judge.build_input(self.ctx, cfixtures.make_contract(),
                                judge.Failure(kind="gate", detail="tsc failed"))

        self.assertEqual(inp.task_id, "T60")
        self.assertIn("organisationalUnits", inp.task_row)
        self.assertEqual(inp.policy, "autonomous")
        self.assertEqual(inp.failure, "gate: tsc failed")

        self.assertEqual(inp.tier, "standard", "the tier this attempt ran at")
        self.assertEqual(inp.attempt, 1)
        self.assertEqual(inp.max_attempts, 3)
        self.assertEqual(inp.cap_default_s, 1500, "worker_timeout is the ceiling")
        self.assertFalse(inp.cap_extended)
        self.assertEqual(inp.resumes_left, 1)
        self.assertEqual(inp.boot_evidence, "")

        self.assertEqual(len(inp.gate_outputs), 1)
        body = inp.gate_outputs[0]
        self.assertLessEqual(len(body.splitlines()), judge.MAX_GATE_LINES + 1)
        self.assertIn("line 199", body)
        self.assertNotIn("line 5\n", body)

        self.assertEqual(inp.checkpoint, "register the mixin in index.ts")

        sources = dict((f["path"], f["source"]) for f in inp.forbidden)
        self.assertEqual(sources["apps/frontend/**"], "plan")
        self.assertEqual(
            sources["packages/api/src/requests/activepipe/index.ts"], "scout")

        joined = "\n".join(inp.excerpts)
        self.assertEqual(len(inp.excerpts), 2, "one block per source document")
        self.assertIn("dedicated internal mixin set", joined)
        self.assertIn("composition root", joined)

    def test_parity_ids_come_from_the_task_row(self):
        self.assertEqual(judge.PARITY_RE.findall(
            "- [~] T60 Add organisationalUnits REST mixin (P-ORG-001)"), ["P-ORG-001"])
        inp = judge.build_input(self.ctx, cfixtures.make_contract(),
                                judge.Failure(kind="needs-work", detail="criterion 2 unmet"))
        self.assertIn("P-ORG-001", "\n".join(inp.excerpts))

    def test_missing_optional_inputs_do_not_raise(self):
        inp = judge.build_input(self.ctx, cfixtures.make_contract(),
                                judge.Failure(kind="gate", detail=""))
        self.assertEqual(inp.gate_outputs, [])
        self.assertEqual(inp.checkpoint, "")
        self.assertEqual(inp.attempts, [], "no task-T60.json yet")
        self.assertIn("Cleanup", inp.cleanup_text)

    def test_the_tier_falls_back_to_the_history_then_the_config(self):
        self.ctx.tier = None
        cfixtures.seed_attempt(self.runtime, "T60", 1, "gate:tsc",
                               tier="most-capable")
        inp = judge.build_input(self.ctx, cfixtures.make_contract(),
                                judge.Failure(kind="gate", detail="tsc failed"))
        self.assertEqual(inp.tier, "most-capable")



class _PolicyBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.policy = getattr(self, "POLICY", "autonomous")
        self.cfg, self.plan, self.loop_dir, self.runtime = cfixtures.make_loop(
            self.tmp, policy=self.policy)
        self.ctx = cfixtures.make_ctx(self.cfg, self.plan, self.loop_dir, self.runtime)
        self.contract = cfixtures.make_contract()
        self.inp = judge.build_input(self.ctx, self.contract,
                                     judge.Failure(kind="gate", detail="tsc failed"))

    def decision(self, **over):
        d = {"decision": "widen", "classification": "self-imposed",
             "rationale": "the blocking constraint is scout-sourced",
             "instruction": "", "alternatives": ["defer to a human"],
             "reversal": "drop the allow_list entry",
             "changes": {"allow_list_add": [], "forbidden_remove": [],
                         "default_choice": "", "blocks": [], "sub_rows": [],
                         "tier": "", "extend_cap_s": 0}}
        changes = over.pop("changes", {})
        d.update(over)
        d["changes"].update(changes)
        return d


class TestValidateDecision(_PolicyBase):
    def test_widening_a_scout_sourced_constraint_is_allowed(self):
        d = self.decision(changes={
            "forbidden_remove": ["packages/api/src/requests/activepipe/index.ts"],
            "allow_list_add": ["packages/api/src/requests/activepipe/index.ts"]})
        self.assertEqual(judge.validate_decision(d, self.inp), [])

    def test_widening_a_plan_sourced_constraint_is_rejected(self):
        d = self.decision(changes={"forbidden_remove": ["apps/frontend/**"]})
        errors = judge.validate_decision(d, self.inp)
        self.assertTrue(any("plan-sourced" in e for e in errors), errors)

    def test_allow_list_add_under_a_plan_forbidden_glob_is_rejected(self):
        d = self.decision(changes={"allow_list_add": ["apps/frontend/src/App.tsx"]})
        errors = judge.validate_decision(d, self.inp)
        self.assertTrue(any("apps/frontend/**" in e for e in errors), errors)

    def test_removing_a_constraint_the_contract_does_not_have_is_rejected(self):
        d = self.decision(changes={"forbidden_remove": ["packages/api/nope.ts"]})
        errors = judge.validate_decision(d, self.inp)
        self.assertTrue(any("not in the contract" in e for e in errors), errors)

    def test_widen_without_changes_is_rejected(self):
        errors = judge.validate_decision(self.decision(), self.inp)
        self.assertTrue(any("widen" in e for e in errors), errors)

    def test_unknown_decision_and_classification_are_rejected(self):
        errors = judge.validate_decision(
            self.decision(decision="improvise"), self.inp)
        self.assertTrue(any("unknown decision" in e for e in errors), errors)
        errors = judge.validate_decision(
            self.decision(decision="defer", classification="vibes"), self.inp)
        self.assertTrue(any("classification" in e for e in errors), errors)

    def test_default_choice_allowed_under_autonomous(self):
        d = self.decision(decision="retry", classification="open",
                          changes={"default_choice": "place it under internal/"})
        self.assertEqual(judge.validate_decision(d, self.inp), [])

    def test_third_decision_is_forced_to_defer_or_halt(self):
        cfixtures.seed_attempt(self.runtime, "T60", 1, "gate:tsc",
                               judge={"decision": "widen"})
        cfixtures.seed_attempt(self.runtime, "T60", 2, "gate:tsc",
                               judge={"decision": "escalate"})
        inp = judge.build_input(self.ctx, self.contract,
                                judge.Failure(kind="gate", detail="tsc failed"))
        errors = judge.validate_decision(
            self.decision(decision="retry", classification="capability"), inp)
        self.assertTrue(any("only defer or halt" in e for e in errors), errors)
        self.assertEqual(judge.validate_decision(
            self.decision(decision="defer", classification="open",
                          changes={"default_choice": "x"}), inp), [])
        self.assertEqual(judge.validate_decision(
            self.decision(decision="halt", classification="open"), inp), [])


class TestJudgedTier(_PolicyBase):
    """Spec §6 step 2 and §11 item 4: the Judge picks the tier, the harness
    only bounds it. There is no ladder anywhere in the runner."""

    def test_escalate_must_name_a_tier_above_the_current_one(self):
        self.assertTrue(any("above standard" in e for e in judge.validate_decision(
            self.decision(decision="escalate", classification="capability",
                          changes={"tier": "standard"}), self.inp)))
        self.assertTrue(any("above standard" in e for e in judge.validate_decision(
            self.decision(decision="escalate", classification="capability",
                          changes={"tier": "cheap"}), self.inp)))
        self.assertEqual(judge.validate_decision(
            self.decision(decision="escalate", classification="capability",
                          changes={"tier": "most-capable"}), self.inp), [])

    def test_escalate_without_a_tier_is_rejected(self):
        errors = judge.validate_decision(
            self.decision(decision="escalate", classification="capability"),
            self.inp)
        self.assertTrue(any("changes.tier" in e for e in errors), errors)

    def test_escalate_from_the_top_of_the_ladder_is_impossible(self):
        self.ctx.tier = "most-capable"
        inp = judge.build_input(self.ctx, self.contract,
                                judge.Failure(kind="gate", detail="tsc failed"))
        errors = judge.validate_decision(
            self.decision(decision="escalate", classification="capability",
                          changes={"tier": "most-capable"}), inp)
        self.assertTrue(any("no tier above" in e for e in errors), errors)

    def test_an_unknown_tier_is_rejected(self):
        errors = judge.validate_decision(
            self.decision(decision="retry", classification="capability",
                          changes={"tier": "opus"}), self.inp)
        self.assertTrue(any("unknown tier" in e for e in errors),
                        "tiers, never model names")

    def test_resume_keeps_the_same_tier_and_needs_a_session_and_budget(self):
        errors = judge.validate_decision(
            self.decision(decision="resume", classification="capability",
                          changes={"tier": "standard"}), self.inp)
        self.assertTrue(any("session" in e for e in errors), errors)

        self.ctx.last_session = "sid-1"
        inp = judge.build_input(self.ctx, self.contract,
                                judge.Failure(kind="worker-incomplete", detail=""))
        self.assertEqual(judge.validate_decision(
            self.decision(decision="resume", classification="capability",
                          changes={"tier": "standard"}), inp), [])

        self.ctx.resume_count = 1              # worker_resume_max is 1
        spent = judge.build_input(self.ctx, self.contract,
                                  judge.Failure(kind="worker-incomplete", detail=""))
        self.assertTrue(any("resume budget" in e for e in judge.validate_decision(
            self.decision(decision="resume", classification="capability",
                          changes={"tier": "standard"}), spent)))

    def test_cap_extension_is_bounded_and_once_per_task(self):
        self.assertTrue(any("exceeds the phase default" in e
                            for e in judge.validate_decision(
                                self.decision(decision="retry",
                                              classification="capability",
                                              changes={"extend_cap_s": 1501}),
                                self.inp)))
        ok = self.decision(decision="retry", classification="capability",
                           changes={"extend_cap_s": 600})
        self.assertEqual(judge.validate_decision(ok, self.inp), [])

        cfixtures.seed_attempt(self.runtime, "T60", 1, "worker-incomplete:",
                               judge={"decision": "resume",
                                      "changes": {"extend_cap_s": 600}})
        inp = judge.build_input(self.ctx, self.contract,
                                judge.Failure(kind="gate", detail="tsc failed"))
        self.assertTrue(inp.cap_extended)
        self.assertTrue(any("already used its one cap extension" in e
                            for e in judge.validate_decision(ok, inp)))

    def test_the_last_attempt_accepts_only_split_defer_or_halt(self):
        self.ctx.attempt = 3                   # max_attempts defaults to 3
        inp = judge.build_input(self.ctx, self.contract,
                                judge.Failure(kind="gate", detail="tsc failed"))
        errors = judge.validate_decision(
            self.decision(decision="escalate", classification="capability",
                          changes={"tier": "most-capable"}), inp)
        self.assertTrue(any("is the last" in e for e in errors), errors)
        self.assertEqual(judge.validate_decision(
            self.decision(decision="split", classification="capability",
                          changes={"sub_rows": ["- [ ] Build the mixin",
                                                "- [ ] Register the mixin"]}),
            inp), [])


class TestConservativePolicy(_PolicyBase):
    POLICY = "conservative"

    def test_default_choice_is_rejected(self):
        d = self.decision(decision="retry", classification="open",
                          changes={"default_choice": "place it under internal/"})
        errors = judge.validate_decision(d, self.inp)
        self.assertTrue(any("autonomous" in e for e in errors), errors)

    def test_widen_and_escalate_still_allowed(self):
        d = self.decision(changes={
            "forbidden_remove": ["packages/api/src/requests/activepipe/index.ts"]})
        self.assertEqual(judge.validate_decision(d, self.inp), [])
        self.assertEqual(judge.validate_decision(
            self.decision(decision="escalate", classification="capability",
                          changes={"tier": "most-capable"}), self.inp), [])


class TestDecide(_PolicyBase):
    def _run(self, payload):
        from unittest import mock
        from runner.claude_proc import PhaseResult
        result = PhaseResult(
            phase="judge", model="opus", rc=0, killed=False, timed_out=False,
            session_id="sid-judge", transcript_path=None, started=1, ended=2,
            result_text="Here is my call.\n\n```json\n%s\n```\n" % json.dumps(payload),
            usage_by_model={}, tool_calls=2, last_activity=2)
        with mock.patch.object(judge.claude_proc, "run_phase",
                               return_value=result) as spawn:
            return judge.decide(self.ctx, self.inp), spawn

    def test_valid_decision_passes_through_and_runs_at_most_capable(self):
        out, spawn = self._run({
            "decision": "widen", "classification": "self-imposed",
            "rationale": "scout invented it", "alternatives": ["halt"],
            "reversal": "revert the contract",
            "changes": {"forbidden_remove":
                        ["packages/api/src/requests/activepipe/index.ts"]}})
        self.assertEqual(out["decision"], "widen")
        self.assertEqual(out["rejected"], [])
        self.assertEqual(out["changes"]["allow_list_add"], [])
        self.assertEqual(out["changes"]["sub_rows"], [])
        self.assertEqual(out["changes"]["tier"], "")
        self.assertEqual(out["changes"]["extend_cap_s"], 0)
        self.assertEqual(out["_phase"].session_id, "sid-judge")
        kwargs = spawn.call_args[1]
        self.assertEqual(kwargs["model"], "opus")
        self.assertEqual(kwargs["phase"], "judge")
        self.assertEqual(kwargs["timeout_s"], 360, "judge_timeout")
        self.assertIn("P-ORG-001", kwargs["prompt"])
        self.assertIn("autonomous", kwargs["prompt"])
        self.assertIn("attempt 1 of 3", kwargs["prompt"])
        self.assertIn("one cap extension of up to 1500 s", kwargs["prompt"])

    def test_rejected_decision_is_downgraded_to_defer(self):
        out, _ = self._run({
            "decision": "widen", "classification": "self-imposed",
            "rationale": "I want the composition root",
            "changes": {"forbidden_remove": ["apps/frontend/**"]}})
        self.assertEqual(out["decision"], "defer")
        self.assertTrue(any("plan-sourced" in e for e in out["rejected"]))
        self.assertIn("I want the composition root", out["rationale"])
        self.assertEqual(out["changes"]["forbidden_remove"], [])

    def test_malformed_output_defers(self):
        from unittest import mock
        from runner.claude_proc import PhaseResult
        result = PhaseResult(phase="judge", model="opus", rc=0, killed=False,
                             timed_out=False, session_id="s", transcript_path=None,
                             started=1, ended=2, result_text="no json here",
                             usage_by_model={}, tool_calls=0, last_activity=2)
        with mock.patch.object(judge.claude_proc, "run_phase", return_value=result):
            out = judge.decide(self.ctx, self.inp)
        self.assertEqual(out["decision"], "defer")
        self.assertEqual(out["classification"], "open")
        self.assertTrue(any("malformed" in e for e in out["rejected"]))


class TestSubRowGrammar(_PolicyBase):
    def test_sub_rows_are_titles_that_the_harness_numbers(self):
        self.assertEqual(judge.validate_sub_rows(
            "T60", ["- [ ] Build the organisationalUnits mixin",
                    "- [ ] Register it in the composition root"]), [])
        self.assertTrue(judge.validate_sub_rows("T60", ["- [ ] only one"]))
        self.assertTrue(any("must not name a task id" in e
                            for e in judge.validate_sub_rows(
                                "T60", ["- [ ] T60a Build the mixin",
                                        "- [ ] T60b Register it"])),
                        "lettered ids are gone: serve.py's id regex is \\bT\\d+\\b")
        self.assertTrue(any("must not name a task id" in e
                            for e in judge.validate_sub_rows(
                                "T60", ["- [ ] T74 Build the mixin",
                                        "- [ ] T75 Register it"])),
                        "Plan.split allocates the numbers, not the Judge")
        self.assertTrue(any("grammar" in e for e in judge.validate_sub_rows(
            "T60", ["no checkbox", "- [ ] Register it"])))
        self.assertTrue(any("duplicate" in e for e in judge.validate_sub_rows(
            "T60", ["- [ ] Build the mixin", "- [ ] build the mixin"])))

    def test_split_decision_without_sub_rows_is_rejected(self):
        errors = judge.validate_decision(
            self.decision(decision="split", classification="capability"), self.inp)
        self.assertTrue(any("sub_rows" in e for e in errors), errors)


class TestApply(_PolicyBase):
    def setUp(self):
        _PolicyBase.setUp(self)
        cfixtures.git_init(self.cfg.worktree)
        self.contract_path = cfixtures.write_contract(self.ctx, self.contract)

    def test_widen_mutates_the_contract_and_records_the_decision(self):
        from runner.contract import load_contract
        action = judge.apply(self.ctx, self.contract, judge._normalize({
            "decision": "widen", "classification": "self-imposed",
            "rationale": "the blocking constraint is the Scout's own",
            "instruction": "register the mixin in index.ts, do not re-create it",
            "alternatives": ["halt and ask a human, as the 2026-09-11 run did"],
            "reversal": "delete the allow_list entry from runtime/sprint-T60.json",
            "changes": {
                "forbidden_remove": ["packages/api/src/requests/activepipe/index.ts"],
                "allow_list_add": ["packages/api/src/requests/activepipe/index.ts"]}}))
        self.assertEqual(action, "retry")

        saved = load_contract(self.contract_path)
        self.assertIn("packages/api/src/requests/activepipe/index.ts",
                      saved.allow_list)
        self.assertEqual([f.path for f in saved.forbidden], ["apps/frontend/**"])
        self.assertIn("register the mixin in index.ts", saved.scout_notes)
        self.assertIn("follow T59's precedent", saved.scout_notes)

        text = cfixtures.read(os.path.join(self.loop_dir, "LOOP_DECISIONS.md"))
        self.assertRegex(
            text,
            r"(?m)^## \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ T60 — widen \(self-imposed\)$")
        self.assertIn("- **Rationale:** the blocking constraint is the Scout's own",
                      text)
        self.assertIn("- **Alternatives:** halt and ask a human", text)
        self.assertIn("- **Reverse:** delete the allow_list entry", text)
        self.assertIn("allow_list += packages/api/src/requests/activepipe/index.ts",
                      text)

        evs = [e for e in cfixtures.read_events(self.loop_dir)
               if e["type"] == "decision"]
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["task"], "T60")
        self.assertEqual(evs[0]["decision"], "widen")
        self.assertEqual(evs[0]["classification"], "self-imposed")

    def test_escalate_and_resume_inject_the_instruction_only(self):
        from runner.contract import load_contract
        for decision, expected in (("escalate", "escalate"), ("resume", "resume")):
            action = judge.apply(self.ctx, self.contract, judge._normalize({
                "decision": decision, "classification": "capability",
                "rationale": "same tier twice", "instruction": "use the mixin chain",
                "alternatives": ["split"], "reversal": "none needed",
                "changes": {}}))
            self.assertEqual(action, expected)
        saved = load_contract(self.contract_path)
        self.assertEqual(saved.allow_list, self.contract.allow_list)
        self.assertEqual(saved.scout_notes.count("use the mixin chain"), 2)

    def test_the_judged_tier_and_cap_extension_are_armed_on_the_context(self):
        action = judge.apply(self.ctx, self.contract, judge._normalize({
            "decision": "resume", "classification": "capability",
            "rationale": "steady file-by-file progress, simply out of clock",
            "instruction": "", "alternatives": ["escalate to most-capable"],
            "reversal": "none needed; the cap returns to its default next task",
            "changes": {"tier": "standard", "extend_cap_s": 600}}))
        self.assertEqual(action, "resume")
        self.assertEqual(self.ctx.tier, "standard",
                         "the Judge's tier, not a ladder step")
        self.assertEqual(self.ctx.extend_cap_s, 600,
                         "apply hands the extended cap to the next run_worker")
        text = cfixtures.read(os.path.join(self.loop_dir, "LOOP_DECISIONS.md"))
        self.assertIn("worker cap extended by 600s", text)
        self.assertIn("next attempt runs at tier standard", text)

    def test_escalate_arms_the_tier_the_judge_named(self):
        judge.apply(self.ctx, self.contract, judge._normalize({
            "decision": "escalate", "classification": "capability",
            "rationale": "no progress in two attempts",
            "instruction": "", "alternatives": ["split"], "reversal": "none",
            "changes": {"tier": "most-capable"}}))
        self.assertEqual(self.ctx.tier, "most-capable")

    def test_defer_writes_cleanup_blocks_downstream_and_marks_the_task(self):
        from runner.plan import Plan
        action = judge.apply(self.ctx, self.contract, judge._normalize({
            "decision": "defer", "classification": "open",
            "rationale": "the spec does not say where the mixin is registered",
            "alternatives": ["guess the composition root"],
            "reversal": "clear blocked_by on T61 and re-run T60",
            "changes": {"default_choice": "register it in internal/index.ts",
                        "blocks": ["T61"]}}))
        self.assertEqual(action, "defer")

        cleanup = cfixtures.read(os.path.join(self.loop_dir, "LOOP_CLEANUP.md"))
        self.assertIn("## T60", cleanup)
        self.assertIn("(open)", cleanup)
        self.assertIn("**Proposed default:** register it in internal/index.ts",
                      cleanup)
        self.assertIn("**Blocks:** T61", cleanup)

        reloaded = Plan.load(os.path.join(self.loop_dir, "LOOP_PLAN.md"))
        t60 = [t for t in reloaded.tasks() if t.id == "T60"][0]
        t61 = [t for t in reloaded.tasks() if t.id == "T61"][0]
        self.assertEqual(t60.state, "blocked")
        self.assertEqual(t61.blocked_by, ["T60"])
        self.assertIn("| blocked_by: T60", t61.raw)
        eligible = [t.id for t in reloaded.eligible()]
        self.assertNotIn("T61", eligible)
        self.assertIn("T62", eligible)

    def test_halt_marks_the_task_and_returns_halt(self):
        from runner.plan import Plan
        action = judge.apply(self.ctx, self.contract, judge._normalize({
            "decision": "halt", "classification": "open",
            "rationale": "continuing would drop uncommitted work",
            "alternatives": ["defer"], "reversal": "resume the loop",
            "changes": {}}))
        self.assertEqual(action, "halt")
        reloaded = Plan.load(os.path.join(self.loop_dir, "LOOP_PLAN.md"))
        self.assertEqual(
            [t for t in reloaded.tasks() if t.id == "T60"][0].state, "blocked")

    def test_mark_blocked_refuses_without_a_decision(self):
        with self.assertRaises(RuntimeError) as ctx:
            judge.mark_blocked(self.ctx, {})
        self.assertIn("without a Judge decision", str(ctx.exception))

if __name__ == "__main__":
    unittest.main()
