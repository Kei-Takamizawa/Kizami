"""Where a sung sound stops: chunk end times from the loudness of a vocal track.

Timing files give each chunk a start. Lyric players such as AMLL also need an
end, because they fill the text from its start to its end. A chunk ends where
its sound stops, but never after the next chunk starts.

This is the same rule as the tagger's (``soundEnd`` in ``tagger/index.html``),
so a chunk ends at the same place in the tagger and in an exported file:

* Loudness: mean power per 2 ms frame, averaged over about 22 ms, in dB.
* Quiet: 40 dB below the loudest frame of the track.
* A sound stops at the first 80 ms of quiet that begins 60 ms or more after
  the chunk start. With no such quiet before the next chunk start, the chunk
  ends where the next chunk starts.

This module needs ``numpy``. Install it with ``pip install "kizami[audio]"``.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from kizami.onsets import OnsetError, read_wav_mono
from kizami.timing import TimingDocument

HOP_S = 0.002
SMOOTH_FRAMES = 5  # on each side: 11 frames, about 22 ms
FLOOR_BELOW_PEAK_DB = 50.0
QUIET_ABOVE_FLOOR_DB = 10.0
QUIET_RUN_S = 0.08
SKIP_AFTER_START_S = 0.06
MIN_LENGTH_S = 0.03
# A chunk with no later chunk ends at most this long after its start.
LAST_LIMIT_S = 4.0


class LoudnessError(ValueError):
    """The audio cannot be read."""


@dataclass(frozen=True)
class Loudness:
    """The loudness curve of one track."""

    db: np.ndarray  # one value per frame
    hop_s: float  # frame length in seconds
    quiet_db: float  # below this level a frame counts as quiet

    def sound_end(self, start_s: float, limit_s: float) -> float:
        """Where the sound that starts at ``start_s`` stops, no later than ``limit_s``."""
        need = max(1, round(QUIET_RUN_S / self.hop_s))
        first = round((start_s + SKIP_AFTER_START_S) / self.hop_s)
        last = min(len(self.db), round(limit_s / self.hop_s))
        end = limit_s
        if last - first >= need:
            quiet = (self.db[first:last] < self.quiet_db).astype(np.int64)
            # Number of quiet frames in each window of ``need`` frames.
            full = np.flatnonzero(np.convolve(quiet, np.ones(need, dtype=np.int64), "valid") == need)
            if len(full):
                end = (first + int(full[0])) * self.hop_s
        return max(end, start_s + MIN_LENGTH_S)


def loudness_from_samples(samples: np.ndarray, rate: int) -> Loudness:
    """Loudness curve of mono samples from -1 to 1."""
    hop = max(1, round(rate * HOP_S))
    count = -(-len(samples) // hop)
    if count == 0:
        return Loudness(np.zeros(0), hop / rate, 0.0)
    padded = np.zeros(count * hop, dtype=np.float64)
    padded[: len(samples)] = samples
    power = (padded.reshape(count, hop) ** 2).sum(axis=1)
    # The last frame may be shorter than the others.
    sizes = np.full(count, hop, dtype=np.float64)
    sizes[-1] = len(samples) - (count - 1) * hop
    power /= sizes
    total = np.concatenate([[0.0], np.cumsum(power)])
    index = np.arange(count)
    lo = np.maximum(index - SMOOTH_FRAMES, 0)
    hi = np.minimum(index + SMOOTH_FRAMES, count - 1)
    mean = (total[hi + 1] - total[lo]) / (hi - lo + 1)
    db = 10 * np.log10(mean + 1e-12)
    quiet = float(db.max()) - FLOOR_BELOW_PEAK_DB + QUIET_ABOVE_FLOOR_DB
    return Loudness(db, hop / rate, quiet)


def loudness_from_wav(path: str | Path) -> Loudness:
    try:
        samples, rate = read_wav_mono(path)
    except (OSError, OnsetError) as exc:
        raise LoudnessError(f"{path}: {exc}") from exc
    return loudness_from_samples(samples, rate)


def add_end_times(doc: TimingDocument, loudness: Loudness) -> TimingDocument:
    """A copy of ``doc`` where every chunk without ``end_ms`` ends where its sound stops.

    The limit for a chunk is the start of the next chunk in song order (the
    first chunk of the next line for the last chunk of a line). Ends that are
    already set are kept.
    """
    starts = sorted({c.start_ms for line in doc.lines for c in line.chunks})
    new_lines = []
    for line in doc.lines:
        chunks = []
        for chunk in line.chunks:
            if chunk.end_ms is None:
                later = _next_after(starts, chunk.start_ms)
                limit_s = later / 1000 if later is not None else chunk.start_ms / 1000 + LAST_LIMIT_S
                end_s = loudness.sound_end(chunk.start_ms / 1000, limit_s)
                chunk = replace(chunk, end_ms=round(end_s * 1000))
            chunks.append(chunk)
        new_lines.append(replace(line, chunks=tuple(chunks)))
    return replace(doc, lines=tuple(new_lines))


def _next_after(starts: list[int], ms: int) -> int | None:
    i = bisect.bisect_right(starts, ms)
    return starts[i] if i < len(starts) else None
