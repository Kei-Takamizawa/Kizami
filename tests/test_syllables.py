import pytest

from kizami.generate.syllables import latin_letters, links, syllables


@pytest.mark.parametrize(
    "word, parts",
    [
        ("beautiful", ["beau", "ti", "ful"]),
        ("love", ["love"]),
        ("baby", ["ba", "by"]),
        ("little", ["lit", "tle"]),
        ("together", ["to", "ge", "ther"]),
        ("don't", ["don't"]),
        ("dancing", ["dan", "cing"]),
        ("hmm", ["hmm"]),
        ("Tonight", ["To", "night"]),
        ("forever", ["fo", "re", "ver"]),
        ("yeah", ["yeah"]),
        ("you", ["you"]),
        ("rock-n-roll", ["rock-", "n-", "roll"]),
        ("'cause", ["'cause"]),
        ("lonely", ["lone", "ly"]),
        ("dreamed", ["dreamed"]),
        ("wanted", ["wan", "ted"]),
        ("butterfly", ["but", "ter", "fly"]),
        ("hopeless", ["hope", "less"]),
        ("sadly", ["sad", "ly"]),
    ],
)
def test_english_syllables(word, parts):
    assert syllables(word, "en") == parts


@pytest.mark.parametrize(
    "word, parts",
    [
        ("corazón", ["co", "ra", "zón"]),
        ("quiero", ["quie", "ro"]),
        ("noche", ["no", "che"]),
        ("perro", ["pe", "rro"]),
        ("país", ["pa", "ís"]),
        ("poeta", ["po", "e", "ta"]),
        ("hablar", ["ha", "blar"]),
        ("contigo", ["con", "ti", "go"]),
        ("ahora", ["a", "ho", "ra"]),
        ("guitarra", ["gui", "ta", "rra"]),
        ("construir", ["cons", "truir"]),
        ("Bailando", ["Bai", "lan", "do"]),
        ("y", ["y"]),
        ("muy", ["muy"]),
        ("porque", ["por", "que"]),
        ("aquí", ["a", "quí"]),
        ("parque", ["par", "que"]),
        ("sigue", ["si", "gue"]),
        ("seguir", ["se", "guir"]),
        ("pequeño", ["pe", "que", "ño"]),
        ("alguien", ["al", "guien"]),
        ("Miguel", ["Mi", "guel"]),
        ("pingüino", ["pin", "güi", "no"]),
        ("agua", ["a", "gua"]),
    ],
)
def test_spanish_syllables(word, parts):
    assert syllables(word, "es") == parts


def test_latin_letters():
    assert latin_letters("CORAZÓN") == "corazon"
    assert latin_letters("Ñandú") == "nandu"
    assert latin_letters("don't") == "don't"
    assert latin_letters("ＬＯＶＥ") == "love"
    assert latin_letters("rock-n-") == "rockn"


def test_links():
    assert links("mi", "amor")
    assert links("que", "hago")
    assert not links("mi", "vida")
    assert not links("amor", "eterno")
    assert links("muy", "alto")
