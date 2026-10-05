import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import RepoManager 1.0
import "../design" as Design

AppDialog {
    id: dialog
    objectName: "settingsDialog"
    title: "Settings"
    width: Math.min(760, parent.width - 40)
    height: Math.min(580, parent.height - 40)
    property var roots: []
    property var ignoredHealth: []
    property var agentTargets: []
    property int agentBrowseIndex: -1
    property string error: ""
    property string section: "Scan folders"
    function addRoot(value) {
        const result = App.addScanRoot(roots, value)
        error = result.error
        if (error) return false
        roots = result.roots
        return true
    }
    onOpened: {
        roots = App.settingsData.roots.slice()
        ignoredHealth = App.settingsData.ignoredHealth.slice()
        agentTargets = JSON.parse(JSON.stringify(App.settingsData.agentTargets))
        depth.currentIndex = Math.max(0, App.settingsData.depth - 1)
        agent.text = App.settingsData.agent
        godot.text = App.settingsData.godot
        manualRoot.text = ""
        section = "Scan folders"
        error = ""
    }
    onSectionChanged: if (body.contentItem) body.contentItem.contentY = 0
    FolderDialog { id: folder; title: "Add scan folder"; onAccepted: dialog.addRoot(App.localPath(selectedFolder)) }
    FileDialog {
        id: agentFile; title: "Select Agent executable"; nameFilters: ["Executable (*.exe *.cmd *.bat)", "All files (*)"]
        onAccepted: {
            if (dialog.agentBrowseIndex < 0) agent.text = App.localPath(selectedFile)
            else {
                let items = dialog.agentTargets.slice()
                items[dialog.agentBrowseIndex].executable = App.localPath(selectedFile)
                dialog.agentTargets = items
            }
        }
    }
    FileDialog { id: godotFile; title: "Select Godot executable"; nameFilters: ["Executable (*.exe)", "All files (*)"]; onAccepted: godot.text = App.localPath(selectedFile) }
    contentItem: ColumnLayout {
        spacing: 12
        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            Repeater {
                model: ["Scan folders", "Integrations", "Ignored projects", "Health", "Appearance"]
                AppButton {
                    required property string modelData
                    text: modelData
                    objectName: "settingsTab" + modelData
                    Layout.fillWidth: true
                    primary: dialog.section === modelData
                    onClicked: dialog.section = modelData
                }
            }
        }
        ScrollView {
            id: body
            objectName: "settingsBody"
            Layout.fillWidth: true; Layout.fillHeight: true
            contentWidth: availableWidth; clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: body.availableWidth
                spacing: 10
                ColumnLayout {
                    visible: dialog.section === "Scan folders"; Layout.fillWidth: true; spacing: 10
                    AppText { text: "Search scope"; font.weight: Font.DemiBold; font.pixelSize: 15 }
                    AppText { text: "Only these folders are scanned. Add your repository collection here; saving starts a new scan."; wrapMode: Text.WordWrap; color: Design.Theme.textSecondary; Layout.fillWidth: true }
                    Rectangle {
                        Layout.fillWidth: true; Layout.preferredHeight: 140
                        color: Design.Theme.background; border.color: Design.Theme.border; radius: 5
                        ListView {
                            anchors.fill: parent; anchors.margins: 4; clip: true
                            model: dialog.roots
                            ScrollBar.vertical: ScrollBar {}
                            delegate: RowLayout {
                                required property string modelData
                                required property int index
                                width: ListView.view.width; height: 36
                                Icon { name: "folder"; width: 18; height: 18 }
                                AppText { text: modelData; elide: Text.ElideMiddle; Layout.fillWidth: true; font.pixelSize: 12; ToolTip.visible: rootHover.hovered; ToolTip.text: modelData; HoverHandler { id: rootHover } }
                                AppButton { iconName: "x"; compact: true; tip: "Remove scan folder"; onClicked: { let changed = dialog.roots.slice(); changed.splice(index, 1); dialog.roots = changed; dialog.error = "" } }
                            }
                        }
                        AppText { visible: dialog.roots.length === 0; anchors.centerIn: parent; text: "No scan folders configured"; color: Design.Theme.textMuted }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        AppButton { text: "Add folder…"; iconName: "folder"; compact: true; onClicked: folder.open() }
                        AppButton { objectName: "addCDrive"; text: "Add C:\\"; compact: true; visible: Qt.platform.os === "windows"; onClicked: dialog.addRoot("C:\\") }
                        Item { Layout.fillWidth: true }
                        AppText { text: "Scan depth"; color: Design.Theme.textSecondary }
                        AppCombo { id: depth; objectName: "scanDepth"; model: ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]; Layout.preferredWidth: 65 }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        AppField { id: manualRoot; objectName: "scanRootPath"; placeholderText: "Or enter a folder path…"; Layout.fillWidth: true; onAccepted: if (dialog.addRoot(text)) text = "" }
                        AppButton { objectName: "addScanPath"; text: "Add path"; compact: true; enabled: !!manualRoot.text.trim(); onClicked: if (dialog.addRoot(manualRoot.text)) manualRoot.text = "" }
                    }
                    AppText { text: "Depth " + (depth.currentIndex + 1) + " checks the scan folder itself and up to " + (depth.currentIndex + 1) + " nested folder levels. Repositories deeper than this are not discovered."; wrapMode: Text.WordWrap; color: Design.Theme.textSecondary; Layout.fillWidth: true }
                    AppText { text: "Scanning all of C:\\ can take longer and encounter protected folders. A folder closer to your repositories is usually faster."; wrapMode: Text.WordWrap; color: Design.Theme.textMuted; Layout.fillWidth: true }
                }
                ColumnLayout {
                    visible: dialog.section === "Integrations"; Layout.fillWidth: true; spacing: 10
                    AppText { text: "External Agent"; font.weight: Font.DemiBold; font.pixelSize: 15 }
                    AppText { text: "Launched explicitly for the selected repository. Enter an executable path or a command available on PATH, without arguments."; wrapMode: Text.WordWrap; color: Design.Theme.textSecondary; Layout.fillWidth: true }
                    RowLayout {
                        Layout.fillWidth: true
                        AppField { id: agent; objectName: "agentExecutable"; Layout.fillWidth: true; placeholderText: "opencode" }
                        AppButton { text: "Browse…"; compact: true; onClicked: { dialog.agentBrowseIndex = -1; agentFile.open() } }
                    }
                    AppText { text: "Additional Agent targets"; font.weight: Font.DemiBold; Layout.topMargin: 6 }
                    AppText { visible: !!App.settingsData.agentConfigurationError; text: App.settingsData.agentConfigurationError + ". Reconfigure the additional targets before saving."; color: Design.Theme.error; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    AppText { text: "OpenCode, Codex and Gemini CLI are offered when their commands are found on PATH. Add other configured executables here. Each target uses the selected repository as its working directory."; wrapMode: Text.WordWrap; color: Design.Theme.textSecondary; Layout.fillWidth: true }
                    Repeater {
                        model: dialog.agentTargets
                        RowLayout {
                            required property var modelData
                            required property int index
                            Layout.fillWidth: true
                            AppField { text: modelData.display_name; placeholderText: "Agent name"; Layout.preferredWidth: 145; onTextEdited: dialog.agentTargets[index].display_name = text }
                            AppField { text: modelData.executable; placeholderText: "Executable / command"; Layout.fillWidth: true; onTextEdited: dialog.agentTargets[index].executable = text }
                            AppButton { text: "Browse…"; compact: true; onClicked: { dialog.agentBrowseIndex = index; agentFile.open() } }
                            AppButton { iconName: "x"; compact: true; tip: "Remove Agent target"; onClicked: { let items = dialog.agentTargets.slice(); items.splice(index, 1); dialog.agentTargets = items } }
                        }
                    }
                    AppButton { objectName: "addAgentTarget"; text: "Add Agent target"; compact: true; enabled: dialog.agentTargets.length < 20; onClicked: dialog.agentTargets = dialog.agentTargets.concat([App.newAgentTarget()]) }
                    AppText { text: "Godot executable (optional)"; font.weight: Font.DemiBold; font.pixelSize: 15; Layout.topMargin: 10 }
                    AppText { text: "Used by detected Godot launchers. Leave empty when you do not use Godot."; wrapMode: Text.WordWrap; color: Design.Theme.textSecondary; Layout.fillWidth: true }
                    RowLayout {
                        Layout.fillWidth: true
                        AppField { id: godot; objectName: "godotExecutable"; Layout.fillWidth: true; placeholderText: "Path to Godot executable" }
                        AppButton { text: "Browse…"; compact: true; onClicked: godotFile.open() }
                    }
                }
                ColumnLayout {
                    visible: dialog.section === "Ignored projects"; Layout.fillWidth: true
                    AppText { text: "Restore a Project without changing repository files."; color: Design.Theme.textSecondary; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    Repeater {
                        model: App.ignoredProjects
                        RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            ColumnLayout {
                                Layout.fillWidth: true
                                AppText { text: modelData.name + " · " + modelData.status; font.weight: Font.DemiBold; Layout.fillWidth: true }
                                AppText { text: modelData.path; color: Design.Theme.textSecondary; font.pixelSize: 12; elide: Text.ElideMiddle; Layout.fillWidth: true }
                                AppText { text: modelData.id; color: Design.Theme.textMuted; font.pixelSize: 10; Layout.fillWidth: true }
                            }
                            AppButton { text: "Restore"; compact: true; enabled: !App.registryBlocked; onClicked: App.restoreProject(modelData.id) }
                        }
                    }
                    AppText { visible: !App.ignoredProjects.length; text: "No ignored Projects"; color: Design.Theme.textMuted }
                }
                ColumnLayout {
                    visible: dialog.section === "Health"; Layout.fillWidth: true; spacing: 10
                    AppText { text: "Advisory Health checks"; font.pixelSize: 15; font.weight: Font.DemiBold }
                    AppText { text: "Unchecked types are explicitly IGNORED across repositories and excluded from active scoring. Repository integrity, Git access and working-tree checks always remain enabled. Per-repository exclusions can be restored in repository Health details."; wrapMode: Text.WordWrap; color: Design.Theme.textSecondary; Layout.fillWidth: true }
                    Repeater {
                        model: App.healthCheckTypes
                        AppCheckBox {
                            required property var modelData
                            objectName: "globalHealth_" + modelData.rule
                            text: modelData.label
                            checked: dialog.ignoredHealth.indexOf(modelData.rule) < 0
                            onClicked: {
                                let values = dialog.ignoredHealth.filter(function(rule) { return rule !== modelData.rule })
                                if (!checked) values.push(modelData.rule)
                                dialog.ignoredHealth = values
                            }
                        }
                    }
                }
                ColumnLayout {
                    visible: dialog.section === "Appearance"; Layout.fillWidth: true; spacing: 10
                    AppText { text: "Theme: " + App.themeName; font.pixelSize: 15; font.weight: Font.DemiBold }
                    AppButton { text: "Switch light / dark"; onClicked: App.toggleTheme() }
                    AppText { text: "Repository table columns"; font.pixelSize: 15; font.weight: Font.DemiBold; Layout.topMargin: 10 }
                    AppText { text: "Drag table-header separators to resize columns. Safe bounds prevent hidden identity and state values. Restore defaults if the layout no longer fits your workflow."; wrapMode: Text.WordWrap; color: Design.Theme.textSecondary; Layout.fillWidth: true }
                    AppButton { text: "Restore column defaults"; onClicked: App.resetColumns() }
                }
            }
        }
        AppText { visible: dialog.error !== ""; text: dialog.error; color: Design.Theme.error; wrapMode: Text.WordWrap; Layout.fillWidth: true }
        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            AppButton { objectName: "settingsCancel"; text: "Cancel"; onClicked: dialog.reject() }
            AppButton {
                objectName: "settingsSave"
                text: "Save & scan"; primary: true; iconName: "check"
                onClicked: {
                    if (App.saveSettings(dialog.roots, depth.currentIndex + 1, agent.text, godot.text, dialog.ignoredHealth, dialog.agentTargets)) dialog.accept()
                    else dialog.error = App.statusText
                }
            }
        }
    }
}
