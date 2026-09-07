# Contributing to RepoManager

RepoManager 0.1.0 development is supported on Windows 10 and Windows 11 with
Python 3.14 and Git. CI and release verification use normal 64-bit CPython
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

Run the complete suite from the repository root:

```text
py -3.14 -B -m unittest discover -s tests -v
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
