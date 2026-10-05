import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import RepoManager 1.0
import "../design" as Design

AppDialog {
    id: dialog
    property var launcher: ({})
    property string error: ""
    title: launcher.launcher_id ? "Edit Custom Launcher" : "Add Custom Launcher"
    function edit(index) {
        launcher = App.editLauncher(index)
        name.text = launcher.name || ""
        executable.text = launcher.executable || ""
        arguments.text = (launcher.args || []).join("\n")
        cwd.text = launcher.cwd || ""
        error = ""
        open()
    }
    function save(remove) {
        let value = {launcher_id: launcher.launcher_id || "", name: name.text,
            executable: executable.text, args: arguments.text ? arguments.text.split("\n") : [], cwd: cwd.text}
        if (App.saveLauncher(value, remove)) accept()
        else error = App.statusText
    }
    contentItem: ColumnLayout {
        spacing: 9
        AppText { text: "Name"; color: Design.Theme.textSecondary }
        AppField { id: name; Layout.fillWidth: true }
        AppText { text: "Executable (path or command on PATH)"; color: Design.Theme.textSecondary }
        AppField { id: executable; Layout.fillWidth: true }
        AppText { text: "Arguments — one literal argument per line"; color: Design.Theme.textSecondary }
        TextArea {
            id: arguments; Layout.fillWidth: true; Layout.preferredHeight: 100
            font.family: Design.Theme.monoFamily; font.pixelSize: 12; color: Design.Theme.textPrimary
            selectByMouse: true; selectionColor: Design.Theme.selected
            background: Rectangle { color: Design.Theme.background; radius: 4; border.color: arguments.activeFocus ? Design.Theme.focus : Design.Theme.border }
        }
        AppText { text: "Shell syntax is not parsed. Running a launcher executes project code."; color: Design.Theme.textMuted; wrapMode: Text.WordWrap; Layout.fillWidth: true }
        AppText { text: "Working directory"; color: Design.Theme.textSecondary }
        AppField { id: cwd; Layout.fillWidth: true }
        AppText { text: dialog.error; visible: text !== ""; color: Design.Theme.error; wrapMode: Text.WordWrap; Layout.fillWidth: true }
        RowLayout {
            Layout.fillWidth: true
            AppButton { text: "Remove…"; visible: !!dialog.launcher.launcher_id; onClicked: removeConfirm.open() }
            Item { Layout.fillWidth: true }
            AppButton { text: "Cancel"; onClicked: dialog.reject() }
            AppButton { text: "Save"; primary: true; onClicked: dialog.save(false) }
        }
    }
    AppDialog {
        id: removeConfirm; title: "Remove Custom Launcher"
        contentItem: ColumnLayout {
            AppText { text: "Remove this saved launcher? Repository files stay in place."; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                AppButton { text: "Cancel"; onClicked: removeConfirm.reject() }
                AppButton { text: "Remove"; primary: true; onClicked: { removeConfirm.accept(); dialog.save(true) } }
            }
        }
    }
}
