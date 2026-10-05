import QtQuick
import QtQuick.Controls
import "../design" as Design

CheckBox {
    id: control
    font.family: Design.Theme.fontFamily
    indicator: Rectangle {
        x: 0; y: (control.height-height)/2
        width: 16; height: 16; radius: 3
        color: control.checked ? Design.Theme.blue : Design.Theme.background
        border.color: control.activeFocus ? Design.Theme.focus : Design.Theme.border
        Icon { visible: control.checked; anchors.fill: parent; anchors.margins: 1; name: "check"; tint: Design.Theme.textPrimary }
    }
    contentItem: AppText {
        text: control.text; leftPadding: 23
        color: control.enabled ? Design.Theme.textPrimary : Design.Theme.textMuted
        verticalAlignment: Text.AlignVCenter; font.pixelSize: 12
    }
}
