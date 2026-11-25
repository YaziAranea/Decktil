pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtQuick.VirtualKeyboard
import QtQuick.VirtualKeyboard.Components
import "DeckWords.js" as Dictionary

KeyboardRow {
    id: row
    // Keep the letter rows stationary as completions appear or disappear.
    readonly property int stripHeight: Math.round(Math.max(24, Math.min(38, parent.height * 0.12)))
    Layout.minimumHeight: stripHeight
    Layout.maximumHeight: stripHeight
    Layout.preferredHeight: stripHeight
    readonly property string prefix: {
        const blocked = Qt.ImhHiddenText | Qt.ImhSensitiveData | Qt.ImhDigitsOnly
            | Qt.ImhFormattedNumbersOnly | Qt.ImhDialableCharactersOnly
            | Qt.ImhEmailCharactersOnly | Qt.ImhUrlCharactersOnly
        if ((InputContext.inputMethodHints & blocked) || InputContext.selectedText || InputContext.preeditText)
            return ""
        const text = InputContext.surroundingText
        const pos = InputContext.cursorPosition
        if (pos < 2 || pos > text.length || /[a-zа-яё]/i.test(text.charAt(pos)))
            return ""
        const match = (InputContext.locale.startsWith("ru") ? /[а-яё]{2,}$/i : /[a-z]{2,}$/i).exec(text.slice(0, pos))
        return match ? match[0] : ""
    }
    readonly property var candidates: {
        if (!prefix.length)
            return []
        const lower = prefix.toLowerCase()
        const bucket = Dictionary.words[lower.slice(0, 2)] || []
        const result = []
        for (const word of bucket) {
            if (word.length <= lower.length || !word.startsWith(lower))
                continue
            const suffix = word.slice(lower.length)
            result.push(prefix + (prefix === prefix.toUpperCase() ? suffix.toUpperCase() : suffix))
            if (result.length === 3)
                break
        }
        return result
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
            onClicked: {
                const prefix = row.prefix
                if (prefix && text.startsWith(prefix) && text.length > prefix.length)
                    InputContext.commit(text.slice(prefix.length))
            }
        }
    }
}
