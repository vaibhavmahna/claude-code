import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from helpers import ROOT, slurp, spit


def install(proj, *args):
    return subprocess.run([sys.executable, os.path.join(ROOT, "install.py"), proj, *args],
                          capture_output=True, text=True, timeout=120)


def snapshot(proj):
    out = {}
    for dp, dn, fn in os.walk(proj):
        dn[:] = [d for d in dn if d not in (".git", "state", "__pycache__")]
        for f in fn:
            p = os.path.join(dp, f)
            out[os.path.relpath(p, proj)] = slurp(p) if not f.endswith(".pyc") else ""
    return out


class TestInstaller(unittest.TestCase):
    def setUp(self):
        self.proj = tempfile.mkdtemp(prefix="harness-install-")
        spit(os.path.join(self.proj, "package.json"),
             json.dumps({"name": "demo", "scripts": {"test": "vitest", "lint": "eslint ."}, "devDependencies": {"typescript": "5"}}))
        spit(os.path.join(self.proj, "src", "index.ts"), "export const main = () => 1;\n")
        spit(os.path.join(self.proj, "README.md"), "# Demo\n\n## Usage\n")
        spit(os.path.join(self.proj, "CLAUDE.md"), "# My notes\nKeep this.\n")
        spit(os.path.join(self.proj, ".claude", "settings.json"),
             json.dumps({"model": "sonnet", "permissions": {"allow": ["Bash(make *)"], "deny": ["Read(./private/**)"]}}))

    def tearDown(self):
        shutil.rmtree(self.proj)

    def test_install_merge_idempotent_uninstall(self):
        r = install(self.proj)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("18 passed", r.stdout)
        s = json.loads(slurp(os.path.join(self.proj, ".claude", "settings.json")))
        self.assertEqual(s["model"], "sonnet")
        self.assertIn("Bash(make *)", s["permissions"]["allow"])
        self.assertIn("Read(./private/**)", s["permissions"]["deny"])
        self.assertIn("Bash(npm test)", s["permissions"]["allow"])
        self.assertIn("PreToolUse", s["hooks"])
        claude_md = slurp(os.path.join(self.proj, "CLAUDE.md"))
        self.assertTrue(claude_md.startswith("# My notes\nKeep this."))
        self.assertIn("<!-- harness:start -->", claude_md)
        self.assertIn("TypeScript", claude_md)
        self.assertNotIn("{{", claude_md)
        self.assertTrue(os.path.isfile(os.path.join(self.proj, "AGENTS.md")))
        self.assertTrue(os.path.isfile(os.path.join(self.proj, ".claude", "knowledge", "INDEX.md")))
        self.assertEqual(len(os.listdir(os.path.join(self.proj, ".claude", "agents"))), 14)
        self.assertTrue(os.access(os.path.join(self.proj, ".claude", "hooks", "guard_bash.py"), os.X_OK))

        before = snapshot(self.proj)
        r2 = install(self.proj)
        self.assertEqual(r2.returncode, 0)
        self.assertEqual(snapshot(self.proj), before, "second install must be a no-op")

        r3 = install(self.proj, "--uninstall")
        self.assertEqual(r3.returncode, 0, r3.stdout + r3.stderr)
        s = json.loads(slurp(os.path.join(self.proj, ".claude", "settings.json")))
        self.assertEqual(s["permissions"]["allow"], ["Bash(make *)"])
        self.assertEqual(s["permissions"]["deny"], ["Read(./private/**)"])
        self.assertNotIn("hooks", s)
        self.assertEqual(slurp(os.path.join(self.proj, "CLAUDE.md")).strip(), "# My notes\nKeep this.")
        self.assertFalse(os.path.exists(os.path.join(self.proj, "AGENTS.md")))
        self.assertFalse(os.path.exists(os.path.join(self.proj, ".claude", "hooks")))
        self.assertTrue(os.path.isfile(os.path.join(self.proj, ".claude", "knowledge", "INDEX.md")))

    def test_user_modified_file_is_kept(self):
        install(self.proj)
        agent = os.path.join(self.proj, ".claude", "agents", "coding-agent.md")
        spit(agent, slurp(agent) + "\nCustom rule.\n")
        mpath = os.path.join(self.proj, ".claude", ".harness-manifest.json")
        m = json.loads(slurp(mpath))
        m["files"][".claude/agents/coding-agent.md"] = "0" * 64  # simulate an older harness version
        spit(mpath, json.dumps(m))
        r = install(self.proj)
        self.assertIn("skip", r.stdout)
        self.assertIn("Custom rule.", slurp(agent))
        r = install(self.proj, "--force")
        self.assertNotIn("Custom rule.", slurp(agent))
        self.assertTrue(any(f.startswith("coding-agent.md.bak-") for f in os.listdir(os.path.dirname(agent))))

    def test_locked_security_file_is_restored(self):
        install(self.proj)
        guard = os.path.join(self.proj, ".claude", "hooks", "guard_bash.py")
        stock = slurp(guard)
        spit(guard, stock + "\n# weakened\n")
        r = install(self.proj)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("restore", r.stdout)
        self.assertEqual(slurp(guard), stock)
        self.assertTrue(any(f.startswith("guard_bash.py.bak-") for f in os.listdir(os.path.dirname(guard))))
        self.assertTrue(os.access(guard, os.X_OK))

    def test_check_flags_locked_changes_only(self):
        self.assertEqual(install(self.proj, "--check").returncode, 0)  # nothing installed yet
        install(self.proj)
        r = install(self.proj, "--check")
        self.assertEqual(r.returncode, 0, r.stdout)
        agent = os.path.join(self.proj, ".claude", "agents", "coding-agent.md")
        spit(agent, slurp(agent) + "\nCustom rule.\n")  # editing a skill or agent is allowed
        self.assertEqual(install(self.proj, "--check").returncode, 0)
        detector = os.path.join(self.proj, ".claude", "hooks", "lib", "detectors.py")
        spit(detector, slurp(detector) + "\n# weakened\n")
        spath = os.path.join(self.proj, ".claude", "settings.json")
        s = json.loads(slurp(spath))
        dropped = s["permissions"]["deny"].pop()  # a harness rule (the team's own rule comes first)
        self.assertNotEqual(dropped, "Read(./private/**)")
        s["hooks"].pop("PreToolUse")
        spit(spath, json.dumps(s))
        os.remove(os.path.join(self.proj, ".claude", "hooks", "guard_write.py"))
        r = install(self.proj, "--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("modified  .claude/hooks/lib/detectors.py", r.stdout)
        self.assertIn("missing   .claude/hooks/guard_write.py", r.stdout)
        self.assertIn(dropped, r.stdout)
        self.assertIn("PreToolUse hook", r.stdout)
        self.assertNotIn("coding-agent", r.stdout)
        self.assertNotIn("private", r.stdout)
        install(self.proj)
        self.assertEqual(install(self.proj, "--check").returncode, 0)
        self.assertIn("Custom rule.", slurp(agent))

    def test_check_fails_when_manifest_is_deleted_or_ask_rule_removed(self):
        install(self.proj)
        spath = os.path.join(self.proj, ".claude", "settings.json")
        s = json.loads(slurp(spath))
        s["permissions"]["ask"] = [r for r in s["permissions"]["ask"] if r != "Bash(git push *)"]
        spit(spath, json.dumps(s))
        r = install(self.proj, "--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("ask rule Bash(git push *)", r.stdout)
        os.remove(os.path.join(self.proj, ".claude", ".harness-manifest.json"))
        r = install(self.proj, "--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("manifest", r.stdout)

    def test_upgrade_keeps_purpose_line_and_team_hooks(self):
        install(self.proj)
        cpath = os.path.join(self.proj, "CLAUDE.md")
        text = slurp(cpath)
        self.assertIn("TODO(human)", text)
        mine = "- **Purpose:** Billing API used by the finance team."
        spit(cpath, "\n".join(mine if l.startswith("- **Purpose:**") else l for l in text.splitlines()) + "\n")
        spath = os.path.join(self.proj, ".claude", "settings.json")
        s = json.loads(slurp(spath))
        team = {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 .claude/hooks/team_audit.py"}]}
        s["hooks"]["PreToolUse"].append(team)
        spit(spath, json.dumps(s))
        self.assertEqual(install(self.proj).returncode, 0)
        self.assertIn(mine, slurp(cpath))
        self.assertNotIn("TODO(human)", slurp(cpath))
        self.assertIn(team, json.loads(slurp(spath))["hooks"]["PreToolUse"])
        self.assertEqual(install(self.proj, "--uninstall").returncode, 0)
        self.assertIn(team, json.loads(slurp(spath))["hooks"]["PreToolUse"])

    def test_check_uses_release_hashes_not_the_manifest(self):
        install(self.proj)
        guard = os.path.join(self.proj, ".claude", "hooks", "guard_read.py")
        spit(guard, slurp(guard) + "\n# weakened\n")
        mpath = os.path.join(self.proj, ".claude", ".harness-manifest.json")
        m = json.loads(slurp(mpath))
        import hashlib
        m["files"][".claude/hooks/guard_read.py"] = hashlib.sha256(slurp(guard).encode()).hexdigest()
        spit(mpath, json.dumps(m))  # a team "approving" its own edit in the manifest
        r = install(self.proj, "--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("guard_read.py", r.stdout)

    def test_codeowners_block(self):
        spit(os.path.join(self.proj, "CODEOWNERS"), "* @acme/app-team\n")
        r = install(self.proj, "--codeowners", "@acme/security")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.proj, ".github", "CODEOWNERS")))
        co = slurp(os.path.join(self.proj, "CODEOWNERS"))
        self.assertTrue(co.startswith("* @acme/app-team\n"))
        self.assertIn("/.claude/hooks/guard_*.py @acme/security", co)
        install(self.proj, "--codeowners", "@acme/security")
        self.assertEqual(slurp(os.path.join(self.proj, "CODEOWNERS")).count("# harness:start"), 1)
        install(self.proj, "--uninstall")
        self.assertEqual(slurp(os.path.join(self.proj, "CODEOWNERS")).strip(), "* @acme/app-team")

    def test_no_codeowners_without_owners(self):
        install(self.proj)
        self.assertFalse(os.path.exists(os.path.join(self.proj, ".github", "CODEOWNERS")))

    def test_upgrade_removes_files_the_harness_dropped(self):
        install(self.proj)
        mpath = os.path.join(self.proj, ".claude", ".harness-manifest.json")
        m = json.loads(slurp(mpath))
        old = os.path.join(self.proj, ".claude", "skills", "old-skill", "SKILL.md")
        kept = os.path.join(self.proj, ".claude", "tools", "old_tool.py")
        spit(old, "old")
        spit(kept, "old")
        import hashlib
        h = hashlib.sha256(b"old").hexdigest()
        m["files"][".claude/skills/old-skill/SKILL.md"] = h
        m["files"][".claude/tools/old_tool.py"] = h
        spit(mpath, json.dumps(m))
        spit(kept, "old, then edited by the user")
        r = install(self.proj)
        self.assertFalse(os.path.exists(old), r.stdout)
        self.assertTrue(os.path.exists(kept))
        self.assertIn("modified locally", r.stdout)

    def test_dry_run_writes_nothing(self):
        before = snapshot(self.proj)
        r = install(self.proj, "--dry-run")
        self.assertEqual(r.returncode, 0)
        self.assertIn("dry run", r.stdout)
        self.assertEqual(snapshot(self.proj), before)

    def test_dir_argument(self):
        r = install(f"DIR={self.proj}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.proj, ".claude", "settings.json")))
        self.assertNotEqual(install("DIR=").returncode, 0)

    def test_refuses_bad_targets(self):
        self.assertNotEqual(install(os.path.expanduser("~")).returncode, 0)
        self.assertNotEqual(install("DIR=~").returncode, 0)
        self.assertNotEqual(install(ROOT).returncode, 0)
        self.assertNotEqual(install(os.path.join(self.proj, "nope")).returncode, 0)


class TestTemplateHygiene(unittest.TestCase):
    """Installed docs/agents/skills must not trip the project's own Read guard."""

    def test_no_false_positives_in_template(self):
        sys.path.insert(0, os.path.join(ROOT, "template", "claude", "hooks", "lib"))
        from detectors import scan_text
        hits = []
        base = os.path.join(ROOT, "template")
        for dp, _, fn in os.walk(base):
            for f in fn:
                if f.endswith((".md", ".tmpl", ".json", ".css", ".svg", ".py")) and "__pycache__" not in dp:
                    p = os.path.join(dp, f)
                    for finding in scan_text(slurp(p)):
                        hits.append(f"{os.path.relpath(p, base)}: {finding.detector}")
        self.assertEqual(hits, [])

    def test_doc_voice_rules(self):
        base = os.path.join(ROOT, "template", "claude")
        writer = slurp(os.path.join(base, "agents", "doc-writer.md"))
        for needle in ("No em-dashes", "Staccato pairs", "Antithesis reframe", "Isocolon metaphor-pairs",
                       "Backward-reference", "hand back to the main session", "preserve their actual meaning"):
            self.assertIn(needle, writer)
        self.assertIn("Voice rules", slurp(os.path.join(base, "agents", "doc-reviewer.md")))
        clean = [os.path.join(base, "agents", "doc-writer.md")]
        refs = os.path.join(base, "skills", "doc-standards")
        for dp, _, fn in os.walk(refs):
            clean += [os.path.join(dp, f) for f in fn if f.endswith(".md")]
        for p in clean:
            with self.subTest(p):
                self.assertNotIn("\u2014", slurp(p), "doc templates must not model em-dashes")

    def test_agent_skills_exist(self):
        import re
        base = os.path.join(ROOT, "template", "claude")
        skills = set(os.listdir(os.path.join(base, "skills")))
        for f in os.listdir(os.path.join(base, "agents")):
            m = re.search(r"^skills:\s*\[(.*)\]", slurp(os.path.join(base, "agents", f)), re.M)
            for name in (x.strip() for x in (m.group(1).split(",") if m else []) if x.strip()):
                with self.subTest(agent=f, skill=name):
                    self.assertIn(name, skills)

    def test_agent_models(self):
        expected = {"fable": {"security-auditor", "compliance-reviewer"},
                    "opus": {"orchestrator", "coding-agent", "frontend-ui-builder", "code-reviewer"},
                    "sonnet": {"doc-writer", "doc-reviewer", "diagram-builder", "diagram-reviewer"},
                    "haiku": {"knowledge-keeper", "repo-explorer", "commit-helper", "lint-fixer"}}
        adir = os.path.join(ROOT, "template", "claude", "agents")
        got = {}
        for f in os.listdir(adir):
            fm = slurp(os.path.join(adir, f)).split("---")[1]
            meta = dict(l.split(":", 1) for l in fm.strip().splitlines() if ":" in l and not l.startswith(" "))
            got.setdefault(meta["model"].strip(), set()).add(meta["name"].strip())
        self.assertEqual(got, expected)


if __name__ == "__main__":
    unittest.main()
