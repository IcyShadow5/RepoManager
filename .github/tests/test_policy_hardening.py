"""
GIT-GOVERNANCE-FIX-02 : hardening test set.

Covers the weaknesses found in REPOMANAGER-GOVERNANCE-REVIEW-03E plus the
Unicode, email-matching, false-positive and skip-removal work.

These tests exercise the policy components directly. They deliberately use no
environment bypass, because none exists any more.

Invisible characters are written as explicit escapes rather than literals, so
the file survives any editor or encoding round-trip.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
import unittest
import urllib.request
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
POLICY_DIR = os.path.abspath(os.path.join(_HERE, "..", "policy"))
WORKFLOW = os.path.join(POLICY_DIR, "..", "workflows", "attribution-policy.yml")
for _p in (os.path.join(_HERE, "..", "policy"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import attribution_policy as P  # noqa: E402
import github_api  # noqa: E402
import hook_entry  # noqa: E402
import pr_gate  # noqa: E402

ZWSP = "\u200b"          # ZERO WIDTH SPACE
SOFT_HYPHEN = "\u00ad"   # SOFT HYPHEN
ZWNJ = "\u200c"          # ZERO WIDTH NON-JOINER
WJ = "\u2060"            # WORD JOINER
BOM = "\ufeff"          # ZERO WIDTH NO-BREAK SPACE
NBSP = "\u00a0"          # NO-BREAK SPACE
IDEOGRAPHIC_SPACE = "\u3000"
FULLWIDTH_CODEBUFF = "\uff23\uff4f\uff44\uff45\uff42\uff55\uff46\uff46"
COMBINING_ACUTE = "\u0301"

OWNER_ENV = {"GITHUB_REPOSITORY": "IcyShadow5/RepoManager", "GITHUB_TOKEN": "t", "PR_NUMBER": "7"}


def _commit(sha, message, an="Icy Shadow", ae="r@example.com", cn="Icy Shadow", ce="r@example.com"):
    return {
        "sha": sha,
        "commit": {
            "message": message,
            "author": {"name": an, "email": ae},
            "committer": {"name": cn, "email": ce},
        },
    }


def _env(**overrides):
    env = dict(OWNER_ENV)
    env.update(overrides)
    return mock.patch.dict(os.environ, env, clear=False)


def _to_python(pattern: str) -> str:
    """Bridge the RE2 / Python spelling difference.

    RE2 writes a code point as ``\\x{1F916}`` and end-of-text as ``\\z``.
    Python spells the first ``\\U0001F916`` and, on 3.12, rejects ``\\z``
    outright (3.14 accepts it). Bridging both here is what lets this file
    assert RE2 conformance on whatever interpreter runs it.
    """
    pattern = re.sub(
        r"\\x\{([0-9A-Fa-f]{1,6})\}",
        lambda m: f"\\U{int(m.group(1), 16):08X}",
        pattern,
    )
    return pattern.replace(r"\z", r"\Z")


def _re2(pattern: str):
    return re.compile(_to_python(pattern), re.IGNORECASE | re.MULTILINE)


class TestUnicodeNormalisation(unittest.TestCase):
    """FIX-02 section 6."""

    def test_invisible_and_combining_variants_collapse_onto_one_token(self):
        """Removing the character must leave the token whole."""
        cases = {
            "zero width space": f"Code{ZWSP}buff",
            "soft hyphen": f"Code{SOFT_HYPHEN}buff",
            "zwnj": f"Code{ZWNJ}buff",
            "word joiner": f"Code{WJ}buff",
            "bom": f"Code{BOM}buff",
            "fullwidth": FULLWIDTH_CODEBUFF,
            "combining acute": f"Codebuff{COMBINING_ACUTE}",
        }
        for label, raw in cases.items():
            with self.subTest(variant=label):
                self.assertTrue(P.is_agent_name(raw), f"{label} not detected")
                self.assertEqual(P.normalise_identity(raw), "codebuff")

    def test_whitespace_variants_collapse_to_a_single_space(self):
        """Whitespace is normalised, not deleted.

        ``Code Buff`` is a legitimate two-word spelling of the agent name, so
        the separator has to survive as a single space. Deleting it outright
        would also mangle unrelated words. The name matcher accepts the space,
        so detection still works.
        """
        for label, raw in {
            "nbsp": f"Code{NBSP}buff",
            "ideographic space": f"Code{IDEOGRAPHIC_SPACE}buff",
            "multiple spaces": "Code   buff",
        }.items():
            with self.subTest(variant=label):
                self.assertTrue(P.is_agent_name(raw), f"{label} not detected")
                self.assertEqual(P.normalise_identity(raw), "code buff")

    def test_all_invisible_characters_are_category_cf(self):
        """The premise of the fix: NFKC does not remove them."""
        import unicodedata
        for ch in (ZWSP, SOFT_HYPHEN, ZWNJ, WJ, BOM):
            with self.subTest(ch=f"U+{ord(ch):04X}"):
                self.assertEqual(unicodedata.category(ch), "Cf")
                self.assertEqual(unicodedata.normalize("NFKC", ch), ch)

    def test_zero_width_in_trailer_value_is_blocked(self):
        msg = f"chore: x\n\nbody\n\nCo-Authored-By: Code{ZWSP}buff <noreply@codebuff.com>"
        self.assertFalse(P.check_message(msg).ok)

    def test_soft_hyphen_in_trailer_value_is_blocked(self):
        msg = f"chore: x\n\nbody\n\nCo-Authored-By: Code{SOFT_HYPHEN}buff <noreply@codebuff.com>"
        self.assertFalse(P.check_message(msg).ok)

    def test_zero_width_in_robot_banner_is_blocked(self):
        msg = f"chore: x\n\n{P.ROBOT_MARKER} Generated with Code{ZWSP}buff"
        self.assertFalse(P.check_message(msg).ok, "banner must survive normalisation")

    def test_zero_width_in_trailer_key_is_blocked(self):
        msg = f"chore: x\n\nbody\n\nCo-Authored{ZWSP}-By: Codebuff <noreply@codebuff.com>"
        self.assertFalse(P.check_message(msg).ok)

    def test_zero_width_in_author_name_is_blocked(self):
        self.assertFalse(P.check_identity(f"Code{ZWSP}buff", "x@example.com").ok)

    def test_normalisation_is_idempotent(self):
        for raw in (
            f"Code{ZWSP}buff",
            f"Code{SOFT_HYPHEN}buff",
            FULLWIDTH_CODEBUFF,
            "  CODE   BUFF  ",
            f"Codebuff{COMBINING_ACUTE}",
        ):
            with self.subTest(raw=repr(raw)):
                once = P.normalise_identity(raw)
                self.assertEqual(once, P.normalise_identity(once))

    def test_empty_input(self):
        self.assertEqual(P.normalise_identity(""), "")
        self.assertFalse(P.is_agent_name(""))
        self.assertFalse(P.is_agent_email(""))

    def test_cyrillic_homoglyph_is_not_claimed_as_covered(self):
        """Documents a real limit instead of pretending it is handled.

        U+043E CYRILLIC SMALL LETTER O is not NFKC-equivalent to Latin 'o', so
        this policy does not detect it. The test pins that fact so the limit
        stays visible and any future change is deliberate.
        """
        cyrillic = "C" + "\u043e" + "debuff"
        self.assertNotEqual(P.normalise_identity(cyrillic), "codebuff")
        self.assertFalse(P.is_agent_name(cyrillic))


class TestEmailDomainMatching(unittest.TestCase):
    """FIX-02 section 7."""

    def test_apex_domain_matches(self):
        self.assertTrue(P.is_agent_email("anyone@codebuff.com"))

    def test_real_subdomains_match(self):
        for address in (
            "noreply@mail.codebuff.com",
            "bot@sub.codebuff.com",
            "a.b.c@deep.sub.codebuff.com",
        ):
            with self.subTest(address=address):
                self.assertTrue(P.is_agent_email(address))

    def test_lookalike_domains_do_not_match(self):
        for address in (
            "noreply@notcodebuff.com",
            "noreply@codebuff.com.evil.example",
            "noreply@mycodebuff.com",
            "noreply@freebuff.com",
        ):
            with self.subTest(address=address):
                self.assertFalse(P.is_agent_email(address), f"{address} must not match")

    def test_match_is_case_and_unicode_insensitive(self):
        self.assertTrue(P.is_agent_email("noreply@MAIL.CODEBUFF.COM"))
        self.assertTrue(P.is_agent_email(f"noreply@ma{ZWSP}il.codebuff.com"))

    def test_no_freebuff_domain_was_invented(self):
        self.assertEqual(P.AGENT_EMAIL_DOMAINS, ("codebuff.com",))

    def test_observed_exact_address_still_matches(self):
        self.assertTrue(P.is_agent_email("noreply@codebuff.com"))

    def test_trailer_with_subdomain_email_is_rejected(self):
        msg = "chore: x\n\nbody\n\nCo-Authored-By: Codebuff <noreply@mail.codebuff.com>"
        self.assertFalse(P.check_message(msg).ok)

    def test_owner_identities_still_allowed(self):
        for address in (
            "rarnold466@outlook.de",
            "51265389+IcyShadow5@users.noreply.github.com",
            "noreply@github.com",
        ):
            with self.subTest(address=address):
                self.assertFalse(P.is_agent_email(address))


class TestProseFalsePositive(unittest.TestCase):
    """FIX-02 section 8: attribution is a structure, not a phrase."""

    PROSE = [
        "docs: explain the policy\n\nWe reject lines like Generated with Codebuff here.\n",
        "docs: explain the policy\n\nLines like `Generated by Codebuff` are refused.\n",
        "docs: explain the policy\n\nThe string Generated with Codebuff is a fixture.\n",
        "docs: explain the policy\n\nThe phrase Generated by Free Buff is rejected.\n",
        "docs: explain the policy\n\nSee the note about Assisting by Code Buff below.\n",
    ]

    def test_prose_mentioning_the_phrase_is_allowed(self):
        for msg in self.PROSE:
            with self.subTest(msg=msg.splitlines()[1]):
                self.assertTrue(P.check_message(msg).ok, msg)

    def test_prose_with_a_colon_and_agent_name_is_allowed(self):
        msg = "docs: x\n\nnote: the Codebuff parser lives in policy.py\n"
        self.assertTrue(P.check_message(msg).ok)

    def test_fenced_block_is_allowed(self):
        msg = (
            "docs: explain the policy\n\n```\n"
            "Co-Authored-By: Codebuff <noreply@codebuff.com>\n"
            "Generated with Codebuff\n```\n"
        )
        self.assertTrue(P.check_message(msg).ok)

    def test_structured_attribution_is_still_blocked(self):
        cases = [
            "chore: x\n\nbody\n\nCo-Authored-By: Codebuff <noreply@codebuff.com>\n",
            "chore: x\n\nbody\n\nGenerated with Codebuff\n",
            "chore: x\n\nbody\n\nGenerated by Codebuff\n",
            f"chore: x\n\nbody\n\n{P.ROBOT_MARKER} Generated with Codebuff\n",
            f"chore: x\n\nbody\n\n{P.ROBOT_MARKER} Codebuff\n",
            "chore: x\n\nbody\n\nGenerated with Codebuff " + P.ROBOT_MARKER + "\n",
            "Generated with Codebuff",
            "Generated by Free Buff",
            "Generated with Codebuff",
        ]
        for msg in cases:
            with self.subTest(msg=msg.splitlines()[-1]):
                self.assertFalse(P.check_message(msg).ok, msg)

    def test_prose_is_also_allowed_for_the_re2_layer(self):
        rx = _re2(P.RE2_COMMIT_MESSAGE_PATTERN)
        for msg in self.PROSE:
            with self.subTest(msg=msg.splitlines()[1]):
                self.assertIsNone(rx.search(msg), msg)


class TestSkipRemoved:
    """FIX-02 section 3: no production skip switch survives."""

    SOURCES = ("attribution_policy.py", "hook_entry.py", "pr_gate.py", "github_api.py")
    TOKEN = "ICY_GOVERNANCE_SKIP"

    def _executable_references(self, source: str):
        """Every string constant and identifier that is NOT a docstring.

        Documentation that *mentions* the removed switch is required -- that is
        how a reader learns it is gone. What must not exist is executable code
        that reads or writes it.
        """
        tree = ast.parse(source)
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = getattr(node, "body", None)
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    if isinstance(body[0].value.value, str):
                        docstrings.add(id(body[0].value))
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docstrings:
                    continue
                if self.TOKEN in node.value:
                    found.append(("string literal", node.value[:60]))
            elif isinstance(node, ast.Name) and self.TOKEN in node.id:
                found.append(("name", node.id))
            elif isinstance(node, ast.Attribute) and self.TOKEN in node.attr:
                found.append(("attribute", node.attr))
        return found

    def test_no_executable_reference_to_the_skip_switch(self):
        for name in self.SOURCES:
            with self.subTest(file=name):
                with open(os.path.join(POLICY_DIR, name), encoding="utf-8") as fh:
                    found = self._executable_references(fh.read())
                self.assertEqual(found, [], f"{name} still references the skip switch")

    def test_skip_switch_is_absent_from_the_installed_hooks(self):
        """The workstation hook shims must not reintroduce it either."""
        gov = os.environ.get("ICY_GOVERNANCE_SOURCE", "")
        if not gov or not os.path.isdir(os.path.join(gov, "hooks")):
            self.skipTest("governance source not available in this checkout")
        for name in ("commit-msg", "pre-push"):
            with self.subTest(hook=name):
                with open(os.path.join(gov, "hooks", name), encoding="utf-8") as fh:
                    self.assertNotIn(self.TOKEN, fh.read())

    def test_no_skip_variable_in_workflow(self):
        with open(WORKFLOW, encoding="utf-8") as fh:
            body = fh.read()
        # Strip comments: the workflow documents the removal on purpose.
        code = "\n".join(
            line for line in body.splitlines() if not line.lstrip().startswith("#")
        )
        self.assertNotIn(self.TOKEN, code)

    def test_hook_entry_has_no_ignore_helper(self):
        self.assertFalse(hasattr(hook_entry, "_ignore_env"))

    def test_env_var_cannot_bypass_the_local_hook_path(self):
        with mock.patch.dict(os.environ, {self.TOKEN: "1"}):
            msg = "chore: x\n\nCo-Authored-By: Codebuff <noreply@codebuff.com>"
            self.assertFalse(P.check_message(msg).ok)

    def test_env_var_cannot_bypass_the_server_gate(self):
        commits = [_commit("a" * 40, f"chore: x\n\n{P.ROBOT_MARKER} Generated with Codebuff")]
        with mock.patch.dict(os.environ, {self.TOKEN: "1"}):
            with mock.patch.object(github_api, "list_pull_request_commits", return_value=commits):
                with mock.patch.object(github_api, "get_pull_request", return_value={"title": "t", "body": "b"}):
                    with _env():
                        self.assertEqual(pr_gate.main(["p", "check-pr"]), 1)


class TestFailClosed(unittest.TestCase):
    """FIX-02 section 4: no failure mode becomes a pass."""

    def test_api_error_fails(self):
        with mock.patch.object(github_api, "list_pull_request_commits", side_effect=github_api.GateError("HTTP 503")):
            with _env():
                self.assertEqual(pr_gate.main(["p", "check-pr"]), 1)

    def test_malformed_response_fails(self):
        for bad in ({"not": "a list"}, "a string", 42, None):
            with self.subTest(bad=repr(bad)):
                with mock.patch.object(github_api, "list_pull_request_commits", return_value=bad):
                    with mock.patch.object(github_api, "get_pull_request", return_value={"title": "t", "body": "b"}):
                        with _env():
                            self.assertEqual(pr_gate.main(["p", "check-pr"]), 1)

    def test_empty_commit_list_fails(self):
        with mock.patch.object(github_api, "list_pull_request_commits", return_value=[]):
            with mock.patch.object(github_api, "get_pull_request", return_value={"title": "t", "body": "b"}):
                with _env():
                    self.assertEqual(pr_gate.main(["p", "check-pr"]), 1)

    def test_missing_committer_fails(self):
        broken = [{
            "sha": "b" * 40,
            "commit": {
                "message": "clean",
                "author": {"name": "Icy", "email": "i@example.com"},
            },
        }]
        with mock.patch.object(github_api, "list_pull_request_commits", return_value=broken):
            with mock.patch.object(github_api, "get_pull_request", return_value={"title": "t", "body": "b"}):
                with _env():
                    self.assertEqual(pr_gate.main(["p", "check-pr"]), 1)

    def test_missing_sha_fails(self):
        broken = [{
            "commit": {
                "message": "clean",
                "author": {"name": "Icy", "email": "i@example.com"},
                "committer": {"name": "Icy", "email": "i@example.com"},
            }
        }]
        with mock.patch.object(github_api, "list_pull_request_commits", return_value=broken):
            with mock.patch.object(github_api, "get_pull_request", return_value={"title": "t", "body": "b"}):
                with _env():
                    self.assertEqual(pr_gate.main(["p", "check-pr"]), 1)

    def test_unexpected_exception_fails(self):
        with mock.patch.object(github_api, "list_pull_request_commits", side_effect=RuntimeError("boom")):
            with _env():
                self.assertEqual(pr_gate.main(["p", "check-pr"]), 1)

    def test_missing_token_fails(self):
        with mock.patch.dict(os.environ, {**OWNER_ENV, "GITHUB_TOKEN": ""}):
            self.assertEqual(pr_gate.main(["p", "check-pr"]), 1)

    def test_non_numeric_pr_number_fails(self):
        with _env(PR_NUMBER="abc"):
            self.assertEqual(pr_gate.main(["p", "check-pr"]), 1)

    def test_malformed_repository_slug_fails(self):
        with _env(GITHUB_REPOSITORY="nope"):
            self.assertEqual(pr_gate.main(["p", "check-pr"]), 1)

    def test_pull_request_payload_missing_body_fails(self):
        with mock.patch.object(github_api, "list_pull_request_commits", return_value=[_commit("c" * 40, "chore: x")]):
            with mock.patch.object(github_api, "get_pull_request", return_value={"title": "t"}):
                with _env():
                    self.assertEqual(pr_gate.main(["p", "check-pr"]), 1)

    def test_no_fallback_range_construct_remains_in_the_workflow(self):
        with open(WORKFLOW, encoding="utf-8") as fh:
            body = fh.read()
        self.assertNotIn("|| python", body)
        self.assertNotIn("HEAD_SHA~1", body)

    def test_local_pre_push_fails_closed_on_error(self):
        with mock.patch.object(P, "check_range", side_effect=OSError("boom")):
            self.assertEqual(hook_entry.main(["p", "pre-push", "a..b"]), 1)

    def test_push_gate_requires_both_shas(self):
        with _env():
            with mock.patch.dict(os.environ, {"BEFORE_SHA": "", "AFTER_SHA": ""}):
                self.assertEqual(pr_gate.main(["p", "check-push"]), 1)

    def test_push_gate_fails_when_previous_tip_unreachable(self):
        with mock.patch.object(github_api, "list_commits", return_value=[_commit("d" * 40, "chore: x")]):
            with _env(BEFORE_SHA="e" * 40, AFTER_SHA="d" * 40):
                self.assertEqual(pr_gate.main(["p", "check-push"]), 1)

    def test_push_gate_judges_the_whole_introduced_range(self):
        # API order is newest first; BEFORE_SHA terminates the range.
        commits = [
            _commit("2" * 40, f"chore: new\n\nCo-Authored-By: Codebuff <noreply@codebuff.com>"),
            _commit("1" * 40, "chore: previous tip"),
        ]
        with mock.patch.object(github_api, "list_commits", return_value=commits):
            with _env(BEFORE_SHA="1" * 40, AFTER_SHA="2" * 40):
                self.assertEqual(pr_gate.main(["p", "check-push"]), 1)

    def test_push_gate_passes_on_a_clean_range(self):
        commits = [
            _commit("3" * 40, "chore: newer"),
            _commit("2" * 40, "chore: older"),
            _commit("1" * 40, "chore: previous tip"),
        ]
        with mock.patch.object(github_api, "list_commits", return_value=commits):
            with _env(BEFORE_SHA="1" * 40, AFTER_SHA="3" * 40):
                self.assertEqual(pr_gate.main(["p", "check-push"]), 0)


class TestApiClient(unittest.TestCase):
    def test_pagination_follows_the_next_link(self):
        calls = []

        def fake_request(path, token):
            calls.append(path)
            # Anchored on the parameter boundary: a bare "page=" would also
            # match inside "per_page=".
            page = int(re.search(r"[?&]page=(\d+)", path).group(1))
            headers = {"link": '<x>; rel="next"'} if page == 1 else {}
            return [{"sha": f"{page}0" * 20}], headers

        with mock.patch.object(github_api, "_request", side_effect=fake_request):
            out = github_api._paginate("/repos/o/r/pulls/1/commits", "t")
        self.assertEqual(len(out), 2)
        self.assertEqual(len(calls), 2)

    def test_pagination_rejects_a_non_array(self):
        with mock.patch.object(github_api, "_request", return_value=({"a": 1}, {})):
            with self.assertRaises(github_api.GateError):
                github_api._paginate("/x", "t")

    def test_pagination_refuses_to_truncate(self):
        with mock.patch.object(
            github_api, "_request", return_value=([{"sha": "x"}], {"link": '<x>; rel="next"'})
        ):
            with self.assertRaises(github_api.GateError):
                github_api._paginate("/x", "t")

    def test_malformed_json_raises(self):
        class Resp:
            headers = {}

            def read(self):
                return b"not json"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch.object(urllib.request, "urlopen", return_value=Resp()):
            with self.assertRaises(github_api.GateError):
                github_api._request("/x", "t")

    def test_malformed_ref_is_refused(self):
        with self.assertRaises(github_api.GateError):
            github_api.list_commits("o/r", "../../etc/passwd", "t")

    def test_bad_pr_number_is_refused(self):
        with self.assertRaises(github_api.GateError):
            github_api.list_pull_request_commits("o/r", 0, "t")


class TestEveryPullRequestCommitIsJudged(unittest.TestCase):
    """FIX-02 section 2."""

    def _run(self, commits, title="t", body="b"):
        with mock.patch.object(github_api, "list_pull_request_commits", return_value=commits):
            with mock.patch.object(github_api, "get_pull_request", return_value={"title": title, "body": body}):
                with _env():
                    return pr_gate.check_pull_request()

    def test_all_clean_commits_pass(self):
        commits = [_commit(f"{i}" * 40, f"chore: step {i}") for i in range(1, 6)]
        violations, count = self._run(commits)
        self.assertEqual(violations, [])
        self.assertEqual(count, 5)

    def test_violation_in_the_last_commit_is_found(self):
        commits = [_commit(f"{i}" * 40, f"chore: step {i}") for i in range(1, 5)]
        commits.append(_commit("9" * 40, "chore: last\n\nCo-Authored-By: Codebuff <noreply@codebuff.com>"))
        violations, count = self._run(commits)
        self.assertTrue(violations, "a late commit must not be skipped")
        self.assertEqual(count, 5)

    def test_violation_in_the_first_commit_is_found(self):
        commits = [_commit("1" * 40, f"chore: first\n\n{P.ROBOT_MARKER} Generated with Codebuff")]
        commits += [_commit(f"{i}" * 40, f"chore: step {i}") for i in range(2, 5)]
        violations, _ = self._run(commits)
        self.assertTrue(violations)

    def test_violation_in_the_middle_commit_is_found(self):
        commits = [_commit("1" * 40, "chore: one"), _commit("2" * 40, "chore: two"), _commit("3" * 40, "chore: three")]
        commits[1]["commit"]["message"] = "chore: two\n\nGenerated by Free Buff"
        violations, _ = self._run(commits)
        self.assertTrue(violations)

    def test_author_email_violation_is_found(self):
        violations, _ = self._run([_commit("1" * 40, "chore: clean", ae="noreply@codebuff.com")])
        self.assertTrue(any("AUTHOR" in v.code for v in violations))

    def test_committer_name_violation_is_found(self):
        violations, _ = self._run([_commit("1" * 40, "chore: clean", cn="Codebuff")])
        self.assertTrue(any("COMMITTER" in v.code for v in violations))

    def test_committer_subdomain_email_violation_is_found(self):
        violations, _ = self._run([_commit("1" * 40, "chore: clean", ce="bot@mail.codebuff.com")])
        self.assertTrue(any("COMMITTER" in v.code for v in violations))

    def test_pull_request_title_attribution_is_found(self):
        violations, _ = self._run([_commit("1" * 40, "chore: clean")], title=f"{P.ROBOT_MARKER} Generated with Codebuff")
        self.assertTrue(any("PR_TITLE" in v.code for v in violations))

    def test_pull_request_body_attribution_is_found(self):
        violations, _ = self._run(
            [_commit("1" * 40, "chore: clean")], body="Co-Authored-By: Codebuff <noreply@codebuff.com>"
        )
        self.assertTrue(any("PR_BODY" in v.code for v in violations))

    def test_pull_request_title_prose_is_allowed(self):
        violations, _ = self._run([_commit("1" * 40, "chore: clean")], title="fix Freebuff integration")
        self.assertEqual(violations, [])


class TestTrustModel(unittest.TestCase):
    """FIX-02 section 1: the gate cannot be influenced by its own pull request."""

    @staticmethod
    def _body():
        with open(WORKFLOW, encoding="utf-8") as fh:
            return fh.read()

    def test_uses_pull_request_target(self):
        self.assertIn("pull_request_target:", self._body())

    def test_does_not_use_the_bare_pull_request_trigger(self):
        self.assertNotIn("\n  pull_request:\n", self._body())

    def test_checkout_is_pinned_to_the_base_commit(self):
        body = self._body()
        self.assertIn("github.event.pull_request.base.sha", body)
        self.assertNotIn("github.event.pull_request.head.sha", body)

    def test_no_pull_request_ref_is_checked_out(self):
        self.assertNotIn("refs/pull/", self._body())

    def test_permissions_are_read_only(self):
        body = self._body()
        perms = re.search(r"^permissions:\n((?:  \S.*\n)+)", body, re.MULTILINE)
        self.assertIsNotNone(perms, "workflow must declare explicit permissions")
        seen = set()
        for line in perms.group(1).strip().splitlines():
            key, _, value = line.strip().partition(":")
            seen.add(key.strip())
            self.assertEqual(value.strip(), "read", f"{key} must be read-only")
        self.assertEqual(seen, {"contents", "pull-requests"})

    def test_no_write_permissions_or_id_token(self):
        body = self._body()
        for bad in ("contents: write", "pull-requests: write", "id-token", "packages: write"):
            self.assertNotIn(bad, body)

    def test_no_repository_secrets_are_used(self):
        body = self._body()
        self.assertNotIn("secrets.", body)
        self.assertIn("GITHUB_TOKEN: ${{ github.token }}", body)

    def test_no_step_uses_the_pull_request_head(self):
        for line in self._body().splitlines():
            if line.strip().startswith("run:") or line.strip().startswith("ref:"):
                self.assertNotIn("head.sha", line)

    def test_gate_module_never_executes_anything(self):
        with open(os.path.join(POLICY_DIR, "pr_gate.py"), encoding="utf-8") as fh:
            body = fh.read()
        for bad in ("subprocess", "os.system", "eval(", "exec(", "compile("):
            self.assertNotIn(bad, body, f"the gate must not contain {bad}")

    def test_gate_module_does_not_shelve_a_token(self):
        with open(os.path.join(POLICY_DIR, "pr_gate.py"), encoding="utf-8") as fh:
            body = fh.read()
        self.assertNotIn("GH_TOKEN", body)


class TestPolicySelfProtection(unittest.TestCase):
    """FIX-02 section 9."""

    def _run(self, files, body=""):
        with mock.patch.object(github_api, "list_pull_request_files", return_value=files):
            with mock.patch.object(github_api, "get_pull_request", return_value={"title": "t", "body": body}):
                with _env():
                    return pr_gate.main(["p", "check-governed-paths"])

    def test_governed_paths_are_declared(self):
        self.assertIn(".github/policy/", pr_gate.GOVERNED_PREFIXES)
        self.assertIn(".github/tests/", pr_gate.GOVERNED_PREFIXES)
        self.assertIn(".github/workflows/attribution-policy.yml", pr_gate.GOVERNED_EXACT)

    def test_ordinary_pull_request_passes(self):
        self.assertEqual(self._run([{"filename": "src/app.py"}, {"filename": "README.md"}]), 0)

    def test_policy_edit_without_authorisation_fails(self):
        self.assertEqual(self._run([{"filename": ".github/policy/attribution_policy.py"}]), 1)

    def test_workflow_edit_without_authorisation_fails(self):
        self.assertEqual(self._run([{"filename": ".github/workflows/attribution-policy.yml"}]), 1)

    def test_governance_test_edit_without_authorisation_fails(self):
        self.assertEqual(self._run([{"filename": ".github/tests/test_attribution_policy.py"}]), 1)

    def test_explicit_authorisation_is_permitted(self):
        rc = self._run(
            [{"filename": ".github/policy/attribution_policy.py"}],
            body=f"owner approved\n\n{pr_gate.POLICY_CHANGE_MARKER}\n",
        )
        self.assertEqual(rc, 0)

    def test_a_near_miss_marker_does_not_authorise(self):
        rc = self._run(
            [{"filename": ".github/policy/attribution_policy.py"}],
            body="ICY-GOVERNANCE-POLICY-CHANGE: NOT-AUTHORIZED",
        )
        self.assertEqual(rc, 1)

    def test_missing_body_fails_closed(self):
        with mock.patch.object(github_api, "list_pull_request_files", return_value=[{"filename": "a.py"}]):
            with mock.patch.object(github_api, "get_pull_request", return_value={"title": "t"}):
                with _env():
                    self.assertEqual(pr_gate.main(["p", "check-governed-paths"]), 1)

    def test_entry_without_filename_fails_closed(self):
        with mock.patch.object(github_api, "list_pull_request_files", return_value=[{"status": "modified"}]):
            with mock.patch.object(github_api, "get_pull_request", return_value={"title": "t", "body": ""}):
                with _env():
                    self.assertEqual(pr_gate.main(["p", "check-governed-paths"]), 1)


class TestLegitimateContentStillPasses(unittest.TestCase):
    """The fix must not have become a blunt instrument."""

    def test_subjects(self):
        for msg in (
            "fix Freebuff integration",
            "document Codebuff benchmark results",
            "remove old Codebuff attribution",
            'test parser against "Generated with Codebuff"',
            "add Code Buff migration guide",
            "chore: bump deps",
        ):
            with self.subTest(msg=msg):
                self.assertTrue(P.check_message(msg).ok, msg)

    def test_human_trailers(self):
        msg = (
            "chore: release 1.0.0\n\nbody\n\n"
            "Signed-off-by: Icy Shadow <rarnold466@outlook.de>\n"
            "Reviewed-by: Someone <s@example.com>\n"
            "Acked-by: Dev <d@example.com>\n"
        )
        self.assertTrue(P.check_message(msg).ok)

    def test_other_tools_and_people_are_not_blocked(self):
        for msg in (
            "feat: integrate Copilot suggestions",
            "feat: add Claude Code session export",
            "Co-Authored-By: Jane Doe <jane@example.com>",
            "chore: add \U0001F680 release notes template",
        ):
            with self.subTest(msg=msg):
                self.assertTrue(P.check_message(msg).ok)

    def test_re2_layer_still_matches_the_required_rejections(self):
        rx = _re2(P.RE2_COMMIT_MESSAGE_PATTERN)
        for msg in (
            f"{P.ROBOT_MARKER} Generated with Codebuff",
            "Generated with Codebuff",
            "Generated by Codebuff",
            "Generated with Freebuff",
            "Generated by Free Buff",
            "Co-Authored-By: Codebuff <noreply@codebuff.com>",
            "Co-Authored-By: Codebuff <noreply@mail.codebuff.com>",
            "Co-authored-by: Freebuff <anything@example.com>",
            "Assisted-by: Code Buff",
            "Created-by: Free Buff",
            "co_authored_by: Codebuff <noreply@codebuff.com>",
        ):
            with self.subTest(msg=msg):
                self.assertTrue(rx.search(msg), msg)

    def test_re2_layer_does_not_match_ordinary_mentions(self):
        rx = _re2(P.RE2_COMMIT_MESSAGE_PATTERN)
        for msg in (
            "fix Freebuff integration",
            "document Codebuff benchmark results",
            "remove old Codebuff attribution",
            "Co-Authored-By: Jane Doe <jane@example.com>",
        ):
            with self.subTest(msg=msg):
                self.assertIsNone(rx.search(msg), msg)


class TestRe2AndRuntimeCompatibility(unittest.TestCase):
    """FIX-02 section 11."""

    def test_pattern_is_inside_the_re2_subset(self):
        pat = P.RE2_COMMIT_MESSAGE_PATTERN
        self.assertTrue(pat.startswith("(?im)"), "inline flags must lead")
        self.assertEqual(pat.count("(?i"), 1, "inline flags must appear once")
        for bad in ("(?=", "(?!", "(?<", "\\1", "\\2", "(?P<"):
            self.assertNotIn(bad, pat, f"RE2-unsupported construct {bad!r}")
        self.assertNotIn("\\U", pat)
        self.assertIn(r"\x{1F916}", pat)

    def test_pattern_matches_patterns_json(self):
        with open(os.path.join(POLICY_DIR, "patterns.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["commit_message_pattern"], P.RE2_COMMIT_MESSAGE_PATTERN)

    def test_all_policy_sources_parse_as_python_312(self):
        for name in sorted(os.listdir(POLICY_DIR)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(POLICY_DIR, name), encoding="utf-8") as fh:
                src = fh.read()
            try:
                ast.parse(src, feature_version=(3, 12))
            except SyntaxError as exc:  # pragma: no cover
                self.fail(f"{name} does not parse as Python 3.12: {exc}")

    def test_policy_uses_no_removed_stdlib_apis(self):
        """guard against APIs that differ across the supported range"""
        for name in sorted(os.listdir(POLICY_DIR)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(POLICY_DIR, name), encoding="utf-8") as fh:
                src = fh.read()
            for bad in ("unicodedata.ucd_3_2_0", "locale.getdefaultlocale"):
                self.assertNotIn(bad, src, f"{name} uses {bad}")

    def test_policy_version_was_bumped(self):
        self.assertTrue(P.POLICY_VERSION.startswith("2."), P.POLICY_VERSION)


if __name__ == "__main__":
    unittest.main(verbosity=2)
