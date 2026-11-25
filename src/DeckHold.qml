import QtQuick

// Passive overlay. The controller owns the press, while native touchscreen
// events still go through Qt Virtual Keyboard's own alternative-keys view.
Item {
    id: menu
    required property Item layout
    required property bool leftHand
    required property color accent
    property color panelColor: "#dc212837"
    property color textColor: "white"
    property int holdInterval: 500
    property Item sourceKey: null
    property point origin: Qt.point(0, 0)
    property var choices: []
    property int selected: -1
    property real cellWidth: 40
    property bool opened: false
    property bool moved: false
    readonly property bool tracking: sourceKey !== null
    signal tapped(var key)
    signal chosen(var key, string symbol)
    visible: opened
    enabled: false
    z: 30
    width: choices.length * cellWidth
    height: sourceKey ? Math.max(24, Math.min(54, sourceKey.height * 0.9)) : 0
    readonly property real cancelDistance: sourceKey ? sourceKey.height * 1.25 : 0

    function begin(key, position) {
        cancel()
        sourceKey = key
        origin = position
        delay.start()
        watchdog.start()
    }

    function present() {
        if (!sourceKey || !sourceKey.visible || !layout.visible)
            return cancel()
        const source = Array.from(sourceKey.effectiveAlternativeKeys)
        if (!source.length)
            return cancel()
        const padMin = leftHand ? 2 : parent.width * 0.45 + 2
        const padMax = (leftHand ? parent.width * 0.55 : parent.width) - 2
        cellWidth = Math.max(1, Math.min(64, sourceKey.width * 0.84, (padMax - padMin) / source.length))
        const reverse = origin.x + (source.length - 0.5) * cellWidth > padMax
                && origin.x - padMin > padMax - origin.x
        const actual = source.map(symbol => sourceKey.uppercased ? symbol.toUpperCase() : symbol)
        choices = reverse ? actual.reverse() : actual
        selected = reverse ? choices.length - 1 : 0
        x = Math.max(padMin, Math.min(padMax - width, origin.x - (selected + 0.5) * cellWidth))
        const top = sourceKey.mapToItem(parent, 0, 0).y
        const above = top - height - 4
        y = above >= 0 ? above : Math.min(parent.height - height, top + sourceKey.height + 4)
        opened = true
    }

    function update(position) {
        if (!tracking)
            return
        watchdog.restart()
        // A clear vertical slide cancels even on the top or bottom row.
        if (Math.abs(position.y - origin.y) > cancelDistance) {
            cancel()
            return
        }
        if (!opened)
            return
        if (!moved && Math.abs(position.x - origin.x) < Math.min(5, cellWidth / 5))
            return
        moved = true
        selected = Math.max(0, Math.min(choices.length - 1, Math.floor((position.x - x) / cellWidth)))
    }

    function release() {
        if (!tracking)
            return
        const key = sourceKey
        const symbol = opened && selected >= 0 ? choices[selected] : ""
        cancel()
        if (symbol)
            chosen(key, symbol)
        else
            tapped(key)
    }

    function cancel() {
        delay.stop()
        watchdog.stop()
        opened = false
        moved = false
        selected = -1
        choices = []
        sourceKey = null
    }

    Timer { id: delay; interval: menu.holdInterval; onTriggered: menu.present() }
    // The bridge sends a heartbeat every 100 ms while either pad is touched.
    // If it disappears without reporting a release, discard the pending input.
    Timer { id: watchdog; interval: 400; onTriggered: menu.cancel() }

    Rectangle {
        anchors.fill: parent
        radius: 7
        color: menu.panelColor
        border.color: menu.accent
        border.width: 1
    }
    Repeater {
        model: menu.opened ? menu.choices : []
        delegate: Item {
            id: choice
            required property int index
            required property string modelData
            x: index * menu.cellWidth
            width: menu.cellWidth
            height: menu.height
            Rectangle {
                anchors.fill: parent
                anchors.margins: 2
                radius: 5
                color: menu.accent
                opacity: 0.42
                visible: choice.index === menu.selected
            }
            Text {
                anchors.centerIn: parent
                text: choice.modelData
                textFormat: Text.PlainText
                color: menu.textColor
                font.pixelSize: Math.round(Math.min(parent.width * 0.63, parent.height * 0.62))
                font.weight: choice.index === menu.selected ? Font.Bold : Font.Medium
            }
        }
    }
}
