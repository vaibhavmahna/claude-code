# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-11-15

### Added

- `data-handling-compliance` skill: GDPR, SOC 2 and health-data rules for personal data, customer data and AI features, in BLOCK / REQUIRE / FLAG tiers, with one reference file per category and the PR compliance note.
- `compliance-reviewer` agent (Fable, read-only), which reviews the staged files and returns `COMPLIANCE: PASS` or `FAIL`.
- Compliance commit gate: `git commit` is denied until the compliance-reviewer has passed exactly the staged files. `guard_compliance.py` records the verdict, `guard_bash.py` checks it, and `tools/compliance.py` reports it. Every verdict and commit check is written to `.claude/state/compliance-log.jsonl`, rotated at 5 MB.
- `gh pr create` is denied unless the body carries the `## Compliance note` section.
- `install.py --git-hook`: an optional git `pre-commit` hook that applies the same gate to commits typed in a terminal.
- `/harness-doctor` fails when the gate is not `block` or the compliance-reviewer agent is missing.
- `knowledge.max_age_days` (30): knowledge-graph nodes nobody has re-verified are listed for the keeper to re-verify or delete.
- A `[1m]` model suffix is accepted in agent frontmatter.
- README banner (`docs/assets/banner.svg`). The README's reference sections moved to `docs/security.md` and `docs/configuration.md`.
- Windows guidance: run inside WSL2, a dev container or a Codespace.
- Eval scenarios for the gate, a docs-only commit, personal data in logs, a new data store, and a blocked training-set request.

### Changed

- Models: security-auditor moves from Opus to Fable, and the new compliance-reviewer runs on Fable. Orchestrator, code-reviewer and the coding agents stay on Opus, documentation agents on Sonnet, helpers on Haiku. Where Fable is unavailable, requests fall back to Opus (`fallbackModel` in `settings.json`, plus a rule in `AGENTS.md`).
- Every agent that writes or reviews work is told to read `data-handling-compliance` first when a change touches data about people, customer content or an AI feature. `CLAUDE.md` gains a Compliance section and the Definition of done requires the PASS.
- Context budget: warning at 70% (was 40%), hand-off at 75% (was 50%), auto-compact at 85% (was 50%). The main session is reminded at the limit every 25th tool call (was every 10th).
- Knowledge-graph upkeep no longer blocks the end of a turn. Hooks delete nodes whose sources are gone, and the refresh runs on knowledge-keeper in the background.
- `git commit --no-verify` is denied (it asked before). `git commit -a`, commits with file arguments, `git commit-tree` and `git add … && git commit` on one line are denied. `git merge`, `cherry-pick`, `revert` and `am` ask first.
- Prompt budgets: `always_loaded_chars` is 19500, with per-file overrides for `orchestrator.md` and the compliance skill.
