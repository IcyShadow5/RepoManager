import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import RepoManager 1.0
import "../design" as Design
import "." as UI

Rectangle {
    id: panel
    readonly property var project: App.selectedProject
    property string tab: "Overview"
    property string editorProject: ""
    property string launcherFilter: ""
    property string healthFilter: "All checks"
    onTabChanged: if (detailScroll.contentItem) detailScroll.contentItem.contentY = 0
    function showActions() { if (panel.project.projectId) projectMenu.open() }
    LauncherDialog { id: launcherEditor }
    FileDialog {
        id: exportFile
        fileMode: FileDialog.SaveFile
        title: "Export Project / repository report"
        onAccepted: App.exportProject(selectedFile)
    }
    color: Design.Theme.surface
    radius: 6
    border.color: Design.Theme.border
    function loadCuration() {
        status.currentIndex = Math.max(0, status.model.indexOf(project.projectStatus || "idea"))
        focus.text = project.focus || ""
        pin.checked = project.pinned || false
        editorProject = project.projectId || ""
    }
    Connections {
        target: App
        function onChanged() {
            if (panel.editorProject !== (panel.project.projectId || "")) { quickRunMenu.close(); panel.loadCuration() }
        }
        function onQuickRunChooserRequested() { quickRunMenu.open() }
    }
    Component.onCompleted: loadCuration()
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 6
        RowLayout {
            Layout.fillWidth: true
            spacing: 9
            Icon { name: "folder"; width: 27; height: 27 }
            AppText {
                text: panel.project.projectName || "Project details"
                font.pixelSize: 18; font.weight: Font.DemiBold
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
            Item {
                Layout.preferredWidth: quickRunButton.implicitWidth
                Layout.preferredHeight: quickRunButton.implicitHeight
                HoverHandler { id: quickRunHover }
                ToolTip.visible: quickRunHover.hovered
                ToolTip.delay: 650
                ToolTip.text: App.detailLoading ? "Detecting launchers…" : App.quickRunCount === 0 ? "No usable launcher. Configure one in the Run tab." : App.quickRunCount === 1 ? "Run the available launcher" : "Choose a launcher"
                AppButton {
                    id: quickRunButton; objectName: "quickRunButton"
                    text: "Run"; iconName: "player-play"; compact: true
                    Accessible.name: "Quick Run selected repository"
                    enabled: !App.detailLoading && App.quickRunCount > 0
                    onClicked: App.requestQuickRun()
                }
            }
            AppButton { iconName: "copy"; compact: true; tip: "Copy project path"; enabled: !!panel.project.path; onClicked: App.copySelectedPath() }
            AppButton { id: actionsButton; objectName: "projectActionsButton"; iconName: "settings"; compact: true; tip: "Project actions"; enabled: !!panel.project.projectId; onClicked: panel.showActions() }
        }
        RowLayout {
            Layout.fillWidth: true
            StatusBadge { label: panel.project.kind || "No selection" }
            StatusBadge { label: panel.project.projectStatus || "—"; accent: Design.Theme.icy }
            StatusBadge { label: panel.project.branch || "—" }
            Item { Layout.fillWidth: true }
        }
        AppText {
            text: panel.project.path || "Select a project to inspect it."
            color: Design.Theme.textSecondary; font.pixelSize: 12
            elide: Text.ElideMiddle; Layout.fillWidth: true
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 0
            Repeater {
                model: ["Overview", "Technical", "Health", "Notes", "Run"]
                AbstractButton {
                    required property string modelData
                    Layout.fillWidth: true; implicitHeight: 34
                    focusPolicy: Qt.StrongFocus
                    onClicked: panel.tab = modelData
                    background: Rectangle {
                        color: parent.hovered ? Design.Theme.elevated : "transparent"
                        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 2; color: panel.tab === modelData ? Design.Theme.icy : Design.Theme.divider }
                        Rectangle { anchors.fill: parent; color: "transparent"; border.color: parent.parent.activeFocus ? Design.Theme.focus : "transparent" }
                    }
                    contentItem: AppText {
                        text: modelData; color: panel.tab === modelData ? Design.Theme.icy : Design.Theme.textSecondary
                        font.pixelSize: 12; font.weight: panel.tab === modelData ? Font.DemiBold : Font.Normal
                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                    }
                }
            }
        }
        ScrollView {
            id: detailScroll; objectName: "detailScroll"
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth
            clip: true
            ColumnLayout {
                width: parent.width
                spacing: 6
                SectionCard {
                    visible: panel.tab === "Overview"
                    Layout.fillWidth: true
                    title: "Project information"
                    DetailLine { label: "Branch"; value: App.gitState === "available" ? panel.project.branch || "—" : "Unavailable"; Layout.fillWidth: true }
                    DetailLine { label: "Last commit"; value: panel.project.lastCommit || "—"; Layout.fillWidth: true }
                    DetailLine {
                        label: "Working tree"
                        value: App.gitState !== "available" ? "Unavailable" : !panel.project.dirtyKnown ? "Unknown" : panel.project.dirty > 0 ? panel.project.dirty + " changes" : "Clean (0 changes)"
                        Layout.fillWidth: true
                    }
                }
                SectionCard {
                    visible: panel.tab === "Overview"
                    Layout.fillWidth: true
                    title: "Curation"; iconName: "settings"
                    RowLayout {
                        Layout.fillWidth: true
                        AppText { text: "Status"; color: Design.Theme.textSecondary; Layout.preferredWidth: 58 }
                        AppCombo { id: status; model: ["idea", "active", "paused", "archived"]; Layout.fillWidth: true; enabled: !!panel.project.projectId && !App.registryBlocked }
                        CheckBox {
                            id: pin
                            text: "Pinned"
                            enabled: !!panel.project.projectId && !App.registryBlocked
                            font.family: Design.Theme.fontFamily
                            indicator: Rectangle {
                                x: 0; y: (parent.height-height)/2; width: 15; height: 15; radius: 3
                                color: pin.checked ? Design.Theme.blue : Design.Theme.background
                                border.color: pin.activeFocus ? Design.Theme.focus : Design.Theme.border
                                Icon { visible: pin.checked; anchors.fill: parent; anchors.margins: 1; name: "check"; tint: Design.Theme.textPrimary }
                            }
                            contentItem: AppText { text: pin.text; leftPadding: 21; font.pixelSize: 12; verticalAlignment: Text.AlignVCenter }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        AppField { id: focus; placeholderText: "Add focus…"; Layout.fillWidth: true; enabled: !!panel.project.projectId && !App.registryBlocked }
                        AppButton {
                            text: "Save"; compact: true; tip: "Save status, pin and focus"
                            enabled: !!panel.project.projectId && !App.registryBlocked
                            onClicked: App.saveCuration(status.currentText, pin.checked, focus.text)
                        }
                    }
                }
                SectionCard {
                    visible: panel.tab === "Overview"
                    Layout.fillWidth: true
                    title: "Repository health"; iconName: "check"
                    StatusBadge {
                        label: App.detailLoading ? "Evaluating…" : ({"PASS": "Healthy — no blocking problem found", "WARN": "Needs attention", "FAIL": "Action required", "UNKNOWN": "Unknown", "NOT_APPLICABLE": "Not applicable"})[App.healthEvidence.status] || "Not evaluated"
                        accent: App.healthEvidence.status === "PASS" ? Design.Theme.success : App.healthEvidence.status === "FAIL" ? Design.Theme.error : Design.Theme.warning
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        AppText {
                            text: App.detailLoading ? "Evaluating…" : App.healthEvidence.count !== undefined ? App.healthEvidence.count + " checks · " + App.healthEvidence.unknown + " unknown · " + App.healthEvidence.stale + " stale" + (App.healthEvidence.ignored ? " · " + App.healthEvidence.ignored + " ignored" : "") : "Not evaluated"
                            color: Design.Theme.textSecondary; font.pixelSize: 11
                            wrapMode: Text.WordWrap; Layout.fillWidth: true
                        }
                        AppButton { text: "Checks"; iconName: "chevron-right"; compact: true; tip: "View all Health checks"; enabled: !!panel.project.projectId; onClicked: panel.tab = "Health" }
                    }
                }
                SectionCard {
                    visible: panel.tab === "Overview"
                    Layout.fillWidth: true
                    title: "Project actions"; iconName: "player-play"
                    AppButton {
                        text: panel.project.projectStatus === "active" ? "Pause working on this" : "Work on this"
                        primary: true; compact: true; iconName: "focus-2"
                        Layout.fillWidth: true
                        enabled: !!panel.project.projectId && !App.registryBlocked
                        onClicked: { App.toggleWorking(); panel.loadCuration() }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        AppButton { text: "Explorer"; iconName: "folder"; compact: true; Layout.fillWidth: true; enabled: !!panel.project.path; onClicked: App.openFolder("explorer") }
                        AppButton { text: "VS Code"; iconName: "code"; compact: true; Layout.fillWidth: true; enabled: !!panel.project.path; onClicked: App.openFolder("vscode") }
                        AppButton { text: "Terminal"; iconName: "terminal-2"; compact: true; Layout.fillWidth: true; enabled: !!panel.project.path; onClicked: App.openFolder("terminal") }
                    }
                }
                SectionCard {
                    visible: panel.tab === "Technical"
                    Layout.fillWidth: true
                    title: "Project evidence"; iconName: "info-circle"
                    DetailLine { label: "Class"; value: panel.project.classification || "Unknown"; Layout.fillWidth: true }
                    AppButton { text: "Stack & documentation evidence"; compact: true; Layout.fillWidth: true; onClicked: App.showTechnicalEvidence() }
                    RowLayout {
                        Repeater {
                            model: ["branch", "head", "remote"]
                            AppButton { required property string modelData; text: "Copy " + modelData; compact: true; Layout.fillWidth: true; onClicked: App.copyTechnical(modelData) }
                        }
                    }
                    AppText { text: "Health score  " + (App.healthEvidence.score === null || App.healthEvidence.score === undefined ? "—" : App.healthEvidence.score + " / 100") + " · " + (App.healthEvidence.label || "Not evaluated"); color: Design.Theme.textSecondary; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    AppButton { text: "Open remote website"; compact: true; onClicked: App.openRemote() }
                }
                SectionCard {
                    visible: panel.tab === "Technical"
                    Layout.fillWidth: true
                    title: "Git observation"; iconName: "git-branch"
                    DetailLine { label: "HEAD"; value: panel.project.head || "Unborn / unobserved"; Layout.fillWidth: true }
                    DetailLine { label: "Upstream"; value: panel.project.upstream || "None / unobserved"; Layout.fillWidth: true }
                    DetailLine { label: "Ahead / behind"; value: panel.project.syncKnown ? panel.project.ahead + " / " + panel.project.behind : "Unknown"; Layout.fillWidth: true }
                    DetailLine { label: "Staged"; value: panel.project.dirtyKnown ? String(panel.project.staged) : "Unknown"; Layout.fillWidth: true }
                    DetailLine { label: "Unstaged"; value: panel.project.dirtyKnown ? String(panel.project.unstaged) : "Unknown"; Layout.fillWidth: true }
                    DetailLine { label: "Untracked"; value: panel.project.dirtyKnown ? String(panel.project.untracked) : "Unknown"; Layout.fillWidth: true }
                    AppText { text: panel.project.remote || "No observed remote"; color: Design.Theme.textSecondary; wrapMode: Text.WrapAnywhere; font.pixelSize: 12; Layout.fillWidth: true }
                    AppText { text: panel.project.lastMessage || "No observed commit message"; color: Design.Theme.textMuted; wrapMode: Text.WordWrap; font.pixelSize: 12; Layout.fillWidth: true }
                }
                SectionCard {
                    visible: panel.tab === "Technical"
                    Layout.fillWidth: true
                    title: "Provider evidence"; iconName: "info-circle"
                    AppText { text: App.providerData.local.provider_id ? "Detected locally: " + App.providerData.local.provider_id + " · " + App.providerData.local.host + "\n" + App.providerData.local.namespace + "/" + App.providerData.local.repository : "No recognized remote provider"; wrapMode: Text.WordWrap; color: Design.Theme.textSecondary; Layout.fillWidth: true }
                    AppText { text: "Ownership / access: not inferred"; color: Design.Theme.textMuted; font.pixelSize: 11 }
                    AppText { text: App.providerData.busy ? "Checking provider…" : App.providerData.online.status ? App.providerData.online.status + " · " + App.providerData.online.freshness + "\nVisibility: " + App.providerData.online.visibility + "\nDefault branch: " + (App.providerData.online.default_branch || "Unknown") + "\n" + (App.providerData.online.evidence || []).join("\n") + "\n" + (App.providerData.online.disagreements || []).join("\n") : "Online details: not run"; wrapMode: Text.WordWrap; color: Design.Theme.textSecondary; Layout.fillWidth: true }
                    AppButton { text: "Check online (read-only)"; compact: true; enabled: !!App.providerData.local.provider_id && !App.providerData.busy; onClicked: App.checkProvider() }
                }
                SectionCard {
                    visible: panel.tab === "Technical" || panel.tab === "Overview"
                    Layout.fillWidth: true
                    title: "Git actions"; iconName: "git-branch"
                    GridLayout {
                        Layout.fillWidth: true; columns: 3; columnSpacing: 5; rowSpacing: 5
                        Repeater {
                            model: ["changes", "commit", "history", "fetch", "push", "pull", "remotes"]
                            AppButton {
                                required property string modelData
                                text: modelData.charAt(0).toUpperCase() + modelData.slice(1)
                                compact: true; Layout.fillWidth: true
                                enabled: !!panel.project.projectId && panel.project.kind === "Git repository" && App.gitState === "available" && !App.registryBlocked
                                onClicked: App.openGit(modelData)
                            }
                        }
                    }
                }
                SectionCard {
                    visible: panel.tab === "Technical"
                    Layout.fillWidth: true
                    title: "Worktrees"; iconName: "folder"
                    Repeater {
                        model: panel.project.worktrees || []
                        AppText { required property var modelData; text: (modelData.branch || "Detached") + " · " + modelData.path; wrapMode: Text.WrapAnywhere; font.pixelSize: 12; Layout.fillWidth: true }
                    }
                    AppText { visible: !(panel.project.worktrees || []).length; text: "No observed Worktrees"; color: Design.Theme.textMuted; font.pixelSize: 12 }
                }
                AppText { visible: panel.tab === "Health" && App.detailLoading; text: "Evaluating repository health…"; color: Design.Theme.textSecondary }
                SectionCard {
                    visible: panel.tab === "Health"; title: "Health overview"; iconName: "check"; Layout.fillWidth: true
                    AppText { text: App.healthEvidence.summary || "Not evaluated"; wrapMode: Text.WordWrap; color: Design.Theme.textSecondary; Layout.fillWidth: true }
                    AppText {
                        text: { const c = App.healthEvidence.counts || {}; return "Health score: " + (App.healthEvidence.score === null || App.healthEvidence.score === undefined ? "—" : App.healthEvidence.score + " / 100") + "\nPassed " + (c.passed || 0) + " · Warnings " + (c.warnings || 0) + " · Problems " + (c.problems || 0) + "\nUnknown " + (c.unknown || 0) + " · Informational " + (c.informational || 0) + " · Stale " + (c.stale || 0) + " · Ignored " + (c.ignored || 0) }
                        wrapMode: Text.WordWrap; color: Design.Theme.textSecondary; Layout.fillWidth: true
                    }
                    AppButton { text: "How Health works"; compact: true; onClicked: { evidenceDialog.title = "Repository Health"; evidenceDialog.text = "Health evaluates current local evidence. Its status is authoritative.\n\nThe presentation score refines that status within fixed bands:\nPASS 80–100 · WARN 60–79 · UNKNOWN 40–59 · FAIL 0–39.\nRequired, recommended and informational findings have different penalties. Stale evidence is bounded to a five-point penalty.\n\nIt does not measure code quality, security assurance or Agent success. Missing / unobserved evidence remains explicit. Every check exposes its evidence and evaluated timestamp."; evidenceDialog.open() } }
                }
                AppCombo { visible: panel.tab === "Health"; model: ["All checks", "Action Needed", "Needs Attention", "Informational", "Not Applicable", "Passed Checks", "Ignored", "Disabled", "Other", "Required", "Recommended"]; Layout.fillWidth: true; onActivated: panel.healthFilter = currentText }
                Repeater {
                    model: panel.tab === "Health" ? App.healthEvidence.findings || [] : []
                    SectionCard {
                        required property var modelData
                        Layout.fillWidth: true
                        visible: panel.healthFilter === "All checks" || panel.healthFilter === modelData.group || panel.healthFilter.toUpperCase() === modelData.importance
                        title: modelData.rule
                        iconName: modelData.status === "PASS" ? "check" : "info-circle"
                        RowLayout {
                            StatusBadge { label: modelData.status; accent: modelData.status === "IGNORED" ? Design.Theme.textMuted : modelData.status === "PASS" ? Design.Theme.success : modelData.status === "FAIL" ? Design.Theme.error : Design.Theme.warning }
                            AppText { text: modelData.importance + " · " + modelData.severity + " · " + modelData.freshness; color: Design.Theme.textMuted; font.pixelSize: 10 }
                        }
                        AppText { text: modelData.explanation; wrapMode: Text.WordWrap; color: Design.Theme.textSecondary; font.pixelSize: 12; Layout.fillWidth: true }
                        AppText {
                            visible: modelData.status === "IGNORED"
                            text: "Intentionally ignored " + (modelData.suppressionScope === "global" ? "in Settings → Health" : "for this repository") + ". Observed: " + modelData.observedStatus + ". Excluded from active Health scoring."
                            wrapMode: Text.WordWrap; color: Design.Theme.textMuted; font.pixelSize: 12; Layout.fillWidth: true
                        }
                        AppButton {
                            objectName: "healthPreference_" + modelData.rule
                            visible: modelData.suppressible && modelData.suppressionScope !== "global"
                            enabled: !App.registryBlocked
                            text: modelData.status === "IGNORED" ? "Restore check" : "Ignore for this repository"
                            compact: true
                            onClicked: App.setHealthIgnored(modelData.rule, modelData.status !== "IGNORED")
                        }
                        AppText { visible: !!modelData.remediation; text: modelData.remediation; wrapMode: Text.WordWrap; color: Design.Theme.icy; font.pixelSize: 12; Layout.fillWidth: true }
                        AppButton { text: "Evidence details"; compact: true; onClicked: { evidenceDialog.title = modelData.rule; evidenceDialog.text = "Target: " + modelData.target + "\nEvaluated: " + modelData.timestamp + "\n\n" + (modelData.evidence || []).map(function(item) { return item.source + " · " + item.target + "\n" + item.observation + "\n" + item.timestamp + " · " + item.freshness + "\n" + item.explanation }).join("\n\n"); evidenceDialog.open() } }
                    }
                }
                SectionCard {
                    visible: panel.tab === "Notes"
                    Layout.fillWidth: true
                    title: "Project notes"; iconName: "pin"
                    AppText { text: App.notesDirty ? "Unsaved changes" : "Saved locally · autosave enabled"; color: App.notesDirty ? Design.Theme.warning : Design.Theme.textMuted; font.pixelSize: 12 }
                    TextArea {
                        Layout.fillWidth: true; Layout.preferredHeight: Math.max(240, implicitHeight)
                        text: App.notesText
                        enabled: App.notesEditable
                        onTextChanged: App.editNotes(text)
                        placeholderText: "Notes, next steps, useful context…"
                        color: Design.Theme.textPrimary; placeholderTextColor: Design.Theme.textMuted
                        selectionColor: Design.Theme.selected
                        font.family: Design.Theme.fontFamily; font.pixelSize: 13
                        wrapMode: TextEdit.Wrap
                        background: Rectangle { color: Design.Theme.surface; radius: 4; border.color: parent.activeFocus ? Design.Theme.focus : Design.Theme.border }
                    }
                    AppButton { text: "Save notes"; compact: true; iconName: "check"; enabled: App.notesEditable; onClicked: App.saveNotes() }
                }
                SectionCard {
                    visible: panel.tab === "Run"
                    Layout.fillWidth: true
                    title: "Detected launchers"; iconName: "player-play"
                    AppText { text: App.detailLoading ? "Detecting available commands…" : "Commands detected from this project. Running one may execute project code."; wrapMode: Text.WordWrap; color: Design.Theme.textSecondary; font.pixelSize: 12; Layout.fillWidth: true }
                    UI.SearchField { placeholderText: "Filter launchers…"; Layout.fillWidth: true; onTextEdited: panel.launcherFilter = text.toLowerCase() }
                    Repeater {
                        model: App.detectedLaunchers
                        ColumnLayout {
                            required property var modelData
                            required property int index
                            Layout.fillWidth: true
                            visible: JSON.stringify(modelData).toLowerCase().indexOf(panel.launcherFilter) >= 0
                            AppButton { text: modelData.label; iconName: "player-play"; compact: true; Layout.fillWidth: true; enabled: modelData.healthy; onClicked: App.runLauncher(index) }
                            AppText { text: modelData.source; color: Design.Theme.textMuted; font.pixelSize: 11; Layout.fillWidth: true }
                            AppText { visible: !!modelData.reason; text: modelData.reason; color: Design.Theme.warning; wrapMode: Text.WordWrap; font.pixelSize: 12; Layout.fillWidth: true }
                        }
                    }
                    AppText { visible: !App.detailLoading && App.detectedLaunchers.length === 0; text: "No supported launcher found."; color: Design.Theme.textMuted; font.pixelSize: 12 }
                    AppButton { text: "Add Custom Launcher…"; compact: true; Layout.fillWidth: true; enabled: !!panel.project.projectId && !App.registryBlocked; onClicked: launcherEditor.edit(-1) }
                    Repeater {
                        model: App.customLaunchers
                        AppButton { required property var modelData; required property int index; text: "Edit " + modelData.name + "…"; compact: true; Layout.fillWidth: true; onClicked: launcherEditor.edit(index) }
                    }
                    AppButton { text: "Generate starter run.bat…"; compact: true; Layout.fillWidth: true; enabled: !!panel.project.path && !App.registryBlocked; onClicked: App.requestAction("stub") }
                }
            }
        }
    }
    TextDialog { id: evidenceDialog }
    Menu {
        id: quickRunMenu; objectName: "quickRunMenu"
        width: Math.min(300, panel.width - 20); margins: 8; popupType: Popup.Item
        background: Rectangle { color: Design.Theme.elevated; radius: 5; border.color: Design.Theme.border }
        parent: quickRunButton
        x: quickRunButton.width - width; y: quickRunButton.height + 4
        Instantiator {
            model: App.quickRunChoices
            delegate: MenuItem {
                id: quickItem
                required property var modelData
                required property int index
                text: modelData.label
                implicitHeight: 34
                contentItem: AppText { text: quickItem.text; elide: Text.ElideRight; color: Design.Theme.textPrimary; verticalAlignment: Text.AlignVCenter; leftPadding: 8 }
                background: Rectangle { color: quickItem.highlighted ? Design.Theme.selected : "transparent"; border.color: quickItem.activeFocus ? Design.Theme.focus : "transparent" }
                onTriggered: App.runQuickLauncher(index)
            }
            onObjectAdded: function(index, object) { quickRunMenu.insertItem(index, object) }
            onObjectRemoved: function(index, object) { quickRunMenu.removeItem(object) }
        }
    }
    ProjectMenu {
        id: projectMenu
        parent: actionsButton
        x: actionsButton.width - width
        y: actionsButton.height + 4
        exportDialog: exportFile
    }
}
