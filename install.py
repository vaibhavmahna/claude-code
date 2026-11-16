#!/usr/bin/env python3
"""Install the Claude Code project harness into a project with one command.

    python3 install.py DIR="/path/to/project"            # install or upgrade
    python3 install.py DIR="/path/to/project" --dry-run  # show what would change
    python3 install.py DIR="/path/to/project" --uninstall
    python3 install.py DIR="/path/to/project" --check    # CI: fail if locked security files were changed
    (a plain path works too: python3 install.py /path/to/project)

Copies template/ into the project (.claude/, CLAUDE.md, AGENTS.md),
merges settings without dropping user rules, detects the stack, seeds the knowledge
graph, and self-tests the security hooks. Stdlib only; Python >= 3.9.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "template")
TEMPLATE_CLAUDE = os.path.join(TEMPLATE, "claude")
MANIFEST = ".claude/.harness-manifest.json"
SETTINGS = ".claude/settings.json"
DATA_FILES = {".claude/knowledge/INDEX.md", ".claude/knowledge/graph.json", ".claude/security/allowlist"}
MD_START, MD_END = "<!-- harness:start -->", "<!-- harness:end -->"
GI_START, GI_END = "# harness:start", "# harness:end"
GITIGNORE_LINES = [
    ".env", ".env.*", "!.env.example", "!.env.sample", "*.pem", "*.key",
    ".claude/settings.local.json", ".claude/state/", ".claude/scratch/", ".claude/knowledge/handoffs/*", "!.claude/knowledge/handoffs/.gitkeep",
    ".claude/**/*.bak-*", ".claude/**/__pycache__/",
]
SKIP_NAMES = {"__pycache__", ".DS_Store"}
# Locked security files. Install and upgrade always restore them to the release (a team's version is
# backed up), --check fails when they differ, and CODEOWNERS routes edits to the security owners.
LOCKED_PREFIXES = (".claude/hooks/guard_", ".claude/hooks/lib/", ".claude/security/")
LOCKED_FILES = {".claude/hooks/redact_output.py", ".claude/tools/save.py", ".claude/tools/compliance.py"}
CODEOWNERS_PATHS = ["/.claude/hooks/guard_*.py", "/.claude/hooks/redact_output.py", "/.claude/hooks/lib/",
                    "/.claude/security/", "/.claude/tools/save.py", "/.claude/tools/compliance.py",
                    "/.claude/.harness-manifest.json"]
# Optional git pre-commit hook (--git-hook): the compliance gate for commits typed in a terminal.
GIT_HOOK_MARK = "# Claude Code Harness compliance gate"
GIT_HOOK = f"""#!/bin/sh
{GIT_HOOK_MARK}: refuse a commit whose staged files have no compliance-reviewer PASS.
root="$(git rev-parse --show-toplevel)" || exit 1
exec python3 "$root/.claude/tools/compliance.py" --root "$root" check
"""
CODEOWNERS_FILES = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")  # GitHub reads the first one found

sys.path.insert(0, os.path.join(TEMPLATE_CLAUDE, "tools"))
import stack  # noqa: E402


# ------------------------------------------------------------------ helpers

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: str) -> Optional[str]:
    try:
        with open(p, "rb") as fh:
            return sha256_bytes(fh.read())
    except OSError:
        return None


def stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def canon(x: Any) -> str:
    return json.dumps(x, sort_keys=True)


class Ops:
    """All filesystem writes go through here so --dry-run is exact."""

    def __init__(self, root: str, dry: bool):
        self.root, self.dry = root, dry
        self.log: List[str] = []

    def abs(self, rel: str) -> str:
        return os.path.join(self.root, rel)

    def note(self, action: str, rel: str, extra: str = "") -> None:
        self.log.append(f"{action:<10} {rel}{(': ' + extra) if extra else ''}")

    def write(self, rel: str, data: bytes, action: str = "write", extra: str = "", mode: Optional[int] = None) -> None:
        self.note(action, rel, extra)
        if self.dry:
            return
        p = self.abs(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)
        if mode is not None:
            os.chmod(p, mode)

    def backup(self, rel: str) -> None:
        p = self.abs(rel)
        if os.path.exists(p):
            b = f"{rel}.bak-{stamp()}"
            self.note("backup", b)
            if not self.dry:
                shutil.copy2(p, self.abs(b))

    def remove(self, rel: str, extra: str = "") -> None:
        self.note("remove", rel, extra)
        if not self.dry:
            os.remove(self.abs(rel))


def read_text(p: str) -> Optional[str]:
    try:
        with open(p, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def template_files() -> List[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(TEMPLATE_CLAUDE):
        dirnames[:] = [d for d in dirnames if d not in SKIP_NAMES]
        for f in filenames:
            if f in SKIP_NAMES or f.endswith(".pyc"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, f), TEMPLATE_CLAUDE).replace(os.sep, "/")
            out.append(rel)
    return sorted(out)


def is_locked(rel: str) -> bool:
    return rel not in DATA_FILES and (rel in LOCKED_FILES or rel.startswith(LOCKED_PREFIXES))


def codeowners_file(root: str) -> str:
    for rel in CODEOWNERS_FILES:
        if os.path.isfile(os.path.join(root, rel)):
            return rel
    return CODEOWNERS_FILES[0]


def security_owners(args) -> str:
    """Owners for the locked files: --codeowners, else "codeowners" in the template's harness.json."""
    if getattr(args, "codeowners", None):
        return args.codeowners.strip()
    try:
        with open(os.path.join(TEMPLATE_CLAUDE, "harness.json")) as fh:
            return str(json.load(fh).get("codeowners", "")).strip()
    except (OSError, ValueError):
        return ""


def harness_version() -> str:
    try:
        with open(os.path.join(TEMPLATE_CLAUDE, "harness.json")) as fh:
            return json.load(fh).get("version", "0.0.0")
    except (OSError, ValueError):
        return "0.0.0"


# ------------------------------------------------------------------ settings merge

def remove_fragment(cur: Any, old: Any) -> Any:
    """Remove items a previous harness version added (so upgrades don't leave stale rules)."""
    if isinstance(cur, dict) and isinstance(old, dict):
        out = {}
        for k, v in cur.items():
            if k in old:
                nv = remove_fragment(v, old[k])
                if nv not in ({}, [], None):
                    out[k] = nv
                elif not isinstance(v, (dict, list)) and canon(v) != canon(old[k]):
                    out[k] = v
            else:
                out[k] = v
        return out
    if isinstance(cur, list) and isinstance(old, list):
        olds = {canon(x) for x in old}
        return [x for x in cur if canon(x) not in olds]
    return None if canon(cur) == canon(old) else cur


def harness_hook_names() -> List[str]:
    d = os.path.join(TEMPLATE_CLAUDE, "hooks")
    return [f".claude/hooks/{f}" for f in sorted(os.listdir(d)) if f.endswith(".py")]


def strip_harness_hooks(settings: Dict) -> Dict:
    """Drop hooks that run harness scripts (covers installs without a manifest).

    Only the harness's own script names match, so a team's hook kept under .claude/hooks/ survives."""
    names = harness_hook_names()

    def ours(h: Any) -> bool:
        return any(n in canon(h) for n in names)
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings
    for ev, groups in list(hooks.items()):
        if isinstance(groups, list):
            kept = []
            for g in groups:
                inner = g.get("hooks") if isinstance(g, dict) else None
                if isinstance(inner, list):
                    mine = [h for h in inner if not ours(h)]
                    if mine and len(mine) != len(inner):
                        g = {**g, "hooks": mine}
                    elif not mine:
                        continue
                elif ours(g):
                    continue
                kept.append(g)
            if kept:
                hooks[ev] = kept
            else:
                del hooks[ev]
    if not hooks:
        settings.pop("hooks", None)
    return settings


def merge(base: Any, add: Any) -> Any:
    """Union lists, recurse dicts, keep the user's scalars (warn upstream)."""
    if isinstance(base, dict) and isinstance(add, dict):
        out = dict(base)
        for k, v in add.items():
            out[k] = merge(base[k], v) if k in base else v
        return out
    if isinstance(base, list) and isinstance(add, list):
        seen = {canon(x) for x in base}
        return base + [x for x in add if canon(x) not in seen]
    return base


def scalar_conflicts(base: Any, add: Any, path: str = "") -> List[str]:
    out = []
    if isinstance(base, dict) and isinstance(add, dict):
        for k, v in add.items():
            if k in base:
                out += scalar_conflicts(base[k], v, f"{path}.{k}" if path else k)
    elif not isinstance(add, (dict, list)) and canon(base) != canon(add):
        out.append(path)
    return out


# ------------------------------------------------------------------ markdown blocks

def upsert_block(existing: Optional[str], block: str, start: str, end: str) -> str:
    wrapped = f"{start}\n{block.rstrip()}\n{end}\n"
    if not existing:
        return wrapped
    if start in existing and end in existing:
        pre, rest = existing.split(start, 1)
        _, post = rest.split(end, 1)
        return pre + wrapped.rstrip("\n") + post if post.strip() else pre + wrapped
    return existing.rstrip() + "\n\n" + wrapped


def remove_block(existing: str, start: str, end: str) -> str:
    if start not in existing or end not in existing:
        return existing
    pre, rest = existing.split(start, 1)
    _, post = rest.split(end, 1)
    return (pre.rstrip() + "\n" + post.lstrip("\n")).strip() + "\n"


PURPOSE_RE = re.compile(r"^- \*\*Purpose:\*\* .*$", re.M)


def keep_purpose(existing: Optional[str], block: str) -> str:
    """Carry the project purpose line a person wrote inside the harness block over to the new block."""
    if not existing or MD_START not in existing or MD_END not in existing:
        return block
    old = PURPOSE_RE.search(existing.split(MD_START, 1)[1].split(MD_END, 1)[0])
    if not old or "TODO(human)" in old.group(0):
        return block
    return PURPOSE_RE.sub(lambda _m: old.group(0), block, count=1)


def render_claude_md(root: str, info: Dict, version: str) -> str:
    tmpl = read_text(os.path.join(TEMPLATE, "CLAUDE.md.tmpl")) or ""
    return (tmpl.replace("{{PROJECT_NAME}}", info["name"])
            .replace("{{STACK_SUMMARY}}", stack.summary(info))
            .replace("{{COMMANDS}}", stack.commands_markdown(info))
            .replace("{{LAYOUT}}", stack.layout_markdown(root))
            .replace("{{HARNESS_VERSION}}", version))


# ------------------------------------------------------------------ install

def install(root: str, args) -> int:
    ops = Ops(root, args.dry_run)
    version = harness_version()
    old_manifest = json.loads(read_text(ops.abs(MANIFEST)) or "{}")
    old_files: Dict[str, str] = old_manifest.get("files", {})
    new_files: Dict[str, str] = {}
    skipped: List[str] = []
    restored: List[str] = []
    info = stack.detect(root)

    # 1. .claude/ payload
    for rel_t in template_files():
        rel = f".claude/{rel_t}"
        if rel == SETTINGS:
            continue
        with open(os.path.join(TEMPLATE_CLAUDE, rel_t), "rb") as fh:
            data = fh.read()
        new_hash = sha256_bytes(data)
        mode = 0o755 if rel_t.endswith(".py") and ("/hooks/" in rel or "/tools/" in rel) else None
        cur_hash = sha256_file(ops.abs(rel))
        if cur_hash is None:
            ops.write(rel, data, "create", mode=mode)
            new_files[rel] = new_hash
        elif rel in DATA_FILES:
            ops.note("keep", rel, "project data")
            if rel in old_files:
                new_files[rel] = old_files[rel]
        elif cur_hash == new_hash:
            new_files[rel] = new_hash
        elif old_files.get(rel) == cur_hash or args.force or is_locked(rel):
            edited = old_files.get(rel) != cur_hash
            if edited:
                ops.backup(rel)
            if edited and is_locked(rel) and not args.force:
                restored.append(rel)
                ops.write(rel, data, "restore", "locked security file; your version was backed up", mode=mode)
            else:
                ops.write(rel, data, "update", mode=mode)
            new_files[rel] = new_hash
        else:
            skipped.append(rel)
            ops.note("skip", rel, "locally modified (use --force to overwrite; a backup is kept)")
            if rel in old_files:
                new_files[rel] = old_files[rel]

    # 1b. files an earlier harness version installed that this version no longer ships
    shipped = {f".claude/{r}" for r in template_files()}
    for rel, old_hash in sorted(old_files.items()):
        if rel in shipped or rel in DATA_FILES or rel == SETTINGS:
            continue
        cur_hash = sha256_file(ops.abs(rel))
        if cur_hash == old_hash:
            ops.remove(rel, "no longer part of the harness")
        elif cur_hash is not None:
            ops.note("keep", rel, "no longer part of the harness, but modified locally")

    # 2. settings.json merge
    fragment = json.loads(read_text(os.path.join(TEMPLATE_CLAUDE, "settings.json")))
    allow_extra = stack.allow_rules(info)
    fragment["permissions"]["allow"] = fragment["permissions"]["allow"] + [r for r in allow_extra if r not in fragment["permissions"]["allow"]]
    if args.no_sandbox:
        fragment.pop("sandbox", None)
    existing_txt = read_text(ops.abs(SETTINGS))
    try:
        existing = json.loads(existing_txt) if existing_txt else {}
    except ValueError:
        print(f"ERROR: {SETTINGS} is not valid JSON; fix it and re-run.", file=sys.stderr)
        return 2
    base = existing
    if old_manifest.get("settings_fragment"):
        base = remove_fragment(base, old_manifest["settings_fragment"]) or {}
    base = strip_harness_hooks(json.loads(canon(base)))
    conflicts = [c for c in scalar_conflicts(base, fragment) if c != "$schema"]
    merged = merge(base, fragment)
    merged_txt = json.dumps(merged, indent=2) + "\n"
    # Compare parsed JSON, not text: removing and re-adding the fragment can reorder keys,
    # which would otherwise rewrite an unchanged file and leave a needless backup on every run.
    if merged != existing or not existing_txt:
        if existing_txt:
            ops.backup(SETTINGS)
        ops.write(SETTINGS, merged_txt.encode(), "merge" if existing_txt else "create")
    for c in conflicts:
        ops.note("conflict", SETTINGS, f"kept your value for '{c}' (harness default differs)")

    # 3. CLAUDE.md + AGENTS.md (marker blocks)
    for name, content in (("CLAUDE.md", render_claude_md(root, info, version)),
                          ("AGENTS.md", read_text(os.path.join(TEMPLATE, "AGENTS.md")) or "")):
        cur = read_text(ops.abs(name))
        new = upsert_block(cur, keep_purpose(cur, content), MD_START, MD_END)
        if new != cur:
            ops.write(name, new.encode(), "update" if cur else "create", "harness block" if cur else "")

    # 3b. CODEOWNERS: route edits to the locked files to the security owners
    owners = security_owners(args)
    if owners:
        co_rel = codeowners_file(root)
        cur = read_text(ops.abs(co_rel))
        block = "# Locked Claude Code Harness security files. Changes need the security owners' review.\n" + \
                "\n".join(f"{p} {owners}" for p in CODEOWNERS_PATHS)
        new = upsert_block(cur, block, GI_START, GI_END)
        if new != cur:
            ops.write(co_rel, new.encode(), "update" if cur else "create", "locked files owned by " + owners)

    # 4. .gitignore
    gi = read_text(ops.abs(".gitignore"))
    new_gi = upsert_block(gi, "\n".join(GITIGNORE_LINES), GI_START, GI_END)
    if new_gi != gi:
        ops.write(".gitignore", new_gi.encode(), "update" if gi else "create")

    # 5. manifest
    manifest = {"version": version, "installed_at": dt.datetime.now().isoformat(timespec="seconds"),
                "files": dict(sorted(new_files.items())), "settings_fragment": fragment}
    same = {k: v for k, v in old_manifest.items() if k != "installed_at"} == \
        {k: v for k, v in json.loads(canon(manifest)).items() if k != "installed_at"}
    if not same:
        ops.write(MANIFEST, (json.dumps(manifest, indent=1) + "\n").encode(), "write")
    elif not ops.log:
        ops.note("unchanged", ".claude/", "already up to date")

    # 5b. optional: git pre-commit hook, so commits typed in a terminal pass the compliance gate too
    if args.git_hook:
        install_git_hook(ops, root)

    # 6. optional: user-level env (file mentions through the Read guard)
    if args.user_env:
        set_user_env(ops, args.dry_run)

    for line in ops.log:
        print("  " + line)
    if args.dry_run:
        print("\n(dry run: nothing written)")
        return 0

    # 7. knowledge graph seed + self-test
    kg = os.path.join(root, ".claude", "tools", "kg.py")
    if not args.no_seed:
        r = subprocess.run([sys.executable, kg, "--root", root, "seed"], capture_output=True, text=True)
        print("\nKnowledge graph: " + (r.stdout.strip().replace("\n", " · ") or r.stderr.strip()))
    else:
        subprocess.run([sys.executable, kg, "--root", root, "index"], capture_output=True, text=True)
    print("\nSecurity self-test:")
    r = subprocess.run([sys.executable, os.path.join(root, ".claude", "tools", "doctor.py"), "--root", root,
                        "--selftest-only"], capture_output=True, text=True)
    print("\n".join("  " + l for l in r.stdout.strip().splitlines()))

    print(f"""
Installed Claude Code Harness v{version} into {root}
  Stack: {stack.summary(info)}
  Agents: {len([f for f in new_files if f.startswith('.claude/agents/')])} · Skills: {len({f.split('/')[2] for f in new_files if f.startswith('.claude/skills/')})}
{('  Skipped (locally modified): ' + ', '.join(skipped)) if skipped else ''}
{('  Restored (locked security files, backups kept): ' + ', '.join(restored)) if restored else ''}
Next steps:
  1. cd {root} && claude
  2. Run /harness-doctor, then /kg-bootstrap to build the knowledge graph.
  3. Fill in the TODO(human) purpose line in CLAUDE.md.
  4. Commit .claude/, CLAUDE.md, AGENTS.md (handoffs/state are gitignored).""" +
          ("" if args.user_env else "\n  Optional: re-run with --user-env to route @file mentions through the Read guard."))
    return 1 if r.returncode else 0


def install_git_hook(ops: Ops, root: str) -> None:
    """Write .git/hooks/pre-commit unless the project already has its own (that one is left alone)."""
    r = subprocess.run(["git", "-C", root, "rev-parse", "--git-path", "hooks/pre-commit"], capture_output=True, text=True)
    if r.returncode != 0:
        ops.note("skip", ".git/hooks/pre-commit", "not a git repository")
        return
    path = os.path.join(root, r.stdout.strip())
    cur = read_text(path)
    if cur is not None and GIT_HOOK_MARK not in cur:
        ops.note("skip", os.path.relpath(path, root), "a pre-commit hook already exists; add this line to it: "
                 'python3 .claude/tools/compliance.py check || exit 1')
        return
    if cur == GIT_HOOK:
        return
    ops.note("update" if cur else "create", os.path.relpath(path, root), "compliance gate for terminal commits")
    if not ops.dry:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(GIT_HOOK)
        os.chmod(path, 0o755)


def set_user_env(ops: Ops, dry: bool) -> None:
    p = os.path.expanduser("~/.claude/settings.json")
    try:
        cur = json.loads(read_text(p) or "{}")
    except ValueError:
        ops.note("skip", p, "invalid JSON; set CLAUDE_CODE_DISABLE_FILE_MENTIONS=1 manually")
        return
    if (cur.get("env") or {}).get("CLAUDE_CODE_DISABLE_FILE_MENTIONS") == "1":
        ops.note("keep", p, "CLAUDE_CODE_DISABLE_FILE_MENTIONS already set")
        return
    cur.setdefault("env", {})["CLAUDE_CODE_DISABLE_FILE_MENTIONS"] = "1"
    ops.note("update", p, "env.CLAUDE_CODE_DISABLE_FILE_MENTIONS=1 (user scope)")
    if not dry:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if os.path.exists(p):
            shutil.copy2(p, f"{p}.bak-{stamp()}")
        with open(p, "w") as fh:
            json.dump(cur, fh, indent=2)
            fh.write("\n")


# ------------------------------------------------------------------ uninstall

def uninstall(root: str, args) -> int:
    ops = Ops(root, args.dry_run)
    mtxt = read_text(ops.abs(MANIFEST))
    if not mtxt:
        print("No harness manifest found; nothing to uninstall.", file=sys.stderr)
        return 1
    manifest = json.loads(mtxt)
    kept = []
    for rel, h in manifest.get("files", {}).items():
        cur = sha256_file(ops.abs(rel))
        if cur is None:
            continue
        if rel in DATA_FILES:
            ops.note("keep", rel, "project data")
        elif cur == h:
            ops.remove(rel)
        else:
            kept.append(rel)
            ops.note("keep", rel, "locally modified")
    hook = subprocess.run(["git", "-C", root, "rev-parse", "--git-path", "hooks/pre-commit"], capture_output=True, text=True)
    if hook.returncode == 0 and GIT_HOOK_MARK in (read_text(os.path.join(root, hook.stdout.strip())) or ""):
        ops.note("remove", hook.stdout.strip(), "compliance gate git hook")
        if not ops.dry:
            os.remove(os.path.join(root, hook.stdout.strip()))
    stxt = read_text(ops.abs(SETTINGS))
    if stxt:
        cur = strip_harness_hooks(remove_fragment(json.loads(stxt), manifest.get("settings_fragment", {})) or {})
        ops.backup(SETTINGS)
        if cur:
            ops.write(SETTINGS, (json.dumps(cur, indent=2) + "\n").encode(), "restore", "harness rules removed")
        else:
            ops.remove(SETTINGS, "only harness rules")
    for name, (s, e) in (("CLAUDE.md", (MD_START, MD_END)), ("AGENTS.md", (MD_START, MD_END)),
                         (".gitignore", (GI_START, GI_END)), (codeowners_file(root), (GI_START, GI_END))):
        txt = read_text(ops.abs(name))
        if txt and s in txt:
            new = remove_block(txt, s, e)
            if new.strip():
                ops.write(name, new.encode(), "update", "harness block removed")
            else:
                ops.remove(name, "only harness content")
    ops.remove(MANIFEST)
    for line in ops.log:
        print("  " + line)
    if not args.dry_run:
        shutil.rmtree(ops.abs(".claude/state"), ignore_errors=True)
        for dirpath, dirnames, filenames in sorted(os.walk(ops.abs(".claude")), key=lambda x: -len(x[0])):
            if "/knowledge" in dirpath.replace(os.sep, "/"):
                continue
            for d in ("__pycache__",):
                shutil.rmtree(os.path.join(dirpath, d), ignore_errors=True)
            try:
                os.rmdir(dirpath)
            except OSError:
                pass
    print("\nUninstalled. Knowledge graph nodes were kept in .claude/knowledge/." +
          (f"\nKept locally modified files: {', '.join(kept)}" if kept else ""))
    return 0


# ------------------------------------------------------------------ check

def check(root: str) -> int:
    """Fail if a locked security file, a harness deny rule or a harness hook was changed or removed.

    Locked files are compared with this release when the repo is on the same version, otherwise with the
    hashes the installer recorded (a sync PR may still be open). Edits to other harness files are allowed."""
    mtxt = read_text(os.path.join(root, MANIFEST))
    if not mtxt:
        if os.path.exists(os.path.join(root, ".claude", "harness.json")) or \
                os.path.exists(os.path.join(root, ".claude", "hooks", "guard_bash.py")):
            print(f"FAIL the harness is installed but {MANIFEST} is missing; re-run install.py to restore it.")
            return 1
        print("No harness manifest found; nothing to check.")
        return 0
    try:
        manifest = json.loads(mtxt)
    except ValueError:
        print(f"FAIL {MANIFEST} is not valid JSON")
        return 1
    same_version = manifest.get("version") == harness_version()
    expected: Dict[str, str] = {r: h for r, h in manifest.get("files", {}).items() if is_locked(r)}
    if same_version:
        for rel_t in template_files():
            rel = f".claude/{rel_t}"
            if is_locked(rel):
                expected[rel] = sha256_file(os.path.join(TEMPLATE_CLAUDE, rel_t)) or ""
    problems = []
    for rel, h in sorted(expected.items()):
        cur = sha256_file(os.path.join(root, rel))
        if cur is None:
            problems.append(f"missing   {rel}")
        elif cur != h:
            problems.append(f"modified  {rel}")
    try:
        settings = json.loads(read_text(os.path.join(root, SETTINGS)) or "{}")
    except ValueError:
        settings = {}
        problems.append(f"invalid   {SETTINGS}")
    frag = manifest.get("settings_fragment") or {}
    deny = set(map(canon, (settings.get("permissions") or {}).get("deny") or []))
    problems += [f"removed   deny rule {r}" for r in (frag.get("permissions") or {}).get("deny") or [] if canon(r) not in deny]
    ask = set(map(canon, (settings.get("permissions") or {}).get("ask") or []))
    problems += [f"removed   ask rule {r}" for r in (frag.get("permissions") or {}).get("ask") or [] if canon(r) not in ask]
    want_bypass = (frag.get("permissions") or {}).get("disableBypassPermissionsMode")
    if want_bypass and (settings.get("permissions") or {}).get("disableBypassPermissionsMode") != want_bypass:
        problems.append("changed   permissions.disableBypassPermissionsMode")
    hooks = settings.get("hooks") or {}
    for ev, groups in (frag.get("hooks") or {}).items():
        have = set(map(canon, hooks.get(ev) or []))
        problems += [f"removed   {ev} hook {g.get('matcher', '*')}" for g in groups if canon(g) not in have]
    if problems:
        print("FAIL locked Claude Code Harness security files or rules were changed:")
        print("\n".join("  " + p for p in problems))
        print("Re-run install.py to restore them (your versions are backed up), or ask the security owners.")
        return 1
    print(f"OK locked security files and rules match Claude Code Harness v{manifest.get('version')}.")
    return 0


# ------------------------------------------------------------------ main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Install the Claude Code project harness")
    ap.add_argument("project", help='the target project: DIR="/path/to/project" (or just the path)')
    ap.add_argument("--dry-run", action="store_true", help="show actions without writing")
    ap.add_argument("--force", action="store_true", help="overwrite locally modified harness files (backups kept)")
    ap.add_argument("--uninstall", action="store_true", help="remove unmodified harness files and the harness rules in settings; keep your rules and KG nodes")
    ap.add_argument("--check", action="store_true", help="exit 1 if locked security files, harness deny rules or hooks were changed (for CI)")
    ap.add_argument("--codeowners", help='owners for the locked files in CODEOWNERS, e.g. "@org/security" (default: harness.json "codeowners")')
    ap.add_argument("--no-seed", action="store_true", help="do not seed the knowledge graph")
    ap.add_argument("--no-sandbox", action="store_true", help="omit the Bash sandbox settings")
    ap.add_argument("--git-hook", action="store_true",
                    help="also install a git pre-commit hook, so commits typed in a terminal need a compliance PASS too")
    ap.add_argument("--user-env", action="store_true",
                    help="also set CLAUDE_CODE_DISABLE_FILE_MENTIONS=1 in ~/.claude/settings.json")
    args = ap.parse_args(argv)

    if sys.version_info < (3, 9):
        print("Python 3.9+ is required.", file=sys.stderr)
        return 2
    project = args.project[len("DIR="):] if args.project.startswith("DIR=") else args.project
    if not project:
        print('Give the project directory: DIR="/path/to/project"', file=sys.stderr)
        return 2
    root = os.path.abspath(os.path.expanduser(project))
    if not os.path.isdir(root):
        print(f"Not a directory: {root}", file=sys.stderr)
        return 2
    if root in ("/", os.path.expanduser("~")) or os.path.dirname(root) == root:
        print(f"Refusing to install into {root}; point at a project directory.", file=sys.stderr)
        return 2
    try:
        inside_harness = os.path.commonpath([root, HERE]) == HERE
    except ValueError:  # Windows: paths on different drives share no common path
        inside_harness = False
    if inside_harness:
        print("Refusing to install into the harness repo itself.", file=sys.stderr)
        return 2
    if args.check:
        return check(root)
    print(f"{'Uninstalling' if args.uninstall else 'Installing'} Claude Code Harness -> {root}"
          f"{' (dry run)' if args.dry_run else ''}")
    return uninstall(root, args) if args.uninstall else install(root, args)


if __name__ == "__main__":
    sys.exit(main())
