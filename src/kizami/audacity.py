"""Convert an Audacity label track export into a Kizami reference document.

Audacity writes one label per line: ``start<TAB>end<TAB>text``, with times in
seconds. A point label has the same start and end. Lines that start with a
backslash hold a spectral frequency range and carry no timing, so they are
skipped.

The labelling procedure is described in ``docs/labeling-guide.md``.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from kizami.timing import Chunk, Line, TimingDocument

UNCERTAIN_MARKERS = ("?", "？")


class AudacityLabelError(ValueError):
    """The label file cannot be converted."""


def _seconds_to_ms(value: str, line_number: int) -> int:
    try:
        seconds = float(value)
    except ValueError:
        raise AudacityLabelError(f"line {line_number}: '{value}' is not a time in seconds") from None
    if not math.isfinite(seconds) or seconds < 0:
        raise AudacityLabelError(f"line {line_number}: time must be 0 or more, got '{value}'")
    return round(seconds * 1000)


def _parse_line(raw: str, line_number: int) -> Chunk:
    columns = raw.split("\t", 2)
    if len(columns) < 3:
        raise AudacityLabelError(f"line {line_number}: the label has no text")

    start_ms = _seconds_to_ms(columns[0], line_number)
    end_ms = _seconds_to_ms(columns[1], line_number)
    if end_ms < start_ms:
        raise AudacityLabelError(f"line {line_number}: the label ends before it starts")

    text = columns[2].strip()
    uncertain = text.startswith(UNCERTAIN_MARKERS)
    if uncertain:
        text = text[1:].strip()
    if not text:
        raise AudacityLabelError(f"line {line_number}: the label has no text")

    return Chunk(
        text=text,
        start_ms=start_ms,
        end_ms=end_ms if end_ms > start_ms else None,
        uncertain=uncertain,
    )


def document_from_label_text(text: str, meta: dict[str, Any] | None = None) -> TimingDocument:
    """Convert the content of an Audacity label file into a reference document.

    All labels go into one line, ordered by start time. Line breaks do not
    affect evaluation, so the label file does not need to carry them.
    """
    numbered_chunks: list[tuple[int, Chunk]] = []
    for line_number, raw in enumerate(text.lstrip("﻿").splitlines(), start=1):
        if not raw.strip() or raw.startswith("\\"):
            continue
        numbered_chunks.append((line_number, _parse_line(raw, line_number)))

    if not numbered_chunks:
        raise AudacityLabelError("the file has no labels")

    numbered_chunks.sort(key=lambda item: item[1].start_ms)
    for (_, previous), (line_number, chunk) in zip(numbered_chunks, numbered_chunks[1:]):
        if chunk.start_ms == previous.start_ms:
            raise AudacityLabelError(
                f"line {line_number}: '{chunk.text}' starts at the same millisecond "
                f"as '{previous.text}'"
            )

    return TimingDocument(
        kind="reference",
        meta=dict(meta or {}),
        lines=(Line(chunks=tuple(chunk for _, chunk in numbered_chunks)),),
    )


def load_labels(path: str | Path, meta: dict[str, Any] | None = None) -> TimingDocument:
    text = Path(path).read_text(encoding="utf-8-sig")
    return document_from_label_text(text, meta)
