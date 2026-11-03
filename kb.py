"""Curated knowledge base: user-uploadable per-app docs.

One .md file per app at ``KB_DIR/<app_name>.md``. Match by .exe basename
(sanitized like memory.py, so users see matching filenames in
`~/.clixy/memory/` and the KB folder).
No metadata files, no folder hierarchy, no keyword ranking.

If the file is missing → ``recall()`` returns ``('', '')``. The
pipeline worker checks for empty + skips KB injection, so Claude
proceeds with vision + memory only. This is the "Claude already
knows that software" path.

If the file is over ``KB_RECALL_MAX_CHARS``, tail-truncate (matches
``memory.recall()`` overflow handling).

The user's mental model: drop ``edupack.exe.md`` (or whatever the
.exe basename is, visible in their existing memory folder) into
``KB_DIR``, Clicky picks it up next PTT.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from config import KB_DIR, KB_RECALL_MAX_CHARS


def _sanitize_app_name(app_name: str) -> str:
    """Lowercase + replace filesystem-reserved chars.

    Mirrors ``memory._sanitize_app_name`` exactly so the same
    foreground app yields the same filename in both folders. Users
    can `ls ~/.clixy/memory/` to learn the canonical name
    for their app, then drop the matching file in KB_DIR.
    """
    sanitized = app_name.lower()
    for ch in ":\\/":
        sanitized = sanitized.replace(ch, "_")
    return sanitized


def recall(
    app_name: str,
    kb_dir: Path | None = None,
    max_chars: int = KB_RECALL_MAX_CHARS,
) -> tuple[str, str]:
    """Look up the curated KB for ``app_name``.

    Args:
        app_name: foreground .exe basename, e.g. ``"EDUPACK.EXE"``.
        kb_dir: override the KB folder (test hook). Defaults to
            ``config.KB_DIR``.
        max_chars: tail-truncate limit. Defaults to
            ``config.KB_RECALL_MAX_CHARS``.

    Returns:
        ``(content, sanitized_name)``. ``content`` is the markdown
        body, tail-truncated to ``max_chars`` if the file exceeds
        it. ``sanitized_name`` (e.g. ``"edupack.exe"``) is what
        ``ai.py`` will display in the system-prompt injection
        marker after stripping the .exe suffix.

        Both empty if no file matches, ``app_name`` is blank, or
        ``max_chars <= 0`` (defensive guard: Python's ``text[-0:]``
        otherwise returns the whole string, so a caller passing 0
        from a misconfigured override would get the FULL file).
    """
    if max_chars <= 0:
        return ("", "")
    if not app_name or not app_name.strip():
        return ("", "")

    sanitized = _sanitize_app_name(app_name)
    base_dir = Path(kb_dir) if kb_dir is not None else KB_DIR
    md_path = base_dir / f"{sanitized}.md"
    if not md_path.is_file():
        return ("", "")

    text = md_path.read_text(encoding="utf-8")
    if len(text) > max_chars:
        text = text[-max_chars:]
    return (text, sanitized)


# --- Know-how packs for guide mode --------------------------------------------------
#
# ``knowhow/`` ships one short pack per common app (and windows.md for Windows
# itself): the fastest routes, shortcuts, run commands, URIs and tricks. Each
# starts with a little front matter:
#
#     ---
#     name: task manager
#     apps: taskmgr.exe            the .exe names it covers
#     names: task manager          what people call it
#     words: cpu, processes, ...   words in a goal that mean it's about this app
#     ---
#     - lines before the first heading are always kept
#     ## a heading
#     - bullets, kept when they match the goal
#
# Guide mode gets only what's relevant: the packs for the app the goal is
# about (or the app in front), trimmed to the bullets that match the goal.
# The user's own KB folder extends them: ``taskmgr.exe.md`` (or
# ``taskmgr.md``) there goes first and wins; with ``replace: yes`` in its
# front matter the built-in pack is dropped.

KNOWHOW_DIR = Path(__file__).resolve().parent / "knowhow"

_STOP = set("""a an and are as at be but by can could do does for from get go have help how i i'd i'm in into is it
its let like make me my need of on onto open or our please show so some take that the their them then there these
this to up us use want was we what when where which who why will with would you your up get got way find see""".split())
_SAME = {"cpu": ["processor"], "processor": ["cpu"], "ram": ["memory"], "memory": ["ram"], "wifi": ["wi-fi"],
         "wi-fi": ["wifi"], "screenshot": ["snip", "prtsc"], "battery": ["power"], "display": ["screen"],
         "screen": ["display"], "volume": ["sound"], "sound": ["volume", "audio"], "audio": ["sound"],
         "colour": ["color"], "color": ["colour"], "dark": ["mode"], "frozen": ["end task", "not responding"],
         "uninstall": ["installed apps"], "favourites": ["favorites"], "favorites": ["favourites"],
         "email": ["mail"], "mail": ["email"], "internet": ["network", "wi-fi"], "slow": ["cpu", "memory"]}


@dataclass
class Pack:
    """One know-how pack."""
    id: str
    name: str
    apps: set = field(default_factory=set)
    names: list = field(default_factory=list)
    words: list = field(default_factory=list)
    head: list = field(default_factory=list)          # always kept
    sections: list = field(default_factory=list)      # [(heading, [bullets])]
    replace: bool = False                              # (a user's file) drop the built-in pack
    kind: str = ""                                     # "browser": only one of a kind per goal


def parse_pack(text: str, pack_id: str = "") -> Pack:
    """A pack from its markdown (front matter optional)."""
    meta: dict[str, str] = {}
    body = text.replace("\r\n", "\n")
    if body.startswith("---\n"):
        end = body.find("\n---", 4)
        if end != -1:
            for line in body[4:end].splitlines():
                key, _, value = line.partition(":")
                meta[key.strip().lower()] = value.strip()
            body = body[end + 4:]
    split = lambda key: [w.strip().lower() for w in meta.get(key, "").split(",") if w.strip()]  # noqa: E731
    pack = Pack(id=pack_id, name=meta.get("name", pack_id) or pack_id, apps=set(split("apps")),
                names=split("names"), words=split("words"),
                replace=meta.get("replace", "").lower() in ("yes", "true", "1"),
                kind=meta.get("kind", "").lower())
    heading = None
    for raw in body.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("# "):
            continue
        if line.startswith("## "):
            heading = line[3:].strip()
            pack.sections.append((heading, []))
        elif heading is None:
            pack.head.append(line.strip())
        else:
            pack.sections[-1][1].append(line.strip())
    return pack


_PACKS: dict = {}


def load_packs(folder: Path | None = None) -> dict[str, Pack]:
    """Every pack in the folder by id ("taskmgr"), read once (until a file changes)."""
    folder = Path(folder) if folder is not None else KNOWHOW_DIR
    try:
        files = sorted(folder.glob("*.md"))
        stamp = tuple((f.name, f.stat().st_mtime_ns) for f in files)
    except OSError:
        return {}
    cached = _PACKS.get(str(folder))
    if cached and cached[0] == stamp:
        return cached[1]
    packs = {}
    for f in files:
        try:
            packs[f.stem.lower()] = parse_pack(f.read_text(encoding="utf-8"), f.stem.lower())
        except (OSError, UnicodeDecodeError):
            continue
    _PACKS[str(folder)] = (stamp, packs)
    return packs


_GENERIC = {"settings", "setting", "app", "apps", "windows", "window", "computer", "pc", "laptop", "page"}


def goal_terms(goal: str, ignore: set | frozenset = frozenset()) -> list[tuple[str, float]]:
    """The words of a goal worth matching, each with a weight: 1 for the
    goal's own words, ½ for their everyday twins ("cpu" -> "processor")."""
    words = [w.strip(".,!?'\"") for w in re.findall(r"[a-z0-9][a-z0-9+.'-]*", (goal or "").lower())]
    terms: dict[str, float] = {}
    for w in words:
        if len(w) < 2 or w in _STOP or w in ignore:
            continue
        singular = w[:-1] if len(w) > 4 and w.endswith("s") else ""
        for t, weight in [(w, 1.0), (singular, 1.0), *((s, 0.5) for s in _SAME.get(w, []))]:
            if t and t not in ignore and terms.get(t, 0) < weight:
                terms[t] = weight
    return list(terms.items())


def _hits(text: str, terms: list[tuple[str, float]]) -> float:
    low = text.lower()
    return sum(weight for t, weight in terms if re.search(r"(?<![a-z0-9])" + re.escape(t), low))


def _has(text: str, phrase: str) -> bool:
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text))


def pack_score(pack: Pack, goal: str) -> int:
    """How clearly a goal is about this pack's app: its name counts most."""
    low = (goal or "").lower()
    return 5 * sum(_has(low, n) for n in pack.names) + 2 * sum(_has(low, w) for w in pack.words)


def pick_packs(goal: str, front_exe: str = "", packs: dict | None = None, most: int = 2) -> list[Pack]:
    """The packs a goal needs: the apps it's about (best two, the app in
    front winning a tie, never two of one kind like two browsers), else the
    app in front. windows.md isn't picked here (``knowhow`` adds it)."""
    packs = load_packs() if packs is None else packs
    exe = (front_exe or "").lower()
    apps_only = {k: p for k, p in packs.items() if k != "windows"}
    scored = sorted(((pack_score(p, goal) + (1 if exe and exe in p.apps else 0), k)
                     for k, p in apps_only.items()), reverse=True)
    picked: list[Pack] = []
    for score, k in scored:
        if score < 2 or len(picked) >= most:
            break
        kind = apps_only[k].kind
        if kind and any(p.kind == kind for p in picked):
            continue
        picked.append(apps_only[k])
    if not picked and exe:
        picked = [p for p in apps_only.values() if exe in p.apps][:1]
    return picked


def trim(pack: Pack, goal: str, budget: int = 1100, *, head: bool = True, fill: bool = True,
         least: float = 1.0) -> str:
    """The pack cut down to what the goal needs: its first lines, then the
    bullets that match the goal best (scoring at least ``least``; in their
    original order, under their headings) within ``budget`` characters.
    With nothing matching, the first bullets in order when ``fill``, else
    only the first lines."""
    terms = goal_terms(goal, ignore=_GENERIC | set(pack.names))
    out = [f"[{pack.name}]"] + (list(pack.head) if head else [])
    used = sum(len(line) + 1 for line in out)
    ranked = []
    order = 0
    for si, (heading, bullets) in enumerate(pack.sections):
        bonus = _hits(heading, terms) * 0.5
        for bullet in bullets:
            ranked.append((-(_hits(bullet, terms) + bonus), order, si, bullet))
            order += 1
    matched = [r for r in ranked if -r[0] >= least]
    pool = sorted(matched) if matched else (sorted(ranked, key=lambda r: r[1]) if fill else [])
    chosen = []
    for r in pool:
        cost = len(r[3]) + 1 + (len(pack.sections[r[2]][0]) + 2)
        if used + cost > budget:
            continue
        chosen.append(r)
        used += cost
    last = None
    for _score, _order, si, bullet in sorted(chosen, key=lambda r: r[1]):
        if si != last:
            out.append(pack.sections[si][0] + ":")
            last = si
        out.append(bullet)
    return "\n".join(out)


def _user_pack(pack: Pack, kb_dir: Path | None) -> Pack | None:
    base = Path(kb_dir) if kb_dir is not None else KB_DIR
    for stem in [*sorted(pack.apps), pack.id]:
        path = base / f"{_sanitize_app_name(stem)}.md"
        try:
            if path.is_file():
                return parse_pack(path.read_text(encoding="utf-8"), pack.id)
        except (OSError, UnicodeDecodeError):
            return None
    return None


def knowhow(goal: str, front_exe: str = "", *, folder: Path | None = None, kb_dir: Path | None = None,
            budget: int = 2600) -> str:
    """What the guide should know for ``goal``: the packs for the apps it's
    about (or the app in front), with the user's own notes for them first,
    and Windows' own shortcuts and tools that match the goal. Trimmed to
    about ``budget`` characters; "" when nothing fits."""
    packs = load_packs(folder)
    if not packs:
        return ""
    parts: list[str] = []
    left = budget
    picked = pick_packs(goal, front_exe, packs)
    if not picked and front_exe:
        # An app with no built-in pack: the user's own notes for it, if any.
        exe = front_exe.lower()
        own = _user_pack(Pack(id=exe.removesuffix(".exe"), name=exe.removesuffix(".exe"), apps={exe}), kb_dir)
        if own is not None:
            text = trim(own, goal, min(1200, left))
            parts.append(text.replace(f"[{own.name}]", f"[your notes on {own.name}, these win]", 1))
            left -= len(text)
    share = max(500, (left - 700) // max(1, len(picked)))
    for pack in picked:
        own = _user_pack(pack, kb_dir)
        if own is not None:
            text = trim(own, goal, min(1200, share))
            parts.append(text.replace(f"[{own.name}]", f"[your notes on {pack.name}, these win]", 1))
            left -= len(text)
            if own.replace:
                continue
        text = trim(pack, goal, min(share, max(0, left - 500)))
        parts.append(text)
        left -= len(text)
    if "windows" in packs:
        # Windows itself: only the bullets that clearly match (no padding).
        text = trim(packs["windows"], goal, max(400, min(900, left)), fill=False, least=1.5 if picked else 1.0)
        if text.count("\n") > len(packs["windows"].head):
            parts.append(text)
    return "\n\n".join(p for p in parts if p.strip())


if __name__ == "__main__":
    print("=" * 70)
    print("Clixy -- kb.py manual verification")
    print(f"  KB_DIR: {KB_DIR}")
    print(f"  KB_RECALL_MAX_CHARS: {KB_RECALL_MAX_CHARS}")
    print("=" * 70)

    test_apps = [
        "EDUPACK.EXE",
        "edupack.exe",
        "Fusion360.exe",
        "blender.exe",
        "DOES_NOT_EXIST.EXE",
    ]
    for app in test_apps:
        content, name = recall(app)
        if content:
            print(f"\n{app:30s} -> matched {name!r}, {len(content)} chars")
            preview = content[:120].replace("\n", " ")
            print(f"  preview: {preview!r}...")
        else:
            print(
                f"\n{app:30s} -> no file ({_sanitize_app_name(app)}.md "
                f"not in {KB_DIR})"
            )

    print(f"\nDrop an .md file at:\n  {KB_DIR / 'edupack.exe.md'}")
    print("...then re-run to see it injected.")
