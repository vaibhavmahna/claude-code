# 🔒 Security and compliance

How Claude Code Harness blocks secrets, personal data and dangerous commands, how the compliance gate works, and what the harness cannot do. The short version is in the [README](../README.md).

<!-- toc -->
## 📑 Contents

- [Security model](#-security-model)
- [Compliance](#-compliance)
- [Known limits](#-known-limits)

<!-- /toc -->

## 🔒 Security model

Several independent layers enforce the policy, so one gap doesn't expose everything.

| Layer | What it stops |
|---|---|
| `guard_prompt.py` (UserPromptSubmit) | Secrets/PII pasted into a prompt. The prompt is blocked and suppressed. |
| `guard_read.py` (Read, Grep) | Credential files by path, and any file whose **content** contains secrets/PII. The content is scanned *before* Claude sees it; for a ranged read of a big file, only the requested lines are scanned. Grep results are pre-scanned too. |
| `guard_write.py` (Write, Edit, …) | Secrets/PII written into files; writes to `.env`/keys; edits to harness config and to anything under `.git/`. Asks before a change to a file that runs on its own or that Claude follows in later sessions: `.mcp.json`, agents, skills, commands, `CLAUDE.md`, `AGENTS.md`, CI workflows, `.husky/`, `.vscode/tasks.json`. |
| `guard_bash.py` (Bash) | `rm`, `sudo`, force-push, `reset --hard`, `clean -f`, infra destroy, DB drops, `curl \| sh`, `cat .env`, `printenv`, keychain/cloud-credential reads, file uploads, `git --output` into harness files. Parses `&&`, `;`, pipes, `$( )`, `bash -lc`, wrappers with options (`nice -n`, `timeout -s`), here-strings, heredocs, `xargs` and `find -exec`. Asks before discarding a single file's changes or before inline code that deletes files. Resolves relative paths from the directory the shell is in, follows a variable set earlier in the same command, and denies inline code that names harness files. **Supply chain:** package installs must pass `--ignore-scripts` (`supply_chain.install_scripts`), a new package asks in plain words, and fetched code cannot run unread in any common form (`curl \| sh`, `sh -c "$(curl …)"`, `bash <(curl …)`, download then run). |
| `guard_egress.py` (WebFetch, WebSearch, MCP) | Secrets/PII sent to external services. MCP recipient fields such as `to` are exempt from the PII check. |
| `redact_output.py` (PostToolUse) | Defense in depth: masks anything that still shows up in Bash/Grep/Web/MCP output. |
| `guard_bash.py` + `guard_compliance.py` (compliance gate) | Any `git commit` whose staged files the compliance reviewer has not passed. See [Compliance](#-compliance). |
| `settings.json` | 132 deny rules (Read/Edit of credential paths, destructive Bash), 16 ask rules, 43 allow rules for read-only and harness commands, bypass mode disabled, Bash sandbox with credential directories unreadable. |

How one tool call passes through the hooks:

![How a tool call passes through the harness hooks](diagrams/tool-call-flow.svg)

**Detection rules:**
- **Secrets:**
  - private keys (the whole block, body included)
  - AWS, GitHub, GitLab, Slack, Stripe, Google, Anthropic, OpenAI, npm, SendGrid, PyPI, Twilio, Mailgun, Grafana and age keys
  - Azure storage account keys, SAS tokens and Entra ID (AD) client secrets
  - HashiCorp Vault and Terraform Cloud tokens, Docker registry auth, kubeconfig keys, `Bearer` tokens
  - JWTs
  - credentials inside connection strings, including SQL Server, ADO.NET and JDBC `Password=` forms
  - high-entropy `password`/`token`/`api_key`/`signing_key`/`*_PAT` assignments, quoted, in dotenv form or as unquoted YAML
- **PII:**
  - emails and phone numbers: block on web and MCP calls, and locally only in bulk (20 or more distinct values in one text, `scan.pii_bulk_threshold`); a single contributor address passes
  - US SSNs and Canadian SINs (Luhn-checked)
  - credit card numbers (Luhn-checked)
  - IBANs (mod-97)

**Allowed vs blocked:**
- **Block messages** name the detector, never the value.
- **Placeholders pass:** `user@example.com`, `555-0100`, Stripe test cards, `${VAR}` placeholders, well-known local dev values (`postgres`, `guest`, `django-insecure-...`) and AWS's documented example key are not flagged.
- **Allowlist:** for genuine exceptions, a human edits `.claude/security/allowlist` (`path:<glob>`, `sha256:<hash>` or `detector:<name>`). `python3 .claude/tools/safe_read.py --hash`, run in a terminal, prints the `sha256:` line for one value.

**Git policy:**
- **Allowed:** status, diff, log, show, add, commit (once the compliance review has passed), branch, switch, checkout -b, stash list/push, fetch, blame.
- **Asks first:** push, rebase, `--amend`, merge, cherry-pick, revert, tag, new dependencies, `restore <file>` / `checkout -- <file>`, `switch -f`, `--output` to a file.
- **Denied:** `commit --no-verify`, `commit -a`, `commit-tree`, force-push, `reset --hard`, `clean -f`, `branch -D`, `checkout .` / `restore .`, history rewrites, `git -c` settings that run commands.

**Deletion and cleanup:**
- `rm` and every other delete command stay blocked; build output goes through `cleanup.py` instead (below).
- Claude Code asks before any Write/Edit inside `.claude/`, so agents save knowledge-graph nodes, hand-off notes and scratch files with `python3 .claude/tools/save.py`. It is pre-approved, writes only to those three places, and runs the same secret/PII scan as the write guard.
- Agents remove only their own leftovers, through `python3 .claude/tools/cleanup.py remove <path>`: files in `.claude/scratch/`, untracked files an agent created this session, or build output: a git-ignored folder named in `cleanup.build_dirs` (`dist/`, `node_modules/` and similar) that holds no tracked files. Tracked, sensitive and harness files are refused, and so is other git-ignored data such as a local database or an uploads folder.
- Removed files go to `.claude/state/trash/` and can be restored with `cleanup.py restore <path>` until the trash is emptied (7 days by default).
- At the end of a turn, a `[harness:cleanup]` message lists anything that looks temporary (`tmp_*`, `debug_*`, `*.log`, ...) so the agent cleans up once its tests pass. Scratch files older than a day are cleared at session start.

## 🧾 Compliance

The `data-handling-compliance` skill carries the GDPR, SOC 2 and health-data rules for code that touches personal data, customer data or AI features. Each rule has a tier: **BLOCK** (stop), **REQUIRE** (implement it in the same change) or **FLAG** (proceed and note it for Security). `CLAUDE.md` and every agent that writes or reviews work point to it, so it applies to requests that never mention compliance.

Instructions alone can be skipped, so the commit itself is gated:

1. commit-helper stages the files.
2. **compliance-reviewer** (Fable, read-only) reads the staged diff, classifies the data, applies the rules and ends with `COMPLIANCE: PASS <id>` or `COMPLIANCE: FAIL`. An open BLOCK or an unmet REQUIRE fails. It also fills in the PR compliance note.
3. `guard_compliance.py` records a PASS only when it comes from that agent and the id matches what is staged at that moment.
4. `guard_bash.py` refuses `git commit` unless every staged file is covered by the recorded PASS with the same content. Editing a file after the review means a new review. Reviewed files can still be split across several commits.

| Attempt | Result |
|---|---|
| `git commit` with no PASS, or after a later edit | Denied |
| `git commit -a`, file arguments, `--include`, `--patch`, `git add … && git commit` on one line | Denied: they add content nobody staged for review |
| `git commit --no-verify`, `git commit-tree` | Denied |
| `git merge`, `cherry-pick`, `revert`, `am` | Asks you first |
| `gh pr create` without the `## Compliance note` section in the body | Denied. `python3 .claude/tools/compliance.py note` prints the note |
| An agent writing the PASS record itself | Denied: `.claude/state/` is writable by hooks only |

Every verdict and every commit check is appended to `.claude/state/compliance-log.jsonl` with actor, action, target, outcome and timestamp. The file stays on the machine (it is gitignored), holds identifiers only, and is rotated at 5 MB with one older file kept. The compliance note in the pull request is the record that travels with the change.

**Commits you type yourself.** Hooks apply to Claude, not to your terminal. Install with `--git-hook` to add a git `pre-commit` hook that runs the same check, so a commit typed by hand also needs a PASS. It is opt-in because it changes local git for everyone working in that clone, and it leaves an existing `pre-commit` hook alone.

The gate is set by `compliance.commit_gate` in `.claude/harness.json` (`block`). Agents cannot edit that file, and `/harness-doctor` fails when the value is anything else.

## 🚫 Known limits

- **@-mentions:** `@file` mentions skip hooks unless `CLAUDE_CODE_DISABLE_FILE_MENTIONS=1` is set at user or shell level, which `--user-env` does. Project settings cannot set it. Read deny rules still apply to mentions on a best-effort basis.
- **Scripts:** a script that reads a secret file at runtime (e.g. `python app.py` loading `.env`) is not inspected. The sandbox, the `.env` deny rules and output redaction reduce this risk without removing it.
- **Blocked prompts:** the text of a blocked prompt can remain in the local transcript and history files on your machine.
- **Indirection:** the Bash guard reads the command text. It follows variables, `cd` and nested shells within one command, but a script on disk, or a value built at runtime, is not inspected. Install scripts are switched off for npm, pnpm, yarn and bun only; pip, cargo and other installers ask without that protection.
- **Detection:** regex and entropy detection catches common formats. Unusual secret formats can slip through, and some PII false positives are expected; the allowlist covers those.
- **Very large searches:** a Grep over more than `scan.grep_max_files` files or `scan.grep_max_bytes` isn't pre-scanned, so it relies on output masking (`redact_output.py`), the same layer that covers Bash output.
- **Context measurement:** context % is measured after each tool call, so a single huge read can overshoot before the hand-off triggers.
- **Python required:** hooks are Python; if `python3` is missing, or a guard runs past its time limit, the tool call goes ahead unchecked. A guard whose files are damaged denies the call. `/harness-doctor` flags a missing `python3`.
- **Shell parsing:** the Bash guard reads common command forms (wrappers, `bash -c`, heredocs, pipes). Variable indirection such as `X=rm; $X file` and scripts that delete files through their own code are not caught; the permission prompt and the sandbox are the backstop.
- **Context limit:** the limit is a strong instruction that repeats, not a hard stop. Auto-compact is the backstop.
- **Compliance gate:** it blocks commits made through Claude Code. A commit typed in a terminal is only gated when the project was installed with `--git-hook`, and a local git hook can be skipped by the person at the keyboard. `git merge`, `cherry-pick`, `revert` and `am` ask instead of being reviewed. The reviewer is a model applying written rules: it can miss things, and the questions the skill leaves to Legal or Security still need a person.
- **Background refresh:** the knowledge-graph refresh runs as a background agent. If the session ends before it finishes, the pending list is kept and the next session starts it again.
