import QtQml
import "DeckPrediction.js" as Prediction
import "DeckWords.js" as Dictionary
import "DeckNext.js" as Next
import "DeckSpelling.js" as Spelling
import "DeckMorphData.js" as Morph

QtObject {
    function suggest(text, model, personal) {
        const ctx = Prediction.context(text, text.length, "ru_RU")
        return Prediction.suggestions(ctx, Dictionary.words, Next.bigram, Next.trigram, personal || [], Spelling.spelling, model)
    }

    function run() {
        if (!Morph.model)
            throw new Error("Russian morphology model is unavailable")
        const cases = [
            ["к новому др", "другу"], ["без горячего ча", "чая"],
            ["о новой игр", "игре"], ["перед важной встр", "встречей"],
            ["нет врем", "времени"], ["новая ид", "идея"],
            ["новое ок", "окно"], ["новые игр", "игры"],
            ["две иг", "игры"], ["пять иг", "игр"], ["помочь сест", "сестре"],
            ["иду в школ", "школу"], ["работаю в школ", "школе"],
            ["к этой нов", "новой"], ["эти нов", "новые"], ["22 стул", "стула"],
            ["перед важной ", "встречей"], ["без горячей ", "воды"],
            ["о горячем ча", "чае"], ["живу в лес", "лесу"],
            ["о густом лес", "лесе"]
        ]
        for (const [text, word] of cases) {
            const row = suggest(text, Morph.model)
            if (row.length > 3 || !row.includes(word))
                throw new Error(text + " should offer " + word + ", got " + JSON.stringify(row))
        }
        const learned = suggest("к новому др", Morph.model, ["друга"])
        if (learned[0] !== "другу")
            throw new Error("A learned ending overrode the explicit dative: " + JSON.stringify(learned))
        if (suggest("о горячем ча", Morph.model).includes("чаю"))
            throw new Error("The second locative is incompatible with о")
        for (const text of ["првиет", "прив", "к новому. прив", "без\nприв"]) {
            if (JSON.stringify(suggest(text, Morph.model)) !== JSON.stringify(suggest(text, null)))
                throw new Error("Unrelated or absent context changed " + text)
        }
    }

    Component.onCompleted: Qt.callLater(function() {
        try { run() }
        catch (error) { console.error(error.message); Qt.exit(1); return }
        Qt.exit(0)
    })
}
