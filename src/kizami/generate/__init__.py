"""Generate onset-synced chunk timing from a vocal track and its lyrics.

    from kizami.generate import generate
    result = generate("songs/demo", model_dir="models")

writes, next to the audio unless ``out_dir`` is given, a timing file and an
Audacity label file for level chu (medium):

* ``auto_chu.json`` / ``auto_labels_chu.txt``: a chunk starts at each word
  (with its particles) and at the strongest attacks inside words.

A chunk can also start inside a kanji, such as at the が of 願. By default
(``kanji="auto"``) such a kanji word is written in hiragana (ねがい) and every
other kanji as in the lyrics. ``"hiragana"`` writes every kanji in hiragana;
``"keep"`` writes every kanji as in the lyrics, and a kanji never splits.
Two more levels can be asked for with ``levels``, for comparison:
``dai`` (a word with its particles per chunk) and ``sho`` (every kana or
character with a sound of its own).

The same character has the same start time at every level. A chunk with no
clear attack of its own is marked uncertain (``?`` in the label file).

Models are read from ``model_dir`` and never downloaded:

* ``w2v2-hiragana/``: a Hugging Face wav2vec2 CTC model with a hiragana
  vocabulary (``config.json``, ``vocab.json``, ``model.safetensors`` ...).
  Required.
* ``mms_fa/model.pt``: the torchaudio MMS_FA forced-alignment model.
  Optional, used where the first model is unsure. Check its licence before
  any commercial use.

The heavy parts (numpy, torch, torchaudio, transformers, SudachiPy) and the
``ffmpeg`` program are needed: ``pip install "kizami[generate]"``.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

LEVELS = ("dai", "chu", "sho")
DEFAULT_LEVELS = ("chu",)
KANJI_MODES = ("auto", "hiragana", "keep")
KANA_MODEL = "w2v2-hiragana"
MMS_MODEL = Path("mms_fa") / "model.pt"
REQUIRED_MODULES = {
    "numpy": "numpy",
    "torch": "torch",
    "torchaudio": "torchaudio",
    "transformers": "transformers",
    "sudachipy": "SudachiPy",
    "sudachidict_core": "SudachiDict-core",
}


class GenerateError(Exception):
    """A problem with the input or the setup, to show to the user without a traceback."""


@dataclass
class LevelResult:
    level: str
    timing: Path
    labels: Path
    chunks: int
    uncertain: int
    kanji_in_hiragana: int = 0  # kanji words written in hiragana because a chunk starts inside one


@dataclass
class GenerateResult:
    """Counts only: the generator never reports lyric text."""

    lines: int
    units: int
    with_attack: int
    from_second_aligner: int
    kanji_kept_whole: int
    used_second_aligner: bool
    start_rules: dict[str, int]
    levels: list[LevelResult] = field(default_factory=list)


def missing_modules() -> list[str]:
    """Package names of the required Python modules that are not installed."""
    return [name for module, name in REQUIRED_MODULES.items() if importlib.util.find_spec(module) is None]


def _need_file(path: Path, what: str) -> Path:
    if not path.is_file():
        raise GenerateError(f"{what} not found: {path}")
    return path


def generate(
    folder: str | Path | None = None,
    *,
    model_dir: str | Path,
    vocals: str | Path | None = None,
    lyrics: str | Path | None = None,
    out_dir: str | Path | None = None,
    levels: Iterable[str] = DEFAULT_LEVELS,
    kanji: str = "auto",
    mms: str = "auto",
    cache: str | Path | None = None,
    params: dict | None = None,
    progress: Callable[[str], None] | None = None,
) -> GenerateResult:
    """Write timing and label files for one song. See the module docstring.

    ``folder`` holds ``vocals.wav`` and ``lyrics.txt`` unless ``vocals`` and
    ``lyrics`` name other files. ``kanji`` is ``"auto"`` (a chunk can start
    at a strong attack inside a kanji; only such kanji words are shown in
    hiragana), ``"hiragana"`` (every kanji shown as its reading) or ``"keep"``
    (kanji shown as written; a kanji never splits). ``mms`` is ``"auto"`` (use the second model if
    its file is there), ``"on"`` or ``"off"``. ``cache`` is a folder where the
    model outputs are kept, so a second run on the same audio is fast.
    ``params`` overrides values of ``kizami.generate.place.DEFAULTS``.
    ``progress`` receives a short message before each slow step.
    """
    say = progress or (lambda message: None)
    levels = list(levels)
    unknown = [lv for lv in levels if lv not in LEVELS]
    if unknown or not levels:
        raise GenerateError(f"levels must be some of: {', '.join(LEVELS)}")
    if kanji not in KANJI_MODES:
        raise GenerateError(f"kanji must be one of: {', '.join(KANJI_MODES)}")
    if mms not in ("auto", "on", "off"):
        raise GenerateError('mms must be "auto", "on" or "off"')
    if folder is None and (vocals is None or lyrics is None):
        raise GenerateError("give a song folder, or both the vocal track and the lyrics file")
    base = Path(folder) if folder is not None else None
    vocals_path = _need_file(Path(vocals) if vocals else base / "vocals.wav", "vocal track")
    lyrics_path = _need_file(Path(lyrics) if lyrics else base / "lyrics.txt", "lyrics file")
    out = Path(out_dir) if out_dir else (base if base is not None else vocals_path.parent)
    model_root = Path(model_dir)
    kana_dir = model_root / KANA_MODEL
    for name in ("vocab.json", "config.json", "model.safetensors"):
        _need_file(kana_dir / name, f"kana model (w2v2-hiragana/{name} in the model folder)")
    mms_file = model_root / MMS_MODEL
    if mms == "on":
        _need_file(mms_file, "second model (mms_fa/model.pt in the model folder)")
    use_mms = mms == "on" or (mms == "auto" and mms_file.is_file())

    missing = missing_modules()
    if missing:
        raise GenerateError(f'missing Python packages: {", ".join(missing)}. Install them with: pip install "kizami[generate]"')

    import json
    import warnings

    from kizami.generate import align, audio, place, text

    p = dict(place.DEFAULTS)
    for key, value in (params or {}).items():
        if key not in p:
            raise GenerateError(f"unknown parameter: {key}")
        p[key] = value
    if p["anchor"] not in ("prev-end", "attack"):
        raise GenerateError('anchor must be "prev-end" or "attack"')

    try:
        vocab = json.loads((kana_dir / "vocab.json").read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise GenerateError(f"{kana_dir / 'vocab.json'}: not a JSON vocabulary ({exc})") from exc
    if not isinstance(vocab, dict) or "[PAD]" not in vocab:
        raise GenerateError(f"{kana_dir / 'vocab.json'}: no [PAD] token; is this the hiragana wav2vec2 model?")
    say("reading the lyrics")
    try:
        lines = text.parse_lyrics(lyrics_path)
    except UnicodeDecodeError as exc:
        raise GenerateError(f"{lyrics_path}: not UTF-8 text ({exc})") from exc
    atoms, kanji_whole = text.atoms_for(lines, vocab)
    if not any(a["kana"] for a in atoms):
        raise GenerateError(f"{lyrics_path}: no singable lyrics found")

    try:
        x, sr = audio.load_mono(vocals_path)
    except audio.AudioError as exc:
        raise GenerateError(str(exc)) from exc
    audio_key = None
    if cache:
        audio_key = Path(cache) / f"{vocals_path.stem}-{align.fingerprint(vocals_path.read_bytes())}"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            pcm16k = audio.load_16k(vocals_path)
        except audio.AudioError as exc:
            raise GenerateError(str(exc)) from exc
        say("aligning the lyrics (kana model)")
        key = Path(f"{audio_key}-kana-{align.kana_model_key(kana_dir)}.npy") if audio_key else None
        try:
            em = align.kana_emissions(pcm16k, kana_dir, key)
        except (OSError, ValueError) as exc:
            raise GenerateError(f"the kana model could not be loaded from {kana_dir} ({exc})") from exc
        try:
            align.ctc_align(em, atoms, vocab)
        except RuntimeError as exc:
            raise GenerateError(f"the lyrics could not be aligned to the audio ({exc}). Is it the right song?") from exc
        if use_mms:
            say("aligning the lyrics (second model)")
            key = Path(f"{audio_key}-mms-{align.mms_model_key(mms_file)}.npy") if audio_key else None
            try:
                em2 = align.mms_emissions(pcm16k, mms_file, key)
            except (OSError, RuntimeError, ValueError) as exc:
                raise GenerateError(f"the second model could not be loaded from {mms_file} ({exc})") from exc
            try:
                align.mms_align(em2, atoms)
            except RuntimeError as exc:
                raise GenerateError(f"the second model could not align the lyrics ({exc})") from exc
        say("finding the attacks")
        onsets, strengths, _ = audio.detect_attacks(x, sr)
        hiss, fps = audio.hiss_frames(x, sr)
        env, tt = audio.envelope(x, sr) if p["anchor"] == "prev-end" else (None, None)
    n_mms = place.place(atoms, onsets, strengths, p, hiss, fps, env, tt)
    place.place_morae(atoms, onsets, strengths, p, hiss, fps, env, tt)
    p["m_thr"] = place.chu_threshold(atoms, p["m_share"])

    timed = [a for a in atoms if a["time"] is not None]
    rules: dict[str, int] = {}
    for a in timed:
        rules[a["why"] or "attack"] = rules.get(a["why"] or "attack", 0) + 1
    result = GenerateResult(
        lines=len(lines),
        units=len(atoms),
        with_attack=sum(a["snapped"] for a in timed),
        from_second_aligner=n_mms,
        kanji_kept_whole=kanji_whole,
        used_second_aligner=use_mms,
        start_rules=dict(sorted(rules.items())),
    )
    if kanji in ("auto", "hiragana"):
        atoms = text.to_hiragana(lines, atoms)  # after the threshold, so the kanji's morae do not move it
    out.mkdir(parents=True, exist_ok=True)
    for level in levels:
        chosen = place.select(atoms, level, p["m_thr"], p["gap"] if p["gap"] > 0 else None)
        shown, units, left = lines, atoms, 0
        if kanji == "auto":
            shown, units, chosen, left = text.kanji_unless_split(lines, atoms, chosen)
        level_result = _write_level(out, level, shown, chosen, p, units, kanji)
        level_result.kanji_in_hiragana = left
        result.levels.append(level_result)
    return result


def _ms(seconds: float) -> int:
    return max(0, int(round(seconds * 1000)))


def _write_level(out: Path, level: str, lines, chosen, p, atoms=None, kanji: str = "keep") -> LevelResult:
    from kizami.audacity import label_text_from_document
    from kizami.generate.place import chunks_for
    from kizami.timing import Chunk, Line, Mora, TimingDocument, save_document

    doc_lines = []
    for chunks in chunks_for(lines, chosen, atoms):
        if chunks:
            doc_lines.append(
                Line(
                    chunks=tuple(
                        Chunk(
                            text=t,
                            start_ms=_ms(s),
                            uncertain=u,
                            morae=tuple(Mora(kana=k, start_ms=_ms(ms), uncertain=mu) for k, ms, mu in morae),
                        )
                        for t, s, u, morae in chunks
                    )
                )
            )
    if not doc_lines:
        raise GenerateError("no chunk could be placed")
    params = {k: (round(v, 6) if isinstance(v, float) else v) for k, v in p.items()}
    doc = TimingDocument(lines=tuple(doc_lines), kind="generated",
                         meta={"source": "kizami generate", "level": level, "kanji": kanji, "params": params})  # fmt: skip
    timing = out / f"auto_{level}.json"
    labels = out / f"auto_labels_{level}.txt"
    save_document(doc, timing)
    with open(labels, "w", encoding="utf-8", newline="\n") as f:
        f.write(label_text_from_document(doc))
    all_chunks = [c for line in doc_lines for c in line.chunks]
    return LevelResult(level, timing, labels, len(all_chunks), sum(c.uncertain for c in all_chunks))
