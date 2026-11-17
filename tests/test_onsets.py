import wave

import pytest

np = pytest.importorskip("numpy")

from kizami.onsets import (  # noqa: E402
    OnsetError,
    detect_onsets,
    label_text_from_onsets,
    read_wav_mono,
)

RATE = 44100


def _silence(seconds):
    return np.zeros(round(seconds * RATE))


def _tone(seconds, freq=440.0, level=0.5):
    t = np.arange(round(seconds * RATE)) / RATE
    return level * np.sin(2 * np.pi * freq * t)


def _voice(seconds, f0=220.0, level=0.3):
    """A sound with many harmonics, like a sung vowel."""
    t = np.arange(round(seconds * RATE)) / RATE
    harmonics = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 21))
    return level * harmonics


def _noise(seconds, level=0.05, seed=1):
    rng = np.random.default_rng(seed)
    return level * rng.standard_normal(round(seconds * RATE))


def _near(onsets_ms, want_ms, tolerance_ms):
    return [ms for ms in onsets_ms if abs(ms - want_ms) <= tolerance_ms]


def test_abrupt_tone_bursts_give_one_onset_each_at_their_start():
    signal = np.concatenate(
        [_silence(0.5), _tone(0.3), _silence(0.4), _tone(0.3), _silence(0.5), _tone(0.3)]
    )

    onsets = detect_onsets(signal, RATE)

    assert len(onsets) == 3
    for onset, want in zip(onsets, [500, 1200, 2000]):
        assert abs(onset - want) <= 10


def test_silence_gives_no_onsets():
    assert detect_onsets(_silence(2.0), RATE) == []


def test_steady_tone_gives_no_onset_after_its_start():
    signal = np.concatenate([_silence(0.5), _tone(2.0)])

    onsets = detect_onsets(signal, RATE)

    assert [ms for ms in onsets if ms > 600] == []


def test_quiet_noise_before_a_tone_gives_an_onset_at_the_noise_start():
    # Like "sa": a quiet hiss for 100 ms, then the vowel.
    signal = np.concatenate([_silence(1.0), _noise(0.1), _voice(0.4), _silence(0.3)])

    onsets = detect_onsets(signal, RATE)

    assert _near(onsets, 1000, 10)
    assert min(onsets) >= 990


def test_soft_attack_gives_an_onset_near_the_start_of_the_rise():
    rise = _voice(0.04) * np.linspace(0.0, 1.0, round(0.04 * RATE))
    signal = np.concatenate([_silence(1.0), rise, _voice(0.4), _silence(0.3)])

    onsets = detect_onsets(signal, RATE)

    assert len(onsets) == 1
    assert -10 <= onsets[0] - 1000 <= 25


def test_new_pitch_without_a_gap_gives_an_onset():
    signal = np.concatenate([_silence(0.5), _voice(0.5, 196.0), _voice(0.5, 262.0)])

    onsets = detect_onsets(signal, RATE)

    assert _near(onsets, 1000, 15)


def test_onsets_are_never_closer_than_the_minimum_gap():
    clicks = np.concatenate([_silence(0.5)] + [_tone(0.01), _silence(0.01)] * 20)

    onsets = detect_onsets(clicks, RATE, min_gap_ms=60)

    assert onsets
    assert all(b - a >= 60 for a, b in zip(onsets, onsets[1:]))


def test_higher_threshold_gives_fewer_or_equal_onsets():
    signal = np.concatenate([_silence(0.5), _noise(0.1, level=0.002), _tone(0.4)])

    low = detect_onsets(signal, RATE, threshold_db=1.0)
    high = detect_onsets(signal, RATE, threshold_db=12.0)

    assert len(high) <= len(low)
    assert low


def test_result_does_not_depend_on_overall_volume():
    signal = np.concatenate([_silence(0.5), _tone(0.3), _silence(0.4), _tone(0.3)])

    assert detect_onsets(signal, RATE) == detect_onsets(signal * 0.05, RATE)


def test_other_sample_rate_gives_the_same_times():
    rate = 48000
    t = np.arange(round(0.3 * rate)) / rate
    signal = np.concatenate([np.zeros(round(0.5 * rate)), 0.5 * np.sin(2 * np.pi * 440 * t)])

    onsets = detect_onsets(signal, rate)

    assert len(onsets) == 1
    assert abs(onsets[0] - 500) <= 10


def test_signal_shorter_than_one_window_gives_no_onsets():
    assert detect_onsets(np.ones(10), RATE) == []


def _write_wav(path, frames, channels=2, sample_width=2, rate=RATE):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(sample_width)
        out.setframerate(rate)
        out.writeframes(frames)


def test_read_wav_mono_averages_channels_and_scales_to_one(tmp_path):
    path = tmp_path / "a.wav"
    # Two stereo frames: (16384, 0) and (-32768, -32768).
    frames = np.array([16384, 0, -32768, -32768], dtype="<i2").tobytes()
    _write_wav(path, frames)

    samples, rate = read_wav_mono(path)

    assert rate == RATE
    assert samples.tolist() == [0.25, -1.0]


def test_read_wav_mono_reads_24_bit_files(tmp_path):
    path = tmp_path / "a.wav"
    half = (2**22).to_bytes(3, "little", signed=True)
    minus_one = (-(2**23)).to_bytes(3, "little", signed=True)
    _write_wav(path, half + minus_one, channels=1, sample_width=3)

    samples, _ = read_wav_mono(path)

    assert samples.tolist() == [0.5, -1.0]


def test_read_wav_mono_rejects_a_file_that_is_not_wav(tmp_path):
    path = tmp_path / "a.wav"
    path.write_bytes(b"not a wav file")

    with pytest.raises(OnsetError) as excinfo:
        read_wav_mono(path)

    assert "WAV" in str(excinfo.value)


def test_label_text_has_one_point_label_without_text_per_onset():
    assert label_text_from_onsets([1000, 2345]) == "1.000000\t1.000000\t\n2.345000\t2.345000\t\n"


def test_label_text_of_nothing_is_empty():
    assert label_text_from_onsets([]) == ""
