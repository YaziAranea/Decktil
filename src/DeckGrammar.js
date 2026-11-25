.pragma library

// OpenCorpora cases, encoded by the dictionary generator. Several cases can
// share a spelling, so keep each complete case/gender/number reading together.
var nominative = 1, genitive = 2, dative = 4, accusative = 8, instrumental = 16, prepositional = 32
var locative = 64, partitive = 128
var anyCase = 63
var fixedCase = {
    "без": 2, "безо": 2, "близ": 2, "вдоль": 2, "вместо": 2, "вне": 2,
    "внутри": 2, "возле": 2, "вокруг": 2, "для": 2, "до": 2, "из": 2,
    "изо": 2, "из-за": 2, "из-под": 2, "кроме": 2, "мимо": 2, "напротив": 2,
    "около": 2, "от": 2, "ото": 2, "после": 2, "против": 2, "ради": 2,
    "среди": 2, "у": 2, "нет": 2, "нету": 2,
    "к": 4, "ко": 4, "вопреки": 4, "согласно": 4, "навстречу": 4,
    "про": 8, "сквозь": 8, "через": 8,
    "меж": 16, "между": 16, "над": 16, "перед": 16, "передо": 16,
    "о": 32, "об": 32, "обо": 32, "при": 32
}

// Each line groups a common verb's forms. Homographs such as «стоит» can
// still take several readings, so these are ranking signals for completions.
function verbForms(paradigms) {
    const words = new Set()
    for (const paradigm of paradigms) {
        for (const form of paradigm.replace(/ё/g, "е").split(" "))
            words.add(form)
    }
    return words
}
var placeVerbs = verbForms([
    "жить живу живёшь живёт живём живёте живут жил жила жило жили",
    "работать работаю работаешь работает работаем работаете работают работал работала работало работали",
    "находиться нахожусь находишься находится находимся находитесь находятся находился находилась находилось находились",
    "сидеть сижу сидишь сидит сидим сидите сидят сидел сидела сидело сидели",
    "стоять стою стоишь стоит стоим стоите стоят стоял стояла стояло стояли",
    "лежать лежу лежишь лежит лежим лежите лежат лежал лежала лежало лежали",
    "учиться учусь учишься учится учимся учитесь учатся учился училась училось учились"
])
var directionVerbs = verbForms([
    "идти иду идёшь идёт идём идёте идут иди идите шёл шла шло шли",
    "пойти пойду пойдёшь пойдёт пойдём пойдёте пойдут пойди пойдите пошёл пошла пошло пошли",
    "прийти приду придёшь придёт придём придёте придут приди придите пришёл пришла пришло пришли",
    "уйти уйду уйдёшь уйдёт уйдём уйдёте уйдут уйди уйдите ушёл ушла ушло ушли",
    "войти войду войдёшь войдёт войдём войдёте войдут войди войдите вошёл вошла вошло вошли",
    "ехать еду едешь едет едем едете едут ехал ехала ехало ехали",
    "поехать поеду поедешь поедет поедем поедете поедут поехал поехала поехало поехали",
    "лететь лечу летишь летит летим летите летят летел летела летело летели",
    "направляюсь отправляюсь кладу кладёшь кладёт кладут положу положи положил положила положили",
    "поставь поставил поставила поставили"
])
var dativeVerbs = verbForms([
    "помочь помогу поможешь поможет поможем поможете помогут помоги помогите помог помогла помогло помогли",
    "помогать помогаю помогаешь помогает помогаем помогаете помогают помогай помогайте помогал помогала помогало помогали",
    "доверять доверяю доверяешь доверяет доверяем доверяете доверяют доверяй доверяйте доверял доверяла доверяло доверяли"
])
var instrumentalVerbs = verbForms([
    "гордиться горжусь гордишься гордится гордимся гордитесь гордятся гордился гордилась гордилось гордились",
    "интересоваться интересуюсь интересуешься интересуется интересуемся интересуетесь интересуются",
    "заниматься занимаюсь занимаешься занимается занимаемся занимаетесь занимаются"
])
var accusativeVerbs = verbForms([
    "видеть вижу видишь видит видим видите видят видел видела видело видели",
    "любить люблю любишь любит любим любите любят любил любила любило любили",
    "читать читаю читаешь читает читаем читаете читают читай читайте читал читала читало читали"
])

function governor(word, previous) {
    if (fixedCase[word])
        return fixedCase[word]
    const verb = (previous || "").replace(/ё/g, "е")
    if (/^(в|во|на|под|подо|за)$/.test(word)) {
        const placeCase = /^(в|во|на)$/.test(word) ? prepositional | locative : instrumental
        if (placeVerbs.has(verb))
            return placeCase
        if (directionVerbs.has(verb))
            return accusative
        return placeCase | accusative
    }
    if (word === "с" || word === "со")
        return /^(вместе|рядом|наравне)$/.test(verb) ? instrumental : genitive | instrumental
    if (word === "по")
        return dative | accusative | prepositional
    const direct = word.replace(/ё/g, "е")
    if (dativeVerbs.has(direct))
        return dative
    if (instrumentalVerbs.has(direct))
        return instrumental
    return accusativeVerbs.has(direct) ? accusative : 0
}

function numberRule(word, previous) {
    if (/^[0-9]{1,9}$/.test(word)) {
        const two = Number(word.slice(-2))
        const units = two % 10
        const singular = units === 1 && two !== 11
        const before = governor(previous || "", "")
        if (before && !(before & accusative))
            return {cases: before, number: singular ? "s" : "p", gender: ""}
        if (singular)
            return {cases: nominative | accusative, number: "s", gender: ""}
        const few = units >= 2 && units <= 4 && (two < 12 || two > 14)
        return {cases: genitive, number: few ? "s" : "p", gender: ""}
    }
    if (/^(два|оба|три|четыре)$/.test(word))
        return {cases: genitive, number: "s", gender: /^(два|оба)$/.test(word) ? "mn" : ""}
    if (/^(две|обе)$/.test(word))
        return {cases: genitive, number: "s", gender: "f"}
    if (/^(много|мало|сколько|столько)$/.test(word))
        return {cases: genitive | partitive, number: "", gender: ""}
    if (/^(пять|шесть|семь|восемь|девять|десять|одиннадцать|двенадцать|тринадцать|четырнадцать|пятнадцать|шестнадцать|семнадцать|восемнадцать|девятнадцать|двадцать|тридцать|сорок|пятьдесят|шестьдесят|семьдесят|восемьдесят|девяносто|сто|несколько)$/.test(word))
        return {cases: genitive, number: "p", gender: ""}
    return null
}

function modifiers(word, model) {
    const result = []
    for (const id of model.byWord[word] || []) {
        const family = model.families[id]
        if (family[1] !== "ADJF")
            continue
        for (const form of family[2]) {
            if (form[0] === word)
                result.push(form)
        }
    }
    return result
}

function requirement(history, model) {
    const agreement = []
    for (let i = history.length - 1; i >= 0; --i) {
        const word = history[i]
        const cases = governor(word, history[i - 1])
        if (cases)
            return {cases: cases, number: "", gender: "", modifiers: agreement, counted: false}
        const count = numberRule(word, history[i - 1])
        if (count) {
            // After a numeral the following noun and the adjective can take
            // different cases. Apply the count only to a directly following noun.
            if (!agreement.length)
                return {cases: count.cases, number: count.number, gender: count.gender,
                        modifiers: [], counted: true}
            break
        }
        const readings = modifiers(word, model)
        if (!readings.length)
            break
        agreement.push(readings)
    }
    return agreement.length
        ? {cases: anyCase, number: "", gender: "", modifiers: agreement, counted: false}
        : null
}

function agrees(form, rule) {
    let cases = form[1] & rule.cases
    if (!cases || (rule.number && form[3] !== rule.number)
            || (rule.gender && !rule.gender.includes(form[2])))
        return false
    // The adjective has an ordinary prepositional or genitive form even
    // when the noun has a special locative or partitive ending.
    if (cases & locative)
        cases |= prepositional
    if (cases & partitive)
        cases |= genitive
    for (const alternatives of rule.modifiers) {
        let compatible = 0
        for (const adj of alternatives) {
            if (adj[3] !== form[3] || (adj[2] && form[2] && adj[2] !== form[2]))
                continue
            let matches = adj[1]
            // Masculine singular and plural accusatives distinguish animate
            // (same ending as the genitive) and inanimate (as the nominative).
            if ((adj[3] === "p" || adj[2] === "m") && (matches & accusative)) {
                const type = nominative | genitive
                if ((matches & type) && (form[1] & type) && !(matches & form[1] & type))
                    matches &= ~accusative
            }
            compatible |= matches
        }
        cases &= compatible
        if (!cases)
            return false
    }
    return true
}

function surfaceRank(spelling, word, fallback) {
    const bucket = spelling && spelling[word.slice(0, 2)]
    return (bucket && bucket[word.slice(2)]) || fallback
}

function candidates(history, prefix, words, groups, spelling, model) {
    if (!model || !model.byWord || !model.families)
        return null
    const rule = requirement(history, model)
    if (!rule)
        return null

    const offers = new Map()
    const families = new Map()
    const incompatible = new Set()
    function offer(word, id, score, reading) {
        const seen = offers.get(word)
        if (seen === undefined || score < seen.score)
            offers.set(word, {word: word, id: id, score: score, reading: reading})
    }
    function expand(id, priority) {
        const seen = families.get(id)
        if (seen !== undefined && seen <= priority)
            return
        families.set(id, priority)
        const family = model.families[id]
        if ((rule.counted && family[1] !== "NOUN")
                || (rule.modifiers.length && family[1] !== "NOUN" && family[1] !== "ADJF"))
            return
        const lemmaRank = surfaceRank(spelling, family[0], (id + 1) * 5 + 100)
        const modifierPenalty = rule.modifiers.length && family[1] === "ADJF" ? 1.6 : 1
        const amount = !!(rule.cases & partitive)
        const hasPartitive = amount && family[2].some(form => form[1] & partitive)
        for (const form of family[2]) {
            const word = form[0]
            if (word.length <= prefix.length || !word.startsWith(prefix) || !agrees(form, rule))
                continue
            const rank = surfaceRank(spelling, word, Math.max(lemmaRank * 6, 35000))
            let numberPenalty = form[3] === "p" && !rule.number ? 1.4 : 1
            if (amount && !hasPartitive && form[3] === "s")
                numberPenalty = 3
            const specialCase = form[1] & rule.cases
            const casePenalty = specialCase & locative ? 0.45 : specialCase & partitive ? 1.6 : 1
            // Rare dialectal pronouns inherit extremely frequent lemmas.
            // Their own corpus frequency better represents actual usage.
            const frequency = family[1] === "NPRO" ? rank : Math.pow(lemmaRank, 0.60) * Math.pow(rank, 0.40)
            const score = frequency * modifierPenalty * priority * numberPenalty * casePenalty
            offer(word, id, score, [id, specialCase, form[2], form[3]].join("/"))
        }
    }

    // A matching personal expression or an attested continuation retains its
    // place. A familiar lemma in another case can supply a compatible form.
    for (let g = 0; g < groups.length; ++g) {
        for (let j = 0; j < groups[g].length; ++j) {
            const word = groups[g][j].toLowerCase()
            if (!/^[а-яё]+(?:-[а-яё]+)*$/.test(word))
                continue
            const ids = model.byWord[word] || []
            let compatible = !ids.length
            for (const id of ids) {
                const family = model.families[id]
                if ((!rule.counted || family[1] === "NOUN")
                        && family[2].some(form => form[0] === word && agrees(form, rule)))
                    compatible = true
                expand(id, 0.15 + 0.07 * g + 0.02 * j)
            }
            if (!compatible)
                incompatible.add(word)
            else if (word.startsWith(prefix) && word.length > prefix.length)
                offer(word, ids[0], -1000 + g * 100 + j)
        }
    }
    if (model.companions && rule.modifiers.length) {
        // Use adjective–noun combinations attested in the phrase corpus.
        // Inflect the noun for *this* phrase, even without a typed prefix.
        const adjective = history[history.length - 1]
        for (const id of model.byWord[adjective] || []) {
            const family = model.families[id]
            if (family[1] !== "ADJF")
                continue
            const nouns = model.companions[family[0]] || []
            for (let j = 0; j < nouns.length; ++j) {
                for (const nounId of model.byWord[nouns[j]] || []) {
                    const noun = model.families[nounId]
                    if (noun[1] === "NOUN" && noun[0] === nouns[j])
                        expand(nounId, 0.04 * (j + 1))
                }
            }
        }
    }
    if (prefix.length >= 2) {
        // The word buckets are in corpus frequency order. Expand a bounded
        // set of frequent families, including endings absent from the corpus.
        const bucket = words[prefix.slice(0, 2)] || []
        const stem = prefix.slice(0, Math.max(2, prefix.length - 2))
        for (const start of stem === prefix ? [prefix] : [prefix, stem]) {
            for (const word of bucket) {
                if (word.startsWith(start)) {
                    for (const id of model.byWord[word] || [])
                        expand(id, 1)
                    if (families.size >= 64)
                        break
                }
            }
            if (families.size >= 64)
                break
        }
    }

    // Leave room for another lemma when several endings of one common word
    // are plausible. Direct contextual evidence can still place siblings first.
    const ordered = Array.from(offers.values()).sort((a, b) => a.score - b.score)
    const used = new Set()
    for (const item of ordered) {
        if (item.id !== undefined && used.has(item.id) && item.score > 0)
            item.score *= 2
        used.add(item.id)
    }
    const readings = new Set()
    const best = []
    for (const item of ordered.sort((a, b) => a.score - b.score)) {
        // Equivalent endings («мамой» / «мамою») share one slot. An
        // explicitly attested spelling or personal choice always remains.
        if (item.reading && readings.has(item.reading))
            continue
        readings.add(item.reading)
        best.push(item)
        if (best.length === 3)
            break
    }
    if (!best.length)
        return null
    // A literal inflection supported by its context is stronger evidence
    // than a near spelling of this unfinished stem, even for uncommon endings.
    return {words: best.map(item => item.word), confident: best[0].score < 25000,
            pinned: best.filter(item => item.score < 75000).length,
            incompatible: incompatible}
}
