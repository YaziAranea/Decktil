import QtQuick
import QtQuick.Window
import QtQuick.VirtualKeyboard
import QtQuick.VirtualKeyboard.Settings
import "../../src/DeckLearning.js" as Learning
import "../../src/DeckPrediction.js" as Prediction

Window {
    id: window
    title: "Decktil Gaming Keyboard"
    visible: false
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus | Qt.X11BypassWindowManagerHint
    color: "transparent"
    width: Screen.width
    height: Screen.height
    x: 0
    y: 0

    property bool coveringNative: false
    readonly property int panelHeight: Math.ceil(panel.height)
    // Steam's inline animation grows to 240 CSS pixels. The Deck's
    // 1280-wide viewport fits about 854 CSS pixels at its default UI scale.
    readonly property int surfaceHeight: coveringNative
        ? Math.min(height, Math.ceil(Math.max(panelHeight, height / 2, width * 240 / 854)))
        : panelHeight
    onCoveringNativeChanged: {
        if (coveringNative) {
            touch.side = 0
            if (selector)
                selector.updateFrame(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        }
    }
    Rectangle {
        anchors.bottom: parent.bottom
        width: parent.width
        height: window.surfaceHeight
        visible: window.coveringNative
        readonly property color tint: panel.keyboard.style?.theme?.keyboardBackgroundColor ?? "#31363b"
        color: Qt.rgba(tint.r, tint.g, tint.b, 1)
    }
    InputPanel {
        id: panel
        anchors.bottom: parent.bottom
        width: parent.width
        active: true
        enabled: !window.coveringNative
        externalLanguageSwitchEnabled: true
        onExternalLanguageSwitch: (locales, index) => {
            if (locales.length > 1) {
                const next = locales[(index + 1) % locales.length]
                if (!decktilGamePanel.requestLocale(next))
                    VirtualKeyboardSettings.locale = next
            }
        }
    }
    readonly property var selector: {
        let current = panel.keyboard.keyboardLayoutLoader.item
        while (current && !current.deckSelector)
            current = current.item
        return current ? current.deckSelector : null
    }
    Component.onCompleted: {
        VirtualKeyboardSettings.activeLocales = ["ru_RU", "en_US"]
        VirtualKeyboardSettings.locale = decktilGamePanel.initialLocale
    }

    property var learningState: null

    function lookupPersonal() {
        const locale = VirtualKeyboardSettings.locale
        const text = decktilGamePanel.predictionText
        const ctx = decktilGamePanel.personalGeneration && text
            ? Prediction.context(text, text.length, locale) : null
        const prev = ctx ? ctx.previous : []
        decktilGamePanel.lookup(ctx ? locale : "", prev[prev.length - 2] || "",
                                prev[prev.length - 1] || "", ctx ? ctx.prefix : "")
    }

    function learnEdit(edit) {
        const generation = decktilGamePanel.personalGeneration
        // R1 (Space) is delivered by the supervisor and can only finish an
        // existing sequence. All other edits originate in this panel session.
        if (!generation || (edit.generation !== generation
                && !(edit.text === " " && !edit.generation && learningState))) {
            learningState = null
            return
        }
        if (!learningState)
            learningState = Learning.start(edit.previous, edit.previous.length)
        const locale = VirtualKeyboardSettings.locale
        const ru = locale.startsWith("ru")
        const records = []
        if (edit.action === "enter") {
            Learning.complete(learningState, ru, records)
            learningState = null
        } else {
            Learning.apply(learningState, { text: edit.text, choice: edit.choice,
                                            erase: edit.action === "backspace" }, ru, records)
        }
        for (const entry of records) {
            const prev = entry.previous
            decktilGamePanel.learn(generation, locale, prev[prev.length - 2] || "",
                                   prev[prev.length - 1] || "", entry.word)
        }
        if (records.length)
            lookupPersonal()
    }

    Connections {
        target: VirtualKeyboardSettings
        function onLocaleChanged() {
            window.learningState = null
            decktilGamePanel.resetPredictionLocale()
        }
    }
    Connections {
        target: decktilGamePanel
        function onPersonalGenerationChanged() {
            window.learningState = null
            window.lookupPersonal()
        }
        function onPredictionChanged() { window.lookupPersonal() }
        function onSequenceReset() { window.learningState = null }
        function onPersonalEdited(edit) { window.learnEdit(edit) }
        function onLocaleSelected(locale) { VirtualKeyboardSettings.locale = locale }
        function onSpaceProbe(values) {
            if (!window.visible || !window.coveringNative || !window.selector || touch.pressed
                    || !(values[4] & 1) || !(values[5] & 1) || values[9] <= 0)
                return
            const control = window.selector
            const point = control.padPosition(0, values[0], values[1])
            // The covered panel disables its keys. Inspect geometry only;
            // the supervisor verifies focus before delivering a pending space.
            const key = control.keyAt(control.layout, point.x, point.y, true)
            if (key && key.keyType === QtVirtualKeyboard.KeyType.SpaceKey)
                decktilGamePanel.spaceIntent(values[9])
        }
        function onFrame(values) {
            if (values[9] === 0) {
                touch.side = 0
                if (window.selector)
                    window.selector.updateFrame(...values)
            } else if (window.visible && !window.coveringNative && window.selector && !touch.pressed) {
                window.selector.updateFrame(...values)
            }
        }
    }
    // Use the same key, hold and repeat logic for a touch and a pad click.
    MouseArea {
        id: touch
        anchors.bottom: panel.bottom
        width: panel.width
        height: window.surfaceHeight
        property int side: 0
        function send(down, edge) {
            const control = window.selector
            if (!control || !window.visible || window.coveringNative || !side)
                return
            const layout = control.layout
            const point = layout.mapFromItem(touch, mouseX, mouseY)
            if (edge && !layout.contains(point))
                return
            const x = (2 * (point.x / (layout.width - 1) - (side === 1 ? 0 : .45)) / .55 - 1)
            const y = 1 - 2 * point.y / (layout.height - 1)
            const lx = Math.round(x * 32767 / control.typingScale)
            const ly = Math.round(y * 32767 / control.typingScale)
            control.updateFrame(side === 1 ? lx : 0, side === 1 ? ly : 0,
                                side === 2 ? lx : 0, side === 2 ? ly : 0,
                                down ? side : 0, edge ? side : 0, down ? side : 0, 0, 0, -1)
        }
        onPressed: {
            side = window.coveringNative ? 0 : mouseX < width / 2 ? 1 : 2
            send(true, true)
        }
        onPositionChanged: if (pressed) send(true, false)
        onReleased: { send(false, false); side = 0 }
        onCanceled: {
            if (window.selector) {
                window.selector.stopErase()
                window.selector.cancelHolds()
                window.selector.updateFrame(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
            }
            side = 0
        }
        Timer {
            interval: 90
            repeat: true
            running: touch.pressed && window.visible && !window.coveringNative
            onTriggered: touch.send(true, false)
        }
    }
}
