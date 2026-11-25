pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtQuick.VirtualKeyboard
import QtQuick.VirtualKeyboard.Components
import "DeckWords.js" as Dictionary
import "DeckNext.js" as NextWords
import "DeckPrediction.js" as Prediction

KeyboardRow {
    id: row
    // Keep the letter rows stationary as suggestions appear or disappear.
    readonly property int stripHeight: Math.round(Math.max(24, Math.min(38, parent.height * 0.12)))
    Layout.minimumHeight: stripHeight
    Layout.maximumHeight: stripHeight
    Layout.preferredHeight: stripHeight

    readonly property bool blocked: Prediction.fieldBlocked(
        InputContext.inputMethodHints, InputContext.selectedText, InputContext.preeditText,
        InputContext.cursorPosition, InputContext.anchorPosition)
    readonly property var wordContext: blocked ? null : Prediction.context(
        InputContext.surroundingText, InputContext.cursorPosition, InputContext.locale)
    readonly property var candidates: Prediction.suggestions(
        wordContext, Dictionary.words, NextWords.bigram, NextWords.trigram, personal.words)

    property DeckPersonal personalization: DeckPersonal {
        id: personal
        active: row.visible
        blocked: row.blocked
        context: row.wordContext
    }

    function choose(candidate) {
        if (blocked || settle.running || !candidate || candidates.indexOf(candidate) < 0)
            return
        const text = InputContext.surroundingText
        const pos = InputContext.cursorPosition
        const insert = Prediction.insertion(wordContext, candidate, Prediction.canAppendSpace(text, pos))
        if (insert) {
            // Prevent a second tap on the old list before the native editor acknowledges the edit.
            settle.restart()
            personal.key(insert, candidate, false)
            InputContext.commit(insert)
        }
    }
    Connections {
        target: InputContext
        function onSurroundingTextChanged() { settle.stop() }
        function onInputItemChanged() { settle.stop() }
        function onLocaleChanged() { settle.stop() }
    }
    Timer {
        id: settle
        interval: 320
    }
    Repeater {
        model: 3
        Key {
            required property int index
            objectName: "deckSuggestion" + index
            text: row.candidates[index] || ""
            displayText: text
            noKeyEvent: true
            visible: text.length > 0
            enabled: visible
            onClicked: row.choose(text)
        }
    }
}
