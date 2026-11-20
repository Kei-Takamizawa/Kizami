import pytest

from kizami.datasets import DatasetError, csd_reference, jamendo_reference
from kizami.sweep import parse_param


def _jamendo(tmp_path, lyrics="lay awake at night\nwondering how\n", words=None, rows=None):
    words = words or ["lay", "awake", "at", "night", "wondering", "how"]
    rows = rows or [(18.62, 18.89, "nan"), (18.89, 19.21, "nan"), (19.21, 19.44, "nan"), (19.44, 19.87, "19.87"),
                    (21.0, 21.6, "nan"), (21.6, 22.0, "22.0")]  # fmt: skip
    (tmp_path / "song.txt").write_text(lyrics, encoding="utf-8")
    (tmp_path / "song.words.txt").write_text("\n".join(words) + "\n", encoding="utf-8")
    csv = "word_start,word_end,line_end\n" + "".join(f"{a},{b},{c}\n" for a, b, c in rows)
    (tmp_path / "song.csv").write_text(csv, encoding="utf-8")
    return tmp_path / "song.csv", tmp_path / "song.words.txt", tmp_path / "song.txt"


def test_jamendo_words_become_one_chunk_each_in_the_lyric_lines(tmp_path):
    doc = jamendo_reference(*_jamendo(tmp_path))
    assert [[c.text for c in line.chunks] for line in doc.lines] == [["lay ", "awake ", "at ", "night"], ["wondering ", "how"]]
    first = doc.lines[0].chunks[0]
    assert (first.start_ms, first.end_ms) == (18620, 18890)
    assert doc.kind == "reference" and doc.meta["source"] == "jamendolyrics"


def test_jamendo_word_count_must_match(tmp_path):
    with pytest.raises(DatasetError, match="6 words"):
        jamendo_reference(*_jamendo(tmp_path, rows=[(1.0, 2.0, "nan")] * 5))


def test_jamendo_words_must_follow_the_lyrics(tmp_path):
    with pytest.raises(DatasetError, match="line 2"):
        jamendo_reference(*_jamendo(tmp_path, lyrics="lay awake at night\nwandering how\n"))


def test_cli_import_ref_writes_a_reference(tmp_path, capsys):
    from kizami.cli import main
    from kizami.timing import load_document

    csv, words, lyrics = _jamendo(tmp_path)
    main(["import-ref", "--format", "jamendo", str(csv), "--words", str(words), "--lyrics", str(lyrics), "-o", str(tmp_path / "ref.json")])
    assert len(load_document(tmp_path / "ref.json").lines) == 2
    assert "6 chunks" in capsys.readouterr().out


def _csd(tmp_path, lyrics="가나 다\n라마\n", rows=None):
    rows = rows if rows is not None else [(1.0, 1.4, 60, "가"), (1.5, 1.9, 62, "나"), (2.0, 2.8, 64, "다"),
                                          (4.0, 4.5, 60, "라"), (4.5, 5.25, 59, "마")]  # fmt: skip
    (tmp_path / "kr.txt").write_text(lyrics, encoding="utf-8")
    csv = "start,end,pitch,syllable\n" + "".join(f"{a},{b},{c},{d}\n" for a, b, c, d in rows)
    (tmp_path / "kr.csv").write_text(csv, encoding="utf-8")
    return tmp_path / "kr.csv", tmp_path / "kr.txt"


def test_csd_notes_become_one_chunk_per_hangul_block(tmp_path):
    doc = csd_reference(*_csd(tmp_path))
    assert [[c.text for c in line.chunks] for line in doc.lines] == [["가", "나 ", "다"], ["라", "마"]]
    assert [(c.start_ms, c.end_ms) for c in doc.lines[1].chunks] == [(4000, 4500), (4500, 5250)]
    assert doc.kind == "reference" and doc.meta["source"] == "csd"


def test_csd_rows_without_a_hangul_block_are_skipped(tmp_path):
    rows = [(0.5, 0.9, 0, ""), (1.0, 1.4, 60, "가"), (1.5, 1.9, 62, "나"), (2.0, 2.8, 64, "다"), (3.0, 3.5, 0, "-"),
            (4.0, 4.5, 60, "라"), (4.5, 5.25, 59, "마")]  # fmt: skip
    doc = csd_reference(*_csd(tmp_path, rows=rows))
    assert sum(len(line.chunks) for line in doc.lines) == 5


def test_csd_romanized_syllables_pair_with_the_blocks_in_order(tmp_path):
    rows = [(1.0, 1.4, 60, "g_a"), (1.5, 1.9, 62, "n_a"), (2.0, 2.8, 64, "d_a"), (4.0, 4.5, 60, "r_a"), (4.5, 5.25, 59, "m_a")]
    doc = csd_reference(*_csd(tmp_path, rows=rows))
    assert [[c.text for c in line.chunks] for line in doc.lines] == [["가", "나 ", "다"], ["라", "마"]]
    assert doc.lines[0].chunks[2].start_ms == 2000


def test_csd_note_count_must_match_the_blocks(tmp_path):
    with pytest.raises(DatasetError, match="4 sung syllables but the lyrics have 5"):
        csd_reference(*_csd(tmp_path, rows=[(1.0, 1.4, 60, "가"), (1.5, 1.9, 62, "나"), (2.0, 2.8, 64, "다"), (4.0, 4.5, 60, "라")]))


def test_csd_syllables_must_follow_the_lyrics_and_the_error_shows_no_lyric_text(tmp_path):
    with pytest.raises(DatasetError, match="syllable 4") as err:
        csd_reference(*_csd(tmp_path, lyrics="가나 다\n바마\n"))
    assert not any(c in str(err.value) for c in "가나다라마바")


def test_cli_import_ref_reads_csd(tmp_path, capsys):
    from kizami.cli import main

    csv, lyrics = _csd(tmp_path)
    main(["import-ref", "--format", "csd", str(csv), "--lyrics", str(lyrics), "-o", str(tmp_path / "ref.json")])
    assert "5 chunks" in capsys.readouterr().out


def test_cli_import_ref_jamendo_needs_the_word_list(tmp_path):
    from kizami.cli import main

    csv, _, lyrics = _jamendo(tmp_path)
    with pytest.raises(SystemExit):
        main(["import-ref", "--format", "jamendo", str(csv), "--lyrics", str(lyrics), "-o", str(tmp_path / "ref.json")])


@pytest.mark.parametrize(
    "text, name, values",
    [
        ("lag_en=-0.06:-0.02:0.02", "lag_en", [-0.06, -0.04, -0.02]),
        ("m_share=0.45", "m_share", [0.45]),
        ("lag_ko=0,0.01", "lag_ko", [0.0, 0.01]),
        ("m_share=0:0.8:0.3", "m_share", [0.0, 0.3, 0.6]),
        ("lag_en=-0.06:0.04:0.01", "lag_en", [round(-0.06 + i * 0.01, 6) for i in range(11)]),
    ],
)
def test_parse_param(text, name, values):
    got_name, got = parse_param(text)
    assert got_name == name and got == pytest.approx(values)


def test_parse_param_refuses_unknown_names():
    with pytest.raises(ValueError, match="unknown parameter"):
        parse_param("nope=1")
