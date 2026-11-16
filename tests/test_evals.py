import json
import os
import re
import subprocess
import sys
import unittest

from helpers import ROOT, slurp

EVALS = os.path.join(ROOT, "evals")
sys.path.insert(0, EVALS)
import run_evals  # noqa: E402

KNOWN = set(re.findall(r'if kind == "(\w+)"', slurp(os.path.join(EVALS, "run_evals.py"))))


class TestEvalSuite(unittest.TestCase):
    def test_scenarios_are_well_formed(self):
        spec = json.loads(slurp(os.path.join(EVALS, "scenarios.json")))
        names = [s["name"] for s in spec["scenarios"]]
        self.assertEqual(len(names), len(set(names)))
        for s in spec["scenarios"]:
            with self.subTest(s["name"]):
                self.assertTrue(s["prompt"] and s["why"] and s["checks"])
                self.assertLessEqual(s.get("budget_usd", spec["defaults"]["budget_usd"]), 4.0)
                kinds = [k for c in s["checks"] for k in c]
                kinds += [k for c in s["checks"] for sub in c.get("any", []) for k in sub]
                self.assertEqual([k for k in kinds if k not in KNOWN and k != "any"], [])

    def test_stream_parsing_collects_text_tools_and_result(self):
        raw = "\n".join(json.dumps(e) for e in [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "hello"},
                                                         {"type": "tool_use", "name": "Read", "input": {"file_path": "a"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "file body"}]}},
            {"type": "result", "result": "done", "total_cost_usd": 0.01, "num_turns": 2}])
        run = run_evals.parse_stream(raw + "\nnot json")
        self.assertIn("file body", run["corpus"])
        self.assertEqual(run["tools"][0]["name"], "Read")
        self.assertEqual(run["final"]["num_turns"], 2)

    def test_new_check_kinds(self):
        raw = "\n".join(json.dumps(e) for e in [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Agent", "input": {"subagent_type": "coding-agent", "prompt": "build"}},
                {"type": "tool_use", "name": "Bash", "input": {"command": "echo fake-value-123 > out.txt"}},
                {"type": "tool_use", "name": "Agent", "input": {"subagent_type": "code-reviewer", "prompt": "review"}}]}},
            {"type": "result", "result": "2 tests fail: test_x", "is_error": False}])
        ctx = {"proj": "/nonexistent", "run": run_evals.parse_stream(raw), "$SECRET": "fake-value-123"}
        check = lambda c: run_evals.run_check(c, ctx)[0]  # noqa: E731
        self.assertEqual(ctx["run"]["agents"], ["coding-agent", "code-reviewer"])
        self.assertFalse(check({"transcript_excludes": ["$SECRET"]}))  # a value in a tool input counts as a leak
        self.assertTrue(check({"agent_sequence": ["coding-agent", "code-reviewer"]}))
        self.assertFalse(check({"agent_sequence": ["code-reviewer", "coding-agent"]}))
        self.assertFalse(check({"agent_sequence": ["coding-agent", "commit-helper"]}))
        self.assertTrue(check({"agents_exclude": ["security-auditor"]}))
        self.assertFalse(check({"agents_exclude": ["code-reviewer"]}))
        self.assertTrue(check({"tool_input_contains": ["echo .* > out"]}))
        self.assertFalse(check({"tool_input_excludes": ["out\\.txt"]}))
        self.assertTrue(check({"result_matches": ["(?i)fail", "test_x"]}))
        self.assertFalse(check({"result_excludes": ["(?i)fail"]}))
        self.assertTrue(check({"path_missing": ["dist"]}))
        self.assertFalse(check({"handoff_file": "coding-agent"}))

    def test_compare_prints_both_runs(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            for name, harness, cost in (("a.json", True, 0.5), ("b.json", False, 0.2)):
                with open(os.path.join(d, name), "w") as fh:
                    json.dump({"model": "sonnet", "harness": harness, "results": [
                        {"name": "s1", "pass": harness, "passed_trials": int(harness), "trials": 1, "cost_usd": cost}]}, fh)
            r = subprocess.run([sys.executable, os.path.join(EVALS, "run_evals.py"), "--compare",
                                os.path.join(d, "a.json"), os.path.join(d, "b.json")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no harness", r.stdout)
        self.assertRegex(r.stdout, r"s1\s+1/1\s+0/1\s+0\.500\s+0\.200\s+-0\.300")

    def test_child_env_drops_session_variables(self):
        os.environ["CLAUDE_CODE_SESSION_ID"] = "x"
        self.assertNotIn("CLAUDE_CODE_SESSION_ID", run_evals.child_env())

    def test_dry_run_builds_a_project_without_spending(self):
        r = subprocess.run([sys.executable, os.path.join(EVALS, "run_evals.py"), "--dry-run", "--only", "kg-update"],
                           capture_output=True, text=True, timeout=180, env={**os.environ, "CLAUDE_BIN": "/nonexistent/claude"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("DRY  kg-update", r.stdout)
        proj = re.search(r"DRY  kg-update: (\S+)", r.stdout).group(1)
        self.assertIn("module-app-text", slurp(os.path.join(proj, ".claude", "knowledge", "INDEX.md")))
        import shutil
        shutil.rmtree(os.path.dirname(proj), ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
