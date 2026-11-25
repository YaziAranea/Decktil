.pragma library

// The first alternative is the hint and the stationary long-press choice.
// Preserve additional native variants supplied by the installed Qt layouts.
var preferred = {
    ".": "?!…",
    "й": "1", "ц": "2", "у": "3", "к": "4", "е": "ё5",
    "н": "6", "г": "7", "ш": "8", "щ": "9", "з": "0", "х": "(",
    "ф": "@", "ы": "#", "в": "₽", "а": "%", "п": "&",
    "р": "*", "о": "№", "л": "+", "д": "=", "ж": ":", "э": "»",
    "я": "«", "ч": "?", "с": "!", "м": "—-", "и": "/", "т": "…",
    "ь": "ъ", "б": ";", "ю": ")",
    "q": "1", "w": "2", "e": "3éè", "r": "4", "t": "5", "y": "6",
    "u": "7ü", "i": "8í", "o": "9ö", "p": "0",
    "a": "á", "s": "ß", "d": "ď", "f": "&", "g": "ğ", "h": "#",
    "j": "+", "k": "(", "l": ")", "z": "ž", "x": "×", "c": "ç",
    "v": "/", "b": "!", "n": "ñ", "m": "—-"
}

function keys(text, original) {
    if (!preferred[text])
        return original || []
    const result = [text]
    for (const collection of [preferred[text], original || []]) {
        for (const symbol of collection) {
            if (symbol && !result.includes(symbol))
                result.push(symbol)
        }
    }
    return result
}

function hint(text, alternatives, uppercase) {
    for (const symbol of alternatives) {
        if (symbol !== text)
            return uppercase ? symbol.toUpperCase() : symbol
    }
    return ""
}
