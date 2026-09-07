import io
import json
import unittest
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import Request
from unittest import mock

from repo_manager import providers


class ProviderTests(unittest.TestCase):
    def test_github_redirect_is_rejected_before_cross_host_follow(self):
        handler = providers._PinnedGitHubRedirectHandler()
        request = Request(
            "https://api.github.com/repos/owner/repository",
            headers={"Authorization": "Bearer secret"},
        )
        with self.assertRaises(HTTPError) as caught:
            handler.redirect_request(
                request, None, 302, "Found", {},
                "https://attacker.example/repos/owner/repository",
            )
        self.addCleanup(caught.exception.close)
        self.assertEqual(caught.exception.reason, "unsafe provider redirect")

    def test_local_provider_correspondence_covers_common_and_generic_hosts(self):
        cases = {
            "https://github.com/o/r.git": ("github", "o", "r"),
            "git@gitlab.com:group/sub/r.git": ("gitlab", "group/sub", "r"),
            "ssh://git@bitbucket.org/team/r": ("bitbucket", "team", "r"),
            "https://codeberg.org/team/r.git": ("codeberg", "team", "r"),
            "https://git.example.com/team/r.git": ("generic", "team", "r"),
            "https://gitea.com/team/r.git": ("gitea", "team", "r"),
            "https://forgejo.org/team/r.git": ("forgejo", "team", "r"),
            "https://dev.azure.com/org/project/_git/repo": ("azure_devops", "org/project", "repo"),
        }
        for remote, expected in cases.items():
            result = providers.provider_correspondence(remote)
            self.assertIsNotNone(result, remote)
            self.assertEqual((result["provider_id"], result["namespace"], result["repository"]), expected)

    def test_codeberg_is_not_falsely_labeled_forgejo_and_self_hosted_is_generic(self):
        self.assertEqual(providers.provider_correspondence("https://codeberg.org/team/r")["provider_id"], "codeberg")
        self.assertEqual(providers.provider_correspondence("https://git.example.com/team/r")["provider_id"], "generic")
        self.assertEqual(providers.provider_correspondence("https://gitea.internal/team/r")["provider_id"], "generic")
        self.assertEqual(providers.provider_correspondence("https://forgejo.internal/team/r")["provider_id"], "generic")

    def test_malformed_remote_is_not_strong_correspondence(self):
        for remote in ("", "   ", "git@example.com:../repo", "git@example.com:org/",
                       "git@example.com:org/repo?x=1", "host:value", "https://bad host/org/repo"):
            self.assertIsNone(providers.provider_correspondence(remote), remote)

    def test_unsupported_provider_observation_is_explicit(self):
        result = providers.GitHubAdapter(transport=mock.Mock()).observe("https://gitlab.com/group/repo.git")
        self.assertEqual(result.provider_id, "gitlab")
        self.assertEqual(result.status, providers.NOT_RUN)
        transport = mock.Mock()
        result = providers.GitHubAdapter(transport=transport).observe("https://git.example.com/team/repo.git")
        self.assertEqual(result.provider_id, "generic")
        self.assertEqual(result.status, providers.NOT_RUN)
        transport.assert_not_called()
        transport = mock.Mock()
        result = providers.GitHubAdapter(transport=transport).observe("https://gitea.com/team/repo.git")
        self.assertEqual(result.status, providers.NOT_RUN)
        transport.assert_not_called()

    def test_github_correspondence_supported_and_unsupported(self):
        self.assertEqual(providers.github_correspondence("https://github.com/acme/demo.git")["owner"], "acme")
        self.assertEqual(providers.github_correspondence("git@github.com:acme/demo.git")["name"], "demo")
        self.assertIsNone(providers.github_correspondence("https://gitlab.com/acme/demo.git"))
        self.assertIsNone(providers.github_correspondence("demo"))

    def test_correspondence_accepts_normalized_and_full_remote_forms(self):
        """All representative remote forms must yield the same correspondence.

        The scanner stores only the normalized host/path form
        (github.com/owner/repo), so that form — with and without .git — must
        be accepted; raw full URLs must keep working for legacy records.
        """
        cases = [
            ("https://github.com/owner/repo.git", "owner", "repo"),
            ("https://github.com/owner/repo", "owner", "repo"),
            ("git@github.com:owner/repo.git", "owner", "repo"),
            ("ssh://git@github.com/owner/repo.git", "owner", "repo"),
            ("github.com/owner/repo", "owner", "repo"),
            ("github.com/owner/repo.git", "owner", "repo"),
            ("www.github.com/owner/repo", "owner", "repo"),
        ]
        for raw, owner, name in cases:
            result = providers.github_correspondence(raw)
            self.assertIsNotNone(result, raw)
            self.assertEqual(result["owner"], owner, raw)
            self.assertEqual(result["name"], name, raw)
            self.assertEqual(result["host"], "github.com", raw)

    def test_correspondence_rejects_non_github_and_malformed(self):
        for raw in ("https://gitlab.com/acme/demo.git", "demo",
                    "github.com", "https://github.com/onlyowner",
                    "ssh://github.com/owner/repo", "", None):
            self.assertIsNone(providers.github_correspondence(raw), raw)

    def test_multiple_remote_correspondence_can_be_selected_without_authority(self):
        remotes = ["gitlab.com/group/repo", "github.com/org/repo"]
        selected = remotes[0]
        result = providers.provider_correspondence(selected)
        self.assertEqual(result["provider_id"], "gitlab")
        self.assertEqual(result["local_correspondence"], "available")
        self.assertNotIn("authorization", result)

    def test_multiple_remote_correspondences_preserve_conflicting_hosts(self):
        matches = providers.provider_correspondences([
            "gitlab.com/team/app", "github.com/team/app",
            "ssh://git@example.internal/team/app.git",
            "github.com/team/app",
        ])
        self.assertEqual(
            [(item["provider_id"], item["host"]) for item in matches],
            [("github", "github.com"),
             ("gitlab", "gitlab.com"),
             ("generic", "example.internal")],
        )

    def test_malformed_origin_does_not_hide_valid_alternate(self):
        remotes = ["not a remote", "gitlab.com/group/repo"]
        valid = [providers.provider_correspondence(remote) for remote in remotes]
        self.assertIsNone(valid[0])
        self.assertEqual(valid[1]["provider_id"], "gitlab")

    def test_scanner_metadata_remote_reaches_provider_observation(self):
        """A record produced by collect_metadata must flow end-to-end.

        Regression for the normalization boundary: the stored remote is the
        normalized host/path form, and observe() must reach the provider
        instead of short-circuiting to UNKNOWN.
        """
        import subprocess
        import tempfile
        from pathlib import Path
        from repo_manager import scanner
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "demo"
            d.mkdir()
            (d / "f.txt").write_text("x", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=d, check=True)
            subprocess.run(["git", "-C", str(d), "remote", "add", "origin",
                            "https://github.com/acme/demo.git"], check=True)
            meta = scanner.collect_metadata(str(d))
            self.assertEqual(meta["remote"], "github.com/acme/demo")
            payload = {"id": 7, "name": "demo", "private": False,
                       "default_branch": "main",
                       "html_url": "https://github.com/acme/demo",
                       "owner": {"login": "acme"}}
            calls = []

            def transport(request, timeout):
                calls.append(request.full_url)
                return payload

            observation = providers.GitHubAdapter(
                transport=transport).observe(meta["remote"])
            self.assertEqual(observation.status, providers.AVAILABLE)
            self.assertEqual(calls, ["https://api.github.com/repos/acme/demo"])

    def test_successful_observation_normalizes_metadata(self):
        payload = {"id": 42, "name": "demo", "private": True,
                   "default_branch": "main", "html_url": "https://github.com/acme/demo",
                   "owner": {"login": "acme"}}
        adapter = providers.GitHubAdapter(transport=lambda request, timeout: payload)
        result = adapter.observe("https://github.com/acme/demo.git", token="secret")
        self.assertEqual(result.status, providers.AVAILABLE)
        self.assertEqual(result.repository_id, "42")
        self.assertEqual(result.visibility, providers.VISIBILITY_PRIVATE)
        self.assertEqual(result.default_branch, "main")
        self.assertNotIn("secret", repr(result))

    def test_response_identity_must_match_selected_remote(self):
        payload = {"id": 42, "name": "other", "private": False,
                   "default_branch": "main",
                   "html_url": "https://github.com/acme/other",
                   "owner": {"login": "acme"}}
        result = providers.GitHubAdapter(
            transport=lambda request, timeout: payload).observe(
                "https://github.com/acme/demo.git")
        self.assertEqual(result.status, providers.UNKNOWN)
        self.assertIsNone(result.repository_id)

    def test_malformed_success_payload_is_not_available(self):
        result = providers.GitHubAdapter(
            transport=lambda request, timeout: {"message": "ok"}).observe(
                "https://github.com/acme/demo.git")
        self.assertEqual(result.status, providers.UNKNOWN)

    def test_provider_presentation_status_separates_local_and_online_severity(self):
        correspondence = providers.provider_correspondence(
            "github.com/acme/demo")
        observed_at = "2026-09-03T00:00:00+00:00"
        cases = {
            providers.AVAILABLE: providers.AVAILABLE,
            providers.NETWORK_FAILURE: "WARN",
            providers.TIMEOUT: "WARN",
            providers.AUTHENTICATION_REQUIRED: "WARN",
            providers.NOT_FOUND: "WARN",
            providers.UNKNOWN: providers.UNKNOWN,
        }
        for observation_status, presentation_status in cases.items():
            observation = providers.ProviderObservation(
                "github", observation_status, providers.CURRENT, observed_at,
                correspondence=correspondence)
            self.assertEqual(
                providers.provider_presentation_status(correspondence, observation),
                presentation_status, observation_status)

        unsupported = providers.provider_correspondence(
            "gitlab.com/acme/demo")
        not_run = providers.ProviderObservation(
            "gitlab", providers.NOT_RUN, providers.UNKNOWN_FRESHNESS,
            observed_at, correspondence=unsupported)
        self.assertEqual(
            providers.provider_presentation_status(unsupported, not_run),
            providers.AVAILABLE)
        self.assertEqual(
            providers.provider_presentation_status(None, providers.UNKNOWN),
            providers.UNKNOWN)

    def test_failure_states_are_distinct(self):
        def unauthorized(request, timeout):
            raise HTTPError(request.full_url, 401, "unauthorized", {}, None)
        result = providers.GitHubAdapter(transport=unauthorized).observe("git@github.com:a/b.git")
        self.assertEqual(result.status, providers.AUTHENTICATION_REQUIRED)

        def missing(request, timeout):
            raise HTTPError(request.full_url, 404, "missing", {}, None)
        result = providers.GitHubAdapter(transport=missing).observe("git@github.com:a/b.git")
        self.assertEqual(result.status, providers.NOT_FOUND)

        result = providers.GitHubAdapter(transport=lambda request, timeout: (_ for _ in ()).throw(TimeoutError())).observe("git@github.com:a/b.git")
        self.assertEqual(result.status, providers.TIMEOUT)

    def test_http_failure_closes_response_without_changing_status(self):
        for code, expected in ((401, providers.AUTHENTICATION_REQUIRED),
                               (404, providers.NOT_FOUND),
                               (500, providers.NETWORK_FAILURE)):
            with self.subTest(code=code):
                body = io.BytesIO(b"error response")
                error = HTTPError("https://api.github.com/repos/a/b", code,
                                  "failure", {}, body)
                self.addCleanup(error.close)
                with mock.patch.object(providers.GitHubAdapter, "_request",
                                       side_effect=error):
                    result = providers.GitHubAdapter().observe("git@github.com:a/b.git")
                self.assertEqual(result.status, expected)
                self.assertTrue(body.closed)

    def test_freshness_and_disagreement(self):
        now = datetime.now(timezone.utc)
        self.assertEqual(providers.freshness(now.isoformat(), now=now), providers.CURRENT)
        self.assertEqual(providers.freshness((now - timedelta(hours=1)).isoformat(), now=now), providers.STALE)
        self.assertEqual(providers.freshness((now + timedelta(seconds=1)).isoformat(), now=now), providers.STALE)
        observation = providers.ProviderObservation("github", providers.AVAILABLE, providers.CURRENT,
                                                    now.isoformat(), default_branch="main")
        self.assertEqual(providers.compare_local_provider({"branch": "feature"}, observation),
                         ["local branch='feature'; provider default_branch='main'"])
        self.assertEqual(providers.compare_local_provider({"branch": "main"}, observation), [])

    def test_provider_observation_is_read_only_and_non_authoritative(self):
        local = {"remote": "github.com/acme/demo", "branch": "feature"}
        observation = providers.ProviderObservation("github", providers.AVAILABLE, providers.CURRENT,
                                                    "2026-08-30T00:00:00+00:00", repository_id="99",
                                                    default_branch="main")
        providers.compare_local_provider(local, observation)
        self.assertEqual(local["branch"], "feature")


if __name__ == "__main__":
    unittest.main()
