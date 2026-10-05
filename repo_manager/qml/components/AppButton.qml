import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../design" as Design

Button {
    id: control
    property bool primary: false
    property bool compact: false
    property string iconName: ""
    property string tip: ""
    implicitHeight: compact ? 30 : 36
    implicitWidth: Math.max(compact ? 30 : 80, content.implicitWidth + 24)
    padding: 8
    focusPolicy: Qt.StrongFocus
    Keys.onReturnPressed: function(event) { if (enabled && !event.isAutoRepeat) clicked() }
    Keys.onEnterPressed: function(event) { if (enabled && !event.isAutoRepeat) clicked() }
    background: Rectangle {
        radius: 5
        color: !control.enabled ? Design.Theme.surface
             : control.down ? (control.primary ? Design.Theme.accentPressed : Design.Theme.selected)
             : control.primary ? (control.hovered ? Design.Theme.blueHover : Design.Theme.blue)
             : control.hovered ? Design.Theme.elevated : Design.Theme.surface
        border.color: control.activeFocus ? Design.Theme.focus
                    : control.primary ? "#289edb" : Design.Theme.border
        border.width: control.activeFocus ? 2 : 1
    }
    contentItem: Item {
        id: content
        implicitWidth: buttonContents.implicitWidth
        implicitHeight: buttonContents.implicitHeight
        RowLayout {
            id: buttonContents
            anchors.centerIn: parent
            spacing: control.text && control.iconName ? 7 : 0
            Icon {
                visible: control.iconName !== ""
                name: control.iconName || "folder"
                tint: !control.enabled ? Design.Theme.textMuted : control.primary ? Design.Theme.onAccent : Design.Theme.textPrimary
                Layout.preferredWidth: 18
                Layout.preferredHeight: 18
                Layout.alignment: Qt.AlignVCenter
            }
            AppText {
                visible: control.text !== ""
                text: control.text
                color: !control.enabled ? Design.Theme.textMuted : control.primary ? Design.Theme.onAccent : Design.Theme.textPrimary
                font.pixelSize: control.compact ? 12 : 13
                font.weight: control.primary ? Font.DemiBold : Font.Normal
                horizontalAlignment: Text.AlignHCenter
            }
        }
    }
    ToolTip.visible: hovered && tip !== ""
    ToolTip.text: tip
    ToolTip.delay: 650
}
