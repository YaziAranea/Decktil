import QtQml
import "../src/DeckPrediction.js" as Prediction
import "../src/DeckLearning.js" as Learning

QtObject {
    function equal(actual, expected, message) {
        if (JSON.stringify(actual) !== JSON.stringify(expected))
            throw new Error(message + ": expected " + JSON.stringify(expected) +
                            ", got " + JSON.stringify(actual))
    }

    function dictionary(ranked) {
        const words = {}, spelling = {}
        for (let i = 0; i < ranked.length; ++i) {
            const word = ranked[i], key = word.slice(0, 2)
            if (!words[key]) {
                words[key] = []
                spelling[key] = {}
            }
            words[key].push(word)
            spelling[key][word.slice(2)] = i + 1
        }
        return { words: words, spelling: spelling }
    }

    function run() {
        const en = dictionary(["hello", "help", "receive", "from", "form", "go"])
        const ru = dictionary(["привет", "словарь", "утро"])
        function suggest(text, locale, data, personal, next) {
            const ctx = Prediction.context(text, text.length, locale)
            return Prediction.suggestions(ctx, data.words, next || {}, {}, personal || [], data.spelling)
        }
        for (const [lang, data, cases] of [
            ["ru_RU", ru, [["пивет", "привет"], ["приввет", "привет"], ["приает", "привет"],
                           ["рпивет", "привет"], ["Првиет", "Привет"], ["ПРВИЕТ", "ПРИВЕТ"],
                           ["први", "привет"]]],
            ["en_US", en, [["hllo", "hello"], ["helllo", "hello"], ["heklo", "hello"],
                           ["ehllo", "hello"], ["Recieve", "Receive"], ["RECIEVE", "RECEIVE"],
                           ["recie", "receive"]]]
        ]) {
            for (const [typed, expected] of cases) {
                const found = suggest(typed, lang, data)
                equal(found.includes(expected), true, typed + " in " + JSON.stringify(found))
                equal(found.length <= 3, true, "suggestion strip limit")
            }
        }
        equal(suggest("from", "en_US", en), [], "common correctly spelled word")
        equal(suggest("og", "en_US", en), [], "two letters are insufficient for a correction")
        equal(suggest("hel", "en_US", en, ["helmet"]).slice(0, 3), ["helmet", "hello", "help"],
              "personal and direct completions keep their order")
        equal(suggest("nerlux", "en_US", en, ["merlux"]), ["merlux"],
              "correct to a learned word absent from the shipped dictionary")
        equal(suggest("доброе ", "ru_RU", ru, [], {"доброе":["утро"]}), ["утро"],
              "space shows a continuation")
        equal(suggest("user@heklo", "en_US", en), [], "email address")
        equal(suggest("сhllo", "en_US", en), [], "mixed alphabets")

        function choose(input, lang, word) {
            const pos = input.indexOf("|")
            const text = input.replace("|", "")
            const ctx = Prediction.context(text, pos, lang)
            const edit = Prediction.edit(ctx, word, Prediction.canAppendSpace(text, pos))
            const from = pos + edit.replaceFrom
            return text.slice(0, from) + edit.text + "|" + text.slice(from + edit.replaceLength)
        }
        equal(choose("🔔 Првиет|, друг", "ru_RU", "Привет"), "🔔 Привет|, друг",
              "replace only the current token before punctuation")
        equal(choose("well, hllo| there", "en_US", "hello"), "well, hello| there",
              "leave the space and following word intact")
        equal(choose("please recieve|", "en_US", "receive"), "please receive |",
              "correction at document end")
        equal(choose("please rec|", "en_US", "receive"), "please receive |",
              "partial-word completion still inserts only the suffix")
        equal(Prediction.edit(null, "hello", false), null, "invalid edit context")

        const state = Learning.start("", 0)
        for (const ch of "say hllo")
            Learning.queue(state, ch, "", false)
        equal(Learning.confirm(state, "say hllo", 8, "en_US"), [{word:"say", previous:[]}],
              "unconfirmed misspelling stays pending")
        Learning.queue(state, "hello ", "hello", false, 4)
        equal(Learning.confirm(state, "say hllo", 8, "en_US"), null,
              "client has not yet accepted the replacement")
        equal(Learning.confirm(state, "say hello ", 10, "en_US"), [{word:"hello", previous:["say"]}],
              "learn corrected word once, with prior context")
        equal(Learning.confirm(state, "say hello ", 10, "en_US"), null,
              "duplicate native acknowledgment")

        const batched = Learning.start("", 0)
        for (const ch of "пивет")
            Learning.queue(batched, ch, "", false)
        Learning.queue(batched, "привет ", "привет", false, 5)
        equal(Learning.confirm(batched, "привет ", 7, "ru_RU"),
              [{word:"привет", previous:[]}], "correction coalesced with its original keystrokes")
    }

    Component.onCompleted: Qt.callLater(function() {
        try { run() }
        catch (err) { console.error(err.message); Qt.exit(1); return }
        Qt.exit(0)
    })
}
