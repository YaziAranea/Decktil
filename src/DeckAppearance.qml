pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.VirtualKeyboard
import "../DecktilStyle" as SharedStyle

QtObject {
    id: skin
    required property Item layout
    property var keyboard: null
    readonly property var palette: SharedStyle.Palette
    readonly property bool presented: !!layout && layout.visible && !!layout.Window.window && layout.Window.window.visible
    readonly property bool usingSkin: !!palette && palette.usingSkin
    readonly property color textColor: palette ? palette.textColor : systemColors.text
    readonly property color accentColor: palette ? palette.accentColor : systemColors.highlight
    readonly property color accentTextColor: palette ? palette.accentTextColor : systemColors.highlightedText
    readonly property color popupColor: palette ? palette.popupColor : systemColors.window
    property SystemPalette systemColors: SystemPalette {}
    property Item backdrop: null
    property bool decorated: false
    function translucent(color, alpha) { return Qt.rgba(color.r, color.g, color.b, alpha) }
    function findKeyboard() {
        for (let item = layout; item; item = item.parent) {
            if (item.keyboardLayoutLoader !== undefined && item.style !== undefined) return item
        }
        return null
    }
    // BaseKey delivers input and Breeze owns the text, icons, margins and focus states.
    // Attach paint only to the backing item inside its existing key panel.
    function decorateKeys(item) {
        let count = 0
        if (item.keyPanelDelegate !== undefined) {
            for (let loader of item.children) {
                const panel = loader.item
                if (!panel || !panel.background || panel.control !== item) continue
                if (!panel.background.children.some(child => child.objectName === "decktilKeyGradient")) {
                    keyPaint.createObject(panel.background, {keyControl: item})
                    tintIcons(panel)
                }
                ++count
            }
        } else {
            for (let child of item.children) count += decorateKeys(child)
        }
        return count
    }
    function tintIcons(item) {
        if (item.isMask !== undefined && item.color !== undefined && item.source !== undefined) {
            glyphPaint.createObject(item, {glyph: item})
            return
        }
        for (let child of item.children) tintIcons(child)
    }
    function decorate() {
        keyboard = findKeyboard()
        if (!keyboard || !keyboard.style || !usingSkin) return
        if (!backdrop) {
            for (let child of keyboard.children) {
                if (child.sourceComponent === keyboard.style.keyboardBackground && child.item) {
                    backdrop = panelPaint.createObject(child.item)
                    break
                }
            }
        }
        decorated = decorateKeys(layout) > 0 && !!backdrop
    }

    property Timer waitForPanels: Timer {
        running: skin.presented && skin.usingSkin && !skin.decorated
        interval: 250
        repeat: true
        onTriggered: skin.decorate()
    }
    onUsingSkinChanged: if (usingSkin) Qt.callLater(decorate)
    onPresentedChanged: if (presented) Qt.callLater(refresh)
    function refresh() {
        keyboard = findKeyboard()
        palette.keyboard = keyboard || layout
        palette.reload()
        if (usingSkin) Qt.callLater(decorate)
    }
    Component.onCompleted: Qt.callLater(refresh)
    Component.onDestruction: if (backdrop) backdrop.destroy()

    property Component panelPaint: Component {
        Rectangle {
            objectName: "decktilPanelGradient"
            anchors.fill: parent
            visible: skin.usingSkin && skin.layout.visible
            gradient: Gradient {
                orientation: skin.palette.horizontal ? Gradient.Horizontal : Gradient.Vertical
                GradientStop { position: 0; color: skin.palette.backgroundStart }
                GradientStop { position: 1; color: skin.palette.backgroundEnd }
            }
        }
    }
    property Component keyPaint: Component {
        Rectangle {
            id: paint
            objectName: "decktilKeyGradient"
            required property Item keyControl
            readonly property bool indicated: keyControl.pressed
                                               || (keyControl.key === Qt.Key_Shift && InputContext.capsLockActive)
            anchors.fill: parent
            visible: skin.usingSkin
            radius: parent ? parent.radius : 0
            border.width: indicated ? 2 : 0
            border.color: skin.accentColor
            gradient: Gradient {
                orientation: skin.palette.horizontal ? Gradient.Horizontal : Gradient.Vertical
                GradientStop { position: 0; color: paint.indicated ? Qt.tint(skin.palette.keyStart, skin.translucent(skin.accentColor, 0.30)) : skin.palette.keyStart }
                GradientStop { position: 1; color: paint.indicated ? Qt.tint(skin.palette.keyEnd, skin.translucent(skin.accentColor, 0.30)) : skin.palette.keyEnd }
            }
        }
    }
    property Component glyphPaint: Component {
        QtObject {
            id: glyphStyle
            required property Item glyph
            property Binding tint: Binding { target: glyphStyle.glyph; property: "color"; value: skin.textColor; when: skin.usingSkin }
            property Binding mask: Binding { target: glyphStyle.glyph; property: "isMask"; value: true; when: skin.usingSkin }
        }
    }

}
