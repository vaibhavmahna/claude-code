import os
import shutil
import subprocess
import sys
import time
import unittest

from helpers import FAKE, HOOKS, TOOLS, decision, spit, tmp_project
from common import DEFAULT_CONFIG, scan_local
from detectors import Allowlist, redact, scan_text, sensitive_path_reason


def names(text, **kw):
    return {f.detector for f in scan_text(text, **kw)}


class TestSecrets(unittest.TestCase):
    def test_true_positives(self):
        cases = {
            "aws": "secret.aws_access_key", "github": "secret.github_token", "slack": "secret.slack_token",
            "anthropic": "secret.anthropic_key", "stripe": "secret.stripe_key", "pem": "secret.private_key",
            "jwt": "secret.jwt", "conn": "secret.connection_string",
        }
        for key, det in cases.items():
            with self.subTest(key):
                self.assertIn(det, names(f"value = {FAKE[key]}"))

    def test_generic_assignments(self):
        self.assertIn("secret.generic_assignment", names('db_password = "Tr0ub4dor&3x"'))
        self.assertIn("secret.generic_assignment", names("API_KEY=9f8e7d6c5b4a39281706f5e4d3c2b1a0"))
        self.assertIn("secret.generic_assignment", names('{"clientSecret": "q8Zr2Lm9Xc4Vb7Nn1Kd5Tp3Ws6Yh0Gf"}'))

    def test_placeholder_words_count_only_at_the_start_of_a_word(self):
        # a real value that happens to contain "test" or "your" inside another word is still a secret
        self.assertIn("secret.generic_assignment", names("DB_PASSWORD=Contest7zzPrxd99"))
        self.assertIn("secret.generic_assignment", names("DB_PASSWORD=Flavour8zzPrxd41"))
        self.assertIn("secret.connection_string", names("DB=postgres://app:Latest_zzPrxdPw@db.internal/x"))
        for placeholder in ("DB_PASSWORD=test_password_123", "API_TOKEN=your-api-token-goes-here",
                            'password = "myTestPassw0rd"', "DB_PASSWORD=examplePassw0rd", "DB_PASSWORD=fakepass99xyz",
                            "SECRET_KEY=XXXXXXXXXXXXXXXXXXXX", "API_KEY=<replace-with-real-key-value>"):
            with self.subTest(placeholder):
                self.assertEqual(names(placeholder), set())

    def test_false_positives(self):
        clean = [
            'password = os.environ["DB_PASSWORD"]', 'token = get_token()', 'API_KEY=${API_KEY}',
            'password: "<your-password>"', "const pw = process.env.PASSWORD", 'secret = "changeme"',
            '".claude/skills/x/tokens.css": "bdf1972e552cf2c7a725f73b929f95b4f13270ffb87edcb4b4e1040f"',
            "see https://example.com/docs#token", "for i in range(10): pass",
            "card 4242 4242 4242 4242 is Stripe's test card", "call 555-0100 for help",
            "contact user@example.com", "logo@2x.png", "SSN example 123-45-6789", "version 1.2.3",
            "Co-Authored-By: Claude <noreply@anthropic.com>", "@types/node", "import x from '@scope/pkg'",
        ]
        for c in clean:
            with self.subTest(c):
                self.assertEqual(names(c), set())


class TestPII(unittest.TestCase):
    def test_pii_detected(self):
        for key, det in (("email", "pii.email"), ("ssn", "pii.ssn"), ("card", "pii.credit_card"), ("phone", "pii.phone")):
            with self.subTest(key):
                self.assertIn(det, names(f"x {FAKE[key]} y"))

    def test_iban_and_sin(self):
        self.assertIn("pii.iban", names("IBAN GB82 WEST 1234 5698 7654 32"))
        self.assertIn("pii.sin", names("SIN 130 692 544"))

    def test_pii_toggle(self):
        self.assertEqual(names(FAKE["email"], include_pii=False), set())
        self.assertEqual(names(FAKE["email"], pii_detectors=["phone"]), set())


class TestAllowlistAndRedact(unittest.TestCase):
    def test_hash_and_detector_allowlist(self):
        f = scan_text(FAKE["github"])[0]
        self.assertEqual(scan_text(FAKE["github"], Allowlist(hashes={f.sha256})), [])
        self.assertEqual(scan_text(FAKE["email"], Allowlist(disabled={"pii.email"})), [])

    def test_path_allowlist(self):
        a = Allowlist(path_globs=["tests/fixtures/**"])
        self.assertTrue(a.path_allowed("./tests/fixtures/x/y.json"))
        self.assertFalse(a.path_allowed("src/app.py"))

    def test_redact(self):
        text = f"key={FAKE['aws']} mail={FAKE['email']}"
        out = redact(text, scan_text(text))
        self.assertNotIn(FAKE["aws"], out)
        self.assertNotIn(FAKE["email"], out)
        self.assertIn("***REDACTED:secret.aws_access_key***", out)


class TestSensitivePaths(unittest.TestCase):
    def test_sensitive(self):
        for p in (".env", "app/.env.production", "~/.ssh/id_rsa", "/home/u/.aws/credentials", "certs/server.pem",
                  "terraform.tfstate", "secrets/db.yaml", ".git-credentials", "~/.kube/config", ".env*", "id_*"):
            with self.subTest(p):
                self.assertIsNotNone(sensitive_path_reason(p))

    def test_not_sensitive(self):
        for p in (".env.example", ".env.sample", "src/env.ts", "id_rsa.pub", "README.md", "src/credentials/handler.py",
                  "docs/secrets.md", "environment.py"):
            with self.subTest(p):
                self.assertIsNone(sensitive_path_reason(p))


PEM = FAKE["pem"] + "\n" + "\n".join("QUJD" * 16 for _ in range(20)) + "\nabcd==\n-----END " + "RSA PRIVATE KEY-----"


class TestBalance(unittest.TestCase):
    """The balance metric: ordinary project content passes, real leaks still block."""

    MUST_NOT_BLOCK = [
        '"author": "Jane Doe <' + FAKE["email"] + '>"',                      # package.json author
        "* @org/platform " + FAKE["email"],                                    # CODEOWNERS
        "Author: Jane Doe <" + FAKE["email"] + ">\nCall " + FAKE["phone"],     # git log, a contact line
        "DATABASE_URL=postgres://postgres:" + "postgres@localhost:5432/app",   # local dev database
        "amqp://guest:" + "guest@rabbit:5672",
        "POSTGRES_PASSWORD=" + "postgres",
        "SECRET_KEY = 'django-insecure-" + "9x8f7g6h5j4k3l2m1n0b'",
        '"token_endpoint": "https://login.example.org/oauth2/token"',
        'tokenizer_name = "bert-base-multilingual-cased"',
        'secretName: "my-app-tls-certificate-2024"',
        "AKIA" + "IOSFODNN7" + "EXAMPLE",                                      # AWS's documented example key
        "id: 3f2504e0-4f89-11d3-9a0c-0305e82c3301 sha: 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b",
    ]
    MUST_BLOCK = [
        "key = " + FAKE["github"], "x " + FAKE["aws"], FAKE["conn"], PEM, "token: " + FAKE["jwt"],
        'password = "' + "Hunter2" + 'pass!"', 'api_key = "' + "9f8a7b6c5d4e3f2a" + '1b0c9d8e7f6a5b4c"',
        "ssn " + FAKE["ssn"], "card " + FAKE["card"],
        "\n".join(f"P{i},person{i}@corp-mail.io" for i in range(25)),          # a customer export
    ]

    def local(self, text):
        return scan_local(text, Allowlist(), DEFAULT_CONFIG["scan"])

    def test_ordinary_content_passes(self):
        for text in self.MUST_NOT_BLOCK:
            with self.subTest(text[:40]):
                self.assertEqual([f.detector for f in self.local(text)], [])

    def test_real_leaks_block(self):
        for text in self.MUST_BLOCK:
            with self.subTest(text[:25]):
                self.assertTrue(self.local(text))

    def test_strict_setting_blocks_single_email(self):
        strict = {**DEFAULT_CONFIG["scan"], "pii_action": {}}
        self.assertTrue(scan_local("mail " + FAKE["email"], Allowlist(), strict))

    def test_private_key_body_is_masked(self):
        out = redact("before\n" + PEM + "\nafter", scan_text("before\n" + PEM + "\nafter"))
        self.assertNotIn("QUJD", out)
        self.assertTrue(out.startswith("before\n") and out.endswith("\nafter"))
        escaped = '{"private_key": "' + PEM.replace("\n", "\\n") + '\\n"}'
        self.assertNotIn("QUJD", redact(escaped, scan_text(escaped)))

    def test_many_findings_scan_fast(self):
        big = "\n".join(f"row{i},person{i}@corp-mail.io" for i in range(20000))
        t0 = time.time()
        self.assertEqual(len(scan_text(big)), 20000)
        self.assertLess(time.time() - t0, 5)

    def test_paths_ignore_case_and_allowlist_rejects_traversal(self):
        for p in (".ENV", "keys/ID_RSA", ".Ssh/config", "Secrets/x.txt"):
            self.assertIsNotNone(sensitive_path_reason(p), p)
        a = Allowlist(path_globs=["tests/fixtures/**"])
        self.assertTrue(a.path_allowed("tests/fixtures/people.csv"))
        self.assertFalse(a.path_allowed("tests/fixtures/../../.env"))


class TestSafeReadAndFailClosed(unittest.TestCase):
    def setUp(self):
        self.proj = tmp_project()

    def tearDown(self):
        shutil.rmtree(self.proj)

    def test_safe_read_masks_key_body_and_keeps_line_numbers(self):
        spit(os.path.join(self.proj, "server.key"), "# cert\n" + PEM + "\nlast line\n")
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "safe_read.py"), os.path.join(self.proj, "server.key")],
                           capture_output=True, text=True, env={**os.environ, "CLAUDE_PROJECT_DIR": self.proj})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("QUJD", r.stdout)
        self.assertIn("25\tlast ***REDACTED:value***", r.stdout)  # line numbers kept; a key file shows no values

    def safe_read(self, rel, text):
        spit(os.path.join(self.proj, rel), text)
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "safe_read.py"), os.path.join(self.proj, rel)],
                           capture_output=True, text=True, env={**os.environ, "CLAUDE_PROJECT_DIR": self.proj})
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_safe_read_masks_every_value_in_credential_files_of_any_format(self):
        out = self.safe_read("credentials.json", '{\n  "auths": {"registry.test": {"auth": "ZmFrZXVzZXI6ZmFrZXBhc3M="}},\n'
                                                 '  "token": "abc123",\n  "scopes": ["read", "write"],\n  "port": 5432\n}\n')
        for value in ("ZmFrZXVzZXI6ZmFrZXBhc3M=", "abc123", '"read"', '"write"'):
            self.assertNotIn(value, out)
        for kept in ('"auths"', '"registry.test"', '"auth"', '"token"', '"scopes"', "5432"):
            self.assertIn(kept, out)  # keys and layout stay readable
        out = self.safe_read(".netrc", "# my hosts\nmachine api.test login fakeuser password fakepass99\n")
        self.assertNotIn("fakepass99", out)
        self.assertNotIn("fakeuser", out)
        self.assertIn("# my hosts", out)
        self.assertIn("machine ***REDACTED:value***", out)
        out = self.safe_read(".npmrc", "//registry.test/:_authToken=3f9c2ab14d\nregistry=https://registry.test/\n[section]\n")
        self.assertNotIn("3f9c2ab14d", out)
        self.assertIn("//registry.test/:_authToken=***REDACTED:value***", out)
        self.assertIn("[section]", out)
        out = self.safe_read("secrets/api_token", "dGhpc2lzYWZha2V0b2tlbg==\n")
        self.assertNotIn("dGhpc2lzYWZha2V0b2tlbg", out)
        out = self.safe_read("secrets.yaml", "db:\n  password: hunter2isweak\n  host: db.internal\n")
        self.assertNotIn("hunter2isweak", out)
        self.assertIn("  password: ***REDACTED:value***", out)
        self.assertIn("db:", out)

    def test_safe_read_leaves_ordinary_files_readable(self):
        out = self.safe_read("notes.md", "# Notes\nThe port is 5432 and the host is db.internal.\n")
        self.assertIn("The port is 5432 and the host is db.internal.", out)

    def test_safe_read_hash_needs_a_terminal(self):
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "safe_read.py"), "--hash"], input="value\n",
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("sha256:", r.stdout)

    def test_guards_deny_when_their_library_is_broken(self):
        broken = os.path.join(self.proj, "hooks")
        shutil.copytree(HOOKS, broken)
        spit(os.path.join(broken, "lib", "detectors.py"), "this is not python (\n")
        for script, event in (("guard_bash.py", {"tool_name": "Bash", "tool_input": {"command": "ls"}}),
                              ("guard_read.py", {"tool_name": "Read", "tool_input": {"file_path": "a.py"}}),
                              ("guard_write.py", {"tool_name": "Write", "tool_input": {"file_path": "a.py", "content": "x"}}),
                              ("guard_egress.py", {"tool_name": "WebFetch", "tool_input": {"url": "https://example.com"}}),
                              ("guard_prompt.py", {"prompt": "hello"})):
            with self.subTest(script):
                r = subprocess.run([sys.executable, os.path.join(broken, script)], input=__import__("json").dumps(event),
                                   capture_output=True, text=True, env={**os.environ, "CLAUDE_PROJECT_DIR": self.proj})
                self.assertIn(decision(__import__("json").loads(r.stdout)), ("deny", "block"))


if __name__ == "__main__":
    unittest.main()
