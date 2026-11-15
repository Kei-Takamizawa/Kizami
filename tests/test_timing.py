import json

import pytest

from kizami.timing import (
    Chunk,
    Line,
    TimingDocument,
    TimingFormatError,
    document_from_dict,
    document_to_dict,
    load_document,
    normalize_text,
    save_document,
)


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("夜空を", "夜空を"),
        ("夜空 を　駆ける", "夜空を駆ける"),  # ASCII and full-width spaces
        ("「ねえ、」", "ねえ"),  # Japanese punctuation
        ("ＬＯＶＥ！", "love"),  # full-width Latin and punctuation
        ("Hello, World!", "helloworld"),
        ("ｶﾞ", "ガ"),  # half-width kana with voiced mark
        ("スーパー", "スーパー"),  # long vowel mark is a letter, not a symbol
        ("時々", "時々"),  # iteration mark is kept
        ("♪〜", ""),  # symbols only
    ],
)
def test_normalize_text_keeps_only_letters_marks_and_digits(raw, want):
    assert normalize_text(raw) == want


def _minimal_dict():
    return {
        "format": "kizami-timing",
        "version": 1,
        "kind": "reference",
        "meta": {"title": "test"},
        "lines": [
            {
                "agent": "A",
                "chunks": [
                    {"text": "夜空を", "start_ms": 1000, "end_ms": 1400},
                    {"text": "駆ける", "start_ms": 1500, "uncertain": True},
                ],
            }
        ],
    }


def test_document_from_dict_reads_all_fields():
    doc = document_from_dict(_minimal_dict())

    assert doc == TimingDocument(
        kind="reference",
        meta={"title": "test"},
        lines=(
            Line(
                agent="A",
                chunks=(
                    Chunk(text="夜空を", start_ms=1000, end_ms=1400),
                    Chunk(text="駆ける", start_ms=1500, end_ms=None, uncertain=True),
                ),
            ),
        ),
    )


def test_document_to_dict_omits_unset_optional_fields():
    doc = TimingDocument(
        kind="generated",
        lines=(Line(chunks=(Chunk(text="あ", start_ms=0),)),),
    )

    assert document_to_dict(doc) == {
        "format": "kizami-timing",
        "version": 1,
        "kind": "generated",
        "meta": {},
        "lines": [{"chunks": [{"text": "あ", "start_ms": 0}]}],
    }


def test_save_then_load_returns_equal_document(tmp_path):
    doc = document_from_dict(_minimal_dict())
    path = tmp_path / "doc.json"

    save_document(doc, path)

    assert load_document(path) == doc
    # Japanese text must stay readable in the file, not \u escapes.
    assert "夜空を" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("mutate", "message_part"),
    [
        (lambda d: d.update(format="other"), "format"),
        (lambda d: d.update(version=2), "version"),
        (lambda d: d.update(kind="draft"), "kind"),
        (lambda d: d.update(lines=[]), "lines"),
        (lambda d: d["lines"][0].update(chunks=[]), "lines[0].chunks"),
        (lambda d: d["lines"][0]["chunks"][0].update(text=""), "lines[0].chunks[0].text"),
        (
            lambda d: d["lines"][0]["chunks"][1].update(start_ms=-1),
            "lines[0].chunks[1].start_ms",
        ),
        (
            lambda d: d["lines"][0]["chunks"][0].update(start_ms=1.5),
            "lines[0].chunks[0].start_ms",
        ),
        (
            lambda d: d["lines"][0]["chunks"][0].update(start_ms=True),
            "lines[0].chunks[0].start_ms",
        ),
        (
            lambda d: d["lines"][0]["chunks"][0].update(end_ms="1400"),
            "lines[0].chunks[0].end_ms",
        ),
        (
            lambda d: d["lines"][0]["chunks"][0].update(uncertain="yes"),
            "lines[0].chunks[0].uncertain",
        ),
    ],
)
def test_document_from_dict_rejects_invalid_input_and_names_the_field(mutate, message_part):
    data = _minimal_dict()
    mutate(data)

    with pytest.raises(TimingFormatError) as excinfo:
        document_from_dict(data)

    assert message_part in str(excinfo.value)


def test_document_from_dict_accepts_end_before_start_so_evaluation_can_report_it():
    data = _minimal_dict()
    data["lines"][0]["chunks"][0].update(start_ms=1000, end_ms=900)

    doc = document_from_dict(data)

    assert doc.lines[0].chunks[0].end_ms == 900


def test_load_document_reports_invalid_json_as_format_error(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(TimingFormatError):
        load_document(path)


def test_load_document_rejects_non_object_root(tmp_path):
    path = tmp_path / "list.json"
    path.write_text(json.dumps([1, 2]), encoding="utf-8")

    with pytest.raises(TimingFormatError):
        load_document(path)


def test_document_from_dict_ignores_unknown_fields():
    data = _minimal_dict()
    data["future_field"] = 1
    data["lines"][0]["chunks"][0]["reading"] = "よぞらを"

    doc = document_from_dict(data)

    assert doc.lines[0].chunks[0].text == "夜空を"
