"""Native EOL comparisons stay readable without relaxing helper isolation."""
import hashlib
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from repo_manager import git_observation as observation, git_operations as ops, scanner
from tests.git_repository import git, init_repository


class EolObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = init_repository(self.root / "project space ü")
        git(self.repo, "config", "core.autocrlf", "true")
        git(self.repo, "config", "core.safecrlf", "warn")
        self.file = self.repo / "src/app.py"
        self.file.parent.mkdir()
        self.file.write_bytes(b'print("before")\r\n')
        (self.repo / "rename me.txt").write_bytes(b"rename content\r\n")
        (self.repo / "deleted.txt").write_bytes(b"delete content\r\n")
        (self.repo / "binary.bin").write_bytes(b"before\0binary")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "initial fixture")
        self.service = ops.Repository(self.repo)

    def native(self, *args):
        result = subprocess.run([observation.native_git(), "-C", str(self.repo), *args],
                              env=observation.git_environment(read_only=True),
                              stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=20,
                              creationflags=ops.CREATE_NO_WINDOW)
        result.stdout = result.stdout.decode("utf-8")
        result.stderr = result.stderr.decode("utf-8")
        return result

    def snapshot(self):
        digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        return (git(self.repo, "rev-parse", "HEAD"),
                digest(self.repo / ".git/index"), digest(self.repo / ".git/config"),
                {str(p.relative_to(self.repo)): digest(p) for p in self.repo.rglob("*")
                 if p.is_file() and ".git" not in p.relative_to(self.repo).parts})

    def test_native_roundtrip_warning_diff_preserves_index_head_config_and_files(self):
        self.file.write_bytes(b'print("after!")\n')
        (self.repo / "NOTES.txt").write_bytes(b"external notes\n")
        native = self.native("diff", "--no-ext-diff", "--no-textconv", "--no-color", "--", "src/app.py")
        self.assertEqual(native.returncode, 0)
        self.assertIn("LF will be replaced by CRLF", native.stderr)
        before = self.snapshot()
        state = self.service.state()
        change = next(c for c in state.changes if c.path == "src/app.py")
        self.assertEqual(self.service.diff(change), native.stdout)
        self.assertEqual(self.service.remotes(), ())
        metadata = scanner.collect_metadata(self.repo)
        self.assertTrue(metadata["status_available"])
        self.assertEqual(metadata["dirty"], 2)
        self.assertEqual(self.snapshot(), before)

    def test_line_endings_autocrlf_safecrlf_and_attributes_matrix(self):
        for autocrlf in ("true", "input", "false"):
            git(self.repo, "config", "core.autocrlf", autocrlf)
            for safecrlf in ("warn", "true", "false"):
                git(self.repo, "config", "core.safecrlf", safecrlf)
                for contents in (b'print("after!")\n', b'print("after!")\r\n', b"one\r\ntwo\n"):
                    with self.subTest(autocrlf=autocrlf, safecrlf=safecrlf, contents=contents):
                        self.file.write_bytes(contents)
                        native = self.native("diff", "--no-ext-diff", "--no-textconv", "--no-color", "--", "src/app.py")
                        self.assertEqual(native.returncode, 0, native.stderr)
                        change = next(c for c in self.service.state().changes if c.path == "src/app.py")
                        self.assertEqual(self.service.diff(change), native.stdout)
        for eol in ("lf", "crlf"):
            with self.subTest(attribute_eol=eol):
                (self.repo / ".gitattributes").write_text(f"src/app.py text eol={eol}\n", encoding="utf-8")
                git(self.repo, "config", "core.safecrlf", "warn")
                self.file.write_bytes(b'print("after!")\n')
                native = self.native("diff", "--no-ext-diff", "--no-textconv", "--no-color", "--", "src/app.py")
                change = next(c for c in self.service.state().changes if c.path == "src/app.py")
                self.assertEqual(self.service.diff(change), native.stdout)

    def test_staged_unstaged_deleted_renamed_binary_and_untracked_previews(self):
        self.file.write_bytes(b'print("staged")\r\n')
        git(self.repo, "add", "src/app.py")
        self.file.write_bytes(b'print("working")\n')
        git(self.repo, "mv", "rename me.txt", "renamed ü.txt")
        (self.repo / "deleted.txt").unlink()
        (self.repo / "binary.bin").write_bytes(b"after!\0binary")
        (self.repo / "notes space ü.txt").write_bytes(b"untracked text\n")
        (self.repo / "new binary.bin").write_bytes(b"untracked\0binary")
        before = self.snapshot()
        changes = {c.path: c for c in self.service.state().changes}
        self.assertTrue(changes["src/app.py"].staged and changes["src/app.py"].unstaged)
        self.assertIn('+print("staged")', self.service.diff(changes["src/app.py"], staged=True))
        self.assertIn('+print("working")', self.service.diff(changes["src/app.py"]))
        self.assertIn("rename", self.service.diff(changes["renamed ü.txt"], staged=True))
        self.assertIn("deleted file", self.service.diff(changes["deleted.txt"]))
        self.assertIn("Binary files", self.service.diff(changes["binary.bin"]))
        self.assertEqual(self.service.diff(changes["notes space ü.txt"]), "untracked text\n")
        self.assertIn("preview unavailable", self.service.diff(changes["new binary.bin"]))
        self.assertIn("initial fixture", self.service.history())
        self.assertIn("print", self.service.commit_details(git(self.repo, "rev-parse", "HEAD")))
        self.assertEqual(self.service.remotes(), ())
        self.assertEqual(self.snapshot(), before)

    def test_detached_and_unborn_without_remotes(self):
        git(self.repo, "checkout", "--detach", "-q")
        self.file.write_bytes(b'print("detached")\n')
        state = self.service.state()
        self.assertIsNone(state.branch)
        self.assertIn("detached", self.service.diff(state.changes[0]))
        self.assertIn("initial fixture", self.service.history())
        unborn = init_repository(self.root / "unborn ü")
        (unborn / "note.txt").write_bytes(b"unborn text\n")
        repository = ops.Repository(unborn)
        state = repository.state()
        self.assertIsNone(state.head)
        self.assertEqual(repository.history(), "No commits yet")
        self.assertEqual(repository.remotes(), ())
        self.assertEqual(repository.diff(state.changes[0]), "unborn text\n")

    def test_other_diagnostics_and_nonzero_git_failures_remain_unavailable(self):
        git(self.repo, "update-ref", "refs/heads/HEAD", "HEAD")
        native = self.native("rev-parse", "HEAD")
        self.assertEqual(native.returncode, 0)
        self.assertIn("ambiguous", native.stderr)
        with self.assertRaisesRegex(observation.ObservationUnavailable, "diagnostics"):
            observation.run_read_only(["git", "-C", str(self.repo), "rev-parse", "HEAD"],
                                      env=observation.git_environment(read_only=True),
                                      timeout=20, capture_output=True, text=True)
        with self.assertRaises(ops.GitError):
            self.service.text("show", "does-not-exist")
        self.assertIsNone(self.service.config("missing.fixture.value"))

    def test_explicit_stage_still_honors_repository_safecrlf(self):
        git(self.repo, "config", "core.safecrlf", "true")
        self.file.write_bytes(b'print("after!")\n')
        before = self.snapshot()
        state = self.service.state()
        with mock.patch.object(ops.subprocess, "run", wraps=subprocess.run) as run:
            result = self.service.stage(state, state.changes)
        self.assertEqual(result.outcome, ops.FAILED)
        writes = [call.args[0] for call in run.call_args_list if "add" in call.args[0]]
        self.assertEqual(len(writes), 1)
        self.assertNotIn("core.safecrlf=false", writes[0])
        self.assertEqual(self.snapshot(), before)

    def test_filters_remain_blocked_with_roundtrip_warning_configuration(self):
        helper = self.root / "helper.py"
        marker = self.root / "executed.txt"
        helper.write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("executed")\n', encoding="utf-8")
        command = shlex.quote(Path(sys.executable).as_posix()) + " " + shlex.quote(helper.as_posix())
        self.file.write_bytes(b'print("after!")\n')
        change = next(c for c in self.service.state().changes if c.path == "src/app.py")
        for kind in ("clean", "process"):
            with self.subTest(filter=kind):
                git(self.repo, "config", "filter.sentinel." + kind, command)
                (self.repo / ".gitattributes").write_text("src/app.py filter=sentinel\n", encoding="utf-8")
                with self.assertRaises(ops.GitError):
                    self.service.diff(change)
                self.assertFalse(marker.exists())
                git(self.repo, "config", "--unset", "filter.sentinel." + kind)
