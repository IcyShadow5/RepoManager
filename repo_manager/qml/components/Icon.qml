import QtQuick
import QtQuick.Window
import "../design" as Design

Item {
    id: icon
    property string name: "folder"
    property color tint: Design.Theme.icy
    implicitWidth: 20
    implicitHeight: 20
    width: 20
    height: 20
    Image {
        anchors.fill: parent
        source: "image://icons/" + icon.name + "/" + icon.tint.toString().slice(1)
        sourceSize.width: icon.width * Screen.devicePixelRatio
        sourceSize.height: icon.height * Screen.devicePixelRatio
        fillMode: Image.PreserveAspectFit
        smooth: true
    }
}
