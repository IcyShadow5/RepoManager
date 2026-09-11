"""Check persistence fixture isolation using readable app-data snapshots.

Store paths are redirected before fixture writes. Expected outputs are
checked in the temporary store, and hashes of readable original artifacts
are compared afterward. Unreadable artifacts are outside this snapshot.

Original artifacts are only read; no writes or deletions target them.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from repo_manager import store

REAL_ARTIFACT_NAMES = ("repos.json", "repos.json.bak1", "repos.json.bak2",
                       "settings.json")


def real_artifact_hashes():
    """Read-only snapshot of real RepoManager artifacts (may be empty)."""
    app_dir = store.APP_DIR
    out = {}
    for name in REAL_ARTIFACT_NAMES:
        path = app_dir / name
        try:
            raw = path.read_bytes()
        except (OSError, PermissionError):
            continue
        out[str(path)] = (len(raw), hashlib.sha256(raw).hexdigest())
    notes = app_dir / "notes"
    if notes.is_dir():
        try:
            for note in sorted(notes.iterdir()):
                if not note.is_file():
                    continue
                raw = note.read_bytes()
                out[str(note)] = (len(raw), hashlib.sha256(raw).hexdigest())
        except OSError:
            pass
    return out


def _mk_fixture_project(i):
    """Project fixture modeled on the GUI suite, with an explicit stable ID."""
    return {"path": rf"C:\repos\repo{i:05d}", "name": f"repo{i:05d}",
            "status": "active" if i % 5 == 0 else "idea", "focus": "",
            "pinned": i % 5 == 0, "dirty": i % 3, "ahead": i % 4, "behind": 0,
            "remote": f"github.com/o/repo{i}", "branch": "main",
            "last_commit_date": "2026-08-01", "last_commit_msg": "x",
            "project_id": f"fixture-{i:05d}"}


class RealDataIsolationTripwireTests(unittest.TestCase):
    """Fixture outputs use redirected paths; readable original artifacts stay unchanged."""

    def test_isolated_fixture_writes_stay_in_tmp_and_real_data_unchanged(self):
        before = real_artifact_hashes()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "app"
            original = (store.APP_DIR, store.REPOS_FILE,
                        store.SETTINGS_FILE, store.NOTES_DIR)
            store.APP_DIR = base
            store.REPOS_FILE = base / "repos.json"
            store.SETTINGS_FILE = base / "settings.json"
            store.NOTES_DIR = base / "notes"
            try:
                # Gate: no write may happen unless redirection is proven.
                self.assertEqual(store.REPOS_FILE.parent, base)
                self.assertEqual(store.SETTINGS_FILE.parent, base)
                self.assertEqual(store.NOTES_DIR, base / "notes")
                self.assertEqual(str(store.APP_DIR), str(base))
                store.ensure_dirs()
                store.save_projects([_mk_fixture_project(i) for i in range(40)])
                store.save_settings({"roots": [rf"C:\repos"],
                                     "theme": "dark"})
                store.save_note("repo00000", r"C:\repos\repo00000",
                                "fixture note", project_id="fixture-00000")
                # Expected fixture outputs exist under the temporary directory.
                self.assertTrue(store.REPOS_FILE.exists())
                self.assertTrue(store.SETTINGS_FILE.exists())
                note_files = list(store.NOTES_DIR.glob("*.md"))
                self.assertEqual(len(note_files), 1)
                loaded = json.loads(store.REPOS_FILE.read_text(encoding="utf-8"))
                self.assertEqual(len(loaded["projects"]), 40)
                self.assertTrue(
                    all(not str(p).startswith(str(Path(r"C:\repos") / ".."))
                        for p in (store.REPOS_FILE, store.SETTINGS_FILE)))
            finally:
                store.APP_DIR, store.REPOS_FILE, store.SETTINGS_FILE, \
                    store.NOTES_DIR = original
        after = real_artifact_hashes()
        for path, (size, digest) in before.items():
            with self.subTest(artifact=path):
                self.assertIn(path, after)
                self.assertEqual(after[path], (size, digest),
                                 "real user artifact changed during isolated run")
        self.assertEqual(len(after), len(before))


if __name__ == "__main__":
    unittest.main()
