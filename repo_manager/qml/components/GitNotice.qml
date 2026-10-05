import QtQuick
import QtQuick.Layouts
import RepoManager 1.0
import "../design" as Design

Rectangle {
    id: notice
    signal helpRequested()
    implicitHeight: 110
    radius: 6
    color: Design.Theme.light ? "#fff5dc" : "#24271f"
    border.color: Design.Theme.light ? "#c8a156" : "#65563c"
    RowLayout {
        anchors.fill: parent; anchors.margins: 14; spacing: 13
        Icon { name: "alert-triangle"; tint: Design.Theme.warning; width: 25; height: 25; Layout.alignment: Qt.AlignTop }
        ColumnLayout {
            Layout.fillWidth: true; spacing: 3
            AppText { text: App.gitState === "not_found" ? "Git is required" : "Git could not be started"; font.pixelSize: 16; font.weight: Font.DemiBold }
            AppText {
                text: App.gitState === "not_found" ? "RepoManager uses Git to inspect and manage local repositories." : "Git was found, but the installation did not pass the startup check."
                color: Design.Theme.textSecondary; font.pixelSize: 13
                wrapMode: Text.WordWrap; Layout.fillWidth: true
            }
            AppText {
                text: "Install Git for Windows with Git on PATH, then check again. If detection still fails, restart RepoManager."
                color: Design.Theme.textSecondary; font.pixelSize: 12
                wrapMode: Text.WordWrap; Layout.fillWidth: true
            }
        }
        ColumnLayout {
            RowLayout {
                AppButton { text: "Install Git"; primary: true; iconName: "git-branch"; onClicked: App.openGitInstall() }
                AppButton { text: "Check again"; iconName: "refresh"; onClicked: App.checkAgain() }
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                AppButton { text: "Details"; compact: true; onClicked: { details.title = "Git detection"; details.text = App.gitDetails; details.open() } }
                AppButton { text: "Why is Git required?"; compact: true; onClicked: notice.helpRequested() }
            }
        }
    }
    TextDialog { id: details }
}
