"""Get the vocal track out of a mixed song with Demucs, or convert a vocal file to WAV.

``kizami generate`` needs the voice alone. :func:`separate_vocals` runs
Demucs (model ``htdemucs``, vocals only) on this computer and writes a 16 bit
PCM WAV file. Demucs downloads its model weights (about 80 MB) the first time
it runs; after that it works offline. The song itself never leaves the
computer.

:func:`convert_to_wav` is for a file that is a vocal track already: it only
converts it to the WAV format that ``kizami generate`` reads, with the
``ffmpeg`` program.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

MODEL = "htdemucs"
SAMPLE_RATE = 44100  # what Demucs writes, and what the generator was tuned on
_PERCENT = re.compile(r"(\d{1,3})%\|")


class SeparateError(Exception):
    """A problem to show to the user without a traceback."""


def demucs_available() -> bool:
    return importlib.util.find_spec("demucs") is not None


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def separate_vocals(
    song: str | Path,
    out_wav: str | Path,
    *,
    progress: Callable[[int], None] | None = None,
    python: str = sys.executable,
) -> None:
    """Write the vocal track of ``song`` to ``out_wav``.

    ``progress`` receives the share done, from 0 to 100, while Demucs runs.
    """
    song, out_wav = Path(song), Path(out_wav)
    if not demucs_available():
        raise SeparateError('Demucs is not installed. Install it with: pip install "kizami[separate]"')
    if not ffmpeg_available():
        raise SeparateError("the ffmpeg program is needed to read the song. Install it, for example: brew install ffmpeg")
    with tempfile.TemporaryDirectory(prefix="kizami-demucs-") as tmp:
        cmd = [python, "-m", "demucs", "--two-stems", "vocals", "--other-method", "none", "-n", MODEL,
               "-o", tmp, "--filename", "{stem}.{ext}", str(song)]  # fmt: skip
        _run_with_progress(cmd, progress)
        made = Path(tmp) / MODEL / "vocals.wav"
        if not made.is_file():
            raise SeparateError("Demucs finished but wrote no vocal track")
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(made), str(out_wav))


def _run_with_progress(cmd: list[str], progress: Callable[[int], None] | None) -> None:
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
    except OSError as exc:
        raise SeparateError(f"Demucs could not be started ({exc})") from exc
    assert proc.stderr is not None
    tail, buf, last = [], b"", -1
    while True:
        block = proc.stderr.read1(4096) if hasattr(proc.stderr, "read1") else proc.stderr.read(4096)
        if not block:
            break
        buf += block
        parts = re.split(rb"[\r\n]", buf)
        buf = parts.pop()
        for raw in parts:
            text = raw.decode("utf-8", "replace").strip()
            if not text:
                continue
            m = _PERCENT.search(text)
            if m:
                pct = min(100, int(m.group(1)))
                if progress and pct != last:
                    progress(pct)
                    last = pct
            else:
                tail = (tail + [text])[-5:]
    code = proc.wait()
    if code != 0:
        if buf.strip():
            tail = (tail + [buf.decode("utf-8", "replace").strip()])[-5:]
        raise SeparateError(f"Demucs could not separate the song ({' / '.join(tail[-2:]) or f'exit code {code}'})")


def convert_to_wav(src: str | Path, out_wav: str | Path) -> None:
    """Convert any audio file that ffmpeg reads to 16 bit PCM WAV at 44.1 kHz."""
    if not ffmpeg_available():
        raise SeparateError("the ffmpeg program is needed to read the audio. Install it, for example: brew install ffmpeg")
    out_wav = Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-c:a", "pcm_s16le",
           "-ar", str(SAMPLE_RATE), str(out_wav)]  # fmt: skip
    run = subprocess.run(cmd, capture_output=True)
    if run.returncode != 0:
        tail = run.stderr.decode("utf-8", "replace").strip().splitlines()[-1:]
        raise SeparateError(f"ffmpeg could not read the audio ({' '.join(tail) or 'unknown error'})")
