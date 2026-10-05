# RepoManager — Policies

RepoManager does not have a general policy engine or unattended automation. These rules describe the safety behavior of the current Qt candidate.

## Current rules

- Repository discovery, metadata collection, Health, and Provider observation are read-only with respect to managed repositories.
- RepoManager never runs a detected launcher automatically. Launcher and configured Agent commands run only after explicit user action.
- Commands run in the selected local Working Tree and use structured arguments where applicable. Local processes are not sandboxed; a child process may change files outside the checked directory.
- RepoManager never moves, merges, overwrites, or deletes project data based only on inferred lineage, path similarity, ownership, or scan results.
- Move suggestions require confirmation. Ambiguous matches require exact-pair selection and confirmation with proven counterpart identity; user-owned target curation and notes remain protected.
- Project association updates through the move/reconciliation flows change RepoManager metadata only (no general repository-association picker is exposed in the UI). They do not move files or modify Git.
- Git writes require explicit UI initiation. Commit and Push are independent; Commit is local and staged-only by default, with explicit stage-all. Unstage does not discard working files. Pull is fast-forward-only. Push cannot force; Fetch does not prune. Changed approval evidence cancels actions; no automatic mutation retries.
- Changes/History/Remotes are bounded read-only views. External diff/textconv execution is disabled; URL userinfo/query/fragment filtering is not general secret scanning.
- The optional generated `run.bat` is opt-in and never overwrites an existing file.
- Registry, settings, notes, backups, and quarantine files are stored outside managed repositories.
- Cached data must not be presented as current when the Working Tree is stale or unavailable.
- A Remote URL does not prove Provider access, ownership, visibility, or authorization.
- Discovering an external, vendor, archive, or reference repository does not make it owned, trusted, or writable.
- Health reports conditions; it does not repair, delete, rewrite, or block a repository.
- Exports and reports omit fields with credential-like keys, but arbitrary
  text values and notes are not scanned for embedded secrets. Users must not
  treat this bounded filtering as general secret detection. Configured
  commands, launched processes, and logs may expose data according to their
  inputs and behavior.

Settings such as scan roots, scan depth, skipped directory names, command paths, theme, sorting, and move suppressions are preferences. They do not override these rules or authorize destructive actions.

## Future policy support

A future policy layer may add rules for risky Git actions, Workspaces, Worktrees, Agents, Providers, and import/archive operations. It will need explicit scope, confirmation, failure handling, and secret boundaries. No policy language or automation engine is part of 0.1.1.

Inherited Git repository/config redirection variables are removed from every
application-controlled Git subprocess. Normal on-disk configuration and transport
settings remain effective. Read-only observations disable optional Git locks.
