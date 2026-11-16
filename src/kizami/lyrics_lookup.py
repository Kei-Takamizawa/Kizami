"""Look up lyrics for a song that the user names, and find lyric pages.

Nothing in this module runs unless the user asks for it. A lookup sends the
song title, artist, album, and duration to the lookup service, and nothing
else. See section 11 of ``docs/design.md`` for the rules.

The answer of the service is untrusted text. It is size-limited, type-checked,
and cleaned before it is returned.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

LRCLIB_ENDPOINT = "https://lrclib.net/api/get"
# A generic client name. It must not identify the user.
USER_AGENT = "kizami/0.0.1"
TIMEOUT_S = 15
MAX_ANSWER_BYTES = 1_000_000

LYRIC_SITES = {
    "uta-net": "uta-net.com",
    "utaten": "utaten.com",
    "j-lyric": "j-lyric.net",
    "genius": "genius.com",
}
SEARCH_ENGINES = {
    "google": "https://www.google.com/search?",
    "duckduckgo": "https://duckduckgo.com/?",
}


class LyricsLookupError(Exception):
    """The lookup cannot be done or its answer cannot be used."""


@dataclass(frozen=True)
class LookupResult:
    """Lyrics as the service holds them. ``synced`` is line-timed text in LRC form."""

    plain: str | None
    synced: str | None
    instrumental: bool = False


def clean_lyrics_text(text: str) -> str:
    """Unify line ends and drop control characters. Tabs and line breaks stay."""
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    kept = "".join(ch for ch in unified if ch in "\n\t" or (ord(ch) >= 32 and ord(ch) != 127))
    if not kept.strip():
        return ""
    return kept.rstrip("\n") + "\n"


def lrclib_url(
    title: str, artist: str, album: str | None = None, duration_s: float | None = None
) -> str:
    query = [("track_name", title), ("artist_name", artist)]
    if album:
        query.append(("album_name", album))
    if duration_s is not None:
        query.append(("duration", str(round(duration_s))))
    return f"{LRCLIB_ENDPOINT}?{urllib.parse.urlencode(query)}"


def _optional_text(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise LyricsLookupError(f"the answer has an unexpected '{key}' field")
    return clean_lyrics_text(value) or None


def fetch_lrclib(
    title: str,
    artist: str,
    album: str | None = None,
    duration_s: float | None = None,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> LookupResult | None:
    """Ask LRCLIB for the lyrics of one song. Return None if it has no such song."""
    title, artist = title.strip(), artist.strip()
    if not title or not artist:
        raise LyricsLookupError("a lookup needs both a title and an artist")

    request = urllib.request.Request(
        lrclib_url(title, artist, album, duration_s),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        method="GET",
    )
    try:
        with opener(request, timeout=TIMEOUT_S) as response:
            body = response.read(MAX_ANSWER_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        if exc.code == 429:
            wait = exc.headers.get("Retry-After") if exc.headers else None
            hint = f" Wait {wait} seconds before the next try." if wait else ""
            raise LyricsLookupError(f"the service asks for fewer requests (429).{hint}") from exc
        raise LyricsLookupError(f"the service answered with error {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise LyricsLookupError(f"the service cannot be reached: {exc}") from exc

    if len(body) > MAX_ANSWER_BYTES:
        raise LyricsLookupError("the answer is too large to be lyrics")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LyricsLookupError("the answer is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise LyricsLookupError("the answer has an unexpected form")

    return LookupResult(
        plain=_optional_text(payload, "plainLyrics"),
        synced=_optional_text(payload, "syncedLyrics"),
        instrumental=payload.get("instrumental") is True,
    )


def search_url(site: str, title: str, artist: str, engine: str = "google") -> str:
    """Return the address of a web search for the song, limited to one lyric site.

    The address is for the user's browser. Kizami does not read the pages.
    """
    if site not in LYRIC_SITES:
        raise LyricsLookupError(f"unknown site '{site}'. Choose from: {', '.join(LYRIC_SITES)}")
    if engine not in SEARCH_ENGINES:
        raise LyricsLookupError(
            f"unknown search engine '{engine}'. Choose from: {', '.join(SEARCH_ENGINES)}"
        )
    terms = f"site:{LYRIC_SITES[site]} {title.strip()} {artist.strip()}".strip()
    return SEARCH_ENGINES[engine] + urllib.parse.urlencode({"q": terms})
