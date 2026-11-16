# 🔒 Security policy

Claude Code Harness installs hooks that block secrets, PII and destructive commands. A flaw in those guards, or in the installer that places them, matters to everyone who uses it. This page says how to report one.

<!-- toc -->
## 📑 Contents

- [Supported versions](#-supported-versions)
- [Reporting a vulnerability](#-reporting-a-vulnerability)
- [In scope](#-in-scope)
- [Out of scope](#-out-of-scope)
- [Use fake secrets](#-use-fake-secrets)
- [What to include](#-what-to-include)

<!-- /toc -->

## 🔖 Supported versions

| Version | Supported |
|---|---|
| 0.2.x | Yes |
| Older | No |

The current version is set in `template/claude/harness.json`.

## 🚨 Reporting a vulnerability

Report privately. Do not open a public issue or pull request for a vulnerability.

1. Open the repository's **Security** tab.
2. Choose **Report a vulnerability** (GitHub private vulnerability reporting).
3. Fill in the form with the details listed below.

This is a volunteer project. We aim to acknowledge a report within 7 days. Fix times depend on severity and on who is available, and we will tell you where things stand.

## 🎯 In scope

- Any way to get a secret or PII past a guard: into a prompt, a file read, a file write, a Bash command, or an outgoing web or MCP call.
- Bypasses of `guard_bash.py` command parsing, for example a destructive command that is not blocked because of how it is quoted, nested or chained.
- Any way for Claude to create a commit that the compliance gate did not check: a `git commit` form the guard misses, a PASS recorded from anything other than the compliance-reviewer's own result, a PASS that covers content it was not given for, or a way for an agent to write `.claude/state/compliance-pass.json`.
- A guard that fails open, other than the documented cases: `python3` is missing, or a guard runs past its time limit (see [Known limits](docs/security.md#-known-limits)).
- The installer overwriting your files or settings. The installer is meant to deep-merge `settings.json` (your rules and scalar values win) after writing a `.bak-<timestamp>` copy, and to keep your content in `CLAUDE.md` and `AGENTS.md` outside the `<!-- harness:start -->` block. It should skip a locally modified harness file unless you pass `--force`, and then it writes a `.bak-<timestamp>` copy first. Locked security files (guard hooks, `hooks/lib/`, `.claude/security/`, `save.py`, `compliance.py`) are the exception: they are always restored to the release, after a `.bak-<timestamp>` copy.
- A way to change a locked security file, a harness deny rule or a harness hook that `install.py --check` does not report. Anything that breaks those promises is in scope.

## 🚫 Out of scope

The limits already listed under [Known limits](docs/security.md#-known-limits) in the README are known and documented. They include `@file` mentions, scripts that read secret files at runtime, blocked prompt text in local history, unusual secret formats, and context measured only after each tool call. Reports about those will be closed, unless you found a way to make them worse than documented.

## 🧪 Use fake secrets

Never put a real secret, token, key or personal data in a report, repro or test. Generate a fake value that matches the detector's format. The test suite builds its fake secrets at runtime so no secret-shaped string sits in the repository; see `tests/helpers.py`.

## 📝 What to include

- The harness version and Python version (`python3 --version`).
- Which guard or installer step is involved.
- The smallest repro, using fake values.
- What you expected, and what happened.
- Your assessment of the impact, if you have one.
