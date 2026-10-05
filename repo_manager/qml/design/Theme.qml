pragma Singleton
import QtQuick
import RepoManager 1.0

QtObject {
    readonly property bool light: App.themeName === "light"
    readonly property color background: light ? "#eaf1f7" : "#07131f"
    readonly property color sidebar: light ? "#dce9f3" : "#091c2d"
    readonly property color surface: light ? "#f8fbfe" : "#0c2133"
    readonly property color elevated: light ? "#e1edf6" : "#112b40"
    readonly property color rowAlternate: light ? "#eef5fa" : "#0e2639"
    readonly property color border: light ? "#a9c1d3" : "#20445e"
    readonly property color divider: light ? "#c6d8e5" : "#183a52"
    readonly property color textPrimary: light ? "#142c40" : "#f2f8fe"
    readonly property color textSecondary: light ? "#385a74" : "#aac5d8"
    readonly property color textMuted: light ? "#58758b" : "#718fa6"
    readonly property color icy: light ? "#006e99" : "#35caff"
    readonly property color blue: light ? "#0874b5" : "#117fc6"
    readonly property color blueHover: light ? "#07689f" : "#149fea"
    readonly property color accentPressed: light ? "#075e95" : "#1266a0"
    readonly property color selected: light ? "#c2e7f9" : "#1266a0"
    readonly property color navSelected: light ? "#c9e5f4" : "#123c59"
    readonly property color navBorder: light ? "#78a9c6" : "#1d678f"
    readonly property color onAccent: "#f2f8fe"
    readonly property color success: light ? "#087c5c" : "#22dca0"
    readonly property color warning: light ? "#916000" : "#f0b74f"
    readonly property color error: light ? "#be2941" : "#ff6c79"
    readonly property color info: light ? "#176f9c" : "#6dbcf1"
    readonly property color focus: light ? "#006e99" : "#6cdaff"
    readonly property string fontFamily: "Segoe UI"
    readonly property string monoFamily: "Cascadia Mono"
    readonly property int titleSize: 23
    readonly property int sectionSize: 15
    readonly property int bodySize: 13
    readonly property int metaSize: 12
    readonly property int tableSize: 12
    readonly property int rowHeight: 34
}
