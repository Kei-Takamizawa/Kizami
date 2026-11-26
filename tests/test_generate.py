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
    chu_thresholds,
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
    atoms, kept_whole, *_ = atoms_for(lines, _vocab())
    shown = lines[0]["display"]
    return [(shown[a["offset"] : a["end"]], "".join(a["kana"])) for a in atoms], atoms, kept_whole


def _atoms(*texts, latin="auto"):
    """Units of several lines (no readings), the Latin language used, and the lines."""
    pytest.importorskip("sudachipy")
    pytest.importorskip("sudachidict_core")
    from kizami.generate.text import atoms_for

    lines = [{"display": t, "sung": t, "spans": []} for t in texts]
    atoms, _, used = atoms_for(lines, _vocab(), latin=latin)
    return atoms, used, lines


def _shown(atoms, lines):
    return [lines[a["line"]]["display"][a["offset"] : a["end"]] for a in atoms]


GOLDEN_KEYS = ("line", "offset", "end", "kana", "hiragana", "word_start", "dependent", "line_first")


def test_japanese_units_match_the_golden_file(tmp_path):
    """Units of Japanese-only lines are exactly what the Japanese-only code gave (tests/data)."""
    pytest.importorskip("sudachipy")
    pytest.importorskip("sudachidict_core")
    from pathlib import Path

    from kizami.generate.text import atoms_for

    golden = json.loads((Path(__file__).parent / "data" / "ja_units_golden.json").read_text(encoding="utf-8"))
    (tmp_path / "lyrics.txt").write_text("\n".join(golden["lines"]) + "\n", encoding="utf-8")
    atoms, kept, _ = atoms_for(parse_lyrics(tmp_path / "lyrics.txt"), _vocab())
    assert kept == golden["kept_whole"]
    assert [[a[k] for k in GOLDEN_KEYS] for a in atoms] == golden["units"]


def test_japanese_line_is_the_same_next_to_other_languages():
    alone, _, _ = _atoms("夜空に星が光る")
    mixed, _, _ = _atoms("夜空に星が光る", "I love you baby", "사랑해 너를")
    keys = ("offset", "end", "kana", "hiragana", "word_start", "dependent", "line_first")
    assert [[a[k] for k in keys] for a in mixed if a["line"] == 0] == [[a[k] for k in keys] for a in alone]


def test_korean_units_are_blocks_and_words_start_after_spaces():
    atoms, _, lines = _atoms("사랑해 너를")
    assert _shown(atoms, lines) == ["사", "랑", "해", "너", "를"]
    assert [a["syllable"] for a in atoms] == ["사", "랑", "해", "너", "를"]
    assert [a["word_start"] for a in atoms] == [True, False, False, True, False]
    assert [a["letters"] for a in atoms] == ["sa", "rang", "hae", "neo", "reul"]
    assert {a["lang"] for a in atoms} == {"ko"}
    assert all(a["kana"] == ["*"] and a["hiragana"] is None and not a["dependent"] for a in atoms)
    assert [a["line_first"] for a in atoms] == [True, False, False, False, False]


def test_english_units_are_syllables():
    atoms, used, lines = _atoms("beautiful baby")
    assert used == "en"
    assert _shown(atoms, lines) == ["beau", "ti", "ful", "ba", "by"]
    assert [a["word_start"] for a in atoms] == [True, False, False, True, False]
    assert [a["letters"] for a in atoms] == ["beau", "ti", "ful", "ba", "by"]


def test_spanish_sinalefa_joins_the_next_word():
    atoms, used, lines = _atoms("mi amor")
    assert used == "es"
    assert _shown(atoms, lines) == ["mi", "a", "mor"]
    assert [a["word_start"] for a in atoms] == [True, False, False]
    assert {a["lang"] for a in atoms} == {"es"}


def test_no_sinalefa_in_english():
    atoms, _, _ = _atoms("my eyes", latin="en")
    assert [a["word_start"] for a in atoms] == [True, True]


def test_mixed_line_without_spaces():
    atoms, _, lines = _atoms("Baby사랑해")
    assert _shown(atoms, lines) == ["Ba", "by", "사", "랑", "해"]
    assert [a["word_start"] for a in atoms] == [True, False, True, False, False]
    assert [a["lang"] for a in atoms] == ["en", "en", "ko", "ko", "ko"]


def test_english_word_inside_a_japanese_line():
    atoms, _, lines = _atoms("君のsmileが好き")
    shown = _shown(atoms, lines)
    assert "smile" in shown
    smile = atoms[shown.index("smile")]
    assert smile["lang"] == "en" and smile["word_start"] and smile["letters"] == "smile"
    assert [a["lang"] for a in atoms if a["lang"] == "ja"] and atoms[0]["line_first"]


def test_latin_guess_is_returned():
    assert _atoms("te quiero mi amor")[1] == "es"
    assert _atoms("te quiero mi amor", latin="en")[1] == "en"
    assert {a["lang"] for a in _atoms("te quiero", latin="en")[0]} == {"en"}


def test_japanese_units_carry_romaji_letters():
    from kizami.generate.letters import romaji

    atoms, _, _ = _atoms("青空を駆ける")
    assert all(a["lang"] == "ja" and a["letters"] == romaji(a["kana"]) for a in atoms)


def test_apostrophes_and_hyphens_stay_inside_a_word():
    atoms, _, lines = _atoms("don't stop 'cause rock-n-roll", latin="en")
    assert _shown(atoms, lines) == ["don't", "stop", "'cause", "rock-", "n-", "roll"]
    assert [a["word_start"] for a in atoms] == [True, True, True, True, False, False]
    assert atoms[0]["letters"] == "don't" and atoms[2]["letters"] == "cause"


def test_a_word_without_vowels_is_one_unit():
    atoms, _, lines = _atoms("hmm shh", latin="en")
    assert _shown(atoms, lines) == ["hmm", "shh"]
    assert [a["letters"] for a in atoms] == ["hmm", "shh"]


def test_uppercase_accented_and_full_width_letters():
    atoms, _, lines = _atoms("CORAZÓN", latin="es")
    assert _shown(atoms, lines) == ["CO", "RA", "ZÓN"]
    assert [a["letters"] for a in atoms] == ["co", "ra", "zon"]
    atoms, _, lines = _atoms("ＬＯＶＥ", latin="en")
    assert _shown(atoms, lines) == ["ＬＯＶＥ"]
    assert [a["letters"] for a in atoms] == ["love"]


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


def _auto(display, morae_of, chosen_at):
    """Units of one line placed by hand, made hiragana, then kanji written again unless split.

    ``chosen_at`` lists the hiragana-text offsets of the units that start chunks.
    """
    from kizami.generate.text import kanji_unless_split, to_hiragana

    lines, atoms = _placed_line(display, morae_of)
    out = to_hiragana(lines, atoms)
    chosen = [u for u in out if u["offset"] in chosen_at]
    shown, units, chosen2, left = kanji_unless_split(lines, out, chosen)
    return shown, units, chosen2, left, lines


def test_kanji_stay_as_written_when_no_chunk_starts_inside_them():
    shown, units, chosen, left, lines = _auto(
        "願いを", [[("ね", 1.0, 2.0), ("が", 1.3, 3.8)], [("い", 1.6, 0.5)], [("を", 1.9, 1.0)]], {0, 3}
    )

    assert (shown[0]["display"], left) == ("願いを", 0)
    assert [text for text, *_ in chunks_for(shown, chosen, units)[0]] == ["願い", "を"]
    assert lines[0]["display"] == "ねがいを"  # the input is not changed


def test_a_kanji_word_with_a_chunk_start_inside_is_written_in_hiragana():
    shown, units, chosen, left, _ = _auto(
        "願いを", [[("ね", 1.0, 2.0), ("が", 1.3, 3.8)], [("い", 1.6, 0.5)], [("を", 1.9, 1.0)]], {0, 1}
    )

    assert (shown[0]["display"], left) == ("ねがいを", 1)
    assert [text for text, *_ in chunks_for(shown, chosen, units)[0]] == ["ね", "がいを"]


def test_kanji_next_to_each_other_change_together_but_a_start_between_them_keeps_them():
    morae = [[("よ", 1.0, 2.0)], [("ぞ", 1.3, 2.0), ("ら", 1.6, 3.0)], [("を", 1.9, 1.0)]]

    inside, units, chosen, left, _ = _auto("夜空を", morae, {0, 2})  # starts at よ and at the ら of 空
    between, units2, chosen2, left2, _ = _auto("夜空を", morae, {0, 1})  # starts at 夜 and at 空

    assert (inside[0]["display"], left) == ("よぞらを", 1)
    assert [text for text, *_ in chunks_for(inside, chosen, units)[0]] == ["よぞ", "らを"]
    assert (between[0]["display"], left2) == ("夜空を", 0)
    assert [text for text, *_ in chunks_for(between, chosen2, units2)[0]] == ["夜", "空を"]


def test_kanji_after_a_hiragana_word_keep_their_offsets():
    morae = [[("ね", 1.0, 2.0), ("が", 1.3, 3.8)], [("い", 1.6, 0.5)], [("を", 1.9, 1.0)],
             [("よ", 2.2, 2.0)], [("ぞ", 2.5, 2.0), ("ら", 2.8, 1.0)]]  # fmt: skip
    shown, units, chosen, left, _ = _auto("願いを夜空", morae, {0, 1, 4})  # 願 split; 夜空 not

    assert (shown[0]["display"], left) == ("ねがいを夜空", 1)
    assert [text for text, *_ in chunks_for(shown, chosen, units)[0]] == ["ね", "がいを", "夜空"]
    assert [c["offset"] for c in chosen] == [0, 1, 4]


@pytest.mark.parametrize(
    ("written", "reading", "expected"),
    [
        ("願", "ねが", (0, 2, "願")),
        ("行っ", "いっ", (0, 1, "行")),  # the joined っ stays outside
        ("お願い", "おねがい", (1, 3, "願")),
        ("今日", "きょう", (0, 3, "今日")),
        ("ゆめ", "ゆめ", None),  # no kanji
    ],
)
def test_kanji_part_leaves_the_shared_kana_outside(written, reading, expected):
    from kizami.generate.text import kanji_part

    assert kanji_part(written, reading) == expected


def test_a_space_between_words_stays_with_the_chunk_before_it():
    lines = [{"display": "「夜空を 駆ける」 "}]
    chosen = [{"line": 0, "offset": 1, "time": 1.0, "snapped": True}, {"line": 0, "offset": 5, "time": 2.0, "snapped": True}]

    assert [text for text, *_ in chunks_for(lines, chosen)[0]] == ["「夜空を ", "駆ける」"]


def test_a_long_gap_starts_a_medium_chunk_inside_its_line():
    units = [
        {"line": 0, "time": 1.0, "snapped": True, "strength": 2.0, "word_start": True, "dependent": False, "line_first": True},
        {"line": 0, "time": 1.4, "snapped": True, "strength": 1.0, "word_start": False, "dependent": False, "line_first": False},
        {"line": 0, "time": 2.4, "snapped": False, "strength": 0.0, "word_start": False, "dependent": False, "line_first": False},
        {"line": 0, "time": 2.9, "snapped": True, "strength": 1.0, "word_start": False, "dependent": False, "line_first": False},
        {"line": 1, "time": 9.0, "snapped": True, "strength": 1.0, "word_start": True, "dependent": True, "line_first": False},
    ]

    with_gap = [u["time"] for u in select(units, "chu", m_thr=3.0, gap=1.0)]

    assert with_gap == [1.0, 2.4]  # 1.0 s after the unit before it; a new line never counts as a gap
    assert [u["time"] for u in select(units, "chu", m_thr=3.0)] == [1.0]
    assert [u["time"] for u in select(units, "dai", m_thr=3.0, gap=1.0)] == [1.0]


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


def test_cli_generate_writes_medium_with_kanji_auto_by_default():
    from kizami.cli import _build_parser

    args = _build_parser().parse_args(["generate", "song"])

    assert (args.level, args.kanji, args.anchor) == ("chu", "auto", "prev-end")


def test_a_given_reading_in_english_is_one_unit_with_the_sung_letters(tmp_path):
    pytest.importorskip("sudachipy")
    pytest.importorskip("sudachidict_core")
    from kizami.generate.text import atoms_for

    (tmp_path / "lyrics.txt").write_text("call me {24/7|twenty four seven} baby\n", encoding="utf-8")
    lines = parse_lyrics(tmp_path / "lyrics.txt")
    atoms, _, used = atoms_for(lines, _vocab(), latin="en")
    shown = [lines[0]["display"][a["offset"] : a["end"]] for a in atoms]
    assert shown == ["call", "me", "24/7", "ba", "by"]
    assert atoms[2]["letters"] == "twentyfourseven" and atoms[2]["word_start"]


def _lang_atom(rough, lang, letters, **flags):
    a = _atom(rough, "*" if lang != "ja" else flags.pop("kana", "か"), **flags)
    a.update(lang=lang, letters=letters, syllable=letters)
    return a


def test_mms_letters_come_from_the_units():
    from kizami.generate.align import mms_tokens

    atoms = [_lang_atom(1.0, "ja", "ka"), _lang_atom(2.0, "ko", "sa"), _lang_atom(3.0, "en", "don't")]
    letters, owner = mms_tokens(atoms)
    assert "".join(letters) == "kasadon't"
    assert owner == [0, 0, 1, 1, 2, 2, 2, 2, 2]


def test_star_tokens_open_every_line_and_close_the_song():
    from kizami.generate.align import mms_tokens

    atoms = [_lang_atom(1.0, "en", "hi"), _lang_atom(2.0, "en", "yo"), _lang_atom(3.0, "en", "ok")]
    atoms[2]["line"] = 1
    letters, owner = mms_tokens(atoms, star=True)
    assert "".join(letters) == "*hiyo*ok*"
    assert owner == [-1, 0, 0, 1, 1, -1, 2, 2, -1]
    assert mms_tokens(atoms)[0] == list("hiyook")


def test_units_without_letters_fall_back_to_romaji():
    from kizami.generate.align import mms_tokens

    letters, owner = mms_tokens([_atom(1.0, "か")])  # a unit from before units had letters
    assert letters == ["k", "a"] and owner == [0, 0]


def test_non_japanese_units_take_the_mms_time():
    from kizami.generate.align import adopt_mms

    atoms = [_lang_atom(1.0, "ja", "ka"), _lang_atom(9.0, "en", "love"), _lang_atom(None, "ko", "sa")]
    for a, (m, last) in zip(atoms, ((1.1, 1.1), (2.0, 2.1), (3.0, 3.2))):
        a.update(mms=m, mms_last=last, mms_conf=0.4)
    assert adopt_mms(atoms) == 2
    assert atoms[0]["rough"] == 1.0  # Japanese keeps the kana model's time
    assert (atoms[1]["rough"], atoms[1]["rough_last"], atoms[1]["conf"]) == (2.0, 2.1, 0.4)
    assert atoms[2]["rough"] == 3.0 and atoms[2]["kana_starts"] == [3.0]


def test_fusion_offset_uses_japanese_units_only():
    atoms = [_lang_atom(1.0, "ja", "ka"), _lang_atom(2.0, "ja", "ka"), _lang_atom(3.5, "ja", "ka")]
    for a, m in zip(atoms, (1.0, 2.0, 3.0)):
        a["mms"], a["mms_conf"] = m, 0.3
    others = [_lang_atom(t, "en", "la") for t in (5.0, 6.0, 7.0, 8.0)]
    for a in others:
        a["mms"], a["mms_conf"] = a["rough"] - 0.5, 0.3  # would move the median offset if counted
    onsets, strengths = np.array([0.98, 1.98, 2.98]), np.array([4.0, 4.0, 4.0])
    atoms[2]["conf"] = 0.001
    out, n = fused_rough(atoms + others, DEFAULTS, onsets, strengths)
    assert n == 1 and out[2] == (3.0, 3.0)
    assert out[3:] == [(a["rough"], a["rough_last"]) for a in others]


def test_lag_is_read_per_language():
    atoms = [_lang_atom(1.0, "ja", "ka"), _lang_atom(2.0, "en", "la")]
    onsets, strengths = [0.95, 1.95], [5.0, 5.0]
    p = dict(DEFAULTS, lag=0.0, lag_en=-0.05, before=0.01, after=0.01)
    place(atoms, onsets, strengths, p, np.zeros(1000, bool), 200.0)
    assert not atoms[0]["snapped"]  # 50 ms away from the Japanese search centre
    assert atoms[1]["snapped"] and atoms[1]["time"] == pytest.approx(1.95)


def test_threshold_per_language_keeps_japanese():
    ja = [dict(_lang_atom(1.0, "ja", "ka", word_start=False), snapped=True, strength=float(s)) for s in range(1, 11)]
    en = [dict(_lang_atom(1.0, "en", "la", word_start=False), snapped=True, strength=100.0) for _ in range(10)]
    t = chu_thresholds(ja + en, 0.45)
    assert t["ja"] == pytest.approx(chu_threshold(ja, 0.45))
    assert t["en"] == pytest.approx(100.0)


def test_select_uses_the_unit_language_threshold():
    lines, atoms = _timed("ab", [(0, 1.0, True, 5.0, True, False), (1, 2.0, True, 5.0, False, False)])
    atoms[1]["lang"] = "en"
    assert len(select(atoms, "chu", {"ja": 1.0, "en": 9.0})) == 1
    assert len(select(atoms, "chu", {"ja": 9.0, "en": 1.0})) == 2
    assert len(select(atoms, "chu", 1.0)) == 2  # one number still works


def test_non_japanese_units_list_their_syllable_as_one_mora():
    atoms = [_lang_atom(1.0, "en", "beau")]
    atoms[0]["syllable"] = "beau"
    place(atoms, [0.98], [5.0], dict(DEFAULTS), np.zeros(1000, bool), 200.0)
    place_morae(atoms, [0.98], [5.0], dict(DEFAULTS), np.zeros(1000, bool), 200.0)
    assert atoms[0]["morae"] == [("beau", atoms[0]["time"], True)]


@pytest.mark.parametrize("lang, letters", [("en", "so"), ("es", "si"), ("ko", "sa"), ("en", "shy"), ("es", "cielo"), ("ko", "jo")])
def test_sibilant_from_letters(lang, letters):
    from kizami.generate.place import is_sibilant

    assert is_sibilant(_lang_atom(1.0, lang, letters))


@pytest.mark.parametrize("lang, letters", [("en", "love"), ("es", "casa"), ("ko", "na"), ("en", "jump")])
def test_not_sibilant_from_letters(lang, letters):
    from kizami.generate.place import is_sibilant

    assert not is_sibilant(_lang_atom(1.0, lang, letters))


def _with_kana_model(tmp_path):
    model = tmp_path / "w2v2-hiragana"
    model.mkdir()
    (model / "vocab.json").write_text(json.dumps(_vocab()), encoding="utf-8")
    (model / "config.json").write_text("{}", encoding="utf-8")
    (model / "model.safetensors").write_bytes(b"")


def test_latin_must_be_auto_english_or_spanish(tmp_path):
    (tmp_path / "vocals.wav").write_bytes(b"")
    (tmp_path / "lyrics.txt").write_text("あ\n", encoding="utf-8")
    with pytest.raises(GenerateError, match="latin"):
        generate(tmp_path, model_dir=tmp_path, latin="fr")


def test_a_korean_song_needs_the_letter_model_and_not_the_kana_model(tmp_path):
    (tmp_path / "vocals.wav").write_bytes(b"")
    (tmp_path / "lyrics.txt").write_text("사랑해\n", encoding="utf-8")
    with pytest.raises(GenerateError, match="mms_fa/model.pt") as err:
        generate(tmp_path, model_dir=tmp_path)
    assert "kana model" not in str(err.value)


def test_the_letter_model_cannot_be_turned_off_for_other_languages(tmp_path):
    (tmp_path / "vocals.wav").write_bytes(b"")
    (tmp_path / "lyrics.txt").write_text("あ\nI love you\n", encoding="utf-8")
    _with_kana_model(tmp_path)
    with pytest.raises(GenerateError, match="letter model"):
        generate(tmp_path, model_dir=tmp_path, mms="off")


def test_languages_and_latin_are_written_to_the_timing_file(tmp_path):
    from kizami.generate import _write_level
    from kizami.timing import load_document

    lines, atoms = _timed("ab", [(0, 1.0, True, 5.0, True, False)])
    _write_level(tmp_path, "chu", lines, atoms, dict(DEFAULTS), meta_extra={"languages": {"ko": 3, "en": 1}, "latin": "en"})
    meta = load_document(tmp_path / "auto_chu.json").meta
    assert meta["languages"] == {"ko": 3, "en": 1} and meta["latin"] == "en"


def test_cli_generate_takes_the_latin_language():
    from kizami.cli import _build_parser

    assert _build_parser().parse_args(["generate", "song"]).latin == "auto"
    assert _build_parser().parse_args(["generate", "song", "--latin", "es"]).latin == "es"
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["generate", "song", "--latin", "fr"])


def test_a_line_without_letters_in_a_song_without_japanese_is_not_japanese():
    atoms, _, lines = _atoms("사랑해 너를", "3, 2, 1", "사랑해")
    count_in = [a for a in atoms if a["line"] == 1]
    assert _shown(count_in, lines) == ["3", "2", "1"]
    assert {a["lang"] for a in atoms} == {"ko"}
    assert [a["letters"] for a in count_in] == ["*", "*", "*"]
    assert [a["word_start"] for a in count_in] == [True, True, True]
    assert [a["line_first"] for a in count_in] == [True, False, False]


def test_a_line_without_letters_takes_the_main_language_of_the_song():
    atoms, used, _ = _atoms("I love you baby", "5, 6, 7, 8", "사랑해")
    assert used == "en"
    assert {a["lang"] for a in atoms if a["line"] == 1} == {"en"}


def test_a_line_without_letters_in_a_japanese_song_stays_japanese():
    atoms, _, _ = _atoms("夜空に星が光る", "3, 2, 1")
    assert {a["lang"] for a in atoms if a["line"] == 1} == {"ja"}


def test_a_leading_apostrophe_belongs_to_its_word_and_is_not_a_letter():
    atoms, _, lines = _atoms("사랑해 'cause I love you", "I want you 'cause", "'til the end", latin="en")
    shown = _shown(atoms, lines)
    starts = [a for a, s in zip(atoms, shown) if s in ("'cause", "'til")]
    assert [s for s in shown if "'" in s] == ["'cause", "'cause", "'til"]
    assert [a["letters"] for a in starts] == ["cause", "cause", "til"]
    assert all(a["word_start"] for a in starts)
    assert [a["letters"] for a in _atoms("don't", latin="en")[0]] == ["don't"]


def _tone_in_silence(level=0.3, floor=0.0):
    sr = 16000
    rng = np.random.default_rng(0)
    x = floor * rng.standard_normal(3 * sr)
    t = np.arange(sr) / sr
    x[sr : 2 * sr] = level * np.sin(2 * np.pi * 220 * t)
    return x.astype(np.float32)


def test_voiced_frames_mark_singing_and_a_margin_around_it():
    from kizami.generate.align import voiced_frames

    voiced = voiced_frames(_tone_in_silence(), 150, pad=3)
    assert voiced.dtype == bool and len(voiced) == 150
    assert not voiced[:47].any() and voiced[47:103].all() and not voiced[103:].any()


def test_voiced_frames_ignore_a_low_noise_floor():
    from kizami.generate.align import voiced_frames

    voiced = voiced_frames(_tone_in_silence(floor=0.002), 150, pad=0)
    assert voiced[50:100].all() and not voiced[:50].any() and not voiced[100:].any()


def test_silence_penalty_lowers_only_letters_outside_singing():
    from kizami.generate.align import mask_silence

    em = np.zeros((4, 5), dtype=np.float32)
    voiced = np.array([True, False, True, False])
    out = mask_silence(em, voiced, 8.0)
    assert (out[[0, 2]] == 0).all()  # sung frames unchanged
    assert (out[[1, 3], 1:4] == -8.0).all()  # letters in silence
    assert (out[:, 0] == 0).all() and (out[:, 4] == 0).all()  # blank and star stay
    assert (em == 0).all()  # the input is not changed
    assert mask_silence(em, voiced, 0.0) is em


def test_digits_in_a_line_of_another_language_get_their_own_unit():
    atoms, _, lines = _atoms("I have 2 dreams", "사랑해 1 2 3", "君と 2 people", latin="en")
    shown = _shown(atoms, lines)
    digits = [(lines[a["line"]]["display"][a["offset"] : a["end"]], a["lang"]) for a in atoms if a["letters"] == "*"]
    assert digits == [("2", "en"), ("1", "ko"), ("2", "ko"), ("3", "ko"), ("2", "en")]
    assert shown[:4] == ["I", "have", "2", "dreams"]
    assert all(a["word_start"] for a in atoms if a["letters"] == "*")
    for li in range(3):
        firsts = [a["line_first"] for a in atoms if a["line"] == li]
        assert firsts[0] and not any(firsts[1:])
