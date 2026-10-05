import QtQuick
import QtQuick.Controls
import "../design" as Design

TextField {
    id: field
    implicitHeight: 31
    color: Design.Theme.textPrimary
    selectionColor: Design.Theme.selected
    selectedTextColor: Design.Theme.textPrimary
    placeholderTextColor: Design.Theme.textMuted
    font.family: Design.Theme.fontFamily
    font.pixelSize: 12
    padding: 7
    background: Rectangle {
        radius: 4
        color: Design.Theme.background
        border.color: field.activeFocus ? Design.Theme.focus : Design.Theme.border
    }
}
