import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import RepoManager 1.0
import "../design" as Design

Rectangle {
    id: strip
    property var chooserAgents: []
    implicitHeight: App.agentRun.id ? 80 : 62
    radius: 6
    color: Design.Theme.surface
    border.color: Design.Theme.border
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 12; spacing: 5
        RowLayout {
            Layout.fillWidth: true; spacing: 12
            Icon { name: "settings"; width: 27; height: 27 }
            ColumnLayout {
                spacing: 2; Layout.preferredWidth: 148
                AppText { text: "Context & automation"; font.pixelSize: 14; font.weight: Font.DemiBold }
                AppText { text: "External Agent workflow"; font.pixelSize: 11; color: Design.Theme.textMuted }
            }
            StatusBadge {
                label: App.agentActive ? "Agent running" : App.agentInfo.state === "READY" ? "Agent ready" : "Agent not ready"
                accent: App.agentActive ? Design.Theme.info : App.agentInfo.state === "READY" ? Design.Theme.success : Design.Theme.textMuted
            }
            Rectangle { width: 1; height: 29; color: Design.Theme.divider }
            ColumnLayout {
                Layout.fillWidth: true; spacing: 2
                AppText { text: "Agent: " + App.agentInfo.agentName + "  ·  Target: " + App.agentInfo.name; elide: Text.ElideRight; Layout.fillWidth: true; font.pixelSize: 12 }
                AppText {
                    text: App.agentInfo.command ? "Command: " + App.agentInfo.command + "  ·  CWD: " + App.agentInfo.cwd : App.agentInfo.reason
                    color: Design.Theme.textSecondary; elide: Text.ElideMiddle; Layout.fillWidth: true; font.pixelSize: 12
                }
            }
            AppButton { id: startAgent; objectName: "startAgentButton"; text: "Start Agent"; iconName: "player-play"; primary: true; tip: App.agentInfo.choiceCount > 1 ? "Choose an Agent to start in this repository" : App.agentInfo.reason; enabled: App.agentInfo.state === "READY" && !App.agentActive && !App.registryBlocked && App.gitState === "available"; onClicked: App.startAgent() }
            AppButton { text: "Stop Agent"; iconName: "player-stop"; enabled: App.agentActive; onClicked: App.stopAgent() }
        }
        AppText {
            visible: !!App.agentRun.id
            text: "Run " + App.agentRun.id + " · " + App.agentRun.name + " · " + App.agentRun.state + (App.agentRun.exitCode !== null && App.agentRun.exitCode !== undefined ? " · exit " + App.agentRun.exitCode : "") + " · " + App.agentRun.verification + (App.agentRun.failure ? " · " + App.agentRun.failure : "")
            color: App.agentRun.state === "FAILED_TO_START" ? Design.Theme.error : Design.Theme.textSecondary
            font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true
        }
    }
    Menu {
        id: agentMenu
        objectName: "agentChooser"
        parent: startAgent
        y: startAgent.height
        width: 280
        padding: 5
        background: Rectangle { color: Design.Theme.elevated; border.color: Design.Theme.border; radius: 5 }
        Instantiator {
            model: strip.chooserAgents
            delegate: MenuItem {
                required property var modelData
                text: (modelData.selected ? "✓ " : "") + modelData.display_name + (modelData.ready ? "" : " — unavailable")
                enabled: modelData.ready
                Accessible.name: text
                contentItem: AppText { text: parent.text; color: parent.enabled ? Design.Theme.textPrimary : Design.Theme.textMuted; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { color: parent.highlighted ? Design.Theme.selected : "transparent"; radius: 3 }
                onTriggered: App.chooseAgent(modelData.agent_id)
            }
            onObjectAdded: function(index, object) { agentMenu.insertItem(index, object) }
            onObjectRemoved: function(index, object) { agentMenu.removeItem(object) }
        }
    }
    Connections { target: App; function onAgentChooserRequested() { strip.chooserAgents = App.agentChoices.slice(); agentMenu.open() } }
}
