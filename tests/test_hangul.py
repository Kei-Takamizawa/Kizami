from kizami.generate.hangul import block_letters, word_letters


def test_block_letters_follow_uroman():
    assert block_letters("사") == ("s", "a", "")
    assert block_letters("랑") == ("r", "a", "ng")
    assert block_letters("차") == ("c", "a", "")
    assert block_letters("아") == ("", "a", "")


def test_liaison_moves_a_final_consonant_to_a_silent_lead():
    assert word_letters("음악") == ["eu", "mag"]
    assert word_letters("사랑해") == ["sa", "rang", "hae"]
    assert word_letters("읽어") == ["il", "geo"]  # double tail: the second part moves
    assert word_letters("좋아") == ["joh", "a"]  # h stays
    assert word_letters("사랑이") == ["sa", "rang", "i"]  # the ng tail never moves
    assert "".join(word_letters("음악")) == "eumag"


def test_non_hangul_characters_give_no_letters():
    assert word_letters("ㅋㅋ") == ["", ""]
