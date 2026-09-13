import _path  # noqa: F401
import json
import os
import tempfile
import time
import unittest

from runner import migrate, util
from runner.events import EventLog


class Base(unittest.TestCase):
    def setUp(self):
        self.ld = tempfile.mkdtemp()
        self.rt = os.path.join(self.ld, "runtime")
        os.makedirs(self.rt)
        self.events = EventLog(os.path.join(self.ld, "events.jsonl"),
                               os.path.join(self.rt, "eventseq"))

    def migrations(self):
        out = []
        path = os.path.join(self.ld, "events.jsonl")
        if not os.path.exists(path):
            return out
        with open(path) as f:
            for line in f:
                rec = json.loads(line)
                if rec["type"] == "migration":
                    out.append(rec)
        return out

    def run_migrate(self, legacy_live=False, force=False):
        return migrate.migrate_loop_dir(self.ld, self.rt, self.events,
                                        migrate.LOOP_SCHEMA, legacy_live, force, "3.0.0")


class TestSchemaRead(Base):
    def test_a_stamped_dir_reports_its_stamp(self):
        util.atomic_write(os.path.join(self.rt, "schema"), "2")
        self.assertEqual(2, migrate.schema_read(self.rt, 3))

    def test_a_tick_counter_without_a_stamp_means_schema_one(self):
        util.atomic_write(os.path.join(self.rt, "tickseq"), "41")
        self.assertEqual(1, migrate.schema_read(self.rt, 3))

    def test_a_fresh_dir_reports_the_current_schema(self):
        self.assertEqual(3, migrate.schema_read(self.rt, 3))


class TestMigrateFresh(Base):
    def test_a_fresh_dir_is_stamped_with_no_migration_event(self):
        self.assertEqual("ok:3", self.run_migrate())
        self.assertEqual(3, util.read_int(os.path.join(self.rt, "schema")))
        self.assertEqual([], self.migrations())


class TestMigrateTwoToThree(Base):
    def setUp(self):
        Base.setUp(self)
        util.atomic_write(os.path.join(self.rt, "schema"), "2")

    def test_it_creates_the_schema_three_files(self):
        out = self.run_migrate()
        self.assertTrue(out.startswith("migrated:2:3:"), out)
        self.assertTrue(os.path.isdir(os.path.join(self.ld, "artifacts")))
        self.assertTrue(os.path.exists(os.path.join(self.ld, "LOOP_DECISIONS.md")))
        self.assertTrue(os.path.exists(os.path.join(self.ld, "harness.log")))
        self.assertEqual(3, util.read_int(os.path.join(self.rt, "schema")))

    def test_artifacts_are_gitignored_so_the_sandbox_cannot_revert_them(self):
        self.run_migrate()
        body = util.read_text(os.path.join(self.ld, ".gitignore"))
        self.assertIn("artifacts/", body)
        self.assertIn("runtime/", body)

    def test_an_existing_gitignore_is_appended_to_not_replaced(self):
        util.atomic_write(os.path.join(self.ld, ".gitignore"), "runtime/\n")
        self.run_migrate()
        body = util.read_text(os.path.join(self.ld, ".gitignore"))
        self.assertEqual(1, body.count("runtime/"))
        self.assertIn("artifacts/", body)

    def test_an_existing_decisions_file_is_left_alone(self):
        util.atomic_write(os.path.join(self.ld, "LOOP_DECISIONS.md"), "# keep me\n")
        self.run_migrate()
        self.assertIn("keep me", util.read_text(os.path.join(self.ld, "LOOP_DECISIONS.md")))

    def test_the_migration_event_records_the_step(self):
        self.run_migrate()
        ev = self.migrations()[0]
        self.assertEqual(2, ev["from"])
        self.assertEqual(3, ev["to"])
        self.assertEqual("3.0.0", ev["plugin_version"])
        self.assertIn("artifacts-dir", ev["actions"])


class TestContractNormalisation(Base):
    def setUp(self):
        Base.setUp(self)
        util.atomic_write(os.path.join(self.rt, "schema"), "2")

    def contract(self, task_id, doc):
        path = os.path.join(self.rt, "sprint-%s.json" % task_id)
        util.write_json(path, doc)
        return path

    def test_a_string_forbidden_entry_gains_scout_provenance(self):
        path = self.contract("T60", {"task": "T60", "forbidden": ["apps/frontend/**"]})
        self.run_migrate()
        self.assertEqual([{"path": "apps/frontend/**", "source": "scout"}],
                         util.read_json(path)["forbidden"])

    def test_an_entry_that_already_has_provenance_is_left_alone(self):
        path = self.contract("T61", {"task": "T61", "forbidden": [
            {"path": "apps/frontend/**", "source": "plan"}]})
        self.run_migrate()
        self.assertEqual("plan", util.read_json(path)["forbidden"][0]["source"])

    def test_missing_v3_fields_get_their_defaults(self):
        path = self.contract("T62", {"task": "T62", "success_criteria": ["x"],
                                     "allow_list": ["src/a.ts"], "verification": ["true"]})
        self.run_migrate()
        doc = util.read_json(path)
        self.assertEqual([], doc["forbidden"])
        self.assertIsNone(doc["render_gate"])
        self.assertEqual([], doc["fidelity_source"])
        self.assertEqual([], doc["evaluator_must_read"])
        self.assertEqual([], doc["evaluator_must_view"])
        self.assertEqual(0, doc["estimated_diff_lines"])
        self.assertEqual(["x"], doc["success_criteria"])

    def test_the_count_lands_in_the_migration_actions(self):
        self.contract("T60", {"task": "T60", "forbidden": ["a/**"]})
        self.contract("T61", {"task": "T61", "forbidden": ["b/**"]})
        self.run_migrate()
        self.assertIn("contracts-normalised:2", self.migrations()[0]["actions"])

    def test_a_malformed_contract_is_skipped_rather_than_crashing_the_migration(self):
        util.atomic_write(os.path.join(self.rt, "sprint-T63.json"), "{not json")
        self.assertTrue(self.run_migrate().startswith("migrated:2:3:"))
        self.assertEqual("{not json",
                         util.read_text(os.path.join(self.rt, "sprint-T63.json")))

    def test_nothing_else_under_runtime_is_touched(self):
        util.write_json(os.path.join(self.rt, "worker-result.json"),
                        {"task": "T60", "status": "partial"})
        self.run_migrate()
        self.assertEqual("partial",
                         util.read_json(os.path.join(self.rt, "worker-result.json"))["status"])

    def test_an_in_flight_task_is_left_for_the_boot_rule(self):
        plan = os.path.join(self.ld, "LOOP_PLAN.md")
        util.atomic_write(plan, "## S\n- [~] T60: in flight\n- [ ] T61: next\n")
        self.run_migrate()
        self.assertEqual("## S\n- [~] T60: in flight\n- [ ] T61: next\n",
                         util.read_text(plan))

    def test_a_normalised_contract_actually_passes_contract_validate(self):
        # The point of this step is that contract.validate stops failing on a
        # migrated 2.x contract's first tick. Reading the migrated JSON back
        # and asserting field shapes (as the tests above do) is not the same
        # claim as "validate() accepts it" -- so call the real validator here.
        path = self.contract("T70", {
            "task": "T70",
            "success_criteria": [
                "packages/api/src/requests/orgUnits.ts exports listOrgUnits"],
            "allow_list": ["packages/api/src/requests/orgUnits.ts"],
            "forbidden": ["apps/frontend/**"],
            "verification": ["pnpm turbo run lint"],
        })
        self.run_migrate()

        from runner import contract as contract_mod
        from runner.config import LoopConfig
        from runner.plan import Task

        loaded = contract_mod.load_contract(path)
        task = Task(id="T70", segment="S", title="Add the org-unit request",
                   state="pending")
        cfg = LoopConfig(worktree="/tmp/wt")
        ui_globs = ["apps/*/src/**", "packages/ui/**"]
        errors = contract_mod.validate(loaded, task, cfg, "", ui_globs)
        self.assertEqual([], errors)


class TestMigrateOneToThree(Base):
    def setUp(self):
        Base.setUp(self)
        util.atomic_write(os.path.join(self.rt, "tickseq"), "41")
        util.atomic_write(os.path.join(self.rt, "LOCK"), "4242")

    def test_every_step_runs_and_each_stamps_before_the_next(self):
        out = self.run_migrate()
        self.assertTrue(out.startswith("migrated:1:3:"), out)
        self.assertFalse(os.path.exists(os.path.join(self.rt, "LOCK")))
        self.assertTrue(os.path.isdir(os.path.join(self.ld, "artifacts")))
        self.assertEqual([(1, 2), (2, 3)],
                         [(e["from"], e["to"]) for e in self.migrations()])

    def test_the_tick_counter_is_untouched(self):
        self.run_migrate()
        self.assertEqual(41, util.read_int(os.path.join(self.rt, "tickseq")))


class TestGuards(Base):
    def test_a_newer_dir_is_refused_and_nothing_is_touched(self):
        util.atomic_write(os.path.join(self.rt, "schema"), "99")
        self.assertEqual("newer:99", self.run_migrate())
        self.assertEqual(99, util.read_int(os.path.join(self.rt, "schema")))
        self.assertFalse(os.path.exists(os.path.join(self.ld, "LOOP_DECISIONS.md")))

    def test_a_live_one_x_harness_blocks_the_start(self):
        util.atomic_write(os.path.join(self.rt, "tickseq"), "41")
        out = self.run_migrate(legacy_live=True)
        self.assertTrue(out.startswith("blocked:"), out)
        self.assertIn("LOOP_MIGRATE_FORCE", out)
        self.assertIn("PAUSE", out)
        self.assertFalse(os.path.exists(os.path.join(self.rt, "schema")))

    def test_force_overrides_the_live_guard(self):
        util.atomic_write(os.path.join(self.rt, "tickseq"), "41")
        self.assertTrue(self.run_migrate(legacy_live=True, force=True)
                        .startswith("migrated:1:3:"))

    def test_the_guard_only_applies_to_schema_one(self):
        util.atomic_write(os.path.join(self.rt, "schema"), "2")
        self.assertTrue(self.run_migrate(legacy_live=True).startswith("migrated:2:3:"))


class TestLegacyLiveDetection(Base):
    def test_no_run_log_means_not_live(self):
        self.assertFalse(migrate.legacy_harness_live(self.ld))

    def test_a_recent_log_whose_last_line_is_not_an_exit_line_looks_live(self):
        util.atomic_write(os.path.join(self.ld, "run.log"),
                          "2026-01-01T00:00:00Z tick 41 starting\n")
        self.assertTrue(migrate.legacy_harness_live(self.ld))

    def test_a_clean_exit_line_means_not_live(self):
        util.atomic_write(os.path.join(self.ld, "run.log"),
                          "2026-01-01T00:00:00Z tick 41 starting\n"
                          "2026-01-01T00:20:00Z LOOP_DONE after 41 ticks\n")
        self.assertFalse(migrate.legacy_harness_live(self.ld))

    def test_an_old_log_means_not_live(self):
        path = os.path.join(self.ld, "run.log")
        util.atomic_write(path, "2026-01-01T00:00:00Z tick 41 starting\n")
        old = time.time() - 3600
        os.utime(path, (old, old))
        self.assertFalse(migrate.legacy_harness_live(self.ld))

    def test_an_untimestamped_stream_line_cannot_fake_an_exit(self):
        util.atomic_write(os.path.join(self.ld, "run.log"),
                          "2026-01-01T00:00:00Z tick 41 starting\n"
                          '{"type":"assistant","text":"LOOP_DONE after"}\n')
        self.assertTrue(migrate.legacy_harness_live(self.ld))


class TestCrashOrdering(Base):
    """The stamp file is the ONLY thing that says a step is done; it must be
    the last write of the step so a kill never leaves it bumped ahead of the
    step's own effects or ahead of the step's audit event."""

    def setUp(self):
        Base.setUp(self)
        util.atomic_write(os.path.join(self.rt, "schema"), "2")

    def test_the_schema_stamp_is_written_after_the_migration_event_not_before(self):
        real_emit = self.events.emit
        seen = {}

        def spy(type, **fields):
            if type == "migration":
                seen["schema_at_emit_time"] = util.read_int(
                    os.path.join(self.rt, "schema"), -1)
            return real_emit(type, **fields)

        self.events.emit = spy
        self.run_migrate()
        # At the moment the event was recorded, the stamp must still show the
        # OLD schema -- proof the event is durable before the transition is
        # marked complete. If a kill lands between the two writes, the next
        # boot re-runs (and re-emits) an idempotent step instead of silently
        # losing the audit trail for a step that already happened.
        self.assertEqual(2, seen["schema_at_emit_time"])
        self.assertEqual(3, util.read_int(os.path.join(self.rt, "schema")))


if __name__ == "__main__":
    unittest.main()
