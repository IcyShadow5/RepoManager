import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import RepoManager 1.0
import "../design" as Design
import "." as UI

AppDialog {
    id: dialog
    objectName: "inventoryDialog"
    title: "Inventory issues & possible moves"
    width: Math.min(1000, parent.width - 40)
    height: Math.min(690, parent.height - 40)
    property int selected: -1
    readonly property var suggestion: selected >= 0 && selected < App.moveSuggestions.length ? App.moveSuggestions[selected] : ({})
    onOpened: { selected = -1; filter.text = "" }
    contentItem: ColumnLayout {
        spacing: 10
        AppText { text: "Review evidence before updating a Project location. No repository files are moved."; color: Design.Theme.textSecondary; wrapMode: Text.WordWrap; Layout.fillWidth: true }
        RowLayout {
            AppButton { text: "Scan issues"; primary: tabs.currentIndex === 0; onClicked: tabs.currentIndex = 0 }
            AppButton { text: "Possible moves (" + App.moveSuggestions.length + ")"; primary: tabs.currentIndex === 1; onClicked: tabs.currentIndex = 1 }
        }
        StackLayout {
            id: tabs; Layout.fillWidth: true; Layout.fillHeight: true
            CodeView { text: App.scanIssues }
            RowLayout {
                spacing: 10
                ColumnLayout {
                    Layout.preferredWidth: 290; Layout.fillHeight: true
                    UI.SearchField { id: filter; placeholderText: "Filter moves…"; Layout.fillWidth: true }
                    ScrollView {
                        Layout.fillWidth: true; Layout.fillHeight: true; contentWidth: availableWidth; clip: true
                        ColumnLayout {
                            width: parent.width
                            Repeater {
                                model: App.moveSuggestions
                                AppButton {
                                    required property var modelData
                                    required property int index
                                    Layout.fillWidth: true
                                    visible: JSON.stringify(modelData).toLowerCase().indexOf(filter.text.toLowerCase()) >= 0
                                    text: (modelData.name || "Project") + " · " + (modelData.category || "possible")
                                    primary: dialog.selected === index
                                    onClicked: { dialog.selected = index; candidate.currentIndex = 0 }
                                }
                            }
                        }
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    CodeView {
                        Layout.fillWidth: true; Layout.fillHeight: true
                        text: dialog.selected < 0 ? "Select a suggestion to inspect its locations and evidence."
                            : "Previous locations:\n" + (dialog.suggestion.old_paths || [dialog.suggestion.old_path || ""]).join("\n")
                            + "\n\nCandidate locations:\n" + (dialog.suggestion.new_paths || [dialog.suggestion.new_path || ""]).join("\n")
                            + "\n\nEvidence:\n" + (dialog.suggestion.evidence || []).join("\n")
                    }
                    AppCombo {
                        id: candidate; Layout.fillWidth: true
                        visible: dialog.suggestion.category === "ambiguous"
                        model: (dialog.suggestion.candidates || []).map(function(item) { return item.old_path + " → " + item.new_path })
                    }
                    RowLayout {
                        AppButton { text: "Update location…"; primary: true; enabled: dialog.selected >= 0 && !App.scanning && !App.registryBlocked; onClicked: App.previewMove(dialog.selected, candidate.currentIndex) }
                        AppButton { text: "Keep both…"; enabled: dialog.selected >= 0 && !App.scanning && !App.registryBlocked; onClicked: App.requestKeepBoth(dialog.selected) }
                    }
                }
            }
        }
        AppText { text: "Later leaves suggestions unresolved. Keep both durably suppresses these exact pairings."; color: Design.Theme.textMuted; wrapMode: Text.WordWrap; Layout.fillWidth: true }
        AppButton { text: "Later / Close"; Layout.alignment: Qt.AlignRight; onClicked: dialog.accept() }
    }
}
