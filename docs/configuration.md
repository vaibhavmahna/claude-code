# 🔧 Configuration and upkeep

The knowledge graph, the context budget, installer options, tunables, upgrades and troubleshooting. The short version is in the [README](../README.md).

<!-- toc -->
## 📑 Contents

- [Knowledge graph](#-knowledge-graph)
- [Context budget (75% per agent)](#-context-budget-75-per-agent)
- [Configuration](#-configuration)
- [Update and uninstall](#-update-and-uninstall)
- [Troubleshooting](#-troubleshooting)

<!-- /toc -->

## 🧠 Knowledge graph

`.claude/knowledge/` is the project's index-based memory.

**How lookups work:**
1. `INDEX.md` (≤ 200 lines, generated) lists nodes by kind, one line each; in a big graph each kind shows its first entries and points to `kg.py query` for the rest: `` - **<id>**: <summary> → `nodes/<id>.md` · `<first source>` ``.
2. `nodes/<kind>-<slug>.md` holds a short list of facts, each ending in an exact pointer such as `→ src/auth/jwt.py:20-27`.
3. `graph.json` holds typed edges: `depends_on`, `implements`, `calls`, `documented_in`, `decided_by`.
4. `python3 .claude/tools/kg.py query <terms>` returns ranked nodes, pointers and matching facts.

**How it is maintained:**
- The installer seeds the layout, entry points, commands and README sections. `/kg-bootstrap` builds the graph out in depth.
- Hooks record each edited file. When edits touch files the KG references (or a new area), Claude gets a `[harness:kg]` note and starts **knowledge-keeper** (Haiku) in the background, then carries on with your request. Nothing waits for the refresh, and at most one note is sent every 20 minutes. Test files, lockfiles and small unrelated edits never trigger one.
- `kg.py validate` catches dangling links, missing files and line ranges past the end of a file. `kg.py stale` lists nodes whose sources changed since `verified_commit`.
- The graph stays lean and current. When source files are deleted, hooks delete the nodes that describe them at the end of the turn and at session start, with no model call. Nodes nobody has re-verified for `knowledge.max_age_days` (30) are listed by `kg.py stale`; knowledge-keeper re-verifies each against the code or deletes it, and it deletes facts that no longer hold whenever it updates a node. Hand-off notes older than 7 days are deleted at session start (`cleanup.handoff_days`).

## ⏳ Context budget (75% per agent)

`context_guard.py` reads each agent's own transcript after every tool call. It takes the real token usage and compares it with the real context window; for the main session it uses the window size reported to the status line, which handles 1M sessions correctly.

| Usage | What happens |
|---|---|
| ≥ 70% | One warning: finish the current unit of work and delegate exploration. |
| ≥ 75% | The agent must save a hand-off note (`save.py handoff`, written to `.claude/knowledge/handoffs/<agent>-<timestamp>.md`) and return `STATUS: HANDOFF <path>`. A hook then tells the parent to spawn a **fresh agent of the same type** that resumes from the handoff note. |

The main session is reminded at the limit once, then every 25th tool call. Auto-compact at 85% (`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`) is the backstop, set above the limit so the hand-off happens first. On a 1M-token window 75% is 750,000 tokens; set `context.window_tokens` to a smaller number to hand off sooner.

## 🔧 Configuration

Installer options:

```bash
python3 ~/claude-code/install.py DIR="/path/to/your/project" --dry-run     # print every action, write nothing
python3 ~/claude-code/install.py DIR="/path/to/your/project" --force       # also overwrite harness files you modified (backups kept)
python3 ~/claude-code/install.py DIR="/path/to/your/project" --user-env    # set CLAUDE_CODE_DISABLE_FILE_MENTIONS=1 in ~/.claude/settings.json
python3 ~/claude-code/install.py DIR="/path/to/your/project" --git-hook    # also gate commits typed in a terminal (git pre-commit hook)
python3 ~/claude-code/install.py DIR="/path/to/your/project" --no-sandbox  # omit Bash sandbox settings
python3 ~/claude-code/install.py DIR="/path/to/your/project" --no-seed     # skip knowledge-graph seeding
python3 ~/claude-code/install.py DIR="/path/to/your/project" --uninstall   # remove the harness (see below)
python3 ~/claude-code/install.py DIR="/path/to/your/project" --check       # exit 1 if locked security files or rules were changed (for CI)
python3 ~/claude-code/install.py DIR="/path/to/your/project" --codeowners "@org/security"  # route edits to locked files to these owners
```

**How the installer treats your existing files:**
- `.claude/settings.json` is deep-merged: your rules and values win, and a `.bak` copy is written first.
- `CLAUDE.md` and `AGENTS.md`: the harness only edits its block between `<!-- harness:start -->` and `<!-- harness:end -->`. Everything outside it is yours.
- `.claude/.harness-manifest.json` tracks the hash of every installed file, so files you changed are never overwritten silently.

Tune behavior in `.claude/harness.json`:
- `context.limit_pct` (75), `context.warn_pct` (70), `context.window_tokens`. If you raise the limit, keep `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` in `.claude/settings.json` above it.
- `compliance.commit_gate`: `block`. Leave it there; see [Compliance](security.md#-compliance).
- `knowledge.max_age_days` (30): how long a knowledge-graph node may go without being re-verified; `0` turns the check off
- `scan.block_pii`, `scan.pii_detectors`: which personal-data detectors run
- `scan.pii_action`: per detector, `block` (default) or `warn`. Emails and phone numbers ship as `warn`: a few in a file, command or prompt pass, and `scan.pii_bulk_threshold` (20) or more distinct values in one text still block, which is what a customer export looks like. Every detector still blocks on web and MCP calls. Set a detector to `block` for the strict behavior.
- `scan.max_file_bytes` (5 MB), `scan.grep_max_files` (2,000), `scan.grep_max_bytes` (50 MB): how much the read guards pre-scan. A bigger file can still be read in ranges, and a bigger Grep runs with its output masked instead of pre-scanned.
- `cleanup.trash_days`, `cleanup.scratch_hours`, `cleanup.handoff_days`: how long removed files stay restorable in the trash, and how old scratch files and hand-off notes get before session start clears them
- `cleanup.build_dirs`: the git-ignored folders agents may clear as build output. Anything else git ignores stays.
- `supply_chain.install_scripts`: `block` (default) denies an npm, pnpm, yarn or bun install without `--ignore-scripts`; `ask` asks instead; `allow` switches the check off. A new package asks in every mode.
- `diagrams.theme`: `light` (default), `dark`, or `auto` (follows the viewer's setting) for every diagram the agents draw
- `knowledge.max_index_lines`, `knowledge.pending_trigger_count`
- `prompt_budgets`: size caps for agent prompts, skills, an agent plus the skills it preloads (`spawn_chars`), and `CLAUDE.md` + `AGENTS.md` (loaded every session); `/harness-doctor` warns when a prompt grows past its cap

## 🔄 Update and uninstall

**Update** to the latest harness:

```bash
git -C ~/claude-code pull
python3 ~/claude-code/install.py DIR="/path/to/your/project"
```

Re-running the installer is an upgrade. Harness files you haven't touched are refreshed, files you changed are skipped (pass `--force` to overwrite them, with backups), and rules from the old version are swapped for the new ones.

The security files are locked: the guard hooks (`guard_*.py`, `redact_output.py`), the shared detectors in `hooks/lib/`, `.claude/security/` and `tools/save.py`. Install and upgrade always restore them to the release, and back up your version first. `--check` exits 1 if a locked file was changed or removed, or if a harness deny rule or hook was taken out of `settings.json`. Run it in CI on the committed files. To change how a guard behaves for your project, use `harness.json` or the allowlist, or ask the security owners. If `codeowners` is set in `harness.json` (or you pass `--codeowners`), the installer adds a marked block to your CODEOWNERS file so edits to the locked files need those owners' review. Allowlist entries stay yours, but that block covers them too. See [CHANGELOG.md](../CHANGELOG.md) for what changed.

**Uninstall:**

```bash
python3 ~/claude-code/install.py DIR="/path/to/your/project" --uninstall
```

This removes the harness files you haven't modified and the harness rules in `settings.json`. Your own settings, your content in `CLAUDE.md` and `AGENTS.md`, and the knowledge-graph nodes stay.

## 🐛 Troubleshooting

| Problem | Fix |
|---|---|
| A command I need is blocked | Run it yourself in a terminal. The guards apply to Claude, not to you. To delete build output that git ignores (`dist/`, `node_modules/`), Claude can use `python3 .claude/tools/cleanup.py remove <path>`, which moves it to a restorable trash. |
| A file read or write is blocked by a detector | The message names the detector and the lines. Claude can read just the lines it needs, or use `/safe-read` for a masked view. For a false positive, add `path:<glob>` to `.claude/security/allowlist`, or allow one value: run `python3 .claude/tools/safe_read.py --hash` in a terminal, type the value at the hidden prompt, and paste the `sha256:` line it prints. |
| Too many permission prompts | Add your own `allow` rules to `.claude/settings.json`; they survive upgrades. Lockfile installs (`npm ci --ignore-scripts`, `uv sync`) and `npx` for a binary already in `node_modules` follow those rules. Adding a new dependency always asks. |
| `npm install` is blocked | Installs must pass `--ignore-scripts`, because install scripts are how most malicious packages attack. Claude re-runs with the flag on its own. If a package needs its build step (native modules), run `npm rebuild <package>` yourself, or set `supply_chain.install_scripts` to `ask` in `.claude/harness.json`. |
| Hooks fail with `python3: command not found` | Install Python 3.9+ and make sure `python3` is on the `PATH` Claude Code sees. Without it the guards cannot run, so nothing is checked; `/harness-doctor` reports this. |
| `/harness-doctor` reports a failure | Re-run the installer. If a file shows as modified and you want the stock version back, re-run with `--force`. Locked security files are restored on every run without it. |
| `git commit` is refused with `[compliance gate]` | Expected until the compliance reviewer passes the staged files. Ask Claude to run the compliance review; if a file changed since, it needs a new one. `python3 .claude/tools/compliance.py status` shows which files are not covered. |
| `--check` fails in CI | A locked security file, a harness deny rule or a harness hook was changed. Re-run the installer to restore it, or ask the security owners. |
| `@file` mentions show a secret file | Re-run the installer with `--user-env`, then restart Claude Code. |
