"""Where each unit is sung, from two speech models (CTC forced alignment).

* The kana model (wav2vec2 trained on hiragana) aligns every kana of the
  lyrics. Its times are the main source.
* The MMS model aligns romaji letters. It is optional and only consulted
  where the kana model is unsure (see ``place.fused_rough``).

Both run over the whole track in 30 s pieces with 2 s of context on each side.
Model files are read from a local folder and never downloaded here.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from kizami.generate.audio import SR
from kizami.generate.letters import ROMAJI, SMALL_V, SMALL_Y, VOWELS, romaji  # noqa: F401 (kept importable from here)

STRIDE = 320  # frame step of both models in samples at 16 kHz (20 ms)
PIECE_S, CONTEXT_S = 30, 2

def fingerprint(*parts: bytes | str | int) -> str:
    """Short hash of the given parts, for naming cache files."""
    h = hashlib.sha256()
    for part in parts:
        h.update(part if isinstance(part, bytes) else str(part).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:16]


def kana_model_key(model_dir: Path) -> str:
    """Identifies the kana model: its vocabulary, its settings and the size of its weights."""
    d = Path(model_dir)
    return fingerprint((d / "vocab.json").read_bytes(), (d / "config.json").read_bytes(), (d / "model.safetensors").stat().st_size)


def mms_model_key(model_file: Path) -> str:
    return fingerprint(Path(model_file).stat().st_size)


def _run_pieces(audio: np.ndarray, forward) -> np.ndarray:
    """Log-probabilities per frame for the whole track, model run in pieces with context."""
    import torch

    piece, ctx = PIECE_S * SR, CONTEXT_S * SR
    n_frames = (len(audio) - 400) // STRIDE + 1
    outs = []
    with torch.no_grad():
        for s in range(0, len(audio), piece):
            a, b = max(0, s - ctx), min(len(audio), s + piece + ctx)
            lp = torch.log_softmax(forward(audio[a:b]), dim=-1).numpy()
            f0 = (s - a) // STRIDE
            outs.append(lp[f0 : f0 + min(piece, len(audio) - s) // STRIDE])
    return np.concatenate(outs)[:n_frames]


def kana_emissions(audio: np.ndarray, model_dir: Path, key: Path | None = None) -> np.ndarray:
    """Per 20 ms frame, log-probabilities of every kana of the kana model. ``key``: cache file."""
    if key is not None and key.exists():
        return np.load(key)
    import torch
    from transformers import Wav2Vec2ForCTC
    from transformers.utils import logging as hf_logging

    hf_logging.set_verbosity_error()
    hf_logging.disable_progress_bar()
    model = Wav2Vec2ForCTC.from_pretrained(str(model_dir), use_safetensors=True).eval()

    def forward(x):
        x = (x - x.mean()) / (x.std() + 1e-7)
        return model(torch.from_numpy(x)[None]).logits[0]

    em = _run_pieces(audio, forward)
    if key is not None:
        key.parent.mkdir(parents=True, exist_ok=True)
        np.save(key, em)
    return em


def ctc_align(em: np.ndarray, atoms: list[dict], vocab: dict) -> int:
    """Set ``rough`` (first kana), ``rough_last`` (last kana), ``kana_starts`` (every kana) and ``conf``
    on each unit. Returns the kana count.

    ``*`` (one Latin syllable) is aligned to an extra "anything but silence" token.
    """
    import torch
    import torchaudio.functional as F

    blank = vocab["[PAD]"]
    star = em.shape[1]
    p_blank = np.exp(em[:, blank]).clip(0, 1 - 1e-6)
    em = np.concatenate([em, np.log1p(-p_blank)[:, None]], axis=1).astype(np.float32)
    ids, owner = [], []
    for ai, a in enumerate(atoms):
        for c in a["kana"]:
            ids.append(star if c == "*" else vocab[c])
            owner.append(ai)
    for a in atoms:
        a["rough"] = a["rough_last"] = None
        a["conf"] = 1.0
        a["kana_starts"] = []
    if not ids:
        return 0
    aligned, scores = F.forced_align(torch.from_numpy(em)[None], torch.tensor([ids], dtype=torch.int32), blank=blank)
    spans = F.merge_tokens(aligned[0], scores[0].exp(), blank=blank)
    if len(spans) != len(ids):
        raise RuntimeError(f"kana alignment lost tokens ({len(spans)} of {len(ids)})")
    for sp, ai in zip(spans, owner):
        if atoms[ai]["rough"] is None:
            atoms[ai]["rough"] = sp.start * STRIDE / SR
        atoms[ai]["rough_last"] = sp.start * STRIDE / SR
        atoms[ai]["kana_starts"].append(sp.start * STRIDE / SR)
        atoms[ai]["conf"] = min(atoms[ai]["conf"], float(sp.score))  # weakest kana of the unit
    return len(ids)


def mms_emissions(audio: np.ndarray, model_file: Path, key: Path | None = None) -> np.ndarray:
    """Per 20 ms frame, log-probabilities of the MMS letters (model file read from disk, never downloaded)."""
    if key is not None and key.exists():
        return np.load(key)
    import torch
    import torchaudio

    bundle = torchaudio.pipelines.MMS_FA
    model = bundle.get_model(with_star=True, dl_kwargs={"model_dir": str(Path(model_file).parent)}).eval()

    def forward(x):
        return model(torch.from_numpy(x)[None])[0][0]

    em = _run_pieces(audio, forward).astype(np.float32)
    if key is not None:
        key.parent.mkdir(parents=True, exist_ok=True)
        np.save(key, em)
    return em


def mms_tokens(atoms: list[dict], star: bool = False) -> tuple[list[str], list[int]]:
    """The MMS letters of all units in order, and the index of the unit each letter belongs to.

    A unit's letters are its ``letters`` (set for every language by
    ``text.atoms_for``), or the romaji of its kana for a unit without them.
    With ``star``, a ``*`` token (owner -1) opens every line and closes the
    song, so singing that is not in the lyrics between lines (ad-libs,
    backing vocals) need not be matched to lyric letters.
    """
    letters, owner = [], []
    for ai, a in enumerate(atoms):
        if star and (ai == 0 or a.get("line") != atoms[ai - 1].get("line")):
            letters.append("*")
            owner.append(-1)
        word = a.get("letters")
        if word is None:
            word = romaji(a["kana"])
        for ch in word:
            letters.append(ch)
            owner.append(ai)
    if star and letters:
        letters.append("*")
        owner.append(-1)
    return letters, owner


def mms_align(em: np.ndarray, atoms: list[dict], star: bool = False) -> None:
    """Set ``mms`` (start of the first letter), ``mms_last`` (start of the last letter) and
    ``mms_conf`` on each unit; None where it has no letters. ``star``: see ``mms_tokens``."""
    import torch
    import torchaudio
    import torchaudio.functional as F

    d = torchaudio.pipelines.MMS_FA.get_dict(star="*")
    letters, owner = mms_tokens(atoms, star=star)
    ids = [d[ch] for ch in letters]
    for a in atoms:
        a["mms"], a["mms_last"], a["mms_conf"] = None, None, 0.0
    if not ids:
        return
    aligned, scores = F.forced_align(torch.from_numpy(em)[None], torch.tensor([ids], dtype=torch.int32), blank=0)
    spans = F.merge_tokens(aligned[0], scores[0].exp(), blank=0)
    if len(spans) != len(ids):
        raise RuntimeError(f"letter alignment lost tokens ({len(spans)} of {len(ids)})")
    conf: dict[int, float] = {}
    for sp, ai in zip(spans, owner):
        if ai < 0:
            continue  # a star token
        if atoms[ai]["mms"] is None:
            atoms[ai]["mms"] = sp.start * STRIDE / SR
        atoms[ai]["mms_last"] = sp.start * STRIDE / SR
        conf[ai] = min(conf.get(ai, 1.0), float(sp.score))  # weakest letter of the unit
    for ai, c in conf.items():
        atoms[ai]["mms_conf"] = round(c, 4)


def adopt_mms(atoms: list[dict]) -> int:
    """Use the MMS times as the aligned times of every unit that is not Japanese.

    Sets ``rough``, ``rough_last``, ``kana_starts`` and ``conf`` from ``mms``,
    ``mms_last`` and ``mms_conf``. Japanese units keep the kana model's times.
    Returns how many units took the MMS time.
    """
    n = 0
    for a in atoms:
        if a.get("lang", "ja") == "ja" or a.get("mms") is None:
            continue
        last = a.get("mms_last")
        a["rough"], a["rough_last"] = a["mms"], a["mms"] if last is None else last
        a["kana_starts"], a["conf"] = [a["mms"]], a.get("mms_conf", 0.0)
        n += 1
    return n
