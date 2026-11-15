import json

from kizami.cli import main
from kizami.timing import Chunk, Line, TimingDocument, load_document, save_document

LABELS = "1.000000\t1.000000\t夜空を\n1.500000\t1.900000\t?駆ける\n"


def _write_doc(path, chunks, kind="generated"):
    doc = TimingDocument(kind=kind, lines=(Line(chunks=tuple(Chunk(*c) for c in chunks)),))
    save_document(doc, path)
    return path


def test_import_labels_writes_a_reference_document(tmp_path, capsys):
    labels = tmp_path / "labels.txt"
    labels.write_text(LABELS, encoding="utf-8")
    out = tmp_path / "ref.json"

    code = main(["import-labels", str(labels), "-o", str(out), "--title", "demo"])

    assert code == 0
    doc = load_document(out)
    assert doc.kind == "reference"
    assert doc.meta["title"] == "demo"
    assert list(doc.lines[0].chunks) == [
        Chunk(text="夜空を", start_ms=1000),
        Chunk(text="駆ける", start_ms=1500, end_ms=1900, uncertain=True),
    ]
    assert "2 chunks" in capsys.readouterr().out


def test_import_labels_reports_bad_input_without_writing_output(tmp_path, capsys):
    labels = tmp_path / "labels.txt"
    labels.write_text("abc\t1.0\tあ\n", encoding="utf-8")
    out = tmp_path / "ref.json"

    code = main(["import-labels", str(labels), "-o", str(out)])

    assert code == 2
    assert "line 1" in capsys.readouterr().err
    assert not out.exists()


def test_eval_prints_report_for_a_pair(tmp_path, capsys):
    ref = _write_doc(tmp_path / "ref.json", [("あ", 1000), ("い", 2000)], kind="reference")
    hyp = _write_doc(tmp_path / "hyp.json", [("あ", 1010), ("い", 2300)])

    code = main(["eval", str(ref), str(hyp)])

    out = capsys.readouterr().out
    assert code == 0
    assert "50.0% (1/2)" in out
    assert "FAIL" in out


def test_eval_options_change_tolerance_and_targets(tmp_path, capsys):
    ref = _write_doc(tmp_path / "ref.json", [("あ", 1000), ("い", 2000)], kind="reference")
    hyp = _write_doc(tmp_path / "hyp.json", [("あ", 1010), ("い", 2300)])

    code = main(
        [
            "eval",
            str(ref),
            str(hyp),
            "--tolerance-ms",
            "300",
            "--target-recall",
            "0.9",
            "--target-precision",
            "0.9",
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "100.0% (2/2)" in out
    assert "FAIL" not in out


def test_eval_json_output_has_each_pair_and_the_pooled_total(tmp_path, capsys):
    ref_a = _write_doc(tmp_path / "a_ref.json", [("あ", 1000)], kind="reference")
    hyp_a = _write_doc(tmp_path / "a_hyp.json", [("あ", 1000)])
    ref_b = _write_doc(tmp_path / "b_ref.json", [("か", 1000)], kind="reference")
    hyp_b = _write_doc(tmp_path / "b_hyp.json", [("か", 1500)])

    code = main(["eval", str(ref_a), str(hyp_a), str(ref_b), str(hyp_b), "--json"])

    data = json.loads(capsys.readouterr().out)
    assert code == 0
    assert [pair["recall"] for pair in data["pairs"]] == [1.0, 0.0]
    assert data["pairs"][0]["reference"] == str(ref_a)
    assert data["total"]["recall"] == 0.5
    assert data["total"]["ref_count"] == 2


def test_eval_needs_files_in_pairs(tmp_path, capsys):
    ref = _write_doc(tmp_path / "ref.json", [("あ", 1000)], kind="reference")

    code = main(["eval", str(ref)])

    assert code == 2
    assert "pairs" in capsys.readouterr().err


def test_eval_reports_text_mismatch_as_an_error(tmp_path, capsys):
    ref = _write_doc(tmp_path / "ref.json", [("翔ける", 1000)], kind="reference")
    hyp = _write_doc(tmp_path / "hyp.json", [("駆ける", 1000)])

    code = main(["eval", str(ref), str(hyp)])

    err = capsys.readouterr().err
    assert code == 2
    assert "ref.json" in err
    assert "not found" in err


def test_eval_reports_missing_file_as_an_error(tmp_path, capsys):
    code = main(["eval", str(tmp_path / "none.json"), str(tmp_path / "none2.json")])

    assert code == 2
    assert "none.json" in capsys.readouterr().err


def test_labels_compared_with_themselves_agree_fully(tmp_path, capsys):
    labels = tmp_path / "labels.txt"
    labels.write_text(LABELS, encoding="utf-8")
    first = tmp_path / "pass1.json"
    second = tmp_path / "pass2.json"
    main(["import-labels", str(labels), "-o", str(first)])
    main(["import-labels", str(labels), "-o", str(second)])
    capsys.readouterr()

    code = main(["eval", str(first), str(second)])

    assert code == 0
    assert "100.0% (2/2)" in capsys.readouterr().out
