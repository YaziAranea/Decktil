pragma ComponentBehavior: Bound
import QtQml
import QtQuick.VirtualKeyboard
import org.kde.plasma.workspace.dbus as DBus
import "DeckLearning.js" as Learning

QtObject {
    id: personal
    required property bool active
    required property bool blocked
    required property var context
    readonly property string service: "local.deck.PlasmaKeyboard"
    property string session: ""
    property int settingRequest: 0
    property int lookupRequest: 0
    property var tracker: null
    property bool textChanged: false
    property bool ready: false
    property string resultKey: ""
    property var result: []
    readonly property bool permitted: !!session && active && !blocked
    readonly property string queryKey: permitted && context ? JSON.stringify(
        [InputContext.locale, context.previous, context.prefix]) : ""
    readonly property var words: queryKey && resultKey === queryKey ? result : []

    function message(member, args) {
        return { service: service, path: "/Pads", iface: service, member: member,
                 signature: "(" + "s".repeat(args.length) + ")",
                 arguments: args.map(value => new DBus.string(value)) }
    }

    function resetSequence() {
        tracker = permitted ? Learning.start(InputContext.surroundingText, InputContext.cursorPosition) : null
        textChanged = false
        expiry.stop()
    }

    function invalidate() {
        ++lookupRequest
        resultKey = ""
        result = []
        resetSequence()
        if (ready && permitted)
            Qt.callLater(lookup)
    }

    function reloadSetting() {
        if (!ready)
            return
        const serial = ++settingRequest
        session = ""
        invalidate()
        if (!owner.registered)
            return
        DBus.SessionBus.asyncCall(message("PersonalSession", []), reply => {
            if (serial !== settingRequest || !owner.registered)
                return
            session = String(reply.value.value ?? reply.value)
            resetSequence()
        }, () => {})
    }

    function lookup() {
        const serial = ++lookupRequest
        const key = queryKey
        if (!key)
            return
        const prev = context.previous
        const args = [InputContext.locale, prev[prev.length - 2] || "", prev[prev.length - 1] || "", context.prefix]
        DBus.SessionBus.asyncCall(message("PersonalSuggestions", args), reply => {
            if (serial === lookupRequest && key === queryKey) {
                result = Array.from(reply.value, word => String(word.value ?? word))
                resultKey = key
            }
        }, () => {})
    }

    function learn(records) {
        for (const entry of records) {
            const prev = entry.previous
            DBus.SessionBus.asyncCall(message("LearnWord", [session, InputContext.locale,
                prev[prev.length - 2] || "", prev[prev.length - 1] || "", entry.word]))
        }
        if (records.length)
            Qt.callLater(lookup)
    }

    function reconcile() {
        if (!ready)
            return
        if (!permitted || !tracker) {
            resetSequence()
            return
        }
        const text = InputContext.surroundingText
        const pos = InputContext.cursorPosition
        if (!tracker.edits.length) {
            if (textChanged || pos !== tracker.pos)
                resetSequence()
            return
        }
        textChanged = false
        const records = Learning.confirm(tracker, text, pos, InputContext.locale)
        if (records !== null) {
            learn(records)
            if (!tracker.edits.length)
                expiry.stop()
        }
    }

    function key(inserted, choice, erase, replaceLength) {
        if (!permitted || !tracker)
            return
        if (!Learning.queue(tracker, inserted, choice, erase, replaceLength)) {
            resetSequence()
            return
        }
        expiry.restart()
        Qt.callLater(reconcile)
    }

    function click(keyCode, text, modifiers) {
        if (!permitted)
            return
        if (modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)) {
            resetSequence()
        } else if (keyCode === Qt.Key_Backspace) {
            key("", "", true)
        } else if (keyCode === Qt.Key_Return || keyCode === Qt.Key_Enter) {
            // Some chat editors submit and clear instead of inserting a newline.
            // Only finalize a word already acknowledged in this focused editor.
            reconcile()
            if (tracker && !tracker.edits.length && tracker.pos === InputContext.cursorPosition) {
                const records = []
                Learning.complete(tracker, InputContext.locale.startsWith("ru"), records)
                learn(records)
            }
            resetSequence()
        } else if (keyCode === Qt.Key_Space || (text && text.length === 1)) {
            key(keyCode === Qt.Key_Space ? " " : text, "", false)
        } else {
            resetSequence()
        }
    }

    onQueryKeyChanged: if (ready) Qt.callLater(lookup)
    onPermittedChanged: if (ready) Qt.callLater(reconcile)
    onActiveChanged: if (ready) invalidate()
    Component.onCompleted: { ready = true; reloadSetting() }

    property DBus.DBusServiceWatcher ownerWatcher: DBus.DBusServiceWatcher {
        id: owner
        busType: DBus.BusType.Session
        watchedService: personal.service
        onRegisteredChanged: personal.reloadSetting()
    }
    property DBus.SignalWatcher notifications: DBus.SignalWatcher {
        busType: DBus.BusType.Session
        service: personal.service
        path: "/Pads"
        iface: personal.service
        function dbusPersonalChanged() { personal.reloadSetting() }
        function dbusCancelRepeat() { personal.invalidate() }
    }
    property Timer timeout: Timer {
        id: expiry
        interval: 450
        onTriggered: personal.resetSequence()
    }
    property Connections keys: Connections {
        target: InputContext.inputEngine
        function onVirtualKeyClicked(key, text, modifiers, isAutoRepeat) { personal.click(key, text, modifiers) }
    }
    property Connections editor: Connections {
        target: InputContext
        function onSurroundingTextChanged() {
            personal.textChanged = true
            Qt.callLater(personal.reconcile)
        }
        function onCursorPositionChanged() { Qt.callLater(personal.reconcile) }
        function onInputItemChanged() { personal.invalidate() }
        function onLocaleChanged() { personal.invalidate() }
        function onInputMethodHintsChanged() { personal.invalidate() }
    }
}
