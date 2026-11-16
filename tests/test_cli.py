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


def test_import_labels_skip_empty_drops_labels_without_text(tmp_path, capsys):
    labels = tmp_path / "labels.txt"
    labels.write_text("1.0\t1.0\t\n1.5\t1.5\tあ\n2.0\t2.0\t\n", encoding="utf-8")
    out = tmp_path / "ref.json"

    code = main(["import-labels", str(labels), "-o", str(out), "--skip-empty"])

    assert code == 0
    assert [c.text for c in load_document(out).lines[0].chunks] == ["あ"]
    printed = capsys.readouterr().out
    assert "1 chunks" in printed
    assert "Dropped 2 labels without text" in printed


def _write_bursts_wav(path, starts_s, total_s=3.0, rate=44100):
    import math
    import struct
    import wave

    samples = []
    for n in range(round(total_s * rate)):
        t = n / rate
        on = any(start <= t < start + 0.3 for start in starts_s)
        value = sum(math.sin(2 * math.pi * 220 * k * t) / k for k in range(1, 6)) if on else 0.0
        samples.append(round(8000 * value))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def _label_times_ms(path):
    return [round(float(line.split("\t")[0]) * 1000) for line in path.read_text().splitlines()]


def test_suggest_labels_writes_one_empty_point_label_per_onset(tmp_path, capsys):
    wav = tmp_path / "vocals.wav"
    _write_bursts_wav(wav, [0.5, 1.5, 2.5])
    out = tmp_path / "candidates.txt"

    code = main(["suggest-labels", str(wav), "-o", str(out)])

    assert code == 0
    times = _label_times_ms(out)
    assert len(times) == 3
    assert all(abs(got - want) <= 10 for got, want in zip(times, [500, 1500, 2500]))
    assert all(line.endswith("\t") for line in out.read_text().splitlines())
    assert "3 candidates" in capsys.readouterr().out


def test_suggest_labels_keeps_only_the_section_between_start_and_end(tmp_path):
    wav = tmp_path / "vocals.wav"
    _write_bursts_wav(wav, [0.5, 1.5, 2.5])
    out = tmp_path / "candidates.txt"

    code = main(["suggest-labels", str(wav), "-o", str(out), "--start", "1.0", "--end", "2.0"])

    assert code == 0
    times = _label_times_ms(out)
    assert len(times) == 1
    assert abs(times[0] - 1500) <= 10


def test_suggest_labels_reports_a_file_that_is_not_wav(tmp_path, capsys):
    bad = tmp_path / "vocals.wav"
    bad.write_bytes(b"not audio")
    out = tmp_path / "candidates.txt"

    code = main(["suggest-labels", str(bad), "-o", str(out)])

    assert code == 2
    assert "WAV" in capsys.readouterr().err
    assert not out.exists()


def test_suggest_labels_rejects_end_before_start(tmp_path, capsys):
    wav = tmp_path / "vocals.wav"
    _write_bursts_wav(wav, [0.5], total_s=1.0)

    code = main(["suggest-labels", str(wav), "-o", str(tmp_path / "c.txt"), "--start", "2", "--end", "1"])

    assert code == 2
    assert "--end" in capsys.readouterr().err


def test_suggested_labels_convert_after_text_is_added(tmp_path):
    wav = tmp_path / "vocals.wav"
    _write_bursts_wav(wav, [0.5, 1.5])
    candidates = tmp_path / "candidates.txt"
    main(["suggest-labels", str(wav), "-o", str(candidates)])
    lines = candidates.read_text().splitlines()
    labels = tmp_path / "labels.txt"
    labels.write_text(lines[0] + "\n" + lines[1] + "うた\n", encoding="utf-8")
    out = tmp_path / "ref.json"

    code = main(["import-labels", str(labels), "-o", str(out), "--skip-empty"])

    assert code == 0
    chunks = load_document(out).lines[0].chunks
    assert [c.text for c in chunks] == ["うた"]
    assert abs(chunks[0].start_ms - 1500) <= 10
