# Release evidence — 0.1.2-rc.2

This is an unpublished Windows x64 candidate. Public v0.1.1 remains unchanged.
Candidate identity comes from `repo_manager/version.py`; the exact clean source
commit and binary hashes are recorded in `BUILD_INFO.json` and the build manifest.
Do not use older Development ZIP hashes or a dirty tree's HEAD as provenance.

## Reconciled baseline and current test catalog

The RC1 release gate contained 791 current-product cases: Core 583, Services 99,
Qt Bridge 61, actual QML/UI 6, Packaging/Tooling 27 and Windows Integration 15.
Legacy/reference was 366, auxiliary/helper 37; the complete RC1 catalog was 1194.
Historical larger aggregate claims were not current Qt UI evidence. Intermediate
focused-run logs are not substitutes for the complete classified gate.

The RC2 catalog retains that coverage, adds eight adversarial Git environment
cases, one real QML load-failure case, two PE import classification cases and
one isolated source-companion entrypoint regression.
It also retains 41 valid neutral/public safety tests from the public parent;
their assertions are bound to current backend modules, not Tk widgets. The
move-semantic assertions import `relocation` directly rather than the legacy UI.

| Current evidence layer | RC2 catalog cases |
|---|---:|
| Core | 626 |
| Services | 99 |
| Qt Bridge | 61 |
| Actual QML/UI | 7 |
| Packaging/Tooling | 30 |
| Windows Integration | 21 |
| Current product total | 844 |
| Legacy/reference, separately | 366 |
| Auxiliary/helper, separately | 37 |
| Complete classified catalog | 1247 |

Counts above describe the source catalog. Executed results and runtime probe
outputs belong to the exact candidate freeze. No legacy UI test is current UI
proof. `tests/suite_manifest.json` and `tests/run_layers.py` reject unclassified,
absent and duplicate cases; current-product skips are failures.

Run the current layers (`core`, `service`, `bridge`, `qml`, `package`, `windows`)
using `python -B -m tests.run_layers --layer <layer> --json <output.json>`.
Run `legacy` and `auxiliary` separately. The attribution governance tests under
`.github/tests` are separate policy evidence, not product cases.

## Release-critical feature evidence

| Feature | Concrete current evidence |
|---|---|
| Scan, counters, cancellation, close choices, no partial complete inventory | `test_scan_control`, `test_qt_scan_agent_control`, actual QML interactions and extracted-package probes |
| Inventory/search/selection/Working on now | `test_repository_service`, `test_qt_bridge`, `qt_interaction_smoke`, portable probes |
| Changes/diff/stage/unstage/commit and network approval errors | `test_git_operations`, `test_qt_git`, real temporary repositories and portable Stage/Unstage probes |
| Hostile inherited Git repository/config variables | `test_git_environment`: two real distinct repositories; scan/diff/index/commit/local fetch/pull/push and linked-worktree attribution; protected repository snapshots unchanged |
| Health states, ignore/restore/persistence/score and protected integrity | `test_health`, `test_health_preferences`, `test_qt_owner_corrections`, QML/portable Health controls |
| Run/Quick Run/launcher chooser and process cwd/stop | `test_launchers`, `test_repository_service`, `test_qt_owner_corrections`, guarded portable launch receipts |
| Agent zero/one/multiple/custom/detection/selection/stop | `test_agent_selection`, `test_agents`, Qt bridge and synthetic portable process tests; no real owner AI sessions |
| Settings/Help/Feedback/themes/focus/dialogs | Actual QML interaction/reconciliation/runtime cases and portable probes; browser submission is never automated |
| QML root failure | Actual engine failure, error-dialog boundary, diagnostic file and exit code 1 in `qt_load_failure_smoke` |
| Windows actions/taskbar properties | `test_current_windows`, `test_desktop_identity`, controlled process tests; owner native-window/prompt acceptance is separate |
| Packaging/licenses/QML closure/PE imports | `test_qt_packaging`, actual builder, extracted EXE, `PE_IMPORT_CLOSURE.json`, source/license companion and hashes |

Physical monitor/DPI inspection and real external-tool windows remain operator
evidence. Process-scale tests at 100/125/150 percent do not certify every monitor.
The historical rescan timeout and `DelegateModel::cancel` warning were not
reproduced in bounded preceding checks; no guessed cause is asserted.

## Candidate corrections and deferred limitations

Repository/config-redirection Git environment variables are removed by one
canonical helper for product, scanner, Classic reference and build/probe Git.
Safe non-interactive controls remain; read-only Git disables optional locks.
Normal on-disk Git configuration and transport settings remain effective.

RC builds require clean committed source. Corresponding hash-pinned Qt/PySide
source archives and notices are delivered in the adjacent third-party-sources
ZIP. Expat and liblzma notices are explicit. ICU is supplied by supported Windows,
not an arbitrary build-machine DLL. Direct/delay PE imports are checked against
bundled libraries or approved Windows components.

No product features or UI redesign were added for RC2. Unsigned/SmartScreen,
possible Git-check/note-flush latency, full release automation, attestations,
schema-validated SBOM, custom themes, Agent history, cloud/monetization,
Linux/macOS support and self-update remain outside this correction package.
They are not declared blockers without direct release-blocking evidence.
