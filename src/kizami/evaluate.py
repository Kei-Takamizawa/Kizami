"""Compare generated chunk timing against hand-made reference timing.

Definitions (see ``docs/design.md`` for the reasons):

* A *boundary* is the start of a chunk: a position in the lyric text plus a
  time. Positions are counted on normalized text (letters, marks, digits).
* A *hit* is a pair of boundaries, one in each file, at the same text
  position and no more than ``tolerance_ms`` apart.
* Recall = hits / reference boundaries.
* Precision = hits / generated boundaries inside the reference excerpt.

The reference may cover only part of a song, for example one chorus. The
excerpt is located in the generated file by its text. If the text repeats,
the occurrence nearest in time to the reference is used.
"""

from __future__ import annotations

import difflib
import math
import statistics
from dataclasses import dataclass
from typing import Iterable, Sequence

from kizami.timing import TimingDocument, normalize_text

DEFAULT_TOLERANCE_MS = 50
DEFAULT_TARGET_RECALL = 0.85
DEFAULT_TARGET_PRECISION = 0.80


class EvaluationError(ValueError):
    """The two documents cannot be compared."""


@dataclass(frozen=True)
class Boundary:
    """The start of one chunk."""

    offset: int  # position in the normalized text
    text: str
    start_ms: int
    end_ms: int | None
    uncertain: bool
    line_index: int


@dataclass(frozen=True)
class BoundaryMatch:
    """A reference boundary and a generated boundary at the same text position."""

    offset: int
    text: str
    ref_ms: int
    hyp_ms: int
    uncertain: bool

    @property
    def error_ms(self) -> int:
        """Generated minus reference. Positive means the generated chunk is late."""
        return self.hyp_ms - self.ref_ms


@dataclass(frozen=True)
class TimingStats:
    count: int
    median_ms: float
    mean_ms: float
    median_abs_ms: float
    p90_abs_ms: float


@dataclass(frozen=True)
class EndTimeChecks:
    """Reference-only figures about generated end times. Not pass criteria."""

    missing_end: int = 0
    non_positive_duration: int = 0
    overlaps: int = 0
    max_overlap_ms: int = 0


@dataclass(frozen=True)
class PairResult:
    tolerance_ms: int
    ref_count: int
    hyp_count: int
    matched: tuple[BoundaryMatch, ...]
    missed: tuple[Boundary, ...]  # in the reference only: a split is missing
    extra: tuple[Boundary, ...]  # in the generated file only: an extra split
    uncertain_ref_count: int
    end_time_checks: EndTimeChecks

    @property
    def hits(self) -> tuple[BoundaryMatch, ...]:
        return tuple(m for m in self.matched if abs(m.error_ms) <= self.tolerance_ms)

    @property
    def hit_count(self) -> int:
        return len(self.hits)

    @property
    def recall(self) -> float | None:
        return _ratio(self.hit_count, self.ref_count)

    @property
    def precision(self) -> float | None:
        return _ratio(self.hit_count, self.hyp_count)

    @property
    def certain_ref_count(self) -> int:
        return self.ref_count - self.uncertain_ref_count

    @property
    def certain_hit_count(self) -> int:
        return sum(1 for m in self.hits if not m.uncertain)

    @property
    def recall_excluding_uncertain(self) -> float | None:
        return _ratio(self.certain_hit_count, self.certain_ref_count)

    @property
    def timing(self) -> TimingStats | None:
        return timing_stats([m.error_ms for m in self.matched])


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def timing_stats(errors_ms: Sequence[int]) -> TimingStats | None:
    """Summarize signed errors (generated minus reference)."""
    if not errors_ms:
        return None
    absolute = sorted(abs(e) for e in errors_ms)
    p90_rank = math.ceil(0.9 * len(absolute))  # nearest-rank percentile
    return TimingStats(
        count=len(errors_ms),
        median_ms=statistics.median(errors_ms),
        mean_ms=statistics.fmean(errors_ms),
        median_abs_ms=statistics.median(absolute),
        p90_abs_ms=absolute[p90_rank - 1],
    )


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% confidence interval for a proportion (Wilson score interval)."""
    if total == 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return (max(0.0, center - half), min(1.0, center + half))


def _boundaries(doc: TimingDocument) -> tuple[str, list[Boundary]]:
    """Return the normalized text of a document and the start of each chunk."""
    parts: list[str] = []
    boundaries: list[Boundary] = []
    offset = 0
    for line_index, line in enumerate(doc.lines):
        for chunk in line.chunks:
            normalized = normalize_text(chunk.text)
            if not normalized:
                continue  # punctuation or symbols only: no position in the text
            boundaries.append(
                Boundary(
                    offset=offset,
                    text=chunk.text,
                    start_ms=chunk.start_ms,
                    end_ms=chunk.end_ms,
                    uncertain=chunk.uncertain,
                    line_index=line_index,
                )
            )
            parts.append(normalized)
            offset += len(normalized)
    return "".join(parts), boundaries


def _text_mismatch_message(ref_text: str, hyp_text: str) -> str:
    matcher = difflib.SequenceMatcher(None, ref_text, hyp_text, autojunk=False)
    block = matcher.find_longest_match(0, len(ref_text), 0, len(hyp_text))
    shared = ref_text[block.a : block.a + block.size]
    ref_next = ref_text[block.a + block.size : block.a + block.size + 12]
    hyp_next = hyp_text[block.b + block.size : block.b + block.size + 12]
    return (
        "the reference text was not found in the generated text. "
        f'Longest shared part: "{shared[-30:]}". After it, the reference continues with '
        f'"{ref_next}" and the generated text continues with "{hyp_next}". '
        "Check the label text for typing mistakes."
    )


def _locate_excerpt(
    ref_text: str, ref_start_ms: int, hyp_text: str, hyp_boundaries: list[Boundary]
) -> int:
    """Return the offset in the generated text where the reference excerpt starts."""
    occurrences = []
    index = hyp_text.find(ref_text)
    while index != -1:
        occurrences.append(index)
        index = hyp_text.find(ref_text, index + 1)
    if not occurrences:
        raise EvaluationError(_text_mismatch_message(ref_text, hyp_text))

    def distance_ms(occurrence: int) -> int:
        # Time of the generated chunk that covers the first excerpt character.
        covering = [b for b in hyp_boundaries if b.offset <= occurrence][-1]
        return abs(covering.start_ms - ref_start_ms)

    return min(occurrences, key=distance_ms)


def _end_time_checks(boundaries: Sequence[Boundary]) -> EndTimeChecks:
    missing_end = sum(1 for b in boundaries if b.end_ms is None)
    non_positive = sum(1 for b in boundaries if b.end_ms is not None and b.end_ms <= b.start_ms)
    # A long chunk can run past several later chunks, so every later chunk
    # of the same line is checked, not only the next one.
    overlap_amounts = [
        earlier.end_ms - later.start_ms
        for index, earlier in enumerate(boundaries)
        if earlier.end_ms is not None
        for later in boundaries[index + 1 :]
        if later.line_index == earlier.line_index and earlier.end_ms > later.start_ms
    ]
    return EndTimeChecks(
        missing_end=missing_end,
        non_positive_duration=non_positive,
        overlaps=len(overlap_amounts),
        max_overlap_ms=max(overlap_amounts, default=0),
    )


def evaluate_pair(
    ref: TimingDocument, hyp: TimingDocument, tolerance_ms: int = DEFAULT_TOLERANCE_MS
) -> PairResult:
    """Compare one reference document with one generated document."""
    ref_text, ref_boundaries = _boundaries(ref)
    if not ref_boundaries:
        raise EvaluationError("the reference has no chunk that contains letters")
    hyp_text, all_hyp_boundaries = _boundaries(hyp)

    first_ref_ms = min(b.start_ms for b in ref_boundaries)
    excerpt_start = _locate_excerpt(ref_text, first_ref_ms, hyp_text, all_hyp_boundaries)
    excerpt_end = excerpt_start + len(ref_text)
    hyp_boundaries = [b for b in all_hyp_boundaries if excerpt_start <= b.offset < excerpt_end]
    hyp_by_offset = {b.offset - excerpt_start: b for b in hyp_boundaries}
    ref_offsets = {b.offset for b in ref_boundaries}

    matched = tuple(
        BoundaryMatch(
            offset=b.offset,
            text=b.text,
            ref_ms=b.start_ms,
            hyp_ms=hyp_by_offset[b.offset].start_ms,
            uncertain=b.uncertain,
        )
        for b in ref_boundaries
        if b.offset in hyp_by_offset
    )
    return PairResult(
        tolerance_ms=tolerance_ms,
        ref_count=len(ref_boundaries),
        hyp_count=len(hyp_boundaries),
        matched=matched,
        missed=tuple(b for b in ref_boundaries if b.offset not in hyp_by_offset),
        extra=tuple(b for b in hyp_boundaries if b.offset - excerpt_start not in ref_offsets),
        uncertain_ref_count=sum(1 for b in ref_boundaries if b.uncertain),
        end_time_checks=_end_time_checks(hyp_boundaries),
    )


def combine_results(results: Iterable[PairResult]) -> PairResult:
    """Pool boundaries from several songs into one result (micro-average)."""
    results = list(results)
    if not results:
        raise EvaluationError("there is no result to combine")
    tolerances = {r.tolerance_ms for r in results}
    if len(tolerances) != 1:
        raise EvaluationError("results with different tolerances cannot be combined")
    checks = [r.end_time_checks for r in results]
    return PairResult(
        tolerance_ms=tolerances.pop(),
        ref_count=sum(r.ref_count for r in results),
        hyp_count=sum(r.hyp_count for r in results),
        matched=tuple(m for r in results for m in r.matched),
        missed=tuple(b for r in results for b in r.missed),
        extra=tuple(b for r in results for b in r.extra),
        uncertain_ref_count=sum(r.uncertain_ref_count for r in results),
        end_time_checks=EndTimeChecks(
            missing_end=sum(c.missing_end for c in checks),
            non_positive_duration=sum(c.non_positive_duration for c in checks),
            overlaps=sum(c.overlaps for c in checks),
            max_overlap_ms=max(c.max_overlap_ms for c in checks),
        ),
    )


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _seconds(ms: int) -> str:
    return f"{ms / 1000:.3f}"


def _score_line(name: str, hits: int, total: int, target: float) -> str:
    value = _ratio(hits, total)
    interval = wilson_interval(hits, total)
    if value is None or interval is None:
        return f"  {name:<10}: n/a (no boundaries)"
    verdict = "PASS" if value >= target else "FAIL"
    return (
        f"  {name:<10}: {_percent(value)} ({hits}/{total})  "
        f"target >= {target * 100:.0f}%  {verdict}  "
        f"95% CI {_percent(interval[0])} to {_percent(interval[1])}"
    )


def _format_result(
    label: str, result: PairResult, target_recall: float, target_precision: float, details: bool
) -> list[str]:
    tolerance = result.tolerance_ms
    lines = [
        f"{label}",
        f"  Reference boundaries: {result.ref_count}   Generated boundaries: {result.hyp_count}"
        f"   Hits (same position, within ±{tolerance} ms): {result.hit_count}",
        _score_line("Recall", result.hit_count, result.ref_count, target_recall),
        _score_line("Precision", result.hit_count, result.hyp_count, target_precision),
    ]
    if result.uncertain_ref_count:
        lines.append(
            f"  Recall without uncertain reference boundaries: "
            f"{_percent(result.recall_excluding_uncertain)} "
            f"({result.certain_hit_count}/{result.certain_ref_count}, "
            f"{result.uncertain_ref_count} excluded)"
        )

    late = sum(1 for m in result.matched if m.error_ms > tolerance)
    early = sum(1 for m in result.matched if m.error_ms < -tolerance)
    lines.append(
        f"  Misses: missing splits {len(result.missed)}, extra splits {len(result.extra)}, "
        f"too late {late}, too early {early}"
    )

    stats = result.timing
    if stats is not None:
        lines.append(
            f"  Timing error at {stats.count} shared positions (generated minus reference): "
            f"median {stats.median_ms:+.0f} ms, mean {stats.mean_ms:+.0f} ms, "
            f"median |error| {stats.median_abs_ms:.0f} ms, 90th percentile |error| "
            f"{stats.p90_abs_ms:.0f} ms"
        )

    checks = result.end_time_checks
    lines.append(
        f"  End times (for reference, not a pass criterion): missing {checks.missing_end}, "
        f"zero or negative length {checks.non_positive_duration}, overlaps {checks.overlaps} "
        f"(largest {checks.max_overlap_ms} ms)"
    )

    if details:
        for match in result.matched:
            if abs(match.error_ms) > tolerance:
                lines.append(
                    f"    off by {match.error_ms:+d} ms: '{match.text}' reference "
                    f"{_seconds(match.ref_ms)} s, generated {_seconds(match.hyp_ms)} s"
                )
        for boundary in result.missed:
            lines.append(
                f"    missing split: '{boundary.text}' at {_seconds(boundary.start_ms)} s"
            )
        for boundary in result.extra:
            lines.append(f"    extra split: '{boundary.text}' at {_seconds(boundary.start_ms)} s")
    return lines


def format_report(
    labelled_results: Sequence[tuple[str, PairResult]],
    target_recall: float = DEFAULT_TARGET_RECALL,
    target_precision: float = DEFAULT_TARGET_PRECISION,
    details: bool = False,
) -> str:
    """Render results as text. With several songs, a pooled total is added."""
    lines: list[str] = []
    for label, result in labelled_results:
        lines.extend(_format_result(label, result, target_recall, target_precision, details))
        lines.append("")
    if len(labelled_results) > 1:
        total = combine_results(result for _, result in labelled_results)
        lines.extend(
            _format_result(
                f"All songs ({len(labelled_results)}, boundaries pooled)",
                total,
                target_recall,
                target_precision,
                details=False,
            )
        )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def result_to_dict(result: PairResult) -> dict:
    """Machine-readable summary of one result."""
    stats = result.timing
    checks = result.end_time_checks
    return {
        "tolerance_ms": result.tolerance_ms,
        "ref_count": result.ref_count,
        "hyp_count": result.hyp_count,
        "hit_count": result.hit_count,
        "recall": result.recall,
        "precision": result.precision,
        "recall_ci95": wilson_interval(result.hit_count, result.ref_count),
        "precision_ci95": wilson_interval(result.hit_count, result.hyp_count),
        "uncertain_ref_count": result.uncertain_ref_count,
        "recall_excluding_uncertain": result.recall_excluding_uncertain,
        "missing_splits": len(result.missed),
        "extra_splits": len(result.extra),
        "timing": None
        if stats is None
        else {
            "count": stats.count,
            "median_ms": stats.median_ms,
            "mean_ms": stats.mean_ms,
            "median_abs_ms": stats.median_abs_ms,
            "p90_abs_ms": stats.p90_abs_ms,
        },
        "end_time_checks": {
            "missing_end": checks.missing_end,
            "non_positive_duration": checks.non_positive_duration,
            "overlaps": checks.overlaps,
            "max_overlap_ms": checks.max_overlap_ms,
        },
    }
