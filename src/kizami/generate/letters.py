"""Letters of the MMS letter model for kana (romaji). Standard library only."""

from __future__ import annotations

ROMAJI = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o", "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so", "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no", "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo", "や": "ya", "ゆ": "yu", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro", "わ": "wa", "を": "o", "ん": "n",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go", "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do", "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po", "ゔ": "bu",
    "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o", "ゃ": "ya", "ゅ": "yu", "ょ": "yo", "ゎ": "wa",
}  # fmt: skip
SMALL_Y = {"ゃ": "a", "ゅ": "u", "ょ": "o"}
SMALL_V = {"ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o"}
VOWELS = set("aiueo")


def romaji(kana: list[str]) -> str:
    """Letters for one unit's kana. The geminate adds no letter; the long mark repeats the vowel."""
    out, i = "", 0
    while i < len(kana):
        k, nxt = kana[i], kana[i + 1] if i + 1 < len(kana) else ""
        if k == "*":
            out += "*"
        elif k in ROMAJI:
            r = ROMAJI[k]
            if nxt in SMALL_Y and r.endswith("i") and len(r) > 1:
                stem = r[:-1]
                out += (stem if stem in ("sh", "ch", "j") else stem + "y") + SMALL_Y[nxt]
                i += 1
            elif nxt in SMALL_V and len(r) > 1:
                out += r[:-1] + SMALL_V[nxt]
                i += 1
            else:
                out += r
        elif k == "ー" and out and out[-1] in VOWELS:
            out += out[-1]
        i += 1
    return out
