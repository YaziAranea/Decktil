import QtQuick
import QtQuick.VirtualKeyboard
import QtQuick.VirtualKeyboard.Settings
import org.kde.plasma.workspace.dbus as DBus

QtObject {
    id: selector
    required property Item layout
    property real typingScale: 1
    property Item leftKey: null
    property Item rightKey: null
    property point leftPosition: Qt.point(0, 0)
    property point rightPosition: Qt.point(0, 0)
    property int contacts: 0
    property int held: 0
    property real leftPressure: 0
    property real rightPressure: 0
    property int eraseOwner: 0
    property int eraseSerial: 0
    property int placeholderSerial: 0
    property double placeholderDeadline: 0
    property double caretCheckAt: 0
    property var placeholderInputItem: null
    property double lastControllerFrame: 0
    readonly property int eraseButton: 16

    function keyAt(item, x, y) {
        for (let child of item.children) {
            if (!child.visible || !child.enabled)
                continue
            const point = child.mapFromItem(layout, x, y)
            if (!child.contains(point))
                continue
            if (child.keyType !== undefined)
                return child.keyType === QtVirtualKeyboard.KeyType.FillerKey ? null : child
            const nested = keyAt(child, x, y)
            if (nested)
                return nested
        }
        return null
    }

    function axis(value) {
        return Math.max(-1, Math.min(1, Number(value) * typingScale / 32767))
    }

    function padPosition(side, rawX, rawY) {
        const fraction = (axis(rawX) + 1) / 2
        const x = ((side === 0 ? 0 : 0.45) + fraction * 0.55) * (layout.width - 1)
        const y = (1 - axis(rawY)) / 2 * (layout.height - 1)
        return Qt.point(x, y)
    }

    function keyLabel(key) {
        if (!key)
            return ""
        const russian = InputContext.locale.startsWith("ru")
        switch (key.keyType) {
        case QtVirtualKeyboard.KeyType.SpaceKey: return russian ? "Пробел" : "Space"
        case QtVirtualKeyboard.KeyType.BackspaceKey: return russian ? "Стереть" : "Backspace"
        case QtVirtualKeyboard.KeyType.EnterKey: return key.displayText || (russian ? "Ввод" : "Enter")
        case QtVirtualKeyboard.KeyType.ShiftKey: return "Shift"
        case QtVirtualKeyboard.KeyType.ChangeLanguageKey: return russian ? "Язык" : "Language"
        case QtVirtualKeyboard.KeyType.HideKeyboardKey: return russian ? "Скрыть" : "Hide"
        default: {
            const label = key.displayText || key.text
            return key.uppercased ? label.toUpperCase() : label
        }
        }
    }

    function activate(key) {
        if (!key || !key.enabled || !key.visible)
            return
        if (key.objectName === "changeLanguageKey") {
            const locales = VirtualKeyboardSettings.activeLocales
            if (locales.length > 1) {
                const index = locales.indexOf(InputContext.locale)
                VirtualKeyboardSettings.locale = locales[(index + 1) % locales.length]
                return
            }
        }
        if (!key.noKeyEvent) {
            const text = key.uppercased ? key.text.toUpperCase() : key.text
            InputContext.inputEngine.virtualKeyClick(key.key, text,
                key.uppercased ? Qt.ShiftModifier : Qt.NoModifier)
        }
        key.clicked()
    }

    function stopErase() {
        eraseTimer.stop()
        eraseOwner = 0
        eraseSerial = 0
    }

    function cancelPlaceholder() {
        placeholderTimer.stop()
        placeholderSerial = 0
        placeholderInputItem = null
        caretCheckAt = 0
    }

    function refreshEditor() {
        // Plasma receives surrounding text and caret changes asynchronously.
        // Re-query its focused input listener before deciding to dismiss.
        // qmllint disable missing-property
        Qt.inputMethod.update(Qt.ImSurroundingText | Qt.ImCurrentSelection | Qt.ImCursorPosition | Qt.ImAnchorPosition)
        // qmllint enable missing-property
    }

    function atUnselectedStart() {
        const hints = InputContext.inputMethodHints
        return !(hints & (Qt.ImhHiddenText | Qt.ImhSensitiveData))
            && !InputContext.preeditText.length && !InputContext.selectedText.length
            && InputContext.cursorPosition === 0 && InputContext.anchorPosition === 0
    }

    function requestClose(serial) {
        DBus.SessionBus.asyncCall({
            service: "local.deck.PlasmaKeyboard", path: "/Pads",
            iface: "local.deck.PlasmaKeyboard", member: "CloseOnEmpty",
            signature: "(i)", arguments: [new DBus.int32(serial)]
        })
    }

    function eraseOnce(owner) {
        refreshEditor()
        const atStart = atUnselectedStart()
        const surrounding = InputContext.surroundingText
        if (atStart && !surrounding.length) {
            if (owner === eraseButton && eraseSerial > 0)
                requestClose(eraseSerial)
            return false
        }
        // An empty Chromium paragraph and an actual newline can both appear
        // as a single LF at offset zero. Send the normal Backspace once.
        const ambiguous = owner === eraseButton && atStart && surrounding === "\n"
        if (ambiguous) {
            placeholderSerial = eraseSerial
            placeholderInputItem = InputContext.inputItem
            placeholderDeadline = Date.now() + 200
            caretCheckAt = 0
            placeholderTimer.restart()
        }
        InputContext.inputEngine.virtualKeyClick(Qt.Key_Backspace, "", Qt.NoModifier)
        return !ambiguous
    }

    function startErase(owner, serial) {
        stopErase()
        eraseSerial = serial
        if (eraseOnce(owner)) {
            eraseOwner = owner
            eraseTimer.interval = 450
            eraseTimer.start()
        }
    }

    function eraseHeld() {
        if (!eraseOwner || !(held & eraseOwner) || !layout.visible || Date.now() - lastControllerFrame > 350)
            return false
        if (eraseOwner === eraseButton)
            return true
        const key = eraseOwner === 1 ? leftKey : rightKey
        return isBackspace(key)
    }

    function isBackspace(key) {
        return !!key && key.keyType === QtVirtualKeyboard.KeyType.BackspaceKey
    }

    property Timer eraseTimer: Timer {
        repeat: false
        onTriggered: {
            if (!selector.eraseHeld() || !selector.eraseOnce(selector.eraseOwner)) {
                selector.stopErase()
                return
            }
            interval = 75
            restart()
        }
    }
    property Timer placeholderTimer: Timer {
        interval: 25
        repeat: true
        onTriggered: {
            const now = Date.now()
            if (!selector.layout.visible || now > selector.placeholderDeadline
                    || selector.placeholderInputItem !== InputContext.inputItem) {
                selector.cancelPlaceholder()
                return
            }
            selector.refreshEditor()
            const text = InputContext.surroundingText
            const pos = InputContext.cursorPosition
            if (selector.caretCheckAt && text === "\n" && pos === 1
                    && InputContext.anchorPosition === pos && !InputContext.preeditText.length
                    && !InputContext.selectedText.length
                    && !(InputContext.inputMethodHints & (Qt.ImhHiddenText | Qt.ImhSensitiveData))) {
                // The caret reached the next line. Restore it in the same field.
                selector.cancelPlaceholder()
                InputContext.inputEngine.virtualKeyClick(Qt.Key_Up, "", Qt.NoModifier)
            } else if (!selector.atUnselectedStart() || (text !== "" && text !== "\n")) {
                selector.cancelPlaceholder()
            } else if (!text.length || (selector.caretCheckAt && now >= selector.caretCheckAt)) {
                const serial = selector.placeholderSerial
                selector.cancelPlaceholder()
                if (serial > 0)
                    selector.requestClose(serial)
            } else if (!selector.caretCheckAt) {
                // ProseMirror preserves its trailing <br> after Backspace.
                // Down can reach a real next line, but not that decoration.
                // Avoid Right, which can accept an inline completion in Codex.
                selector.caretCheckAt = now + 115
                InputContext.inputEngine.virtualKeyClick(Qt.Key_Down, "", Qt.NoModifier)
            }
        }
    }
    property Connections editorFocus: Connections {
        target: InputContext
        function onInputItemChanged() { selector.stopErase(); selector.cancelPlaceholder() }
    }
    property Connections layoutVisibility: Connections {
        target: selector.layout
        function onVisibleChanged() {
            if (!selector.layout.visible) {
                selector.stopErase()
                selector.cancelPlaceholder()
            }
        }
    }

    function updateFrame(lx, ly, rx, ry, touches, pressed, heldMask, leftForce, rightForce, serial) {
        if (serial === 0) {
            stopErase()
            cancelPlaceholder()
        }
        if (!layout.visible || layout.width <= 0 || layout.height <= 0)
            return
        lastControllerFrame = Date.now()
        contacts = Number(touches)
        held = Number(heldMask)
        leftPressure = Number(leftForce) / 31
        rightPressure = Number(rightForce) / 31
        if (contacts & 1)
            leftPosition = padPosition(0, lx, ly)
        if (contacts & 2)
            rightPosition = padPosition(1, rx, ry)
        const nextLeft = (contacts & 1) ? keyAt(layout, leftPosition.x, leftPosition.y) : null
        const nextRight = (contacts & 2) ? keyAt(layout, rightPosition.x, rightPosition.y) : null
        const moved = ((nextLeft && nextLeft !== leftKey) ? 1 : 0)
                    | ((nextRight && nextRight !== rightKey) ? 2 : 0)
        leftKey = nextLeft
        rightKey = nextRight
        if (eraseOwner && !eraseHeld())
            stopErase()
        const selectable = (leftKey ? 1 : 0) | (rightKey ? 2 : 0)
        const clicked = Number(pressed) & selectable
        if (clicked || (Number(pressed) & eraseButton))
            cancelPlaceholder()
        const pressureTick = (Number(pressed) >> 2) & selectable
        const hovered = moved & ~held & ~clicked & ~pressureTick
        // Switching language or hiding the panel can unload this layout during activation.
        if (serial > 0 && (clicked || hovered || pressureTick))
            DBus.SessionBus.asyncCall({
                service: "local.deck.PlasmaKeyboard", path: "/Pads",
                iface: "local.deck.PlasmaKeyboard", member: "Feedback",
                signature: "(iii)",
                arguments: [new DBus.int32(serial), new DBus.int32(clicked),
                            new DBus.int32(hovered | (pressureTick << 2))]
            })
        if (clicked & 1) {
            leftFeedback.pulse()
            if (isBackspace(leftKey))
                startErase(1, 0)
            else
                activate(leftKey)
        }
        if (clicked & 2) {
            rightFeedback.pulse()
            if (isBackspace(rightKey))
                startErase(2, 0)
            else
                activate(rightKey)
        }
        if (Number(pressed) & eraseButton)
            startErase(eraseButton, Number(serial))
    }

    property DBus.SignalWatcher input: DBus.SignalWatcher {
        busType: DBus.BusType.Session
        service: "local.deck.PlasmaKeyboard"
        path: "/Pads"
        iface: "local.deck.PlasmaKeyboard"
        function dbusCancelRepeat() { selector.stopErase(); selector.cancelPlaceholder() }
        function dbusFrame(lx, ly, rx, ry, touches, pressed, held, leftForce, rightForce, serial) {
            selector.updateFrame(lx, ly, rx, ry, touches, pressed, held, leftForce, rightForce, serial)
        }
    }

    // Keep the feedback outside KeyboardLayout's rows and native touch hit-test tree.
    property Item feedbackLayer: Item {
        id: feedbackSurface
        // Position beside the outer Loader: Qt checks its children on touch.
        // Explicit dimensions also follow the loader while it changes parents.
        readonly property Item host: {
            for (let item = selector.layout.parent; item; item = item.parent)
                if (item.objectName === "keyboardLayoutLoader")
                    return item
            return selector.layout
        }
        parent: host.parent
        x: host.x
        y: host.y
        width: host.width
        height: host.height
        z: 10
        enabled: false
        visible: parent !== null && selector.layout.visible && selector.contacts !== 0
        readonly property bool splitLabels: leftFeedback.showLabel && rightFeedback.showLabel
            && Math.abs(leftFeedback.labelY - rightFeedback.labelY) < leftFeedback.labelHeight + 4
            && leftFeedback.idealX < rightFeedback.idealX + rightFeedback.labelWidth + 4
            && rightFeedback.idealX < leftFeedback.idealX + leftFeedback.labelWidth + 4
        readonly property real midpoint: (leftFeedback.position.x + rightFeedback.position.x) / 2

        FingerFeedback {
            id: leftFeedback
            isLeft: true
            active: (selector.contacts & 1) !== 0
            held: (selector.held & 1) !== 0
            pressure: selector.leftPressure
            selectedKey: selector.leftKey
            position: selector.leftPosition
            label: selector.keyLabel(selector.leftKey)
            accent: "#52c9ff"
            splitLabels: feedbackSurface.splitLabels
            midpoint: feedbackSurface.midpoint
        }
        FingerFeedback {
            id: rightFeedback
            isLeft: false
            active: (selector.contacts & 2) !== 0
            held: (selector.held & 2) !== 0
            pressure: selector.rightPressure
            selectedKey: selector.rightKey
            position: selector.rightPosition
            label: selector.keyLabel(selector.rightKey)
            accent: "#ffbb59"
            splitLabels: feedbackSurface.splitLabels
            midpoint: feedbackSurface.midpoint
        }
    }

    component FingerFeedback: Item {
        id: finger
        required property bool isLeft
        required property bool active
        required property bool held
        required property real pressure
        required property Item selectedKey
        property Item confirmedKey: null
        readonly property bool confirmed: held && confirmedKey !== null && confirmedKey === selectedKey
        onSelectedKeyChanged: confirmedKey = null
        required property point position
        required property string label
        required property color accent
        required property bool splitLabels
        required property real midpoint
        anchors.fill: parent
        visible: active
        readonly property bool showLabel: active && label.length > 0
        readonly property int labelHeight: Math.round(Math.max(31, Math.min(44, height * 0.18)))
        readonly property real labelWidth: Math.min(width / 2 - 8, Math.max(labelHeight, caption.implicitWidth + 20))
        readonly property real idealX: boundX(position.x - labelWidth / 2)
        readonly property real labelY: position.y >= labelHeight + 22
            ? position.y - labelHeight - 16 : Math.min(height - labelHeight - 2, position.y + 16)

        function boundX(x) {
            return Math.max(2, Math.min(width - labelWidth - 2, x))
        }
        function pulse() {
            confirmedKey = selectedKey
            clickPulse.restart()
        }

        Rectangle {
            // Distinct ring diameters keep both pads visible at the exact same point.
            id: ring
            objectName: finger.isLeft ? "deckLeftCursor" : "deckRightCursor"
            z: 1
            width: finger.isLeft ? 27 : 21
            height: width
            x: Math.max(0, Math.min(parent.width - width, finger.position.x - width / 2))
            y: Math.max(0, Math.min(parent.height - height, finger.position.y - height / 2))
            radius: width / 2
            color: finger.confirmed ? finger.accent : "#85111922"
            border.width: 3
            border.color: finger.accent
            Rectangle {
                anchors.centerIn: parent
                width: 4 + Math.round(10 * finger.pressure)
                height: width
                radius: width / 2
                color: finger.confirmed ? "#17202b" : "white"
            }
            NumberAnimation {
                id: clickPulse
                target: ring
                property: "scale"
                from: 0.8
                to: 1
                duration: 80
            }
        }
        Rectangle {
            objectName: finger.isLeft ? "deckLeftHint" : "deckRightHint"
            visible: finger.showLabel
            width: finger.labelWidth
            height: finger.labelHeight
            x: finger.splitLabels ? finger.boundX(finger.isLeft ? finger.midpoint - width - 3
                                                               : finger.midpoint + 3) : finger.idealX
            y: finger.labelY
            color: finger.confirmed ? "#ffffff" : finger.accent
            radius: height / 4
            border.width: finger.confirmed ? 3 : 1
            border.color: finger.confirmed ? finger.accent : "#17202b"
            Text {
                id: caption
                anchors.centerIn: parent
                text: finger.label
                color: "#111820"
                font.pixelSize: Math.round(finger.labelHeight * (text.length === 1 ? 0.72 : 0.50))
                font.weight: Font.DemiBold
                textFormat: Text.PlainText
            }
        }
    }

    property Rectangle leftMarker: Rectangle {
        parent: selector.leftKey
        visible: parent !== null
        anchors.fill: parent
        anchors.margins: 2
        z: 50
        radius: 6
        color: "#393daee9"
        border.width: 3
        border.color: "#52c9ff"
    }
    property Rectangle rightMarker: Rectangle {
        parent: selector.rightKey
        visible: parent !== null
        anchors.fill: parent
        anchors.margins: selector.leftKey === parent ? 5 : 2
        z: 51
        radius: 6
        color: "#39ffbb59"
        border.width: 3
        border.color: "#ffbb59"
    }
}
