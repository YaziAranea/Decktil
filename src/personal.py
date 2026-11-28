"""Local, opt-in word and phrase frequencies for the Decktil keyboard."""
from contextlib import closing
import os
from pathlib import Path
import re
import sqlite3
from uuid import uuid4


MAX_ENTRIES_PER_LANGUAGE = 12000
WORD = {
    "en": re.compile(r"[a-z]+(?:['-][a-z]+)*"),
    "ru": re.compile(r"[а-яё]+(?:-[а-яё]+)*"),
}


def _path():
    data_home = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(data_home) / "decktil" / "personal.sqlite3"


def _open(path, create=False):
    if create:
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if path.parent.is_symlink():
            raise OSError("Unexpected personal dictionary directory")
        path.parent.chmod(0o700)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
    if path.is_symlink() or path.parent.is_symlink():
        raise OSError("Unexpected personal dictionary path")
    # mode=rw avoids accidentally recreating a missing database during a query.
    return sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True, timeout=.15)


def _init(db):
    db.execute("""CREATE TABLE IF NOT EXISTS settings (
        id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL, generation TEXT NOT NULL)""")
    db.execute("""INSERT OR IGNORE INTO settings (id, enabled, generation) VALUES (1, 0, '')""")
    db.execute("""CREATE TABLE IF NOT EXISTS entries (
        lang TEXT NOT NULL, prev2 TEXT NOT NULL, prev1 TEXT NOT NULL,
        word TEXT NOT NULL, uses INTEGER NOT NULL,
        UNIQUE (lang, prev2, prev1, word))""")


def set_enabled(value):
    """Pausing leaves learned counts intact. A later session has a different generation."""
    path = _path()
    if not value and not path.exists():
        return
    with closing(_open(path, create=True)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        _init(db)
        db.execute("""UPDATE settings SET enabled=?, generation=?
                      WHERE id=1 AND enabled!=?""", (bool(value), uuid4().hex, bool(value)))


def clear():
    """Erase learned entries for both languages and invalidate queued word submissions."""
    path = _path()
    if not path.exists():
        return
    with closing(_open(path)) as db, db:
        db.execute("PRAGMA secure_delete=ON")
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM entries")
        db.execute("UPDATE settings SET generation=? WHERE id=1", (uuid4().hex,))


def _session(db):
    row = db.execute("SELECT generation FROM settings WHERE id=1 AND enabled=1").fetchone()
    return row[0] if row else ""


def session():
    path = _path()
    if not path.is_file():
        return ""
    try:
        with closing(_open(path)) as db:
            return _session(db)
    except (OSError, sqlite3.Error):
        return ""


def enabled():
    return bool(session())


def _language(locale):
    return locale.lower().replace("-", "_").split("_", 1)[0]


def _word(value, lang):
    value = value.replace("’", "'").lower()
    return value if 0 < len(value) <= 32 and WORD[lang].fullmatch(value) else ""


def learn(generation, locale, prev2, prev1, word):
    lang = _language(locale)
    path = _path()
    if lang not in WORD or not generation or not path.is_file():
        return
    word = _word(word, lang)
    if not word:
        return
    prev1 = _word(prev1, lang)
    prev2 = _word(prev2, lang) if prev1 else ""
    contexts = [("", "")]
    if prev1:
        contexts.append(("", prev1))
    if prev2:
        contexts.append((prev2, prev1))
    with closing(_open(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if generation != _session(db):
            return
        created = False
        for a, b in contexts:
            count = db.execute("""INSERT INTO entries VALUES (?, ?, ?, ?, 1)
                ON CONFLICT (lang, prev2, prev1, word) DO UPDATE SET uses=MIN(uses+1, 65535)
                RETURNING uses""", (lang, a, b, word)).fetchone()[0]
            created = created or count == 1
        if created:
            size = db.execute("SELECT COUNT(*) FROM entries WHERE lang=?", (lang,)).fetchone()[0]
            if size > MAX_ENTRIES_PER_LANGUAGE:
                # Remove infrequent, older entries in batches to avoid pruning on every new word.
                db.execute("""DELETE FROM entries WHERE rowid IN (
                    SELECT rowid FROM entries WHERE lang=? ORDER BY uses, rowid LIMIT ?)""",
                           (lang, max(size - MAX_ENTRIES_PER_LANGUAGE, MAX_ENTRIES_PER_LANGUAGE // 20)))


def _one_edit(left, right):
    """A single missing, extra, changed or transposed letter."""
    if left == right or abs(len(left) - len(right)) > 1:
        return False
    if len(left) > len(right):
        left, right = right, left
    pos = next((i for i, ch in enumerate(left) if ch != right[i]), len(left))
    if len(left) != len(right):
        return left[pos:] == right[pos + 1:]
    return (left[pos + 1:] == right[pos + 1:] or
            (left[pos:pos + 2] == right[pos:pos + 2][::-1] and left[pos + 2:] == right[pos + 2:]))


def suggestions(locale, prev2, prev1, prefix):
    lang = _language(locale)
    path = _path()
    if lang not in WORD or not path.is_file():
        return []
    prefix = prefix.lower().replace("’", "'")
    if prefix and (len(prefix) > 32 or not _word(prefix, lang)
                   and not (lang == "en" and prefix.endswith("'") and _word(prefix[:-1], lang))):
        return []
    prev1 = _word(prev1, lang)
    prev2 = _word(prev2, lang) if prev1 else ""
    contexts = []
    if prev2:
        contexts.append((prev2, prev1))
    if prev1:
        contexts.append(("", prev1))
    if prefix:
        contexts.append(("", ""))
    if not contexts:
        return []
    with closing(_open(path)) as db:
        db.execute("BEGIN")
        if not _session(db):
            return []
        result = []
        for a, b in contexts:
            # The unique index covers language, context and this prefix range.
            rows = db.execute("""SELECT word FROM entries
                WHERE lang=? AND prev2=? AND prev1=? AND word>? AND word<?
                ORDER BY uses DESC, rowid DESC LIMIT 3""", (lang, a, b, prefix, prefix + "\U0010ffff"))
            for (word,) in rows:
                if word not in result:
                    result.append(word)
                if len(result) == 3:
                    break
            if len(result) == 3:
                break
        if len(prefix) >= 3:
            # With at least three input letters a single edit preserves at
            # least one end. Keep the Python matcher off unrelated entries.
            db.create_function("one_edit", 2, _one_edit, deterministic=True)
            close = []
            for a, b in contexts:
                rows = db.execute("""SELECT word FROM entries
                    WHERE lang=? AND prev2=? AND prev1=?
                      AND length(word) BETWEEN ? AND ? AND (word GLOB ? OR word GLOB ?)
                      AND word NOT GLOB ? AND one_edit(word, ?)
                    ORDER BY uses DESC, rowid DESC LIMIT 3""",
                    (lang, a, b, len(prefix)-1, len(prefix)+1,
                     prefix[0]+"*", "*"+prefix[-1], prefix+"*", prefix))
                for (word,) in rows:
                    if word not in close:
                        close.append(word)
                    if len(close) == 3:
                        break
                if len(close) == 3:
                    break
            result.extend(close)
        return result
