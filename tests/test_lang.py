from kizami.generate.lang import guess_latin, script_of, script_runs


def test_script_of():
    assert [script_of(c) for c in "あア漢ー々한aÑ1 ,"] == ["ja"] * 5 + ["ko", "latin", "latin", "", "", ""]


def test_runs_split_at_script_changes_and_keep_inner_spaces():
    assert script_runs("I love you 君が好き") == [(0, 10, "latin"), (11, 15, "ja")]
    assert script_runs("Baby사랑해") == [(0, 4, "latin"), (4, 7, "ko")]
    assert script_runs("「君と」 dance") == [(1, 3, "ja"), (5, 10, "latin")]


def test_a_line_in_one_script_is_one_run():
    assert script_runs("君と、ずっと") == [(0, 6, "ja")]
    assert script_runs("") == []


def test_guess_latin():
    assert guess_latin("yo quiero bailar contigo".split()) == "es"
    assert guess_latin("I want you baby".split()) == "en"
    assert guess_latin(["corazón"]) == "es"
    assert guess_latin([]) == "en"


def test_guess_latin_ignores_case():
    assert guess_latin("TE QUIERO MI AMOR".split()) == "es"
    assert guess_latin("THE NIGHT IS YOUNG".split()) == "en"
