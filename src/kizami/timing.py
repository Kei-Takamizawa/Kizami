"""Kizami timing JSON: data model, validation, and text normalization.

The format is described for humans in ``docs/timing-json-format.md``.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FORMAT_NAME = "kizami-timing"
FORMAT_VERSION = 1
KINDS = ("reference", "generated")


class TimingFormatError(ValueError):
    """The input is not a valid Kizami timing document."""


@dataclass(frozen=True)
class Mora:
    """One sung kana (with its small kana) inside a chunk, and when it starts."""

    kana: str
    start_ms: int
    uncertain: bool = False


@dataclass(frozen=True)
class Chunk:
    """One unit of lyric text that lights up at ``start_ms``."""

    text: str
    start_ms: int
    end_ms: int | None = None
    uncertain: bool = False
    morae: tuple[Mora, ...] = ()


@dataclass(frozen=True)
class Line:
    chunks: tuple[Chunk, ...]
    agent: str | None = None


@dataclass(frozen=True)
class TimingDocument:
    lines: tuple[Line, ...]
    kind: str = "generated"
    meta: dict[str, Any] = field(default_factory=dict)


def normalize_text(text: str) -> str:
    """Reduce text to the characters that identify a position in the lyrics.

    Two files can write the same lyrics with different spacing, punctuation,
    or character width. Positions are compared after this normalization:
    NFKC, case folding, then only letters, marks, and digits are kept.
    """
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(ch for ch in folded if unicodedata.category(ch)[0] in "LMN")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _chunk_from_dict(data: Any, path: str) -> Chunk:
    if not isinstance(data, dict):
        raise TimingFormatError(f"{path} must be an object")

    text = data.get("text")
    if not isinstance(text, str) or not text:
        raise TimingFormatError(f"{path}.text must be a non-empty string")

    start_ms = data.get("start_ms")
    if not _is_int(start_ms) or start_ms < 0:
        raise TimingFormatError(f"{path}.start_ms must be an integer of 0 or more")

    end_ms = data.get("end_ms")
    if end_ms is not None and (not _is_int(end_ms) or end_ms < 0):
        raise TimingFormatError(f"{path}.end_ms must be an integer of 0 or more, or null")

    uncertain = data.get("uncertain", False)
    if not isinstance(uncertain, bool):
        raise TimingFormatError(f"{path}.uncertain must be true or false")

    morae = data.get("morae", [])
    if not isinstance(morae, list):
        raise TimingFormatError(f"{path}.morae must be a list")

    return Chunk(
        text=text,
        start_ms=start_ms,
        end_ms=end_ms,
        uncertain=uncertain,
        morae=tuple(_mora_from_dict(m, f"{path}.morae[{i}]") for i, m in enumerate(morae)),
    )


def _mora_from_dict(data: Any, path: str) -> Mora:
    if not isinstance(data, dict):
        raise TimingFormatError(f"{path} must be an object")
    kana = data.get("kana")
    if not isinstance(kana, str) or not kana:
        raise TimingFormatError(f"{path}.kana must be a non-empty string")
    start_ms = data.get("start_ms")
    if not _is_int(start_ms) or start_ms < 0:
        raise TimingFormatError(f"{path}.start_ms must be an integer of 0 or more")
    uncertain = data.get("uncertain", False)
    if not isinstance(uncertain, bool):
        raise TimingFormatError(f"{path}.uncertain must be true or false")
    return Mora(kana=kana, start_ms=start_ms, uncertain=uncertain)


def _line_from_dict(data: Any, path: str) -> Line:
    if not isinstance(data, dict):
        raise TimingFormatError(f"{path} must be an object")

    agent = data.get("agent")
    if agent is not None and not isinstance(agent, str):
        raise TimingFormatError(f"{path}.agent must be a string or null")

    chunks = data.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        raise TimingFormatError(f"{path}.chunks must be a non-empty list")

    return Line(
        agent=agent,
        chunks=tuple(
            _chunk_from_dict(chunk, f"{path}.chunks[{i}]") for i, chunk in enumerate(chunks)
        ),
    )


def document_from_dict(data: Any) -> TimingDocument:
    """Build a document from parsed JSON. Raise TimingFormatError if invalid.

    Only the structure is checked here. Timing quality problems, such as an
    end time before a start time, are accepted so that evaluation can count
    and report them.
    """
    if not isinstance(data, dict):
        raise TimingFormatError("the document root must be an object")
    if data.get("format") != FORMAT_NAME:
        raise TimingFormatError(f'format must be "{FORMAT_NAME}"')
    if data.get("version") != FORMAT_VERSION:
        raise TimingFormatError(f"version must be {FORMAT_VERSION}")

    kind = data.get("kind")
    if kind not in KINDS:
        raise TimingFormatError(f"kind must be one of: {', '.join(KINDS)}")

    meta = data.get("meta", {})
    if not isinstance(meta, dict):
        raise TimingFormatError("meta must be an object")

    lines = data.get("lines")
    if not isinstance(lines, list) or not lines:
        raise TimingFormatError("lines must be a non-empty list")

    return TimingDocument(
        kind=kind,
        meta=meta,
        lines=tuple(_line_from_dict(line, f"lines[{i}]") for i, line in enumerate(lines)),
    )


def _chunk_to_dict(chunk: Chunk) -> dict[str, Any]:
    data: dict[str, Any] = {"text": chunk.text, "start_ms": chunk.start_ms}
    if chunk.end_ms is not None:
        data["end_ms"] = chunk.end_ms
    if chunk.uncertain:
        data["uncertain"] = True
    if chunk.morae:
        data["morae"] = [
            {"kana": m.kana, "start_ms": m.start_ms, **({"uncertain": True} if m.uncertain else {})}
            for m in chunk.morae
        ]
    return data


def _line_to_dict(line: Line) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if line.agent is not None:
        data["agent"] = line.agent
    data["chunks"] = [_chunk_to_dict(chunk) for chunk in line.chunks]
    return data


def document_to_dict(doc: TimingDocument) -> dict[str, Any]:
    return {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "kind": doc.kind,
        "meta": doc.meta,
        "lines": [_line_to_dict(line) for line in doc.lines],
    }


def load_document(path: str | Path) -> TimingDocument:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TimingFormatError(f"{path} is not valid JSON: {exc}") from exc
    try:
        return document_from_dict(data)
    except TimingFormatError as exc:
        raise TimingFormatError(f"{path}: {exc}") from exc


def save_document(doc: TimingDocument, path: str | Path) -> None:
    text = json.dumps(document_to_dict(doc), ensure_ascii=False, indent=2)
    Path(path).write_text(text + "\n", encoding="utf-8")
