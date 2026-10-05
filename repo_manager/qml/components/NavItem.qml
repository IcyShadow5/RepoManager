import QtQuick
import QtQuick.Controls
import "../design" as Design

AbstractButton {
    id: nav
    property string iconName: "database"
    property string label: ""
    property bool selected: false
    signal activated()
    implicitHeight: 40
    focusPolicy: Qt.StrongFocus
    Keys.onReturnPressed: function(event) { if (enabled && !event.isAutoRepeat) clicked() }
    Keys.onEnterPressed: function(event) { if (enabled && !event.isAutoRepeat) clicked() }
    onClicked: activated()
    background: Rectangle {
        radius: 5
        color: nav.selected ? Design.Theme.navSelected : nav.hovered ? Design.Theme.elevated : "transparent"
        border.color: nav.activeFocus ? Design.Theme.focus : nav.selected ? Design.Theme.navBorder : "transparent"
        Rectangle {
            visible: nav.selected
            width: 3; radius: 2
            color: Design.Theme.icy
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.topMargin: 10; anchors.bottomMargin: 10
        }
    }
    contentItem: Item {
        Icon {
            name: nav.iconName
            tint: nav.selected ? Design.Theme.icy : Design.Theme.textSecondary
            width: 20; height: 20
            x: 13; anchors.verticalCenter: parent.verticalCenter
        }
        AppText {
            text: nav.label
            x: 43; anchors.verticalCenter: parent.verticalCenter
            color: nav.selected ? Design.Theme.textPrimary : Design.Theme.textSecondary
            font.weight: nav.selected ? Font.DemiBold : Font.Normal
        }
    }
}
