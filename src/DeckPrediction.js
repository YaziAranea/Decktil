.pragma library
.import "DeckGrammar.js" as Grammar

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

function precedingWords(text, pos, russian, limit, crossComma) {
    const result = []
    for (let n = 0; n < limit && pos > 0; ++n) {
        const beforeSpace = pos
        while (pos > 0 && horizontalSpace(text.charAt(pos - 1)))
            --pos
        if (beforeSpace === pos)
            break
        // A conversational comma can join words in the same clause.
        if (crossComma && text.charAt(pos - 1) === ",")
            --pos
        const end = pos
        // Numeric counts inform Russian agreement locally. Keep them out of
        // phrase lookups, and do not read parts of IDs, decimals or times.
        if (russian && !crossComma && /[0-9]/.test(text.charAt(pos - 1))) {
            while (pos > 0 && end - pos < 9 && /[0-9]/.test(text.charAt(pos - 1)))
                --pos
            const before = text.charAt(pos - 1)
            if (!boundary(before) || /[.,:]/.test(before))
                break
            result.unshift(text.slice(pos, end))
            continue
        }
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
    const previous = precedingWords(text, start, russian, 2, true)
    if (!prefix && !previous.length)
        return null
    // Keep the longer grammatical context inside the local prediction engine.
    const history = russian ? precedingWords(text, start, true, 6, false) : []
    return { prefix: prefix, previous: previous, russian: russian, history: history }
}

function lookup(table, key) {
    return Object.prototype.hasOwnProperty.call(table, key) ? table[key] : []
}

function rankOf(spelling, word) {
    const bucket = spelling[word.slice(0, 2)]
    const rank = bucket && bucket[word.slice(2)]
    return typeof rank === "number" ? rank : 0
}

function withCase(word, prefix) {
    if (prefix.includes("’"))
        word = word.replace(/'/g, "’")
    if (prefix.length > 1 && prefix === prefix.toUpperCase())
        return word.toUpperCase()
    if (prefix[0] !== prefix[0].toLowerCase() || /^i['’]/.test(word))
        return word[0].toUpperCase() + word.slice(1)
    return word
}

// Generate the few hundred strings one ordinary typing error away. The
// prebuilt rank table gives a constant-time lookup without scanning the dictionary.
function visitEdits(prefix, russian, keep) {
    const alphabet = russian ? "абвгдеёжзийклмнопрстуфхцчшщъыьэюя-" : "abcdefghijklmnopqrstuvwxyz'-"
    for (let i = 0; i <= prefix.length; ++i) {
        const left = prefix.slice(0, i)
        const right = prefix.slice(i)
        const ch = right.charAt(0)
        const rest = right.slice(1)
        const last = prefix.charAt(i - 1)
        if (right) {
            keep(left + rest, ch === last ? 1.1 : 2)
            if (right.length > 1 && ch !== right[1])
                keep(left + right[1] + ch + right.slice(2), 1)
        }
        for (let j = 0; j < alphabet.length; ++j) {
            const letter = alphabet[j]
            if (letter !== ch && right)
                keep(left + letter + rest, 3)
            if (letter !== last)
                keep(left + letter + right, letter === ch ? 1.1 : 2)
        }
    }
}

function corrections(ctx, prefix, words, spelling, priorityGroups) {
    const typedRank = rankOf(spelling, prefix)
    // Valid everyday words are left alone. Low-frequency correct spellings,
    // names and common misspellings can coexist with a much likelier form.
    if (typedRank && typedRank <= 8000)
        return []
    const variants = Object.create(null)
    const matches = new Map()
    function consider(word, rank, cost) {
        if (word.startsWith(prefix) || (typedRank && rank * 8 > typedRank))
            return
        const score = (rank + 80) * cost
        const previous = matches.get(word)
        if (previous === undefined || score < previous)
            matches.set(word, score)
    }
    visitEdits(prefix, ctx.russian, (word, cost) => {
        if (variants[word] !== undefined && variants[word] <= cost)
            return
        variants[word] = cost
        const rank = rankOf(spelling, word)
        if (rank)
            consider(word, rank, cost)
    })

    function considerLonger(word, rank) {
        // Also finish a mistyped prefix. A distant suffix has weaker evidence.
        for (let size = prefix.length - 1; size <= prefix.length + 1; ++size) {
            const extra = word.length - size
            if (extra <= 0 || extra > 7)
                continue
            const cost = variants[word.slice(0, size)]
            if (cost !== undefined)
                consider(word, rank, cost * (3 + 0.5 * extra))
        }
    }
    if (prefix.length >= 4) {
        // The buckets retain corpus frequency order. A bounded scan finds
        // common continuations even when the first two letters are transposed.
        const heads = [prefix.slice(0, 2)]
        if (prefix[0] !== prefix[1])
            heads.push(prefix[1] + prefix[0])
        for (const head of heads) {
            const bucket = lookup(words, head)
            for (let i = 0; i < Math.min(240, bucket.length); ++i) {
                const word = bucket[i]
                if (!word.startsWith(prefix))
                    considerLonger(word, rankOf(spelling, word) || 100000)
            }
        }
    }
    for (const group of priorityGroups) {
        for (const item of group) {
            const word = canonical(item)
            if (!validWord(word, ctx.russian, false) || word.startsWith(prefix))
                continue
            const rank = Math.min(rankOf(spelling, word) || 1500, 1500) * 0.45
            const cost = variants[word]
            if (cost !== undefined)
                consider(word, rank, cost)
            else if (prefix.length >= 4)
                considerLonger(word, rank)
        }
    }
    return Array.from(matches, pair => ({word: pair[0], score: pair[1]}))
                .sort((a, b) => a.score - b.score || a.word.localeCompare(b.word)).slice(0, 3)
}

function suggestions(ctx, words, bigram, trigram, personal, spelling, morphology) {
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
    const priorityGroups = groups.slice()
    const grammatical = ctx.russian && morphology
        ? Grammar.candidates(ctx.history || ctx.previous, prefix, words, groups, spelling, morphology) : null
    if (grammatical) {
        if (grammatical.confident)
            groups.length = 0
        groups.unshift(grammatical.words)
    }
    const contextual = groups.length
    if (prefix.length >= 2 && !(grammatical && grammatical.confident))
        groups.push(lookup(words, prefix.slice(0, 2)))
    let pinned = 0
    exact: for (let g = 0; g < groups.length; ++g) {
        const group = groups[g]
        for (let i = 0; i < group.length; ++i) {
            const word = group[i]
            // Generated dictionary entries are already normalized. Most words
            // in a two-letter bucket do not match the rest of this prefix.
            const lower = g === contextual ? word : canonical(word)
            if (lower.length <= prefix.length || !lower.startsWith(prefix) || seen.indexOf(lower) >= 0
                    || (g > 0 && grammatical && grammatical.incompatible.has(lower))
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
            if (g < contextual && (g > 0 || !grammatical || i < grammatical.pinned))
                pinned = result.length
            if (result.length === 3)
                break exact
        }
    }
    if ((grammatical && grammatical.confident) || !spelling || prefix.length < 3
            || prefix.length > 32 || pinned === 3)
        return result
    if (result.length === 3 && result.every(word => {
        const rank = rankOf(spelling, canonical(word))
        return rank > 0 && rank < 2000
    }))
        return result
    const nearby = corrections(ctx, prefix, words, spelling, priorityGroups)
    if (!nearby.length)
        return result
    // Retain contextual and competitive literal completions, leaving room for
    // a strong correction when the only literal hits are obscure names/forms.
    while (pinned < result.length) {
        const rank = rankOf(spelling, canonical(result[pinned]))
        if (rank && rank > 4 * nearby[0].score)
            break
        ++pinned
    }
    const leading = result.slice(0, pinned)
    const slots = Math.min(3 - pinned, result.length > pinned ? 2 : 3)
    for (const match of nearby.slice(0, slots))
        leading.push(withCase(match.word, ctx.prefix))
    return leading.concat(result.slice(pinned)).slice(0, 3)
}

function insertion(ctx, candidate, atEnd) {
    if (!ctx || !candidate || !candidate.startsWith(ctx.prefix) || candidate.length <= ctx.prefix.length)
        return ""
    return candidate.slice(ctx.prefix.length) + (atEnd ? " " : "")
}

function edit(ctx, candidate, atEnd) {
    if (!ctx || !candidate || candidate === ctx.prefix || !validWord(candidate, ctx.russian, false))
        return null
    const suffix = insertion(ctx, candidate, atEnd)
    if (suffix)
        return { text: suffix, replaceFrom: 0, replaceLength: 0 }
    // Both offsets use Qt's UTF-16 units relative to the editor's cursor.
    const count = ctx.prefix.length
    return { text: candidate + (atEnd ? " " : ""), replaceFrom: -count, replaceLength: count }
}
