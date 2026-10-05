# RepoManager — Roadmap

## Public v0.1.1 / prepared v0.1.2 release scope

Public RepoManager v0.1.1 is already released from the separate Public Release
repository. The curated v0.1.2 source includes post-v0.1.1 hardening,
move reconciliation and the Qt presentation. Publication remains a separate
owner-controlled step.

The prepared v0.1.2 release provides:

- bounded local Git discovery and metadata display;
- stable Project IDs, curation, notes, filtering, sorting, and one-repository-per-Project association;
- JSON persistence with validation, atomic replacement, rotating backups, quarantine, and recovery;
- advisory move suggestions;
- explicit launchers, independent staged-only/local Commit (optional stage-all),
  confirmed Push, fast-forward-only Pull, and Fetch;
- bounded Changes/Diff, per-file Stage/Unstage, History, and read-only Remotes;
- target-bound accessible menus/sidebar Git actions and selected metadata refresh;
- resizable scan/move review with proven exact-pair manual ambiguity approval;
- transient read-only Health;
- Workspace metadata/inspection and Worktree observation (create/remove domain functions exist internally; no UI workflow exposed);
- read-only GitHub observation and bounded metadata/report export;
- background work, logging, theming, and incremental UI updates.

The application runs from source on Windows. Its intended public distribution
is an unsigned portable Windows build; any future artifact from this
source requires explicit publication approval plus clean
curated release preparation, and must be built
and verified from a curated release source. Linux and macOS are deferred,
not supported by generic source availability.

The current UI also includes Help/Guidance, empty-state guidance, keyboard
behavior, dark and Ice Light themes, responsive/bounded columns, a scrollable
detail panel, semantic state colors, and Workspace/Agent context presentation.
These are current v0.1.2 capabilities, not roadmap promises; the newest
everyday Git and review UI additions are not part of the public v0.1.1 artifact.

## Current / release-relevant

- keep source-run instructions, repository documentation, and release identity
  synchronized;
- decide the production signing identity and signing process;
- decide whether a later installer materially improves distribution, including
  install, upgrade, and uninstall behavior;
- decide the post-V1 update channel.

## POST-V1 / deferred

- a mature non-Git Project lifecycle and folder import;
- explicit Register, Import, Initialize, and Setup flows with rollback rules;
- remaining everyday Git operations: Branch, hunk staging, conflict resolution,
  submodule writes, and independently designed stash/discard/history-rewriting flows;
- stronger repository identity/origin treatment for clones, forks, mirrors,
  moved paths, and archives;
- coordinated multi-repository Workspaces and full Worktree lifecycle management;
- Provider authentication and write/admin operations;
- configurable Policy, Profile, and Workflow support;
- Drift detection and an Attention view;
- import, archive, package, restore, and richer export formats;
- Agent sessions, orchestration, isolation, review, and merge support;
- expanded dependency, technical-stack, and documentation intelligence;
- additional adversarial and scale hardening outside the release path;
- non-fatal Tk test-harness callback-noise cleanup if it remains reproducible.

Each of these needs clear safety rules and useful tests before it becomes part of the product.

## Deferred beyond v0.1.2

Custom themes, background/opacity personalization, Agent History, advanced
Agent workflows, Starship, Universal AI Integration Agent and Guard integrations,
cloud services, monetization/paywalls/Pro/Cloud plans, Linux/macOS support,
full self-update and GitHub Pages documentation are not part of v0.1.2.
Full user, Changes/staging, Health and Agent guides and refreshed public
screenshots belong to a separate documentation program after release approval.

## Historical v0.1.1 boundary

RepoManager 0.1.1 is not a full Git replacement, Provider administration console, cloud-sync service, unattended automation engine, multi-repository Project manager, full Workspace/Worktree manager, Agent orchestrator, policy platform, Knowledge-Base application, or packaged installer. Full competitor feature parity, a database/service/plugin architecture, and rich visual maps are also outside this release.

Open POST-V1 product choices include the non-Git Project model, folder-import
behavior, repository identity rules, ownership/origin classification, Project
versus Workspace responsibilities, the first Provider credential boundary,
policy precedence, import/package format, and Agent/session scope. The Windows
update channel remains a release decision rather than a V1 application
feature.
