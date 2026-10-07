# RepoManager — Testing

RepoManager uses standard-library `unittest`. The current product is Python
3.14.7 plus PySide6/QML; install the existing pinned
`packaging/requirements-qt.txt` in the test environment. Qt is a runtime/test
dependency. Tkinter is retained only for the separate classic parity reference.

## Current product gate

Run from the repository root with the verified Python environment:

```text
python -B -m tests.run_layers --layer current
```

The reviewed `tests/suite_manifest.json` assigns every test ID to one category.
The runner refuses unclassified, duplicate or deleted IDs. The current gate
imports no `repo_manager.main` and fails if a selected current test is skipped;
missing Git, Qt or Windows capabilities must not silently create a green
release claim. `--list` prints category counts without importing presentation
modules. Counts describe a working tree and are not an API contract.

Run individual evidence layers:

```text
python -B -m tests.run_layers --layer core
python -B -m tests.run_layers --layer service
python -B -m tests.run_layers --layer bridge
python -B -m tests.run_layers --layer qml
python -B -m tests.run_layers --layer package
python -B -m tests.run_layers --layer windows
```

`core` covers shared domain/scan/persistence contracts. `service` exercises
application boundaries, including real temporary Git repositories. `bridge`
instantiates Qt controllers/models and processes signals; it does not by itself
render QML. `qml` launches real Main.qml/components in isolated subprocesses,
uses QTest events, checks Git mutations only in temporary repositories and
captures both global Qt messages and engine warnings. Offscreen Windows probes
use the system Fonts directory; this is synthetic UI evidence, not physical DPI
or native taskbar acceptance.

`package` covers build preflight, collection, licenses, provenance, QML import
closure and gate integrity. These are tooling tests, **not extracted-EXE launch
proof**. `windows` mixes explicit mocked launch/metadata boundaries with actual
controlled child-process and native-property tests. A property readback does
not prove the physical taskbar icon or a Terminal prompt.

## Historical reference and full repository run

```text
python -B -m tests.run_layers --layer legacy
python -B -m tests.run_layers --layer auxiliary
python -B -m tests.run_layers --layer all
python -B -m unittest discover -s tests -v
```

`tests/legacy/` is **LEGACY / PARITY REFERENCE — NOT CURRENT UI PROOF**.
The obsolete classic Light-default and Tk license-collector contracts remain
quarantined rather than deleted. Valid contracts imported accidentally through
Tk were moved to `test_current_contracts.py` or rebound to neutral production
modules. UA05 maintenance tools have their own auxiliary, non-product gate.
The full discovery command retains all references; its total must never be
presented as current Qt UI coverage. CI runs the current gate and the historical
reference in separately labelled jobs.

## Portable and manual acceptance

Launch the exact built EXE from an extracted clean directory with isolated
application data and controlled repositories. Use the guarded existing
`packaging/qt_runtime_probe.py` instrumentation embedded in development packages
for repeatable package evidence; it refuses owner data and unmarked roots.
`--rc-qa <configuration.json>` is opt-in development QA, not normal startup.
Its check count is a separate runtime result, never added to unittest counts.
The portable acceptance harness is `tests/portable_acceptance.py`; it requires
an explicitly marked isolated QA root and rejects owner application data.
Rebuild after changes to the release source revision so package provenance
identifies the exact commit being distributed. These prerelease-only
options are inactive for stable versions; final stable acceptance must launch the
ordinary EXE and exercise its UI, not override the prerelease guard.

Normal Windows acceptance also covers native folder/file dialogs, Explorer,
installed VS Code, a visible Terminal prompt/cwd, browser feedback draft,
taskbar icon/title, minimize/restore/relaunch and physical display scaling.
Programmatic/source QML tests do not replace these observations.

## Isolation and limitations

Persistence tests redirect APP_DIR, REPOS_FILE, SETTINGS_FILE and NOTES_DIR.
Git fixtures use temporary repositories and local remotes, bounded subprocess
budgets and disconnected stdin. No test should start an owner AI session,
mutate an owner repository, alter global PATH or use owner credentials.
Missing-runtime skips are tolerated only in the explicit historical reference.

No coverage package is currently installed in the verified environment; there
is no percentage claim. Branch and feature evidence, missing automation and
manual-only acceptance are recorded in
[release evidence](RELEASE_EVIDENCE.md) and the explicit suite manifest. Full host/network/provider
acceptance, WSL sessions and a 10,000-repository performance claim are outside
these tests. Test evidence applies only to the executed assertions and paths.
