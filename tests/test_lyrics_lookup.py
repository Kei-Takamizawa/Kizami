import io
import json
import urllib.error

import pytest

from kizami.lyrics_lookup import (
    LyricsLookupError,
    clean_lyrics_text,
    fetch_lrclib,
    lrclib_url,
    search_url,
)

# All lyric text in this file is made up for the tests.


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(payload=None, status=None, headers=None, raw=None):
    calls = []

    def open_(request, timeout):
        calls.append((request, timeout))
        if status is not None:
            raise urllib.error.HTTPError(request.full_url, status, "error", headers or {}, None)
        body = raw if raw is not None else json.dumps(payload).encode("utf-8")
        return FakeResponse(body)

    open_.calls = calls
    return open_


def test_lrclib_url_encodes_the_song_details():
    url = lrclib_url("夜の 歌", "A&B", album="空", duration_s=215.6)

    assert url == (
        "https://lrclib.net/api/get?track_name=%E5%A4%9C%E3%81%AE+%E6%AD%8C"
        "&artist_name=A%26B&album_name=%E7%A9%BA&duration=216"
    )


def test_lrclib_url_leaves_out_details_that_are_not_given():
    assert lrclib_url("a", "b") == "https://lrclib.net/api/get?track_name=a&artist_name=b"


def test_fetch_returns_plain_and_line_timed_lyrics():
    opener = _opener(
        {"plainLyrics": "そらを\nみる\n", "syncedLyrics": "[00:01.00] そらを", "instrumental": False}
    )

    result = fetch_lrclib("t", "a", opener=opener)

    assert result.plain == "そらを\nみる\n"
    assert result.synced == "[00:01.00] そらを\n"
    assert result.instrumental is False


def test_fetch_sends_one_https_request_with_a_generic_client_name():
    opener = _opener({"plainLyrics": "あ", "syncedLyrics": None})

    fetch_lrclib("t", "a", opener=opener)

    assert len(opener.calls) == 1
    request, timeout = opener.calls[0]
    assert request.full_url == "https://lrclib.net/api/get?track_name=t&artist_name=a"
    assert request.get_method() == "GET"
    assert request.get_header("User-agent").startswith("kizami/")
    assert set(request.headers) == {"User-agent", "Accept"}
    assert timeout > 0


def test_song_that_the_service_does_not_have_gives_none():
    assert fetch_lrclib("t", "a", opener=_opener(status=404)) is None


def test_rate_limit_is_reported_with_the_wait_time():
    opener = _opener(status=429, headers={"Retry-After": "30"})

    with pytest.raises(LyricsLookupError) as excinfo:
        fetch_lrclib("t", "a", opener=opener)

    assert "30" in str(excinfo.value)


def test_other_http_errors_are_reported():
    with pytest.raises(LyricsLookupError) as excinfo:
        fetch_lrclib("t", "a", opener=_opener(status=500))

    assert "500" in str(excinfo.value)


def test_network_failure_is_reported():
    def failing(request, timeout):
        raise urllib.error.URLError("no route")

    with pytest.raises(LyricsLookupError):
        fetch_lrclib("t", "a", opener=failing)


def test_answer_that_is_not_json_is_an_error():
    with pytest.raises(LyricsLookupError):
        fetch_lrclib("t", "a", opener=_opener(raw=b"<html>"))


def test_answer_with_wrong_field_types_is_an_error():
    with pytest.raises(LyricsLookupError):
        fetch_lrclib("t", "a", opener=_opener({"plainLyrics": ["x"], "syncedLyrics": None}))


def test_answer_that_is_too_large_is_an_error():
    big = json.dumps({"plainLyrics": "あ" * 2_000_000}).encode("utf-8")

    with pytest.raises(LyricsLookupError) as excinfo:
        fetch_lrclib("t", "a", opener=_opener(raw=big))

    assert "large" in str(excinfo.value)


def test_instrumental_track_has_no_lyrics():
    result = fetch_lrclib(
        "t", "a", opener=_opener({"plainLyrics": None, "syncedLyrics": None, "instrumental": True})
    )

    assert result.instrumental is True
    assert result.plain is None


def test_empty_title_or_artist_is_rejected_before_any_request():
    opener = _opener({"plainLyrics": "あ"})

    with pytest.raises(LyricsLookupError):
        fetch_lrclib(" ", "a", opener=opener)

    assert opener.calls == []


def test_clean_lyrics_text_removes_control_characters_and_unifies_line_ends():
    assert clean_lyrics_text("あ\r\nい\rう\x00\x1b[31m\tえ") == "あ\nい\nう[31m\tえ\n"


def test_clean_lyrics_text_of_nothing_is_empty():
    assert clean_lyrics_text("  \n ") == ""


def test_search_url_limits_a_web_search_to_the_lyric_site():
    url = search_url("uta-net", "夜の歌", "A B")

    assert url == (
        "https://www.google.com/search?q=site%3Auta-net.com+%E5%A4%9C%E3%81%AE%E6%AD%8C+A+B"
    )


def test_search_url_can_use_another_search_engine():
    url = search_url("utaten", "t", "a", engine="duckduckgo")

    assert url == "https://duckduckgo.com/?q=site%3Autaten.com+t+a"


def test_search_url_rejects_an_unknown_site():
    with pytest.raises(LyricsLookupError) as excinfo:
        search_url("example", "t", "a")

    assert "uta-net" in str(excinfo.value)
