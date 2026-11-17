"""Lyrics text to timing units.

A *unit* (``atom`` in the code) is the smallest piece of the lyrics that can
get its own chunk start: usually one kana or one kanji with its reading. Small
kana, the long mark, a geminate and a drawn-out う join the unit before them,
because they have no attack of their own.

Lyrics file format: one lyric line per line. Blank lines and section tags
such as ``[Chorus]`` are skipped. A reading can be given for a word as
``{漢字|かんじ}`` or ``漢字{かんじ}``; the reading is what is sung.

The functions that split words need SudachiPy with a dictionary
(``pip install "kizami[generate]"``). Parsing the file does not.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

TAG = re.compile(r"^\[[^\]]+\]$")
OVERRIDE = re.compile(r"\{([^{}|]+)\|([^{}]+)\}|([㐀-鿿々〆ヶ]+)\{([^{}]+)\}")
DEPENDENT_POS = {"助詞", "助動詞", "接尾辞"}
SKIP_POS = {"補助記号", "空白"}
# No attack of their own: small kana, long mark, geminate.
ATTACH = set("ゃゅょぁぃぅぇぉゎっー")
# Kana whose vowel is o: a following う is usually the same long vowel (もう, そう, よう).
O_ROW = set("おこそとのほもよろをごぞどぼぽょ")
VOICED = str.maketrans("かきくけこさしすせそたちつてとはひふへほ", "がぎぐげござじずぜぞだぢづでどばびぶべぼ")
HALF_VOICED = str.maketrans("はひふへほ", "ぱぴぷぺぽ")


def parse_lyrics(path: str | Path) -> list[dict]:
    """Return the lyric lines as dicts: ``display`` text, ``sung`` text, reading ``spans``.

    A span is ``(display start, display end, sung start, sung end)`` for a word
    with a reading given in braces.
    """
    lines = []
    for raw in Path(path).read_text(encoding="utf-8-sig").splitlines():
        s = raw.strip()
        if not s or TAG.match(s):
            continue
        display, sung, spans, pos = "", "", [], 0
        for m in OVERRIDE.finditer(s):
            pre = s[pos : m.start()]
            display += pre
            sung += pre
            base, reading = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            spans.append((len(display), len(display) + len(base), len(sung), len(sung) + len(reading)))
            display += base
            sung += reading
            pos = m.end()
        display += s[pos:]
        sung += s[pos:]
        lines.append({"display": display, "sung": sung, "spans": spans})
    return lines


def norm_char(c: str) -> str:
    """The character in comparable form, or "" for punctuation and spaces."""
    c = unicodedata.normalize("NFKC", c).casefold()
    return c if c and c[0].isalnum() else ""


def display_to_sung(line: dict, i: int) -> int:
    """Offset in the sung text of the display character at offset ``i``."""
    shift = 0
    for ds, de, ss, se in line["spans"]:
        if i >= de:
            shift = se - de
        elif i > ds:
            return ss
        else:
            break
    return i + shift


def kata_to_hira(s: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def is_kana(c: str) -> bool:
    return "ぁ" <= c <= "ゖ" or c == "ー"


def is_kanji(c: str) -> bool:
    return "㐀" <= c <= "鿿" or c in "々〆ヶ"


def furigana_atoms(surface: str, reading: str) -> list[tuple[int, int, str]] | None:
    """Split a short unit into kana characters and kanji runs with their readings."""
    s = kata_to_hira(surface)
    parts, i = [], 0
    while i < len(s):
        if is_kana(s[i]):
            parts.append(("k", i, i + 1))
            i += 1
        else:
            j = i
            while j < len(s) and not is_kana(s[j]):
                j += 1
            parts.append(("x", i, j))
            i = j
    pattern = "".join(re.escape(s[a:b]) if kind == "k" else "(.+?)" for kind, a, b in parts)
    m = re.fullmatch(pattern, reading)
    if not m:
        return None
    out, g = [], 0
    for kind, a, b in parts:
        if kind == "k":
            out.append((a, b, s[a:b]))
        else:
            g += 1
            out.append((a, b, m.group(g)))
    return out


_LEXICON: list = []


def kanji_readings(tok, k: str) -> set[str]:
    """Every reading the dictionary lists for one kanji (on and kun), plus the tokenizer's choice."""
    if not _LEXICON:
        from sudachipy import dictionary

        _LEXICON.append(dictionary.Dictionary())
    lexicon = _LEXICON[0]
    out = {kata_to_hira(m.reading_form()) for m in lexicon.lookup(k)} if hasattr(lexicon, "lookup") else set()
    ms = tok.tokenize(k)
    if ms:
        out.add(kata_to_hira(ms[0].reading_form()))
    return {r for r in out if r}


def kanji_candidates(tok, k: str, prev: set[str]) -> set[str]:
    """Readings one kanji may take inside a compound: full, shortened, voiced, geminated."""
    base = set(prev) if k == "々" else kanji_readings(tok, k)
    out = set()
    for r in base:
        for n in range(1, len(r) + 1):
            pre = r[:n]
            out.update({pre, pre[0].translate(VOICED) + pre[1:], pre[0].translate(HALF_VOICED) + pre[1:]})
            if n >= 2:
                out.add(pre[:-1] + "っ")
    return out


def split_kanji_reading(tok, run: str, reading: str) -> tuple[list[tuple[str, str]], bool]:
    """``[(kanji, reading), ...]`` and whether the split is a guess."""
    n = len(run)
    if n == 1:
        return [(run, reading)], False
    cands, prev = [], set()
    for k in run:
        c = kanji_candidates(tok, k, prev)
        cands.append(c)
        prev = c or prev

    def solve(free):
        best = None

        def rec(i, pos, parts, score):
            nonlocal best
            if i == n:
                if pos == len(reading) and (best is None or score > best[0]):
                    best = (score, parts)
                return
            for end in range(pos + 1, len(reading) - (n - i - 1) + 1):
                piece = reading[pos:end]
                if piece in cands[i]:
                    rec(i + 1, end, parts + [piece], score + len(piece) ** 2)
                elif i == free:
                    rec(i + 1, end, parts + [piece], score)

        rec(0, 0, [], 0)
        return best

    got = solve(None)
    guess = False
    if got is None:
        tries = [g for g in (solve(f) for f in range(n)) if g]
        got = max(tries, key=lambda g: g[0]) if tries else None
    if got is None:
        if len(reading) < n:
            return [(run, reading)], True
        guess, parts, pos = True, [], 0
        for i in range(n):
            size = (len(reading) - pos) // (n - i) if i < n - 1 else len(reading) - pos
            size = max(1, size)
            parts.append(reading[pos : pos + size])
            pos += size
        got = (0, parts)
    return list(zip(run, got[1])), guess


def atoms_for(lines: list[dict], vocab: dict) -> tuple[list[dict], int]:
    """Timing units for all lines, and how many kanji compounds were kept whole.

    Each unit is a dict: ``line``, ``offset`` and ``end`` in the display text,
    ``kana`` (the sung kana known to the aligner, ``*`` for one Latin
    syllable), and the flags ``word_start``, ``dependent`` (particle or
    auxiliary) and ``line_first``.
    """
    from sudachipy import dictionary, tokenizer

    tok = dictionary.Dictionary().tokenizer()
    mode_c, mode_a = tokenizer.Tokenizer.SplitMode.C, tokenizer.Tokenizer.SplitMode.A
    atoms = []
    guesses = 0

    def kana_tokens(reading):
        return [c for c in kata_to_hira(reading) if c in vocab and c != "|"]

    for li, line in enumerate(lines):
        line_atoms, first, done_spans = [], True, set()
        for m in tok.tokenize(line["display"], mode_c):
            pos = m.part_of_speech()[0]
            if pos in SKIP_POS or not norm_char(m.surface()[:1] or " "):
                continue
            flags = {"word_start": True, "dependent": pos in DEPENDENT_POS and not first, "line_first": first}
            first = False
            span = next((sp for sp in line["spans"] if sp[0] <= m.begin() < sp[1]), None)
            if span:
                if span in done_spans:
                    continue
                done_spans.add(span)
                units = [(span[0], span[1], kana_tokens(line["sung"][span[2] : span[3]]))]
            else:
                units = []
                for u in m.split(mode_a) or [m]:
                    surf = u.surface()
                    if not norm_char(surf[:1] or " "):
                        continue
                    if surf.isascii():
                        n = max(1, len(re.findall(r"[aeiouy]+", surf.lower())))
                        units.append((u.begin(), u.end(), ["*"] * n))
                        continue
                    reading = kata_to_hira(u.reading_form())
                    parts = furigana_atoms(surf, reading)
                    if parts is None:
                        units.append((u.begin(), u.end(), kana_tokens(reading)))
                        continue
                    for pa, pb, k in parts:
                        run = surf[pa:pb]
                        if pb - pa >= 2 and all(is_kanji(c) for c in run):
                            pieces, guess = split_kanji_reading(tok, run, k)
                            guesses += guess
                            if guess:  # irregular reading (for example 今日): keep the compound whole
                                pieces = [(run, k)]
                            for j, (_, r) in enumerate(pieces):
                                units.append((u.begin() + pa + j, u.begin() + pa + j + 1, kana_tokens(r)))
                            if len(pieces) == 1:
                                units[-1] = (u.begin() + pa, u.begin() + pb, kana_tokens(k))
                        else:
                            units.append((u.begin() + pa, u.begin() + pb, kana_tokens(k)))
            m_first = len(line_atoms)
            for b, e, kana in units:
                surf = kata_to_hira(line["display"][b:e])
                long_u = (
                    surf == "う"
                    and line_atoms
                    and line_atoms[-1]["kana"][-1:]
                    and line_atoms[-1]["kana"][-1] in O_ROW
                    and (
                        (pos == "助動詞" and m.surface() == "う")
                        or (len(line_atoms) > m_first and not (pos == "動詞" and e == m.end()))
                    )
                )
                if (long_u or (len(surf) == 1 and surf in ATTACH)) and line_atoms and line_atoms[-1]["end"] == b:
                    line_atoms[-1]["end"] = e  # no attack of its own: joins the unit before
                    line_atoms[-1]["kana"] += kana
                    continue  # flags stay pending for the next unit of this word
                line_atoms.append({"line": li, "offset": b, "end": e, "kana": kana, **flags})
                flags = {"word_start": False, "dependent": flags["dependent"], "line_first": False}
        atoms.extend(line_atoms)
    return atoms, guesses
