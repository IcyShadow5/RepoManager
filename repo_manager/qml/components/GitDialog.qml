import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import RepoManager 1.0
import "../design" as Design

AppDialog {
    id: dialog
    objectName: "gitDialog"
    readonly property var controller: App.gitController
    readonly property bool changes: controller.mode === "changes" || controller.mode === "commit"
    readonly property bool network: ["fetch", "push", "pull"].indexOf(controller.mode) >= 0
    title: controller.title
    width: Math.min(1120, parent.width - 40)
    onOpened: { message.text = ""; commitId.text = ""; stageAll.checked = false }
    Connections {
        target: dialog.controller
        function onOpened() { dialog.open() }
        function onConfirmationRequested() { confirm.open() }
    }
    AppDialog {
        id: confirm
        title: "Confirm " + (dialog.changes ? "local commit" : dialog.controller.mode)
        onRejected: dialog.controller.cancelConfirmation()
        contentItem: ColumnLayout {
            spacing: 14
            CodeView { text: dialog.controller.confirmationText; Layout.fillWidth: true; Layout.preferredHeight: Math.min(330, confirm.parent.height - 230) }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                AppButton { text: "Cancel"; onClicked: confirm.reject() }
                AppButton { text: dialog.changes ? "Create local commit" : "Confirm " + dialog.controller.mode; primary: true; onClicked: { confirm.accept(); dialog.controller.executeConfirmed() } }
            }
        }
    }
    TextDialog { id: resultDialog; title: "Git operation result"; text: dialog.controller.lastResult }
    contentItem: ColumnLayout {
        spacing: 10
        RowLayout {
            Layout.fillWidth: true
            ColumnLayout {
                Layout.fillWidth: true; spacing: 4
                AppText { text: dialog.controller.targetPath; font.pixelSize: 12; elide: Text.ElideMiddle; color: Design.Theme.textSecondary; Layout.fillWidth: true }
                AppText { text: dialog.controller.stateSummary; visible: text !== ""; color: Design.Theme.icy; font.pixelSize: 12; Layout.fillWidth: true }
            }
            AppButton { text: "Refresh"; compact: true; iconName: "refresh"; enabled: !dialog.controller.busy; onClicked: dialog.controller.refresh() }
        }
        RowLayout {
            visible: dialog.changes
            Layout.fillWidth: true
            AppButton { text: "Unstaged / untracked"; compact: true; primary: dialog.controller.view === "unstaged"; enabled: !dialog.controller.busy; onClicked: dialog.controller.setView("unstaged") }
            AppButton { text: "Staged"; compact: true; primary: dialog.controller.view === "staged"; enabled: !dialog.controller.busy; onClicked: dialog.controller.setView("staged") }
            Item { Layout.fillWidth: true }
            AppText { text: "Select files to stage; preview their differences."; color: Design.Theme.textMuted; font.pixelSize: 12 }
        }
        RowLayout {
            visible: dialog.network
            Layout.fillWidth: true
            AppText { text: "Remote"; color: Design.Theme.textSecondary }
            AppCombo {
                id: remote
                model: dialog.controller.remoteNames
                currentIndex: model.indexOf(dialog.controller.remote)
                Layout.preferredWidth: 160
                enabled: !dialog.controller.busy
                onActivated: dialog.controller.setNetwork(currentText, destination.text, setUpstream.checked)
            }
            AppText { text: "Destination branch"; visible: dialog.controller.mode !== "fetch"; color: Design.Theme.textSecondary }
            AppField {
                id: destination
                visible: dialog.controller.mode !== "fetch"
                text: dialog.controller.destination
                Layout.fillWidth: true
                enabled: !dialog.controller.busy
                onTextEdited: dialog.controller.setNetwork(remote.currentText, text, setUpstream.checked)
            }
            Item { Layout.fillWidth: true; visible: dialog.controller.mode === "fetch" }
        }
        AppCheckBox {
            id: setUpstream
            visible: dialog.controller.mode === "push"
            text: "Set upstream for this branch (explicit opt-in)"
            checked: dialog.controller.setUpstream
            enabled: !dialog.controller.busy
            onToggled: dialog.controller.setNetwork(remote.currentText, destination.text, checked)
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.max(170, Math.min(400, dialog.parent.height - (dialog.changes ? 420 : 300)))
            Rectangle {
                visible: dialog.changes
                Layout.preferredWidth: Math.max(240, dialog.width * 0.32); Layout.fillHeight: true
                color: Design.Theme.background; border.color: Design.Theme.border; radius: 5
                ListView {
                    id: fileList
                    anchors.fill: parent; anchors.margins: 6
                    clip: true; model: dialog.controller.files
                    ScrollBar.vertical: ScrollBar {}
                    delegate: RowLayout {
                        required property var modelData
                        width: ListView.view.width; height: 35; spacing: 6
                        AppCheckBox {
                            checked: modelData.selected
                            enabled: !dialog.controller.busy
                            onToggled: dialog.controller.toggleFile(modelData.index)
                        }
                        AppText { text: modelData.label; elide: Text.ElideRight; Layout.fillWidth: true; font.pixelSize: 12; color: Design.Theme.textSecondary }
                        AppButton { iconName: "code"; compact: true; tip: "Preview file differences"; enabled: !dialog.controller.busy; onClicked: dialog.controller.previewFile(modelData.index) }
                    }
                    AppText { visible: fileList.count === 0; anchors.centerIn: parent; text: dialog.controller.busy ? "Loading…" : "No files in this view"; color: Design.Theme.textMuted }
                }
            }
            CodeView {
                text: dialog.controller.content
                Layout.fillWidth: true; Layout.fillHeight: true
                onLineSelected: function(line) { if (dialog.controller.mode === "history" && /^[0-9a-fA-F]{40,64}$/.test(line)) commitId.text = line }
            }
        }
        RowLayout {
            visible: dialog.changes
            Layout.fillWidth: true
            AppButton { text: "Stage selected"; compact: true; enabled: !dialog.controller.busy; onClicked: dialog.controller.stageSelected(false) }
            AppButton { text: "Unstage selected"; compact: true; enabled: !dialog.controller.busy; onClicked: dialog.controller.stageSelected(true) }
            AppText { text: "Unstage keeps working files."; color: Design.Theme.textMuted; font.pixelSize: 12; Layout.fillWidth: true }
        }
        Rectangle { visible: dialog.changes; height: 1; color: Design.Theme.divider; Layout.fillWidth: true }
        AppCheckBox {
            id: stageAll
            visible: dialog.changes
            text: "Stage all current changes and commit (default: staged only)"
            enabled: !dialog.controller.busy
        }
        RowLayout {
            visible: dialog.changes
            Layout.fillWidth: true
            AppField { id: message; placeholderText: "Local commit message"; Layout.fillWidth: true; enabled: !dialog.controller.busy; onAccepted: dialog.controller.previewCommit(text, stageAll.checked) }
            AppButton { text: "Commit…"; primary: true; iconName: "git-branch"; enabled: !dialog.controller.busy; onClicked: dialog.controller.previewCommit(message.text, stageAll.checked) }
        }
        RowLayout {
            visible: dialog.network
            Layout.fillWidth: true
            AppButton { text: "Preview destination"; iconName: "search"; enabled: !dialog.controller.busy; onClicked: dialog.controller.previewNetwork() }
            Item { Layout.fillWidth: true }
            AppButton { text: dialog.controller.mode + "…"; primary: true; enabled: !dialog.controller.busy && dialog.controller.hasNetworkPreview; onClicked: dialog.controller.confirmNetwork() }
        }
        RowLayout {
            visible: dialog.controller.mode === "history"
            Layout.fillWidth: true
            AppButton { text: "Load more"; compact: true; enabled: !dialog.controller.busy; onClicked: dialog.controller.moreHistory() }
            AppField { id: commitId; placeholderText: "Commit ID"; Layout.fillWidth: true; enabled: !dialog.controller.busy }
            AppButton { text: "View commit"; compact: true; enabled: !dialog.controller.busy; onClicked: dialog.controller.commitDetails(commitId.text) }
            AppButton { text: "Copy ID"; compact: true; iconName: "copy"; onClicked: dialog.controller.copyCommitId(commitId.text) }
        }
        AppText { text: dialog.controller.statusText; color: Design.Theme.textSecondary; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true; maximumLineCount: 4; elide: Text.ElideRight }
        RowLayout {
            Layout.fillWidth: true
            AppButton { text: "Full result"; visible: !!dialog.controller.lastResult; compact: true; onClicked: resultDialog.open() }
            Item { Layout.fillWidth: true }
            AppButton { text: "Close"; onClicked: dialog.reject() }
        }
    }
}
