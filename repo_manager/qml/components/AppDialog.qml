import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../design" as Design

Dialog {
    id: dialog
    parent: Overlay.overlay
    anchors.centerIn: parent
    modal: true
    width: Math.min(620, parent.width - 40)
    padding: 18
    closePolicy: Popup.CloseOnEscape
    font.family: Design.Theme.fontFamily
    background: Rectangle { color: Design.Theme.surface; radius: 8; border.color: Design.Theme.border }
    Overlay.modal: Rectangle { color: "#aa020a12" }
    header: Item {
        implicitHeight: 56
        RowLayout {
            anchors.fill: parent; anchors.leftMargin: 18; anchors.rightMargin: 12
            AppText { text: dialog.title; font.pixelSize: 19; font.weight: Font.DemiBold; Layout.fillWidth: true }
            AppButton { compact: true; iconName: "x"; tip: "Close dialog"; onClicked: dialog.reject() }
        }
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Design.Theme.divider }
    }
}
