import QtQuick
import QtQuick.Controls
import "../design" as Design

Rectangle {
    id: view
    property alias text: content.text
    signal lineSelected(string line)
    color: Design.Theme.background
    border.color: Design.Theme.border
    radius: 5
    ScrollView {
        anchors.fill: parent; anchors.margins: 1
        clip: true
        TextArea {
            id: content
            readOnly: true; selectByMouse: true
            wrapMode: TextEdit.NoWrap
            color: Design.Theme.textSecondary
            selectionColor: Design.Theme.selected
            font.family: Design.Theme.monoFamily; font.pixelSize: 12
            padding: 10
            background: Item {}
            onCursorPositionChanged: {
                const start = text.lastIndexOf("\n", Math.max(0, cursorPosition - 1)) + 1
                const end = text.indexOf("\n", cursorPosition)
                view.lineSelected(text.slice(start, end < 0 ? text.length : end).trim())
            }
        }
    }
}
