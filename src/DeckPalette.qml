pragma Singleton
pragma ComponentBehavior: Bound
import QtQuick
import QtCore

// One owner per keyboard. Locale and symbol pages may overlap while the layout loader
// switches them; stacking independent Bindings would restore an obsolete custom palette.
QtObject {
    id: skin
    property Item keyboard: null
    readonly property bool presented: !!keyboard && keyboard.visible && !!keyboard.Window.window && keyboard.Window.window.visible
    property string preset: "default"
    property color backgroundStart: "#31363B"
    property color backgroundEnd: "#31363B"
    property color keyStart: "#454B50"
    property color keyEnd: "#454B50"
    property color ink: "#FCFCFC"
    property color highlight: "#3DAEE9"
    property bool horizontal: false
    readonly property bool usingSkin: preset !== "default"
    readonly property var theme: keyboard && keyboard.style ? keyboard.style.theme || null : null
    readonly property color textColor: usingSkin ? ink : (theme ? theme.keyTextColor : systemColors.text)
    readonly property color accentColor: usingSkin ? highlight : systemColors.highlight
    readonly property color popupColor: translucent(usingSkin ? backgroundEnd : (theme ? theme.secondaryColor : systemColors.window), 0.92)
    readonly property color accentTextColor: luminance(accentColor) > 0.179 ? "#000000" : "#FFFFFF"
    property SystemPalette systemColors: SystemPalette {}

    property Settings settings: Settings {
        location: StandardPaths.writableLocation(StandardPaths.GenericConfigLocation) + "/decktil/appearance.ini"
        category: "Appearance"
    }
    function reload() {
        settings.sync()
        preset = settings.value("preset", "default")
        if (usingSkin) {
            backgroundStart = settings.value("backgroundStart", "#31363B")
            backgroundEnd = settings.value("backgroundEnd", "#31363B")
            keyStart = settings.value("keyStart", "#454B50")
            keyEnd = settings.value("keyEnd", "#454B50")
            ink = settings.value("text", "#FCFCFC")
            highlight = settings.value("accent", "#3DAEE9")
            horizontal = settings.value("direction", "vertical") === "horizontal"
        }
    }
    function translucent(color, alpha) {
        return Qt.rgba(color.r, color.g, color.b, alpha)
    }
    function luminance(color) {
        function linear(value) { return value <= 0.04045 ? value / 12.92 : Math.pow((value + 0.055) / 1.055, 2.4) }
        return 0.2126 * linear(color.r) + 0.7152 * linear(color.g) + 0.0722 * linear(color.b)
    }
    property Timer settingsPoll: Timer {
        running: skin.presented
        repeat: true
        interval: 750
        onTriggered: skin.reload()
    }
    onPresentedChanged: if (presented) Qt.callLater(reload)
    Component.onCompleted: Qt.callLater(reload)
    // Breeze uses these roles for native glyphs and its alternate-character picker.
    // When the page or custom palette goes away, Binding restores the original KDE bindings.
    readonly property bool hasTheme: !!theme
    readonly property bool colorsActive: hasTheme && usingSkin
    property Binding keyUnderlay: Binding { target: skin.theme; property: "normalKeyBackgroundColor"; value: skin.keyStart; when: skin.colorsActive }
    property Binding functionUnderlay: Binding { target: skin.theme; property: "highlightedKeyBackgroundColor"; value: skin.keyStart; when: skin.colorsActive }
    property Binding pressedUnderlay: Binding { target: skin.theme; property: "normalKeyPressedBackgroundColor"; value: skin.keyStart; when: skin.colorsActive }
    property Binding mainText: Binding { target: skin.theme; property: "keyTextColor"; value: skin.ink; when: skin.colorsActive }
    property Binding smallText: Binding { target: skin.theme; property: "keySmallTextColor"; value: skin.ink; when: skin.colorsActive }
    property Binding popupSurface: Binding { target: skin.theme; property: "popupBackgroundColor"; value: skin.popupColor; when: skin.hasTheme }
    property Binding popupInk: Binding { target: skin.theme; property: "popupTextColor"; value: skin.ink; when: skin.colorsActive }
    property Binding popupSelectedInk: Binding { target: skin.theme; property: "popupTextSelectedColor"; value: skin.ink; when: skin.colorsActive }
    property Binding popupBorder: Binding { target: skin.theme; property: "popupBorderColor"; value: skin.accentColor; when: skin.colorsActive }
    property Binding popupHighlight: Binding { target: skin.theme; property: "popupHighlightColor"; value: skin.translucent(skin.accentColor, 0.28); when: skin.colorsActive }
    property Binding popupHighlightBorder: Binding { target: skin.theme; property: "popupHighlightBorderColor"; value: skin.accentColor; when: skin.colorsActive }
    property Binding indicator: Binding { target: skin.theme; property: "modeKeyAccentColor"; value: skin.accentColor; when: skin.colorsActive }
}
