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
from collections import Counter
from pathlib import Path

from kizami.generate.hangul import word_letters
from kizami.generate.lang import guess_latin, runs_of, script_of
from kizami.generate.letters import romaji
from kizami.generate.syllables import latin_letters, links, syllables

TAG = re.compile(r"^\[[^\]]+\]$")
OVERRIDE = re.compile(r"\{([^{}|]+)\|([^{}]+)\}|([㐀-鿿々〆ヶ]+)\{([^{}|]+)\}")
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


def kata_to_hira(s: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def is_kana(c: str) -> bool:
    return "ぁ" <= c <= "ゖ" or c == "ー"


def is_kanji(c: str) -> bool:
    return "㐀" <= c <= "鿿" or c in "々〆ヶ"


def has_kanji(s: str) -> bool:
    return any(is_kanji(c) for c in s)


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


def _line_scripts(line: dict) -> list[str]:
    """The script of each display character; inside a given reading, the script of the reading."""
    scripts = [script_of(c) for c in line["display"]]
    for d0, d1, s0, s1 in line["spans"]:
        sung = [script_of(c) for c in line["sung"][s0:s1]]
        sung_script = next((x for x in sung if x), "ja")
        scripts[d0:d1] = [sung_script] * (d1 - d0)
    return scripts


WORD_INNER = set("'\u2019-")


def _words(text: str, start: int, stop: int, script: str, scripts: list[str]) -> list[tuple[int, int]]:
    """``(start, end)`` of each word in ``text[start:stop]``: letters of ``script``, with ``'`` or ``-``
    between letters (and a leading ``'``) kept inside."""
    out, i = [], start
    while i < stop:
        if scripts[i] != script and not (text[i] in "'\u2019" and i + 1 < stop and scripts[i + 1] == script):
            i += 1
            continue
        j = i + 1
        while j < stop and (scripts[j] == script or (text[j] in WORD_INNER and j + 1 < stop and scripts[j + 1] == script)):
            j += 1
        out.append((i, j))
        i = j
    return out


def atoms_for(lines: list[dict], vocab: dict, latin: str = "auto") -> tuple[list[dict], int, str]:
    """Timing units for all lines, how many kanji compounds were kept whole, and the Latin language.

    Each unit is a dict: ``line``, ``offset`` and ``end`` in the display text,
    ``kana`` (the sung kana known to the kana model, ``*`` for a unit in
    another language), ``hiragana`` (the unit's text with its kanji written as
    their reading, or None when it has no kanji), the flags ``word_start``,
    ``dependent`` (Japanese particle or auxiliary) and ``line_first``,
    ``lang`` (``ja``, ``ko``, ``en`` or ``es``) and ``letters`` (for the MMS
    letter model). A unit in another language than Japanese also has
    ``syllable``, its written text.

    Japanese is split with SudachiPy, Korean per Hangul block, English and
    Spanish per syllable. ``latin`` is ``"en"``, ``"es"`` or ``"auto"`` (guess
    from all Latin words of the song); every Latin word of the song is taken
    to be in that one language.
    """
    from sudachipy import dictionary, tokenizer

    dic = dictionary.Dictionary()
    if not _LEXICON:
        _LEXICON.append(dic)  # kanji_readings looks single kanji up in the same dictionary
    tok = dic.tokenizer()
    mode_c, mode_a = tokenizer.Tokenizer.SplitMode.C, tokenizer.Tokenizer.SplitMode.A
    atoms = []
    guesses = 0

    def kana_tokens(reading):
        return [c for c in kata_to_hira(reading) if c in vocab and c != "|"]

    def plain_units(pieces, shift):
        """Units of dictionary words (mode A pieces) whose offsets are ``shift`` from the line start."""
        nonlocal guesses
        units = []
        for u in pieces:
            surf = u.surface()
            b0 = u.begin() + shift
            if not norm_char(surf[:1] or " "):
                continue
            if surf.isascii():
                n = max(1, len(re.findall(r"[aeiouy]+", surf.lower())))
                units.append((b0, b0 + len(surf), ["*"] * n, None))
                continue
            reading = kata_to_hira(u.reading_form())
            parts = furigana_atoms(surf, reading)
            if parts is None:
                units.append((b0, b0 + len(surf), kana_tokens(reading), reading if has_kanji(surf) else None))
                continue
            for pa, pb, k in parts:
                run = surf[pa:pb]
                if pb - pa >= 2 and all(is_kanji(c) for c in run):
                    pieces_k, guess = split_kanji_reading(tok, run, k)
                    guesses += guess
                    if guess:  # irregular reading (for example 今日): keep the compound whole
                        pieces_k = [(run, k)]
                    for j, (_, r) in enumerate(pieces_k):
                        units.append((b0 + pa + j, b0 + pa + j + 1, kana_tokens(r), r))
                    if len(pieces_k) == 1:
                        units[-1] = (b0 + pa, b0 + pb, kana_tokens(k), k)
                else:
                    units.append((b0 + pa, b0 + pb, kana_tokens(k), k if has_kanji(run) else None))
        return units

    def japanese_units(li, line, first):
        """Units of a line (or of a part of one, given as its own line) by today's Japanese rules."""
        line_atoms, done_spans = [], set()
        for m in tok.tokenize(line["display"], mode_c):
            pos = m.part_of_speech()[0]
            if pos in SKIP_POS or not norm_char(m.surface()[:1] or " "):
                continue
            spans = [sp for sp in line["spans"] if sp[0] < m.end() and sp[1] > m.begin()]
            if not spans:
                units = plain_units(m.split(mode_a) or [m], 0)
            else:
                # a given reading inside or across this word: the reading for its span,
                # the dictionary for the text around it
                units, at = [], m.begin()
                for sp in spans:
                    if at < sp[0]:
                        units += plain_units(tok.tokenize(line["display"][at : sp[0]], mode_a), at)
                    if sp not in done_spans:
                        done_spans.add(sp)
                        sung = line["sung"][sp[2] : sp[3]]
                        base = line["display"][sp[0] : sp[1]]
                        units.append((sp[0], sp[1], kana_tokens(sung), sung if has_kanji(base) else None))
                    at = max(at, sp[1])
                if at < m.end():
                    units += plain_units(tok.tokenize(line["display"][at : m.end()], mode_a), at)
                if not units:
                    continue  # the rest of a word with a given reading, already done
            flags = {"word_start": True, "dependent": pos in DEPENDENT_POS and not first, "line_first": first}
            first = False
            m_first = len(line_atoms)
            for b, e, kana, reading in units:
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
                    prev = line_atoms[-1]  # no attack of its own: joins the unit before
                    if prev["hiragana"] is not None or reading is not None:
                        prev["hiragana"] = (prev["hiragana"] or line["display"][prev["offset"] : prev["end"]]) + (
                            reading or line["display"][b:e]
                        )
                    prev["end"] = e
                    prev["kana"] += kana
                    continue  # flags stay pending for the next unit of this word
                line_atoms.append({"line": li, "offset": b, "end": e, "kana": kana, "hiragana": reading, **flags})
                flags = {"word_start": False, "dependent": flags["dependent"], "line_first": False}
        for a in line_atoms:
            a["lang"], a["letters"] = "ja", romaji(a["kana"])
        return line_atoms

    scripts_of = [_line_scripts(line) for line in lines]
    runs_of_line = [runs_of(sc) for sc in scripts_of]
    if latin == "auto":
        latin_words = [
            line["display"][a:b]
            for line, sc, runs in zip(lines, scripts_of, runs_of_line)
            for start, stop, script in runs
            if script == "latin"
            for a, b in _words(line["display"], start, stop, "latin", sc)
        ]
        latin = guess_latin(latin_words)
    counts = Counter(x for sc in scripts_of for x in sc if x)
    song_has_ja = "ja" in counts or not counts
    main_lang = "ko" if counts["ko"] > counts["latin"] else latin

    for li, line in enumerate(lines):
        runs = runs_of_line[li]
        if not runs and not song_has_ja:
            # no letters (a count-in like "3, 2, 1") in a song without Japanese:
            # one unit per group of digits, sung in the song's main language
            for k, m in enumerate(re.finditer(r"[^\W_]+", line["display"])):
                atoms.append(
                    {
                        "line": li, "offset": m.start(), "end": m.end(), "kana": ["*"], "hiragana": None,
                        "word_start": True, "dependent": False, "line_first": k == 0,
                        "lang": main_lang, "letters": "*", "syllable": m.group(),
                    }
                )  # fmt: skip
            continue
        if all(script == "ja" for _, _, script in runs):
            atoms.extend(japanese_units(li, line, True))  # today's path, the whole line
            continue
        line_atoms, prev_word = [], None
        for start, stop, script in runs:
            first = not line_atoms
            if script == "ja":
                part = {
                    "display": line["display"][start:stop],
                    "sung": line["sung"],
                    "spans": [(d0 - start, d1 - start, s0, s1) for d0, d1, s0, s1 in line["spans"] if start <= d0 and d1 <= stop],
                }
                for a in japanese_units(li, part, first):
                    a["offset"] += start
                    a["end"] += start
                    line_atoms.append(a)
                prev_word = None
                continue
            lang = "ko" if script == "ko" else latin
            if script == "latin" and start > 0 and line["display"][start - 1] in "'\u2019":
                start -= 1  # a leading apostrophe ('cause) belongs to the word after it
            for w0, w1 in _words(line["display"], start, stop, script, scripts_of[li]):
                word = line["display"][w0:w1]
                span = next((sp for sp in line["spans"] if sp[0] <= w0 and w1 <= sp[1]), None)
                if span is not None:  # a given reading: one unit for the whole written part
                    if any(a["offset"] == span[0] for a in line_atoms):
                        continue
                    sung = line["sung"][span[2] : span[3]]
                    pieces = [(span[0], span[1], "".join(word_letters(sung)) if lang == "ko" else latin_letters(sung))]
                elif lang == "ko":
                    pieces = [(w0 + i, w0 + i + 1, letters) for i, letters in enumerate(word_letters(word))]
                else:
                    pieces, at = [], w0
                    for syl in syllables(word, lang):
                        pieces.append((at, at + len(syl), latin_letters(syl).strip("'")))  # an edge ' is not sung
                        at += len(syl)
                linked = lang == "es" and prev_word is not None and links(prev_word, word)
                for k, (b, e, letters) in enumerate(pieces):
                    line_atoms.append(
                        {
                            "line": li, "offset": b, "end": e, "kana": ["*"], "hiragana": None,
                            "word_start": k == 0 and not linked, "dependent": False,
                            "line_first": not line_atoms, "lang": lang, "letters": letters,
                            "syllable": line["display"][b:e],
                        }
                    )  # fmt: skip
                prev_word = word
        atoms.extend(line_atoms)
    return atoms, guesses, latin


def to_hiragana(lines: list[dict], atoms: list[dict]) -> list[dict]:
    """Show kanji as their reading: new line texts, and units with a chunk start for every mora.

    Call it after the units are placed (``time``, ``snapped``, ``strength``
    and ``morae`` set). A unit with kanji becomes one unit per mora of its
    reading, timed by its ``morae`` (the が of 願 gets its own unit, so a
    chunk can start there). Every other unit, and the time of every first
    mora, stays as it was. Each line's ``display`` is replaced by the new text
    and the old text is kept as ``original``. Katakana and Latin letters stay
    as written. Each returned unit has ``written``: where it starts in the
    old text, or None for a later mora of a kanji reading.

    Each line also gets ``kanji``: ``[(start, end, written), ...]``, the parts
    of the new text that are the reading of kanji ``written``. Kana that the
    written text and the reading share at either end stay outside: 行っ read
    いっ gives ``written`` 行 over い. :func:`kanji_unless_split` uses them.
    """
    out: list[dict] = []
    by_line: dict[int, list[dict]] = {}
    for a in atoms:
        by_line.setdefault(a["line"], []).append(a)
    for li, line in enumerate(lines):
        old, shown, src, kanji = line["display"], "", 0, []
        for a in by_line.get(li, []):
            if a["offset"] >= src:
                shown += old[src : a["offset"]]
            src = max(src, a["end"])
            text = a["hiragana"] if a.get("hiragana") is not None else old[a["offset"] : a["end"]]
            if a.get("hiragana") is not None:
                written = old[a["offset"] : a["end"]]
                part = kanji_part(written, text) or (0, len(text), written)
                kanji.append((len(shown) + part[0], len(shown) + part[1], part[2]))
            morae = a.get("morae") or []
            strengths = a.get("mora_strengths") or []
            split = (
                a.get("hiragana") is not None
                and a.get("time") is not None
                and len(morae) >= 2
                and len(strengths) == len(morae)
                and "".join(k for k, _, _ in morae) == text
            )
            if not split:
                out.append(dict(a, offset=len(shown), end=len(shown) + len(text), written=a["offset"]))
                shown += text
                continue
            for g, ((kana, t, has_attack), strength) in enumerate(zip(morae, strengths)):
                unit = dict(a, offset=len(shown), end=len(shown) + len(kana), morae=[(kana, t, has_attack)])
                if g == 0:
                    unit["written"] = a["offset"]
                else:
                    unit.update(written=None, time=t, snapped=has_attack, strength=strength,
                                word_start=False, line_first=False, why="mora")  # fmt: skip
                unit["mora_strengths"] = [strength]
                out.append(unit)
                shown += kana
        line["original"] = old
        line["display"] = shown + old[src:]
        line["kanji"] = kanji
    return out


def kanji_part(written: str, reading: str) -> tuple[int, int, str] | None:
    """Where the kanji of ``written`` sit in ``reading``: ``(start, end, base)``, or None.

    Kana at either end that ``written`` and ``reading`` share are left out
    (``行っ`` and ``いっ`` give ``(0, 1, "行")``). None when no kanji is left.
    """
    w = kata_to_hira(written)
    s = 0
    while s < len(w) - 1 and s < len(reading) - 1 and is_kana(w[s]) and w[s] == reading[s]:
        s += 1
    e = 0
    while e < len(w) - s - 1 and e < len(reading) - s - 1 and is_kana(w[-1 - e]) and w[-1 - e] == reading[-1 - e]:
        e += 1
    base = written[s : len(written) - e]
    if not has_kanji(base) or not reading[s : len(reading) - e]:
        return None
    return s, len(reading) - e, base



def kanji_unless_split(lines: list[dict], atoms: list[dict], chosen: list[dict]) -> tuple[list[dict], list[dict], list[dict], int]:
    """Write kanji as in the lyrics again, except where a chunk starts inside their reading.

    Call it with the lines and units from :func:`to_hiragana` and the units
    chosen to start chunks. Kanji next to each other (one word, such as 夜空)
    are one group: when a chunk starts inside the reading of any of them (the
    ら of 空), the whole group stays in hiragana (よぞら). A chunk that starts
    between two kanji (夜|空) does not need hiragana. Returns copies of the
    lines, units and chosen units with the new text and offsets, and the
    number of groups left in hiragana. The inputs are not changed.
    """
    starts: dict[int, set[int]] = {}
    for a in chosen:
        starts.setdefault(a["line"], set()).add(a["offset"])
    new_lines, moves, left = [], [], 0
    for li, line in enumerate(lines):
        groups: list[list[tuple[int, int, str]]] = []
        for r in sorted(line.get("kanji", ())):
            if groups and groups[-1][-1][1] == r[0]:
                groups[-1].append(r)
            else:
                groups.append([r])
        here = starts.get(li, set())
        keep = []
        for g in groups:
            if any(a < s < b for a, b, _ in g for s in here):
                left += 1
            else:
                keep.extend(g)
        text, out, at, move = line["display"], "", 0, []  # move: (old start, old end, new start, new end)
        for a, b, written in keep:
            out += text[at:a]
            move.append((a, b, len(out), len(out) + len(written)))
            out += written
            at = b
        out += text[at:]
        new_lines.append(dict(line, display=out))
        moves.append(move)

    def new_pos(li: int, pos: int, end: bool) -> int:
        shift = 0
        for a, b, na, nb in moves[li]:
            if pos >= b:
                shift = nb - b
            elif pos > a:
                return nb if end else na  # inside a kanji written again
            else:
                break
        return pos + shift

    copies = {}
    new_atoms = []
    for u in atoms:
        c = dict(u, offset=new_pos(u["line"], u["offset"], False), end=new_pos(u["line"], u["end"], True))
        copies[id(u)] = c
        new_atoms.append(c)
    new_chosen = [copies.get(id(a)) or dict(a, offset=new_pos(a["line"], a["offset"], False)) for a in chosen]
    return new_lines, new_atoms, new_chosen, left
