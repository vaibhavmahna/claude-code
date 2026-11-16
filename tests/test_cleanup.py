import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from helpers import TOOLS, decision, run_hook, spit


def sh(cwd, *args):
    return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True)


class TestCleanup(unittest.TestCase):
    def setUp(self):
        self.p = tempfile.mkdtemp(prefix="harness-clean-")
        os.makedirs(os.path.join(self.p, ".claude", "state"))
        spit(os.path.join(self.p, "app.py"), "x = 1\n")
        sh(self.p, "git", "init", "-q")
        sh(self.p, "git", "add", "-A")
        sh(self.p, "git", "-c", "user.email=a@example.com", "-c", "user.name=t", "commit", "-qm", "init")
        spit(os.path.join(self.p, ".claude", "state", "session-start"), str(time.time() - 5))

    def tearDown(self):
        shutil.rmtree(self.p)

    def tool(self, *args):
        return subprocess.run([sys.executable, os.path.join(TOOLS, "cleanup.py"), *args], capture_output=True,
                              text=True, env={**os.environ, "CLAUDE_PROJECT_DIR": self.p})

    def create_via_agent(self, rel, text="tmp\n"):
        run_hook("housekeeping.py", {"tool_name": "Write", "tool_input": {"file_path": os.path.join(self.p, rel)}}, self.p, "pre")
        spit(os.path.join(self.p, rel), text)

    def test_agent_created_temp_files_are_removed_to_trash_and_restorable(self):
        self.create_via_agent("debug_count.py")
        self.create_via_agent("src/feature.py", "def f():\n    return 1\n")
        out = self.tool("candidates").stdout
        self.assertIn("looks temporary", out)
        self.assertIn("debug_count.py", out)
        self.assertIn("src/feature.py", out)
        r = self.tool("remove", "debug_count.py")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.p, "debug_count.py")))
        self.tool("restore", "debug_count.py")
        self.assertTrue(os.path.exists(os.path.join(self.p, "debug_count.py")))

    def test_refuses_what_the_agent_did_not_create(self):
        spit(os.path.join(self.p, ".env"), "A=1\n")
        spit(os.path.join(self.p, "user_notes.txt"), "mine\n")
        os.utime(os.path.join(self.p, "user_notes.txt"), (time.time() - 3600, time.time() - 3600))
        for path, why in (("app.py", "tracked by git"), (".env", "sensitive"), ("user_notes.txt", "not created by an agent"),
                          (".claude/settings.json", "harness file"), ("../outside.txt", "outside the project")):
            with self.subTest(path):
                r = self.tool("remove", path)
                self.assertEqual(r.returncode, 1)
                self.assertIn(why, r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.p, "app.py")))

    def test_ignored_build_output_can_be_trashed_and_restored(self):
        spit(os.path.join(self.p, ".gitignore"), "dist/\n.env\n")
        spit(os.path.join(self.p, "dist", "js", "bundle.js"), "x\n")
        spit(os.path.join(self.p, "src", "new.py"), "x\n")
        spit(os.path.join(self.p, ".env"), "A=1\n")
        os.utime(os.path.join(self.p, "src", "new.py"), (time.time() - 3600, time.time() - 3600))
        self.assertEqual(self.tool("remove", "dist").returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.p, "dist")))
        self.assertEqual(self.tool("remove", "src").returncode, 1)   # a folder git does not ignore
        self.assertEqual(self.tool("remove", ".env").returncode, 1)  # ignored, but sensitive
        self.assertEqual(self.tool("restore", "all").returncode, 0)
        self.assertTrue(os.path.exists(os.path.join(self.p, "dist", "js", "bundle.js")))

    def test_git_ignored_data_is_not_build_output(self):
        spit(os.path.join(self.p, ".gitignore"), "data/\nlocal.db\n*.log\n")
        spit(os.path.join(self.p, "data", "uploads", "photo.bin"), "x\n")
        spit(os.path.join(self.p, "local.db"), "x\n")
        spit(os.path.join(self.p, "debug.log"), "x\n")  # temporary-looking and written this session
        for path in ("data", "data/uploads/photo.bin", "local.db"):
            with self.subTest(path):
                r = self.tool("remove", path)
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertTrue(os.path.exists(os.path.join(self.p, path)))
        self.assertEqual(self.tool("remove", "debug.log").returncode, 0)

    def test_untracked_since_session_needs_flag(self):
        spit(os.path.join(self.p, "out.txt"), "script output\n")
        self.assertEqual(self.tool("remove", "out.txt").returncode, 1)
        self.assertEqual(self.tool("remove", "--untracked", "out.txt").returncode, 0)

    def test_scratch_and_stop_reminder(self):
        spit(os.path.join(self.p, ".claude", "scratch", "render.png"), "x")
        self.assertIn(".claude/scratch/render.png", self.tool("candidates").stdout)
        self.assertEqual(self.tool("remove", ".claude/scratch/render.png").returncode, 0)
        self.create_via_agent("tmp_data.json")
        out = run_hook("stop_checks.py", {"hook_event_name": "Stop"}, self.p)
        self.assertEqual(out.get("decision"), "block")
        self.assertIn("[harness:cleanup]", out["reason"])
        self.assertIn("tmp_data.json", out["reason"])
        self.assertEqual(run_hook("stop_checks.py", {"stop_hook_active": True}, self.p), {})

    def test_real_new_files_do_not_trigger_the_reminder(self):
        self.create_via_agent("src/feature.py", "def f():\n    return 1\n")
        self.assertEqual(run_hook("stop_checks.py", {"hook_event_name": "Stop"}, self.p), {})

    def test_purge_drops_old_handoffs_only(self):
        hdir = os.path.join(self.p, ".claude", "knowledge", "handoffs")
        for name in ("old.md", "new.md", ".gitkeep"):
            spit(os.path.join(hdir, name), "x\n")
        week_ago = time.time() - 8 * 86400
        for name in ("old.md", ".gitkeep"):
            os.utime(os.path.join(hdir, name), (week_ago, week_ago))
        self.assertEqual(self.tool("purge").returncode, 0)
        self.assertEqual(sorted(os.listdir(hdir)), [".gitkeep", "new.md"])

    def test_pre_hook_ignores_existing_files(self):
        run_hook("housekeeping.py", {"tool_name": "Write", "tool_input": {"file_path": os.path.join(self.p, "app.py")}}, self.p, "pre")
        self.assertFalse(os.path.exists(os.path.join(self.p, ".claude", "state", "created-files")))

    def test_bash_guard_allows_cleanup_tool_but_not_rm(self):
        ev = lambda c: {"tool_name": "Bash", "tool_input": {"command": c}}
        self.assertIsNone(decision(run_hook("guard_bash.py", ev("python3 .claude/tools/cleanup.py remove tmp.py"), self.p)))
        self.assertEqual(decision(run_hook("guard_bash.py", ev("rm tmp.py"), self.p)), "deny")


if __name__ == "__main__":
    unittest.main()
