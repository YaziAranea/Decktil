import QtQuick
import "layouts/fallback"

Window {
    visible: true
    width: 240
    height: 120
    TextInput { id: field; text: "привет hello"; focus: true; Component.onCompleted: select(2, 7) }
    Item {
        // Model the native keyboard retaining its owner while layout pages are destroyed.
        property alias keyboardLayoutLoader: page
        property var style: null
        Loader {
            id: page
            sourceComponent: Item {
                id: layout
                property QtObject skin: DeckAppearance { layout: layout }
            }
        }
    }
    readonly property var skin: page.item ? page.item.skin : null
    property var firstPalette: null
    property bool initialized: false
    Timer {
        interval: 80
        running: true
        repeat: true
        onTriggered: {
            if (!skin || !skin.palette) return
            if (!initialized && skin.palette.preset === "lagoon") {
                initialized = true
                firstPalette = skin.palette
                page.active = false
                page.active = true
                console.warn("Decktil palette loaded")
                return
            }
            if (!initialized || skin.palette.preset !== "custom") return
            const correct = skin.palette === firstPalette && skin.palette.horizontal && String(skin.palette.backgroundEnd) === "#432138"
                && String(skin.palette.keyStart) === "#195858" && String(skin.textColor) === "#f9ffff"
                && String(skin.accentColor) === "#ffdb60" && String(skin.accentTextColor) === "#000000"
                && field.text === "привет hello" && field.selectionStart === 2 && field.selectionEnd === 7 && field.activeFocus
            Qt.exit(correct ? 0 : 2)
        }
    }
}
