"""What the generator reads from the vocal track itself.

* ``detect_attacks``: where a new sound starts, with a strength (spectral flux
  over 40 bands, normalised by a moving median). This is a different detector
  from :mod:`kizami.onsets`, which ``suggest-labels`` uses; the two were tuned
  for different jobs.
* ``envelope``: the loudness curve used to find where the previous sound ends.
* ``hiss_frames`` / ``hiss_start``: where a sibilant (さ, し, つ ...) begins
  before its vowel.
* ``load_16k``: the track as 16 kHz mono for the aligners, decoded by ffmpeg.

Needs numpy and torch. ``load_16k`` also needs the ``ffmpeg`` program.
"""

from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path

import numpy as np

SR = 16000  # sample rate of the aligners
HOP = 220  # 5 ms at 44.1 kHz
NFFT = 1024
LAG = 2  # frames
MIN_GAP_S = 0.07
GATE_DB = -40.0  # relative to the loudest frame
SIBILANT = set("さしすせそざじずぜぞつちづぢ")


class AudioError(ValueError):
    """The audio cannot be read."""


def load_mono(path: str | Path) -> tuple[np.ndarray, int]:
    """PCM WAV as float32 samples from -1 to 1 (channels averaged), and the rate."""
    from kizami.onsets import OnsetError, read_wav_mono

    try:
        x, sr = read_wav_mono(path)
    except OnsetError as exc:
        raise AudioError(f"{path}: {exc}") from exc
    return x.astype(np.float32), sr


def load_16k(path: str | Path) -> np.ndarray:
    """The track as 16 kHz mono float32, decoded and resampled by ffmpeg."""
    if shutil.which("ffmpeg") is None:
        raise AudioError("the ffmpeg program is needed to read the audio. Install it, for example: brew install ffmpeg")
    cmd = ["ffmpeg", "-nostdin", "-threads", "0", "-i", str(path), "-f", "s16le", "-ac", "1",
           "-acodec", "pcm_s16le", "-ar", str(SR), "-"]  # fmt: skip
    run = subprocess.run(cmd, capture_output=True)
    if run.returncode != 0:
        tail = run.stderr.decode("utf-8", "replace").strip().splitlines()[-1:]
        raise AudioError(f"{path}: ffmpeg could not read it ({' '.join(tail)})")
    return np.frombuffer(run.stdout, np.int16).flatten().astype(np.float32) / 32768.0


def detect_attacks(x: np.ndarray, sr: int, delta: float = 0.6) -> tuple[list[float], list[float], float]:
    """Attack times in seconds, their strengths, and the track length in seconds."""
    import torch

    spec = torch.stft(torch.from_numpy(x), NFFT, HOP, window=torch.hann_window(NFFT),
                      center=True, return_complex=True).abs().numpy()  # fmt: skip
    freqs = np.linspace(0, sr / 2, spec.shape[0])
    edges = np.geomspace(80, 12000, 41)
    bands = np.stack([spec[(freqs >= lo) & (freqs < hi)].sum(0) if ((freqs >= lo) & (freqs < hi)).any()
                      else np.zeros(spec.shape[1]) for lo, hi in zip(edges[:-1], edges[1:])])  # fmt: skip
    logb = np.log1p(50 * bands)
    flux = np.maximum(logb[:, LAG:] - logb[:, :-LAG], 0).sum(0)
    flux = np.concatenate([np.zeros(LAG), flux])
    fps = sr / HOP
    # Local normalisation: subtract a moving median, divide by the overall level.
    k = int(0.2 * fps) | 1
    pad = np.pad(flux, k // 2, mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(pad, k)
    med = np.median(win, axis=1)
    novelty = np.maximum(flux - med, 0)
    positive = novelty[novelty > 0]
    if not len(positive):
        return [], [], len(x) / sr
    novelty = novelty / np.sqrt(np.mean(positive**2))
    # Energy gate: something must be sung just after the attack.
    rms = np.sqrt((spec**2).mean(0))
    db = 20 * np.log10(rms / (rms.max() or 1.0) + 1e-9)
    ahead = int(0.06 * fps)
    loud_ahead = np.array([db[i : i + ahead + 1].max() for i in range(len(db))])
    r = int(0.03 * fps)
    peaks = []
    for i in range(r, len(novelty) - r):
        v = novelty[i]
        if v < delta or loud_ahead[i] < GATE_DB:
            continue
        if v < novelty[i - r : i + r + 1].max():
            continue
        peaks.append((i, v))
    # Minimum gap: keep the stronger peak.
    kept = []
    for i, v in peaks:
        if kept and (i - kept[-1][0]) / fps < MIN_GAP_S:
            if v > kept[-1][1]:
                kept[-1] = (i, v)
        else:
            kept.append((i, v))
    times = [max(0.0, (i - LAG / 2) / fps) for i, _ in kept]
    return times, [float(v) for _, v in kept], len(x) / sr


def envelope(x: np.ndarray, sr: int, hop_s: float = 0.0025, win_s: float = 0.012) -> tuple[np.ndarray, np.ndarray]:
    """RMS level per frame and the frame centre times in seconds."""
    hop, win = int(sr * hop_s), int(sr * win_s)
    c = np.concatenate([[0.0], np.cumsum(x.astype(np.float64) ** 2)])
    idx = np.arange(0, len(x) - win, hop)
    return np.sqrt((c[idx + win] - c[idx]) / win), (idx + win / 2) / sr


def hiss_frames(x: np.ndarray, sr: int) -> tuple[np.ndarray, float]:
    """Per 5 ms frame: True where the track is dominated by high-frequency noise. Also frames per second."""
    import torch

    spec = torch.stft(torch.from_numpy(x), 1024, HOP, window=torch.hann_window(1024),
                      center=True, return_complex=True).abs().numpy() ** 2  # fmt: skip
    f = np.linspace(0, sr / 2, spec.shape[0])
    hi = spec[(f >= 3000) & (f < 10000)].sum(0) + 1e-12
    lo = spec[(f >= 200) & (f < 2500)].sum(0) + 1e-12
    hi_db = 10 * np.log10(hi / hi.max())
    return (hi_db > -45) & (10 * np.log10(hi / lo) > 0), sr / HOP


def hiss_start(hiss, fps, t, earliest, max_back=0.25, reach=0.04, hole=0.015, min_len=0.02):
    """Start of the hiss run that ends just before (or covers) time t, else None."""
    i = int(round(t * fps))
    if i >= len(hiss):
        return None
    stop = max(int(math.ceil(earliest * fps)), i - int(max_back * fps), 0)
    j = i
    while j > stop and not hiss[j] and (i - j) / fps < reach:  # find the end of the run
        j -= 1
    if j <= stop or not hiss[j]:
        return None
    end, gap = j, 0
    while j > stop:
        if hiss[j - 1]:
            j -= 1
            gap = 0
        elif gap / fps < hole and j - 2 > stop and hiss[max(j - 1 - int(hole * fps), stop) : j - 1].any():
            j -= 1
            gap += 1
        else:
            break
    while not hiss[j]:
        j += 1
    return j / fps if (end - j + 1) / fps >= min_len else None
