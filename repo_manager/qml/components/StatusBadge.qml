import QtQuick
import "../design" as Design

Rectangle {
    property string label: ""
    property color accent: Design.Theme.info
    implicitWidth: badgeText.implicitWidth + 20
    implicitHeight: 24
    radius: 4
    color: Qt.rgba(accent.r, accent.g, accent.b, 0.10)
    border.color: Qt.rgba(accent.r, accent.g, accent.b, 0.50)
    Text {
        id: badgeText
        anchors.centerIn: parent
        text: parent.label
        color: parent.accent
        font.family: Design.Theme.fontFamily
        font.pixelSize: 11
        font.weight: Font.Medium
    }
}
