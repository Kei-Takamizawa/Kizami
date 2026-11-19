import json
import shutil
import stat
import sys
import wave

import pytest

import kizami.separate as separate
from kizami.separate import SeparateError, convert_to_wav, separate_vocals

FAKE_DEMUCS = r'''#!{python}
import sys, pathlib, time, os, json
args = sys.argv[1:]
if os.environ.get("FAKE_DEMUCS_LOG"):
    pathlib.Path(os.environ["FAKE_DEMUCS_LOG"]).write_text(json.dumps({{"args": args, "offline": os.environ.get("HF_HUB_OFFLINE")}}))
assert args[:2] == ["-m", "demucs"], args
out = pathlib.Path(args[args.index("-o") + 1])
name = args[args.index("--filename") + 1]
model = args[args.index("-n") + 1]
if "FAIL" in args[-1]:
    sys.stderr.write("RuntimeError: cannot read the file\n")
    sys.exit(1)
for p in (0, 37, 100):
    sys.stderr.write(f"\r {{p}}%|####| {{p}}/100 [00:01<00:00]")
    sys.stderr.flush()
sys.stderr.write("\n")
target = out / model / name.format(stem="vocals", ext="wav")
target.parent.mkdir(parents=True)
target.write_bytes(b"RIFF fake vocals")
'''


@pytest.fixture
def fake_python(tmp_path, monkeypatch):
    script = tmp_path / "fake_python"
    script.write_text(FAKE_DEMUCS.format(python=sys.executable), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(separate, "demucs_available", lambda: True)
    monkeypatch.setattr(separate, "ffmpeg_available", lambda: True)
    return str(script)


def test_separate_vocals_runs_demucs_and_reports_progress(tmp_path, fake_python):
    song = tmp_path / "song.m4a"
    song.write_bytes(b"x")
    seen = []

    separate_vocals(song, tmp_path / "out" / "vocals.wav", progress=seen.append, python=fake_python)

    assert (tmp_path / "out" / "vocals.wav").read_bytes() == b"RIFF fake vocals"
    assert seen == [0, 37, 100]


def run_logged(tmp_path, fake_python, monkeypatch):
    log = tmp_path / "log.json"
    monkeypatch.setenv("FAKE_DEMUCS_LOG", str(log))
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    song = tmp_path / "song.m4a"
    song.write_bytes(b"x")
    separate_vocals(song, tmp_path / "vocals.wav", python=fake_python)
    return json.loads(log.read_text())


def test_separation_has_no_random_shift_so_a_song_always_gives_the_same_vocals(tmp_path, fake_python, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    args = run_logged(tmp_path, fake_python, monkeypatch)["args"]
    assert args[args.index("--shifts") + 1] == "0"


def test_demucs_stays_offline_once_its_weights_are_in_the_cache(tmp_path, fake_python, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    assert run_logged(tmp_path, fake_python, monkeypatch)["offline"] is None  # first run: may download

    snapshot = tmp_path / "hub" / "models--adefossez--HTDemucs" / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "htdemucs.yaml").write_text("models: [x]\n")
    (snapshot / "955717e8.safetensors").write_bytes(b"w")

    assert separate.weights_cached()
    assert run_logged(tmp_path, fake_python, monkeypatch)["offline"] == "1"


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"HF_HUB_CACHE": "/c/hub"}, "/c/hub"),
        ({"HF_HOME": "/h"}, "/h/hub"),
        ({"XDG_CACHE_HOME": "/x"}, "/x/huggingface/hub"),
    ],
)
def test_hugging_face_cache_is_found_like_huggingface_hub_finds_it(monkeypatch, env, expected):
    for name in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "HF_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert separate.hf_cache_dir().as_posix() == expected


def test_separate_vocals_shows_the_demucs_error(tmp_path, fake_python):
    song = tmp_path / "FAIL.m4a"
    song.write_bytes(b"x")
    with pytest.raises(SeparateError, match="cannot read the file"):
        separate_vocals(song, tmp_path / "vocals.wav", python=fake_python)


def test_separate_vocals_without_demucs_says_how_to_install_it(tmp_path, monkeypatch):
    monkeypatch.setattr(separate, "demucs_available", lambda: False)
    with pytest.raises(SeparateError, match="pip install"):
        separate_vocals(tmp_path / "a.wav", tmp_path / "b.wav")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs the ffmpeg program")
def test_convert_to_wav_writes_16_bit_pcm_at_44_1_khz(tmp_path):
    src = tmp_path / "in.wav"
    with wave.open(str(src), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x01" * 16000)
    out = tmp_path / "sub" / "vocals.wav"

    convert_to_wav(src, out)

    with wave.open(str(out), "rb") as w:
        assert (w.getsampwidth(), w.getframerate()) == (2, 44100)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs the ffmpeg program")
def test_convert_to_wav_reports_a_file_ffmpeg_cannot_read(tmp_path):
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"not audio")
    with pytest.raises(SeparateError, match="ffmpeg could not read"):
        convert_to_wav(bad, tmp_path / "out.wav")
