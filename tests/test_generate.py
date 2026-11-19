import json

import pytest

np = pytest.importorskip("numpy")

from kizami.cli import main  # noqa: E402
from kizami.generate import GenerateError, generate  # noqa: E402
from kizami.generate.align import romaji  # noqa: E402
from kizami.generate.audio import envelope, hiss_start  # noqa: E402
from kizami.generate.place import (  # noqa: E402
    DEFAULTS,
    chu_threshold,
    chunks_for,
    fused_rough,
    mora_groups,
    place,
    place_morae,
    prev_sound_end,
    select,
)
from kizami.generate.text import parse_lyrics  # noqa: E402

HOP = 0.0025


# ---------------------------------------------------------------- lyrics text


def test_parse_lyrics_skips_tags_and_blank_lines_and_reads_readings(tmp_path):
    path = tmp_path / "lyrics.txt"
    path.write_text("﻿[Verse]\n\n  明日{あした}へ \n{空|そら}と海\n", encoding="utf-8")

    lines = parse_lyrics(path)

    assert [line["display"] for line in lines] == ["明日へ", "空と海"]
    assert [line["sung"] for line in lines] == ["あしたへ", "そらと海"]
    assert lines[0]["spans"] == [(0, 2, 0, 3)]
    assert lines[1]["spans"] == [(0, 1, 0, 2)]


def test_reading_after_a_kanji_in_the_bar_form_covers_only_its_braces(tmp_path):
    path = tmp_path / "lyrics.txt"
    path.write_text("今{日|ひ}\n", encoding="utf-8")

    (line,) = parse_lyrics(path)

    assert (line["display"], line["sung"], line["spans"]) == ("今日", "今ひ", [(1, 2, 1, 2)])


@pytest.mark.parametrize(
    "kana, letters",
    [
        (["き", "ゃ"], "kya"),
        (["し", "ゃ"], "sha"),
        (["ち", "ょ"], "cho"),
        (["じ", "ゅ"], "ju"),
        (["ふ", "ぁ"], "fa"),
        (["か", "っ"], "ka"),
        (["そ", "ー"], "soo"),
        (["*", "*"], "**"),
    ],
)
def test_romaji(kana, letters):
    assert romaji(kana) == letters


def _vocab():
    kana = [chr(c) for c in range(0x3041, 0x3097)] + ["ー"]
    return {"[PAD]": 0, "|": 1, **{k: i + 2 for i, k in enumerate(kana)}}


def _units(display, tmp_path=None):
    """Units of one line; with ``tmp_path`` the line is read as a lyrics file (readings in braces)."""
    pytest.importorskip("sudachipy")
    pytest.importorskip("sudachidict_core")
    from kizami.generate.text import atoms_for

    if tmp_path is None:
        lines = [{"display": display, "sung": display, "spans": []}]
    else:
        (tmp_path / "lyrics.txt").write_text(display + "\n", encoding="utf-8")
        lines = parse_lyrics(tmp_path / "lyrics.txt")
    atoms, kept_whole = atoms_for(lines, _vocab())
    shown = lines[0]["display"]
    return [(shown[a["offset"] : a["end"]], "".join(a["kana"])) for a in atoms], atoms, kept_whole


def test_kanji_compound_is_split_per_kanji_with_its_reading():
    units, atoms, _ = _units("青空を")

    assert units == [("青", "あお"), ("空", "ぞら"), ("を", "を")]
    assert [a["word_start"] for a in atoms] == [True, False, True]
    assert atoms[2]["dependent"]  # a particle joins the word before it at level dai


def test_small_kana_geminate_and_long_vowel_join_the_unit_before():
    assert _units("ちょっと")[0] == [("ちょっ", "ちょっ"), ("と", "と")]
    assert _units("そうだ")[0] == [("そう", "そう"), ("だ", "だ")]


def test_final_u_of_a_verb_keeps_its_own_unit():
    assert _units("思う")[0] == [("思", "おも"), ("う", "う")]


def test_irregular_reading_keeps_the_compound_whole():
    units, _, kept_whole = _units("今日も")

    assert units == [("今日", "きょう"), ("も", "も")]
    assert kept_whole == 1


def test_given_reading_is_used_and_the_rest_of_the_word_keeps_its_own(tmp_path):
    assert _units("{大|だい}好きで", tmp_path)[0] == [("大", "だい"), ("好", "す"), ("き", "き"), ("で", "で")]
    units = _units("今{日|ひ}", tmp_path)[0]
    assert [u[0] for u in units] == ["今", "日"]
    assert units[1] == ("日", "ひ")


def test_units_with_kanji_record_their_reading_in_hiragana(tmp_path):
    assert [a["hiragana"] for a in _units("願いを")[1]] == ["ねが", None, None]
    assert [a["hiragana"] for a in _units("待って")[1]] == ["まっ", None]  # the joined っ is part of it
    assert [a["hiragana"] for a in _units("今日も")[1]] == ["きょう", None]
    assert [a["hiragana"] for a in _units("宇宙{そら}をタワー", tmp_path)[1]] == ["そら", None, None, None]


def _placed_line(display, morae_of):
    """Units of one line, placed by hand: ``morae_of[i]`` is ``[(kana, time, strength), ...]`` for unit i."""
    _, atoms, _ = _units(display)
    for a, morae in zip(atoms, morae_of):
        a.update(time=morae[0][1], snapped=morae[0][2] > 0, strength=morae[0][2], why="")
        a["morae"] = [(k, t, s > 0) for k, t, s in morae]
        a["mora_strengths"] = [s for _, _, s in morae]
    return [{"display": display}], atoms


def test_hiragana_gives_every_mora_of_a_kanji_its_own_unit():
    from kizami.generate.text import to_hiragana

    lines, atoms = _placed_line("願いを", [[("ね", 1.0, 2.0), ("が", 1.3, 3.8)], [("い", 1.6, 0.5)], [("を", 1.9, 1.0)]])

    out = to_hiragana(lines, atoms)

    assert (lines[0]["display"], lines[0]["original"]) == ("ねがいを", "願いを")
    assert [lines[0]["display"][u["offset"] : u["end"]] for u in out] == ["ね", "が", "い", "を"]
    assert [u["written"] for u in out] == [0, None, 1, 2]  # が has no place in the written text
    assert [u["word_start"] for u in out] == [True, False, False, True]
    assert (out[1]["time"], out[1]["snapped"], out[1]["strength"]) == (1.3, True, 3.8)
    assert out[0]["time"] == 1.0 and out[0]["morae"] == [("ね", 1.0, True)]


def test_hiragana_keeps_a_kanji_whole_when_its_morae_are_not_known():
    from kizami.generate.text import to_hiragana

    lines, atoms = _placed_line("夜へ、タワー", [[("よ", 1.0, 2.0), ("る", 1.1, 3.0)], [("へ", 1.2, 1.0)], [("た", 1.5, 2.0)], [("わ", 1.8, 0)]])
    atoms[0]["time"] = None  # not placed: shown in hiragana, never split

    out = to_hiragana(lines, atoms)

    assert lines[0]["display"] == "よるへ、タワー"  # punctuation and katakana stay as written
    assert [lines[0]["display"][u["offset"] : u["end"]] for u in out] == ["よる", "へ", "タ", "ワー"]


def test_a_medium_chunk_can_start_inside_a_kanji():
    from kizami.generate.text import to_hiragana

    lines, atoms = _placed_line("願いを", [[("ね", 1.0, 2.0), ("が", 1.3, 3.8)], [("い", 1.6, 0.5)], [("を", 1.9, 1.0)]])
    out = to_hiragana(lines, atoms)

    chosen = select(out, "chu", m_thr=3.0)

    assert [text for text, *_ in chunks_for(lines, chosen, out)[0]] == ["ね", "がいを"]
    assert chunks_for(lines, chosen, out)[0][1][3] == [("が", 1.3, False), ("い", 1.6, False), ("を", 1.9, False)]


# ---------------------------------------------------------- chunk start rules


def _env(pieces, total=2.0):
    """Envelope from (start s, level) steps, one frame per 2.5 ms."""
    tt = np.arange(0, total, HOP)
    env = np.zeros_like(tt)
    for start, level in pieces:
        env[tt >= start - 1e-9] = level
    return env, tt


def test_chunk_starts_where_the_previous_sound_falls_into_the_valley():
    env, tt = _env([(0.0, 1.0), (1.05, 0.1), (1.10, 1.0)])

    t, why = prev_sound_end(env, tt, 1.10, 0.5, DEFAULTS)

    assert why == "prev-end"
    assert t == pytest.approx(1.0475, abs=0.003)


def test_unbroken_sound_starts_just_before_the_attack():
    env, tt = _env([(0.0, 1.0)])

    t, why = prev_sound_end(env, tt, 1.10, 0.5, DEFAULTS)

    assert why == "legato"
    assert t == pytest.approx(1.10 - DEFAULTS["lead"])


def test_after_silence_the_chunk_starts_at_the_rise():
    env, tt = _env([(0.0, 1e-4), (1.08, 1.0)])

    t, why = prev_sound_end(env, tt, 1.10, 0.5, DEFAULTS)

    assert why == "silence"
    assert t == pytest.approx(1.0775, abs=0.003)


def test_after_a_long_gap_the_chunk_starts_at_the_rise():
    env, tt = _env([(0.0, 1.0), (0.70, 0.01), (1.08, 1.0)])

    t, why = prev_sound_end(env, tt, 1.10, 0.0, DEFAULTS)

    assert why == "gap"
    assert t == pytest.approx(1.0775, abs=0.003)


def test_envelope_follows_the_level():
    sr = 8000
    x = np.zeros(sr, dtype=np.float32)
    x[sr // 2 :] = 0.5
    env, tt = envelope(x, sr)

    assert env[tt < 0.4].max() == 0
    assert env[tt > 0.6].min() == pytest.approx(0.5, abs=1e-6)


def test_hiss_start_finds_the_start_of_a_hiss_run():
    fps = 200.0
    hiss = np.zeros(400, dtype=bool)
    hiss[180:198] = True  # 0.90 to 0.99 s

    assert hiss_start(hiss, fps, 1.0, 0.5) == pytest.approx(0.90)
    assert hiss_start(hiss, fps, 1.5, 0.5) is None  # too far after the hiss


# ------------------------------------------------------------- placing units


def _atom(rough, kana="か", **flags):
    return {
        "rough": rough,
        "rough_last": rough,
        "conf": 0.5,
        "kana": [kana],
        "line": 0,
        "line_first": flags.get("line_first", False),
        "word_start": flags.get("word_start", True),
        "dependent": flags.get("dependent", False),
    }


def test_place_snaps_to_the_best_nearby_attack_and_keeps_the_minimum_gap():
    atoms = [_atom(1.02), _atom(1.10, "な")]
    onsets, strengths = [0.98, 1.04], [2.0, 5.0]

    place(atoms, onsets, strengths, dict(DEFAULTS), np.zeros(1000, bool), 200.0)

    assert atoms[0]["snapped"] and atoms[0]["time"] == pytest.approx(1.04)
    assert atoms[0]["strength"] == 5.0
    assert not atoms[1]["snapped"]  # the only attack nearby is taken
    assert atoms[1]["time"] == pytest.approx(1.04 + DEFAULTS["min_gap"])


def test_second_aligner_is_used_only_where_the_first_is_unsure_and_an_attack_agrees():
    atoms = [_atom(1.0), _atom(2.0), _atom(3.5)]
    for a, m in zip(atoms, (1.0, 2.0, 3.0)):
        a["mms"], a["mms_conf"] = m, 0.3
    onsets, strengths = np.array([0.98, 1.98, 2.98]), np.array([4.0, 4.0, 4.0])

    atoms[2]["conf"] = 0.5  # sure of itself: kept
    assert fused_rough(atoms, DEFAULTS, onsets, strengths) == ([(1.0, 1.0), (2.0, 2.0), (3.5, 3.5)], 0)

    atoms[2]["conf"] = 0.001
    out, n = fused_rough(atoms, DEFAULTS, onsets, strengths)
    assert n == 1 and out[2] == (3.0, 3.0)


def _timed(display, specs):
    """Units of one line: (offset, time, snapped, strength, word_start, dependent)."""
    atoms = []
    for i, (offset, time, snapped, strength, word_start, dependent) in enumerate(specs):
        atoms.append(
            {
                "line": 0,
                "offset": offset,
                "time": time,
                "snapped": snapped,
                "strength": strength,
                "word_start": word_start,
                "dependent": dependent,
                "line_first": i == 0,
            }
        )
    return [{"display": display}], atoms


def test_levels_share_times_and_differ_in_which_units_start_a_chunk():
    lines, atoms = _timed(
        "「あいうえ」",
        [
            (1, 1.0, True, 3.0, True, False),
            (2, 1.2, True, 9.0, False, False),
            (3, 1.4, True, 1.0, False, False),
            (4, 1.6, False, 0.0, True, True),
        ],
    )
    m_thr = chu_threshold(atoms, 0.5)

    dai = chunks_for(lines, select(atoms, "dai", m_thr))
    chu = chunks_for(lines, select(atoms, "chu", m_thr))
    sho = chunks_for(lines, select(atoms, "sho", m_thr))

    assert dai == [[("「あいうえ」", 1.0, False, [])]]
    assert chu == [[("「あ", 1.0, False, []), ("いうえ」", 1.2, False, [])]]
    assert sho == [[("「あ", 1.0, False, []), ("い", 1.2, False, []), ("う", 1.4, False, []), ("え」", 1.6, True, [])]]


def test_mora_groups_keep_small_kana_with_the_kana_before():
    assert mora_groups(["ね", "が"]) == [("ね", 0), ("が", 1)]
    assert mora_groups(["き", "ょ", "う"]) == [("きょ", 0), ("う", 2)]
    assert mora_groups(["が", "っ", "こ", "ー"]) == [("がっ", 0), ("こー", 2)]


def _placed(kana, kana_starts, time, onset, line=0, offset=0):
    return {
        "kana": kana,
        "kana_starts": kana_starts,
        "time": time,
        "onset": onset,
        "snapped": True,
        "strength": 3.0,
        "shift": 0.0,
        "line": line,
        "offset": offset,
        "word_start": True,
        "dependent": False,
        "line_first": offset == 0,
    }


def test_a_later_mora_snaps_to_its_own_attack_inside_the_unit():
    first = _placed(["ね", "が"], [1.00, 1.30], 0.98, 1.00)
    second = _placed(["い"], [1.70], 1.66, 1.68, offset=1)
    onsets, strengths = [1.00, 1.33, 1.68], [3.0, 6.0, 3.0]

    place_morae([first, second], onsets, strengths, dict(DEFAULTS), np.zeros(1000, bool), 200.0)

    assert first["morae"][0] == ("ね", 0.98, True)
    kana, start, snapped = first["morae"][1]
    assert (kana, snapped) == ("が", True)
    assert start == pytest.approx(1.33)  # aligned at 1.28, the attack is at 1.33
    assert second["morae"] == [("い", 1.66, True)]


def test_a_mora_without_room_stays_between_its_neighbours():
    first = _placed(["ね", "が"], [1.00, 1.50], 0.98, 1.00)  # aligned after the next unit
    second = _placed(["い"], [1.20], 1.10, 1.12, offset=1)

    place_morae([first, second], [1.00, 1.12], [3.0, 3.0], dict(DEFAULTS), np.zeros(1000, bool), 200.0)

    _, start, snapped = first["morae"][1]
    assert not snapped
    assert 0.98 + 0.03 <= start <= 1.10 - 0.03


def test_chunks_list_the_morae_of_all_their_units():
    lines = [{"display": "願い"}]
    first = _placed(["ね", "が"], [1.00, 1.30], 0.98, 1.00)
    second = _placed(["い"], [1.70], 1.66, 1.68, offset=1)
    second["word_start"] = False
    first["morae"] = [("ね", 0.98, True), ("が", 1.31, False)]
    second["morae"] = [("い", 1.66, True)]
    atoms = [first, second]

    (dai,) = chunks_for(lines, select(atoms, "dai", 99.0), atoms)
    sho = chunks_for(lines, select(atoms, "sho", 99.0), atoms)[0]

    assert dai == [("願い", 0.98, False, [("ね", 0.98, False), ("が", 1.31, True), ("い", 1.66, False)])]
    assert sho[0][3] == [("ね", 0.98, False), ("が", 1.31, True)]
    assert sho[1][3] == []  # a single mora needs no list


# -------------------------------------------------------------- entry points


def test_generate_reports_missing_inputs(tmp_path):
    with pytest.raises(GenerateError, match="vocal track not found"):
        generate(tmp_path, model_dir=tmp_path)

    (tmp_path / "vocals.wav").write_bytes(b"")
    (tmp_path / "lyrics.txt").write_text("あ\n", encoding="utf-8")
    with pytest.raises(GenerateError, match="kana model"):
        generate(tmp_path, model_dir=tmp_path)

    model = tmp_path / "w2v2-hiragana"
    model.mkdir()
    (model / "vocab.json").write_text(json.dumps(_vocab()), encoding="utf-8")
    with pytest.raises(GenerateError, match="config.json"):
        generate(tmp_path, model_dir=tmp_path)

    (model / "config.json").write_text("{}", encoding="utf-8")
    (model / "model.safetensors").write_bytes(b"")
    with pytest.raises(GenerateError, match="second model"):
        generate(tmp_path, model_dir=tmp_path, mms="on")
    with pytest.raises(GenerateError, match="levels"):
        generate(tmp_path, model_dir=tmp_path, levels=["big"])
    with pytest.raises(GenerateError, match="kanji"):
        generate(tmp_path, model_dir=tmp_path, kanji="katakana")


def test_vocabulary_without_a_blank_token_is_refused(tmp_path):
    pytest.importorskip("torch")
    (tmp_path / "vocals.wav").write_bytes(b"")
    (tmp_path / "lyrics.txt").write_text("あ\n", encoding="utf-8")
    model = tmp_path / "w2v2-hiragana"
    model.mkdir()
    (model / "vocab.json").write_text(json.dumps({"<pad>": 0, "あ": 1}), encoding="utf-8")
    (model / "config.json").write_text("{}", encoding="utf-8")
    (model / "model.safetensors").write_bytes(b"")

    with pytest.raises(GenerateError, match=r"\[PAD\]"):
        generate(tmp_path, model_dir=tmp_path, mms="off")


def test_morae_are_written_to_the_timing_file(tmp_path):
    from kizami.generate import _write_level
    from kizami.timing import Mora, load_document

    lines = [{"display": "願"}]
    unit = _placed(["ね", "が"], [1.00, 1.30], 0.98, 1.00)
    unit["morae"] = [("ね", 0.98, True), ("が", 1.3304, False)]

    result = _write_level(tmp_path, "sho", lines, [unit], {}, [unit])

    (chunk,) = load_document(result.timing).lines[0].chunks
    assert chunk.morae == (Mora("ね", 980), Mora("が", 1330, uncertain=True))
    assert "が" not in (tmp_path / "auto_labels_sho.txt").read_text(encoding="utf-8")


def test_written_start_times_are_never_negative(tmp_path):
    from kizami.generate import _write_level
    from kizami.timing import load_document

    lines = [{"display": "あい"}]
    chosen = [
        {"line": 0, "offset": 0, "time": -0.02, "snapped": False},
        {"line": 0, "offset": 1, "time": 0.5, "snapped": True},
    ]

    result = _write_level(tmp_path, "sho", lines, chosen, {"lag": -0.02})

    doc = load_document(result.timing)
    assert [c.start_ms for c in doc.lines[0].chunks] == [0, 500]
    assert result.uncertain == 1
    assert (tmp_path / "auto_labels_sho.txt").read_text(encoding="utf-8").startswith("0.000000\t0.000000\t?あ")


def test_cli_generate_needs_a_folder_and_a_model_folder(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("KIZAMI_MODEL_DIR", raising=False)

    assert main(["generate"]) == 2
    assert "song folder" in capsys.readouterr().err

    assert main(["generate", str(tmp_path)]) == 2
    assert "KIZAMI_MODEL_DIR" in capsys.readouterr().err

    monkeypatch.setenv("KIZAMI_MODEL_DIR", str(tmp_path))
    assert main(["generate", str(tmp_path)]) == 2
    assert "vocal track not found" in capsys.readouterr().err


def test_cli_generate_writes_medium_with_hiragana_by_default():
    from kizami.cli import _build_parser

    args = _build_parser().parse_args(["generate", "song"])

    assert (args.level, args.kanji, args.anchor) == ("chu", "hiragana", "prev-end")
