"""His people: who he messages and emails, so "tell Wasif I'll be late" goes to the right
chat however it's said or heard ("Vasif", "Waseef"), written the way he writes to them.

For each person: the name he uses, other spellings, their WhatsApp chat's name
("Waseeeeeeef"), their email, who they are ("flatmate") and how he writes to them
("short, casual"). It fills three ways: seeded once from his sent mail, learned when Clixy
opens a chat for a name he said, and told ("remember Wasif's my flatmate", a [PERSON:...]
tag from the answer). Never what anyone wrote, only who they are.

Kept in the memory folder (people.json), only while Settings > Privacy keeps history.
"""

from __future__ import annotations

import datetime as dt
import difflib
import json
import re
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Person:
    name: str                                   # what he calls them: "Wasif"
    aliases: list = field(default_factory=list)  # other spellings: "Vasif", "Waseef"
    whatsapp: str = ""                          # their chat's name in WhatsApp: "Waseeeeeeef"
    email: str = ""
    who: str = ""                               # "flatmate", "lecturer for robotics"
    style: str = ""                             # how he writes to them: "short, casual, lowercase"
    seen: int = 0
    last: str = ""


def squash(text: str) -> str:
    """A name as it sounds: lowercase letters, repeats collapsed ("Waseeeeeeef" -> "wasef")."""
    letters = re.sub(r"[^a-z]", "", (text or "").lower())
    return re.sub(r"(.)\1+", r"\1", letters)


def alike(a: str, b: str) -> float:
    """How alike two names sound, 0 to 1 ("Vasif" ~ "Wasif" ~ "Waseeeeeeef")."""
    x, y = squash(a), squash(b)
    if not x or not y:
        return 0.0
    if x == y:
        return 1.0
    x, y = x.replace("v", "w"), y.replace("v", "w")       # Whisper hears the one as the other
    return difflib.SequenceMatcher(None, x, y).ratio()


_TIME = re.compile(r"\s+(?:\d{1,2}[:.]\d{2}\s?(?:[ap]\.?m\.?)?|yesterday|today|monday|tuesday|wednesday|thursday|"
                   r"friday|saturday|sunday|\d{1,2}/\d{1,2}/\d{2,4})\b.*$", re.IGNORECASE)
_NOT_NAMES = {"the", "and", "for", "you", "can", "his", "her", "him", "say", "tell", "message", "text", "send",
              "email", "whatsapp", "chat", "open", "find", "locate", "what", "plan", "today", "day", "that", "with",
              "about", "from", "this", "then", "onto", "into", "chrome", "web", "hey", "clixy", "please", "my", "go",
              "late", "there", "here", "now", "our", "your", "their", "them", "they", "will", "i'll", "i'm"}


def chat_name(element_name: str) -> str:
    """A chat list item's name without its time and last message:
    "Waseeeeeeef 12:30 PM k" -> "Waseeeeeeef"."""
    return _TIME.sub("", " ".join((element_name or "").split())).strip()


class PeopleBook:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.people: list[Person] = []
        self._lock = threading.Lock()

    @classmethod
    def load(cls, path: Path) -> "PeopleBook":
        book = cls(path)
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
            fields = set(Person.__dataclass_fields__)
            book.people = [Person(**{k: v for k, v in row.items() if k in fields})
                           for row in rows if isinstance(row, dict) and row.get("name")]
        except (OSError, ValueError, TypeError):
            pass
        return book

    def save(self) -> None:
        if self.path is None:
            return
        with self._lock:
            rows = [asdict(p) for p in self.people]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    # --- Finding them -------------------------------------------------------------------------------

    def find(self, said: str, at_least: float = 0.78) -> Person | None:
        """The person ``said`` names, however it was spelt or heard; None when nobody's close."""
        best, score = None, 0.0
        for person in self.people:
            for name in [person.name, *person.aliases, person.whatsapp,
                         person.email.split("@")[0] if person.email else ""]:
                if not name:
                    continue
                first = name.split()[0]
                s = max(alike(said, name), alike(said, first) if len(said.split()) == 1 else 0.0)
                if s > score:
                    best, score = person, s
        return best if score >= at_least else None

    def mentioned(self, text: str) -> list[Person]:
        """Everyone named in what he said ("tell Vasif..." -> Wasif)."""
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]+", text or "") if w.lower() not in _NOT_NAMES]
        words = [w[:-2] if w.lower().endswith("'s") else w for w in words]
        found: list[Person] = []
        for chunk in [*(" ".join(words[i:i + 2]) for i in range(len(words) - 1)), *words]:
            if len(squash(chunk)) < 3:
                continue
            person = self.find(chunk)
            if person is not None and person not in found:
                found.append(person)
        return found

    # --- Learning them ------------------------------------------------------------------------------

    def remember(self, name: str, **facts) -> Person:
        """Add someone, or add to what's known ("who", "email", "whatsapp", "style", an alias)."""
        now = dt.datetime.now().isoformat(timespec="minutes")
        with self._lock:
            person = self.find(name, at_least=0.86) if self.people else None
            if person is None:
                person = Person(name=name.strip())
                self.people.append(person)
            for key in ("whatsapp", "email", "who", "style"):
                value = (facts.get(key) or "").strip()
                if value:
                    setattr(person, key, value)
            for alias in [facts.get("alias") or "", name]:
                alias = alias.strip()
                if alias and squash(alias) != squash(person.name) and alias not in person.aliases:
                    person.aliases.append(alias)
            person.seen += 1
            person.last = now
        return person

    def learn_chat(self, goal: str, element_name: str) -> Person | None:
        """Clixy opened a chat while doing ``goal``: if its name is a person the goal named
        (said "Wasif", opened "Waseeeeeeef"), that's their chat from now on."""
        chat = chat_name(element_name)
        if not chat or len(chat) > 40:
            return None
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]+", goal or "") if w.lower() not in _NOT_NAMES]
        words = [w[:-2] if w.lower().endswith("'s") else w for w in words]
        chunks = [*(" ".join(words[i:i + 2]) for i in range(len(words) - 1)), *words]
        best, score = "", 0.0
        for chunk in chunks:
            s = max(alike(chunk, chat), alike(chunk, chat.split()[0]) if " " not in chunk else 0.0)
            if s > score:
                best, score = chunk, s
        if score < 0.75:
            return None
        known = self.find(best) or self.find(chat)
        return self.remember(known.name if known else best, whatsapp=chat, alias=best)

    # --- Telling the models -------------------------------------------------------------------------

    def line(self, p: Person) -> str:
        bits = [p.who] if p.who else []
        if p.whatsapp:
            bits.append(f'whatsapp chat "{p.whatsapp}"')
        if p.email:
            bits.append(p.email)
        if p.style:
            bits.append(f"he writes to them: {p.style}")
        if p.aliases:
            bits.append("also heard as " + ", ".join(p.aliases[:3]))
        return p.name + (f" ({'; '.join(bits)})" if bits else "")

    def summary(self, said: str = "", top: int = 8) -> str:
        """Who he means in ``said``, in full, then his other go-to people by name."""
        named = self.mentioned(said) if said else []
        others = sorted((p for p in self.people if p not in named), key=lambda p: -p.seen)[:top]
        parts = []
        if named:
            parts.append("the people he means: " + "; ".join(self.line(p) for p in named))
        if others:
            parts.append("his other go-to people: " + ", ".join(f"{p.name} ({p.who})" if p.who else p.name
                                                                for p in others))
        return ". ".join(parts)


# --- What he tells it -------------------------------------------------------------------------------

_PERSON_TAG = re.compile(r"\[\s*PERSON\s*:\s*([^\]|]+?)\s*((?:\|[^\]]*)?)\]", re.IGNORECASE)

PROMPT = ("- when they tell you about someone in their life (who they are, their email, their whatsapp name, how "
          "they write to them), keep it: add [PERSON:name|who:flatmate|email:x@y.com|whatsapp:chat name|"
          "style:short and casual] with only what they said, after your reply. use the people you're told "
          "about by name; never ask them for details you could look up on screen.")


def parse_tags(text: str) -> tuple[str, list[tuple[str, dict]]]:
    """[PERSON:Wasif|who:flatmate] tags out of a reply: the text without them, and (name, facts)."""
    found = []
    for m in _PERSON_TAG.finditer(text or ""):
        facts = {}
        for part in m.group(2).split("|"):
            key, _, value = part.partition(":")
            if key.strip().lower() in ("who", "email", "whatsapp", "style", "alias") and value.strip():
                facts[key.strip().lower()] = value.strip()
        found.append((m.group(1).strip(), facts))
    return _PERSON_TAG.sub("", text or "").strip(), found


# --- The one book ------------------------------------------------------------------------------------

_book: PeopleBook | None = None


def _path() -> Path:
    try:
        from config import MEMORY_DIR
        return Path(MEMORY_DIR) / "people.json"
    except Exception:
        return Path.home() / ".clixy" / "memory" / "people.json"


def _keeping() -> bool:
    try:
        from config import resolve_setting
        return resolve_setting("KEEP_HISTORY", default="on").lower() == "on"
    except Exception:
        return True


def current() -> PeopleBook:
    global _book
    if _book is None:
        _book = PeopleBook.load(_path())
    return _book


def summary(said: str = "") -> str:
    """What the models get about his people; "" while history is off."""
    return current().summary(said) if _keeping() else ""


def learn_chat(goal: str, element_name: str) -> None:
    if _keeping() and current().learn_chat(goal, element_name) is not None:
        current().save()


def keep_told(text: str) -> str:
    """Save what the reply's [PERSON:...] tags say; returns the reply without them."""
    rest, told = parse_tags(text)
    if told and _keeping():
        for name, facts in told:
            current().remember(name, **facts)
        current().save()
    return rest


def forget() -> None:
    """Forget history: start an empty book (the file itself is deleted with the memory folder)."""
    global _book
    _book = PeopleBook(_path())
