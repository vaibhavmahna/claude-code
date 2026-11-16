import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from helpers import ROOT, TOOLS, run_hook, slurp, spit
import doc_format as df

DOCS = ["README.md", "docs/contracts.md", "docs/security.md", "docs/configuration.md", "template/AGENTS.md", "template/CLAUDE.md.tmpl", "evals/README.md"]


def run(*args):
    return subprocess.run([sys.executable, os.path.join(TOOLS, "doc_format.py"), *args], capture_output=True, text=True)


class TestSlug(unittest.TestCase):
    def test_github_compatible_anchors(self):
        seen = {}
        self.assertEqual(df.slugify("🚀 Quick start", seen), "-quick-start")
        self.assertEqual(df.slugify("🔧 Options (≤ 50% per `agent`)", seen), "-options--50-per-agent")
        self.assertEqual(df.slugify("🤖 1. Roster", seen), "-1-roster")
        self.assertEqual(df.slugify("🚀 Quick start", seen), "-quick-start-1")


class TestCheckAndToc(unittest.TestCase):
    def setUp(self):
        self.p = os.path.join(tempfile.mkdtemp(), "doc.md")

    def test_toc_round_trip(self):
        spit(self.p, "# 🧰 Tool\n\nIntro.\n\n## 🚀 Start\nx\n\n### 🔧 Flags\ny\n\n## 🔒 Security\nSee [flags](#-flags).\n")
        self.assertEqual(run("check", self.p).returncode, 1)
        self.assertEqual(run("toc", self.p).returncode, 0)
        first = slurp(self.p)
        self.assertIn("  - [Flags](#-flags)", first)
        self.assertNotIn("[🚀", first)
        run("toc", self.p)
        self.assertEqual(slurp(self.p), first, "toc must be idempotent")
        r = run("check", self.p)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_violations(self):
        spit(self.p, "# Plain title\n\n## ⚙️ Settings\nDone ✅\n\n#### 🔧 Deep\n[x](#-nope)\n")
        out = run("check", self.p).stdout
        for needle in ("must start with one emoji", "single-codepoint", "emoji outside a heading",
                       "jumps from h2 to h4", "missing TOC", "missing anchor"):
            self.assertIn(needle, out)

    def test_stale_toc_and_code_ignored(self):
        spit(self.p, "# 🧰 T\n\n## 🚀 A\n```\n## not a heading ✅\n<!-- toc -->\n```\n")
        run("toc", self.p)
        spit(self.p, slurp(self.p).replace("## 🚀 A", "## 🚀 Renamed"))
        self.assertIn("TOC is out of date", run("check", self.p).stdout)


class TestRepoDocs(unittest.TestCase):
    def test_harness_docs_follow_format(self):
        r = run("check", *[os.path.join(ROOT, d) for d in DOCS])
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_doc_templates_follow_format(self):
        import re
        refs = os.path.join(ROOT, "template", "claude", "skills", "doc-standards", "references")
        for f in os.listdir(refs):
            block = re.search(r"```markdown\n(.*?)\n```", slurp(os.path.join(refs, f)), re.S).group(1)
            errors, _ = df.check(f, block)
            self.assertEqual(errors, [], f)


class TestDocCheckHook(unittest.TestCase):
    def setUp(self):
        self.p = tempfile.mkdtemp(prefix="harness-doccheck-")
        self.addCleanup(shutil.rmtree, self.p, True)

    def hint(self, rel, text, tool="Write"):
        spit(os.path.join(self.p, rel), text)
        out = run_hook("doc_check.py", {"tool_name": tool, "tool_input": {"file_path": os.path.join(self.p, rel)}}, self.p)
        return (out.get("hookSpecificOutput") or {}).get("additionalContext", "")

    def test_new_doc_without_format_gets_a_hint(self):
        msg = self.hint("docs/usage.md", "# Usage\n\n## Run the tests\n\ntext\n")
        self.assertIn("[harness:docs] docs/usage.md", msg)
        self.assertIn("doc_format.py toc docs/usage.md", msg)

    def test_formatted_doc_edits_and_exempt_files_are_quiet(self):
        good = "# 🧪 Usage\n\n<!-- toc -->\n## 📑 Contents\n\n- [Run](#-run)\n\n<!-- /toc -->\n\n## ⚡ Run\n\ntext\n"
        self.assertEqual(self.hint("docs/ok.md", good), "")
        self.assertEqual(self.hint("README.md", "# Old readme\n", tool="Edit"), "")
        self.assertEqual(self.hint("CHANGELOG.md", "# Changelog\n"), "")
        self.assertEqual(self.hint(".claude/knowledge/handoffs/x.md", "## Goal\n"), "")


if __name__ == "__main__":
    unittest.main()
