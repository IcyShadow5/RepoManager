"""Current neutral contracts migrated from mixed classic test modules."""
import json
import os
import random
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from repo_manager import agents, launchers, projects, relocation, scanner
from repo_manager.project_presentation import location_label
from repo_manager.projects import is_visible, sorted_projects, working_on_now_rows
from repo_manager.git_targets import (GitMutationGuard, git_target_snapshot, git_target_is_authorized,
                                     git_target_is_current, git_mutation_target_is_authorized)
from repo_manager.scan_state import reconcile_scan_result
from repo_manager.relocation import (MOVE_OK, MOVE_ROLLBACK_FAILED, MOVE_STALE_TARGET,
    perform_confirmed_move, move_outcome, filter_superseded_rows, run_batch_moves,
    group_move_suggestions, select_strong_suggestions)


def proj(name, **kw):
    p = {
        "path": rf"C:\repos\{name}", "name": name, "status": "idea",
        "focus": "", "pinned": False, "dirty": 0, "ahead": 0, "behind": 0,
        "remote": f"github.com/x/{name.lower()}",
    }
    p.update(kw)
    return p

def make_repo(root: Path, name: str = "R") -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=d, check=True)
    subprocess.run(["git", "-C", str(d), "config", "user.email", "t@t.t"],
                   check=True)
    subprocess.run(["git", "-C", str(d), "config", "user.name", "t"],
                   check=True)
    subprocess.run(["git", "-C", str(d), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(d), "-c", "user.name=t",
         "-c", "user.email=t@t.t", "commit", "-qm", "init"],
        check=True)
    return d

def git_ok(repo: Path, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True)

def curated(old: dict) -> dict:
    out = dict(old)
    out.update({
        "project_id": "pid-1",
        "name": "Curated",
        "status": "active",
        "focus": "keep",
        "pinned": True,
        "added_at": "2026-01-01T00:00:00Z",
        "last_seen": "2026-01-01T00:00:00Z",
    })
    return out

class LocationLabelTests(unittest.TestCase):
    def test_scanned_path_keeps_root_name_and_relative_location(self):
        root = os.path.join(os.sep, "demo", "repositories")
        path = os.path.join(root, "tools", "Orbit Notes")
        self.assertEqual(
            location_label(path, [root]),
            os.path.join("repositories", "tools", "Orbit Notes"))

    def test_other_root_and_missing_path_are_not_invented(self):
        path = os.path.join(os.sep, "other", "Orbit Notes")
        self.assertEqual(location_label(path, [os.path.join(os.sep, "demo")]), path)
        self.assertEqual(location_label(None, []), "(no folder)")


class VisibleTests(unittest.TestCase):
    def test_empty_filter_matches_all(self):
        self.assertTrue(is_visible(proj("Alpha"), ""))

    def test_matches_name_path_focus_case_insensitive(self):
        p = proj("Alpha", focus="Refactor district system")
        self.assertTrue(is_visible(p, "alpha"))
        self.assertTrue(is_visible(p, r"c:\repos"))
        self.assertTrue(is_visible(p, "DISTRICT"))
        self.assertFalse(is_visible(p, "beta"))

    def test_missing_fields_do_not_crash(self):
        self.assertFalse(is_visible({"path": "x"}, "zzz"))


class SortTests(unittest.TestCase):
    def test_default_pinned_then_active_then_name(self):
        items = [proj("zeta"), proj("beta", status="active"),
                 proj("alpha", pinned=True)]
        self.assertEqual([p["name"] for p in sorted_projects(items)],
                         ["alpha", "beta", "zeta"])

    def test_by_name_asc_and_desc(self):
        items = [proj("B"), proj("a"), proj("C")]
        names = [p["name"] for p in sorted_projects(items, "name")]
        self.assertEqual(names, ["a", "B", "C"])
        names = [p["name"] for p in sorted_projects(items, "name", True)]
        self.assertEqual(names, ["C", "B", "a"])

    def test_dirty_numeric_not_string(self):
        items = [proj("A", dirty=10), proj("B", dirty=9)]
        self.assertEqual(sorted_projects(items, "dirty")[0]["name"], "B")

    def test_sync_sums_ahead_behind(self):
        items = [proj("A", ahead=1), proj("B", behind=3)]
        self.assertEqual(sorted_projects(items, "sync")[0]["name"], "A")
        self.assertEqual(
            sorted_projects(items, "sync", True)[0]["name"], "B")

    def test_missing_values_tolerated(self):
        items = [{"name": "X", "path": "p"}]
        self.assertEqual(len(sorted_projects(items, "branch")), 1)


class ConfirmedMoveTransactionTests(unittest.TestCase):
    """Confirmed moves preserve registry and note consistency."""

    OLD_PATH = r"C:\Users\u\Desktop\Iron"
    NEW_PATH = r"C:\Users\u\Desktop\Projekte\Iron"
    NOW = "2026-08-26T12:00:00Z"

    @staticmethod
    def suggestion():
        return {"kind": "move", "category": "strong",
                "old_path": ConfirmedMoveTransactionTests.OLD_PATH,
                "new_path": ConfirmedMoveTransactionTests.NEW_PATH,
                "name": "Iron", "evidence": ["Same normalized remote"]}

    def _projects(self):
        return [{"path": self.OLD_PATH, "name": "Iron",
                 "status": "active", "focus": "district refactor",
                 "pinned": True, "added_at": "2026-01-01T00:00:00Z",
                 "last_seen": "2026-08-25T00:00:00Z"}]

    def test_happy_path_migrates_and_adopts_name(self):
        projects = self._projects()
        calls = {"save": 0, "note": []}
        outcome, entry = perform_confirmed_move(
            projects, self.suggestion(), self.NOW,
            save_projects=lambda: calls.__setitem__("save", calls["save"]+1),
            move_note=lambda *a: calls["note"].append(a))
        self.assertEqual(outcome, "migrated")
        self.assertEqual(calls["save"], 1)
        self.assertEqual(calls["note"],
                         [("Iron", self.OLD_PATH,
                           Path(self.NEW_PATH).name, self.NEW_PATH)])
        self.assertEqual(entry["path"], self.NEW_PATH)
        self.assertEqual(entry["name"], "Iron")  # folder name adopted
        self.assertEqual(entry["moved_from"], self.OLD_PATH)
        self.assertEqual(entry["last_seen"], self.NOW)
        # curation and added_at survive
        self.assertEqual(entry["status"], "active")
        self.assertEqual(entry["focus"], "district refactor")
        self.assertTrue(entry["pinned"])
        self.assertEqual(entry["added_at"], "2026-01-01T00:00:00Z")

    def test_duplicate_new_path_blocked(self):
        projects = self._projects()
        projects.append({"path": self.NEW_PATH, "name": "Iron"})
        outcome, entry = perform_confirmed_move(
            projects, self.suggestion(), self.NOW,
            save_projects=self._fail, move_note=self._fail)
        self.assertEqual(outcome, "duplicate")
        self.assertIsNone(entry)
        self.assertEqual(len(projects), 2)  # nothing changed

    def test_equivalent_duplicate_new_path_is_blocked(self):
        projects = self._projects()
        projects.append({"path": self.NEW_PATH + r"\.", "name": "Iron"})

        outcome, entry = perform_confirmed_move(
            projects, self.suggestion(), self.NOW,
            save_projects=self._fail, move_note=self._fail)

        self.assertEqual(outcome, "duplicate")
        self.assertIsNone(entry)
        self.assertEqual(projects[0]["path"], self.OLD_PATH)

    def test_missing_old_blocked(self):
        outcome, _ = perform_confirmed_move(
            [], self.suggestion(), self.NOW,
            save_projects=self._fail, move_note=self._fail)
        self.assertEqual(outcome, "missing_old")

    def test_registry_failure_cancels_before_note_touch(self):
        projects = self._projects()
        snapshot = dict(projects[0])
        note_calls = []

        def failing_save():
            raise OSError("disk full")

        outcome, entry = perform_confirmed_move(
            projects, self.suggestion(), self.NOW,
            save_projects=failing_save,
            move_note=lambda *a: note_calls.append(a))
        self.assertEqual(outcome, "rolled_back_registry")
        self.assertEqual(projects[0], snapshot)  # fully reverted
        self.assertEqual(note_calls, [])         # note never touched

    def test_note_failure_rolls_registry_back(self):
        projects = self._projects()
        snapshot = dict(projects[0])
        saves = []

        def save():
            saves.append(1)

        def failing_note(*a):
            raise OSError("rename locked")

        outcome, entry = perform_confirmed_move(
            projects, self.suggestion(), self.NOW,
            save_projects=save, move_note=failing_note)
        self.assertEqual(outcome, "rolled_back_note")
        self.assertEqual(projects[0], snapshot)
        self.assertEqual(len(saves), 2)  # commit attempt + rollback restore

    def test_stable_project_note_migrates_before_registry_commit(self):
        projects = self._projects()
        projects[0]["project_id"] = "project-1"
        calls = []

        outcome, entry = perform_confirmed_move(
            projects, self.suggestion(), self.NOW,
            save_projects=lambda: calls.append("save"),
            move_note=lambda *args: calls.append(("note", args)) or "moved")

        self.assertEqual(outcome, "migrated")
        self.assertEqual(calls[0][0], "note")
        self.assertEqual(calls[0][1][-1], "project-1")
        self.assertEqual(calls[1], "save")
        self.assertEqual(entry["project_id"], "project-1")

    def test_stable_note_failure_never_mutates_registry(self):
        projects = self._projects()
        projects[0]["project_id"] = "project-1"
        snapshot = dict(projects[0])
        saves = []

        outcome, _entry = perform_confirmed_move(
            projects, self.suggestion(), self.NOW,
            save_projects=lambda: saves.append(1),
            move_note=lambda *args: (_ for _ in ()).throw(
                OSError("note locked")))

        self.assertEqual(outcome, "rolled_back_note")
        self.assertEqual(projects[0], snapshot)
        self.assertEqual(saves, [])

    def _fail(self, *a, **k):
        raise OSError("injected failure")


class MoveRaceGuardTests(unittest.TestCase):
    OLD = r"C:\Users\u\Desktop\Iron"
    NEW = r"C:\Users\u\Desktop\Projekte\Iron"

    @staticmethod
    def row(path, status="idea"):
        return {"path": path, "name": Path(path).name, "status": status}

    def test_stale_snapshot_cannot_resurrect_moved_row(self):
        # A scan may finish after a confirmed move; stale rows must not return.
        moved_away = [(1, self.OLD.lower())]
        rows = [self.row(self.OLD, status="active"),  # pre-move snapshot
                self.row(self.NEW)]                   # fresh discovery
        kept = filter_superseded_rows(rows, moved_away, result_gen=0)
        paths = [r["path"] for r in kept]
        self.assertNotIn(self.OLD, paths)
        self.assertIn(self.NEW, paths)

    def test_current_generation_result_unfiltered(self):
        rows = [self.row(r"C:\x\A"), self.row(r"C:\x\B")]
        self.assertEqual(filter_superseded_rows(rows, [], result_gen=3),
                         rows)

    def test_multiple_sequential_moves_scoped_by_gen(self):
        moved_away = [(2, r"c:\old\a"), (5, r"c:\old\b")]
        rows = [self.row(r"C:\old\a"), self.row(r"C:\old\b"),
                self.row(r"C:\old\c")]
        # result_gen=4: move at gen 2 predates this scan (snapshot already
        # lacks 'a' -> nothing to do), move at gen 5 is in-flight -> drop 'b'
        kept = [r["path"] for r in filter_superseded_rows(
            rows, moved_away, result_gen=4)]
        self.assertEqual(kept, [r"C:\old\a", r"C:\old\c"])

    def test_full_ordering_scan_confirm_result(self):
        """A stale scan result cannot restore a moved project."""
        projects = [self.row(self.OLD, status="active")]
        gen_at_start = 0
        snapshot = [dict(p) for p in projects]          # worker's view
        # user confirms: perform move + bump generation
        sug = {"kind": "move", "old_path": self.OLD, "new_path": self.NEW}
        now = "2026-08-26T12:00:00Z"
        perform_confirmed_move(projects, sug, now,
                               save_projects=lambda: None,
                               move_note=lambda *a: "absent")
        gen_after = 1
        self.assertGreater(gen_after, gen_at_start)
        moved_away = [(gen_after, self.OLD.lower())]
        # stale result arrives built from the old snapshot + discovery
        stale_result = snapshot + [self.row(self.NEW)]
        applied = filter_superseded_rows(stale_result, moved_away,
                                         result_gen=gen_at_start)
        self.assertEqual([r["path"] for r in applied], [self.NEW])


class MoveRevalidationWiringTests(unittest.TestCase):
    """Optional callback compatibility and move revalidation guards.

    The UI supplies fresh path and identity checks. Calls without callbacks
    remain supported for callers that manage their own validation.
    """

    OLD = r"C:\Users\u\Desktop\Iron"
    NEW = r"C:\Users\u\Desktop\Projekte\Iron"
    NOW = "2026-08-30T00:00:00Z"

    @staticmethod
    def suggestion():
        return {"kind": "move", "category": "strong",
                "old_path": MoveRevalidationWiringTests.OLD,
                "new_path": MoveRevalidationWiringTests.NEW,
                "name": "Iron", "evidence": ["Same normalized remote"]}

    def _projects(self):
        return [{"path": self.OLD, "name": "Iron", "status": "active"}]

    def test_compatibility_call_without_callbacks_still_moves(self):
        outcome, entry = perform_confirmed_move(
            self._projects(), self.suggestion(), self.NOW,
            save_projects=lambda: None, move_note=lambda *a: "absent")
        self.assertEqual(outcome, MOVE_OK)
        self.assertEqual(entry["path"], self.NEW)

    def test_wired_path_exists_blocks_stale_target(self):
        for label, path_exists in (
                ("old path present again", lambda path: path == self.OLD),
                ("new path missing", lambda path: False)):
            with self.subTest(label=label):
                result = move_outcome(
                    self._projects(), self.suggestion(), self.NOW,
                    lambda: self.fail("registry must not be saved"),
                    lambda *a: self.fail("note must not move"),
                    path_exists=path_exists)
                self.assertEqual(result.status, MOVE_STALE_TARGET)

    def test_run_batch_moves_compatibility_call_without_path_exists(self):
        accepted, failures = run_batch_moves(
            [self.suggestion()], self._projects(), self.NOW,
            save_projects=lambda: None, move_note=lambda *a: "absent")
        self.assertEqual(len(accepted), 1)
        self.assertEqual(failures, [])

    def test_run_batch_moves_forwards_path_exists_guard(self):
        projects = self._projects()
        accepted, failures = run_batch_moves(
            [self.suggestion()], projects, self.NOW,
            save_projects=lambda: None, move_note=lambda *a: "absent",
            path_exists=lambda path: False)  # new path missing -> stale
        self.assertEqual(accepted, [])
        self.assertEqual([o for _s, o in failures], ["stale_target"])
        self.assertEqual(projects[0]["path"], self.OLD)  # nothing moved


class BatchReconciliationTests(unittest.TestCase):
    OLD = r"C:\u\Desktop\Foo"
    NEW = r"C:\u\Projekte\Foo"
    NOW = "2026-08-26T12:00:00Z"

    @staticmethod
    def sug(category, old=None, new=None, name="Foo"):
        return {"kind": "move", "category": category,
                "old_path": old or rf"C:\old\{name}",
                "new_path": new or rf"C:\new\{name}",
                "name": name, "evidence": ["Same normalized remote"]}

    def _projects(self):
        return [{"path": self.OLD, "name": "Foo", "status": "active",
                 "focus": "work", "pinned": True,
                 "last_seen": "2099-01-01T00:00:00Z"}]

    # ---- grouping -------------------------------------------------------
    def test_grouping_splits_and_sorts_categories(self):
        sugs = [self.sug("strong", name="b"),
                self.sug("ambiguous", old=r"C:\old\Relic",
                         new=r"C:\a\Relic"),
                self.sug("strong", name="a"),
                self.sug("possible", name="zz")]
        groups = group_move_suggestions(sugs)
        self.assertEqual([s["name"] for s in groups["strong"]],
                         ["a", "b"])          # sorted by old path
        self.assertEqual(len(groups["ambiguous"]), 1)
        self.assertEqual([s["name"] for s in groups["possible"]], ["zz"])

    def test_unknown_category_lands_in_possible(self):
        groups = group_move_suggestions([self.sug("weird")])
        self.assertEqual(len(groups["possible"]), 1)
        self.assertEqual(groups["strong"], [])

    # ---- batch selection & execution ------------------------------------
    def test_batch_selects_strong_only(self):
        sugs = [self.sug("strong"), self.sug("ambiguous"),
                self.sug("possible")]
        selected = select_strong_suggestions(sugs)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["category"], "strong")

    def test_batch_empty_state(self):
        accepted, failures = run_batch_moves(
            [], self._projects(), self.NOW, lambda: None,
            lambda *a: "absent")
        self.assertEqual((accepted, failures), ([], []))

    def test_batch_reuses_transaction_and_reports(self):
        projects = self._projects()
        strong = [self.sug("strong", name=f"repo{i}") for i in range(3)]
        projects += [{"path": rf"C:\old\repo{i}", "name": f"repo{i}",
                      "status": "idea"} for i in range(3)]
        sugs = strong + [self.sug("ambiguous")]
        accepted, failures = run_batch_moves(
            sugs, projects, self.NOW,
            save_projects=lambda: None,
            move_note=lambda *a: "absent")
        self.assertEqual(len(accepted), 3)
        self.assertEqual(failures, [])
        for (s, entry, outcome) in zip(strong, [e for _, e, _ in accepted],
                                       [o for _, _, o in accepted]):
            self.assertEqual(outcome, "migrated")
            self.assertTrue(entry["path"].endswith(s["new_path"]))

    def store_save(self, projects):
        pass

    def test_one_failure_does_not_block_rest(self):
        projects = self._projects()
        s_bad = self.sug("strong", name="bad")
        s_good1 = self.sug("strong", name="good1")
        s_good2 = self.sug("strong", name="good2")
        projects += [{"path": r"C:\old\bad", "name": "bad"},
                     {"path": r"C:\old\good1", "name": "good1"},
                     {"path": r"C:\old\good2", "name": "good2"}]

        def note_fn(old_name, old_path, new_name, new_path):
            if old_name == "bad":
                raise OSError("locked")
            return "absent"

        with mock.patch.object(relocation, "select_strong_suggestions",
                               return_value=[s_bad, s_good1, s_good2]):
            accepted, failures = run_batch_moves(
                [s_bad, s_good1, s_good2], projects, self.NOW,
                save_projects=lambda: None, move_note=note_fn)
        self.assertEqual(len(accepted), 2)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0][1], "rolled_back_note")
        self.assertEqual(failures[0][0]["name"], "bad")

    def test_consumed_suggestions_disappear_no_double_processing(self):
        suggestions = [self.sug("strong"), self.sug("strong", name="B"),
                       self.sug("ambiguous", old=r"C:\old\Relic",
                                new=r"C:\a\Relic")]
        projects = [{"path": rf"C:\old\{n}", "name": n, "status": "idea"}
                    for n in ("Foo", "B")]
        accepted, failures = run_batch_moves(
            suggestions, projects, self.NOW,
            save_projects=lambda: None,
            move_note=lambda *a: "absent")
        consumed_ids = {id(s) for s, _, _ in accepted}
        self.assertEqual(len(consumed_ids), 2)
        self.assertEqual(failures, [])
        remaining = [s for s in suggestions if id(s) not in consumed_ids]
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["category"], "ambiguous")


class WorkingOnNowTests(unittest.TestCase):
    ALIVE = r"C:\Users\u\Projekte\Live"
    GONE = r"C:\Users\u\Projekte\Gone"

    @staticmethod
    def p(path, status="idea", pinned=False, commit="2026-08-01"):
        return {"path": path, "name": Path(path).name, "status": status,
                "pinned": pinned, "last_commit_date": commit}

    def setUp(self):
        self.exists = {self.ALIVE}
        self.exists_fn = lambda path: path in self.exists

    def rows(self, items):
        return [p["name"] for p in working_on_now_rows(
            items, exists=self.exists_fn)]

    def test_empty_items_yield_empty(self):
        self.assertEqual(working_on_now_rows([], exists=self.exists_fn), [])

    def test_active_alive_included(self):
        row = self.p(self.ALIVE, status="active")
        self.assertEqual(self.rows([row]), ["Live"])

    def test_pinned_only_is_not_included(self):
        row = self.p(self.ALIVE, pinned=True)
        self.assertEqual(self.rows([row]), [])

    def test_stale_active_excluded(self):
        self.rows_excluded(status="active")

    def rows_excluded(self, status="active", pinned=None):
        row = self.p(self.GONE, status=status,
                     pinned=pinned if pinned is not None else False)
        self.assertEqual(self.rows([row]), [])

    def test_stale_pinned_excluded(self):
        self.rows_excluded(pinned=True)

    def test_archived_alive_excluded(self):
        self.assertEqual(self.rows([self.p(self.ALIVE, status="archived")]),
                         [])

    def test_idea_alive_excluded(self):
        self.assertEqual(self.rows([self.p(self.ALIVE)]), [])

    def test_sorting_newest_first_and_cap(self):
        items = []
        for i in range(25):
            items.append(self.p(f"C:\\l\\p{i:02d}", status="active",
                                commit=f"2026-08-{i+1:02d}"))
            self.exists.add(f"C:\\l\\p{i:02d}")
        rows = working_on_now_rows(items, exists=self.exists_fn)
        self.assertEqual(len(rows), 20)
        dates = [r["last_commit_date"] for r in rows]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_membership_does_not_mutate_inputs(self):
        row = self.p(self.ALIVE, status="active")
        before = dict(row)
        working_on_now_rows([row], exists=self.exists_fn)
        self.assertEqual(row, before)


class LauncherQualityTests(unittest.TestCase):
    """Launcher selection demotes utility scripts."""

    def test_configure_scripts_demoted(self):
        from repo_manager import launchers
        self.assertIsNotNone(
            launchers.UTILITY_NAME_RE.search("CONFIGURE_REMOTE_STUDIO"))

    def test_gradlew_demoted(self):
        from repo_manager import launchers
        self.assertIsNotNone(launchers.UTILITY_NAME_RE.search("gradlew"))

    def test_operational_verbs_demoted(self):
        from repo_manager import launchers
        for name in ("DISABLE_REMOTE_STUDIO", "MIGRATE_FROM_EXISTING",
                     "RECOVER_STUCK_VALIDATION"):
            self.assertIsNotNone(launchers.UTILITY_NAME_RE.search(name),
                                 name)

    def test_unknown_tier_ranks_below_build(self):
        from repo_manager import launchers
        self.assertLess(launchers.PRIORITY_NPM_BUILD,
                        launchers.npm_script_priority("catalog:check"))
        self.assertLess(launchers.PRIORITY_NPM_BUILD,
                        launchers.npm_script_priority("mappings:sync"))

    def test_build_wins_over_unknown_tier_in_selection(self):
        from repo_manager import launchers
        sugs = [{"label": "npm run build", "type": "npm",
                 "priority": launchers.PRIORITY_NPM_BUILD, "healthy": True},
                {"label": "[project] npm run catalog:check", "type": "npm",
                 "priority": launchers.PRIORITY_NPM_OTHER, "healthy": True}]
        star = launchers.select_primary_command(sugs)
        self.assertEqual(star["label"], "npm run build")


class GitTargetFreshnessTests(unittest.TestCase):
    def test_snapshot_requires_same_live_project_and_path(self):
        live = {"project_id": "p-1", "path": r"C:\\repo"}
        snapshot = git_target_snapshot(live)
        self.assertTrue(git_target_is_authorized([live], snapshot))
        live["path"] = r"D:\\replacement"
        self.assertFalse(git_target_is_authorized([live], snapshot))

    def test_legacy_snapshot_is_not_authorized_by_equal_values(self):
        live = {"path": r"C:\\repo"}
        snapshot = git_target_snapshot(dict(live))
        self.assertFalse(git_target_is_authorized([live], snapshot))

    def test_same_project_and_path_is_current(self):
        current = {"project_id": "p-1", "path": r"C:\repo"}
        self.assertTrue(git_target_is_current(
            [current], {"project_id": "p-1", "path": r"C:\repo"}))

    def test_reassociation_or_removal_invalidates_preview(self):
        target = {"project_id": "p-1", "path": r"C:\old"}
        self.assertFalse(git_target_is_current(
            [{"project_id": "p-1", "path": r"D:\new"}], target))
        self.assertFalse(git_target_is_current([], target))

    def test_removed_target_cannot_be_authorized(self):
        live = {"project_id": "p-1", "path": r"C:\\repo"}
        snapshot = git_target_snapshot(live)
        self.assertFalse(git_target_is_authorized([], snapshot))

    def test_repository_marker_replacement_blocks_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            marker = root / ".git"
            marker.mkdir(parents=True)
            live = {"project_id": "p-1", "path": str(root)}
            snapshot = git_target_snapshot(live)
            self.assertTrue(git_mutation_target_is_authorized(
                [live], snapshot))
            marker.rename(root / ".git-old")
            marker.mkdir()
            self.assertFalse(git_mutation_target_is_authorized(
                [live], snapshot))


class ScanResultReconciliationTests(unittest.TestCase):
    def test_equivalent_path_variation_does_not_retain_duplicate_identity(self):
        live = [{"project_id": "live", "path": r"D:\Games\COLDLINE",
                 "name": "COLDLINE", "focus": "keep"}]
        merged = [
            dict(live[0]),
            {"project_id": "scan", "path": "d:/games/coldline/.",
             "name": "Scanner duplicate", "focus": "must not transfer"},
        ]

        result = reconcile_scan_result(merged, live)

        self.assertEqual(result, live)

    def test_distinct_canonical_paths_remain_distinct(self):
        live = [{"project_id": "repo", "path": r"D:\Repo",
                 "name": "Repo", "focus": "keep"}]
        merged = [dict(live[0]),
                  {"project_id": "repo-2", "path": r"D:\Repo-2",
                   "name": "Repo 2", "focus": "separate"}]

        result = reconcile_scan_result(merged, live)

        self.assertEqual({record["project_id"] for record in result},
                         {"repo", "repo-2"})

    def test_current_project_fields_survive_same_path_scan(self):
        live = [{"project_id": "p-1", "path": r"C:\repo",
                 "name": "Curated", "focus": "new", "future": 7,
                 "dirty": 0}]
        merged = [{"project_id": "p-1", "path": r"C:\repo",
                   "name": "Snapshot", "focus": "old", "future": 1,
                   "dirty": 3}]
        result = reconcile_scan_result(merged, live)
        self.assertEqual(result[0]["name"], "Curated")
        self.assertEqual(result[0]["focus"], "new")
        self.assertEqual(result[0]["future"], 7)
        self.assertEqual(result[0]["dirty"], 3)

    def test_reassociation_during_scan_keeps_live_identity_once(self):
        live = [{"project_id": "p-1", "path": r"D:\new",
                 "name": "Current", "focus": "keep"}]
        merged = [
            {"project_id": "p-1", "path": r"C:\old", "name": "Old"},
            {"project_id": "scan-new", "path": r"D:\new", "name": "New"},
        ]
        result = reconcile_scan_result(merged, live)
        self.assertEqual(result, live)

    def test_name_and_remote_similarity_do_not_transfer_stale_identity(self):
        live = [{"project_id": "old-ego", "path": r"C:\old\Ego Shooter",
                 "name": "Ego Shooter", "status": "active",
                 "focus": "old curation", "remote": "example/game"}]
        merged = [{"project_id": "new-coldline",
                   "path": r"D:\new\COLDLINE", "name": "COLDLINE",
                   "status": "idea", "focus": "",
                   "remote": "example/game"}]

        result = reconcile_scan_result(merged, live)
        by_id = {record["project_id"]: record for record in result}

        self.assertEqual(set(by_id), {"old-ego", "new-coldline"})
        self.assertEqual(by_id["old-ego"]["focus"], "old curation")
        self.assertEqual(by_id["old-ego"]["path"], r"C:\old\Ego Shooter")
        self.assertEqual(by_id["new-coldline"]["path"], r"D:\new\COLDLINE")

    def test_stale_scan_cannot_reactivate_newly_ignored_project(self):
        # The worker snapshot was captured before the current Project was
        # explicitly ignored, so its payload carries the old active state.
        live = [{"project_id": "p-1", "path": "repo",
                 "name": "Current", "status": "active", "ignored": True,
                 "focus": "keep", "pinned": True}]
        merged = [{"project_id": "p-1", "path": "repo",
                   "name": "Scanner snapshot", "status": "idea",
                   "ignored": False, "focus": "stale"}]

        result = reconcile_scan_result(merged, live)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["project_id"], "p-1")
        self.assertTrue(result[0]["ignored"])
        self.assertEqual(result[0]["status"], "active")
        self.assertEqual(result[0]["focus"], "keep")
        self.assertTrue(result[0]["pinned"])

    def test_stale_scan_cannot_reignore_explicitly_restored_project(self):
        live = [{"project_id": "p-1", "path": "repo",
                 "name": "Current", "status": "active", "ignored": False,
                 "focus": "keep", "pinned": True}]
        merged = [{"project_id": "p-1", "path": "repo",
                   "name": "Scanner snapshot", "status": "idea",
                   "ignored": True, "focus": "stale", "pinned": False}]

        result = reconcile_scan_result(merged, live)

        self.assertEqual(result, live)

    def test_different_project_ids_same_path_do_not_transfer_ignored_state(self):
        live = [{"project_id": "live-b", "path": r"C:\repo",
                 "name": "Live B", "status": "active", "ignored": True,
                 "focus": "B focus"}]
        merged = [{"project_id": "scan-a", "path": r"C:\repo",
                   "name": "Scan A", "status": "idea", "ignored": False,
                   "focus": "A focus"}]

        result = reconcile_scan_result(merged, live)

        self.assertEqual(result, live)

    def test_stale_ignored_identity_cannot_reignore_live_identity_at_same_path(self):
        live = [{"project_id": "live-b", "path": r"C:\repo",
                 "name": "Live B", "status": "active", "ignored": False,
                 "focus": "B focus", "pinned": True}]
        merged = [{"project_id": "scan-a", "path": r"C:\repo",
                   "name": "Scan A", "status": "idea", "ignored": True,
                   "focus": "A focus", "pinned": False}]

        result = reconcile_scan_result(merged, live)

        self.assertEqual(result, live)

    def test_idless_legacy_record_can_still_match_by_path(self):
        live = [{"path": r"C:\repo", "name": "Current",
                 "status": "active", "ignored": True}]
        merged = [{"project_id": "scan-a", "path": r"C:\repo",
                   "name": "Snapshot", "ignored": False}]

        result = reconcile_scan_result(merged, live)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Current")
        self.assertTrue(result[0]["ignored"])


class RecoveryHardeningTests(unittest.TestCase):
    """mutation failures must stop the chain and stay visible."""

    OLD_PATH = r"C:\Users\u\Desktop\Iron"
    NEW_PATH = r"C:\Users\u\Desktop\Projekte\Iron"
    NOW = "2026-08-26T12:00:00Z"

    @staticmethod
    def suggestion():
        return {"kind": "move", "category": "strong",
                "old_path": RecoveryHardeningTests.OLD_PATH,
                "new_path": RecoveryHardeningTests.NEW_PATH,
                "name": "Iron", "evidence": ["Same normalized remote"]}

    def _projects(self):
        return [{"path": self.OLD_PATH, "name": "Iron",
                 "status": "active", "focus": "district refactor",
                 "pinned": True, "added_at": "2026-01-01T00:00:00Z",
                 "last_seen": "2026-08-25T00:00:00Z"}]


    def test_save_failure_after_note_migration_reports_rollback_failed(self):
        """save failure after the note moved is a failed rollback."""
        projects = self._projects()
        projects[0]["project_id"] = "project-1"
        snapshot = dict(projects[0])

        outcome, entry = perform_confirmed_move(
            projects, self.suggestion(), self.NOW,
            save_projects=self._fail,
            move_note=lambda *a: "moved")

        self.assertEqual(outcome, MOVE_ROLLBACK_FAILED)
        self.assertEqual(projects[0], snapshot)  # in-memory entry restored
        self.assertEqual(entry, snapshot)

    def test_compensation_failure_after_note_failure_reports_rollback_failed(self):
        """an unconfirmed registry rollback must be reported as such."""
        projects = self._projects()
        snapshot = dict(projects[0])
        saves = []

        def save():
            saves.append(1)
            if len(saves) == 2:
                raise OSError("compensation save failed")

        outcome, _entry = perform_confirmed_move(
            projects, self.suggestion(), self.NOW,
            save_projects=save,
            move_note=lambda *a: (_ for _ in ()).throw(
                OSError("rename locked")))

        self.assertEqual(outcome, MOVE_ROLLBACK_FAILED)
        self.assertEqual(projects[0], snapshot)
        self.assertEqual(len(saves), 2)  # commit attempt + failed rollback

    def test_unchanged_note_save_failure_still_reports_registry_rollback(self):
        """When the note never moved, a save failure is a clean rollback."""
        projects = self._projects()
        projects[0]["project_id"] = "project-1"
        snapshot = dict(projects[0])

        outcome, _entry = perform_confirmed_move(
            projects, self.suggestion(), self.NOW,
            save_projects=self._fail,
            move_note=lambda *a: "note_failed")

        self.assertEqual(outcome, "rolled_back_registry")
        self.assertEqual(projects[0], snapshot)

    @staticmethod
    def _fail(*a, **k):
        raise OSError("injected failure")


class MoveMatcherBruteForceEquivalenceTests(unittest.TestCase):
    """The indexed matcher must produce byte-identical results to the original
    brute-force scan across representative large stale/fresh combinations."""

    @staticmethod
    def stale(path, remote=None, remotes=None, roots=None):
        entry = {"path": path, "name": os.path.basename(path), "status": "idea",
                 "focus": "", "pinned": False}
        if remote:
            entry["remote"] = remote
        fp = {}
        if remotes is not None:
            fp["remotes"] = remotes
        if roots is not None:
            fp["root_commits"] = roots
        if fp:
            entry["fingerprint"] = fp
        return entry

    @staticmethod
    def bruteforce(stale_entries, fresh_items, suppressed=None):
        """Reference implementation using every stale/fresh pair."""

        def evidence_for(entry):
            fp = entry.get("fingerprint") if isinstance(entry, dict) else None
            remotes = set()
            if isinstance(fp, dict):
                remotes = {r.lower() for r in fp.get("remotes", [])
                           if isinstance(r, str)}
            if not remotes and isinstance(entry.get("remote"), str):
                remotes = {entry["remote"].lower()}
            roots = set()
            if isinstance(fp, dict):
                roots = {r for r in fp.get("root_commits", [])
                         if isinstance(r, str)}
            return remotes, roots

        def fresh_evidence(fingerprint):
            if not isinstance(fingerprint, dict):
                return set(), set()
            remotes = {r.lower() for r in fingerprint.get("remotes", [])
                       if isinstance(r, str)}
            return remotes, {r for r in fingerprint.get("root_commits", [])
                             if isinstance(r, str)}

        supp_set = set()
        for s in suppressed or []:
            if isinstance(s, dict):
                supp_set.add((str(s.get("old", "")).lower(),
                              str(s.get("new", "")).lower()))
            else:
                o, n = s
                supp_set.add((str(o).lower(), str(n).lower()))

        pairs = []
        for entry in sorted(stale_entries,
                            key=lambda e: str(e.get("path", "")).lower()):
            old_path = entry.get("path")
            if not isinstance(old_path, str):
                continue
            old_remotes, old_roots = evidence_for(entry)
            old_folder = os.path.basename(str(old_path)).lower()
            for new_path, fp in sorted(fresh_items,
                                       key=lambda x: x[0].lower()):
                if (old_path.lower(), new_path.lower()) in supp_set:
                    continue
                new_remotes, new_roots = fresh_evidence(fp)
                evidence = []
                shared_remote = bool(old_remotes & new_remotes)
                shared_roots = old_roots & new_roots
                same_folder = old_folder == os.path.basename(new_path).lower()
                folder_eq = same_folder and len(old_folder) >= 5
                if shared_remote:
                    evidence.append("Same normalized remote: "
                                    + sorted(old_remotes & new_remotes)[0])
                if shared_roots:
                    evidence.append(f"Root history matches ({len(shared_roots)})")
                if folder_eq:
                    evidence.append("Folder name matches")
                if not evidence:
                    continue
                if shared_roots or (shared_remote and same_folder):
                    category = "strong"
                else:
                    category = "possible"
                pairs.append({"kind": "move", "category": category,
                              "old_path": old_path,
                              "old_project_id": projects.project_id(entry),
                              "new_path": new_path,
                              "name": entry.get("name")
                              or os.path.basename(old_path),
                              "evidence": evidence,
                              "identity": {
                                  "remotes": sorted(new_remotes),
                                  "root_commits": sorted(new_roots),
                              }})

        by_old, by_new = {}, {}
        for p in pairs:
            by_old.setdefault(p["old_path"].lower(), []).append(p)
            by_new.setdefault(p["new_path"].lower(), []).append(p)
        contested = {id(p) for p in pairs
                     if len(by_old[p["old_path"].lower()]) > 1
                     or len(by_new[p["new_path"].lower()]) > 1}
        final, emitted = [], set()
        for p in pairs:
            if id(p) not in contested:
                final.append(p)
                continue
            ok = p["old_path"].lower()
            nk = p["new_path"].lower()
            gkey = ("old", ok) if len(by_old[ok]) > 1 else ("new", nk)
            if gkey in emitted:
                continue
            emitted.add(gkey)
            if gkey[0] == "old":
                members = by_old[ok]
                final.append({"kind": "move", "category": "ambiguous",
                              "old_path": p["old_path"],
                              "new_paths": [m["new_path"] for m in members],
                              "name": p["name"],
                              "evidence": ["Multiple candidate locations"],
                              "candidates": [dict(member) for member in members]})
            else:
                members = by_new[nk]
                final.append({"kind": "move", "category": "ambiguous",
                              "old_paths": [m["old_path"] for m in members],
                              "new_path": p["new_path"],
                              "name": os.path.basename(p["new_path"]),
                              "evidence": ["Matches multiple vanished entries"],
                              "candidates": [dict(member) for member in members]})
        return sorted(final, key=lambda s: (
            str(s.get("old_path") or s.get("old_paths")[0]).lower(),
            str(s.get("new_path") or "").lower()))

    def test_distinct_remote_dataset(self):
        rnd = random.Random(1)
        stale = [self.stale(rf"C:\old\p{i}", remote=f"g.com/o/p{i}",
                            roots=[f"root{i}"]) for i in range(120)]
        fresh = [(rf"C:\new\m{i}",
                  {"remotes": [f"g.com/o/m{i}"],
                   "root_commits": [f"root{i}"]}) for i in range(300)]
        # embellish a few to create shared-remote/root and folder overlaps
        for i in rnd.sample(range(min(len(fresh), 60)), 60):
            j = rnd.randrange(len(stale))
            fresh[i] = (fresh[i][0], {"remotes": [f"g.com/o/p{j}"],
                                      "root_commits": [f"root{j}"]})
        self.assertEqual(scanner.match_move_candidates(stale, fresh),
                         self.bruteforce(stale, fresh))

    def test_folder_and_suppressed_dataset(self):
        rnd = random.Random(2)
        stale = [self.stale(rf"C:\old\widget-toolkit{i}")
                 for i in range(80)]
        fresh = [(rf"C:\new\widget-toolkit{i}", None) for i in range(200)]
        suppressed = [(rf"c:\old\widget-toolkit{i}",) and
                      (rf"c:\old\widget-toolkit{i}", rf"c:\new\widget-toolkit{i}")
                      for i in rnd.sample(range(80), 20)]
        self.assertEqual(
            scanner.match_move_candidates(stale, fresh, suppressed),
            self.bruteforce(stale, fresh, suppressed))

    def test_shared_remote_hub_dataset(self):
        # many stale and many fresh all sharing a single org remote + roots
        stale = [self.stale(rf"C:\old\p{i}", remote="g.com/org/shared",
                            remotes=["g.com/org/shared"], roots=["rr"])
                 for i in range(100)]
        fresh = [(rf"C:\new\m{i}", {"remotes": ["g.com/org/shared"],
                                    "root_commits": ["rr"]})
                 for i in range(150)]
        self.assertEqual(scanner.match_move_candidates(stale, fresh),
                         self.bruteforce(stale, fresh))

    def test_empty_and_disjoint(self):
        self.assertEqual(scanner.match_move_candidates([], []),
                         self.bruteforce([], []))
        stale = [self.stale(r"C:\x\A", remote="g.com/a")]
        fresh = [(r"C:\y\B", {"remotes": ["g.com/b"], "root_commits": ["q"]})]
        self.assertEqual(scanner.match_move_candidates(stale, fresh),
                         self.bruteforce(stale, fresh))


class MergeScanValidityTests(unittest.TestCase):
    def test_17_merge_status_timeout_preserves_dirty(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            cached = curated(scanner.collect_metadata(str(repo)))
            cached["dirty"] = 0
            real_git = scanner._git

            def fail_status(path, *args):
                if tuple(args) == ("status", "--porcelain"):
                    return None
                return real_git(path, *args)

            with mock.patch.object(scanner, "_git", side_effect=fail_status):
                merged, _ = scanner.merge_scan([dict(cached)], [str(repo)])
            self.assertEqual(merged[0]["dirty"], 0)
            self.assertFalse(merged[0]["status_available"])
            self.assertEqual(merged[0]["branch"], "main")

    def test_18_merge_remote_worktree_partial_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            git_ok(repo, "remote", "add", "origin",
                   "git@github.com:owner/repo.git")
            cached = curated(scanner.collect_metadata(str(repo)))
            real_git = scanner._git

            def fail_remote(path, *args):
                if tuple(args) == ("remote", "-v"):
                    return None
                return real_git(path, *args)

            with mock.patch.object(scanner, "_git", side_effect=fail_remote), \
                    mock.patch.object(scanner, "_worktree_state",
                                      return_value=None):
                merged, _ = scanner.merge_scan([dict(cached)], [str(repo)])
            self.assertEqual(merged[0]["remote"], "github.com/owner/repo")
            self.assertEqual(merged[0]["remotes"], ["github.com/owner/repo"])
            self.assertEqual(merged[0]["worktrees"], cached["worktrees"])
            self.assertTrue(merged[0]["worktrees_available"])

    def test_19_merge_mixed_success_updates_failed_preserves(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            cached = curated(scanner.collect_metadata(str(repo)))
            (repo / "f.txt").write_text("dirty-change\n", encoding="utf-8")
            real_git = scanner._git

            def fail_remote_only(path, *args):
                if tuple(args) == ("remote", "-v"):
                    return None
                return real_git(path, *args)

            with mock.patch.object(scanner, "_git",
                                   side_effect=fail_remote_only):
                merged, _ = scanner.merge_scan([dict(cached)], [str(repo)])
            # status succeeded and observed new dirty state
            self.assertGreater(merged[0]["dirty"], 0)
            self.assertTrue(merged[0]["status_available"])
            # remote failed and preserved (both were empty here; use
            # fingerprint roots to prove successful field updated anyway)
            self.assertEqual(merged[0]["branch"], "main")


class MoveIdentityValidityTests(unittest.TestCase):
    """FIX-1 B: unobserved fingerprint never authorizes identity."""

    def test_26_fingerprint_failure_returns_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            real_git = scanner._git

            def fail_remote(path, *args):
                if tuple(args) == ("remote", "-v"):
                    return None
                return real_git(path, *args)

            with mock.patch.object(scanner, "_git",
                                   side_effect=fail_remote), \
                    mock.patch.object(scanner, "_worktree_state",
                                      return_value=None):
                # Force roots failure as well so both parts unobserved.
                real_result = scanner._git_result

                def fail_roots(path, *args):
                    if tuple(args)[:2] == ("rev-list", "--max-parents=0"):
                        return (1, "", "fatal: transient glitch")
                    return real_result(path, *args)

                with mock.patch.object(scanner, "_git_result",
                                       side_effect=fail_roots):
                    self.assertIsNone(
                        scanner.move_target_identity(str(repo)))

    def test_27_observed_empty_fingerprint_stays_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty"
            empty.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=empty,
                           check=True)
            identity = scanner.move_target_identity(str(empty))
            self.assertIsNotNone(identity)
            self.assertEqual(identity["remotes"], [])
            self.assertEqual(identity["root_commits"], [])


class VerifyPostRunValidityTests(unittest.TestCase):
    """FIX-1 C: incomplete post-run observation never claims rechecked."""

    def _run(self, cwd):
        from repo_manager import agents
        return agents.new_run({"agent_id": "a", "target": {}, "cwd": cwd,
                               "argv": ["stub"],
                               "launch_time": agents.utc_now()})

    def test_28_infrastructure_failure_not_rechecked(self):
        from repo_manager import agents
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            run = self._run(str(repo))
            agents.verify_post_run(
                run,
                observe=lambda cwd: (scanner.empty_meta(cwd), frozenset()))
            self.assertEqual(run["verification"], agents.UNKNOWN)
            self.assertNotEqual(run["verification"],
                                agents.TARGET_RECHECKED)
            self.assertNotIn("post_run", run)

    def test_29_partial_status_failure_is_unknown(self):
        from repo_manager import agents
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            meta_ok, obs_ok = scanner.collect_metadata_observation(str(repo))
            partial = (set(obs_ok)
                       - {"dirty", "status_available", "staged",
                          "unstaged", "untracked"})
            run = self._run(str(repo))
            agents.verify_post_run(
                run, observe=lambda cwd: (dict(meta_ok),
                                          frozenset(partial)))
            self.assertEqual(run["verification"], agents.UNKNOWN)
            self.assertNotIn("post_run", run)

    def test_30_full_success_still_rechecked(self):
        from repo_manager import agents
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            run = self._run(str(repo))
            agents.verify_post_run(
                run, observe=scanner.collect_metadata_observation)
            self.assertEqual(run["verification"], agents.TARGET_RECHECKED)
            self.assertIn("post_run", run)
            self.assertIsNotNone(run["post_run"]["head"])

    def test_31_missing_target_still_failed(self):
        from repo_manager import agents
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "gone")
            run = self._run(missing)
            agents.verify_post_run(
                run, observe=scanner.collect_metadata_observation)
            self.assertEqual(run["verification"], agents.FAILED)



class GitMutationGuardTests(unittest.TestCase):
    def test_same_repository_is_serialized_and_release_reopens_it(self):
        guard = GitMutationGuard()
        key = ("C:/repo/.git", 1, 2)
        self.assertTrue(guard.acquire(key))
        self.assertFalse(guard.acquire(key))
        guard.release(key)
        self.assertTrue(guard.acquire(key))
