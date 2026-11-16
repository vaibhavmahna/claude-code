import os
import shutil
import subprocess
import sys
import unittest

from helpers import FAKE, TOOLS, slurp, tmp_project

NODE = "---\nid: module-app\nkind: module\ntitle: App\nsummary: Entry module\ntags: [app]\nsources: [app.py:1]\n---\n## Facts\n- Runs → app.py:1\n"


class TestSave(unittest.TestCase):
    def setUp(self):
        self.p = tmp_project()

    def tearDown(self):
        shutil.rmtree(self.p)

    def save(self, kind, name, text):
        return subprocess.run([sys.executable, os.path.join(TOOLS, "save.py"), kind, name, "--root", self.p],
                              input=text, capture_output=True, text=True)

    def test_node_is_written_and_stamped(self):
        r = self.save("node", "module-app", NODE)
        self.assertEqual(r.returncode, 0, r.stderr)
        text = slurp(os.path.join(self.p, ".claude", "knowledge", "nodes", "module-app.md"))
        self.assertIn("summary: Entry module", text)
        self.assertIn("verified_commit: none", text)
        self.assertIn("- Runs → app.py:1", text)

    def test_node_needs_matching_frontmatter(self):
        self.assertEqual(self.save("node", "module-other", NODE).returncode, 2)
        self.assertEqual(self.save("node", "module-app", "## Facts\n- x\n").returncode, 2)

    def test_scan_matches_the_write_guard(self):
        # one contributor address passes, as it does for Write; a bulk list and a secret do not
        self.assertEqual(self.save("handoff", "main", f"## Goal\nAsk {FAKE['email']} about the schema\n").returncode, 0)
        bulk = "".join(f"person{i}@corp-mail.io\n" for i in range(25))
        self.assertEqual(self.save("handoff", "main", bulk).returncode, 1)
        self.assertEqual(self.save("scratch", "x.txt", f"key {FAKE['github']}\n").returncode, 1)

    def test_handoff_and_scratch_print_their_path(self):
        r = self.save("handoff", "coding-agent", "## Goal\nx\n")
        self.assertRegex(r.stdout.strip(), r"^\.claude/knowledge/handoffs/coding-agent-\d{8}-\d{6}\.md$")
        r = self.save("scratch", "count.py", "print(1)\n")
        self.assertEqual(r.stdout.strip(), ".claude/scratch/count.py")
        self.assertEqual(slurp(os.path.join(self.p, ".claude", "scratch", "count.py")), "print(1)\n")

    def test_rejects_paths_and_secrets(self):
        self.assertEqual(self.save("scratch", "../escape.py", "x\n").returncode, 2)
        r = self.save("scratch", "notes.txt", f"key = {FAKE['github']}\n")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn(FAKE["github"], r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.p, ".claude", "scratch", "notes.txt")))


if __name__ == "__main__":
    unittest.main()
