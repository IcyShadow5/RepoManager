import QtQuick
import QtQuick.Controls
import "../design" as Design

ComboBox {
    id: control
    implicitHeight: 31
    implicitWidth: 130
    leftPadding: 9; rightPadding: 28
    font.family: Design.Theme.fontFamily
    font.pixelSize: 12
    background: Rectangle {
        radius: 4
        color: control.hovered ? Design.Theme.elevated : Design.Theme.background
        border.color: control.activeFocus ? Design.Theme.focus : Design.Theme.border
    }
    contentItem: AppText {
        text: control.displayText
        color: control.enabled ? Design.Theme.textPrimary : Design.Theme.textMuted
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        font.pixelSize: 12
    }
    indicator: Icon {
        name: "chevron-right"; rotation: 90
        width: 14; height: 14
        x: control.width - width - 9
        y: (control.height - height) / 2
        tint: Design.Theme.textSecondary
    }
    delegate: ItemDelegate {
        width: control.width
        contentItem: AppText { text: modelData; font.pixelSize: 12 }
        background: Rectangle { color: highlighted ? Design.Theme.selected : Design.Theme.elevated }
        highlighted: control.highlightedIndex === index
    }
    popup: Popup {
        y: control.height + 3
        width: control.width
        implicitHeight: Math.min(contentItem.implicitHeight + 2, 260)
        padding: 1
        background: Rectangle { color: Design.Theme.elevated; border.color: Design.Theme.border; radius: 4 }
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            ScrollIndicator.vertical: ScrollIndicator {}
        }
    }
}
