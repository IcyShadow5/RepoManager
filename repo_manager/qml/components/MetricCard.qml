import QtQuick
import QtQuick.Layouts
import "../design" as Design

Rectangle {
    id: metric
    property string label: ""
    property string value: "0"
    property string iconName: "database"
    property color accent: Design.Theme.icy
    implicitHeight: 66
    radius: 6
    color: Design.Theme.surface
    border.color: Design.Theme.border
    RowLayout {
        anchors.fill: parent
        anchors.margins: 13
        spacing: 12
        Rectangle {
            Layout.preferredWidth: 34; Layout.preferredHeight: 34; radius: 6
            color: Qt.rgba(metric.accent.r, metric.accent.g, metric.accent.b, 0.09)
            Icon { anchors.centerIn: parent; name: metric.iconName; tint: metric.accent; width: 22; height: 22 }
        }
        ColumnLayout {
            spacing: 0
            Layout.fillWidth: true
            AppText { text: metric.label; color: Design.Theme.textSecondary; font.pixelSize: 12; Layout.fillWidth: true }
            AppText { text: metric.value; font.pixelSize: 23; font.weight: Font.DemiBold; Layout.fillWidth: true }
        }
    }
}
