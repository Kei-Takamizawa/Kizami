import numpy as np

from kizami.loudness import LAST_LIMIT_S, add_end_times, loudness_from_samples
from kizami.timing import Chunk, Line, TimingDocument

RATE = 16000


def tone(seconds, *parts, level=0.5):
    x = np.zeros(int(RATE * seconds))
    t = np.arange(len(x)) / RATE
    for a, b in parts:
        sl = slice(int(a * RATE), int(b * RATE))
        x[sl] = level * np.sin(2 * np.pi * 220 * t[sl])
    return x


def test_sound_end_is_where_the_voice_stops():
    loud = loudness_from_samples(tone(3.0, (0.5, 1.2)), RATE)
    assert abs(loud.sound_end(0.5, 2.5) - 1.2) < 0.02


def test_sound_end_never_passes_the_limit():
    loud = loudness_from_samples(tone(3.0, (0.5, 2.0)), RATE)
    assert loud.sound_end(0.5, 1.0) == 1.0


def test_a_short_dip_does_not_end_the_sound():
    # 40 ms of silence is shorter than the 80 ms of quiet that ends a sound.
    loud = loudness_from_samples(tone(3.0, (0.5, 1.0), (1.04, 1.6)), RATE)
    assert abs(loud.sound_end(0.5, 2.5) - 1.6) < 0.02


def test_quiet_means_40_db_below_the_loudest_part():
    x = tone(3.0, (0.2, 0.6)) + tone(3.0, (0.6, 1.4), level=0.5 * 10 ** (-30 / 20))
    loud = loudness_from_samples(x, RATE)
    # The part 30 dB down still counts as sound; it stops at 1.4 s.
    assert abs(loud.sound_end(0.2, 2.5) - 1.4) < 0.02


def test_end_is_at_least_30_ms_after_the_start():
    loud = loudness_from_samples(tone(1.0, (0.2, 0.8)), RATE)
    assert loud.sound_end(0.5, 0.51) == 0.53


def test_add_end_times_uses_the_next_chunk_in_song_order_as_the_limit():
    x = tone(8.0, (1.0, 2.0), (2.0, 2.5), (3.0, 3.6))
    doc = TimingDocument(
        lines=(
            Line(chunks=(Chunk("あ", 1000), Chunk("い", 2000, 2222))),
            Line(chunks=(Chunk("う", 3000),)),
        )
    )

    out = add_end_times(doc, loudness_from_samples(x, RATE))

    a, i = out.lines[0].chunks
    (u,) = out.lines[1].chunks
    assert a.end_ms == 2000  # no quiet before the next chunk starts
    assert i.end_ms == 2222  # an end that was set is kept
    assert abs(u.end_ms - 3600) <= 20
    assert u.end_ms <= 3000 + LAST_LIMIT_S * 1000
