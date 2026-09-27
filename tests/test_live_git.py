"""Unit tests for LiveGitEngine."""
import os
import unittest
from pathlib import Path

from agent_trace.engine.live_git import LiveGitEngine

# Path to the bundled fixture repo (has at least one commit)
FIXTURE_REPO = Path(__file__).parent / "fixtures" / "ecommerce-checkout-api"


class TestIsGitRepo(unittest.TestCase):

    def test_fixture_repo_is_git_repo(self):
        engine = LiveGitEngine(str(FIXTURE_REPO))
        self.assertTrue(engine.is_git_repo())

    def test_non_repo_path_returns_false(self):
        engine = LiveGitEngine("/tmp")
        self.assertFalse(engine.is_git_repo())


class TestGetRepoMeta(unittest.TestCase):

    def setUp(self):
        self.engine = LiveGitEngine(str(FIXTURE_REPO))

    def test_meta_has_required_keys(self):
        meta = self.engine.get_repo_meta()
        for key in ("name", "root", "branch", "commit_hash", "commit_msg"):
            self.assertIn(key, meta, f"Missing key: {key}")

    def test_meta_name_is_non_empty(self):
        meta = self.engine.get_repo_meta()
        self.assertTrue(meta["name"])

    def test_meta_root_is_valid_path(self):
        meta = self.engine.get_repo_meta()
        self.assertTrue(Path(meta["root"]).exists())


class TestParseDiffHunks(unittest.TestCase):

    def setUp(self):
        self.engine = LiveGitEngine(str(FIXTURE_REPO))

    def test_empty_diff_returns_empty_list(self):
        hunks = self.engine.parse_diff_hunks("")
        self.assertEqual(hunks, [])

    def test_whitespace_only_diff_returns_empty_list(self):
        hunks = self.engine.parse_diff_hunks("   \n  \n  ")
        self.assertEqual(hunks, [])

    def test_synthetic_diff_produces_hunk(self):
        synthetic = (
            "diff --git a/foo.py b/foo.py\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -1,3 +1,4 @@\n"
            "-old_line\n"
            "+new_line\n"
            "+another_line\n"
        )
        hunks = self.engine.parse_diff_hunks(synthetic)
        self.assertEqual(len(hunks), 1)
        self.assertEqual(hunks[0].file_path, "foo.py")
        self.assertIn("new_line", hunks[0].new_lines)

    def test_hunk_change_type_added(self):
        synthetic = (
            "diff --git a/bar.py b/bar.py\n"
            "--- /dev/null\n"
            "+++ b/bar.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+def hello():\n"
            "+    pass\n"
        )
        hunks = self.engine.parse_diff_hunks(synthetic)
        self.assertEqual(len(hunks), 1)
        self.assertEqual(hunks[0].change_type, "ADDED")

    def test_hunk_change_type_deleted(self):
        synthetic = (
            "diff --git a/baz.py b/baz.py\n"
            "--- a/baz.py\n"
            "+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n"
            "-def old():\n"
            "-    pass\n"
        )
        hunks = self.engine.parse_diff_hunks(synthetic)
        self.assertEqual(len(hunks), 1)
        self.assertEqual(hunks[0].change_type, "DELETED")

    def test_lock_files_are_skipped(self):
        synthetic = (
            "diff --git a/package-lock.json b/package-lock.json\n"
            "--- a/package-lock.json\n"
            "+++ b/package-lock.json\n"
            "@@ -1,2 +1,2 @@\n"
            '-{"version": 1}\n'
            '+{"version": 2}\n'
        )
        hunks = self.engine.parse_diff_hunks(synthetic)
        self.assertEqual(hunks, [])


class TestGetDiff(unittest.TestCase):

    def setUp(self):
        self.engine = LiveGitEngine(str(FIXTURE_REPO))

    def test_get_diff_returns_tuple(self):
        raw_diff, mode_desc = self.engine.get_diff("auto")
        self.assertIsInstance(raw_diff, str)
        self.assertIsInstance(mode_desc, str)

    def test_mode_desc_non_empty(self):
        _, mode_desc = self.engine.get_diff("auto")
        self.assertTrue(mode_desc)


if __name__ == "__main__":
    unittest.main()
