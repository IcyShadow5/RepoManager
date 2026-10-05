# Contributing to RepoManager

The current Qt candidate is developed on Windows 10 and Windows 11 with
Python 3.14, PySide6 and Git. CI and release verification use normal 64-bit CPython
3.14.7.

## Before submitting a change

- Keep changes focused on one problem and preserve existing public contracts.
- Add or update regression tests when behavior changes.
- Do not commit credentials, local application data, build output, logs, or
  repository-specific private paths.
- Do not weaken validation, confirmation, process, path, or Git safety checks
  to make a test pass.
- Update documentation when user-visible behavior, configuration, supported
  platforms, or build procedures change.

Install `packaging/requirements-qt.txt` in a local environment. Run the current
product gate and the legacy reference separately from the repository root:

```text
python -B -m tests.run_layers --layer current
python -B -m tests.run_layers --layer legacy
```

Then check the patch for whitespace errors:

```text
git diff --check
```

Pull requests should explain the problem, the bounded change, relevant risks,
and the checks that actually completed. Distinguish source inspection, test
evidence, and direct runtime verification. Report skipped or unavailable checks
rather than presenting them as successful.

Security vulnerabilities should follow [SECURITY.md](SECURITY.md), not a public
issue.
