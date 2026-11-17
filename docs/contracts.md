# 🧾 Harness contracts

Single source of truth for the conventions shared by hooks, tools, agents, skills, `CLAUDE.md` and `AGENTS.md`.
Anything installed into a project must follow these. Paths are relative to the **target project root**.

<!-- toc -->
## 📑 Contents

- [Installed layout](#-installed-layout)
- [Tools (run from project root with `python3`)](#-tools-run-from-project-root-with-python3)
- [Knowledge graph](#-knowledge-graph)
  - [Node file `.claude/knowledge/nodes/<id>.md`](#-node-file-claudeknowledgenodesmd)
  - [Lookup protocol (all agents)](#-lookup-protocol-all-agents)
  - [Update protocol](#-update-protocol)
- [Context budget protocol](#-context-budget-protocol)
- [Agent return block (every agent ends with this)](#-agent-return-block-every-agent-ends-with-this)
- [Security protocol](#-security-protocol)
- [Git policy](#-git-policy)
- [Compliance gate protocol](#-compliance-gate-protocol)
- [Models](#-models)

<!-- /toc -->

## 📁 Installed layout

| Path | Purpose | Editable by Claude? |
|---|---|---|
| `CLAUDE.md` | Session instructions (harness block between `<!-- harness:start -->` / `<!-- harness:end -->`) | yes (outside block) |
| `AGENTS.md` | Agent roster, routing, protocols (harness block between markers) | yes (outside block) |
| `.claude/settings.json` | Permissions, hooks, env, sandbox, statusline | **no** |
| `.claude/harness.json` | Tunables (context %, compliance gate, scan limits, KG limits) | **no** |
| `.claude/hooks/**` | Hook scripts | **no** |
| `.claude/tools/**` | CLI tools (`kg.py`, `save.py`, `safe_read.py`, `compliance.py`, `svg_lint.py`, `doc_format.py`, `cleanup.py`, `statusline.py`, `doctor.py`, `stack.py`) | **no** |
| `.claude/security/allowlist` | Human-only exceptions for detectors | **no** |
| `.claude/agents/*.md` | Subagent definitions | yes |
| `.claude/skills/<name>/SKILL.md` | Skills | yes |
| `.claude/knowledge/INDEX.md` | Generated KG index (never hand-edit) | via `kg.py index` |
| `.claude/knowledge/nodes/*.md` | KG nodes | via `save.py node` (knowledge-keeper) |
| `.claude/knowledge/graph.json` | Generated graph | via `kg.py index` |
| `.claude/knowledge/handoffs/*.md` | Context handoff notes (gitignored) | via `save.py handoff` |
| `.claude/scratch/` | Throwaway files (gitignored) | via `save.py scratch` |
| `.claude/state/` | Runtime state: `kg-pending`, context-guard state, `compliance-pass.json`, `compliance-note.md`, `compliance-log.jsonl` (gitignored) | hooks only |

## 🧰 Tools (run from project root with `python3`)

- `.claude/tools/kg.py`:
  - `index`: rebuild INDEX.md + graph.json
  - `query <terms...> [--limit N]`
  - `show <id>`
  - `neighbors <id> [--depth N]`
  - `upsert <id> --kind K --summary S [--title T] [--tags a,b] [--links type:id,...] [--sources path:a-b,...]`: create the node or update its frontmatter. The body is preserved.
  - `remove <id>`: delete a node, drop links to it from other nodes, rebuild the index
  - `prune [--apply]`: list nodes whose sources were all deleted; `--apply` removes them
  - `validate`
  - `stale`
  - `pending [--clear]`
  - `seed`: deterministic, used by the installer
  - `stats`
- `.claude/tools/save.py node|handoff|scratch <name>` (content on stdin): the only way agents write inside `.claude/`, because Claude Code asks for approval before any Write/Edit there.
  - `node <id>`: writes `knowledge/nodes/<id>.md`. The content must start with frontmatter whose `id` matches; `updated` and `verified_commit` are stamped.
  - `handoff <agent>`: writes `knowledge/handoffs/<agent>-<YYYYmmdd-HHMMSS>.md` and prints the path.
  - `scratch <name>`: writes `scratch/<name>` and prints the path.
  - Content gets the same secret/PII scan as `guard_write.py`; a finding exits 1 with a `[harness:security]` message. Names cannot contain paths.
- `.claude/tools/safe_read.py <path> [--lines a-b]`: prints a file with secret/PII values masked as `***REDACTED:<detector>***`.
- `.claude/tools/svg_lint.py <file.svg|file.md|file.mmd>...`: diagram safety, accessibility and precision checks (grid, alignment, spacing, consistency, edge routing). Exits 1 on errors.
- `.claude/tools/doc_format.py toc|check <file.md>...`: inserts or refreshes the linked table of contents, and checks that every heading starts with one emoji and no emoji appear elsewhere. `palette` lists the allowed emoji.
- `.claude/tools/cleanup.py candidates|remove|scratch|restore|purge`: garbage cleanup for agent-created files. Only `.claude/scratch/` contents, untracked files an agent created this session, and build output (a git-ignored folder named in `cleanup.build_dirs`) can be removed; they move to `.claude/state/trash/` (restorable, emptied after `cleanup.trash_days`). Scratch files older than `cleanup.scratch_hours` are removed at session start.
- `.claude/tools/doctor.py`: install health check (deps, settings, hook self-test, KG validate), plus the control contract: the compliance gate is `block` and the compliance-reviewer agent is present, every agent keeps its frontmatter, `STATUS:` return block and `[harness:context] LIMIT` handoff rule, and every agent, skill and always-loaded file stays within `prompt_budgets` in `harness.json`.
- `.claude/tools/compliance.py id|status|check|note`: the commit gate's view of the staged files. `id` prints the id the reviewer quotes, `status` and `check` say whether a recorded PASS covers every staged file (`check` exits 1 when not), `note` prints the compliance note from the last PASS.
- `.claude/tools/stack.py`: detected stack/commands as JSON.

## 🧠 Knowledge graph

### 🧬 Node file `.claude/knowledge/nodes/<id>.md`
- `id` = `<kind>-<slug>` (lowercase, `a-z0-9-`).
- The file name equals `id` + `.md`.

```markdown
---
id: module-auth-jwt
kind: module
title: Auth JWT
summary: JWT issue/verify and refresh-token rotation
tags: [auth, jwt, token, login]
links: [depends_on:data-model-user, decided_by:decision-jwt-rotation]
sources: [src/auth/jwt.py:12-88, src/auth/routes.py:40-71]
updated: 2026-10-24
verified_commit: abc1234
---
## Facts
- Tokens are RS256-signed; key loaded from env → src/auth/jwt.py:20-27
- Refresh tokens rotate on every use → src/auth/jwt.py:60-88

## Gotchas
- Clock skew tolerance is 30s → src/auth/jwt.py:45
```

**Frontmatter syntax:** one `key: value` per line. Lists are inline `[a, b]`. No nesting.

**Kinds** (INDEX section order):
1. `architecture`
2. `module`
3. `entrypoint`
4. `api`
5. `data-model`
6. `workflow`
7. `command`
8. `decision`
9. `gotcha`
10. `doc`
11. `glossary`

**Link types:** `depends_on`, `implements`, `calls`, `documented_in`, `decided_by`, `related` (the default when written as a bare `id`).

**Size limits:** the body has at most 60 lines (`harness.json → knowledge.max_node_lines`). INDEX.md has at most 200 lines.

**Pointer format** (every fact ends with one): `→ path/to/file.ext:line` or `→ path/to/file.ext:start-end`.

**What qualifies as "major info".** Only these, never ephemeral task state:
- architecture
- module responsibilities
- entry points
- public APIs
- data models
- build/test commands
- key decisions (and why)
- gotchas

### 🔍 Lookup protocol (all agents)
1. Read `.claude/knowledge/INDEX.md`, or run `python3 .claude/tools/kg.py query <terms>`.
2. Open the node.
3. Jump to the exact `path:line` pointers with ranged Reads.
4. Fall back to Grep/Glob only when the KG has no answer, and mention the gap in your return block (`KG: gap <topic>`).

### 🔄 Update protocol
- Hooks append edited paths to `.claude/state/kg-pending`.
- Nothing here blocks a reply. The **Stop** and **SessionStart** hooks delete nodes whose sources are all gone and rebuild the index, with no model call.
- When the pending set is significant, or a node is older than `knowledge.max_age_days`, the main session gets a note starting `[harness:kg]` (on a prompt, after an edit, or when a subagent returns; at most one every 20 minutes, never sent to a subagent). It starts **knowledge-keeper** in the background (`run_in_background: true`) and carries on. The keeper:
  1. Runs `kg.py pending` and `kg.py stale` to see the changed files, the nodes that reference them, and the aged nodes.
  2. Updates or creates nodes (major info only), which stamps `updated` + `verified_commit` (`git rev-parse --short HEAD`, or `none`), and deletes every node it cannot re-verify.
  3. Runs `kg.py index && kg.py validate`.
  4. Runs `kg.py pending --clear`.

## ⏳ Context budget protocol

The hook `context_guard.py` measures each agent's own context usage after every tool call.

**Hook messages**, which are injected as context:
- `[harness:context] WARN NN%`: at or above `warn_pct` (70). Finish the current unit of work. No large reads; delegate exploration.
- `[harness:context] LIMIT NN%`: at or above `limit_pct` (75). **Stop work now.** Save a handoff note with `save.py handoff <agent>`, then end your turn with the return block `STATUS: HANDOFF <path>`. For a subagent the message repeats on every tool call; the main session is reminded every 25th call. It is an instruction, not a hard stop; auto-compact is the backstop.

**Handoff file** `.claude/knowledge/handoffs/<agent>-<YYYYmmdd-HHMMSS>.md` (written by `save.py handoff`):

```markdown
---
agent: coding-agent
created: 2026-10-24T22:40:00
context_pct: 51
task: <one line>
---
## Goal
## Done
- [x] ... → path:line
## Remaining
- [ ] ...
## Key pointers
- path:line: why it matters; KG ids: module-x, decision-y
## Decisions made
## Open questions
## Next first step
```

**Respawn.** A PostToolUse hook on the `Agent` tool detects `STATUS: HANDOFF <path>` in a subagent's result and injects `[harness:handoff] ...`. The parent must then spawn a **fresh agent of the same type** with this prompt: `Resume from handoff <path>. Read it first, then only the KG nodes/pointers it lists. Continue from "Next first step".`

**Main session:** at LIMIT, write a handoff, then delegate the remainder to a fresh `orchestrator` subagent with that handoff. If that isn't possible, tell the user to run `/clear` then `/resume-handoff <path>`. Auto-compact at 85% (`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=85`) is the backstop.

## 📋 Agent return block (every agent ends with this)

```
STATUS: DONE | HANDOFF <path> | BLOCKED <reason>
SUMMARY: <1-3 lines, outcome first; name anything unverified>
CHANGES:
- path: what changed
POINTERS:
- path:line: why relevant
KG: <updated ids | pending | none | gap <topic>>
NEXT: <recommended next step or agent>
```

## 🔒 Security protocol
- Hooks block raw secrets in prompts, reads, writes, Bash commands, web and MCP calls, and mask them in tool output. Personal data blocks too; emails and phone numbers only in bulk (`scan.pii_bulk_threshold`), or on web and MCP calls. Block messages start with `[harness:security]` and name the detector, never the value.
- On a block, never try to work around it with another tool, encoding, or path trick. Use `/safe-read <path>` (that is, `python3 .claude/tools/safe_read.py <path>`) to see the structure with values masked, or ask the user.
- Never ask the user to paste secrets. Reference env var **names** only.
- `.claude/security/allowlist` is human-edited only. Its format:
  - `path:<glob>` (or a bare glob): skip detectors for matching files.
  - `sha256:<hex>`: allow one specific matched value.
  - `detector:<name>`: disable a detector project-wide, e.g. `detector:pii.email`.

**Detector names:**
- `secret.private_key`
- `secret.aws_access_key`, `secret.aws_secret_key`
- `secret.github_token`, `secret.gitlab_token`
- `secret.slack_token`, `secret.slack_webhook`
- `secret.stripe_key`, `secret.google_api_key`, `secret.anthropic_key`, `secret.openai_key`, `secret.npm_token`, `secret.sendgrid_key`
- `secret.azure_storage_key`, `secret.azure_sas_token`, `secret.azure_client_secret`
- `secret.vault_token`, `secret.terraform_cloud_token`, `secret.docker_auth`, `secret.bearer_token`
- `secret.pypi_token`, `secret.twilio_key`, `secret.mailgun_key`, `secret.age_key`, `secret.grafana_token`
- `secret.jwt`, `secret.connection_string`, `secret.generic_assignment`
- `pii.email`, `pii.phone`, `pii.ssn`, `pii.sin`, `pii.credit_card`, `pii.iban`

## 🔀 Git policy
- **Allowed:** `status`, `diff`, `log`, `show`, `add`, `commit` (after the compliance PASS), `branch` (not `-D`), `switch`, `checkout -b`, `stash list/push`.
- **Ask:** `push`, `rebase`, `commit --amend`, `merge`, `cherry-pick`, `revert`, `am`, `tag`, dependency installs.
- **Denied:** `commit --no-verify`, `commit -a`, commits with file arguments, `commit-tree`, force-push, `reset --hard`, `clean -f`, `branch -D`, `checkout .`/`restore .` (discard everything; a single file asks), `filter-branch`, `update-ref -d`, `stash drop/clear`, `reflog expire`.

## 🧾 Compliance gate protocol

- **Verdict line.** The `compliance-reviewer` agent ends its result with exactly one of `COMPLIANCE: PASS <id>` or `COMPLIANCE: FAIL`, where `<id>` is the 16-character output of `compliance.py id`. A result with no verdict, or with both, is not a PASS.
- **Recording.** `guard_compliance.py` (PostToolUse on `Agent|Task`) acts only when `tool_input.subagent_type` is `compliance-reviewer`. On PASS it recomputes the id of the staged changes and writes `.claude/state/compliance-pass.json` (`id`, `changes`: path to staged blob id, `actor`, `ts`) only when the ids match. On FAIL it deletes the record. The `## Compliance note` section of the result is saved to `compliance-note.md`.
- **Check.** For `git commit`, `guard_bash.py` requires every path in `git diff --cached HEAD` to appear in `changes` with the same blob id (a deletion is recorded as `null`). The command must stand alone, from the project root, with no `-a`, `--include`, `--only`, `--patch` or file arguments.
- **Audit log.** `.claude/state/compliance-log.jsonl`, one JSON object per line: `ts` (UTC), `actor`, `action` (`review` or `commit-check`), `target` (the id), `outcome` (`pass`, `fail`, `pass-stale`, `no-verdict`, `allowed`, `denied`). No file content. Rotated at 5 MB to `compliance-log.jsonl.1`; one older file is kept.
- **Config.** `compliance.commit_gate` in `harness.json`: `block` (default). `off` disables the check and makes `doctor.py` fail.

## 🤖 Models
- Haiku (`haiku`): basic and mechanical work.
- Sonnet (`sonnet`): documentation and diagrams.
- Opus (`opus`): coding, code review and supervision.
- Fable (`fable`): the security and compliance reviews. Fallback: `fallbackModel` in `settings.json` is `["opus"]`, so a request Fable cannot serve moves to Opus; the spawning agent also retries a Fable agent that cannot start with `opus`.
- Aliases always resolve to the latest model in each tier. A `[1m]` suffix (`opus[1m]`) asks for the 1M-token context window.
