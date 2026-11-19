"""English and Spanish words split into syllables by their spelling.

A syllable is the smallest unit that can start a chunk in these languages.
The rules are simple on purpose: a wrong split only matters where a strong
attack also falls, and the audio decides that. Spanish spelling is regular,
so its rules are the standard ones. English needs a few exceptions.

Standard library only.
"""

from __future__ import annotations

import unicodedata

EN_VOWELS = set("aeiou")
EN_DIGRAPHS = {"th", "sh", "ch", "ph", "wh"}  # one sound: never split, go to the next syllable
EN_KEEP_BEFORE = {"ck", "ng", "gh"}  # stay with the syllable before
EN_BLENDS = {"bl", "br", "cl", "cr", "dr", "fl", "fr", "gl", "gr", "pl", "pr", "tr", "thr", "shr", "str", "spr", "scr", "spl"}
EN_SUFFIXES = ("ness", "ment", "less", "ful", "ly")
EN_EXCEPTIONS = {
    "eye": ["eye"],
    "eyes": ["eyes"],
    "every": ["ev", "ery"],
    "everything": ["ev", "ery", "thing"],
    "everybody": ["ev", "ery", "bo", "dy"],
    "fire": ["fire"],
    "fired": ["fired"],
    "desire": ["de", "sire"],
    "real": ["real"],
    "really": ["real", "ly"],
    "being": ["be", "ing"],
    "going": ["go", "ing"],
    "doing": ["do", "ing"],
    "crying": ["cry", "ing"],
    "dying": ["dy", "ing"],
    "flying": ["fly", "ing"],
    "trying": ["try", "ing"],
    "lying": ["ly", "ing"],
    "saying": ["say", "ing"],
    "playing": ["play", "ing"],
    "staying": ["stay", "ing"],
    "piano": ["pi", "a", "no"],
    "radio": ["ra", "di", "o"],
    "idea": ["i", "de", "a"],
    "create": ["cre", "ate"],
    "lion": ["li", "on"],
    "quiet": ["qui", "et"],
    "violin": ["vi", "o", "lin"],
    "people": ["peo", "ple"],
    "something": ["some", "thing"],
    "someone": ["some", "one"],
    "sometimes": ["some", "times"],
    "somebody": ["some", "bo", "dy"],
    "maybe": ["may", "be"],
}

ES_STRONG = set("aeoáéóíú")  # accented i and u break a diphthong like a strong vowel
ES_WEAK = set("iuü")
ES_VOWELS = ES_STRONG | ES_WEAK
ES_PAIRS = {"pl", "pr", "bl", "br", "cl", "cr", "dr", "tr", "fl", "fr", "gl", "gr", "kl", "kr", "ch", "ll", "rr"}


def latin_letters(s: str) -> str:
    """Lower-case letters ``a`` to ``z`` and ``'`` for the letter model: accents removed, ``ñ`` as ``n``."""
    s = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", s).casefold())
    return "".join(c for c in s if "a" <= c <= "z" or c == "'")


def _join(word: str, cuts: list[int]) -> list[str]:
    edges = [0, *cuts, len(word)]
    return [word[a:b] for a, b in zip(edges, edges[1:]) if b > a]


def syllables(word: str, lang: str) -> list[str]:
    """``word`` split into its syllables (``lang`` is ``"en"`` or ``"es"``). Joined, they give ``word``."""
    if "-" in word.strip("-"):
        out, start = [], 0
        for i, c in enumerate(word):
            if c == "-" and 0 < i < len(word) - 1:
                parts = syllables(word[start:i], lang)
                parts[-1] += "-"
                out += parts
                start = i + 1
        return out + syllables(word[start:], lang)
    lead = len(word) - len(word.lstrip("'’"))  # a leading apostrophe stays with the first syllable
    body = word[lead:]
    if not body:
        return [word]
    parts = _english(body) if lang == "en" else _spanish(body)
    parts[0] = word[:lead] + parts[0]
    return parts


def _english(word: str) -> list[str]:
    w = latin_letters(word) if len(latin_letters(word)) == len(word) else word.casefold()
    if w in EN_EXCEPTIONS:
        cuts, at = [], 0
        for p in EN_EXCEPTIONS[w][:-1]:
            at += len(p)
            cuts.append(at)
        return _join(word, cuts)
    for suffix in EN_SUFFIXES:
        stem = len(w) - len(suffix)
        if w.endswith(suffix) and stem >= 3 and w[stem - 1] == "e" and _english_nuclei(w[:stem]):  # lone|ly, hope|less
            return _english(word[:stem]) + [word[stem:]]
    nuclei = _english_nuclei(w)
    if len(nuclei) < 2:
        return [word]
    cuts = []
    for (_, end), (start, _) in zip(nuclei, nuclei[1:]):
        cuts.append(end + _english_coda(w[end:start], is_le=start == len(w) - 1 and w.endswith("le")))
    return _join(word, cuts)


def _english_nuclei(w: str) -> list[tuple[int, int]]:
    """``(start, end)`` of each vowel group that is sung as a syllable."""
    vowel = []
    for i, c in enumerate(w):
        if c == "u" and i > 0 and w[i - 1] == "q":
            vowel.append(False)  # qu is a consonant sound
        elif c == "y":
            vowel.append(i > 0 and not (i + 1 < len(w) and w[i + 1] in EN_VOWELS))
        else:
            vowel.append(c in EN_VOWELS)
    groups, i = [], 0
    while i < len(w):
        if vowel[i]:
            j = i
            while j < len(w) and vowel[j]:
                j += 1
            groups.append((i, j))
            i = j
        else:
            i += 1
    if len(groups) >= 2:
        last_start, last_end = groups[-1]
        tail, before = w[last_start:], w[last_start - 1] if last_start > 0 else ""
        silent = False
        if tail == "e" and last_end == len(w):
            silent = not (before == "l" and last_start >= 2 and not vowel[last_start - 2])  # -le after a consonant is sung
        elif w[last_start:] in ("ed", "es") and last_start > 0 and not vowel[last_start - 1]:
            silent = not (
                (tail == "ed" and before in "td")
                or (tail == "es" and (before in "sxzcg" or w[last_start - 2 : last_start] in ("ch", "sh")))
            )
        if silent:
            groups.pop()
    return groups


def _english_coda(cluster: str, is_le: bool) -> int:
    """How many consonants between two vowel groups stay with the syllable before."""
    n = len(cluster)
    if n == 0:
        return 0
    if is_le:  # lit|tle, ta|ble
        return max(0, n - 2)
    if n == 1:
        return 1 if cluster == "x" else 0
    if cluster[-2:] in EN_KEEP_BEFORE:
        return n
    for size in (3, 2):
        if n >= size and (cluster[-size:] in EN_DIGRAPHS or cluster[-size:] in EN_BLENDS):
            return n - size
    return n - 1


def _spanish(word: str) -> list[str]:
    w = unicodedata.normalize("NFKC", word).casefold()
    if len(w) != len(word):
        w = word.lower()
    vowel = []
    for i, c in enumerate(w):
        if c == "u" and 0 < i < len(w) - 1 and w[i - 1] in "qg" and w[i + 1] in "eiéí":
            vowel.append(False)  # silent u of que, qui, gue, gui
        elif c == "y":
            vowel.append(i == len(w) - 1 and (i == 0 or w[i - 1] in ES_VOWELS) or len(w) == 1)
        else:
            vowel.append(c in ES_VOWELS)
    nuclei, i = [], 0
    while i < len(w):
        if not vowel[i]:
            i += 1
            continue
        start = i
        while i + 1 < len(w) and vowel[i + 1] and not _hiatus(w[i], w[i + 1]):
            i += 1
        nuclei.append((start, i + 1))
        i += 1
    if len(nuclei) < 2:
        return [word]
    cuts = []
    for (_, end), (start, _) in zip(nuclei, nuclei[1:]):
        cluster = w[end:start]
        n = len(cluster)
        if n <= 1:
            keep = 0
        elif cluster[-2:] in ("qu", "gu"):  # the silent u: one consonant with the q or g before it
            keep = n - 2
        elif cluster[-2:] in ES_PAIRS:
            keep = n - 2
        else:
            keep = n - 1
        cuts.append(end + keep)
    return _join(word, cuts)


def _hiatus(a: str, b: str) -> bool:
    """Two vowels next to each other are sung in two syllables."""
    a, b = a.replace("y", "i"), b.replace("y", "i")
    if a in "íú" or b in "íú":
        return True
    return a in ES_STRONG and b in ES_STRONG


def links(prev_word: str, next_word: str) -> bool:
    """Spanish sinalefa: ``prev_word`` ends in a vowel and ``next_word`` starts with one (``h`` is silent)."""
    p = prev_word.casefold().rstrip(".,!?;:\"')»”")
    n = next_word.casefold().lstrip("¿¡\"'(«“")
    if not p or not n:
        return False
    if n[0] == "h":
        n = n[1:]
    return (p[-1] in ES_VOWELS or p[-1] == "y") and bool(n) and n[0] in ES_VOWELS
