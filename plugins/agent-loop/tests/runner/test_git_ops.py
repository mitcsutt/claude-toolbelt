import _path  # noqa: F401
import os
import subprocess
import tempfile
import unittest

from runner import git_ops


def sh(cwd, *args):
    subprocess.run(list(args), cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def repo():
    d = tempfile.mkdtemp()
    sh(d, "git", "init", "-q")
    sh(d, "git", "config", "user.email", "loop@example.com")
    sh(d, "git", "config", "user.name", "Loop")
    sh(d, "git", "config", "commit.gpgsign", "false")
    write(d, "README.md", "seed\n")
    sh(d, "git", "add", "README.md")
    sh(d, "git", "commit", "-q", "-m", "seed")
    return d


def write(d, rel, text):
    path = os.path.join(d, rel)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return path


class TestChangedPaths(unittest.TestCase):
    def setUp(self):
        self.d = repo()

    def test_tracked_edits_and_untracked_files_are_both_reported(self):
        write(self.d, "README.md", "edited\n")
        write(self.d, "src/new.ts", "export const a = 1\n")
        self.assertEqual(["README.md", "src/new.ts"], sorted(git_ops.changed_paths(self.d)))

    def test_a_clean_tree_reports_nothing(self):
        self.assertEqual([], git_ops.changed_paths(self.d))

    def test_a_path_with_a_space_survives_git_quoting(self):
        write(self.d, "a dir/b file.ts", "x\n")
        self.assertIn("a dir/b file.ts", git_ops.changed_paths(self.d))

    def test_a_rename_reports_the_destination(self):
        write(self.d, "old.ts", "x\n")
        sh(self.d, "git", "add", "old.ts")
        sh(self.d, "git", "commit", "-q", "-m", "add old")
        sh(self.d, "git", "mv", "old.ts", "new.ts")
        self.assertIn("new.ts", git_ops.changed_paths(self.d))

    def test_a_rename_reports_both_names(self):
        write(self.d, "old.ts", "x\n")
        sh(self.d, "git", "add", "old.ts")
        sh(self.d, "git", "commit", "-q", "-m", "add old")
        sh(self.d, "git", "mv", "old.ts", "new.ts")
        self.assertEqual(["new.ts", "old.ts"], sorted(git_ops.changed_paths(self.d)))


class TestStrays(unittest.TestCase):
    def test_exact_paths_and_globs_are_inside(self):
        allow = ["src/a.ts", "packages/api/**"]
        changed = ["src/a.ts", "packages/api/src/b.ts", "apps/web/c.ts"]
        self.assertEqual(["apps/web/c.ts"], git_ops.strays(changed, allow))

    def test_a_directory_entry_covers_files_under_it(self):
        self.assertEqual([], git_ops.strays(["src/deep/a.ts"], ["src"]))

    def test_an_empty_allow_list_makes_everything_a_stray(self):
        self.assertEqual(["a/b.ts"], git_ops.strays(["a/b.ts"], []))


class TestRevert(unittest.TestCase):
    def setUp(self):
        self.d = repo()

    def test_a_tracked_edit_is_restored(self):
        write(self.d, "README.md", "vandalised\n")
        git_ops.revert(self.d, ["README.md"])
        with open(os.path.join(self.d, "README.md")) as f:
            self.assertEqual("seed\n", f.read())

    def test_an_untracked_file_is_deleted(self):
        write(self.d, "junk/stray.ts", "x\n")
        git_ops.revert(self.d, ["junk/stray.ts"])
        self.assertFalse(os.path.exists(os.path.join(self.d, "junk/stray.ts")))

    def test_reverting_a_path_that_is_already_gone_is_not_an_error(self):
        git_ops.revert(self.d, ["never/existed.ts"])

    def test_other_changes_are_untouched(self):
        write(self.d, "keep.ts", "keep\n")
        write(self.d, "drop.ts", "drop\n")
        git_ops.revert(self.d, ["drop.ts"])
        self.assertTrue(os.path.exists(os.path.join(self.d, "keep.ts")))

    def test_a_staged_rename_outside_the_allow_list_is_fully_undone(self):
        # End-to-end through the real call path: changed_paths -> strays ->
        # revert. An allow_list that covers neither name means the whole
        # rename is out of scope and must come back exactly as HEAD had it.
        write(self.d, "old.ts", "x\n")
        sh(self.d, "git", "add", "old.ts")
        sh(self.d, "git", "commit", "-q", "-m", "add old")
        sh(self.d, "git", "mv", "old.ts", "new.ts")
        changed = git_ops.changed_paths(self.d)
        to_revert = git_ops.strays(changed, ["README.md"])
        git_ops.revert(self.d, to_revert)
        with open(os.path.join(self.d, "old.ts")) as f:
            self.assertEqual("x\n", f.read())
        self.assertFalse(os.path.exists(os.path.join(self.d, "new.ts")))
        status = subprocess.run(["git", "status", "--porcelain"], cwd=self.d,
                                stdout=subprocess.PIPE).stdout.decode()
        self.assertEqual("", status)

    def test_a_rename_whose_source_is_allow_listed_only_reverts_the_destination(self):
        # The Worker was entitled to delete old.ts (it's in the allow_list),
        # so only new.ts -- the part the contract never sanctioned -- is a
        # stray. old.ts staying deleted is the correct, in-scope outcome.
        write(self.d, "old.ts", "x\n")
        sh(self.d, "git", "add", "old.ts")
        sh(self.d, "git", "commit", "-q", "-m", "add old")
        sh(self.d, "git", "mv", "old.ts", "new.ts")
        changed = git_ops.changed_paths(self.d)
        to_revert = git_ops.strays(changed, ["old.ts"])
        git_ops.revert(self.d, to_revert)
        self.assertFalse(os.path.exists(os.path.join(self.d, "new.ts")))
        self.assertFalse(os.path.exists(os.path.join(self.d, "old.ts")))

    def test_a_non_ascii_untracked_stray_is_reported_and_deleted(self):
        write(self.d, "naïve.ts", "x\n")
        self.assertIn("naïve.ts", git_ops.changed_paths(self.d))
        git_ops.revert(self.d, ["naïve.ts"])
        self.assertFalse(os.path.exists(os.path.join(self.d, "naïve.ts")))

    def test_a_non_ascii_tracked_modification_is_reverted(self):
        write(self.d, "naïve.ts", "orig\n")
        sh(self.d, "git", "add", "naïve.ts")
        sh(self.d, "git", "commit", "-q", "-m", "add naive")
        write(self.d, "naïve.ts", "vandalised\n")
        git_ops.revert(self.d, ["naïve.ts"])
        with open(os.path.join(self.d, "naïve.ts")) as f:
            self.assertEqual("orig\n", f.read())

    def test_an_absolute_path_is_skipped(self):
        outside = tempfile.mkdtemp()
        victim = os.path.join(outside, "victim.ts")
        with open(victim, "w") as f:
            f.write("keep\n")
        git_ops.revert(self.d, [victim])
        with open(victim) as f:
            self.assertEqual("keep\n", f.read())

    def test_a_dotdot_escape_path_is_skipped(self):
        outside = tempfile.mkdtemp()
        victim = os.path.join(outside, "victim.ts")
        with open(victim, "w") as f:
            f.write("keep\n")
        rel = os.path.join("..", os.path.basename(outside), "victim.ts")
        git_ops.revert(self.d, [rel])
        with open(victim) as f:
            self.assertEqual("keep\n", f.read())


class TestCommit(unittest.TestCase):
    def setUp(self):
        self.d = repo()

    def test_commit_returns_the_new_sha_and_writes_the_trailers(self):
        write(self.d, "src/a.ts", "export const a = 1\n")
        sha = git_ops.commit(self.d, ["src/a.ts"], "loop(T1): add a",
                             {"Loop-Status": "done",
                              "Loop-Verification": "lint=pass test=pass",
                              "Loop-Files": "src/a.ts"})
        self.assertEqual(40, len(sha))
        self.assertEqual(sha, git_ops.head_sha(self.d))
        body = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=self.d,
                              stdout=subprocess.PIPE).stdout.decode()
        self.assertIn("loop(T1): add a", body)
        self.assertIn("Loop-Status: done", body)
        self.assertIn("Loop-Verification: lint=pass test=pass", body)
        self.assertIn("Loop-Files: src/a.ts", body)

    def test_only_the_named_paths_are_committed(self):
        write(self.d, "src/a.ts", "a\n")
        write(self.d, "src/b.ts", "b\n")
        git_ops.commit(self.d, ["src/a.ts"], "loop(T1): add a", {"Loop-Status": "done"})
        self.assertEqual(["src/b.ts"], git_ops.changed_paths(self.d))

    def test_nothing_to_commit_returns_empty_string(self):
        self.assertEqual("", git_ops.commit(self.d, [], "loop: nothing", {}))
        write(self.d, "src/a.ts", "a\n")
        git_ops.commit(self.d, ["src/a.ts"], "first", {})
        self.assertEqual("", git_ops.commit(self.d, ["src/a.ts"], "again", {}))

    def test_a_git_failure_raises(self):
        with self.assertRaises(git_ops.GitError):
            git_ops.commit(tempfile.mkdtemp(), ["x"], "s", {})


class TestDiffText(unittest.TestCase):
    def setUp(self):
        self.d = repo()

    def test_working_tree_changes_appear_against_the_base_sha(self):
        base = git_ops.head_sha(self.d)
        write(self.d, "README.md", "changed\n")
        diff = git_ops.diff_text(self.d, base, ["README.md"])
        self.assertIn("-seed", diff)
        self.assertIn("+changed", diff)

    def test_an_untracked_new_file_still_shows_up(self):
        base = git_ops.head_sha(self.d)
        write(self.d, "src/new.ts", "export const a = 1\n")
        diff = git_ops.diff_text(self.d, base, ["src/new.ts"])
        self.assertIn("src/new.ts", diff)
        self.assertIn("+export const a = 1", diff)

    def test_a_huge_diff_is_truncated_with_a_marker(self):
        base = git_ops.head_sha(self.d)
        write(self.d, "big.ts", "x\n" * 50000)
        diff = git_ops.diff_text(self.d, base, ["big.ts"], max_chars=1000)
        self.assertLess(len(diff), 1400)
        self.assertIn("truncated", diff)

    def test_no_paths_means_the_whole_tree(self):
        base = git_ops.head_sha(self.d)
        write(self.d, "README.md", "changed\n")
        self.assertIn("+changed", git_ops.diff_text(self.d, base, []))


class TestSimilarity(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def file(self, name, text):
        return write(self.d, name, text)

    def test_identical_files_are_one(self):
        a = self.file("a.ts", "const x = 1\nconst y = 2\n")
        b = self.file("b.ts", "const x = 1\nconst y = 2\n")
        self.assertEqual(1.0, git_ops.similarity(a, b))

    def test_whitespace_and_blank_lines_are_normalised_away(self):
        a = self.file("a.ts", "const x = 1\n\n  const y = 2\n")
        b = self.file("b.ts", "   const x = 1\nconst y = 2\n\n\n")
        self.assertEqual(1.0, git_ops.similarity(a, b))

    def test_import_order_does_not_count_against_a_copy(self):
        a = self.file("a.ts", "import b from 'b'\nimport a from 'a'\nconst x = 1\n")
        b = self.file("b.ts", "import a from 'a'\nimport b from 'b'\nconst x = 1\n")
        self.assertEqual(1.0, git_ops.similarity(a, b))

    def test_a_real_copy_scores_above_the_default_threshold(self):
        body = "".join("  line %d\n" % i for i in range(40))
        a = self.file("a.tsx", "export const Header = () => (\n" + body + ")\n")
        b = self.file("b.tsx", "export const Header = () => (\n" + body.replace("line 3\n", "line 3b\n") + ")\n")
        self.assertGreater(git_ops.similarity(a, b), 0.9)

    def test_a_six_line_comment_pretending_to_be_a_copy_scores_near_zero(self):
        body = "".join("  line %d\n" % i for i in range(200))
        a = self.file("a.tsx", "export const Header = () => (\n" + body + ")\n")
        b = self.file("b.tsx", "// TODO(copied from a.tsx)\n// see the original\n"
                               "export const Header = () => null\n")
        self.assertLess(git_ops.similarity(a, b), 0.2)

    def test_a_missing_file_scores_zero_rather_than_raising(self):
        a = self.file("a.ts", "const x = 1\n")
        self.assertEqual(0.0, git_ops.similarity(a, os.path.join(self.d, "gone.ts")))

    def test_two_empty_files_are_identical(self):
        self.assertEqual(1.0, git_ops.similarity(self.file("a", ""), self.file("b", "")))


if __name__ == "__main__":
    unittest.main()
