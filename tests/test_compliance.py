"""The compliance commit gate: `git commit` needs a compliance-reviewer PASS for exactly what is staged."""
import json
import os
import shutil
import subprocess
import sys
import unittest

from helpers import ROOT, TOOLS, decision, run_hook, slurp, spit, tmp_project

import compliance

REVIEWER = {"subagent_type": "compliance-reviewer"}
NOTE = "## Compliance note\n\n**Data categories touched:** none\n**Deletion path:** N/A, no data about people\n"


def git(proj, *args):
    return subprocess.run(["git", "-C", proj, *args], capture_output=True, text=True, check=True).stdout


def bash(proj, cmd):
    return run_hook("guard_bash.py", {"tool_name": "Bash", "tool_input": {"command": cmd}}, proj)


def review(proj, text, who=REVIEWER, as_blocks=True):
    resp = {"content": [{"type": "text", "text": text}]} if as_blocks else text
    out = run_hook("guard_compliance.py", {"tool_name": "Agent", "tool_input": who, "tool_response": resp,
                                           "session_id": "s1"}, proj)
    return (out.get("hookSpecificOutput") or {}).get("additionalContext", "")


def staged_id(proj):
    return compliance.changes_id(compliance.staged_changes(proj))


def reason(out):
    return (out.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "")


class TestCommitGate(unittest.TestCase):
    def setUp(self):
        self.proj = tmp_project()
        git(self.proj, "init", "-q")
        git(self.proj, "config", "user.email", "dev@example.com")
        git(self.proj, "config", "user.name", "Dev")
        spit(os.path.join(self.proj, "app.py"), "print('one')\n")
        spit(os.path.join(self.proj, "docs.md"), "# Docs\n")
        git(self.proj, "add", "app.py", "docs.md")

    def tearDown(self):
        shutil.rmtree(self.proj)

    def passed(self):
        return review(self.proj, f"FINDINGS:\n- none\n\n{NOTE}\nCOMPLIANCE: PASS {staged_id(self.proj)}\nSTATUS: DONE")

    def test_commit_is_denied_until_the_reviewer_passes_the_staged_files(self):
        out = bash(self.proj, "git commit -m 'feat: x'")
        self.assertEqual(decision(out), "deny")
        self.assertIn("compliance-reviewer", reason(out))
        self.assertIn("app.py", reason(out))
        self.assertIn("PASS recorded", self.passed())
        self.assertIsNone(decision(bash(self.proj, "git commit -m 'feat: x'")))
        self.assertIsNone(decision(bash(self.proj, "git commit -m \"$(cat <<'EOF'\nfeat: x\n\nbody\nEOF\n)\"")))

    def test_only_the_compliance_reviewer_can_record_a_pass(self):
        verdict = f"COMPLIANCE: PASS {staged_id(self.proj)}"
        for who in ({"subagent_type": "code-reviewer"}, {"subagent_type": "general-purpose"}, {}):
            self.assertEqual(review(self.proj, verdict, who), "")
        self.assertEqual(decision(bash(self.proj, "git commit -m x")), "deny")
        self.assertFalse(os.path.exists(os.path.join(self.proj, ".claude", "state", "compliance-pass.json")))

    def test_an_edit_after_the_review_needs_a_new_review(self):
        self.passed()
        spit(os.path.join(self.proj, "app.py"), "print('two')\n")
        git(self.proj, "add", "app.py")
        out = bash(self.proj, "git commit -m x")
        self.assertEqual(decision(out), "deny")
        self.assertIn("app.py", reason(out))
        self.assertNotIn("docs.md", reason(out))  # the unchanged file is still covered
        self.passed()
        self.assertIsNone(decision(bash(self.proj, "git commit -m x")))

    def test_a_pass_for_other_content_is_not_recorded(self):
        old = staged_id(self.proj)
        spit(os.path.join(self.proj, "app.py"), "print('changed during review')\n")
        git(self.proj, "add", "app.py")
        self.assertIn("not recorded", review(self.proj, f"COMPLIANCE: PASS {old}"))
        self.assertEqual(decision(bash(self.proj, "git commit -m x")), "deny")

    def test_fail_drops_an_earlier_pass(self):
        self.passed()
        self.assertIn("FAIL recorded", review(self.proj, "FINDINGS:\n1. [BLOCK] app.py:1: ...\nCOMPLIANCE: FAIL"))
        self.assertEqual(decision(bash(self.proj, "git commit -m x")), "deny")
        # A result that carries both verdicts is a FAIL.
        self.assertIn("FAIL recorded", review(self.proj, f"COMPLIANCE: FAIL\nCOMPLIANCE: PASS {staged_id(self.proj)}"))
        self.assertIn("no `COMPLIANCE:` verdict", review(self.proj, "looks fine to me"))

    def test_reviewed_files_can_be_split_across_commits(self):
        self.passed()
        git(self.proj, "rm", "-q", "--cached", "docs.md")  # unstage (no HEAD yet, so not `restore --staged`)
        self.assertIsNone(decision(bash(self.proj, "git commit -m 'feat: app'")))
        git(self.proj, "commit", "-q", "-m", "feat: app")
        git(self.proj, "add", "docs.md")
        self.assertIsNone(decision(bash(self.proj, "git commit -m 'docs: docs'")))
        git(self.proj, "commit", "-q", "-m", "docs: docs")
        spit(os.path.join(self.proj, "new.py"), "x = 1\n")
        git(self.proj, "add", "new.py")
        self.assertEqual(decision(bash(self.proj, "git commit -m 'feat: new'")), "deny")

    def test_deleting_a_file_is_reviewed_too(self):
        self.passed()
        git(self.proj, "commit", "-q", "-m", "init")
        git(self.proj, "rm", "-q", "docs.md")
        self.assertEqual(decision(bash(self.proj, "git commit -m 'docs: drop'")), "deny")
        self.passed()
        self.assertIsNone(decision(bash(self.proj, "git commit -m 'docs: drop'")))

    def test_ways_around_the_gate_are_denied(self):
        self.passed()
        for cmd in ("git commit -am x", "git commit --all -m x", "git commit -m x app.py", "git commit -m x -- app.py",
                    "git commit --include app.py -m x", "git commit -p -m x", "git add . && git commit -m x",
                    "git commit -m x; git push", "bash -c 'git commit -m x'", "git -C . commit -m x",
                    "git commit --no-verify -m x", "git commit -n -m x", "git commit-tree HEAD^{tree} -m x",
                    "git commit -m \"$(git add . ; echo x)\""):
            with self.subTest(cmd=cmd):
                self.assertEqual(decision(bash(self.proj, cmd)), "deny")
        for cmd in ("git merge main", "git cherry-pick abc123", "git revert HEAD", "git am x.patch",
                    "git commit --amend -m x"):
            with self.subTest(cmd=cmd):
                self.assertEqual(decision(bash(self.proj, cmd)), "ask")

    def test_agents_cannot_write_the_pass_themselves(self):
        target = os.path.join(self.proj, ".claude", "state", "compliance-pass.json")
        out = run_hook("guard_write.py", {"tool_name": "Write", "tool_input": {"file_path": target, "content": "{}"}}, self.proj)
        self.assertEqual(decision(out), "deny")
        for cmd in ("echo '{}' > .claude/state/compliance-pass.json", "cp /tmp/p.json .claude/state/compliance-pass.json",
                    "python3 -c \"open('.claude/state/compliance-pass.json','w').write('{}')\""):
            with self.subTest(cmd=cmd):
                self.assertEqual(decision(bash(self.proj, cmd)), "deny")

    def test_gate_can_only_be_switched_off_in_harness_json(self):
        spit(os.path.join(self.proj, ".claude", "harness.json"), json.dumps({"compliance": {"commit_gate": "off"}}))
        self.assertIsNone(decision(bash(self.proj, "git commit -m x")))
        self.assertEqual(decision(bash(self.proj, "git commit --no-verify -m x")), "deny")
        out = bash(self.proj, "echo '{}' > .claude/harness.json")
        self.assertEqual(decision(out), "deny")

    def test_pull_request_needs_the_compliance_note(self):
        self.assertEqual(decision(bash(self.proj, "gh pr create --title t --body 'summary only'")), "deny")
        self.assertEqual(decision(bash(self.proj, "gh pr create --fill")), "deny")
        self.assertEqual(decision(bash(self.proj, "gh pr create --title t --body '## Compliance note\nnone'")), "ask")
        spit(os.path.join(self.proj, "pr.md"), "Summary\n\n" + NOTE)
        self.assertEqual(decision(bash(self.proj, "gh pr create --title t --body-file pr.md")), "ask")
        self.assertEqual(decision(bash(self.proj, "gh pr create --title t --body-file missing.md")), "deny")

    def test_every_verdict_and_commit_check_is_logged(self):
        bash(self.proj, "git commit -m x")
        self.passed()
        bash(self.proj, "git commit -m x")
        review(self.proj, "COMPLIANCE: FAIL")
        rows = [json.loads(l) for l in slurp(os.path.join(self.proj, ".claude", "state", "compliance-log.jsonl")).splitlines()]
        self.assertEqual([(r["actor"], r["action"], r["outcome"]) for r in rows],
                         [("agent", "commit-check", "denied"), ("compliance-reviewer", "review", "pass"),
                          ("agent", "commit-check", "allowed"), ("compliance-reviewer", "review", "fail")])
        for r in rows:
            self.assertRegex(r["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertNotIn("print(", json.dumps(rows))  # identifiers only, never file content

    def test_audit_log_is_rotated_not_unbounded(self):
        log = os.path.join(self.proj, ".claude", "state", "compliance-log.jsonl")
        spit(log, "x" * (compliance.LOG_MAX_BYTES + 1))
        compliance.log_event(self.proj, "agent", "commit-check", "abc", "denied")
        self.assertTrue(os.path.getsize(log + ".1") > compliance.LOG_MAX_BYTES)
        self.assertEqual(len(slurp(log).splitlines()), 1)

    def test_tool_commands_and_git_hook(self):
        def tool(*args):
            return subprocess.run([sys.executable, os.path.join(TOOLS, "compliance.py"), "--root", self.proj, *args],
                                  capture_output=True, text=True)
        self.assertEqual(tool("id").stdout.strip(), staged_id(self.proj))
        self.assertEqual(tool("check").returncode, 1)
        self.assertEqual(tool("status").returncode, 0)
        self.assertIn("BLOCKED", tool("status").stdout)
        self.assertEqual(tool("note").returncode, 1)
        self.passed()
        self.assertEqual(tool("check").returncode, 0)
        self.assertIn("Data categories touched", tool("note").stdout)
        # --git-hook: the same check for commits typed in a terminal
        r = subprocess.run([sys.executable, os.path.join(ROOT, "install.py"), self.proj, "--git-hook", "--no-seed"],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        hook = os.path.join(self.proj, ".git", "hooks", "pre-commit")
        self.assertTrue(os.access(hook, os.X_OK))
        git(self.proj, "add", "-A")  # the harness files are new, unreviewed content
        denied = subprocess.run(["git", "-C", self.proj, "commit", "-q", "-m", "x"], capture_output=True, text=True)
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn("BLOCKED", denied.stdout + denied.stderr)
        self.passed()
        git(self.proj, "commit", "-q", "-m", "x")
        subprocess.run([sys.executable, os.path.join(ROOT, "install.py"), self.proj, "--uninstall"], capture_output=True, text=True)
        self.assertFalse(os.path.exists(hook))

    def test_outside_a_git_repository_the_gate_stays_out_of_the_way(self):
        plain = tmp_project()
        try:
            self.assertIsNone(decision(bash(plain, "git commit -m x")))
        finally:
            shutil.rmtree(plain)


class TestComplianceContent(unittest.TestCase):
    """The skill ships whole, and every agent is told to follow it."""
    BASE = os.path.join(ROOT, "template", "claude")

    def test_skill_and_references_ship(self):
        skill = os.path.join(self.BASE, "skills", "data-handling-compliance")
        core = slurp(os.path.join(skill, "SKILL.md"))
        for needle in ("## Classify the data", "## The three tiers", "## Rules that apply to everything", "## Escalation"):
            self.assertIn(needle, core)
        for ref, needle in (("pii", "### Deletion timing"), ("phi", "Processing PHI through any vendor without a BAA"),
                            ("customer-data", "Training or fine-tuning any model on Customer Data"),
                            ("ai-features", "Prohibited practices"), ("soc2", "overclaiming is the risk"),
                            ("pr-compliance-note", "**Data categories touched:**"), ("open-items", "## Changelog")):
            with self.subTest(ref=ref):
                self.assertIn(f"references/{ref}.md", core)
                self.assertIn(needle, slurp(os.path.join(skill, "references", f"{ref}.md")))

    def test_every_agent_that_changes_or_reviews_work_names_the_skill(self):
        for agent in ("coding-agent", "frontend-ui-builder", "lint-fixer", "doc-writer", "knowledge-keeper",
                      "security-auditor", "compliance-reviewer", "orchestrator"):
            with self.subTest(agent=agent):
                self.assertIn("data-handling-compliance", slurp(os.path.join(self.BASE, "agents", f"{agent}.md")))
        for agent in ("commit-helper", "code-reviewer", "security-auditor", "orchestrator"):
            with self.subTest(agent=agent):
                self.assertIn("compliance-reviewer", slurp(os.path.join(self.BASE, "agents", f"{agent}.md")))
        for doc in ("CLAUDE.md.tmpl", "AGENTS.md"):
            self.assertIn("COMPLIANCE: PASS", slurp(os.path.join(ROOT, "template", doc)))

    def test_gate_is_on_and_registered_in_the_template(self):
        self.assertEqual(json.loads(slurp(os.path.join(self.BASE, "harness.json")))["compliance"]["commit_gate"], "block")
        settings = slurp(os.path.join(self.BASE, "settings.json"))
        self.assertIn("guard_compliance.py", settings)
        self.assertIn("Bash(git commit --no-verify*)", json.loads(settings)["permissions"]["deny"])


if __name__ == "__main__":
    unittest.main()
