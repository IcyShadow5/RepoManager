import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import RepoManager 1.0
import "../design" as Design

AppDialog {
    id: dialog
    objectName: "feedbackDialog"
    title: "Feedback & bug reports"
    property string category: "bug"
    contentItem: ColumnLayout {
        spacing: 12
        AppText { text: "What would you like to share?"; font.pixelSize: 15; font.weight: Font.DemiBold }
        GridLayout {
            Layout.fillWidth: true
            columns: 2
            columnSpacing: 8; rowSpacing: 8
            Repeater {
                model: [
                    {key: "positive", label: "Something works well", icon: "check"},
                    {key: "improvement", label: "Suggest an improvement", icon: "focus-2"},
                    {key: "bug", label: "Report a bug", icon: "alert-triangle"},
                    {key: "ui", label: "Report a UI issue", icon: "device-desktop"}
                ]
                AppButton {
                    required property var modelData
                    objectName: "feedbackCategory" + modelData.key
                    text: modelData.label; iconName: modelData.icon
                    primary: dialog.category === modelData.key
                    Layout.fillWidth: true
                    onClicked: dialog.category = modelData.key
                }
            }
        }
        AppField { id: subject; objectName: "feedbackSubject"; Layout.fillWidth: true; placeholderText: "Short title (optional)"; maximumLength: 140 }
        TextArea {
            id: message
            objectName: "feedbackMessage"
            Layout.fillWidth: true; Layout.preferredHeight: 175
            placeholderText: dialog.category === "positive" ? "What worked well?" : dialog.category === "improvement" ? "What would help you work better?" : "What happened? What did you expect? How can it be reproduced?"
            color: Design.Theme.textPrimary; placeholderTextColor: Design.Theme.textMuted
            selectionColor: Design.Theme.selected
            font.family: Design.Theme.fontFamily; font.pixelSize: 13
            wrapMode: TextEdit.Wrap
            background: Rectangle { color: Design.Theme.background; border.color: message.activeFocus ? Design.Theme.focus : Design.Theme.border; radius: 4 }
        }
        CheckBox {
            id: runtime
            text: "Include Python, Qt and operating-system name"
            indicator: Rectangle {
                x: 0; y: (parent.height-height)/2; width: 16; height: 16; radius: 3
                color: runtime.checked ? Design.Theme.blue : Design.Theme.background
                border.color: runtime.activeFocus ? Design.Theme.focus : Design.Theme.border
                Icon { visible: runtime.checked; anchors.fill: parent; anchors.margins: 1; name: "check"; tint: Design.Theme.onAccent }
            }
            contentItem: AppText { text: runtime.text; leftPadding: 23; verticalAlignment: Text.AlignVCenter; font.pixelSize: 12 }
        }
        AppText {
            text: "Save locally or open a draft in the public issue tracker. Review and submit it there. Repository data and local paths are not added automatically."
            color: Design.Theme.textSecondary; font.pixelSize: 12
            wrapMode: Text.WordWrap; Layout.fillWidth: true
        }
        AppText { text: App.feedbackStatus; visible: text !== ""; color: Design.Theme.icy; font.pixelSize: 12; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true }
        RowLayout {
            Layout.fillWidth: true
            AppButton { objectName: "feedbackSave"; text: "Save locally"; compact: true; iconName: "check"; onClicked: App.sendFeedback(dialog.category, subject.text, message.text, runtime.checked, "save") }
            AppButton { objectName: "feedbackCopy"; text: "Copy"; compact: true; iconName: "copy"; onClicked: App.sendFeedback(dialog.category, subject.text, message.text, runtime.checked, "copy") }
            Item { Layout.fillWidth: true }
            AppButton { objectName: "feedbackDraft"; text: "Open issue draft"; primary: true; iconName: "chevron-right"; onClicked: App.sendFeedback(dialog.category, subject.text, message.text, runtime.checked, "issue") }
        }
    }
}
