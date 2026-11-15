import pytest

from kizami.evaluate import (
    EvaluationError,
    combine_results,
    evaluate_pair,
    format_report,
    timing_stats,
    wilson_interval,
)
from kizami.timing import Chunk, Line, TimingDocument


def _doc(*lines, kind="generated"):
    """Build a document. Each line is a list of (text, start_ms[, end_ms[, uncertain]])."""
    return TimingDocument(
        kind=kind,
        lines=tuple(Line(chunks=tuple(Chunk(*chunk) for chunk in line)) for line in lines),
    )


def _ref(*lines):
    return _doc(*lines, kind="reference")


def test_identical_timing_gives_full_recall_and_precision():
    ref = _ref([("夜空を", 1000), ("駆ける", 1500)])
    hyp = _doc([("夜空を", 1000), ("駆ける", 1500)])

    result = evaluate_pair(ref, hyp)

    assert (result.ref_count, result.hyp_count, result.hit_count) == (2, 2, 2)
    assert result.recall == 1.0
    assert result.precision == 1.0


@pytest.mark.parametrize(
    ("shift_ms", "want_hits"),
    [(50, 1), (-50, 1), (51, 0), (-51, 0)],
)
def test_boundary_counts_as_hit_only_within_tolerance(shift_ms, want_hits):
    ref = _ref([("あ", 1000)])
    hyp = _doc([("あ", 1000 + shift_ms)])

    assert evaluate_pair(ref, hyp, tolerance_ms=50).hit_count == want_hits


def test_tolerance_can_be_changed():
    ref = _ref([("あ", 1000)])
    hyp = _doc([("あ", 1080)])

    assert evaluate_pair(ref, hyp, tolerance_ms=100).hit_count == 1


def test_missing_split_lowers_recall_only():
    ref = _ref([("夜空", 1000), ("を", 1300), ("駆ける", 1500)])
    hyp = _doc([("夜空を", 1000), ("駆ける", 1500)])

    result = evaluate_pair(ref, hyp)

    assert result.recall == pytest.approx(2 / 3)
    assert result.precision == 1.0
    assert [(b.text, b.start_ms) for b in result.missed] == [("を", 1300)]
    assert result.extra == ()


def test_extra_split_lowers_precision_only():
    ref = _ref([("夜空を", 1000), ("駆ける", 1500)])
    hyp = _doc([("夜空", 1000), ("を", 1300), ("駆ける", 1500)])

    result = evaluate_pair(ref, hyp)

    assert result.recall == 1.0
    assert result.precision == pytest.approx(2 / 3)
    assert [(b.text, b.start_ms) for b in result.extra] == [("を", 1300)]
    assert result.missed == ()


def test_right_position_at_wrong_time_lowers_both_recall_and_precision():
    ref = _ref([("あ", 1000), ("い", 2000)])
    hyp = _doc([("あ", 1000), ("い", 2120)])

    result = evaluate_pair(ref, hyp)

    assert result.recall == 0.5
    assert result.precision == 0.5
    assert [m.error_ms for m in result.matched] == [0, 120]


def test_boundary_at_right_time_but_wrong_text_position_is_not_a_hit():
    # The generated file lights up "いう" at the moment "う" is sung.
    ref = _ref([("あい", 1000), ("う", 2000)])
    hyp = _doc([("あ", 1000), ("いう", 2000)])

    result = evaluate_pair(ref, hyp)

    assert result.hit_count == 1
    assert [b.text for b in result.missed] == ["う"]
    assert [b.text for b in result.extra] == ["いう"]


def test_spacing_punctuation_and_width_differences_do_not_break_matching():
    ref = _ref([("ねえ", 1000), ("LOVE", 2000)])
    hyp = _doc([("「ねえ、」", 1010), (" ｌｏｖｅ！", 1990)])

    assert evaluate_pair(ref, hyp).hit_count == 2


def test_chunk_without_letters_is_not_counted_as_a_boundary():
    ref = _ref([("あ", 1000)])
    hyp = _doc([("♪", 500), ("あ", 1000)])

    result = evaluate_pair(ref, hyp)

    assert result.hyp_count == 1
    assert result.precision == 1.0


def test_reference_excerpt_is_found_inside_a_full_song():
    ref = _ref([("さび", 5000), ("です", 5400)])
    hyp = _doc(
        [("いんとろ", 1000)],
        [("さび", 5010), ("です", 5390)],
        [("あうとろ", 9000)],
    )

    result = evaluate_pair(ref, hyp)

    assert (result.ref_count, result.hyp_count, result.hit_count) == (2, 2, 2)


def test_repeated_text_is_matched_to_the_occurrence_nearest_in_time():
    ref = _ref([("あい", 10010), ("うえ", 10480)])
    hyp = _doc(
        [("あい", 0), ("うえ", 500)],
        [("かき", 1000)],
        [("あい", 10000), ("うえ", 10500)],
    )

    result = evaluate_pair(ref, hyp)

    assert result.hit_count == 2
    assert [m.error_ms for m in result.matched] == [-10, 20]


def test_generated_chunk_that_starts_before_the_excerpt_is_not_counted():
    ref = _ref([("あ", 1000), ("い", 1500)])
    hyp = _doc([("かあ", 900), ("い", 1500)])

    result = evaluate_pair(ref, hyp)

    assert (result.ref_count, result.hyp_count, result.hit_count) == (2, 1, 1)
    assert [b.text for b in result.missed] == ["あ"]


def test_reference_text_missing_from_generated_text_is_an_error():
    ref = _ref([("夜空を", 1000), ("翔ける", 1500)])
    hyp = _doc([("夜空を", 1000), ("駆ける", 1500)])

    with pytest.raises(EvaluationError) as excinfo:
        evaluate_pair(ref, hyp)

    # The message shows where the two texts stop agreeing.
    assert "夜空を" in str(excinfo.value)


def test_reference_without_any_letters_is_an_error():
    with pytest.raises(EvaluationError):
        evaluate_pair(_ref([("♪", 1000)]), _doc([("あ", 1000)]))


def test_uncertain_reference_boundaries_can_be_excluded_from_recall():
    ref = _ref([("あ", 1000), ("い", 2000, None, True), ("う", 3000)])
    hyp = _doc([("あ", 1000), ("い", 2200), ("う", 3000)])

    result = evaluate_pair(ref, hyp)

    assert result.recall == pytest.approx(2 / 3)
    assert (result.certain_ref_count, result.certain_hit_count) == (2, 2)
    assert result.recall_excluding_uncertain == 1.0


def test_end_time_checks_count_problems_in_generated_chunks():
    ref = _ref([("あ", 1000), ("い", 1400), ("う", 2000)])
    hyp = _doc([("あ", 1000, 1500), ("い", 1400, 1400), ("う", 2000)])

    checks = evaluate_pair(ref, hyp).end_time_checks

    assert checks.missing_end == 1  # う
    assert checks.non_positive_duration == 1  # い
    assert checks.overlaps == 1  # あ ends 100 ms after い starts
    assert checks.max_overlap_ms == 100


def test_overlap_between_different_lines_is_not_reported():
    ref = _ref([("あ", 1000), ("い", 2000)])
    hyp = _doc([("あ", 1000, 3000)], [("い", 2000, 2500)])

    checks = evaluate_pair(ref, hyp).end_time_checks

    assert checks.overlaps == 0


def test_timing_stats_use_generated_minus_reference():
    stats = timing_stats([-10, 20, 30, 200])

    assert stats.count == 4
    assert stats.median_ms == 25
    assert stats.mean_ms == 60
    assert stats.median_abs_ms == 25
    assert stats.p90_abs_ms == 200


def test_timing_stats_of_nothing_is_none():
    assert timing_stats([]) is None


def test_combined_result_pools_counts_across_songs():
    first = evaluate_pair(_ref([("あ", 1000), ("い", 2000)]), _doc([("あ", 1000), ("い", 2000)]))
    second = evaluate_pair(
        _ref([("か", 1000), ("き", 2000)]),
        _doc([("か", 1000), ("き", 2300), ("く", 3000)]),
    )

    total = combine_results([first, second])

    assert (total.ref_count, total.hyp_count, total.hit_count) == (4, 4, 3)
    assert total.recall == 0.75
    assert total.precision == 0.75
    assert sorted(m.error_ms for m in total.matched) == [0, 0, 0, 300]


def test_wilson_interval_for_85_of_100():
    low, high = wilson_interval(85, 100)

    assert low == pytest.approx(0.767, abs=0.002)
    assert high == pytest.approx(0.907, abs=0.002)


def test_wilson_interval_without_data_is_none():
    assert wilson_interval(0, 0) is None


def test_report_marks_each_target_as_pass_or_fail():
    ref = _ref([("あ", 1000), ("い", 2000), ("う", 3000)])
    hyp = _doc([("あ", 1000), ("い", 2000), ("う", 3000), ("え", 4000), ("お", 5000)])
    # "えお" is not in the reference, so the excerpt ends after "う".
    result = evaluate_pair(ref, hyp)
    over_split = evaluate_pair(
        _ref([("あいう", 1000)]),
        _doc([("あ", 1000), ("い", 2000), ("う", 3000)]),
    )

    good = format_report([("song-a", result)], target_recall=0.85, target_precision=0.80)
    bad = format_report([("song-b", over_split)], target_recall=0.85, target_precision=0.80)

    recall_line = next(line for line in good.splitlines() if "Recall" in line)
    precision_line = next(line for line in bad.splitlines() if "Precision" in line)
    assert "PASS" in recall_line
    assert "FAIL" in precision_line  # 1 hit out of 3 generated boundaries


def test_report_with_several_songs_adds_a_pooled_total():
    result = evaluate_pair(_ref([("あ", 1000)]), _doc([("あ", 1000)]))

    report = format_report(
        [("song-a", result), ("song-b", result)], target_recall=0.85, target_precision=0.80
    )

    assert "song-a" in report
    assert "song-b" in report
    assert "All songs" in report


def test_detailed_report_lists_missed_boundaries_with_their_time():
    ref = _ref([("夜空", 1000), ("を", 1300)])
    hyp = _doc([("夜空を", 1000)])

    report = format_report(
        [("song", evaluate_pair(ref, hyp))],
        target_recall=0.85,
        target_precision=0.80,
        details=True,
    )

    assert "を" in report
    assert "1.300" in report


def test_overlap_with_a_later_chunk_that_is_not_adjacent_is_counted():
    ref = _ref([("あ", 0), ("い", 500), ("う", 700)])
    hyp = _doc([("あ", 0, 1000), ("い", 500, 550), ("う", 700, 800)])

    checks = evaluate_pair(ref, hyp).end_time_checks

    assert checks.overlaps == 2  # あ over い, and あ over う
    assert checks.max_overlap_ms == 500
