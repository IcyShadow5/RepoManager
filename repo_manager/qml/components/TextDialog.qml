import QtQuick
import QtQuick.Layouts
import RepoManager 1.0
import "../design" as Design

AppDialog {
    id: dialog
    property string text: ""
    width: Math.min(900, parent.width - 40)
    height: Math.min(640, parent.height - 40)
    contentItem: ColumnLayout {
        CodeView { text: dialog.text; Layout.fillWidth: true; Layout.fillHeight: true }
        RowLayout {
            Layout.fillWidth: true
            AppButton { text: "Copy details"; iconName: "copy"; onClicked: App.copyText(dialog.text) }
            Item { Layout.fillWidth: true }
            AppButton { text: "Close"; onClicked: dialog.accept() }
        }
    }
}
