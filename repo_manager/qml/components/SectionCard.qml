import QtQuick
import QtQuick.Layouts
import "../design" as Design

Rectangle {
    id: card
    property string title: ""
    property string iconName: "info-circle"
    default property alias content: body.data
    implicitHeight: contentColumn.implicitHeight + 16
    radius: 5
    color: Design.Theme.background
    border.color: Design.Theme.border
    data: [ColumnLayout {
        id: contentColumn
        anchors.left: parent.left; anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 8
        spacing: 5
        RowLayout {
            Layout.fillWidth: true
            spacing: 7
            Icon { name: card.iconName; Layout.preferredWidth: 18; Layout.preferredHeight: 18 }
            AppText { text: card.title; font.weight: Font.DemiBold; Layout.fillWidth: true }
        }
        Rectangle { height: 1; color: Design.Theme.divider; Layout.fillWidth: true }
        ColumnLayout { id: body; spacing: 4; Layout.fillWidth: true }
    }]
}
