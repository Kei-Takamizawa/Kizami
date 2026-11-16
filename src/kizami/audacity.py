"""Convert an Audacity label track export into a Kizami reference document.

Audacity writes one label per line: ``start<TAB>end<TAB>text``, with times in
seconds. A point label has the same start and end. Lines that start with a
backslash hold a spectral frequency range and carry no timing, so they are
skipped.

A label file can also hold labels without text. These are candidate
positions that the labeller did not use (see ``kizami suggest-labels``).
With ``skip_empty`` they are dropped.

The labelling procedure is described in ``docs/labeling-guide.md``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kizami.timing import Chunk, Line, TimingDocument

UNCERTAIN_MARKERS = ("?", "？")


class AudacityLabelError(ValueError):
    """The label file cannot be converted."""


@dataclass(frozen=True)
class LabelImport:
    """A converted label file, and how many labels without text were dropped."""

    document: TimingDocument
    skipped_empty: int = 0


def _seconds_to_ms(value: str, line_number: int) -> int:
    try:
        seconds = float(value)
    except ValueError:
        raise AudacityLabelError(f"line {line_number}: '{value}' is not a time in seconds") from None
    if not math.isfinite(seconds) or seconds < 0:
        raise AudacityLabelError(f"line {line_number}: time must be 0 or more, got '{value}'")
    return round(seconds * 1000)


def _parse_line(raw: str, line_number: int, skip_empty: bool) -> Chunk | None:
    """Return the chunk of one label line, or None for a dropped label without text."""
    columns = raw.split("\t", 2)
    text = columns[2].strip() if len(columns) == 3 else ""
    if len(columns) < 2 or (not text and not skip_empty):
        raise AudacityLabelError(f"line {line_number}: the label has no text")

    start_ms = _seconds_to_ms(columns[0], line_number)
    end_ms = _seconds_to_ms(columns[1], line_number)
    if end_ms < start_ms:
        raise AudacityLabelError(f"line {line_number}: the label ends before it starts")
    if not text:
        return None

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


def import_label_text(
    text: str, meta: dict[str, Any] | None = None, skip_empty: bool = False
) -> LabelImport:
    """Convert the content of an Audacity label file into a reference document.

    All labels go into one line, ordered by start time. Line breaks do not
    affect evaluation, so the label file does not need to carry them.

    A label without text is an error, unless ``skip_empty`` is set. Then it
    is dropped and counted.
    """
    numbered_chunks: list[tuple[int, Chunk]] = []
    skipped_empty = 0
    for line_number, raw in enumerate(text.lstrip("﻿").splitlines(), start=1):
        if not raw.strip() or raw.startswith("\\"):
            continue
        chunk = _parse_line(raw, line_number, skip_empty)
        if chunk is None:
            skipped_empty += 1
        else:
            numbered_chunks.append((line_number, chunk))

    if not numbered_chunks:
        if skipped_empty:
            raise AudacityLabelError("the file has no labels with text")
        raise AudacityLabelError("the file has no labels")

    numbered_chunks.sort(key=lambda item: item[1].start_ms)
    for (_, previous), (line_number, chunk) in zip(numbered_chunks, numbered_chunks[1:]):
        if chunk.start_ms == previous.start_ms:
            raise AudacityLabelError(
                f"line {line_number}: '{chunk.text}' starts at the same millisecond "
                f"as '{previous.text}'"
            )

    document = TimingDocument(
        kind="reference",
        meta=dict(meta or {}),
        lines=(Line(chunks=tuple(chunk for _, chunk in numbered_chunks)),),
    )
    return LabelImport(document=document, skipped_empty=skipped_empty)


def document_from_label_text(
    text: str, meta: dict[str, Any] | None = None, skip_empty: bool = False
) -> TimingDocument:
    return import_label_text(text, meta, skip_empty).document


def import_labels(
    path: str | Path, meta: dict[str, Any] | None = None, skip_empty: bool = False
) -> LabelImport:
    text = Path(path).read_text(encoding="utf-8-sig")
    return import_label_text(text, meta, skip_empty)


def load_labels(
    path: str | Path, meta: dict[str, Any] | None = None, skip_empty: bool = False
) -> TimingDocument:
    return import_labels(path, meta, skip_empty).document


def label_text_from_document(doc: TimingDocument) -> str:
    """Return Audacity label file content: one point label per chunk, in time order.

    Only start times are written. A person who reviews generated timing in
    Audacity moves point labels, and only start times are a pass criterion.
    """
    chunks = sorted(
        (chunk for line in doc.lines for chunk in line.chunks),
        key=lambda chunk: chunk.start_ms,
    )
    rows = []
    for chunk in chunks:
        seconds = f"{chunk.start_ms / 1000:.6f}"
        text = " ".join(chunk.text.split())
        marker = UNCERTAIN_MARKERS[0] if chunk.uncertain else ""
        rows.append(f"{seconds}\t{seconds}\t{marker}{text}\n")
    return "".join(rows)
