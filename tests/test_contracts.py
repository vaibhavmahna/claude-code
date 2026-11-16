import os
import shutil
import tempfile
import unittest

from helpers import ROOT, slurp, spit
import doctor

TEMPLATE = os.path.join(ROOT, "template")


class TestControlContract(unittest.TestCase):
    def test_template_is_within_contract_and_budgets(self):
        found = doctor.check_contracts(os.path.join(TEMPLATE, "claude"),
                                       [os.path.join(TEMPLATE, "CLAUDE.md.tmpl"), os.path.join(TEMPLATE, "AGENTS.md")])
        self.assertEqual(found, [])

    def test_drifted_agent_and_bloated_prompts_are_flagged(self):
        d = tempfile.mkdtemp()
        claude = os.path.join(d, ".claude")
        shutil.copytree(os.path.join(TEMPLATE, "claude", "agents"), os.path.join(claude, "agents"))
        shutil.copytree(os.path.join(TEMPLATE, "claude", "skills"), os.path.join(claude, "skills"))
        shutil.copy(os.path.join(TEMPLATE, "claude", "harness.json"), claude)
        spit(os.path.join(claude, "agents", "rogue.md"), "---\nname: rogue\ndescription: x\nmodel: gpt-9\n---\nDo anything.\n")
        p = os.path.join(claude, "agents", "lint-fixer.md")
        spit(p, slurp(p) + "x" * 7000)
        p = os.path.join(claude, "skills", "token-budget", "SKILL.md")
        spit(p, slurp(p) + "y" * 9000)
        big = os.path.join(d, "CLAUDE.md")
        spit(big, "z" * 20000)
        msgs = " | ".join(m for _, m in doctor.check_contracts(claude, [big]))
        for needle in ("rogue.md: name must match the file and model must be a known tier",
                       "rogue.md: missing return block", "rogue.md: missing context-budget handoff rule",
                       "lint-fixer.md:", "prompt budget", "token-budget/SKILL.md:", "loaded every session"):
            self.assertIn(needle, msgs)
        shutil.rmtree(d)

    def test_test_budget_is_wired_into_test_writers(self):
        skills = os.path.join(TEMPLATE, "claude", "skills")
        self.assertIn("## Test budget", slurp(os.path.join(skills, "test-driven-dev", "SKILL.md")))
        for agent in ("coding-agent", "frontend-ui-builder", "code-reviewer"):
            self.assertIn("test budget", slurp(os.path.join(TEMPLATE, "claude", "agents", f"{agent}.md")))


if __name__ == "__main__":
    unittest.main()
