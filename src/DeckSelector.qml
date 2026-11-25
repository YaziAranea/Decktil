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

    function updateFrame(lx, ly, rx, ry, touches, pressed, heldMask, leftForce, rightForce, serial) {
        if (!layout.visible || layout.width <= 0 || layout.height <= 0)
            return
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
        const clicked = Number(pressed) & ((leftKey ? 1 : 0) | (rightKey ? 2 : 0))
        const hovered = moved & ~held & ~clicked
        // Switching language or hiding the panel can unload this layout during activation.
        if (serial > 0 && (clicked || hovered))
            DBus.SessionBus.asyncCall({
                service: "local.deck.PlasmaKeyboard", path: "/Pads",
                iface: "local.deck.PlasmaKeyboard", member: "Feedback",
                signature: "(iii)",
                arguments: [new DBus.int32(serial), new DBus.int32(clicked), new DBus.int32(hovered)]
            })
        if (clicked & 1) {
            leftFeedback.pulse()
            activate(leftKey)
        }
        if (clicked & 2) {
            rightFeedback.pulse()
            activate(rightKey)
        }
    }

    property DBus.SignalWatcher input: DBus.SignalWatcher {
        busType: DBus.BusType.Session
        service: "local.deck.PlasmaKeyboard"
        path: "/Pads"
        iface: "local.deck.PlasmaKeyboard"
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
        readonly property bool confirmed: confirmedKey !== null && confirmedKey === selectedKey
            && (held || clickPulse.running)
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
                duration: 140
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
