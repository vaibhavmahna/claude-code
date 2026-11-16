"""Shared test helpers. Fake credentials are assembled at runtime so no literal secret lives in the repo."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

if os.name == "nt":
    # Git marks object files read-only, and Windows refuses to delete read-only files,
    # so temporary test repos fail to clean up. Clear the flag and retry.
    _rmtree = shutil.rmtree

    def _retry_readonly(func, path, _exc):
        os.chmod(path, stat.S_IWRITE)
        func(path)

    def _rmtree_windows(path, ignore_errors=False, *args, **kw):
        if ignore_errors or args or kw:
            return _rmtree(path, ignore_errors, *args, **kw)
        if sys.version_info >= (3, 12):
            return _rmtree(path, onexc=_retry_readonly)
        return _rmtree(path, onerror=_retry_readonly)

    shutil.rmtree = _rmtree_windows

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "template", "claude")
HOOKS = os.path.join(TEMPLATE, "hooks")
TOOLS = os.path.join(TEMPLATE, "tools")
sys.path.insert(0, os.path.join(HOOKS, "lib"))
sys.path.insert(0, TOOLS)

FAKE = {
    "aws": "AKIA" + "QX7Z" + "W2RT" + "Y3KD" + "M9PL",
    "github": "ghp_" + "aB3dE6gH9jK2mN5pQ8sT" + "1vW4yZ7bC0eF3hJ6",
    "slack": "xoxb-" + "123456789012-" + "abcdefABCDEF1234",
    "anthropic": "sk-ant-" + "api03-" + "Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2",
    "stripe": "sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc",
    "pem": "-----BEGIN " + "RSA PRIVATE KEY-----",
    "jwt": "eyJ" + "hbGciOiJIUzI1NiJ9" + ".eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0" + ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
    "conn": "postgres://admin:" + "Sup3rS3cr3tPw" + "@db.internal:5432/app",
    "email": "jane.doe" + "@" + "acme-corp.io",
    "ssn": "512-" + "44-" + "8231",
    "card": "4539" + " 1488 " + "0343 6467",
    "phone": "(604) " + "872-" + "3311",
}


def run_hook(script, event, proj, *args):
    r = subprocess.run([sys.executable, os.path.join(HOOKS, script), *args], input=json.dumps(event),
                       capture_output=True, text=True, timeout=30,
                       env={**os.environ, "CLAUDE_PROJECT_DIR": proj, "HARNESS_DEBUG": "1"})
    return json.loads(r.stdout) if r.stdout.strip() else {}


def decision(out):
    return (out.get("hookSpecificOutput") or {}).get("permissionDecision") or out.get("decision")


def tmp_project():
    d = tempfile.mkdtemp(prefix="harness-test-")
    os.makedirs(os.path.join(d, ".claude", "security"))
    return d


def slurp(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def spit(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
