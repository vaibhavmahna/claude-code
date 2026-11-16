## Summary

<!-- What changes and why. Link the issue it fixes, for example "Fixes #123". -->

## Type of change

- [ ] Fix
- [ ] Feature
- [ ] Docs
- [ ] Refactor
- [ ] CI or chore
- [ ] Breaking change (explain the impact and the upgrade path in the Summary)

## Checklist

- [ ] `python3 -m unittest discover -s tests` passes locally.
- [ ] New behavior has tests.
- [ ] Docs are updated (README, `docs/`, `docs/contracts.md`, as needed).
- [ ] `CHANGELOG.md` has an entry under `[Unreleased]`.
- [ ] No literal secrets or PII in code, fixtures or docs (fake values are built at runtime, as in `tests/helpers.py`).
- [ ] `doc_format.py check` passes for the docs I changed (see CONTRIBUTING.md for which files it covers).
- [ ] Commit messages follow Conventional Commits.

## Notes for reviewers

<!-- Optional: how you tried it (for example an install into a throwaway project plus doctor.py, or evals you ran and their cost), and anything you are unsure about. -->
