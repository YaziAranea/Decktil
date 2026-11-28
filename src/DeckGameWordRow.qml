pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtQuick.VirtualKeyboard.Components
import QtQuick.VirtualKeyboard.Settings
import "DeckWords.js" as Dictionary
import "DeckNext.js" as NextWords
import "DeckMorphData.js" as Morph
import "DeckPrediction.js" as Prediction

KeyboardRow {
    id: row
    required property QtObject host
    readonly property int stripHeight: Math.round(Math.max(24, Math.min(38, parent.height * 0.12)))
    Layout.minimumHeight: stripHeight
    Layout.maximumHeight: stripHeight
    Layout.preferredHeight: stripHeight

    // A game overlay has no editor of its own. The supervisor supplies only
    // the suffix that Decktil delivered to the currently verified field.
    readonly property var wordContext: Prediction.context(
        host.predictionText, host.predictionText.length, VirtualKeyboardSettings.locale)
    readonly property var candidates: Prediction.suggestions(
        wordContext, Dictionary.words, NextWords.bigram, NextWords.trigram,
        host.personalWords, null, Morph.model, NextWords.suffix)

    Repeater {
        model: 3
        Key {
            required property int index
            objectName: "deckSuggestion" + index
            text: row.candidates[index] || ""
            displayText: text
            visible: text.length > 0
            enabled: visible
            noKeyEvent: true
            onClicked: {
                if (row.wordContext && row.candidates.includes(text))
                    row.host.chooseSuggestion(row.wordContext.prefix, text, row.host.predictionRevision)
            }
        }
    }
}
