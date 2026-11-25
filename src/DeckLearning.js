.pragma library
.import "DeckPrediction.js" as Prediction

// Keep only explicit keyboard events and the last two confirmed words.
// The editor supplies positions and acknowledgements, never training samples.
function start(text, pos) {
    return { pos: pos, canStart: pos === 0 || /[\s(\[«“"]/.test(text.charAt(pos - 1)),
             word: "", punctuation: "", previous: [], edits: [] }
}

function queue(state, inserted, choice, backspace, replaceLength) {
    if (state.edits.length >= 64)
        return false
    state.edits.push({ text: inserted, choice: choice, erase: backspace, replaceLength: replaceLength || 0 })
    return true
}

function complete(state, russian, records) {
    if (state.canStart && state.word.length <= 32 && Prediction.validWord(state.word, russian, false)) {
        const word = Prediction.canonical(state.word)
        records.push({ word: word, previous: state.previous.slice() })
        state.previous = state.previous.concat([word]).slice(-2)
    } else if (state.word || !state.canStart) {
        state.previous = []
    }
    state.word = ""
}

function apply(state, edit, russian, records) {
    if (edit.erase) {
        if (state.punctuation)
            state.punctuation = state.punctuation.slice(0, -1)
        else if (state.word)
            state.word = state.word.slice(0, -1)
        else {
            state.previous = []
            state.canStart = false
        }
        return
    }
    if (edit.choice) {
        // Selecting a displayed completion explicitly confirms the entire word.
        state.word = edit.choice
        state.canStart = true
        complete(state, russian, records)
        state.punctuation = ""
        state.canStart = /\s$/.test(edit.text)
        return
    }
    const ch = edit.text
    if (/^\s$/.test(ch)) {
        complete(state, russian, records)
        if (/[.!?:;…]/.test(state.punctuation) || /[\n\r]/.test(ch))
            state.previous = []
        state.punctuation = ""
        state.canStart = true
    } else if (/^[.,!?;:…]$/.test(ch) && state.word && state.punctuation.length < 4) {
        // Wait for whitespace. A subsequent letter could be part of an address.
        state.punctuation += ch
    } else if (Prediction.wordChar(ch) && !state.punctuation && state.word.length < 32) {
        state.word += ch
    } else {
        state.word = ""
        state.punctuation = ""
        state.previous = []
        state.canStart = false
    }
}

// Find the prefix of pending keyboard events reflected by the editor. Wayland
// clients can acknowledge multiple key presses together. A deletion can remove
// earlier pending input before the client ever reports the intermediate text.
function acknowledged(state, text, pos) {
    let removed = 0, added = "", matched = 0
    for (let i = 0; i < state.edits.length; ++i) {
        const edit = state.edits[i]
        const erase = edit.erase ? 1 : edit.replaceLength
        if (erase) {
            removed += Math.max(0, erase - added.length)
            added = added.slice(0, Math.max(0, added.length - erase))
        }
        if (!edit.erase)
            added += edit.text
        const left = state.pos - removed
        const trusted = state.word + state.punctuation
        const before = trusted.slice(0, Math.max(0, trusted.length - removed))
        if (left >= before.length && pos === left + added.length && text.slice(left, pos) === added
                && text.slice(left - before.length, left) === before)
            matched = i + 1
    }
    return matched
}

function confirm(state, text, pos, locale) {
    const count = acknowledged(state, text, pos)
    if (!count)
        return null
    const edits = state.edits.splice(0, count)
    const result = []
    for (const edit of edits)
        apply(state, edit, locale.startsWith("ru"), result)
    state.pos = pos
    return result
}
