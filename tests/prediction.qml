import QtQml
import "../src/DeckPrediction.js" as Prediction

QtObject {
    function same(actual, expected, description) {
        if (JSON.stringify(actual) !== JSON.stringify(expected))
            throw new Error(description + ": " + JSON.stringify(actual))
    }

    function run() {
        const en = {
            words: { he: ["help", "hello", "healthy"], th: ["the", "thing", "think"] },
            bigram: { hello: ["there", "how", "i"], how: ["have", "are", "is"],
                      "don't": ["know", "think"], "ok": ["i'm", "i"] },
            trigram: { "hello how": ["are", "have"], "i don't": ["think", "know"] }
        }
        const ru = {
            words: { "пр": ["привет", "право"] },
            bigram: { "привет": ["как", "ты", "что"], "как": ["он", "ты", "дела"] },
            trigram: { "привет как": ["дела", "ты"] }
        }
        function choices(input, lang) {
            const pos = input.indexOf("|")
            const ctx = Prediction.context(input.replace("|", ""), pos, lang)
            const data = lang === "ru_RU" ? ru : en
            return Prediction.suggestions(ctx, data.words, data.bigram, data.trigram)
        }
        function apply(input, lang, word) {
            const pos = input.indexOf("|")
            const text = input.replace("|", "")
            const ctx = Prediction.context(text, pos, lang)
            const inserted = Prediction.insertion(ctx, word, Prediction.canAppendSpace(text, pos))
            return text.slice(0, pos) + inserted + "|" + text.slice(pos)
        }
        try {
            same(choices("привет |", "ru_RU"), ["как", "ты", "что"], "Russian next word")
            same(choices("hello how |", "en_US"), ["are", "have", "is"], "trigram before bigram")
            same(choices("привет как д|", "ru_RU"), ["дела"], "contextual one-letter completion")
            same(choices("hello th|", "en_US"), ["there", "the", "thing"], "context before frequency")
            same(choices("X he| right", "en_US"), ["help", "hello", "healthy"], "dictionary fallback")
            same(choices("HE|", "en_US"), ["HELP", "HELLO", "HEALTHY"], "preserve caps")
            same(choices("ok |", "en_US"), ["I'm", "I"], "English pronoun")
            same(choices("I don’t |", "en_US"), ["think", "know"], "typographic apostrophe")
            same(choices("hello, how |", "en_US"), ["are", "have", "is"], "conversational comma")
            same(choices("hello. how |", "en_US"), ["have", "are", "is"], "sentence boundary")
            for (const input of ["|", "абв|", "foo@he|", "example.he|", "9he|", "_he|", "яhe|",
                                 "he|llo", "he|世", "hell|2", "I don|’t", "hello |word", "hello\n|"])
                same(choices(input, "en_US"), [], "protected position " + input)
            same(apply("hello |", "en_US", "how"), "hello how |", "space at document end")
            same(apply("привет |, друг", "ru_RU", "как"), "привет как|, друг", "preserve punctuation")
            same(apply("hello | world", "en_US", "how"), "hello how| world", "preserve adjacent space")
            same(apply("left he| right", "en_US", "hello"), "left hello| right", "preserve surrounding text")
            same(apply("hello |\n", "en_US", "how"), "hello how |\n", "preserve trailing line break")
            same(choices("hello how |\n", "en_US"), ["are", "have", "is"], "rich text chaining")
            same(apply("hello how |\n", "en_US", "are"), "hello how are |\n", "keep one separator")
            same(Prediction.canAppendSpace("я \n\n", 2), true, "Codex trailing paragraph breaks")
            same(Prediction.canAppendSpace("я \nдальше", 2), false, "text beyond a true newline")
            same(apply("hello how |\n\n", "en_US", "are"), "hello how are |\n\n", "keep both line breaks")
            same(apply("hello |\nnext", "en_US", "how"), "hello how|\nnext", "leave internal newline alone")
            same(apply("я |\n\n", "ru_RU", "могу").replace("|", "п|"), "я могу п|\n\n", "manual typing after choice")
            same(Prediction.fieldBlocked(Qt.ImhNoPredictiveText, "", "", 3, 3), false, "normal Plasma hint")
            for (const hint of [Qt.ImhHiddenText, Qt.ImhSensitiveData, Qt.ImhDigitsOnly,
                                 Qt.ImhEmailCharactersOnly, Qt.ImhUrlCharactersOnly])
                same(Prediction.fieldBlocked(hint, "", "", 2, 2), true, "protected hint")
            same(Prediction.fieldBlocked(0, "x", "", 2, 2), true, "selection")
            same(Prediction.fieldBlocked(0, "", "", 3, 2), true, "selection range")
            same(Prediction.fieldBlocked(0, "", "x", 2, 2), true, "preedit")
        } catch (error) {
            console.error(error.message)
            Qt.exit(1)
            return
        }
        Qt.exit(0)
    }
    Component.onCompleted: Qt.callLater(run)
}
