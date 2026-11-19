"""Write timed lyrics as TTML for lyric players such as AMLL.

Each lyric line becomes a ``<p>`` and each chunk a ``<span>`` with its own
``begin`` and ``end`` (word timing). The layout follows the AMLL TTML
format (https://github.com/amll-dev/amll-ttml-db/wiki): the ``ttm``,
``tts``, ``itunes`` and ``amll`` namespaces, ``itunes:timing="Word"``, a
``ttm:agent`` for every singer, ``itunes:key`` ``L1``, ``L2`` ... on the
lines, and title and artist as ``amll:meta``. Times are written as
``hh:mm:ss.mmm``, which TTML and AMLL both read.

Chunk ends: a span needs an end. Give the timing document real ends first
with :func:`kizami.loudness.add_end_times` (where the voice stops). Chunks
that still have no end get one here: the start of the next chunk of the same
line, and for the last chunk of a line at most ``LAST_CHUNK_MS`` after its
start (and never after the next line starts).

Spaces: a space follows a span as its own text node (``<span>a</span>
<span>b</span>``), as AMLL's own TTML writer does. A space written in the
lyrics is kept, and with ``chunk_spaces`` every chunk is followed by one
(not at the end of a line).

Display offset: ``offset_ms`` moves every time by the same amount in the
written file only. A positive offset shows the lyrics later (for example to
make up for Bluetooth earphones), a negative one earlier. The timing
document itself is not changed.
"""

from __future__ import annotations

import json
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from xml.sax.saxutils import escape, quoteattr

from kizami.generate.lang import guess_latin, script_of
from kizami.timing import (
    FORMAT_NAME,
    Chunk,
    Line,
    TimingDocument,
    TimingFormatError,
    document_from_dict,
)

NS_TT = "http://www.w3.org/ns/ttml"
NS_TTM = "http://www.w3.org/ns/ttml#metadata"
NS_TTS = "http://www.w3.org/ns/ttml#styling"
NS_ITUNES = "http://music.apple.com/lyric-ttml-internal"
NS_AMLL = "http://www.example.com/ns/amll"
TAGGER_FORMAT = "kizami-tagger"
LAST_CHUNK_MS = 1000
MAX_OFFSET_MS = 10000


class TtmlError(ValueError):
    """The input cannot be written as TTML."""


@dataclass
class _Word:
    """One span: a chunk's text without the spaces around it, and whether a space follows."""

    text: str
    begin: int
    end: int
    space: bool = False


@dataclass
class TtmlOutput:
    """The TTML text and counts about it."""

    text: str
    lines: int
    chunks: int


def format_time(ms: int) -> str:
    """``hh:mm:ss.mmm``, for example ``00:01:02.345``."""
    ms = max(0, int(ms))
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def load_for_export(path: str | Path) -> TimingDocument:
    """Read a Kizami timing file (``auto_chu.json``) or a tagger project (``tagger.json``)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TtmlError(f"{path} is not valid JSON: {exc}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise TtmlError(f"{path}: {exc}") from exc
    try:
        if isinstance(data, dict) and data.get("format") == TAGGER_FORMAT:
            return document_from_tagger_project(data)
        return document_from_dict(data)
    except TimingFormatError as exc:
        raise TtmlError(
            f"{path}: {exc}. Give a timing file ({FORMAT_NAME}) or a tagger project ({TAGGER_FORMAT})"
        ) from exc


def document_from_tagger_project(data: Any) -> TimingDocument:
    """The timed chunks of a tagger project (``tagger.json``). Chunks without a start are left out."""
    if not isinstance(data, dict) or data.get("format") != TAGGER_FORMAT:
        raise TimingFormatError(f'format must be "{TAGGER_FORMAT}"')
    lines_in = data.get("lines")
    if not isinstance(lines_in, list):
        raise TimingFormatError("lines must be a list")
    lines = []
    for i, line in enumerate(lines_in):
        if not isinstance(line, dict) or not isinstance(line.get("units", []), list):
            raise TimingFormatError(f"lines[{i}] must be an object with a units list")
        chunks = []
        for unit in line.get("units", []):
            if not isinstance(unit, dict):
                continue
            text, start, end = unit.get("text"), unit.get("start_ms"), unit.get("end_ms")
            if not isinstance(text, str) or not text.strip() or not _is_ms(start):
                continue
            chunks.append(
                Chunk(
                    text=text,
                    start_ms=start,
                    end_ms=end if _is_ms(end) and end > start else None,
                    uncertain=unit.get("uncertain") is True,
                )
            )
        if chunks:
            agent = line.get("agent")
            lines.append(Line(chunks=tuple(chunks), agent=agent if isinstance(agent, str) else None))
    if not lines:
        raise TimingFormatError("the project has no timed chunks yet")
    return TimingDocument(lines=tuple(lines), kind="reference", meta={"source": "kizami tagger"})


def _is_ms(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_latin(ch: str) -> bool:
    return ch.isascii() or "LATIN" in unicodedata.name(ch, "")


def _needs_space(before: str, after: str) -> bool:
    """Two chunks of Latin text are separate words: AMLL needs the space between them."""
    if not before or not after or before[-1].isspace() or after[0].isspace():
        return False
    return _is_latin(before[-1]) and after[0].isalnum() and _is_latin(after[0])


def _chunk_ends(line: Line, next_line_start: int | None) -> list[int]:
    """The end of each chunk of the line, without the display offset."""
    chunks, ends = line.chunks, []
    for i, chunk in enumerate(chunks):
        later = chunks[i + 1].start_ms if i + 1 < len(chunks) else None
        end = chunk.end_ms
        if end is None:
            if later is not None:
                end = later
            else:
                end = chunk.start_ms + LAST_CHUNK_MS
                if next_line_start is not None and next_line_start > chunk.start_ms:
                    end = min(end, next_line_start)
        if later is not None and later > chunk.start_ms:
            end = min(end, later)  # spans of one line do not overlap
        ends.append(max(end, chunk.start_ms))
    return ends


def _knows_spaces(doc: TimingDocument) -> bool:
    """The chunk texts carry the spaces between words (``kizami generate`` keeps them, and so does a
    tagger file made from its output), so a missing space means the chunks are inside one word."""
    if doc.meta.get("source") == "kizami generate":
        return True
    return any(c.text[:1].isspace() or c.text[-1:].isspace() for line in doc.lines for c in line.chunks)


def _main_lang(doc: TimingDocument) -> str:
    """The language with the most units in ``meta.languages``. Without it (a tagger project, an
    older file), the script of most chunks: Hangul is ``ko``, Latin letters ``meta.latin`` or a
    guess from the words, anything else ``ja``."""
    counts = doc.meta.get("languages")
    if isinstance(counts, dict) and counts:
        best = max(counts.items(), key=lambda kv: (kv[1] if isinstance(kv[1], int) else 0, kv[0]))[0]
        if isinstance(best, str) and best:
            return best
    scripts = Counter(
        next((s for s in map(script_of, chunk.text) if s), "") for line in doc.lines for chunk in line.chunks
    )
    scripts.pop("", None)
    if not scripts:
        return "ja"
    best = max(("ja", "ko", "latin"), key=lambda s: scripts[s])  # a tie goes to the first
    if best == "latin":
        latin = doc.meta.get("latin")
        if latin in ("en", "es"):
            return latin
        return guess_latin(w for line in doc.lines for w in "".join(c.text for c in line.chunks).split())
    return best


def _words(line: Line, ends: list[int], chunk_spaces: bool, guess_spaces: bool = True) -> list[_Word]:
    chunks, words = line.chunks, []
    for i, (chunk, end) in enumerate(zip(chunks, ends)):
        if chunk.text[:1].isspace() and words:
            words[-1].space = True
        text = chunk.text.strip()
        if not text:
            continue
        word = _Word(text, chunk.start_ms, end, space=chunk.text[-1].isspace())
        if i + 1 < len(chunks) and (chunk_spaces or (guess_spaces and _needs_space(chunk.text, chunks[i + 1].text))):
            word.space = True
        words.append(word)
    return words


def _apply_offset(words: list[_Word], offset_ms: int) -> None:
    for w in words:
        w.begin = max(0, w.begin + offset_ms)
        w.end = max(w.begin + 1, w.end + offset_ms)


def _word_xml(w: _Word) -> str:
    return f'<span begin="{format_time(w.begin)}" end="{format_time(w.end)}">{escape(w.text)}</span>'


def document_to_ttml(doc: TimingDocument, **options: Any) -> str:
    """The document as a TTML text. Takes the options of :func:`render_ttml`."""
    return render_ttml(doc, **options).text


def render_ttml(
    doc: TimingDocument,
    *,
    offset_ms: int = 0,
    title: str | None = None,
    artists: Iterable[str] = (),
    album: str | None = None,
    lang: str | None = None,
    chunk_spaces: bool = True,
) -> TtmlOutput:
    """The document as TTML, with counts. See the module docstring.

    ``chunk_spaces`` puts a space after every chunk but the last of a line.
    Without it, a space comes only where the lyrics have one; for a hand-made
    file without spaces, two Latin chunks that touch are taken to be two words.
    ``lang`` (``xml:lang``) defaults to the language with the most units in
    the document (``meta.languages``), else ``ja``.
    """
    if lang is None:
        lang = _main_lang(doc)
    guess_spaces = not _knows_spaces(doc)
    if not isinstance(offset_ms, int) or isinstance(offset_ms, bool) or abs(offset_ms) > MAX_OFFSET_MS:
        raise TtmlError(f"the display offset must be a whole number of ms from -{MAX_OFFSET_MS} to {MAX_OFFSET_MS}")
    lines = [line for line in doc.lines if line.chunks]
    if not lines:
        raise TtmlError("the timing has no chunks")

    agents: dict[str | None, str] = {}
    for line in lines:
        if line.agent not in agents:
            agents[line.agent] = f"v{len(agents) + 1}"

    body = []
    first_begin, last_end = None, 0
    for n, line in enumerate(lines):
        nxt = lines[n + 1].chunks[0].start_ms if n + 1 < len(lines) else None
        words = _words(line, _chunk_ends(line, nxt), chunk_spaces, guess_spaces)
        _apply_offset(words, offset_ms)
        begin = min(w.begin for w in words)
        end = max(w.end for w in words)
        last_end = max(last_end, end)
        first_begin = begin if first_begin is None else min(first_begin, begin)
        inner = "".join(_word_xml(w) + (" " if w.space and i + 1 < len(words) else "") for i, w in enumerate(words))
        body.append(
            f'<p begin="{format_time(begin)}" end="{format_time(end)}" '
            f'itunes:key="L{n + 1}" ttm:agent="{agents[line.agent]}">{inner}</p>'
        )

    meta = []
    for name, vid in agents.items():
        if name:
            meta.append(f'<ttm:agent type="person" xml:id="{vid}"><ttm:name type="full">{escape(name)}</ttm:name></ttm:agent>')
        else:
            meta.append(f'<ttm:agent type="person" xml:id="{vid}"/>')
    if title:
        meta.append(f"<amll:meta key=\"musicName\" value={quoteattr(title)}/>")
    for artist in artists:
        if artist:
            meta.append(f"<amll:meta key=\"artists\" value={quoteattr(artist)}/>")
    if album:
        meta.append(f"<amll:meta key=\"album\" value={quoteattr(album)}/>")

    out = [
        f'<tt xmlns="{NS_TT}" xmlns:ttm="{NS_TTM}" xmlns:tts="{NS_TTS}" xmlns:itunes="{NS_ITUNES}" '
        f'xmlns:amll="{NS_AMLL}" xml:lang={quoteattr(lang)} itunes:timing="Word">',
        "<head><metadata>" + "".join(meta) + "</metadata></head>",
        f'<body dur="{format_time(last_end)}">',
        f'<div begin="{format_time(first_begin)}" end="{format_time(last_end)}">',
        *body,
        "</div>",
        "</body>",
        "</tt>",
    ]
    return TtmlOutput(
        text="\n".join(out) + "\n",
        lines=len(lines),
        chunks=sum(len(line.chunks) for line in lines),
    )

