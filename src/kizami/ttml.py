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
(not inside the reading of one kanji, and not at the end of a line).

Furigana (``ruby=True``): kanji are written as AMLL ruby, the kanji as the
base and the reading as one or more timed ruby texts. A chunk that starts
inside a kanji (the が of 願) splits its reading, so each part lights up at
its own time. Text that is not a chunk start gets its time from the morae of
the timing file, or else by sharing the chunk's time by character. A timing
document without furigana (a tagger project) can take them from the
generated timing (``readings``): a line gets them when its text is the same.

Display offset: ``offset_ms`` moves every time by the same amount in the
written file only. A positive offset shows the lyrics later (for example to
make up for Bluetooth earphones), a negative one earlier. The timing
document itself is not changed.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from xml.sax.saxutils import escape, quoteattr

from kizami.timing import (
    FORMAT_NAME,
    Chunk,
    Line,
    Ruby,
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
GENERATED_TIMING = "auto_chu.json"  # what kizami generate writes; the tagger opens it
LAST_CHUNK_MS = 1000
MAX_OFFSET_MS = 10000


class TtmlError(ValueError):
    """The input cannot be written as TTML."""


@dataclass
class _Word:
    """A plain span, or furigana: ``text`` is then the kanji and ``ruby`` its timed reading parts."""

    text: str
    begin: int = 0
    end: int = 0
    ruby: list[tuple[str, int, int]] = field(default_factory=list)
    space: bool = False


@dataclass
class TtmlOutput:
    """The TTML text and counts about it."""

    text: str
    lines: int
    chunks: int
    ruby_lines: int = 0  # lines with at least one kanji shown with furigana
    lines_without_readings: int = 0  # lines whose furigana could not be taken from ``readings``


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


def readings_for(source: str | Path, readings: str | Path | None = None) -> TimingDocument | None:
    """The generated timing to take furigana from when ``source`` is a tagger project.

    ``readings`` names the file; by default ``auto_chu.json`` next to the
    project. None when there is no such file or it has no furigana.
    """
    path = Path(readings) if readings is not None else Path(source).with_name(GENERATED_TIMING)
    if readings is None and not path.is_file():
        return None
    doc = load_for_export(path)
    return doc if doc.meta.get("ruby") else None


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


def _hira(text: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)


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


def _positions(line: Line) -> list[int]:
    """Where each chunk starts in the line text."""
    out, at = [], 0
    for chunk in line.chunks:
        out.append(at)
        at += len(chunk.text)
    return out


def _mora_points(line: Line) -> list[tuple[int, int]]:
    """``(position in the line text, start ms)`` of each chunk start and each mora found in its text."""
    points = []
    for at, chunk in zip(_positions(line), line.chunks):
        points.append((at, chunk.start_ms))
        text, i = _hira(chunk.text), 0
        for mora in chunk.morae:
            j = text.find(mora.kana, i)
            if j < 0:
                continue  # sung differently from how it is written: share the time instead
            if j > 0:
                points.append((at + j, mora.start_ms))
            i = j + len(mora.kana)
    return points


def _readings_from(line: Line, source: Line) -> tuple[tuple[Ruby, ...], list[tuple[int, int]]]:
    """The furigana and mora times of ``source`` (same text) for the chunks of ``line``.

    A time inside a chunk that starts at the same place in both moves with
    that chunk's start (the start was fixed in the tagger).
    """
    starts = dict(zip(_positions(line), (c.start_ms for c in line.chunks)))
    src_starts = list(zip(_positions(source), (c.start_ms for c in source.chunks)))
    points = []
    for pos, ms in _mora_points(source):
        if pos in starts:
            continue
        src_at, src_ms = max((p, t) for p, t in src_starts if p <= pos)
        own_at = max(p for p in starts if p <= pos)
        if own_at == src_at:
            ms += starts[own_at] - src_ms
        points.append((pos, ms))
    return source.ruby, points


class _Timeline:
    """Times of the positions in one line's text."""

    def __init__(self, line: Line, ends: list[int], points: list[tuple[int, int]]) -> None:
        self.text = line.text
        self.starts = _positions(line)
        self.chunks = line.chunks
        self.ends = ends
        self.known: list[list[tuple[int, int]]] = []
        for c, chunk in enumerate(line.chunks):
            at, start, end = self.starts[c], chunk.start_ms, ends[c]
            stop = at + len(chunk.text.rstrip())
            inner = sorted((p, t) for p, t in points if at < p < stop and start < t < end)
            row = [(at, start)]
            for p, t in inner:
                if t > row[-1][1] and p > row[-1][0]:
                    row.append((p, t))
            row.append((max(stop, row[-1][0] + 1), end))
            self.known.append(row)

    def chunk_of(self, pos: int) -> int:
        return max(c for c, at in enumerate(self.starts) if at <= pos)

    def time(self, c: int, pos: int) -> int:
        row = self.known[c]
        if pos >= row[-1][0]:
            return row[-1][1]
        for (p0, t0), (p1, t1) in zip(row, row[1:]):
            if p0 <= pos < p1:
                return round(t0 + (t1 - t0) * (pos - p0) / (p1 - p0))
        return row[0][1]


def _words(line: Line, ends: list[int], ruby: tuple[Ruby, ...], points, chunk_spaces: bool) -> list[_Word]:
    tl = _Timeline(line, ends, points)
    text, starts = tl.text, set(tl.starts)
    cuts = sorted(starts | {r.start for r in ruby} | {r.end for r in ruby} | {len(text)})
    words: list[_Word] = []
    current: tuple[Ruby, _Word] | None = None
    for x, y in zip(cuts, cuts[1:]):
        c = tl.chunk_of(x)
        if x in starts and x > 0 and words:
            inside = any(r.start < x < r.end for r in ruby)
            before = line.chunks[c - 1].text
            if not inside and (chunk_spaces or _needs_space(before, line.chunks[c].text)):
                words[-1].space = True
        piece = text[x:y]
        lead = len(piece) - len(piece.lstrip())
        core = piece.strip()
        if lead and words:
            words[-1].space = True
        if not core:
            continue
        a = x + lead
        chunk_stop = tl.starts[c] + len(line.chunks[c].text.rstrip())
        begin = line.chunks[c].start_ms if a == tl.starts[c] else tl.time(c, a)
        later = y + len(text[y:]) - len(text[y:].lstrip())  # where the next text of the chunk starts
        end = tl.time(c, later) if later < chunk_stop else tl.ends[c]
        r = next((r for r in ruby if r.start <= x and y <= r.end), None)
        if r is None:
            current = None
            words.append(_Word(core, begin, end))
        else:
            if current is None or current[0] is not r:
                current = (r, _Word(r.base))
                words.append(current[1])
            current[1].ruby.append((core, begin, end))
        if piece != piece.rstrip():
            words[-1].space = True
    return words


def _apply_offset(words: list[_Word], offset_ms: int) -> None:
    def shift(begin: int, end: int) -> tuple[int, int]:
        b = max(0, begin + offset_ms)
        return b, max(b + 1, end + offset_ms)

    for w in words:
        if w.ruby:
            w.ruby = [(t, *shift(b, e)) for t, b, e in w.ruby]
            w.begin, w.end = min(b for _, b, _ in w.ruby), max(e for _, _, e in w.ruby)
        else:
            w.begin, w.end = shift(w.begin, w.end)


def _word_xml(w: _Word) -> str:
    if not w.ruby:
        return f'<span begin="{format_time(w.begin)}" end="{format_time(w.end)}">{escape(w.text)}</span>'
    texts = "".join(
        f'<span tts:ruby="text" begin="{format_time(b)}" end="{format_time(e)}">{escape(t)}</span>' for t, b, e in w.ruby
    )
    return (
        f'<span tts:ruby="container"><span tts:ruby="base">{escape(w.text)}</span>'
        f'<span tts:ruby="textContainer">{texts}</span></span>'
    )


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
    lang: str = "ja",
    chunk_spaces: bool = True,
    ruby: bool = False,
    readings: TimingDocument | None = None,
) -> TtmlOutput:
    """The document as TTML, with counts. See the module docstring.

    ``chunk_spaces`` puts a space after every chunk. ``ruby`` writes kanji
    with furigana: from the document's own lines, or for lines without them
    from the line with the same text in ``readings`` (the generated timing).
    """
    if not isinstance(offset_ms, int) or isinstance(offset_ms, bool) or abs(offset_ms) > MAX_OFFSET_MS:
        raise TtmlError(f"the display offset must be a whole number of ms from -{MAX_OFFSET_MS} to {MAX_OFFSET_MS}")
    lines = [line for line in doc.lines if line.chunks]
    if not lines:
        raise TtmlError("the timing has no chunks")

    agents: dict[str | None, str] = {}
    for line in lines:
        if line.agent not in agents:
            agents[line.agent] = f"v{len(agents) + 1}"

    own = bool(doc.meta.get("ruby")) or any(line.ruby for line in lines)
    source = [line for line in readings.lines if line.chunks] if (ruby and not own and readings) else []
    body = []
    first_begin, last_end = None, 0
    ruby_lines = without = 0
    at = 0  # next line of ``source`` to look at
    for n, line in enumerate(lines):
        nxt = lines[n + 1].chunks[0].start_ms if n + 1 < len(lines) else None
        ends = _chunk_ends(line, nxt)
        line_ruby: tuple[Ruby, ...] = ()
        points = _mora_points(line)
        if ruby and own:
            line_ruby = line.ruby
        elif ruby and source:
            text = line.text
            match = next((i for i in range(at, len(source)) if source[i].text == text), None)
            if match is None:
                without += 1
            else:
                at = match + 1
                line_ruby, extra = _readings_from(line, source[match])
                points = _mora_points(line) + extra
        ruby_lines += bool(line_ruby)
        words = _words(line, ends, line_ruby, points, chunk_spaces)
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
        ruby_lines=ruby_lines,
        lines_without_readings=without,
    )

