import json
import wave
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from kizami.cli import main
from kizami.timing import Chunk, Line, TimingDocument, save_document
from kizami.ttml import (
    LAST_CHUNK_MS,
    NS_AMLL,
    NS_ITUNES,
    NS_TT,
    NS_TTM,
    TtmlError,
    document_from_tagger_project,
    document_to_ttml,
    format_time,
    load_for_export,
    render_ttml,
)

TT = f"{{{NS_TT}}}"
TTM = f"{{{NS_TTM}}}"
ITUNES = f"{{{NS_ITUNES}}}"
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"


def doc_of(*lines):
    return TimingDocument(lines=tuple(Line(chunks=tuple(c), agent=a) for c, a in lines))


def spans(text):
    root = ET.fromstring(text)
    return [
        [(s.text, s.get("begin"), s.get("end")) for s in p.iter(f"{TT}span")]
        for p in root.iter(f"{TT}p")
    ]


def test_format_time_uses_hours_minutes_seconds_and_milliseconds():
    assert format_time(0) == "00:00:00.000"
    assert format_time(83_456) == "00:01:23.456"
    assert format_time(3_723_004) == "01:02:03.004"
    assert format_time(-5) == "00:00:00.000"


def test_each_line_is_a_p_and_each_chunk_a_span_with_its_own_times():
    doc = doc_of(
        ([Chunk("あの", 12346), Chunk("そらを", 12801, 13400), Chunk("みあげて", 13620, 14500)], None),
        ([Chunk("ねがい", 16000, 17000)], None),
    )

    text = document_to_ttml(doc)
    root = ET.fromstring(text)

    assert root.tag == f"{TT}tt"
    assert root.get(f"{ITUNES}timing") == "Word"
    assert root.get("{http://www.w3.org/XML/1998/namespace}lang") == "ja"
    assert spans(text) == [
        [
            ("あの", "00:00:12.346", "00:00:12.801"),  # no end: the next chunk's start
            ("そらを", "00:00:12.801", "00:00:13.400"),
            ("みあげて", "00:00:13.620", "00:00:14.500"),
        ],
        [("ねがい", "00:00:16.000", "00:00:17.000")],
    ]
    ps = list(root.iter(f"{TT}p"))
    assert [p.get(f"{ITUNES}key") for p in ps] == ["L1", "L2"]
    assert [p.get(f"{TTM}agent") for p in ps] == ["v1", "v1"]
    assert (ps[0].get("begin"), ps[0].get("end")) == ("00:00:12.346", "00:00:14.500")
    body = root.find(f"{TT}body")
    assert body.get("dur") == "00:00:17.000"
    div = body.find(f"{TT}div")
    assert (div.get("begin"), div.get("end")) == ("00:00:12.346", "00:00:17.000")


def line_texts(text):
    """The text of each line as a player shows it (spans and the spaces between them)."""
    return ["".join(p.itertext()) for p in ET.fromstring(text).iter(f"{TT}p")]


def test_every_chunk_is_followed_by_a_space_between_the_spans_unless_turned_off():
    doc = doc_of(([Chunk("あ", 0, 100), Chunk("い", 100, 200), Chunk("う", 200, 300)], None))

    spaced = document_to_ttml(doc)
    plain = document_to_ttml(doc, chunk_spaces=False)

    assert '</span> <span begin="00:00:00.100"' in spaced
    assert line_texts(spaced) == ["あ い う"]  # none at the end of the line
    assert "</span><span" in plain and line_texts(plain) == ["あいう"]


def test_the_last_chunk_of_a_line_without_an_end_is_cut_at_the_next_line():
    doc = doc_of(([Chunk("あ", 1000)], None), ([Chunk("い", 1600)], None), ([Chunk("う", 5000)], None))

    out = spans(document_to_ttml(doc))

    assert out[0][0][2] == "00:00:01.600"  # next line starts first
    assert out[1][0][2] == format_time(1600 + LAST_CHUNK_MS)
    assert out[2][0][2] == format_time(5000 + LAST_CHUNK_MS)


def test_an_end_after_the_next_start_of_the_same_line_is_cut_back():
    out = spans(document_to_ttml(doc_of(([Chunk("あ", 1000, 1900), Chunk("い", 1500, 2000)], None))))
    assert out[0][0] == ("あ", "00:00:01.000", "00:00:01.500")


def test_display_offset_moves_every_time_and_stops_at_zero():
    doc = doc_of(([Chunk("あ", 30, 400), Chunk("い", 400, 900)], None))

    later = spans(document_to_ttml(doc, offset_ms=120))
    earlier = spans(document_to_ttml(doc, offset_ms=-100))

    assert later[0] == [("あ", "00:00:00.150", "00:00:00.520"), ("い", "00:00:00.520", "00:00:01.020")]
    assert earlier[0] == [("あ", "00:00:00.000", "00:00:00.300"), ("い", "00:00:00.300", "00:00:00.800")]


@pytest.mark.parametrize("offset", [10001, -10001, 1.5, True])
def test_display_offset_must_be_a_whole_number_in_range(offset):
    with pytest.raises(TtmlError):
        document_to_ttml(doc_of(([Chunk("あ", 0, 10)], None)), offset_ms=offset)


def test_latin_words_keep_a_space_between_them_but_japanese_does_not():
    doc = doc_of(([Chunk("I", 0), Chunk("love", 200), Chunk("you,", 400), Chunk("baby", 600), Chunk("ねえ", 800, 900)], None))

    text = document_to_ttml(doc, chunk_spaces=False)

    assert [t for t, _, _ in spans(text)[0]] == ["I", "love", "you,", "baby", "ねえ"]
    assert line_texts(text) == ["I love you, babyねえ"]


def test_a_space_written_in_the_lyrics_is_kept_after_its_span():
    doc = doc_of(([Chunk("ねえ\u3000", 0), Chunk("きみ ", 200), Chunk("Hi", 400, 500)], None))

    text = document_to_ttml(doc, chunk_spaces=False)

    assert [t for t, _, _ in spans(text)[0]] == ["ねえ", "きみ", "Hi"]
    assert line_texts(text) == ["ねえ きみ Hi"]


def test_text_is_escaped():
    doc = doc_of(([Chunk("<&>", 0, 10)], None))
    assert spans(document_to_ttml(doc, title='A "B" & C'))[0][0][0] == "<&>"


def test_agents_and_song_details_are_in_the_head():
    doc = doc_of(([Chunk("あ", 0, 10)], None), ([Chunk("い", 20, 30)], "B"), ([Chunk("う", 40, 50)], None))

    root = ET.fromstring(document_to_ttml(doc, title="Song", artists=["X", "Y"], album="Album"))

    agents = root.findall(f".//{TTM}agent")
    assert [(a.get("type"), a.get(XML_ID)) for a in agents] == [("person", "v1"), ("person", "v2")]
    assert agents[1].find(f"{TTM}name").text == "B"
    assert [p.get(f"{TTM}agent") for p in root.iter(f"{TT}p")] == ["v1", "v2", "v1"]
    meta = [(m.get("key"), m.get("value")) for m in root.iter(f"{{{NS_AMLL}}}meta")]
    assert meta == [("musicName", "Song"), ("artists", "X"), ("artists", "Y"), ("album", "Album")]


def test_tagger_project_gives_its_timed_chunks_with_lines_and_agents():
    project = {
        "format": "kizami-tagger",
        "version": 1,
        "lines": [
            {"units": [{"text": "よ", "start_ms": 1000, "end_ms": 1200}, {"text": "ぞら", "start_ms": 1300, "end_ms": None}]},
            {"units": [{"text": "まだ", "start_ms": None}]},
            {"agent": "B", "units": [{"text": "を", "start_ms": 2000, "uncertain": True}]},
        ],
    }

    doc = document_from_tagger_project(project)

    assert [line.agent for line in doc.lines] == [None, "B"]
    assert list(doc.lines[0].chunks) == [Chunk("よ", 1000, 1200), Chunk("ぞら", 1300)]
    assert doc.lines[1].chunks == (Chunk("を", 2000, uncertain=True),)


def test_tagger_project_without_timed_chunks_is_refused(tmp_path):
    path = tmp_path / "tagger.json"
    path.write_text(json.dumps({"format": "kizami-tagger", "lines": [{"units": [{"text": "あ"}]}]}), encoding="utf-8")
    with pytest.raises(TtmlError, match="no timed chunks"):
        load_for_export(path)


def write_wav(path, samples, rate=16000):
    data = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data)


def tone(rate, seconds, *parts):
    """Silence with a sine tone in each (start, stop) part."""
    x = np.zeros(int(rate * seconds))
    t = np.arange(len(x)) / rate
    for a, b in parts:
        sl = slice(int(a * rate), int(b * rate))
        x[sl] = 0.5 * np.sin(2 * np.pi * 220 * t[sl])
    return x


def test_cli_writes_ttml_with_ends_from_the_vocal_track_next_to_the_input(tmp_path, capsys):
    folder = tmp_path / "song"
    folder.mkdir()
    write_wav(folder / "vocals.wav", tone(16000, 4.0, (0.5, 1.2), (1.5, 1.9), (3.0, 3.4)))
    timing = folder / "auto_chu.json"
    save_document(doc_of(([Chunk("あ", 500), Chunk("い", 1500)], None), ([Chunk("う", 3000)], None)), timing)

    code = main(["ttml", str(timing), "--title", "Demo"])

    assert code == 0
    out = spans((folder / "auto_chu.ttml").read_text(encoding="utf-8"))
    ends = [[int(round(_sec(e) * 1000)) for _, _, e in line] for line in out]
    assert abs(ends[0][0] - 1200) <= 30  # the voice stops before the next chunk starts
    assert abs(ends[0][1] - 1900) <= 30
    assert abs(ends[1][0] - 3400) <= 30
    assert "ends from vocals.wav" in capsys.readouterr().out


def test_cli_without_audio_ends_chunks_at_the_next_start(tmp_path, capsys):
    timing = tmp_path / "t.json"
    save_document(doc_of(([Chunk("あ", 500), Chunk("い", 1500)], None)), timing)
    out = tmp_path / "x.ttml"

    assert main(["ttml", str(timing), "-o", str(out), "--offset-ms", "-100"]) == 0

    assert spans(out.read_text(encoding="utf-8"))[0][0] == ("あ", "00:00:00.400", "00:00:01.400")
    assert "no audio" in capsys.readouterr().out


def test_render_counts_lines_and_chunks():
    doc = doc_of(([Chunk("あ", 0), Chunk("い", 100, 200)], None), ([Chunk("う", 300, 400)], None))
    out = render_ttml(doc)
    assert (out.lines, out.chunks) == (2, 3)
    assert out.text == document_to_ttml(doc)


def test_cli_no_chunk_spaces_writes_the_spans_without_spaces(tmp_path):
    timing = tmp_path / "t.json"
    save_document(doc_of(([Chunk("夜空", 0), Chunk("に", 500, 900)], None)), timing)

    assert main(["ttml", str(timing), "--no-audio"]) == 0
    assert line_texts((tmp_path / "t.ttml").read_text(encoding="utf-8")) == ["夜空 に"]
    assert main(["ttml", str(timing), "--no-audio", "--no-chunk-spaces"]) == 0
    assert line_texts((tmp_path / "t.ttml").read_text(encoding="utf-8")) == ["夜空に"]


def test_cli_reports_a_bad_input(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    assert main(["ttml", str(bad)]) == 2
    assert "error:" in capsys.readouterr().err


def _sec(value):
    h, m, s = value.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _english(meta):
    chunks = [Chunk("beau", 0), Chunk("ti", 200), Chunk("ful ", 400), Chunk("ba", 600), Chunk("by", 800, 900)]
    return TimingDocument(lines=(Line(chunks=tuple(chunks)),), meta=meta)


def test_no_space_inside_an_english_word_when_chunk_spaces_are_off():
    text = document_to_ttml(_english({"source": "kizami generate"}), chunk_spaces=False)
    assert line_texts(text) == ["beautiful baby"]


def test_space_after_every_chunk_when_chunk_spaces_are_on():
    text = document_to_ttml(_english({"source": "kizami generate"}))
    assert line_texts(text) == ["beau ti ful ba by"]


def test_a_tagger_file_with_written_spaces_keeps_words_whole():
    text = document_to_ttml(_english({"source": "kizami tagger"}), chunk_spaces=False)
    assert line_texts(text) == ["beautiful baby"]


def test_lang_comes_from_the_document():
    doc = doc_of(([Chunk("사", 0), Chunk("랑", 200, 300)], None))
    ko = TimingDocument(lines=doc.lines, meta={"languages": {"en": 4, "ko": 9}})
    assert 'xml:lang="ko"' in document_to_ttml(ko)
    assert 'xml:lang="en"' in document_to_ttml(ko, lang="en")
    assert 'xml:lang="ko"' in document_to_ttml(doc)  # no languages in meta: from the letters


def test_lang_without_meta_comes_from_the_letters_of_the_chunks():
    ja = doc_of(([Chunk("夜", 0), Chunk("空 ", 200), Chunk("baby", 400, 600)], None))
    assert 'xml:lang="ja"' in document_to_ttml(ja)
    en = doc_of(([Chunk("I ", 0), Chunk("love ", 200), Chunk("사", 400, 600)], None))
    assert 'xml:lang="en"' in document_to_ttml(en)
    es = TimingDocument(lines=en.lines, meta={"latin": "es"})
    assert 'xml:lang="es"' in document_to_ttml(es)
    guessed = doc_of(([Chunk("te ", 0), Chunk("quie", 200), Chunk("ro ", 300), Chunk("mi ", 400), Chunk("a", 500), Chunk("mor", 600, 800)], None))
    assert 'xml:lang="es"' in document_to_ttml(guessed)
    digits = doc_of(([Chunk("1 ", 0), Chunk("2", 200, 400)], None))
    assert 'xml:lang="ja"' in document_to_ttml(digits)


def test_a_tagger_project_of_a_korean_song_is_korean():
    project = {
        "format": "kizami-tagger",
        "version": 1,
        "lines": [{"units": [{"text": "사", "start_ms": 0}, {"text": "랑", "start_ms": 200, "end_ms": 400}]}],
    }
    assert 'xml:lang="ko"' in document_to_ttml(document_from_tagger_project(project))


def test_cli_ttml_lang_defaults_to_the_document():
    from kizami.cli import _build_parser

    assert _build_parser().parse_args(["ttml", "x.json"]).lang is None
