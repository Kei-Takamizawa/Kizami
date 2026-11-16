"""Find the times where a sound starts in a vocal track.

The result is a list of *candidates* for labelling: places where a person
may put a chunk start. It is made to find too many places, not too few. A
person decides which candidates are real chunk starts.

Method: short-time spectrum in decibels with a floor, then the mean rise per
frequency bin between nearby frames (spectral flux), then peak picking.
Decibels make the first quiet part of a sound count as much as the loud
part, so a peak sits near the start of the sound, not at its loudest point.

This module needs ``numpy``. Install it with ``pip install "kizami[audio]"``.
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Sequence

import numpy as np

WINDOW_MS = 12.0
HOP_MS = 4.0
LAG_FRAMES = 2
BAND_HZ = (80.0, 10000.0)
FLOOR_DB = 50.0
LOCAL_MAX_MS = 30.0
LOCAL_MEAN_MS = 100.0
# A real onset is followed by sound. The level shortly after a candidate
# must not be much lower than the level shortly before it. This removes the
# click at the place where a sound stops suddenly.
GATE_FROM_MS = 8.0
GATE_TO_MS = 32.0
GATE_DROP_DB = 6.0

DEFAULT_THRESHOLD_DB = 2.0
DEFAULT_MIN_GAP_MS = 60


class OnsetError(ValueError):
    """The audio cannot be read or analysed."""


def read_wav_mono(path: str | Path) -> tuple[np.ndarray, int]:
    """Read a PCM WAV file. Return samples from -1.0 to 1.0 (channels averaged) and the rate."""
    try:
        with wave.open(str(path), "rb") as wav:
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            rate = wav.getframerate()
            raw = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError) as exc:
        raise OnsetError(
            f"not a PCM WAV file ({exc}). Convert it first, for example: "
            "ffmpeg -i INPUT -c:a pcm_s16le OUTPUT.wav"
        ) from exc

    if width == 1:
        values = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    elif width == 2:
        values = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 2**15
    elif width == 3:
        data = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        joined = data[:, 0] | (data[:, 1] << 8) | (data[:, 2] << 16)
        joined = np.where(joined >= 2**23, joined - 2**24, joined)
        values = joined.astype(np.float64) / 2**23
    elif width == 4:
        values = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2**31
    else:
        raise OnsetError(f"WAV sample width of {width} bytes is not supported")

    return values.reshape(-1, channels).mean(axis=1), rate


def _spectrum_db(samples: np.ndarray, rate: int) -> np.ndarray:
    """Return frames x bins in decibels. Frame ``i`` is centred on sample ``i * hop``."""
    window = max(16, round(rate * WINDOW_MS / 1000))
    hop = max(1, round(rate * HOP_MS / 1000))
    padded = np.concatenate([np.zeros(window // 2), samples, np.zeros(window)])
    count = 1 + len(samples) // hop
    index = np.arange(window)[None, :] + (np.arange(count) * hop)[:, None]
    frames = padded[index] * np.hanning(window)[None, :]
    magnitude = np.abs(np.fft.rfft(frames, axis=1))

    freqs = np.fft.rfftfreq(window, d=1.0 / rate)
    magnitude = magnitude[:, (freqs >= BAND_HZ[0]) & (freqs <= BAND_HZ[1])]

    loudest = magnitude.max()
    if loudest <= 0:
        return np.zeros_like(magnitude)
    floor = loudest * 10 ** (-FLOOR_DB / 20)
    return 20 * np.log10(np.maximum(magnitude, floor) / floor)


def _frame_power(samples: np.ndarray, rate: int, count: int) -> np.ndarray:
    """Return the mean power of each hop-long block. Block ``i`` starts at sample ``i * hop``."""
    hop = max(1, round(rate * HOP_MS / 1000))
    padded = np.concatenate([samples, np.zeros(count * hop)])[: count * hop]
    return (padded.reshape(count, hop) ** 2).mean(axis=1)


def _sound_follows(power: np.ndarray, frame: int, frame_ms: float) -> bool:
    near = max(1, round(GATE_FROM_MS / frame_ms))
    far = max(near + 1, round(GATE_TO_MS / frame_ms))
    after = power[frame + near : frame + far]
    before = power[max(0, frame - far) : max(0, frame - near)]
    if after.size == 0:
        return False
    if before.size == 0:
        return True
    return after.mean() >= before.mean() * 10 ** (-GATE_DROP_DB / 10)


def detect_onsets(
    samples: Sequence[float] | np.ndarray,
    rate: int,
    threshold_db: float = DEFAULT_THRESHOLD_DB,
    min_gap_ms: int = DEFAULT_MIN_GAP_MS,
) -> list[int]:
    """Return onset times in milliseconds, in order.

    ``threshold_db`` is how far the mean rise per frequency bin must stand
    above its local average. A lower value gives more candidates.
    """
    samples = np.asarray(samples, dtype=np.float64)
    hop = max(1, round(rate * HOP_MS / 1000))
    window = max(16, round(rate * WINDOW_MS / 1000))
    if len(samples) < window:
        return []

    spectrum = _spectrum_db(samples, rate)
    rise = np.maximum(spectrum[LAG_FRAMES:] - spectrum[:-LAG_FRAMES], 0.0).mean(axis=1)
    flux = np.concatenate([np.zeros(LAG_FRAMES), rise])

    frame_ms = 1000.0 * hop / rate
    reach_max = max(1, round(LOCAL_MAX_MS / frame_ms))
    reach_mean = max(1, round(LOCAL_MEAN_MS / frame_ms))

    power = _frame_power(samples, rate, len(flux))

    onsets: list[int] = []
    for frame in np.flatnonzero(flux > threshold_db):
        low = max(0, frame - reach_max)
        if flux[frame] < flux[low : frame + reach_max + 1].max():
            continue
        mean_low = max(0, frame - reach_mean)
        if flux[frame] < flux[mean_low : frame + reach_mean + 1].mean() + threshold_db:
            continue
        if not _sound_follows(power, frame, frame_ms):
            continue
        time_ms = round(frame * frame_ms)
        if onsets and time_ms - onsets[-1] < min_gap_ms:
            continue
        onsets.append(time_ms)
    return onsets


def label_text_from_onsets(onsets_ms: Sequence[int]) -> str:
    """Return Audacity label file content: one point label without text per onset."""
    return "".join(f"{ms / 1000:.6f}\t{ms / 1000:.6f}\t\n" for ms in onsets_ms)
