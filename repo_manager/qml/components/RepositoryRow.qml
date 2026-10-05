import QtQuick
import QtQuick.Layouts
import QtQuick.Controls
import RepoManager 1.0
import "../design" as Design

Rectangle {
    id: row
    property string projectId: ""
    property string projectName: ""
    property string projectStatus: ""
    property string branch: ""
    property int dirty: 0
    property bool dirtyKnown: false
    property bool broken: false
    property string lastCommit: ""
    property string path: ""
    property string displayPath: ""
    property bool pinned: false
    property bool selected: false
    property bool gitAvailable: true
    property int stripeIndex: 0
    property string classification: ""
    property int ahead: 0
    property int behind: 0
    property bool syncKnown: false
    property int worktreeCount: 0
    property bool worktreesKnown: false
    property bool extendedColumns: false
    signal activated(string id)
    signal actionsRequested(string id)
    height: Design.Theme.rowHeight
    color: selected ? Design.Theme.selected
         : mouse.containsMouse ? Design.Theme.elevated
         : stripeIndex % 2 ? Design.Theme.rowAlternate : Design.Theme.surface
    Rectangle {
        width: 3; height: parent.height
        visible: row.selected
        color: Design.Theme.icy
    }
    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 12
        anchors.rightMargin: 12
        spacing: 8
        Icon {
            name: row.broken ? "alert-triangle" : "folder"
            tint: row.broken ? Design.Theme.error : row.gitAvailable && row.dirtyKnown && row.dirty > 0 ? Design.Theme.warning : Design.Theme.icy
            Layout.preferredWidth: 19
            Layout.minimumWidth: 19
            Layout.maximumWidth: 19
            Layout.preferredHeight: 19
        }
        AppText {
            text: row.projectName
            color: row.broken ? Design.Theme.error : row.gitAvailable && row.dirtyKnown && row.dirty > 0 ? Design.Theme.warning : Design.Theme.textPrimary
            font.pixelSize: 13
            font.weight: row.selected ? Font.DemiBold : Font.Normal
            elide: Text.ElideRight
            Layout.preferredWidth: App.columnWidths.name
            Layout.minimumWidth: Layout.preferredWidth
            Layout.maximumWidth: Layout.preferredWidth
        }
        AppText { visible: row.extendedColumns; text: row.classification; color: Design.Theme.textSecondary; font.pixelSize: 12; elide: Text.ElideRight; Layout.preferredWidth: App.columnWidths.classification; Layout.minimumWidth: Layout.preferredWidth; Layout.maximumWidth: Layout.preferredWidth }
        AppText {
            text: row.projectStatus
            color: row.projectStatus === "active" ? Design.Theme.icy : Design.Theme.textSecondary
            font.pixelSize: Design.Theme.tableSize
            elide: Text.ElideRight
            Layout.preferredWidth: App.columnWidths.status
            Layout.minimumWidth: Layout.preferredWidth
            Layout.maximumWidth: Layout.preferredWidth
        }
        AppText {
            text: row.gitAvailable ? row.branch : "—"
            color: Design.Theme.textSecondary
            font.family: Design.Theme.monoFamily
            font.pixelSize: 11
            elide: Text.ElideRight
            Layout.preferredWidth: App.columnWidths.branch
            Layout.minimumWidth: Layout.preferredWidth
            Layout.maximumWidth: Layout.preferredWidth
        }
        AppText {
            text: row.gitAvailable && row.dirtyKnown ? String(row.dirty) : "—"
            color: row.gitAvailable && row.dirtyKnown && row.dirty > 0 ? Design.Theme.warning : Design.Theme.textSecondary
            font.pixelSize: 12
            Layout.preferredWidth: App.columnWidths.dirty
            Layout.minimumWidth: Layout.preferredWidth
            Layout.maximumWidth: Layout.preferredWidth
            horizontalAlignment: Text.AlignHCenter
        }
        RowLayout {
            Layout.preferredWidth: App.columnWidths.tree
            Layout.minimumWidth: Layout.preferredWidth
            Layout.maximumWidth: Layout.preferredWidth
            spacing: 5
            Rectangle {
                width: 7; height: 7; radius: 4
                color: !row.gitAvailable || !row.dirtyKnown ? Design.Theme.textMuted
                     : row.dirty > 0 ? Design.Theme.warning : Design.Theme.success
            }
            AppText {
                text: row.broken ? "Unavailable" : !row.gitAvailable || !row.dirtyKnown ? "Unknown" : row.dirty > 0 ? "Modified" : "Clean"
                color: !row.gitAvailable || !row.dirtyKnown ? Design.Theme.textMuted : row.dirty > 0 ? Design.Theme.warning : Design.Theme.success
                font.pixelSize: 12
                Layout.fillWidth: true
            }
        }
        AppText { visible: row.extendedColumns; text: row.gitAvailable && row.syncKnown ? row.ahead + " / " + row.behind : "—"; color: row.ahead || row.behind ? Design.Theme.info : Design.Theme.textSecondary; font.pixelSize: 12; Layout.preferredWidth: App.columnWidths.sync; Layout.minimumWidth: Layout.preferredWidth; Layout.maximumWidth: Layout.preferredWidth }
        AppText { visible: row.extendedColumns; text: row.gitAvailable && row.worktreesKnown ? String(row.worktreeCount) : "—"; color: Design.Theme.textSecondary; font.pixelSize: 12; Layout.preferredWidth: App.columnWidths.worktrees; Layout.minimumWidth: Layout.preferredWidth; Layout.maximumWidth: Layout.preferredWidth }
        AppText {
            objectName: "repositoryLastCommitCell"
            text: row.gitAvailable ? row.lastCommit : "—"
            color: Design.Theme.textSecondary
            font.pixelSize: 12
            Layout.preferredWidth: App.columnWidths.last_commit
            Layout.minimumWidth: Layout.preferredWidth
            Layout.maximumWidth: Layout.preferredWidth
            elide: Text.ElideRight
        }
        AppText {
            objectName: "repositoryPathCell"
            text: row.displayPath || row.path
            color: Design.Theme.textSecondary
            font.pixelSize: 12
            elide: Text.ElideMiddle
            Layout.fillWidth: true
        }
        Icon {
            visible: row.pinned
            name: "pin"; tint: row.selected ? Design.Theme.textPrimary : Design.Theme.textMuted
            Layout.preferredWidth: 14; Layout.preferredHeight: 14
        }
    }
    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        onClicked: function(mouse) { if (mouse.button === Qt.RightButton) row.actionsRequested(row.projectId); else row.activated(row.projectId) }
        onDoubleClicked: if (App.selectedProject.projectId === row.projectId) App.launchPrimary()
    }
    ToolTip.visible: mouse.containsMouse
    ToolTip.text: row.projectName + "\n" + row.path
    ToolTip.delay: 700
}
