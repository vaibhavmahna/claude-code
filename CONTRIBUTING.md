# 📦 Contributing to Claude Code Harness

Claude Code Harness installs a Claude Code project harness (agents, skills, security hooks, a knowledge graph and a context budget) into a project with one command. This guide is for a first-time contributor who wants to change the harness and open a pull request.

<!-- toc -->
## 📑 Contents

- [Prerequisites](#-prerequisites)
- [Repo layout](#-repo-layout)
- [Run the unit tests](#-run-the-unit-tests)
- [Try a change end to end](#-try-a-change-end-to-end)
- [Run the evals](#-run-the-evals)
- [Add to the harness](#-add-to-the-harness)
  - [Add a detector](#-add-a-detector)
  - [Add a guard rule](#-add-a-guard-rule)
  - [Add an agent](#-add-an-agent)
  - [Add a skill](#-add-a-skill)
- [Keep literal secrets out of the repo](#-keep-literal-secrets-out-of-the-repo)
- [Write documentation](#-write-documentation)
- [Write commit messages](#-write-commit-messages)
- [Open a pull request](#-open-a-pull-request)
- [Release process](#-release-process)
- [Security and conduct](#-security-and-conduct)

<!-- /toc -->

## 🧭 Prerequisites

- Python 3.9 or newer. `install.py` exits with an error on older versions.
- Git.
- Nothing else. The installer, the hooks, the tools and the tests use only the standard library, and there is no `pyproject.toml` or `requirements.txt`. Do not add a dependency without opening an issue first.
- Claude Code, only if you want to run the evals (see [Run the evals](#-run-the-evals)).

## 📂 Repo layout

| Path | Contents |
|---|---|
| `install.py` | The single entry point: `python3 install.py DIR="/path/to/project"`. It copies the template into the target project and merges settings. |
| `template/` | An exact mirror of what lands in a project: `claude/` becomes `<project>/.claude/`, and `AGENTS.md` and `CLAUDE.md.tmpl` become the project's `AGENTS.md` and `CLAUDE.md`. Edit the harness here. |
| `docs/` | `contracts.md` (shared conventions) and `diagrams/`. |
| `tests/` | The `unittest` suite. |
| `evals/` | Behavioral evals that run real headless Claude sessions. See `evals/README.md`. |
| `scripts/` | Maintainer scripts. `gen_diagram_templates.py` regenerates the diagram-maker SVG templates. |
| `.github/` | CI workflows, issue templates and the pull request template. |

All harness changes go in `template/`. Never edit an installed `.claude/`, `CLAUDE.md` or `AGENTS.md` in a project, because they are generated from the template. The installer refuses to install into this repo, so use a separate directory to try changes.

Shared conventions (installed paths, KG node format, handoff schema, return block, detector names) live in `docs/contracts.md`. Read the section that matches what you are changing before you start.

## 🧪 Run the unit tests

```bash
python3 -m unittest discover -s tests
```

The suite takes about 20 seconds and needs no network and no API key. The tests build their own temporary projects and runs the real hooks as subprocesses. Run this before every push, and add tests for any behavior you change.

To run one file or one test:

```bash
python3 -m unittest discover -s tests -p "test_guards.py"
python3 -m unittest discover -s tests -k TestBashGuard
```

## ⚡ Try a change end to end

The unit tests do not replace a real install. Put your change in a throwaway project and check it with the harness's own doctor:

```bash
TRY="$(mktemp -d)"
git init -q "$TRY"
python3 install.py DIR="$TRY"
python3 "$TRY/.claude/tools/doctor.py" --root "$TRY"
```

`doctor.py` checks the installed settings, hooks, agent control contract and prompt budgets. Pass `--root`, because inside a Claude Code session it otherwise defaults to `$CLAUDE_PROJECT_DIR` and checks the wrong project. The bar for a PR is exit code 0 and no `FAIL` lines. A `WARN` about `@file` mentions is expected unless you installed with `--user-env`.

Useful installer options:

| Option | Effect |
|---|---|
| `--dry-run` | Show actions without writing. |
| `--force` | Overwrite locally modified harness files (backups are kept). Locked security files are restored even without it. |
| `--check` | Exit 1 if locked security files, harness deny rules or harness hooks were changed. For CI. |
| `--codeowners` | Owners for the locked files in a marked CODEOWNERS block. Defaults to `codeowners` in `harness.json`. |
| `--uninstall` | Remove unmodified harness files and the harness rules in settings. KG nodes are kept. |
| `--no-seed` | Do not seed the knowledge graph. |
| `--no-sandbox` | Omit the Bash sandbox settings. |
| `--git-hook` | Also install a git `pre-commit` hook that applies the compliance gate to commits typed in a terminal. |
| `--user-env` | Also set `CLAUDE_CODE_DISABLE_FILE_MENTIONS=1` in `~/.claude/settings.json`. |

Run the install a second time to confirm it is idempotent. `tests/test_installer.py` covers install, merge, upgrade, locked files, `--check`, CODEOWNERS, uninstall and refusal cases. Extend it when you change installer behavior.

Delete the temporary directory when you are done: `rm -r "$TRY"`.

## 🎯 Run the evals

The evals start real, headless Claude Code sessions against a fixture project and grade the result with fixed checks. They cost money, so CI does not run them and the unit tests do not need them.

Run them when you change agent prompts, skills, hooks, `settings.json` or the models the agents use. Otherwise skip them.

Start with a free check, then one cheap scenario:

```bash
python3 evals/run_evals.py --dry-run
python3 evals/run_evals.py --list
python3 evals/run_evals.py --only secret-stays-secret --model haiku
```

`--dry-run` builds the projects and prints the commands without spending anything. Each scenario has its own spending cap, and `--total-budget-usd` stops the whole run at a limit you choose. According to `evals/README.md`, a full run costs a few dollars with Sonnet as the main model, and the two secret scenarios cost under a cent each on Haiku.

A single failing scenario is a signal, not proof, because model behavior varies. Run it two or three times and read the transcript (`--keep` keeps the projects) before you change a prompt. If you ran evals for your PR, say which scenarios and the result in the PR description. Full flags and the scenario list are in `evals/README.md`.

## 🧩 Add to the harness

Every addition needs a test. Say in the PR which test fails without your change.

### 🔍 Add a detector

Detectors find secrets and PII in prompts, files and tool output.

1. Add a `Rule("secret.<name>", _r(r"..."), ...)` to `SECRET_RULES`, or `Rule("pii.<name>", _r(r"..."), ...)` to `PII_RULES`, in `template/claude/hooks/lib/detectors.py`. Use the optional validator argument (for example `_card_ok`, `_email_ok`) to cut false positives. `ALL_DETECTORS` is built from the rules, so there is no list to update.
2. Add a fake value for it to `FAKE` in `tests/helpers.py`, assembled from string pieces at runtime (see [Keep literal secrets out of the repo](#-keep-literal-secrets-out-of-the-repo)).
3. In `tests/test_detectors.py`, add the case to `TestSecrets.test_true_positives` (for PII, `TestPII.test_pii_detected`) and add look-alike values that must not match to `test_false_positives`.
4. Add the name to the detector list in `docs/contracts.md` (the "Detector names" block).
5. Run the full unit tests. `TestTemplateHygiene.test_no_false_positives_in_template` in `tests/test_installer.py` scans every file under `template/` and fails if your rule flags the harness's own docs or code.

### 🔒 Add a guard rule

Guards decide whether a tool call is allowed, needs a user prompt, or is denied. The Bash guard is `template/claude/hooks/guard_bash.py`. Command rules are in `cmd_rules`, git rules in `git_rules`, and path rules in `path_rules`. The other guards are `guard_read.py`, `guard_write.py`, `guard_egress.py` and `guard_prompt.py`.

1. Add the rule in the matching guard. Keep the denial message free of the matched value, since block messages name the detector or rule and never the secret.
2. Add cases to `tests/test_guards.py`. For Bash, add rows to `BASH_CASES`, each a `(command, expected)` pair where `expected` is `None` (allow), `"ask"` or `"deny"`. Add at least one allowed case next to every denied one, so the rule does not over-block. For the file guards, extend `TestFileGuards`.
3. If the rule changes what users see, update the Git policy or security sections in `docs/contracts.md` and `template/CLAUDE.md.tmpl` to match.
4. The compliance commit gate (`commit_gate` in `guard_bash.py`, `guard_compliance.py`, `tools/compliance.py`) is a security control. A change that loosens it needs the security owners' review, a row in `tests/test_compliance.py` for every new allowed form, and a line in the pull request description saying what changed.

### 🤖 Add an agent

1. Create `template/claude/agents/<name>.md`. The frontmatter needs `name` (the same as the file name), `description` and `model` (`haiku`, `sonnet`, `opus` or `fable`, per the tiers in `docs/contracts.md`). Copy a small existing agent such as `lint-fixer.md` as a starting point.
2. The body must contain the return block (`STATUS:`, per `docs/contracts.md`, "Agent return block") and the context-budget handoff rule (`[harness:context] LIMIT`). It must also include the word `HANDOFF`, which agents return as `STATUS: HANDOFF <path>`. The check lives in `check_contracts` in `template/claude/tools/doctor.py`.
3. Stay inside the prompt budgets in `template/claude/harness.json` (`prompt_budgets`: 6000 characters per agent, with per-file overrides for `doc-writer.md` and `orchestrator.md`). The always-loaded budget also counts `CLAUDE.md.tmpl` and `AGENTS.md`.
4. List the agent in the roster in `template/AGENTS.md` (section 1), and in its routing and pipelines if it is part of one. An agent that writes or reviews work must name the `data-handling-compliance` skill (`test_every_agent_that_changes_or_reviews_work_names_the_skill` in `tests/test_compliance.py`).
5. Add it to the expected model tiers in `tests/test_installer.py` (`test_agent_models`) and raise the agent count in `test_install_merge_idempotent_uninstall`. If it lists skills in a `skills: [...]` line, each one must exist under `template/claude/skills/`.
6. Run `tests/test_contracts.py`, which checks that the template stays within the contract and budgets, and run the doctor in a throwaway install.

### 🧠 Add a skill

1. Create `template/claude/skills/<name>/SKILL.md`. The frontmatter needs `name` (the same as the folder) and a `description` that says when to use the skill.
2. Keep `SKILL.md` under the `skill_chars` budget (8500 characters at the time of writing, see `harness.json`) and move detail into `references/*.md`, which load only when needed. `data-handling-compliance` has a per-file override because its text is owned by Security and Legal and ships unedited; change its rules only with their sign-off, and keep `test_skill_and_references_ship` passing.
3. Skills that agents rely on are listed in the agent's `skills: [...]` frontmatter line. A listed skill that does not exist fails `test_agent_skills_exist`.
4. If your skill ships SVG templates, edit `scripts/gen_diagram_templates.py` and run `python3 scripts/gen_diagram_templates.py`. Never edit the generated SVGs by hand, because the tests fail if they drift from the generator.
5. Run the unit tests and the doctor in a throwaway install. `doc-writer.md` and the `doc-standards` files must not contain em-dashes (`test_doc_voice_rules`).

## 🔒 Keep literal secrets out of the repo

The harness blocks and scans for secrets and PII, so a secret-shaped value in this repo trips the harness's own hooks, scanners and tests, and it could be mistaken for a real leak.

- Never commit a literal secret-shaped value, even a fake one, in code, tests, fixtures, docs or commit messages.
- In tests, build fake credentials at runtime from string pieces, the way `FAKE` does in `tests/helpers.py`. Add new ones there.
- In docs and examples, use placeholders: `<API_KEY>`, `user@example.com`, and environment variable names such as `GITHUB_TOKEN` (the name, not a value).
- Use `example.com` for domains and obviously non-real data for personal information.

If you think you found a real leaked credential, do not open an issue. Follow [SECURITY.md](SECURITY.md).

## 📖 Write documentation

Project docs follow the format enforced by `doc_format.py`. That means the files CI checks (listed below) and any new doc you add under `docs/`. These are exempt: agent and skill definitions under `template/claude/agents` and `skills`, `CHANGELOG.md`, the `.github` templates, and knowledge graph nodes and handoffs.

- Every heading starts with exactly one emoji from the allowed palette, then a space.
- No emoji anywhere else (body text, lists, tables, the table of contents).
- One h1, and no skipped heading levels.
- A linked table of contents between `<!-- toc -->` and `<!-- /toc -->`.

Generate and check it with the tool instead of editing the table of contents by hand:

```bash
python3 template/claude/tools/doc_format.py palette
python3 template/claude/tools/doc_format.py toc docs/my-doc.md
python3 template/claude/tools/doc_format.py check docs/my-doc.md
```

CI runs `check` on `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `docs/*.md` and `evals/README.md`. Run `toc` again after any heading change.

Diagrams in `docs/diagrams/` are SVG and are linted in CI:

```bash
python3 template/claude/tools/svg_lint.py docs/diagrams/<file>.svg
```

Style: short sentences, plain words, second person for instructions. Do not use em-dashes, and do not describe behavior the code does not have. Link to source files instead of copying values that will drift. If you cannot verify a claim, write `TODO(verify): ...` in the draft and say so in the PR.

## 📝 Write commit messages

Use [Conventional Commits](https://www.conventionalcommits.org): `<type>: <summary>`, with an optional scope, for example `feat(guard): block terraform destroy`.

| Type | Use for |
|---|---|
| `feat` | New behavior |
| `fix` | A bug fix |
| `docs` | Documentation only |
| `refactor` | A change with no behavior change |
| `test` | Tests only |
| `ci` or `chore` | CI, tooling, housekeeping |

Mark a breaking change with `!` after the type (`feat!: ...`) and a `BREAKING CHANGE:` line in the body. Write the summary in the imperative, under about 70 characters. Do not use `--no-verify`.

## 🔀 Open a pull request

1. Fork the repo and clone your fork.
2. Create a branch from `main`, for example `git switch -c fix/guard-find-delete`.
3. Make the change in `template/` (or the matching place), with tests.
4. Run `python3 -m unittest discover -s tests`, and try the change in a throwaway install with the doctor.
5. Run `doc_format.py check` on every doc you touched (see [Write documentation](#-write-documentation) for which files it applies to).
6. Add a line under `[Unreleased]` in `CHANGELOG.md`, in the Keep a Changelog section that fits (Added, Changed, Deprecated, Removed, Fixed or Security). Write it from the user's point of view.
7. Push the branch and open a pull request against `main`. Fill in the pull request template (`.github/PULL_REQUEST_TEMPLATE.md`).
8. CI runs the unit tests on Ubuntu and macOS with Python 3.9 and 3.13, the doc format and SVG checks, and an installer smoke test (install into a temporary git repo, then run its `doctor.py`). Fix any failure before asking for review.
9. A maintainer reviews. Keep each PR to one concern, and push follow-up commits instead of force-pushing during review so the discussion stays readable.

Open an issue first for large changes, such as a new dependency, a new top-level folder, or a change to the contracts in `docs/contracts.md`.

## 📦 Release process

Maintainers cut releases. Contributors only need to keep `[Unreleased]` current.

1. Bump `version` in `template/claude/harness.json`, and the `harness` badge at the top of `README.md` to match. The banner in `docs/assets/banner.svg` states the agent and skill counts; update it if they changed.
2. In `CHANGELOG.md`, move the entries under `[Unreleased]` to a new heading for the version with the release date, and leave an empty `[Unreleased]` section.
3. Open a PR with those two changes and merge it after CI passes.
4. After the merge, a maintainer tags the merge commit as `v<version>` (for example `v1.0.0`) and pushes the tag.

## 🔒 Security and conduct

- Report vulnerabilities through private reporting, never in a public issue. See [SECURITY.md](SECURITY.md).
- Everyone taking part in this project follows the [Code of Conduct](CODE_OF_CONDUCT.md).
- Keep customer data, credentials and open security findings out of contributions.
