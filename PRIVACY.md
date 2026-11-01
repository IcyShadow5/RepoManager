# RepoManager Privacy Policy

**Controller:** Ramiro Norman Arnold, publisher of RepoManager.
**Privacy contact:** [icyshadow5.devsec@outlook.com](mailto:icyshadow5.devsec@outlook.com)
**Effective date:** the UTC date on which this text is first added to the repository's default
branch, recorded in that commit. Later revisions take effect on the UTC date recorded in their
respective commits.
**Applies to:** the published Stable 0.1.x releases of RepoManager, distributed as portable
Windows builds from the [Releases page](https://github.com/IcyShadow5/RepoManager/releases) and,
for 0.1.1, through Windows Package Manager if that submission is accepted. The behaviour below
was verified against the source tags `v0.1.1` and `v0.1.4`. Where those two versions differ, the
difference is marked **(0.1.4)** or **(0.1.1)**. Releases 0.1.2 and 0.1.3 are part of the same
line but were not separately re-verified for this revision.

**Policy address:**
`https://github.com/IcyShadow5/RepoManager/blob/main/PRIVACY.md`. The text at this address may
be revised.

---

## 1. Scope

This policy describes how the RepoManager Windows desktop application handles information.

It does not cover private development builds. Those are not distributed, are not installed
through any package manager, and are not offered to the public.

RepoManager is a local desktop application. It has no user account, no sign-in, no cloud
service, and no server operated by the publisher. It runs entirely on your computer, except for
the requests described in section 4.

## 2. Contact

Questions about this policy, or requests about your information, go to
[icyshadow5.devsec@outlook.com](mailto:icyshadow5.devsec@outlook.com).

Security vulnerabilities should follow [SECURITY.md](SECURITY.md) instead.

### 2.1 Messages you send us

If you contact the publisher by Outlook email, submit a report through GitHub Private
Vulnerability Reporting, or write a public GitHub issue, the publisher receives what you send.
This may include your email address or GitHub account, message text, security details, and
attachments. Public issues are visible to anyone. Do not put secrets or information you do not
want public in an issue.

The publisher processes ordinary voluntary correspondence on the basis of Article 6(1)(f) GDPR.
The legitimate interests pursued are responding to inquiries, investigating security reports,
and maintaining the security of RepoManager.

Processing is limited to information reasonably necessary for these purposes. You may object to
processing based on legitimate interests using the privacy contact address above.

Microsoft provides the Outlook mailbox; GitHub provides public issues and private vulnerability
reporting. These services and their service providers process information under their own terms
and privacy notices. The publisher may need to share relevant information with a person helping
investigate an issue or where required by law; disclose sensitive material only as needed.
Public issue content is also accessible to the public.

Microsoft and GitHub may process data in the United States and other countries. Their privacy
statements describe their own international transfer practices and safeguards:
[Microsoft](https://www.microsoft.com/en-us/privacy/privacystatement) and
[GitHub](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement).
The applicable obligations and safeguards depend on the particular processing, recipient and
transfer. Contact the publisher with questions about a particular transfer.

Ordinary private email correspondence that is not a security report is deleted no later than
12 months after the matter is closed. A matter is closed when no further action is pending and
its closure date has been recorded. Deletion covers applicable incoming messages, sent replies,
attachments and separate copies under the publisher's control.

Private security reports, including reports submitted by email or through GitHub Private
Vulnerability Reporting, may be retained while necessary for investigation, remediation,
coordinated disclosure, appropriate security documentation, or applicable legal obligations or
claims. Their continuing necessity is reviewed when a case is closed and at least monthly while
retained. Unnecessary personal data and separately stored copies under the publisher's control
are removed when no longer needed. Open matters are reviewed monthly. A monthly reminder supports
these reviews; deletion is not automatic.

Closing a GitHub security report does not delete it. The publisher cannot promise deletion of
GitHub-hosted originals, attachments, advisory histories or provider backups where removal cannot
be verified. Public GitHub records and provider-controlled copies may remain subject to the
provider's processes. Requests for erasure are considered individually under applicable law,
using GitHub's available controls or requesting provider assistance where appropriate. Limited
technical controls do not automatically remove applicable legal obligations.

You are not required by law or contract to send a message, but the publisher cannot answer a
message that is not sent. Section 10 explains how to request access, correction or deletion,
and how to object.

## 3. Information stored on your computer

RepoManager stores its own data under `%LOCALAPPDATA%\RepoManager`, outside the repositories it
manages. None of it is uploaded by RepoManager.

| Path | Contents | Versions | Why it exists |
|---|---|---|---|
| `repos.json` | Project records (name, path, status, pinned flag, repository association), Workspace records and their membership, and any custom launcher definitions you created (name, executable, arguments, working directory) | all | The application's curated inventory |
| `repos.json.bak1`, `repos.json.bak2` | The two previous registry snapshots | all | Recovery from a damaged or interrupted write |
| `settings.json` | Scan roots, scan depth, skipped folder names, your Agent command, suppressed move suggestions, theme; **(0.1.4)** also advisory exclusions you set for repository health findings | all | Remember your configuration |
| `notes\*.md` | Markdown notes you write per Project. Files are named `project-<hash>.md` for the Project's stable identifier, or `<project-name>-<hash>.md` for older records | all | Your own notes |
| `feedback\*.json` | Feedback drafts you explicitly choose to save | **(0.1.4)** | Keep a draft until you decide what to do with it |
| `repo_manager.log` plus `repo_manager.log.1` to `.3` | Diagnostic events, including local repository paths, error text, and the command line of a launcher that failed to start | all | Diagnose problems you report |
| `repo_manager.lock` | The process ID of the running instance | all | Stop two instances writing the same files |
| `settings.json.corrupt-*` | Up to five quarantined unreadable settings files | all | Preserve the damaged file instead of discarding your settings silently |
| `repos.json.corrupt-*` | Quarantined unreadable registry files | all | Preserve the damaged file instead of discarding your data |
| `cache\` | Compiled QML units and Qt pipeline caches, if the Qt runtime creates them | **(0.1.4)** | Faster startup. Written and read by the Qt runtime, not by RepoManager code |

Notes on that table:

- 0.1.1 has no feedback feature and no Qt runtime, so its profile contains neither `feedback\`
  nor `cache\`. Everything else in the table is written by both versions.
- `cache\` is not created by RepoManager's own code. The Qt runtime owns that directory and may
  not create it at all; RepoManager never reads or writes files there directly.
- `repos.json.corrupt-*` files are not removed automatically. They stay until you delete them.
- The application's source tree also contains a separate maintenance utility for a one-off
  historical migration. It is not part of normal operation, the application never invokes it,
  and it is not included in the distributed package.

Repository content stays in your repositories. RepoManager reads it to display state and writes
to a repository only when you explicitly ask for a Git operation, or generate a starter
`run.bat` (which never overwrites an existing file).

Notes, logs, configured commands and repository metadata are ordinary local files. Do not store
credentials or other secrets in them.

## 4. Information that leaves your computer

### 4.1 Repository metadata for GitHub remotes

For a selected repository with a recognized GitHub remote, RepoManager can make a read-only
HTTPS request:

- **(0.1.1)** Selecting the Project can start this request automatically. Refreshing its detail
  or showing its Health result can also start a request.
- **(0.1.4)** Selecting the Project does not itself start the request. Use **Check online
  (read-only)** to start it.

The request is:

```text
GET https://api.github.com/repos/{owner}/{name}
Accept: application/vnd.github+json
User-Agent: RepoManager
```

The request implementation is the same in both versions; the triggers differ as described above.

- **What is sent:** the repository owner and name in the request path, plus those two headers.
  As with any HTTPS request, GitHub also receives your IP address and normal connection
  metadata.
- **What is not sent:** repository contents, your notes, settings, local file paths, and any
  credential. The request is unauthenticated; the shipped interface has no token field and never
  supplies an authorization header in either version.
- **Safeguards:** the request has a 10-second timeout, only one request is in flight at a time,
  redirects are rejected unless they stay on `https://api.github.com` at the exact requested
  path, and the response is capped at 2 MB.
- **Purpose:** confirm the repository's identity and display its current visibility and default
  branch for the repository you selected. No other field of the response is stored.

GitHub, Inc. operates that service and is based in the United States. Its handling of the
request is governed by GitHub's own privacy statement.

### 4.2 Git operations you start

RepoManager runs the local `git` executable for discovery, status, history and the Git actions
you confirm. RepoManager never stores your Git credentials in either version.

When an action contacts a remote, Git itself contacts your configured remote using your own
configuration and credentials. That traffic is Git's, governed by your Git setup, not by
RepoManager. The available actions differ by version:

- **(0.1.1)** Commit & Push, and Pull.
- **(0.1.4)** Commit, Fetch, Pull, and Push, each previewed before you confirm.

The hardening around Git differs, and this is the one place where a user-visible privacy
difference is material:

- **(0.1.4)** RepoManager always starts Git with interactive terminal prompting disabled. For
  read-only observation it also disables remote helper protocols and lazy fetching and turns off
  optional locks, so observation cannot cause a network fetch. Git output and the repository's
  remote list have URL user information, query strings and fragments removed before display.
- **(0.1.1)** RepoManager does not override Git's credential prompting and does not redact Git
  output. Read-only observation runs only local Git commands, but if a push or pull needs
  credentials, your configured Git credential helper may be invoked exactly as it would be when
  you run Git yourself; and text printed by Git is shown as Git printed it. The one sanitised
  link is the "Open on GitHub" action, which builds its address from a normalised remote that
  drops user information and rejects query strings and fragments.

### 4.3 Feedback you choose to submit

**(0.1.4)** The Feedback dialog builds a report locally. Nothing is sent unless you act:

- **Save locally** writes a JSON file under `%LOCALAPPDATA%\RepoManager\feedback`.
- **Copy** puts the report text on your clipboard.
- **Open issue draft** opens `github.com/IcyShadow5/RepoManager/issues/new` with your title
  and message placed in the URL, which you then review and submit in your browser. The URL
  containing your report text may be recorded in your browser history.

A runtime-details option is **off by default**. If you enable it, the report also includes
your Python version, your operating system name, and your Qt version.

**(0.1.1)** There is no Feedback dialog, no locally saved feedback drafts, and no issue-draft
URL. 0.1.1 does not send feedback anywhere.

### 4.4 Pages you choose to open

Buttons that open a browser hand a fixed URL to your default browser. From that point the
destination site's policy applies. RepoManager adds no tracking parameters.

- **All versions:** "Open on GitHub" opens the HTTPS address of the selected repository's
  recognised remote.
- **(0.1.4)** also opens the issue tracker (through the feedback draft), the Releases page, and
  the Git for Windows download page from the in-application help.

### 4.5 Programs you launch

RepoManager can start your editor, a terminal, a detected launcher, your configured Agent
command, or a custom launcher you defined. Those programs run with the selected repository as
their working directory and receive your environment. When the target is a batch file, the
command and its arguments are also passed through a few `REPOMANAGER_*` variables so they never
appear in a command string. Launched programs are outside RepoManager's control: they may
contact the network and store data under their own policies. RepoManager does not monitor,
intercept, or record their output, and no run history is written to disk.

### 4.6 What RepoManager does not do

- No telemetry, analytics, usage statistics or crash reporting.
- No automatic update check. The published build has no updater; you download new versions
  yourself.
- No scheduled network activity. In 0.1.1 the request in section 4.1 can run in a background
  worker after Project selection or detail refresh; in 0.1.4 it follows the explicit online check.
- No advertising, profiling, or sale of data.
- No automated decision-making with legal or similarly significant effects.

These statements were checked against both source tags.

## 5. Purposes

RepoManager uses the local data in section 3 to provide the features you use: remembering your
curated Projects and Workspaces, showing repository state, keeping your notes, and diagnosing
failures. The request in section 4.1 exists solely to show current metadata for a selected repository. Feedback, where the feature exists, is processed only when you choose to submit it.

## 6. Retention

| Data | Retention |
|---|---|
| `repo_manager.log` | One active file plus three rotations, each at most 1 MB; older content is discarded automatically |
| `repos.json` backups | The two most recent previous snapshots |
| Settings quarantine | At most five files; older ones are removed automatically |
| Registry quarantine (`repos.json.corrupt-*`) | Kept until you delete them |
| Notes, settings, feedback drafts (0.1.4), exports | Kept until you delete them |
| `cache\` (0.1.4) | Managed by the Qt runtime; removed when you delete the folder |
| Anything the application sends the publisher | None — RepoManager sends the publisher nothing |

Messages you send the publisher directly are not stored by the application. Section 2.1
describes their retention and the limits of deletion.

## 7. Deleting your data

RepoManager has no "delete all data" command inside the application. To remove everything the
application stores:

1. Close RepoManager.
2. Delete the folder `%LOCALAPPDATA%\RepoManager`.

What this removes:

- your curated Project and Workspace records, including paths and associations;
- your settings and any custom launcher definitions;
- all Project notes;
- saved feedback drafts (0.1.4);
- all logs and their rotations;
- the registry and settings backups;
- quarantined corrupt files;
- the Qt caches (0.1.4) and the instance lock file.

What it does not affect:

- your Git repositories, working trees, branches and remotes — these live elsewhere and are
  untouched;
- exported reports, which were written wherever you chose to save them and must be deleted
  there;
- anything written by programs you launched, which have their own storage.

Consequences of deleting the folder: the curated inventory, notes and settings are gone
permanently. RepoManager cannot recover them, and reopening it starts from an empty profile.
Because the records also contain the paths the application uses to find your Projects, you will
need to add your scan roots again. If you delete the folder while RepoManager is running, the
application may recreate some files, and the deletion will be incomplete; close it first.

Deleting the application folder itself does not remove this data.

## 8. Security

RepoManager's local data are ordinary files in your Windows user profile. They are not encrypted
by RepoManager and rely on your Windows account and file-system permissions. Logs and the
registry can contain local repository paths. In 0.1.1, Git output is shown without the URL
redaction that 0.1.4 applies. The published executables are not code-signed, so Windows may show
an unknown-publisher or reputation warning on first launch.

Report a vulnerability through the channel in [SECURITY.md](SECURITY.md). Do not put secrets or
exploit details in a public issue.

## 9. Your controls

You can edit or delete your notes and settings, remove Projects and Workspaces, and delete the
whole data directory as described in section 7. RepoManager changes no repository without an
action you confirm.

## 10. Your rights

Nothing in this policy limits rights you may have under the law where you live.

If your local law gives you rights over personal data — for example access, correction,
deletion, restriction of processing, objection, or portability — you can raise them using the
contact in section 2. For information the application stores on your computer, the publisher
holds no copy. Section 7 explains how to delete the application's local data; separately saved
exports and copies may remain elsewhere. For a message you sent the publisher directly, the
publisher will consider requests for access, correction, deletion, restriction, objection or
portability as applicable. The publisher may need
to verify your identity and may be unable to change or delete copies controlled by GitHub, other
people or service providers. Section 2.1 describes the channels and retention.

If you believe your information has been handled unlawfully, you may also complain to your
local data-protection authority.

## 11. Changes to this policy

Material changes are published in this file with a new effective date, and the earlier text
remains available in the repository's commit history. RepoManager has no server component and
no in-application notification, so the current version is always this file at the stable address
above.

---

*This policy describes the published Stable 0.1.x line and was written from the released source
at tags `v0.1.1` and `v0.1.4`. It intentionally makes no claim about the private development
channel.*
