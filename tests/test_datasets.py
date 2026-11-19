import pytest

from kizami.datasets import DatasetError, jamendo_reference
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


@pytest.mark.parametrize(
    "text, name, values",
    [
        ("lag_en=-0.06:-0.02:0.02", "lag_en", [-0.06, -0.04, -0.02]),
        ("m_share=0.45", "m_share", [0.45]),
        ("lag_ko=0,0.01", "lag_ko", [0.0, 0.01]),
    ],
)
def test_parse_param(text, name, values):
    got_name, got = parse_param(text)
    assert got_name == name and got == pytest.approx(values)


def test_parse_param_refuses_unknown_names():
    with pytest.raises(ValueError, match="unknown parameter"):
        parse_param("nope=1")
