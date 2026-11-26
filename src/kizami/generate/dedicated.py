"""Dedicated per-language speech models for English, Spanish and Korean.

Each one is a Hugging Face wav2vec2 CTC model in its own folder of the model
folder (``MODEL_DIRS``), with ``vocab.json``, ``config.json`` and
``model.safetensors``. Where a song has units of that language and the model
is turned on (``w2v_en``, ``w2v_es``, ``w2v_ko`` in ``place.DEFAULTS``; English
and Spanish are on by default), the units of that language take their times
from it instead of from the MMS letter model. Without the folder, MMS times
them as before. By default only a song in that one language uses it
(``w2v_mixed`` 0): in a song with several languages it was not better. Units of other languages are skipped with a "star" token.

The lyrics are written in the model's own characters: letters in the case of
its vocabulary (``DON'T`` for an upper-case English model, ``corazón`` for a
Spanish one) and Hangul blocks for a Korean one. ``|`` separates words, as in
the model's training transcripts.
"""

from __future__ import annotations

import json
import math
import unicodedata
from pathlib import Path

import numpy as np

from kizami.generate.audio import SR

MODEL_DIRS = {"en": "w2v2-en", "es": "w2v2-es", "ko": "w2v2-ko"}
MODEL_FILES = ("vocab.json", "config.json", "model.safetensors")
STRIDE = 320  # 20 ms frames at 16 kHz
STAR = "*"
DELIM = "|"


def model_vocab(model_dir: Path) -> tuple[dict, int]:
    """The vocabulary of the model in ``model_dir`` and the id of its CTC blank.

    The blank is ``pad_token_id`` of ``config.json``, else ``<pad>`` or ``[PAD]``.
    Raises ``ValueError`` when there is no usable vocabulary or blank.
    """
    d = Path(model_dir)
    vocab = json.loads((d / "vocab.json").read_text(encoding="utf-8"))
    if not isinstance(vocab, dict) or not vocab:
        raise ValueError(f"{d / 'vocab.json'}: not a vocabulary")
    config = json.loads((d / "config.json").read_text(encoding="utf-8"))
    ids = set(vocab.values())
    pad = config.get("pad_token_id") if isinstance(config, dict) else None
    if isinstance(pad, int) and pad in ids:
        return vocab, pad
    for name in ("<pad>", "[PAD]"):
        if name in vocab:
            return vocab, vocab[name]
    raise ValueError(f"{d}: no blank token (pad_token_id, <pad> or [PAD])")


def _case(vocab: dict):
    singles = [k for k in vocab if len(k) == 1]
    if any(k.islower() for k in singles):
        return str.lower
    if any(k.isupper() for k in singles):
        return str.upper
    return lambda s: s


def unit_tokens(text: str, vocab: dict, case=None) -> list[str]:
    """The vocabulary tokens for ``text``: each character in the vocabulary's case; a
    character the vocabulary lacks as its parts (Hangul as jamo) or its base letter
    (``é`` as ``E``); anything else, and hyphens and edge apostrophes, left out."""
    case = case or _case(vocab)
    out: list[str] = []
    for c in unicodedata.normalize("NFC", text.replace("’", "'")):
        if c in "-" + DELIM:
            continue
        cc = case(c)
        if cc in vocab:
            out.append(cc)
            continue
        parts = "".join(ch for ch in unicodedata.normalize("NFD", cc) if not unicodedata.combining(ch))
        if parts and all(ch in vocab for ch in parts):
            out.extend(parts)
    while out and out[0] == "'":
        out.pop(0)
    while out and out[-1] == "'":
        out.pop()
    return out


def tokens(atoms: list[dict], vocab: dict, lang: str, star: bool = True) -> tuple[list[str], list[int]]:
    """The tokens to align for the units of ``lang``, and the unit index of each (-1: none).

    A run of units of other languages becomes one ``*`` token, so that model
    can skip them. With ``star``, a ``*`` also opens every line and closes the
    song, for singing that is not in the lyrics. ``|`` goes between two words
    of ``lang`` (units with text between them). A unit with no character in
    the vocabulary is a ``*`` of its own, so it still gets a time.
    """
    case = _case(vocab)
    delim = DELIM in vocab
    toks: list[str] = []
    owner: list[int] = []

    def free_star() -> None:
        if not (toks and toks[-1] == STAR and owner[-1] == -1):
            toks.append(STAR)
            owner.append(-1)

    prev = None  # the last unit of ``lang`` in this line, when it ended in letters
    for i, a in enumerate(atoms):
        if i == 0 or a.get("line") != atoms[i - 1].get("line"):
            prev = None
            if star:
                free_star()
        if a.get("lang") != lang:
            free_star()
            prev = None
            continue
        letters = unit_tokens(a.get("syllable") or "", vocab, case)
        if not letters:
            toks.append(STAR)
            owner.append(i)
            prev = None
            continue
        if delim and prev is not None and atoms[prev]["end"] < a["offset"]:
            toks.append(DELIM)
            owner.append(-1)
        toks.extend(letters)
        owner.extend([i] * len(letters))
        prev = i
    if star and toks:
        free_star()
    return toks, owner


def with_star(em: np.ndarray, blank: int) -> np.ndarray:
    """``em`` with one more column: the log-probability of "anything but blank"."""
    p_blank = np.exp(em[:, blank]).clip(0, 1 - 1e-6)
    return np.concatenate([em, np.log1p(-p_blank)[:, None]], axis=1).astype(np.float32)


def runs(atoms: list[dict], lang: str) -> list[tuple[int, int]]:
    """The index ranges ``[start, stop)`` of consecutive units of ``lang``."""
    out, i = [], 0
    while i < len(atoms):
        if atoms[i].get("lang") != lang:
            i += 1
            continue
        j = i
        while j < len(atoms) and atoms[j].get("lang") == lang:
            j += 1
        out.append((i, j))
        i = j
    return out


def _window(atoms: list[dict], i0: int, i1: int, n_frames: int, margin: float) -> tuple[int, int]:
    """Frames where the run ``[i0, i1)`` may be sung: from the last aligned start of the unit
    before it to the aligned start of the unit after it (times from the other models), widened
    by ``margin`` seconds. A side with no timed neighbour is open to the song's edge."""
    start, stop = 0, n_frames
    for k in range(i0 - 1, -1, -1):
        t = atoms[k].get("rough_last")
        t = atoms[k].get("rough") if t is None else t
        if t is not None:
            start = max(0, int(math.floor((t - margin) * SR / STRIDE)))
            break
    for k in range(i1, len(atoms)):
        t = atoms[k].get("rough")
        if t is not None:
            stop = min(n_frames, int(math.ceil((t + margin) * SR / STRIDE)) + 1)
            break
    return start, stop


def align(
    em: np.ndarray, atoms: list[dict], vocab: dict, lang: str, blank: int, star: bool = True, margin: float = 0.3
) -> int:
    """Set ``w2v`` (start of the first token), ``w2v_last`` (start of the last token) and
    ``w2v_conf`` (weakest token) on each unit of ``lang``. ``em`` has the star column last
    (``with_star``). Returns how many units got a time.

    Each run of units of ``lang`` is aligned on its own, only between the times the other
    models gave the units around it (``margin`` seconds wider), so in a song with several
    languages a word cannot drift into another language's singing. A song in one language
    is one run over the whole track. A run that cannot be aligned in its window gets no
    time here and keeps the letter model's.
    """
    import torch
    import torchaudio.functional as F

    for a in atoms:
        if a.get("lang") == lang:
            a["w2v"], a["w2v_last"], a["w2v_conf"] = None, None, 0.0
    star_id = em.shape[1] - 1
    timed = set()
    for i0, i1 in runs(atoms, lang):
        toks, owner = tokens(atoms[i0:i1], vocab, lang, star=star)
        owner = [o + i0 if o >= 0 else o for o in owner]
        if i0 > 0 and not (toks and toks[0] == STAR and owner[0] == -1):
            toks, owner = [STAR, *toks], [-1, *owner]  # the end of the singing before
        if i1 < len(atoms) and not (toks and toks[-1] == STAR and owner[-1] == -1):
            toks, owner = [*toks, STAR], [*owner, -1]  # the start of the singing after
        if not any(o >= 0 for o in owner):
            continue
        f0, f1 = _window(atoms, i0, i1, len(em), margin)
        ids = [star_id if t == STAR else vocab[t] for t in toks]
        if f1 - f0 < len(ids):
            continue
        x = torch.from_numpy(np.ascontiguousarray(em[f0:f1], dtype=np.float32))[None]
        try:
            aligned, scores = F.forced_align(x, torch.tensor([ids], dtype=torch.int32), blank=blank)
        except RuntimeError:
            continue
        spans = F.merge_tokens(aligned[0], scores[0].exp(), blank=blank)
        if len(spans) != len(ids):
            continue
        for sp, ai in zip(spans, owner):
            if ai < 0:
                continue
            a = atoms[ai]
            t = (f0 + sp.start) * STRIDE / SR
            if a["w2v"] is None:
                a["w2v"], a["w2v_conf"] = t, 1.0
            a["w2v_last"] = t
            a["w2v_conf"] = round(min(a["w2v_conf"], float(sp.score)), 4)
            timed.add(ai)
    return len(timed)


def adopt(atoms: list[dict], lang: str) -> int:
    """Use the dedicated model's times as the aligned times of the units of ``lang``.
    Returns how many units took them."""
    n = 0
    for a in atoms:
        if a.get("lang") != lang or a.get("w2v") is None:
            continue
        last = a.get("w2v_last")
        a["rough"], a["rough_last"] = a["w2v"], a["w2v"] if last is None else last
        a["kana_starts"], a["conf"] = [a["w2v"]], a.get("w2v_conf", 0.0)
        a["src"] = "w2v"  # place.lag_for takes w2v_lag_<lang> for it
        n += 1
    return n
