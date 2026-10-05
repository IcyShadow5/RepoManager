import QtQuick
import QtQuick.Controls
import RepoManager 1.0
import "../design" as Design

Menu {
    id: menu
    objectName: "projectMenu"
    width: 270
    margins: 8
    popupType: Popup.Item
    property var exportDialog
    background: Rectangle { color: Design.Theme.elevated; radius: 5; border.color: Design.Theme.border }
    delegate: MenuItem {
        id: item
        implicitHeight: 32
        contentItem: AppText { text: item.text; color: item.enabled ? Design.Theme.textPrimary : Design.Theme.textMuted; verticalAlignment: Text.AlignVCenter; leftPadding: 8 }
        background: Rectangle { color: item.highlighted ? Design.Theme.selected : "transparent"; border.color: item.activeFocus ? Design.Theme.focus : "transparent" }
    }
    Action { text: "Work on / pause this Project"; onTriggered: App.toggleWorking() }
    Action { text: "Copy path"; onTriggered: App.copySelectedPath() }
    Action { text: "Explorer"; onTriggered: App.openFolder("explorer") }
    Action { text: "VS Code"; onTriggered: App.openFolder("vscode") }
    Action { text: "Terminal"; onTriggered: App.openFolder("terminal") }
    MenuSeparator { }
    Action { text: "Changes / diff"; enabled: App.gitState === "available"; onTriggered: App.openGit("changes") }
    Action { text: "Commit…"; enabled: App.gitState === "available"; onTriggered: App.openGit("commit") }
    Action { text: "History"; enabled: App.gitState === "available"; onTriggered: App.openGit("history") }
    Action { text: "Open remote website"; onTriggered: App.openRemote() }
    Action { text: "Technical evidence"; onTriggered: App.showTechnicalEvidence() }
    MenuSeparator { }
    Action {
        text: "Export Project JSON…"
        enabled: !!menu.exportDialog
        onTriggered: if (App.prepareExport("project")) { menu.exportDialog.nameFilters = ["JSON (*.json)"]; menu.exportDialog.defaultSuffix = "json"; menu.exportDialog.open() }
    }
    Action {
        text: "Export repository report…"
        enabled: !!menu.exportDialog
        onTriggered: if (App.prepareExport("report")) { menu.exportDialog.nameFilters = ["Markdown (*.md)", "JSON (*.json)"]; menu.exportDialog.defaultSuffix = "md"; menu.exportDialog.open() }
    }
    Action { text: "Remove from RepoManager…"; enabled: !App.registryBlocked; onTriggered: App.requestAction("ignore") }
}
