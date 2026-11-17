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

STRIDE = 320  # frame step of both models in samples at 16 kHz (20 ms)
PIECE_S, CONTEXT_S = 30, 2

ROMAJI = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o", "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so", "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no", "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo", "や": "ya", "ゆ": "yu", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro", "わ": "wa", "を": "o", "ん": "n",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go", "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do", "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po", "ゔ": "bu",
    "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o", "ゃ": "ya", "ゅ": "yu", "ょ": "yo", "ゎ": "wa",
}  # fmt: skip
SMALL_Y = {"ゃ": "a", "ゅ": "u", "ょ": "o"}
SMALL_V = {"ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o"}
VOWELS = set("aiueo")


def romaji(kana: list[str]) -> str:
    """Letters for one unit's kana. The geminate adds no letter; the long mark repeats the vowel."""
    out, i = "", 0
    while i < len(kana):
        k, nxt = kana[i], kana[i + 1] if i + 1 < len(kana) else ""
        if k == "*":
            out += "*"
        elif k in ROMAJI:
            r = ROMAJI[k]
            if nxt in SMALL_Y and r.endswith("i") and len(r) > 1:
                stem = r[:-1]
                out += (stem if stem in ("sh", "ch", "j") else stem + "y") + SMALL_Y[nxt]
                i += 1
            elif nxt in SMALL_V and len(r) > 1:
                out += r[:-1] + SMALL_V[nxt]
                i += 1
            else:
                out += r
        elif k == "ー" and out and out[-1] in VOWELS:
            out += out[-1]
        i += 1
    return out


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
    """Set ``rough`` (first kana), ``rough_last`` (last kana) and ``conf`` on each unit. Returns the kana count.

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


def mms_align(em: np.ndarray, atoms: list[dict]) -> None:
    """Set ``mms`` (start of the first letter) and ``mms_conf`` on each unit; None where it has no letters."""
    import torch
    import torchaudio
    import torchaudio.functional as F

    d = torchaudio.pipelines.MMS_FA.get_dict(star="*")
    ids, owner = [], []
    for ai, a in enumerate(atoms):
        for ch in romaji(a["kana"]):
            ids.append(d[ch])
            owner.append(ai)
    for a in atoms:
        a["mms"], a["mms_conf"] = None, 0.0
    if not ids:
        return
    aligned, scores = F.forced_align(torch.from_numpy(em)[None], torch.tensor([ids], dtype=torch.int32), blank=0)
    spans = F.merge_tokens(aligned[0], scores[0].exp(), blank=0)
    if len(spans) != len(ids):
        raise RuntimeError(f"letter alignment lost tokens ({len(spans)} of {len(ids)})")
    conf: dict[int, float] = {}
    for sp, ai in zip(spans, owner):
        if atoms[ai]["mms"] is None:
            atoms[ai]["mms"] = sp.start * STRIDE / SR
        conf[ai] = min(conf.get(ai, 1.0), float(sp.score))  # weakest letter of the unit
    for ai, c in conf.items():
        atoms[ai]["mms_conf"] = round(c, 4)
