"""Reference timing from public lyrics-alignment datasets, for ``kizami eval``.

* JamendoLyrics MultiLang: a word list (``SONG.words.txt``, one word per
  line), its word times (``SONG.csv``: ``word_start,word_end,line_end`` in
  seconds) and the lyrics (``SONG.txt``). Each word becomes one chunk.

The files stay where the user keeps them; nothing is downloaded here.
Standard library only.
"""

from __future__ import annotations

import csv
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
