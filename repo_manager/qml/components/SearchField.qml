import QtQuick
import QtQuick.Controls
import "../design" as Design

TextField {
    id: field
    implicitHeight: 38
    color: Design.Theme.textPrimary
    selectionColor: Design.Theme.selected
    selectedTextColor: Design.Theme.textPrimary
    placeholderTextColor: Design.Theme.textMuted
    font.family: Design.Theme.fontFamily
    font.pixelSize: 13
    leftPadding: 34
    rightPadding: 12
    background: Rectangle {
        radius: 5
        color: Design.Theme.surface
        border.color: field.activeFocus ? Design.Theme.icy : Design.Theme.border
        border.width: field.activeFocus ? 2 : 1
        Icon {
            name: "search"
            width: 18
            height: 18
            anchors.left: parent.left
            anchors.leftMargin: 10
            anchors.verticalCenter: parent.verticalCenter
        }
    }
}
