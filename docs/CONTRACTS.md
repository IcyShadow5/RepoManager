# RepoManager — Contracts

These are the behavioral rules that other parts of RepoManager should rely on.

## Identity and authority

- Project IDs remain stable when a repository moves. A Project is a curated logical record; a Repository is a known local Git repository.
- A valid folder-only Project record remains a Project even when it is not a
  repository scan target. Scan/merge retention must preserve its stable
  `project_id` and `folder_path`.
- The current model associates at most one Repository with a Project.
- A repository path identifies a location, not a Project.
- A Working Tree is checkout content. A Branch is Git history state. A Worktree is a Git-managed checkout. None of these is a Project or a Workspace.
- Workspace metadata records membership and supports inspection. It does not coordinate changes across repositories.
- Git is the source for local branch, status, history, and configured remotes. RepoManager metadata contains curation and observations and can be stale.
- A Remote is a URL. Provider data is informational and does not prove access, ownership, or authorization.
- Classification or scan location does not grant ownership, trust, or permission to modify a repository.

On Windows, automatic/read-only Git observations run native Git in a job that
cannot start child processes. Repository-local fsmonitor helpers are disabled;
filters, implicit transports and other helper paths cannot execute. Failed or
diagnostic observations remain unavailable. Cached working-tree counts do not
override current `status_available=False`. Submodule observations requiring a
Git child process also fail closed; use an external Git tool for affected parent
repositories. This boundary does not sandbox the installed Git binary, explicit
Git mutation/network actions or user-launched Agents.

Read-only Git uses `core.safecrlf=false` to suppress write-related LF/CRLF
round-trip warnings. Line-ending conversion still applies. Explicit Git writes
keep the configured safeguard; the child-process and other diagnostic checks
remain unchanged.

Scanner fingerprints use normalized remotes and root commits to suggest possible moves. They do not prove identity, ownership, authorization, or a move. Suggestions are advisory and require confirmation.

An exact-path scan merge updates repository observations without replacing
Project identity or curation. A missing scan result is not, by itself,
authorization to delete a Project record.

## Health

Health is a transient, read-only report about observed repository conditions. Findings contain a rule, status, severity, evidence, explanation, and timestamp. The available statuses are `PASS`, `WARN`, `FAIL`, `UNKNOWN`, and `NOT_APPLICABLE`.

Health does not certify repository correctness, authorize actions, block operations, or repair anything. Findings based on scanner metadata must say when the metadata may be stale. The current overall-status order (`FAIL`, `UNKNOWN`, `WARN`, `PASS`) is an implementation detail.

## Agents and providers

An Agent run is an explicitly launched local command. Process completion and post-run information do not prove that the Agent’s work is correct. RepoManager does not currently provide Agent sessions, orchestration, sandboxing, task acceptance, or review/merge authority.

Local Provider correspondence is descriptive evidence derived from a supported remote form. Online Provider observation remains separate from local Git operations and is currently GitHub-only. Unsupported online integrations must not be reported as provider absence, and local Git state must not be treated as proof of access, ownership, or authorization.

## Mutations

- Discovery, metadata collection, Health, and Provider observation do not modify managed repositories.
- Detecting a launcher does not execute it. Launcher and Agent execution require explicit user action.
- Changing a Project’s repository association changes RepoManager metadata only. It does not move files, run Git, change branches or remotes, or delete anything.
- Move matching never changes data without confirmation. Ambiguous groups stay unresolved until an exact candidate pair is explicitly selected and confirmed. Absorption requires proven scan-created counterpart identity and pristine curation/notes; paths alone never grant that authority.
- Git writes require explicit UI initiation. Independent Commit defaults to staged-only, permits explicit stage-all, requires a message, needs no remote, and never pushes. Unstage changes only the index, not working files. Push previews a non-forced destination; upstream setup is explicit. Pull is fast-forward-only with a clean checkout. Fetch is explicit and does not prune or alter checkout files.
- Git previews are snapshots, not external-process locks. Live branch/HEAD, index, association, physical identity, and destinations are rechecked; changed evidence cancels the approval. Failed/uncertain writes are re-observed and never automatically retried. Staging before a failed commit may remain in the index.
- Future high-risk or history-rewriting operations need their own preview, confirmation, failure, and recovery rules.

## Persistence and artifacts

Registry and settings are JSON data outside managed repositories. Registry writes use temporary-file flush and atomic replacement, rotating backups, validation, and corruption quarantine. Valid backups may be used for recovery. Atomic replacement does not coordinate concurrent writers. The current registry schema is version 2, and notes are separate Markdown files.

Health is not persisted or cached. A metadata export or generated report is not a persistence backup, repository archive, or complete restore package.

Export/report filtering omits fields with credential-like keys. It does not
inspect arbitrary string content for embedded secrets, so users must review an
artifact before sharing it. Notes are ordinary local Markdown files and are
not secret-scanned.

## Terms that must remain separate

Project ≠ Repository; Repository ≠ Working Tree; Branch ≠ Worktree; Project ≠ Workspace; Git ≠ Provider; Remote ≠ Provider; Health ≠ Policy; Workflow ≠ Policy; Policy ≠ Settings; Backup ≠ Export; Repository Archive ≠ Project Export.

Non-Git Project lifecycle, multiple repository associations, coordinated Workspaces, full Worktree lifecycle management, Agent sessions, Provider writes, Policy/Profile/Workflow engines, Drift, Attention, and import/archive packages are future work rather than current contracts.
