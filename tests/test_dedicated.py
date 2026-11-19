"""Dedicated per-language wav2vec2 models (English, Spanish, Korean)."""

import json

import pytest

np = pytest.importorskip("numpy")

from kizami.generate import GenerateError, generate  # noqa: E402
from kizami.generate.dedicated import (  # noqa: E402
    MODEL_DIRS,
    adopt,
    model_vocab,
    tokens,
    unit_tokens,
    with_star,
)

SPECIAL = {"<pad>": 0, "<s>": 1, "</s>": 2, "<unk>": 3, "|": 4}
EN = {**SPECIAL, **{c: 5 + i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ'")}}
ES = {**SPECIAL, **{c: 5 + i for i, c in enumerate("'-abcdefghijklmnopqrstuvwxyzáéíñóöúü")}}
KO = {"사": 0, "랑": 1, "해": 2, "너": 3, "|": 4, "[UNK]": 5, "[PAD]": 6}


def _atoms(*texts, latin="auto"):
    pytest.importorskip("sudachipy")
    pytest.importorskip("sudachidict_core")
    from kizami.generate.text import atoms_for

    lines = [{"display": t, "sung": t, "spans": []} for t in texts]
    atoms, _, _ = atoms_for(lines, {"[PAD]": 0}, latin=latin)
    return atoms


# ---------------------------------------------------------------- text to tokens


def test_english_letters_follow_the_vocabulary_case():
    assert unit_tokens("Don't", EN) == list("DON'T")
    assert unit_tokens("'cause", EN) == list("CAUSE")  # an edge apostrophe is not sung
    assert unit_tokens("café", EN) == list("CAFE")  # no accents in the vocabulary: the base letter
    assert unit_tokens("mo-", EN) == list("MO")  # a hyphen is never sung


def test_spanish_keeps_accents_and_enye():
    assert unit_tokens("Corazón", ES) == list("corazón")
    assert unit_tokens("pingüino", ES) == list("pingüino")
    assert unit_tokens("Ñandú", ES) == list("ñandú")
    assert unit_tokens("ya-", ES) == list("ya")  # the vocabulary has "-", but it is not sung
    assert unit_tokens("corazón", ES) == list("corazón")  # decomposed input


def test_korean_blocks_map_one_to_one_and_unknown_blocks_give_nothing():
    assert unit_tokens("사", KO) == ["사"]
    assert unit_tokens("랑!", KO) == ["랑"]
    assert unit_tokens("뷁", KO) == []
    assert unit_tokens("2", EN) == []


def test_tokens_put_a_star_before_each_line_and_a_delimiter_between_words():
    atoms = _atoms("Hi you", "Go", latin="en")
    toks, owner = tokens(atoms, EN, "en")
    assert toks == ["*", "H", "I", "|", "Y", "O", "U", "*", "G", "O", "*"]
    assert owner == [-1, 0, 0, -1, 1, 1, 1, -1, 2, 2, -1]


def test_tokens_without_line_stars_keep_only_the_delimiters():
    atoms = _atoms("Hi you", "Go", latin="en")
    toks, owner = tokens(atoms, EN, "en", star=False)
    assert toks == ["H", "I", "|", "Y", "O", "U", "G", "O"]
    assert owner == [0, 0, -1, 1, 1, 1, 2, 2]


def test_units_of_another_language_become_one_star():
    atoms = _atoms("사랑해 baby 너", latin="en")
    toks, owner = tokens(atoms, KO, "ko")
    assert toks == ["*", "사", "랑", "해", "*", "너", "*"]
    ko = [i for i, a in enumerate(atoms) if a["lang"] == "ko"]
    assert owner == [-1, ko[0], ko[1], ko[2], -1, ko[3], -1]


def test_a_unit_with_no_letters_in_the_vocabulary_is_a_star_of_its_own():
    atoms = _atoms("사랑 뷁", latin="en")
    toks, owner = tokens(atoms, KO, "ko")
    assert toks == ["*", "사", "랑", "*", "*"]
    assert owner == [-1, 0, 1, 2, -1]


def test_words_joined_by_sinalefa_still_get_a_delimiter():
    atoms = _atoms("mi amor", latin="es")
    assert not atoms[1]["word_start"]  # "a" of amor is joined to "mi"
    toks, _ = tokens(atoms, ES, "es", star=False)
    assert "".join(toks) == "mi|amor"


# ---------------------------------------------------------------- model files


def test_blank_comes_from_the_config_or_the_pad_token(tmp_path):
    (tmp_path / "vocab.json").write_text(json.dumps(KO), encoding="utf-8")
    (tmp_path / "config.json").write_text(json.dumps({"pad_token_id": 6}), encoding="utf-8")
    vocab, blank = model_vocab(tmp_path)
    assert vocab == KO and blank == 6
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    assert model_vocab(tmp_path)[1] == 6  # [PAD]
    (tmp_path / "vocab.json").write_text(json.dumps(EN), encoding="utf-8")
    assert model_vocab(tmp_path)[1] == 0  # <pad>


def test_a_vocabulary_without_a_blank_is_refused(tmp_path):
    (tmp_path / "vocab.json").write_text(json.dumps({"a": 0}), encoding="utf-8")
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="blank"):
        model_vocab(tmp_path)


def test_model_folders_are_named_per_language():
    assert MODEL_DIRS == {"en": "w2v2-en", "es": "w2v2-es", "ko": "w2v2-ko"}


# ---------------------------------------------------------------- alignment


def test_star_column_is_anything_but_blank():
    em = np.log(np.array([[0.9, 0.1], [0.2, 0.8]], dtype=np.float32))
    out = with_star(em, blank=0)
    assert out.shape == (2, 3)
    assert np.allclose(np.exp(out[:, 2]), [0.1, 0.8], atol=1e-5)


def _peaked(vocab, path, n_extra=1):
    """Log-probabilities with one clear token per frame (``None`` = blank)."""
    n = len(vocab)
    em = np.full((len(path), n), np.log(0.01 / n), dtype=np.float32)
    blank = vocab.get("<pad>", vocab.get("[PAD]"))
    for f, tok in enumerate(path):
        em[f, blank if tok is None else vocab[tok]] = np.log(0.99)
    return em


def test_alignment_times_the_units_of_its_language():
    pytest.importorskip("torchaudio")
    from kizami.generate.dedicated import align

    atoms = _atoms("Hi you", latin="en")
    path = [None] * 5 + ["H", "I", None, "|", "Y", "O", "U"] + [None] * 3
    em = with_star(_peaked(EN, path), blank=0)
    n = align(em, atoms, EN, "en", blank=0)
    assert n == 2
    assert atoms[0]["w2v"] == pytest.approx(0.10, abs=0.021)
    assert atoms[1]["w2v"] == pytest.approx(0.18, abs=0.021)
    assert atoms[1]["w2v_last"] == pytest.approx(0.22, abs=0.021)
    assert 0 < atoms[0]["w2v_conf"] <= 1


def test_alignment_leaves_other_languages_alone():
    pytest.importorskip("torchaudio")
    from kizami.generate.dedicated import align

    atoms = _atoms("사랑 baby", latin="en")
    path = [None] * 3 + ["사", None, "랑"] + [None] * 10
    em = with_star(_peaked(KO, path), blank=6)
    assert align(em, atoms, KO, "ko", blank=6) == 2
    assert atoms[0]["w2v"] == pytest.approx(0.06, abs=0.021)
    assert all(a.get("w2v") is None for a in atoms if a["lang"] != "ko")


def test_adopt_takes_the_dedicated_time_for_its_language_only():
    atoms = [
        {"lang": "en", "rough": 1.0, "rough_last": 1.1, "kana_starts": [1.0], "conf": 0.5,
         "w2v": 2.0, "w2v_last": 2.2, "w2v_conf": 0.7},
        {"lang": "ko", "rough": 3.0, "rough_last": 3.0, "kana_starts": [3.0], "conf": 0.5,
         "w2v": 4.0, "w2v_last": 4.0, "w2v_conf": 0.7},
        {"lang": "en", "rough": 5.0, "rough_last": 5.0, "kana_starts": [5.0], "conf": 0.5, "w2v": None},
    ]  # fmt: skip
    assert adopt(atoms, "en") == 1
    assert (atoms[0]["rough"], atoms[0]["rough_last"], atoms[0]["kana_starts"], atoms[0]["conf"]) == (2.0, 2.2, [2.0], 0.7)
    assert atoms[1]["rough"] == 3.0 and atoms[2]["rough"] == 5.0


# ---------------------------------------------------------------- emissions from a small model


def _tiny_model(path, vocab):
    pytest.importorskip("transformers")
    import torch
    from transformers import Wav2Vec2Config, Wav2Vec2ForCTC

    torch.manual_seed(0)
    config = Wav2Vec2Config(
        vocab_size=len(vocab), hidden_size=16, num_hidden_layers=1, num_attention_heads=2, intermediate_size=16,
        conv_dim=(8, 8, 8, 8, 8, 8, 8), num_conv_pos_embeddings=4, num_conv_pos_embedding_groups=2,
        pad_token_id=vocab.get("<pad>", vocab.get("[PAD]")),
    )  # fmt: skip
    Wav2Vec2ForCTC(config).save_pretrained(str(path), safe_serialization=True)
    (path / "vocab.json").write_text(json.dumps(vocab), encoding="utf-8")


def test_emissions_from_a_hugging_face_model_folder(tmp_path):
    from kizami.generate.align import w2v2_emissions

    _tiny_model(tmp_path / "m", KO)
    x = np.random.default_rng(0).standard_normal(16000 * 3).astype(np.float32)
    em = w2v2_emissions(x, tmp_path / "m")
    assert em.shape == ((len(x) - 400) // 320 + 1, len(KO))
    assert np.allclose(np.exp(em).sum(axis=1), 1, atol=1e-4)


# ---------------------------------------------------------------- generate


def _song(tmp_path, lyrics):
    (tmp_path / "vocals.wav").write_bytes(b"")
    (tmp_path / "lyrics.txt").write_text(lyrics, encoding="utf-8")
    (tmp_path / "mms_fa").mkdir()
    (tmp_path / "mms_fa" / "model.pt").write_bytes(b"")


def test_asking_for_a_dedicated_model_that_is_not_there_is_an_error(tmp_path):
    _song(tmp_path, "Hi you\n")
    with pytest.raises(GenerateError, match="w2v2-en"):
        generate(tmp_path, model_dir=tmp_path, params={"w2v_en": 1})


def test_dedicated_models_are_off_by_default():
    from kizami.generate.place import DEFAULTS

    assert DEFAULTS["w2v_en"] == DEFAULTS["w2v_es"] == DEFAULTS["w2v_ko"] == 0
    assert DEFAULTS["w2v_star"] == 1 and DEFAULTS["w2v_silence"] == 5.0


def test_silence_penalty_spares_the_given_columns():
    from kizami.generate.align import mask_silence

    em = np.zeros((2, 7), dtype=np.float32)
    out = mask_silence(em, np.array([True, False]), 5.0, keep=(4, 6, -1))
    assert (out[0] == 0).all()
    assert (out[1, [4, 6]] == 0).all()
    assert (out[1, [0, 1, 2, 3, 5]] == -5.0).all()


def _wav(path, seconds=4.0, sr=16000):
    import wave

    t = np.arange(int(seconds * sr)) / sr
    x = 0.3 * np.sin(2 * np.pi * 220 * t) * ((t % 1.0) < 0.6)  # four sung bursts
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((x * 32767).astype("<i2").tobytes())


def _fake_mms(monkeypatch):
    torchaudio = pytest.importorskip("torchaudio")
    from kizami.generate import align as align_mod

    size = len(torchaudio.pipelines.MMS_FA.get_dict(star="*"))

    def fake(audio, model_file, key=None):
        n = (len(audio) - 400) // 320 + 1
        rng = np.random.default_rng(1)
        x = rng.standard_normal((n, size)).astype(np.float32)
        return (x - np.log(np.exp(x).sum(axis=1, keepdims=True))).astype(np.float32)

    monkeypatch.setattr(align_mod, "mms_emissions", fake)


@pytest.mark.parametrize("on", [0, 1])
def test_generate_times_english_with_the_dedicated_model_when_asked(tmp_path, monkeypatch, on):
    pytest.importorskip("sudachipy")
    _fake_mms(monkeypatch)
    _song(tmp_path, "Hi you\nGo now\n")
    _wav(tmp_path / "vocals.wav")
    _tiny_model(tmp_path / "w2v2-en", EN)
    cache = tmp_path / "cache"

    result = generate(tmp_path, model_dir=tmp_path, params={"w2v_en": on}, cache=cache, out_dir=tmp_path / "out")

    assert result.dedicated == (["en"] if on else [])
    meta = json.loads((tmp_path / "out" / "auto_chu.json").read_text(encoding="utf-8"))["meta"]
    assert meta.get("dedicated_models") == (["en"] if on else None)
    assert bool(list(cache.glob("*-w2v2-en-*.npy"))) == bool(on)


def test_emissions_follow_do_normalize_of_the_model(tmp_path):
    from kizami.generate.align import w2v2_emissions

    _tiny_model(tmp_path / "m", KO)
    x = (0.5 + 0.01 * np.random.default_rng(0).standard_normal(16000)).astype(np.float32)
    on = w2v2_emissions(x, tmp_path / "m", normalize=True)
    off = w2v2_emissions(x, tmp_path / "m", normalize=False)
    assert not np.allclose(on, off)
    (tmp_path / "m" / "preprocessor_config.json").write_text('{"do_normalize": false}', encoding="utf-8")
    assert np.allclose(w2v2_emissions(x, tmp_path / "m"), off)
