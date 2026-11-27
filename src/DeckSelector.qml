import QtQuick
import QtQuick.VirtualKeyboard
import QtQuick.VirtualKeyboard.Settings
import org.kde.plasma.workspace.dbus as DBus

QtObject {
    id: selector
    required property Item layout
    property QtObject gamePanel: null
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
    property int emptyCheckSerial: 0
    property double emptyCheckDeadline: 0
    property double confirmEmptyAfter: 0
    property int emptyCheckGeneration: 0
    property var emptyCheckInputItem: null
    property double lastControllerFrame: 0
    readonly property int eraseButton: 16

    function keyAt(item, x, y, includeDisabled = false) {
        for (let child of item.children) {
            if (!child.visible || (!includeDisabled && !child.enabled))
                continue
            const point = child.mapFromItem(layout, x, y)
            if (!child.contains(point))
                continue
            if (child.keyType !== undefined)
                return child.keyType === QtVirtualKeyboard.KeyType.FillerKey ? null : child
            const nested = keyAt(child, x, y, includeDisabled)
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
                const next = locales[(index + 1) % locales.length]
                if (!gamePanel || !gamePanel.requestLocale(next))
                    VirtualKeyboardSettings.locale = next
                return
            }
        }
        if (gamePanel && key.keyType === QtVirtualKeyboard.KeyType.HideKeyboardKey) {
            gamePanel.dismiss()
            return
        }
        if (gamePanel && key.keyType === QtVirtualKeyboard.KeyType.ShiftKey) {
            gamePanel.toggleShift()
            return
        }
        if (!key.noKeyEvent) {
            const text = key.uppercased ? key.text.toUpperCase() : key.text
            if (gamePanel)
                gamePanel.key(key.key, text)
            else
                InputContext.inputEngine.virtualKeyClick(key.key, text,
                    key.uppercased ? Qt.ShiftModifier : Qt.NoModifier)
        }
        key.clicked()
    }

    function chooseAlternate(key, symbol) {
        if (key && key.enabled && key.visible && layout.visible) {
            if (gamePanel)
                gamePanel.key(key.key, symbol)
            else
                InputContext.inputEngine.virtualKeyClick(key.key, symbol,
                    key.uppercased ? Qt.ShiftModifier : Qt.NoModifier)
        }
    }

    function cancelHolds() {
        leftHold.cancel()
        rightHold.cancel()
    }

    function pressKey(key, bit, menu, position) {
        if (isBackspace(key))
            startErase(bit, 0)
        else if (key && key.deckHasAlternates && key.effectiveAlternativeKeys.length && (held & bit))
            menu.begin(key, position)
        else
            activate(key)
    }

    function trackHold(menu, bit, position) {
        if (!menu.tracking)
            return
        if (contacts & bit)
            menu.update(position)
        if (!(held & bit))
            menu.release()
    }

    function stopErase() {
        eraseTimer.stop()
        eraseOwner = 0
        eraseSerial = 0
    }

    function cancelEmptyCheck() {
        emptyCheckTimer.stop()
        emptyCheckSerial = 0
        emptyCheckInputItem = null
        ++emptyCheckGeneration
    }

    function watchForEmpty(serial, initiallyEmpty) {
        cancelEmptyCheck()
        const now = Date.now()
        emptyCheckSerial = serial
        emptyCheckInputItem = InputContext.inputItem
        emptyCheckDeadline = now + 360
        confirmEmptyAfter = initiallyEmpty ? now + 65 : 0
        emptyCheckTimer.restart()
    }

    function finishEmptyCheck(serial) {
        cancelEmptyCheck()
        stopErase()
        requestClose(serial)
    }

    function confirmEmpty(serial, paragraphMarker) {
        const generation = emptyCheckGeneration
        emptyCheckTimer.stop()
        if (paragraphMarker)
            stopErase() // Holding X at the start must not send another Backspace.
        DBus.SessionBus.asyncCall({
            service: "local.deck.PlasmaKeyboard", path: "/Pads",
            iface: "local.deck.PlasmaKeyboard", member: "ConfirmEmpty",
            signature: "(ib)", arguments: [new DBus.int32(serial), new DBus.bool(paragraphMarker)]
        }, reply => {
            if (generation !== emptyCheckGeneration || serial !== emptyCheckSerial)
                return
            refreshEditor()
            const text = InputContext.surroundingText
            if ((reply.value.value ?? reply.value) === true && layout.visible
                    && emptyCheckInputItem === InputContext.inputItem && atUnselectedStart()
                    && text === (paragraphMarker ? "\n" : "")) {
                finishEmptyCheck(serial)
            } else {
                cancelEmptyCheck()
            }
        }, () => {
            if (generation === emptyCheckGeneration)
                cancelEmptyCheck()
        })
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
        if (gamePanel) {
            gamePanel.key(Qt.Key_Backspace, "")
            return true
        }
        refreshEditor()
        const atStart = atUnselectedStart()
        const surrounding = InputContext.surroundingText
        const initiallyEmpty = atStart && !surrounding.length
        // Wayland reports the result after the key event, often after X is
        // released. Let the editor perform the deletion (including selection
        // and grapheme handling) and dismiss only after it reports empty.
        const physical = owner === eraseButton && eraseSerial > 0
        const ambiguous = physical && atStart && surrounding === "\n"
        if (physical && !(InputContext.inputMethodHints & (Qt.ImhHiddenText | Qt.ImhSensitiveData)))
            watchForEmpty(eraseSerial, initiallyEmpty)
        // The first Wayland snapshot can be blank even in a populated field.
        // Sending Backspace lets the editor handle the press and publish its state.
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
    property Timer emptyCheckTimer: Timer {
        interval: 25
        repeat: true
        onTriggered: {
            const now = Date.now()
            if (!selector.layout.visible || now > selector.emptyCheckDeadline
                    || selector.emptyCheckInputItem !== InputContext.inputItem) {
                selector.cancelEmptyCheck()
                return
            }
            selector.refreshEditor()
            const text = InputContext.surroundingText
            if (!selector.atUnselectedStart() || (text !== "" && text !== "\n"))
                return
            if (now < selector.confirmEmptyAfter)
                return
            const serial = selector.emptyCheckSerial
            if (!text.length && !selector.confirmEmptyAfter) {
                // A known nonempty field reported empty after our Backspace.
                selector.finishEmptyCheck(serial)
            } else {
                // A cold Qt field can report "" with text still present.
                // ProseMirror reports "\n" for an empty paragraph. Ask without
                // sending navigation keys, which a chat can interpret as history.
                selector.confirmEmpty(serial, text === "\n")
            }
        }
    }
    property Connections editorFocus: Connections {
        target: InputContext
        function onInputItemChanged() { selector.stopErase(); selector.cancelEmptyCheck(); selector.cancelHolds() }
        function onLocaleChanged() { selector.stopErase(); selector.cancelEmptyCheck(); selector.cancelHolds() }
    }
    property Connections layoutVisibility: Connections {
        target: selector.layout
        function onVisibleChanged() {
            if (!selector.layout.visible) {
                selector.stopErase()
                selector.cancelEmptyCheck()
                selector.cancelHolds()
            }
        }
    }

    function updateFrame(lx, ly, rx, ry, touches, pressed, heldMask, leftForce, rightForce, serial) {
        if (serial === 0) {
            stopErase()
            cancelEmptyCheck()
            cancelHolds()
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
        const cancelMenu = (Number(pressed) & eraseButton) && (leftHold.opened || rightHold.opened)
        if (cancelMenu)
            cancelHolds()
        trackHold(leftHold, 1, leftPosition)
        trackHold(rightHold, 2, rightPosition)
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
            cancelEmptyCheck()
        const pressureTick = (Number(pressed) >> 2) & selectable
        const hovered = moved & ~held & ~clicked & ~pressureTick
        // Switching language or hiding the panel can unload this layout during activation.
        if (serial > 0 && (clicked || hovered || pressureTick)) {
            const cues = hovered | (pressureTick << 2)
            if (gamePanel)
                gamePanel.feedback(Number(serial), clicked, cues)
            else
                DBus.SessionBus.asyncCall({
                    service: "local.deck.PlasmaKeyboard", path: "/Pads",
                    iface: "local.deck.PlasmaKeyboard", member: "Feedback",
                    signature: "(iii)",
                    arguments: [new DBus.int32(serial), new DBus.int32(clicked), new DBus.int32(cues)]
                })
        }
        if (clicked & 1) {
            leftFeedback.pulse()
            pressKey(leftKey, 1, leftHold, leftPosition)
        }
        if (clicked & 2) {
            rightFeedback.pulse()
            pressKey(rightKey, 2, rightHold, rightPosition)
        }
        if ((Number(pressed) & eraseButton) && !cancelMenu)
            startErase(eraseButton, Number(serial))
    }

    property DBus.SignalWatcher input: DBus.SignalWatcher {
        busType: DBus.BusType.Session
        service: selector.gamePanel ? "local.deck.DecktilGaming" : "local.deck.PlasmaKeyboard"
        path: "/Pads"
        iface: "local.deck.PlasmaKeyboard"
        function dbusCancelRepeat() {
            if (!selector.gamePanel) {
                selector.stopErase()
                selector.cancelEmptyCheck()
                selector.cancelHolds()
            }
        }
        function dbusFrame(lx, ly, rx, ry, touches, pressed, held, leftForce, rightForce, serial) {
            if (!selector.gamePanel)
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
            label: leftHold.opened ? "" : selector.keyLabel(selector.leftKey)
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
            label: rightHold.opened ? "" : selector.keyLabel(selector.rightKey)
            accent: "#ffbb59"
            splitLabels: feedbackSurface.splitLabels
            midpoint: feedbackSurface.midpoint
        }
        DeckHold {
            id: leftHold
            objectName: "deckLeftAlternates"
            layout: selector.layout
            leftHand: true
            accent: selector.layout.deckAppearance ? selector.layout.deckAppearance.accentColor : "#52c9ff"
            panelColor: selector.layout.deckAppearance ? selector.layout.deckAppearance.popupColor : "#dc212837"
            textColor: selector.layout.deckAppearance ? selector.layout.deckAppearance.textColor : "white"
            onTapped: key => selector.activate(key)
            onChosen: (key, symbol) => selector.chooseAlternate(key, symbol)
        }
        DeckHold {
            id: rightHold
            objectName: "deckRightAlternates"
            layout: selector.layout
            leftHand: false
            accent: selector.layout.deckAppearance ? selector.layout.deckAppearance.accentColor : "#ffbb59"
            panelColor: selector.layout.deckAppearance ? selector.layout.deckAppearance.popupColor : "#dc212837"
            textColor: selector.layout.deckAppearance ? selector.layout.deckAppearance.textColor : "white"
            onTapped: key => selector.activate(key)
            onChosen: (key, symbol) => selector.chooseAlternate(key, symbol)
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
