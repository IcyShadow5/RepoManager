import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import RepoManager 1.0
import "design" as Design
import "components" as UI

ApplicationWindow {
    id: window
    objectName: "repoManagerWindow"
    visible: true
    width: 1660; height: 940
    minimumWidth: 1120; minimumHeight: 640
    title: "RepoManager " + App.versionText
    color: Design.Theme.background
    property bool extendedColumns: false
    font.family: Design.Theme.fontFamily
    onClosing: function(close) { close.accepted = App.prepareClose() }
    Shortcut { sequence: "Ctrl+F"; enabled: window.Overlay.overlay && !window.Overlay.overlay.visible; onActivated: search.forceActiveFocus() }
    Shortcut { sequence: "F5"; enabled: !App.scanning && window.Overlay.overlay && !window.Overlay.overlay.visible; onActivated: App.registryBlocked ? App.retryRegistry() : App.scan() }
    Shortcut { sequence: "F1"; enabled: window.Overlay.overlay && !window.Overlay.overlay.visible; onActivated: window.showHelp("Guide") }
    UI.SettingsDialog { id: settings }
    UI.HelpDialog { id: help }
    UI.FeedbackDialog { id: feedback }
    UI.GitDialog { id: gitDialog }
    UI.InventoryDialog { id: inventory }
    UI.TextDialog { id: notice }
    UI.AppDialog {
        id: actionConfirm
        property string message: ""
        width: Math.min(730, parent.width - 40)
        contentItem: ColumnLayout {
            spacing: 12
            UI.AppText { text: actionConfirm.message; wrapMode: Text.WrapAnywhere; color: Design.Theme.textSecondary; Layout.fillWidth: true; Layout.maximumHeight: 380 }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                UI.AppButton { text: "Cancel"; onClicked: actionConfirm.reject() }
                UI.AppButton { text: "Confirm"; primary: true; onClicked: { actionConfirm.accept(); App.executeAction() } }
            }
        }
    }
    UI.AppDialog {
        id: closeScan
        objectName: "closeScanDialog"
        title: "A repository scan is still running"
        width: Math.min(650, parent.width - 40)
        contentItem: ColumnLayout {
            spacing: 14
            UI.AppText { text: "Cancel the scan before exiting. The current filesystem or Git operation may need to finish; incomplete scan results will not be saved."; color: Design.Theme.textSecondary; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                UI.AppButton { objectName: "returnFromScanClose"; text: "Return to app"; onClicked: closeScan.reject() }
                UI.AppButton { objectName: "keepScanning"; text: "Keep scanning"; onClicked: closeScan.reject() }
                UI.AppButton { objectName: "cancelScanAndExit"; text: "Cancel scan and exit"; primary: true; onClicked: { closeScan.accept(); App.cancelScanAndClose() } }
            }
        }
    }
    UI.AppDialog {
        id: closeAgent
        title: "Agent is still running"
        contentItem: ColumnLayout {
            spacing: 14
            UI.AppText { text: "Stop the Agent before closing RepoManager. Process exit must be confirmed before the window closes."; color: Design.Theme.textSecondary; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                UI.AppButton { text: "Cancel closing"; onClicked: closeAgent.reject() }
                UI.AppButton { text: "Stop Agent and close"; primary: true; onClicked: { closeAgent.accept(); App.stopAgentsAndClose() } }
            }
        }
    }
    Connections {
        target: App
        function onConfirmScanCloseRequested() { closeScan.open() }
        function onConfirmAgentCloseRequested() { closeAgent.open() }
        function onCloseReady() { window.close() }
        function onNoticeRequested(title, text) { notice.title = title; notice.text = text; notice.open() }
        function onActionConfirmationRequested(title, text) { actionConfirm.title = title; actionConfirm.message = text; actionConfirm.open() }
    }
    function showHelp(topic) { help.topic = topic; help.open() }

    RowLayout {
        anchors.fill: parent
        spacing: 0
        Rectangle {
            Layout.preferredWidth: 180; Layout.fillHeight: true
            color: Design.Theme.sidebar
            Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Design.Theme.border }
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 12; spacing: 7
                RowLayout {
                    Layout.fillWidth: true; Layout.preferredHeight: 59; spacing: 9
                    Image { source: "../../assets/repomanager.svg"; sourceSize.width: 96; sourceSize.height: 96; Layout.preferredWidth: 36; Layout.preferredHeight: 36; fillMode: Image.PreserveAspectFit; smooth: true }
                    ColumnLayout {
                        spacing: 1
                        UI.AppText { text: "RepoManager"; font.pixelSize: 15; font.weight: Font.DemiBold }
                        UI.AppText { text: "REPOSITORIES"; color: Design.Theme.icy; font.pixelSize: 10; font.letterSpacing: 1.1 }
                    }
                }
                Rectangle { height: 1; color: Design.Theme.divider; Layout.fillWidth: true }
                UI.AppText { text: "WORKSPACE"; color: Design.Theme.textMuted; font.pixelSize: 10; font.weight: Font.DemiBold; font.letterSpacing: 1; Layout.topMargin: 17; Layout.leftMargin: 12; Layout.bottomMargin: 4 }
                UI.NavItem { Layout.fillWidth: true; iconName: "database"; label: "Repositories"; selected: App.section === "repositories"; onActivated: App.setSection("repositories") }
                UI.NavItem { Layout.fillWidth: true; iconName: "focus-2"; label: "Working on now"; selected: App.section === "working"; onActivated: App.setSection("working") }
                UI.NavItem { Layout.fillWidth: true; iconName: "alert-triangle"; label: "Inventory issues"; selected: inventory.visible; onActivated: inventory.open() }
                Item { Layout.fillHeight: true }
                Rectangle {
                    Layout.fillWidth: true; implicitHeight: 58; radius: 5
                    color: Design.Theme.surface; border.color: Design.Theme.divider
                    RowLayout {
                        anchors.fill: parent; anchors.margins: 10; spacing: 8
                        UI.Icon { name: "git-branch"; tint: App.gitState === "available" ? Design.Theme.success : Design.Theme.warning; width: 19; height: 19 }
                        ColumnLayout {
                            spacing: 2
                            UI.AppText { text: App.gitState === "available" ? "Git available" : "Git unavailable"; font.pixelSize: 12; font.weight: Font.DemiBold }
                            UI.AppText { text: "Local repositories"; color: Design.Theme.textMuted; font.pixelSize: 11 }
                        }
                    }
                }
                UI.AppText { text: "Version " + App.versionText; color: Design.Theme.textMuted; font.pixelSize: 11; Layout.leftMargin: 10; Layout.topMargin: 7; Layout.bottomMargin: 5 }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true; Layout.fillHeight: true
            Layout.margins: 12
            spacing: 9
            RowLayout {
                Layout.fillWidth: true; Layout.preferredHeight: 49; spacing: 10
                UI.AppText { text: App.section === "working" ? "Working on now" : "Repositories"; font.pixelSize: Design.Theme.titleSize; font.weight: Font.DemiBold }
                Item { Layout.preferredWidth: 9 }
                UI.SearchField {
                    id: search
                    objectName: "repositorySearch"
                    Layout.fillWidth: true
                    placeholderText: "Filter repositories…  (name, path, focus)"
                    text: App.query
                    onTextEdited: App.setQuery(text)
                    Keys.onEscapePressed: App.setQuery("")
                }
                UI.AppButton { objectName: "scanButton"; text: App.scanning ? "Scanning…" : "Scan (F5)"; primary: true; iconName: "refresh"; enabled: !App.scanning && !App.registryBlocked && App.gitState === "available"; onClicked: App.scan() }
                UI.AppButton { text: window.width > 1280 ? "Settings" : ""; iconName: "settings"; tip: "Settings"; onClicked: settings.open() }
                UI.AppButton { text: window.width > 1380 ? "Help" : ""; iconName: "help-circle"; tip: "Help"; onClicked: window.showHelp("Guide") }
                UI.AppButton { text: window.width > 1480 ? "Feedback" : "Bug"; iconName: "info-circle"; tip: "Feedback & bug reports"; onClicked: feedback.open() }
                UI.AppButton { text: window.width > 1480 ? (App.themeName === "dark" ? "Dark" : "Light") : ""; iconName: App.themeName === "dark" ? "moon" : "sun"; tip: "Switch light / dark theme"; onClicked: App.toggleTheme() }
            }
            Rectangle {
                visible: App.scanning; Layout.fillWidth: true; implicitHeight: 62
                color: Design.Theme.surface; radius: 5; border.color: Design.Theme.border
                RowLayout {
                    anchors.fill: parent; anchors.margins: 9; spacing: 10
                    BusyIndicator { running: App.scanning; Layout.preferredWidth: 28; Layout.preferredHeight: 28 }
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 2
                        UI.AppText { text: (App.scanProgress.cancelling ? "Cancellation pending" : App.scanProgress.phase || "Scanning") + " · " + (App.scanProgress.directories || 0) + " directories · " + (App.scanProgress.repositories || 0) + " repositories found · " + (App.scanProgress.inspected || 0) + " inspected"; elide: Text.ElideRight; Layout.fillWidth: true }
                        UI.AppText { text: App.scanProgress.path || "Preparing configured folders…"; color: Design.Theme.textSecondary; font.pixelSize: 11; elide: Text.ElideMiddle; Layout.fillWidth: true }
                    }
                    UI.AppButton { objectName: "cancelScanButton"; text: App.scanProgress.cancelling ? "Cancelling…" : "Cancel scan"; enabled: !App.scanProgress.cancelling; onClicked: App.cancelScan() }
                }
            }
            Rectangle {
                visible: !!App.recoveryText; Layout.fillWidth: true; implicitHeight: 48
                color: Design.Theme.surface; radius: 5; border.color: Design.Theme.warning
                RowLayout {
                    anchors.fill: parent; anchors.margins: 8
                    UI.Icon { name: "alert-triangle"; tint: Design.Theme.warning; width: 19; height: 19 }
                    UI.AppText { text: App.registryBlocked ? "Registry recovery required · scanning and changes paused" : "Recovered application data · review the recovery details"; elide: Text.ElideRight; Layout.fillWidth: true }
                    UI.AppButton { text: "Details"; compact: true; onClicked: { notice.title = "Application-data recovery"; notice.text = App.recoveryText; notice.open() } }
                    UI.AppButton { text: "Retry registry"; compact: true; visible: App.registryBlocked; onClicked: App.retryRegistry() }
                }
            }
            UI.GitNotice { visible: App.gitState !== "available"; Layout.fillWidth: true; onHelpRequested: window.showHelp("Git requirement") }
            RowLayout {
                Layout.fillWidth: true; spacing: 9
                UI.MetricCard { Layout.fillWidth: true; label: "Repositories"; value: String(App.totalCount); iconName: "database"; accent: Design.Theme.icy }
                UI.MetricCard { Layout.fillWidth: true; label: "Clean working trees"; value: App.gitState === "available" ? String(App.cleanCount) : "—"; iconName: "check"; accent: Design.Theme.success }
                UI.MetricCard { Layout.fillWidth: true; label: "Modified working trees"; value: App.gitState === "available" ? String(App.modifiedCount) : "—"; iconName: "git-branch"; accent: Design.Theme.warning }
                UI.MetricCard { Layout.fillWidth: true; label: "Unobserved"; value: String(App.unobservedCount); iconName: "info-circle"; accent: Design.Theme.info }
            }
            UI.ContextStrip { Layout.fillWidth: true; onSettingsRequested: settings.openSection("Integrations") }
            Rectangle {
                Layout.fillWidth: true; implicitHeight: 62; radius: 6
                color: Design.Theme.surface; border.color: Design.Theme.border
                RowLayout {
                    anchors.fill: parent; anchors.margins: 12; spacing: 12
                    UI.Icon { name: "focus-2"; width: 27; height: 27 }
                    ColumnLayout {
                        spacing: 2; Layout.preferredWidth: 148
                        UI.AppText { text: "Working on now"; font.pixelSize: 14; font.weight: Font.DemiBold }
                        UI.AppText { text: "Active project context"; color: Design.Theme.textMuted; font.pixelSize: 11 }
                    }
                    Rectangle { width: 1; height: 33; color: Design.Theme.divider }
                    UI.Icon { name: "folder"; width: 21; height: 21; visible: !!App.workingProject.projectId }
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 2
                        UI.AppText { text: App.workingProject.projectName || "Choose a project to work on"; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                        UI.AppText { text: App.workingProject.path || "Select a project and choose Work on this in its details."; color: Design.Theme.textSecondary; font.pixelSize: 12; elide: Text.ElideMiddle; Layout.fillWidth: true }
                    }
                    UI.AppButton { text: "View active projects"; iconName: "chevron-right"; compact: true; enabled: !!App.workingProject.projectId; onClicked: App.setSection("working") }
                }
            }
            RowLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; spacing: 10
                Rectangle {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    color: Design.Theme.surface; radius: 6; border.color: Design.Theme.border
                    ColumnLayout {
                        anchors.fill: parent; spacing: 0
                        RowLayout {
                            Layout.fillWidth: true; Layout.preferredHeight: 44
                            Layout.leftMargin: 12; Layout.rightMargin: 12; spacing: 8
                            UI.Icon { name: "database"; width: 22; height: 22 }
                            UI.AppText { text: (App.section === "working" ? "Active projects" : "Repositories") + " (" + App.visibleCount + ")"; font.pixelSize: 15; font.weight: Font.DemiBold }
                            Item { Layout.fillWidth: true }
                            UI.AppButton { text: window.extendedColumns ? "Compact" : "More columns"; compact: true; tip: "Show classification, sync and Worktree count"; onClicked: window.extendedColumns = !window.extendedColumns }
                            Rectangle { width: 7; height: 7; radius: 4; color: App.problemCount ? Design.Theme.warning : App.scanning ? Design.Theme.icy : Design.Theme.success }
                            UI.AppButton { text: App.scanning ? "Scanning" : App.problemCount ? App.problemCount + " inventory issues" : "Local inventory"; compact: true; enabled: !!App.problemCount; onClicked: inventory.open() }
                        }
                        Rectangle { color: Design.Theme.divider; height: 1; Layout.fillWidth: true }
                        Flickable {
                            id: tableScroll
                            Layout.fillWidth: true; Layout.fillHeight: true
                            contentWidth: Math.max(width, 90 + App.columnWidths.name + App.columnWidths.status + App.columnWidths.branch + App.columnWidths.dirty + App.columnWidths.tree + App.columnWidths.last_commit + App.columnWidths.path + (window.extendedColumns ? App.columnWidths.classification + App.columnWidths.sync + App.columnWidths.worktrees + 24 : 0)); contentHeight: height
                            flickableDirection: Flickable.HorizontalFlick
                            boundsBehavior: Flickable.StopAtBounds
                            clip: true
                            ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }
                            Column {
                                width: tableScroll.contentWidth; height: tableScroll.height
                                Rectangle {
                                    width: parent.width; height: 32; color: Design.Theme.elevated
                                    RowLayout {
                                        anchors.fill: parent; anchors.leftMargin: 39; anchors.rightMargin: 34; spacing: 8
                                        Repeater {
                                            model: [
                                                {label: "Name", column: "name", sortable: true},
                                                {label: "Class", column: "classification", sortable: true, extended: true},
                                                {label: "Status", column: "status", sortable: true},
                                                {label: "Branch", column: "branch", sortable: true},
                                                {label: "Dirty", column: "dirty", sortable: true},
                                                {label: "Working tree", column: "tree", sortable: false},
                                                {label: "↑ / ↓", column: "sync", sortable: true, extended: true},
                                                {label: "Trees", column: "worktrees", sortable: true, extended: true},
                                                {label: "Last commit", column: "last_commit", sortable: true}
                                            ]
                                            AbstractButton {
                                                id: heading
                                                required property var modelData
                                                objectName: "repositoryHeader" + modelData.column
                                                visible: !modelData.extended || window.extendedColumns
                                                Layout.preferredWidth: App.columnWidths[modelData.column]; Layout.preferredHeight: 32
                                                Layout.minimumWidth: Layout.preferredWidth
                                                Layout.maximumWidth: Layout.preferredWidth
                                                focusPolicy: Qt.StrongFocus
                                                onClicked: if (modelData.sortable) App.sortBy(modelData.column)
                                                background: Rectangle { color: parent.hovered ? Design.Theme.surface : "transparent"; border.color: parent.activeFocus ? Design.Theme.focus : "transparent" }
                                                contentItem: UI.AppText {
                                                    text: modelData.label + (App.sortColumn === modelData.column && modelData.column ? (App.sortDescending ? " ↓" : " ↑") : "")
                                                    color: Design.Theme.textSecondary; font.pixelSize: 12; font.weight: Font.DemiBold; verticalAlignment: Text.AlignVCenter
                                                }
                                                MouseArea {
                                                    anchors.right: parent.right; width: 8; height: parent.height
                                                    cursorShape: Qt.SplitHCursor
                                                    property real startX: 0
                                                    property int startWidth: 0
                                                    onPressed: function(mouse) { startX = mapToItem(null, mouse.x, mouse.y).x; startWidth = heading.width }
                                                    onPositionChanged: function(mouse) { if (pressed) App.resizeColumn(heading.modelData.column, Math.round(startWidth + mapToItem(null, mouse.x, mouse.y).x - startX), false) }
                                                    onReleased: App.resizeColumn(heading.modelData.column, heading.width, true)
                                                }
                                            }
                                        }
                                        AbstractButton {
                                            id: pathHeading
                                            Layout.fillWidth: true; Layout.preferredHeight: 32
                                            focusPolicy: Qt.StrongFocus; onClicked: App.sortBy("path")
                                            contentItem: UI.AppText { text: "Path"; color: Design.Theme.textSecondary; font.pixelSize: 12; font.weight: Font.DemiBold; verticalAlignment: Text.AlignVCenter }
                                            MouseArea {
                                                anchors.right: parent.right; width: 8; height: parent.height; cursorShape: Qt.SplitHCursor
                                                property real startX: 0; property int startWidth: 0
                                                onPressed: function(mouse) { startX = mapToItem(null, mouse.x, mouse.y).x; startWidth = pathHeading.width }
                                                onPositionChanged: function(mouse) { if (pressed) App.resizeColumn("path", Math.round(startWidth + mapToItem(null, mouse.x, mouse.y).x - startX), false) }
                                                onReleased: App.resizeColumn("path", App.columnWidths.path, true)
                                            }
                                        }
                                    }
                                }
                                ListView {
                                    id: list
                                    objectName: "repositoryList"
                                    width: parent.width; height: parent.height - 32
                                    clip: true
                                    model: App.repositoryModel
                                    currentIndex: App.selectedIndex
                                    onCurrentIndexChanged: if (currentIndex >= 0) positionViewAtIndex(currentIndex, ListView.Contain)
                                    activeFocusOnTab: true
                                    Keys.onUpPressed: App.moveSelection(-1)
                                    Keys.onDownPressed: App.moveSelection(1)
                                    Keys.onPressed: function(event) {
                                        if (event.key === Qt.Key_Home) { App.moveSelection(-App.visibleCount); event.accepted = true }
                                        else if (event.key === Qt.Key_End) { App.moveSelection(App.visibleCount); event.accepted = true }
                                        else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) { App.launchPrimary(); event.accepted = true }
                                        else if (event.key === Qt.Key_Menu || event.key === Qt.Key_F10 && event.modifiers & Qt.ShiftModifier) { details.showActions(); event.accepted = true }
                                        else if (event.key === Qt.Key_Escape) { App.setQuery(""); event.accepted = true }
                                    }
                                    boundsBehavior: Flickable.StopAtBounds
                                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                                    delegate: UI.RepositoryRow {
                                        width: list.width
                                        projectId: model.projectId; projectName: model.projectName; projectStatus: model.projectStatus
                                        branch: model.branch; dirty: model.dirty; dirtyKnown: model.dirtyKnown; broken: model.broken
                                        lastCommit: model.lastCommit; path: model.path; displayPath: model.displayPath; pinned: model.pinned
                                        classification: model.classification; ahead: model.ahead; behind: model.behind; syncKnown: model.syncKnown
                                        worktreeCount: model.worktreeCount; worktreesKnown: model.worktreesKnown
                                        extendedColumns: window.extendedColumns
                                        stripeIndex: index
                                        selected: projectId === App.selectedProject.projectId
                                        gitAvailable: App.gitState === "available"
                                        onActivated: function(id) { list.forceActiveFocus(); App.selectProject(id) }
                                        onActionsRequested: function(id) { App.selectProject(id); if (App.selectedProject.projectId === id) details.showActions() }
                                    }
                                    Rectangle { anchors.fill: parent; color: "transparent"; border.color: list.activeFocus ? Design.Theme.focus : "transparent"; z: 2 }
                                }
                            }
                            ColumnLayout {
                                visible: App.visibleCount === 0
                                anchors.centerIn: parent
                                spacing: 8
                                UI.Icon { name: "folder"; width: 35; height: 35; Layout.alignment: Qt.AlignHCenter; tint: Design.Theme.textMuted }
                                UI.AppText { text: App.scanning ? "Scanning repositories…" : App.query ? "No matching projects" : "No projects in this view"; font.pixelSize: 16; font.weight: Font.DemiBold; Layout.alignment: Qt.AlignHCenter }
                                UI.AppText { text: App.scanning ? "The inventory will appear when the scan completes." : App.section === "working" ? "Set a project to Active to show it here." : "Add scan folders in Settings, then scan."; color: Design.Theme.textSecondary; font.pixelSize: 12; Layout.alignment: Qt.AlignHCenter }
                                UI.AppButton { visible: !App.query && App.section !== "working" && !App.scanning; text: "Configure scan folders"; iconName: "settings"; Layout.alignment: Qt.AlignHCenter; onClicked: settings.open() }
                            }
                        }
                    }
                }
                UI.DetailPanel { id: details; objectName: "detailPanel"; Layout.preferredWidth: window.width > 1450 ? 390 : 340; Layout.fillHeight: true }
            }
            RowLayout {
                Layout.fillWidth: true; Layout.preferredHeight: 21
                UI.AppText { text: App.statusText; color: App.gitState === "available" ? Design.Theme.textSecondary : Design.Theme.warning; font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true }
                UI.AppText { text: "Last scan  " + App.lastScan; color: Design.Theme.textMuted; font.pixelSize: 11 }
            }
        }
    }
}
