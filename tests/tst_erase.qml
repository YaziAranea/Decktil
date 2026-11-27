import QtQuick
import QtTest
import QtQuick.VirtualKeyboard
import "../src" as Deck

Item {
    id: layout
    width: 400
    height: 150
    property var delivered: []
    property var deckAppearance: null

    TextEdit {
        id: editor
        width: 300
        height: 80
        focus: true
        Keys.onPressed: event => {
            layout.delivered.push(event.key)
            // Like the Codex composer, recall history on Up in a blank field.
            if (event.key === Qt.Key_Up && !text.trim()) {
                text = "prior message"
                event.accepted = true
            }
        }
    }
    InputPanel { width: parent.width; y: editor.height }
    Deck.DeckSelector { id: selector; layout: layout }

    TestCase {
        name: "EraseWithoutNavigation"
        when: windowShown
        function test_x_sends_only_backspace_data() {
            return [{tag: "empty", value: ""}, {tag: "last character", value: "x"}]
        }
        function test_x_sends_only_backspace(data) {
            editor.text = data.value
            editor.cursorPosition = data.value.length
            editor.forceActiveFocus()
            Qt.inputMethod.show()
            tryCompare(InputContext, "inputItem", editor)
            tryCompare(InputContext, "surroundingText", data.value)
            layout.delivered = []
            selector.startErase(selector.eraseButton, 1)
            wait(450)
            compare(layout.delivered, [Qt.Key_Backspace])
            compare(editor.text, "")
        }
    }
}
