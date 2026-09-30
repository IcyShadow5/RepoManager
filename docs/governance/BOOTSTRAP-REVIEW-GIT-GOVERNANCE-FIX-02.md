# FINAL SELF-PROTECTION BOOTSTRAP REVIEW

Independent, read-only review of pull request #2
(`fix/governance-policy-hardening` -> `main`) against
GIT-GOVERNANCE-FIX-02, performed **before** merge, while the required check
that guards this pull request is still the previous and known-vulnerable
revision.

Reviewer: the agent executing the change request, working against the diff
itself rather than against the intent it was written from.

---

## 1. Why an exception is structurally unavoidable

`pull_request_target` fires **only** for workflow files that already exist on
the base branch. A pull request that introduces a `pull_request_target` workflow
therefore cannot have that workflow evaluate it.

Symmetrically, for a `pull_request` event GitHub reads the workflow definition
from the pull request head. This pull request *replaces* the `pull_request`
trigger, so the `pull_request`-triggered run disappears along with the old
workflow.

Consequence: **no sequence of pull requests can install this fix through the
fix's own gate.** The old check must be bypassed exactly once. That is the
bootstrap exception this review covers, and it is the last one.

Verified empirically, not assumed:

```
$ gh run list --branch fix/governance-policy-hardening
dependency audit[pull_request]=completed/success
tests[pull_request]=completed/success
tests[push]=completed/success
(no attribution-policy run)
```

---

## 2. The exception is bounded, and the window is closed

| Property | Finding |
|---|---|
| Scope | one pull request, `fix/governance-policy-hardening` -> `main` |
| Merged content | `.github/` governance files only; **zero product-code lines** |
| Number of bypasses | exactly one, recorded in the ruleset bypass audit log |
| Effect of the bypass | skips a *known-vulnerable* check; it does not weaken the ruleset |
| Post-merge state | `pull_request_target` is on `main`, so every later pull request is judged by the trusted base policy |

The bypass is used to install a *stronger* gate, never to land unreviewed work.

---

## 3. Diff containment

```
$ git diff --cached --stat a95891cc207b527d0e08a454583fd69e60f1a60d -- . ':(exclude).github'
(empty)
```

- product baseline `a95891c` -> branch touches **nothing** outside `.github/`
- changed paths: 6 under `.github/policy/`, 1 under `.github/tests/`,
  1 workflow
- `__pycache__` is covered by the pre-existing `.gitignore`; no artefact staged
- no release tag, no `packaging/`, no `repo_manager/`, no `tests/` (product tests)
- Dependabot PR #1 untouched

---

## 4. Line-by-line check of the four critical requirements

### S1 trust model

| Requirement | Status | Evidence |
|---|---|---|
| workflow from base branch | OK | `on: pull_request_target` |
| no checkout of PR branch | OK | no `refs/pull/`, no `head.sha` anywhere |
| no execution of PR code | OK | `pr_gate.py` contains no `subprocess`, `os.system`, `eval`, `exec`, `compile` |
| minimal permissions | OK | `contents: read`, `pull-requests: read`; no write, no `id-token` |
| no secrets | OK | only `${{ github.token }}`; no `secrets.` |
| no unquoted PR data in shell | OK | PR values reach Python via `env:`, never via shell interpolation |
| base commit only | OK | `ref: ${{ github.event.pull_request.base.sha \|\| github.event.before }}` |

Adversarial reading: could an attacker influence the run through the
concurrency group or the `if:` conditions? `group` interpolates
`github.event.pull_request.number`, which is an integer for a real pull request
and empty otherwise; it only affects grouping, never the checkout or the
judged input. The `if:` conditions compare against fixed literals. No path
leads to a PR-controlled checkout.

### S3 skip removal

The naive check (string absent) is satisfied, but the meaningful check is that
no *executable* reference survives, because the documentation deliberately
mentions the removal. `TestSkipRemoved._executable_references` parses each
module and inspects every string constant that is not a docstring, every
`Name` and every `Attribute`. It passes, and a companion test proves that
setting `ICY_GOVERNANCE_SKIP=1` changes nothing on either the local or the
server path.

### S4 fail closed

Every documented condition is covered by a test that asserts a **non-zero**
exit: API error, malformed response (four shapes), empty list, missing field,
missing sha, unexpected exception, missing token, non-numeric PR number,
malformed repository slug, missing pull-request body, traversal-shaped ref, and
the local `pre-push` error path. The `||` fallback string and the
`HEAD_SHA~1` construct are asserted absent from the workflow.

### S8 no prose false positives

Five prose cases are asserted committable, and the same cases are asserted
*not* to match the RE2 layer, so the two layers agree. Structured attribution
is asserted blocked in eight shapes. This is the requirement most at risk of
over-correction in the other direction, so it was checked in both directions.

---

## 5. Defects found by this review and fixed before merge

Found by the new tests during development, not in review, but recorded because
they would have been real defects in the gate:

1. **Structural matching ran on the raw line.** A zero-width character inside a
   trailer key or a banner made the line structurally unrecognisable, so the
   Unicode fix would have been defeated by the very characters it targets.
   Structural matching now runs on the normalised line while the reported line
   stays the original.
2. **Ref validation accepted `../../etc/passwd`.** The character class permits
   dots and slashes, so a traversal-shaped ref reached the network. Now
   rejected explicitly.
3. **Printing a violation could lose the verdict.** A violating message
   contains characters a narrow console encoding cannot represent;
   `UnicodeEncodeError` mid-report would have replaced a clean rejection with a
   traceback. Both entry points now reconfigure their streams with
   `errors="replace"`.
4. **The workflow's sparse checkout excluded the tests**, so the self-test
   could not find its own suite and would have failed the gate on every run.
5. An `IndentationError` in the fence parser and a missing `unittest.TestCase`
   base on all 11 new test classes — both caught before any push.

---

## 6. Live verification against the real API, before merge

The new gate was executed locally against the real pull request, with the real
API and a real read-only token:

```
$ GITHUB_REPOSITORY=IcyShadow5/RepoManager PR_NUMBER=2 python .github/policy/pr_gate.py check-pr
GIT-GOVERNANCE-FIX-02 v2.0.0: 1 commit(s) checked, clean.                     exit 0

$ ... python .github/policy/pr_gate.py check-governed-paths
::warning::This pull request modifies the attribution gate itself:
::warning::  .github/policy/attribution_policy.py
  ... (8 paths)
Owner authorisation marker present (ICY-GOVERNANCE-POLICY-CHANGE: AUTHORIZED). exit 0

$ ... GITHUB_TOKEN= python .github/policy/pr_gate.py check-pr
GIT-GOVERNANCE-FIX-02 v2.0.0: FAILED CLOSED -- GITHUB_TOKEN is not set       exit 1
```

So the replacement gate was observed to work against live data, and to refuse
to run rather than pass when it cannot. This is the evidence that makes the
single bypass acceptable: what is being installed has already been seen to
function, and has been seen to fail closed.

---

## 7. Verdict

| # | Requirement | Status |
|---|---|---|
| S1 | trust model | fixed, verified statically and by test |
| S2 | every PR commit, five fields, paginated | fixed, tested |
| S3 | `ICY_GOVERNANCE_SKIP` removed | fixed, tested |
| S4 | fail closed, no `||` fallback | fixed, tested |
| S5 | push path decision documented, full range | fixed, documented |
| S6 | Unicode NFKC / casefold / Cf / whitespace | fixed, tested, limits stated |
| S7 | subdomain email matching | fixed, tested |
| S8 | prose false positive | fixed, tested both directions |
| S9 | policy self-protection | mechanism in place; owner-only gate **not** claimed |
| S10 | tests | 23 kept, 85 added, 108 total |
| S11 | CI runtime | pinned 3.12, exercised on 3.12/3.13/3.14 |
| S12 | branch -> PR, no direct push | complied |
| S13 | bootstrap review | this document |
| S15 | ruleset not weakened | complied; no change needed before merge |
| S16 | Dependabot PR #1 | untouched |

No unresolved finding. No requirement silently skipped. No product code
changed. No release tag changed.

**Review outcome: the single bootstrap bypass is justified and bounded. Merge
may proceed.**
