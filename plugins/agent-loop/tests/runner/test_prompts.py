import _path  # noqa: F401
import os
import re
import unittest

from runner import phases
from runner.claude_proc import Usage

PROMPT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "runner", "prompts")
NAMES = ("scout", "worker", "evaluator", "learner", "planner", "reviewer")


def body(name):
    with open(os.path.join(PROMPT_DIR, "%s.md" % name)) as f:
        return f.read()


class TestEveryPromptExists(unittest.TestCase):
    def test_all_six_role_briefs_are_present_and_non_trivial(self):
        for name in NAMES:
            self.assertGreater(len(body(name)), 400, name)


class TestSentinelFree(unittest.TestCase):
    def test_no_prompt_mentions_a_loop_sentinel(self):
        for name in NAMES:
            for token in ("<<LOOP_DONE>>", "<<LOOP_CONTINUE>>", "<<LOOP_HALT", "sentinel"):
                self.assertNotIn(token, body(name), "%s mentions %s" % (name, token))

    def test_no_prompt_names_a_model_or_asks_a_role_to_choose_a_tier(self):
        for name in NAMES:
            text = body(name).lower()
            for token in ("haiku", "sonnet", "opus", "cheapest capable",
                          "choose its tier", "resolve the tier"):
                self.assertNotIn(token, text, "%s mentions %s" % (name, token))

    def test_no_prompt_describes_an_orchestrator_spine(self):
        for name in NAMES:
            self.assertNotIn("orchestrator", body(name).lower(), name)

    def test_no_prompt_mentions_attempts_escalation_or_a_ladder(self):
        """Which model runs a re-attempt is a command-line fact decided by the
        harness (plan A) or the Judge (plan C). A role that reads "attempt 2
        escalates" starts reasoning about its own replacement."""
        for name in NAMES:
            text = body(name).lower()
            for token in ("attempt 2", "next tier", "tier up", "escalat", "a stronger model"):
                self.assertNotIn(token, text, "%s mentions %s" % (name, token))


class TestNonInteractivity(unittest.TestCase):
    def test_every_prompt_forbids_the_blocking_tools(self):
        for name in NAMES:
            text = body(name)
            self.assertIn("AskUserQuestion", text, name)
            self.assertIn("EnterPlanMode", text, name)


class TestJsonContract(unittest.TestCase):
    def test_every_prompt_states_a_fenced_json_block_is_required(self):
        for name in NAMES:
            self.assertIn("```json", body(name), name)

    def test_the_declared_keys_match_what_the_harness_parses(self):
        expected = {
            "scout": ("contract_path", "notes"),
            "worker": ("status", "summary"),
            "evaluator": ("verdict", "findings", "views", "summary"),
            "learner": ("patterns", "log", "invariants"),
            "planner": ("tasks_added",),
            "reviewer": ("findings",),
        }
        for name, keys in expected.items():
            text = body(name)
            for key in keys:
                self.assertIn('"%s"' % key, text, "%s is missing %s" % (name, key))


class TestDurableRulesSurvived(unittest.TestCase):
    def test_scout_carries_the_clone_contract_and_inlining_rules(self):
        text = body("scout")
        self.assertIn("clone_of", text)
        self.assertIn("substitutions", text)
        self.assertIn("see file", text)          # the anti-reference rule names it
        self.assertIn("relevant_learnings", text)
        self.assertIn("Invariants", text)
        self.assertIn("(read)", text)

    def test_worker_carries_the_containment_and_edit_mechanics_rules(self):
        text = body("worker")
        self.assertIn("worker-result.json", text)
        self.assertIn("git commit", text)
        self.assertIn("LOOP_PLAN.md", text)
        self.assertIn("replace_all", text)       # batch mechanical edits
        self.assertIn("re-grep", text)           # write-through on a reverting hook
        self.assertIn("allow_list", text)

    def test_evaluator_carries_the_workaround_catalogue(self):
        text = body("evaluator")
        for token in ("BLOCKER", "NEEDS_WORK", "PASS", "workaround", "stub", "loosen"):
            self.assertIn(token, text)

    def test_learner_carries_the_evidence_rule_and_the_digest_cap(self):
        text = body("learner")
        self.assertIn("2 KB", text)
        self.assertIn("gate-", text)
        self.assertIn("Patterns", text)

    def test_planner_carries_the_one_line_row_budget(self):
        text = body("planner")
        self.assertIn("ONE line", text)
        self.assertIn("depends_on", text)
        self.assertIn("mechanical", text)
        self.assertIn("complex", text)
        self.assertIn("no-ui", text)

    def test_reviewer_keeps_follow_ups_in_the_reviewed_segment(self):
        text = body("reviewer")
        self.assertIn("must-fix", text)
        self.assertIn("follow_up_row", text)
        self.assertIn("never a later", text)

    def test_the_filesystem_is_the_only_durable_state_rule_is_everywhere(self):
        for name in NAMES:
            self.assertIn("filesystem", body(name).lower(), name)


class TestRenderPrompt(unittest.TestCase):
    def test_every_placeholder_in_every_prompt_is_documented(self):
        known = {"loop_dir", "worktree", "task_row", "contract_json", "must_read_blocks",
                 "screenshots", "gate_outputs", "diff", "learnings_digest", "knowledge",
                 "spec_excerpt", "checkpoint", "minutes_left", "validation_errors",
                 "tdd_note", "evaluator_findings", "verdict", "segment"}
        for name in NAMES:
            for token in re.findall(r"\{\{(\w+)\}\}", body(name)):
                self.assertIn(token, known, "%s uses undocumented {{%s}}" % (name, token))

    def test_substitution_replaces_every_occurrence(self):
        out = phases.render_prompt("worker", loop_dir="/l", worktree="/w",
                                   contract_json="{}", tdd_note="",
                                   evaluator_findings="(first attempt)")
        self.assertNotIn("{{", out)
        self.assertIn("/l", out)

    def test_a_missing_placeholder_raises_rather_than_shipping_a_literal(self):
        with self.assertRaises(KeyError):
            phases.render_prompt("worker", loop_dir="/l")

    def test_an_unknown_prompt_name_raises(self):
        with self.assertRaises(KeyError):
            phases.render_prompt("nope")


class TestParseJsonBlock(unittest.TestCase):
    def test_it_extracts_a_fenced_block(self):
        self.assertEqual({"a": 1},
                         phases.parse_json_block('chatter\n```json\n{"a": 1}\n```\n'))

    def test_the_last_block_wins(self):
        text = '```json\n{"a": 1}\n```\nthen\n```json\n{"a": 2}\n```'
        self.assertEqual({"a": 2}, phases.parse_json_block(text))

    def test_an_unlabelled_fence_is_accepted_as_a_fallback(self):
        self.assertEqual({"a": 1}, phases.parse_json_block('```\n{"a": 1}\n```'))

    def test_a_bare_object_is_accepted_when_there_is_no_fence(self):
        self.assertEqual({"a": 1}, phases.parse_json_block('here you go: {"a": 1}'))

    def test_malformed_json_raises_with_the_parser_error(self):
        with self.assertRaises(phases.JsonBlockError) as ctx:
            phases.parse_json_block('```json\n{"a": ,}\n```')
        self.assertIn("line", str(ctx.exception).lower())

    def test_no_block_at_all_raises(self):
        with self.assertRaises(phases.JsonBlockError):
            phases.parse_json_block("I did the thing.")

    def test_a_json_array_is_rejected(self):
        with self.assertRaises(phases.JsonBlockError):
            phases.parse_json_block('```json\n[1, 2]\n```')


class TestMergeUsage(unittest.TestCase):
    def test_both_attempts_are_summed_per_model(self):
        a = {"m": Usage(cost_usd=1.0, input_tokens=10, output_tokens=1,
                        cache_read_tokens=5, cache_creation_tokens=1)}
        b = {"m": Usage(cost_usd=2.0, input_tokens=20, output_tokens=2,
                        cache_read_tokens=6, cache_creation_tokens=0),
             "n": Usage(cost_usd=0.5)}
        out = phases._merge_usage(a, b)
        self.assertEqual(3.0, out["m"].cost_usd)
        self.assertEqual(30, out["m"].input_tokens)
        self.assertEqual(11, out["m"].cache_read_tokens)
        self.assertEqual(0.5, out["n"].cost_usd)

    def test_merging_does_not_mutate_the_inputs(self):
        a = {"m": Usage(cost_usd=1.0)}
        phases._merge_usage(a, {"m": Usage(cost_usd=2.0)})
        self.assertEqual(1.0, a["m"].cost_usd)


if __name__ == "__main__":
    unittest.main()
