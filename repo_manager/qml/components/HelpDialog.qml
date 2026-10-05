import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import RepoManager 1.0
import "../design" as Design

AppDialog {
    id: dialog
    objectName: "helpDialog"
    title: "RepoManager help"
    property string topic: "Guide"
    contentItem: ColumnLayout {
        spacing: 14
        AppCombo { model: ["Guide", "Git requirement", "Keyboard shortcuts"].concat(App.helpTopics.filter(function(item) { return item.title !== "Start here" && item.title !== "Git requirement" && item.title !== "Keyboard and layout" }).map(function(item) { return item.title })); currentIndex: Math.max(0, model.indexOf(dialog.topic)); Layout.fillWidth: true; onActivated: dialog.topic = currentText }
        AppText {
            text: dialog.topic === "Git requirement" ? "Why is Git required?" : dialog.topic === "Keyboard shortcuts" ? "Keyboard shortcuts" : dialog.topic === "Guide" ? "Your local repository workspace" : dialog.topic
            font.pixelSize: 17; font.weight: Font.DemiBold
        }
        ScrollView {
            id: helpScroll
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(helpBody.implicitHeight, Math.max(160, dialog.parent.height - 230))
            contentWidth: availableWidth
            clip: true
        AppText {
            id: helpBody
            objectName: "helpBody"
            width: helpScroll.availableWidth
            wrapMode: Text.WordWrap
            lineHeight: 1.45
            color: Design.Theme.textSecondary
            text: dialog.topic === "Git requirement"
                ? "RepoManager uses Git to inspect branches, working-tree changes, commits and repository state.\n\nUse the normal Git for Windows installation and make Git available on PATH. Then choose Check again.\n\nWindows can give an already running application its previous PATH. If detection still fails, close and restart RepoManager. RepoManager never changes the machine PATH or installs Git for you.\n\nA repository error does not necessarily mean Git is missing. Check the reported error and repository state."
                : dialog.topic === "Keyboard shortcuts"
                ? "Ctrl+F  —  Focus repository search\nF5  —  Scan configured folders\nF1  —  Open Help\nUp / Down  —  Select the previous / next table row\nHome / End  —  Select the first / last row\nEnter  —  Run the primary launcher\nShift+F10  —  Open project actions\nEscape  —  Clear search or close a dialog\nTab / Shift+Tab  —  Move keyboard focus"
                : dialog.topic !== "Guide" ? (App.helpTopics.filter(function(item) { return item.title === dialog.topic })[0] || {text: "Select a help topic."}).text
                : "Add scan folders in Settings, then scan. Search matches project name, folder path and focus. Click a column header to sort; drag its edge to resize. More columns exposes Git sync and Worktrees.\n\nSelect a project to inspect its metadata, curate status/pin/focus, edit local notes, inspect Health checks or run a detected launcher.\n\nWork on this sets a project to Active and pins it. Working on now shows available Active projects ordered by recent commits.\n\nClean / Modified describe the observed working tree. Unknown means there is no reliable observation; it is never counted as Clean. Health is evaluated separately from current local evidence.\n\nFeedback offers positive feedback, suggestions, bugs and UI issues. Save locally, copy, or review a public issue draft before submitting."
        }
        }
        RowLayout {
            visible: dialog.topic === "Git requirement"
            AppButton { text: "Install Git"; primary: true; iconName: "git-branch"; onClicked: App.openGitInstall() }
            AppButton { text: "Check again"; iconName: "refresh"; onClicked: App.checkAgain() }
        }
        AppButton { objectName: "officialReleases"; text: "Official releases"; visible: dialog.topic === "Versions and updates"; iconName: "chevron-right"; onClicked: App.openOfficialReleases() }
        AppButton { text: "Close"; Layout.alignment: Qt.AlignRight; onClicked: dialog.accept() }
    }
}
