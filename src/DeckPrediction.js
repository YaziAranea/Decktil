.pragma library

// The Qt JavaScript engine on SteamOS does not support Unicode property
// escapes. Explicit token boundaries also keep mixed scripts, IDs and URLs
// from being mistaken for ordinary words.
function wordChar(ch) {
    return /[a-zа-яё'’\-]/i.test(ch)
}

function horizontalSpace(ch) {
    return ch === " " || ch === "\t" || ch === "\u00a0"
}

function boundary(ch) {
    return ch === "" || /[\s.,;:!?…()\[\]{}«»“”„"—–]/.test(ch)
}

function canAppendSpace(text, pos) {
    // Rich-text editors can expose trailing empty paragraphs to the input method.
    return pos === text.length || /^[\r\n]+$/.test(text.slice(pos))
}

function wordStart(text, pos) {
    const ch = text.charAt(pos - 1)
    return boundary(ch) && !(ch === "." && wordChar(text.charAt(pos - 2)))
}

function fieldBlocked(hints, selected, preedit, pos, anchor) {
    const blocked = Qt.ImhHiddenText | Qt.ImhSensitiveData | Qt.ImhDigitsOnly
        | Qt.ImhFormattedNumbersOnly | Qt.ImhDialableCharactersOnly
        | Qt.ImhEmailCharactersOnly | Qt.ImhUrlCharactersOnly
    return !!(hints & blocked) || !!selected || !!preedit || pos !== anchor
}

function canonical(word) {
    return word.toLowerCase().replace(/’/g, "'")
}

function validWord(word, russian, partial) {
    if (russian)
        return /^[а-яё]+(?:-[а-яё]+)*$/i.test(word)
    const value = partial ? word.replace(/['’]$/, "") : word
    return /^[a-z]+(?:['’\-][a-z]+)*$/i.test(value)
}

function precedingWords(text, pos, russian) {
    const result = []
    for (let n = 0; n < 2 && pos > 0; ++n) {
        const beforeSpace = pos
        while (pos > 0 && horizontalSpace(text.charAt(pos - 1)))
            --pos
        if (beforeSpace === pos)
            break
        // A conversational comma can join words in the same clause.
        if (text.charAt(pos - 1) === ",")
            --pos
        const end = pos
        while (pos > 0 && end - pos < 64 && wordChar(text.charAt(pos - 1)))
            --pos
        const word = text.slice(pos, end)
        if (!validWord(word, russian, false) || !wordStart(text, pos))
            break
        result.unshift(canonical(word))
    }
    return result
}

function context(text, pos, locale) {
    const russian = locale.startsWith("ru")
    if ((!russian && !locale.startsWith("en")) || pos <= 0 || pos > text.length
        || !boundary(text.charAt(pos)))
        return null
    let start = pos
    while (start > 0 && pos - start < 64 && wordChar(text.charAt(start - 1)))
        --start
    const prefix = text.slice(start, pos)
    if (prefix && (!validWord(prefix, russian, true) || !wordStart(text, start)))
        return null
    const previous = precedingWords(text, start, russian)
    if (!prefix && !previous.length)
        return null
    return { prefix: prefix, previous: previous, russian: russian }
}

function lookup(table, key) {
    return Object.prototype.hasOwnProperty.call(table, key) ? table[key] : []
}

function suggestions(ctx, words, bigram, trigram, personal) {
    if (!ctx)
        return []
    const result = []
    const seen = []
    const prefix = canonical(ctx.prefix)
    const last = ctx.previous.length - 1
    const groups = personal && personal.length ? [personal] : []
    if (last === 1)
        groups.push(lookup(trigram, ctx.previous.join(" ")))
    if (last >= 0)
        groups.push(lookup(bigram, ctx.previous[last]))
    if (prefix.length >= 2)
        groups.push(lookup(words, prefix.slice(0, 2)))
    for (const group of groups) {
        for (const word of group) {
            const lower = canonical(word)
            if (lower.length <= prefix.length || !lower.startsWith(prefix) || seen.indexOf(lower) >= 0
                    || !validWord(word, ctx.russian, false))
                continue
            seen.push(lower)
            if (prefix) {
                const tail = word.slice(prefix.length)
                const allCaps = prefix.length > 1 && ctx.prefix === ctx.prefix.toUpperCase()
                result.push(ctx.prefix + (allCaps ? tail.toUpperCase() : tail))
            } else if (!ctx.russian && (lower === "i" || lower.startsWith("i'"))) {
                result.push("I" + word.slice(1))
            } else {
                result.push(word)
            }
            if (result.length === 3)
                return result
        }
    }
    return result
}

function insertion(ctx, candidate, atEnd) {
    if (!ctx || !candidate || !candidate.startsWith(ctx.prefix) || candidate.length <= ctx.prefix.length)
        return ""
    return candidate.slice(ctx.prefix.length) + (atEnd ? " " : "")
}
