import QtQuick
import QtTest
import "../src"
import "../src/DeckAlternates.js" as Alternates

Item {
    id: panel
    width: 640
    height: 240
    Item {
        id: key
        x: 540
        y: 170
        width: 58
        height: 46
        property var effectiveAlternativeKeys: ["?", "!", "…"]
        property bool uppercased: false
    }
    DeckHold {
        id: right
        layout: panel
        leftHand: false
        accent: "orange"
        holdInterval: 25
    }
    DeckHold {
        id: left
        layout: panel
        leftHand: true
        accent: "cyan"
        holdInterval: 25
    }
    SignalSpy { id: taps; target: right; signalName: "tapped" }
    SignalSpy { id: choices; target: right; signalName: "chosen" }
    SignalSpy { id: otherChoices; target: left; signalName: "chosen" }

    TestCase {
        name: "DeckAlternates"
        when: windowShown

        function init() {
            right.cancel()
            left.cancel()
            taps.clear()
            choices.clear()
            otherChoices.clear()
        }
        function test_keys() {
            compare(Alternates.keys(".", "!.?"), [".", "?", "!", "…"])
            compare(Alternates.keys("е", "её"), ["е", "ё", "5"])
            const options = Alternates.keys("e", "êe3ëèé")
            compare(options.slice(0, 3), ["e", "3", "é"])
            verify(options.includes("ê") && options.includes("ë"))
            compare(Alternates.hint("е", Alternates.keys("е", []), true), "Ё")
            compare(Alternates.keys(",", []), [])
        }
        function test_shortTap() {
            right.begin(key, Qt.point(570, 190))
            right.release()
            right.release()
            compare(taps.count, 1)
            compare(choices.count, 0)
            verify(!right.opened && !right.tracking)
        }
        function test_holdSelectFromEitherSide() {
            left.begin(key, Qt.point(240, 190))
            right.begin(key, Qt.point(570, 190))
            tryVerify(() => left.opened && right.opened)
            compare(left.choices[left.selected], "?")
            compare(right.choices[right.selected], "?")
            verify(right.x + right.width <= panel.width)
            verify(left.x + left.width < panel.width * 0.55)
            const selectX = right.x + (right.choices.indexOf("!") + 0.5) * right.cellWidth
            right.update(Qt.point(selectX, 189))
            right.release()
            right.release()
            left.release()
            compare(choices.count, 1)
            compare(choices.signalArguments[0][1], "!")
            compare(otherChoices.signalArguments[0][1], "?")
            compare(taps.count, 0)
            right.begin(key, Qt.point(570, 190))
            right.release()
            compare(taps.count, 1)
        }
        function test_cancel() {
            right.begin(key, Qt.point(570, 190))
            tryVerify(() => right.opened)
            right.update(Qt.point(570, 90))
            right.release()
            right.begin(key, Qt.point(570, 190))
            right.cancel()
            right.release()
            compare(taps.count, 0)
            compare(choices.count, 0)
        }
        function test_lostController() {
            right.begin(key, Qt.point(570, 190))
            tryVerify(() => right.opened)
            tryVerify(() => !right.tracking, 1000)
            right.release()
            compare(taps.count, 0)
            compare(choices.count, 0)
        }
    }
}
