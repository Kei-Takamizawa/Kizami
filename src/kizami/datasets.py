"""Reference timing from public lyrics-alignment datasets, for ``kizami eval``.

* JamendoLyrics MultiLang: a word list (``SONG.words.txt``, one word per
  line), its word times (``SONG.csv``: ``word_start,word_end,line_end`` in
  seconds) and the lyrics (``SONG.txt``). Each word becomes one chunk.
* Children's Song Dataset (CSD), Korean part: note times
  (``csv/krNNNx.csv``: ``start,end,pitch,syllable`` in seconds, one Hangul
  block per note) and the lyrics (``lyric/krNNNx.txt``). Each Hangul block
  becomes one chunk.

The files stay where the user keeps them; nothing is downloaded here.
Standard library only.
"""

from __future__ import annotations

import csv
import unicodedata
from pathlib import Path

from kizami.timing import Chunk, Line, TimingDocument, normalize_text


class DatasetError(ValueError):
    """The dataset files do not fit together."""


def _ms(value: str) -> int:
    return int(round(float(value) * 1000))


def jamendo_reference(words_csv: str | Path, words_txt: str | Path, lyrics_txt: str | Path) -> TimingDocument:
    """One chunk per word, grouped into the lines of the lyrics file."""
    words = [w.strip() for w in Path(words_txt).read_text(encoding="utf-8").splitlines() if w.strip()]
    with open(words_csv, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if len(rows) != len(words):
        raise DatasetError(f"{words_txt} has {len(words)} words but {words_csv} has {len(rows)} times")
    lines, k = [], 0
    for n, raw in enumerate(Path(lyrics_txt).read_text(encoding="utf-8").splitlines(), start=1):
        line_words = raw.split()
        if not line_words:
            continue
        chunks = []
        for i, w in enumerate(line_words):
            if k >= len(words) or normalize_text(words[k]) != normalize_text(w):
                expected = words[k] if k < len(words) else "(no more words)"
                raise DatasetError(f"{lyrics_txt} line {n}: word {i + 1} does not match the word list ({expected})")
            row = rows[k]
            start, end = _ms(row["word_start"]), _ms(row["word_end"])
            text = w + (" " if i + 1 < len(line_words) else "")
            chunks.append(Chunk(text=text, start_ms=start, end_ms=max(end, start)))
            k += 1
        lines.append(Line(chunks=tuple(chunks)))
    if k != len(words):
        raise DatasetError(f"{lyrics_txt} has fewer words than {words_txt} ({k} of {len(words)})")
    return TimingDocument(lines=tuple(lines), kind="reference", meta={"source": "jamendolyrics"})


def _is_block(s: str) -> bool:
    return len(s) == 1 and 0xAC00 <= ord(s) <= 0xD7A3


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def csd_reference(notes_csv: str | Path, lyrics_txt: str | Path) -> TimingDocument:
    """One chunk per Hangul block, grouped into the lines of the lyrics file. Notes without a
    Hangul block (rests, empty syllables) are skipped. Errors name positions, never lyric text."""
    with open(notes_csv, encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f) if _is_block(_nfc((r.get("syllable") or "").strip()))]
    texts = [_nfc(t.strip()) for t in Path(lyrics_txt).read_text(encoding="utf-8-sig").splitlines() if t.strip()]
    blocks = sum(_is_block(c) for t in texts for c in t)
    if len(rows) != blocks:
        raise DatasetError(f"{notes_csv} has {len(rows)} sung syllables but the lyrics have {blocks}")
    lines, k = [], 0
    for t in texts:
        at = [i for i, c in enumerate(t) if _is_block(c)]
        chunks = []
        for j, i in enumerate(at):
            row = rows[k]
            k += 1
            if _nfc(row["syllable"].strip()) != t[i]:
                raise DatasetError(f"{notes_csv}: sung syllable {k} does not match the lyrics")
            start, end = _ms(row["start"]), _ms(row["end"])
            text = t[0 if j == 0 else i : at[j + 1] if j + 1 < len(at) else len(t)]
            chunks.append(Chunk(text=text, start_ms=start, end_ms=max(end, start)))
        if chunks:
            lines.append(Line(chunks=tuple(chunks)))
    return TimingDocument(lines=tuple(lines), kind="reference", meta={"source": "csd"})
