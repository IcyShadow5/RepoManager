import QtQuick
import QtQuick.Layouts
import "../design" as Design

RowLayout {
    property string label: ""
    property string value: ""
    spacing: 8
    AppText {
        text: parent.label
        color: Design.Theme.textMuted
        font.family: Design.Theme.fontFamily
        font.pixelSize: 12
        Layout.preferredWidth: 92
    }
    AppText {
        text: parent.value
        color: Design.Theme.textSecondary
        font.family: Design.Theme.fontFamily
        font.pixelSize: 12
        elide: Text.ElideRight
        Layout.fillWidth: true
    }
}
