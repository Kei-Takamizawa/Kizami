"""Which language each part of a lyric line is in.

The script decides: kana and kanji are Japanese, Hangul is Korean. Latin
letters are English or Spanish for the whole song (a song with both is not
supported); ``guess_latin`` picks one from the words of the song.

Standard library only.
"""

from __future__ import annotations

import unicodedata
from typing import Iterable

SPANISH_MARKS = set("ñáéíóúü¿¡")
# Common words that are clearly one language. Words both languages share or
# that fill songs in both (a, me, no, y, o, la as in "la la la") are left out.
SPANISH_WORDS = {
    "que", "el", "los", "las", "del", "de", "en", "por", "para", "pero", "porque", "cuando",
    "yo", "tú", "tu", "te", "contigo", "conmigo", "quiero", "amor", "corazón", "noche", "vida",
    "como", "más", "mas", "muy", "todo", "nada", "siempre", "nunca", "ahora", "aquí", "eres",
    "estoy", "esta", "está", "una", "uno", "con", "sin", "bailar", "baila", "mami", "papi", "dime",
    "quiero", "sé", "solo", "sola", "ella", "nosotros", "tus", "sus", "pa", "hasta", "donde",
}  # fmt: skip
ENGLISH_WORDS = {
    "the", "and", "you", "i", "i'm", "is", "my", "your", "it", "it's", "that", "this", "with",
    "love", "night", "know", "want", "just", "what", "don't", "can't", "we", "they", "are", "be",
    "of", "to", "in", "on", "for", "all", "baby", "oh", "yeah", "girl", "boy", "never", "feel",
    "like", "can", "will", "go", "get", "got", "make", "so", "up", "now", "young", "heart", "kiss",
}  # fmt: skip


def script_of(c: str) -> str:
    """``"ja"``, ``"ko"``, ``"latin"`` or ``""`` (digits, punctuation, spaces) for one character."""
    if not c:
        return ""
    c = unicodedata.normalize("NFKC", c)[:1]
    o = ord(c)
    if 0x3040 <= o <= 0x309F or 0x31F0 <= o <= 0x31FF or 0x3400 <= o <= 0x9FFF or c in "々〆":
        return "ja" if c not in "゛゜" else ""
    if 0x30A0 <= o <= 0x30FF:
        return "" if c in "゠・" else "ja"  # ー, ヶ and every katakana letter
    if 0xAC00 <= o <= 0xD7A3 or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F:
        return "ko"
    if c.isalpha() and unicodedata.name(c, "").startswith("LATIN"):
        return "latin"
    return ""


def script_runs(text: str) -> list[tuple[int, int, str]]:
    """``[(start, end, script), ...]``: the parts of ``text`` in one script.

    A run starts and ends on a letter. Spaces and punctuation between two
    letters of the same script belong to the run; at a change of script, or
    at either end of the text, they belong to no run.
    """
    return runs_of([script_of(c) for c in text])


def runs_of(scripts: list[str]) -> list[tuple[int, int, str]]:
    """``script_runs`` for a list with the script of each character (``""`` for none)."""
    runs: list[list] = []
    for i, s in enumerate(scripts):
        if not s:
            continue
        if runs and runs[-1][2] == s:
            runs[-1][1] = i + 1
        else:
            runs.append([i, i + 1, s])
    return [(a, b, s) for a, b, s in runs]


def guess_latin(words: Iterable[str]) -> str:
    """``"es"`` when the Latin words look Spanish, else ``"en"``."""
    score = 0
    for w in words:
        w = unicodedata.normalize("NFKC", w).casefold().strip(".,!?¿¡\"'()")
        score += 2 * sum(c in SPANISH_MARKS for c in w)
        score += (w in SPANISH_WORDS) - (w in ENGLISH_WORDS)
    return "es" if score > 0 else "en"
