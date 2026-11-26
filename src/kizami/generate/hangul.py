"""Korean letters for the MMS letter model.

MMS learned Korean through uroman, which writes each Hangul block as its
lead, vowel and tail letters. The tables below are uroman's. One change is
made inside a word: a tail consonant before a block with a silent lead moves
to that block (``음악`` is sung 으막, so the ``m`` starts the second block).
The letters of the word stay the same; only which block owns them changes.

Standard library only.
"""

from __future__ import annotations

LEADS = "g gg n d dd r m b bb s ss - j jj c k t p h".split()
VOWELS = "a ae ya yae eo e yeo ye o wa wai oe yo u weo we wi yu eu yi i".split()
TAILS = "- g gg gs n nj nh d l lg lm lb ls lt lp lh m b bs s ss ng j c k t p h".split()
# How much of a tail moves to a following silent lead.
MOVES = {"gs": "s", "nj": "j", "lg": "g", "lm": "m", "lb": "b", "ls": "s", "lt": "t", "lp": "p", "bs": "s"}
STAYS = {"ng", "h", "nh", "lh"}  # never move: ng is the sound itself, h is not sung before a vowel


def block_letters(c: str) -> tuple[str, str, str]:
    """``(lead, vowel, tail)`` of one Hangul block; ``("", "", "")`` for anything else."""
    o = ord(c) - 0xAC00
    if not 0 <= o <= 0xD7A3 - 0xAC00:
        return "", "", ""
    lead, vowel, tail = LEADS[o // 588], VOWELS[(o // 28) % 21], TAILS[o % 28]
    return lead.replace("-", ""), vowel, tail.replace("-", "")


def word_letters(word: str) -> list[str]:
    """Letters for each character of ``word``, with the tail moved before a silent lead."""
    parts = [list(block_letters(c)) for c in word]
    for i in range(len(parts) - 1):
        tail, (lead, vowel, _) = parts[i][2], parts[i + 1]
        if tail and tail not in STAYS and vowel and not lead and parts[i][1]:
            moved = MOVES.get(tail, tail)
            parts[i][2] = tail[: len(tail) - len(moved)]
            parts[i + 1][0] = moved
    return ["".join(p) for p in parts]
