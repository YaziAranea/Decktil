"""Short-lived prediction context from keys delivered by this panel to one editor."""
import re


WORD = re.compile(r"[A-Za-zА-Яа-яЁё]+(?:[-'’][A-Za-zА-Яа-яЁё]+)*")
TAIL = re.compile(r"[A-Za-zА-Яа-яЁё'’-]+$")


class TypedContext:
    def __init__(self):
        self.field = None
        self.text = ""
        self.revision = 0

    def update(self, field, *, inserted="", action=None):
        previous = self.text
        changed_field = self.field != field
        if field is None or changed_field:
            self.text = ""
        self.field = field
        if field is not None:
            if action == "backspace":
                self.text = self.text[:-1]
            elif action is not None:
                self.text = ""
            elif inserted:
                # Only the last few words are useful for local completions.
                self.text = (self.text + inserted)[-256:]
        if self.text == previous and not (previous and changed_field):
            return False
        self.revision += 1
        return True

    def completion(self, field, revision, prefix, word):
        if (field is None or field != self.field or not self.text
                or type(revision) is not int or revision != self.revision
                or not isinstance(prefix, str) or not isinstance(word, str)
                or not 0 < len(word) <= 64 or not WORD.fullmatch(word)):
            return None
        tail = TAIL.search(self.text)
        if prefix != (tail[0] if tail else "") or (not prefix and not self.text[-1].isspace()):
            return None
        if not word.startswith(prefix) or len(word) <= len(prefix):
            return None
        return word[len(prefix):] + " "
