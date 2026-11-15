import pytest

from kizami.audacity import AudacityLabelError, document_from_label_text, load_labels
from kizami.timing import Chunk


def _chunks(text):
    doc = document_from_label_text(text)
    assert len(doc.lines) == 1
    return list(doc.lines[0].chunks)


def test_point_label_becomes_chunk_without_end_time():
    assert _chunks("12.345600\t12.345600\t夜空を\n") == [
        Chunk(text="夜空を", start_ms=12346, end_ms=None)
    ]


def test_region_label_becomes_chunk_with_end_time():
    assert _chunks("1.000000\t1.450000\t駆ける\n") == [
        Chunk(text="駆ける", start_ms=1000, end_ms=1450)
    ]


def test_document_is_marked_as_reference_and_keeps_meta():
    doc = document_from_label_text("1.0\t1.0\tあ\n", meta={"title": "demo"})

    assert doc.kind == "reference"
    assert doc.meta == {"title": "demo"}


def test_labels_are_sorted_by_start_time():
    text = "2.000000\t2.000000\tに\n1.000000\t1.000000\tいち\n"

    assert [c.text for c in _chunks(text)] == ["いち", "に"]


def test_spectral_range_lines_and_blank_lines_are_skipped():
    text = (
        "1.000000\t1.000000\tあ\n"
        "\\\t200.000000\t4000.000000\n"
        "\n"
        "2.000000\t2.000000\tい\n"
    )

    assert [c.text for c in _chunks(text)] == ["あ", "い"]


def test_windows_line_endings_and_byte_order_mark_are_accepted():
    text = "﻿1.000000\t1.000000\tあ\r\n2.000000\t2.000000\tい\r\n"

    assert [(c.text, c.start_ms) for c in _chunks(text)] == [("あ", 1000), ("い", 2000)]


@pytest.mark.parametrize("marker", ["?", "？"])
def test_leading_question_mark_flags_chunk_as_uncertain(marker):
    assert _chunks(f"1.000000\t1.000000\t{marker}を\n") == [
        Chunk(text="を", start_ms=1000, end_ms=None, uncertain=True)
    ]


def test_question_mark_inside_text_is_kept_as_text():
    assert _chunks("1.000000\t1.000000\tなぜ?\n") == [
        Chunk(text="なぜ?", start_ms=1000, end_ms=None)
    ]


def test_surrounding_spaces_in_label_text_are_removed():
    assert _chunks("1.000000\t1.000000\t  夜空を \n")[0].text == "夜空を"


@pytest.mark.parametrize(
    ("text", "message_part"),
    [
        ("1.000000\t1.000000\t\n", "line 1"),  # empty text
        ("1.000000\t1.000000\n", "line 1"),  # text column missing
        ("1.000000\t1.000000\tあ\nabc\t2.000000\tい\n", "line 2"),  # not a number
        ("-0.500000\t-0.500000\tあ\n", "line 1"),  # negative time
        ("2.000000\t1.000000\tあ\n", "line 1"),  # end before start
        ("1.000000\t1.000000\t?\n", "line 1"),  # marker with no text
        ("1.000000\t1.000000\tあ\n1.000400\t1.000400\tい\n", "line 2"),  # same millisecond
        ("", "no labels"),
        ("\\\t200.0\t4000.0\n", "no labels"),
    ],
)
def test_invalid_label_files_are_rejected_with_location(text, message_part):
    with pytest.raises(AudacityLabelError) as excinfo:
        document_from_label_text(text)

    assert message_part in str(excinfo.value)


def test_load_labels_reads_utf8_file(tmp_path):
    path = tmp_path / "labels.txt"
    path.write_bytes("﻿1.000000\t1.250000\t星の\n".encode("utf-8"))

    doc = load_labels(path)

    assert list(doc.lines[0].chunks) == [Chunk(text="星の", start_ms=1000, end_ms=1250)]
