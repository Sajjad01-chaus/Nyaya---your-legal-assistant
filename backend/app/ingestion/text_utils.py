"""Shared text normalisation. Used by both the statute parser and the forms pipeline."""

from __future__ import annotations

import re
import unicodedata

# Words that stay lowercase inside a title unless they lead it. Matches the
# convention in the brief's own example filename:
#   FORM-12_Bond-and-Bail-Bond-for-Attendance-before-Court.pdf
_MINOR = frozenset(
    """a an the and or nor but for of to in on at by with from into over under
    as if is be near upon per via vs versus before after against between during
    through without within upon among about""".split()
)

_WS = re.compile(r"\s+")
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―"), "-")
_QUOTES = {
    ord("‘"): "'", ord("’"): "'",
    ord("“"): '"', ord("”"): '"',
    ord("′"): "'", ord("″"): '"',
}


def normalise(text: str) -> str:
    """NFKC, unify dashes and quotes, collapse whitespace.

    Justified gazette text carries runs of wide inter-word spacing that survive
    extraction; collapsing them keeps chunk text stable across re-runs.
    """
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_QUOTES)
    return _WS.sub(" ", text).strip()


def dehyphenate(text: str) -> str:
    """Repair words broken across a line break: 'search- warrant' -> 'search-warrant'."""
    return re.sub(r"(\w)-\s+(\w)", r"\1-\2", text)


def smart_title(text: str) -> str:
    """Title-case that respects minor words, hyphens and apostrophes.

    ``str.title()`` is wrong twice over here: it yields "Arrest Of Persons"
    and, worse, turns "MAGISTRATE'S" into "Magistrate'S" -- which after
    punctuation stripping becomes the filename "MagistrateS".
    """
    text = normalise(text.translate(_DASHES))
    words = text.split(" ")
    out: list[str] = []
    for i, word in enumerate(words):
        if not word:
            continue
        lowered = word.lower()
        if i != 0 and i != len(words) - 1 and lowered.strip(",.;:") in _MINOR:
            out.append(lowered)
            continue
        out.append(_cap_compound(lowered))
    return " ".join(out)


def _cap_compound(word: str) -> str:
    """Capitalise across hyphens but not across apostrophes.

    'bail-bond'      -> 'Bail-Bond'
    "magistrate's"   -> "Magistrate's"   (not "Magistrate'S")
    """
    parts = word.split("-")
    capped = []
    for part in parts:
        if not part:
            capped.append(part)
            continue
        head, sep, tail = part.partition("'")
        capped.append(head[:1].upper() + head[1:] + sep + tail)
    return "-".join(capped)


def slugify(text: str, *, max_len: int = 120) -> str:
    """Filesystem-safe, deterministic, no spaces, no collisions from casing.

    Applied *after* ``smart_title`` so the apostrophe is dropped from an
    already-correct "Magistrate's" rather than from a mangled "Magistrate'S".
    """
    text = normalise(text.translate(_DASHES))
    text = text.replace("&", " and ")
    text = re.sub(r"['’]", "", text)          # possessives close up
    text = re.sub(r"[^\w\s-]", " ", text)          # drop remaining punctuation
    text = _WS.sub("-", text.strip())
    text = re.sub(r"-{2,}", "-", text).strip("-")
    if len(text) > max_len:
        text = text[:max_len].rsplit("-", 1)[0]
    return text


def is_garbage_text(text: str, *, min_chars: int = 60) -> bool:
    """Detect a text layer that exists but does not decode to language.

    The hard case is not a missing text layer, which is trivial to spot, but a
    present and wrong one. Page 1 of the BNSS gazette sets Hindi in a legacy
    font with no usable ToUnicode map; it extracts as ``vlk/kkj.k``, passes
    every "is there text?" check, and embeds as noise.
    """
    stripped = text.strip()
    if len(stripped) < min_chars:
        return True
    letters = sum(ch.isalpha() for ch in stripped)
    if letters == 0:
        return True
    replacement = stripped.count("�") / len(stripped)
    if replacement > 0.05:
        return True
    # Real prose is mostly letters and spaces. Legacy-font mojibake is dense
    # with punctuation because Latin glyph slots are reused for Devanagari.
    alpha_space = sum(ch.isalpha() or ch.isspace() for ch in stripped) / len(stripped)
    if alpha_space < 0.70:
        return True
    # Vowel-free runs are a strong mojibake signal in Latin-script output.
    words = [w for w in re.findall(r"[A-Za-z]{3,}", stripped)]
    if words:
        vowelless = sum(1 for w in words if not re.search(r"[aeiouAEIOU]", w))
        if vowelless / len(words) > 0.40:
            return True
    return False
