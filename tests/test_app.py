import http.client
import json
import os
import threading
import time
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

from kizami import app as app_module
from kizami.app import (
    TOKEN_HEADER,
    App,
    AppError,
    Job,
    create_server,
    run_pipeline,
    safe_name,
)
from kizami.timing import Chunk, Line, TimingDocument, save_document

NS = "{http://www.w3.org/ns/ttml}"


def write_tone(path, seconds=3.0, parts=((0.5, 1.2), (1.5, 2.0)), rate=16000):
    x = np.zeros(int(rate * seconds))
    t = np.arange(len(x)) / rate
    for a, b in parts:
        sl = slice(int(a * rate), int(b * rate))
        x[sl] = 0.5 * np.sin(2 * np.pi * 220 * t[sl])
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((x * 32767).astype("<i2").tobytes())


def fake_pipeline(job, upload, options, app):
    """Stands in for separation and generation: writes the files they would write."""
    folder = app.work_dir / f"{job.name}-{upload.sha256[:8]}"
    folder.mkdir(parents=True, exist_ok=True)
    job.folder = folder
    assert upload.path.read_bytes() == b"song-bytes"
    write_tone(folder / "vocals.wav")
    (folder / "lyrics.txt").write_text(options["lyrics"], encoding="utf-8")
    doc = TimingDocument(lines=(Line(chunks=(Chunk("あ", 500), Chunk("い", 1500))),))
    save_document(doc, folder / "auto_chu.json")
    job.counts = {"lines": 1, "chunks": 2, "uncertain": 0}
    app.write_ttml(job, job.offset_ms, use_tagger=False)


@pytest.fixture
def running(tmp_path):
    started = []

    def tagger_starter(folder):
        started.append(folder)
        return "http://127.0.0.1:1/?t=x"

    app = App(tmp_path / "work", None, pipeline=fake_pipeline, tagger_starter=tagger_starter)
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    app.started_taggers = started
    yield app, server.server_address[1]
    server.shutdown()
    server.server_close()
    app.close()


def request(port, method, path, *, token=None, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    all_headers = dict(headers or {})
    if token is not None:
        all_headers[TOKEN_HEADER] = token
    connection.request(method, path, body=body, headers=all_headers)
    response = connection.getresponse()
    data = response.read()
    connection.close()
    return response.status, data, response


def upload(port, token, name="ドライ フラワー.m4a", body=b"song-bytes"):
    from urllib.parse import quote

    status, data, _ = request(port, "POST", "/api/upload", token=token, body=body, headers={"X-File-Name": quote(name)})
    assert status == 200, data
    return json.loads(data)["upload"]


def start(port, token, **fields):
    payload = {"lyrics": "あい\n", "offset_ms": 0, **fields}
    return request(port, "POST", "/api/jobs", token=token, body=json.dumps(payload).encode("utf-8"),
                   headers={"Content-Type": "application/json"})  # fmt: skip


def wait_done(port, token, job_id):
    for _ in range(200):
        status, data, _ = request(port, "GET", f"/api/jobs/{job_id}", token=token)
        assert status == 200
        info = json.loads(data)
        if info["state"] != "running":
            return info
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_page_is_served_with_a_content_security_policy(running):
    _, port = running
    status, body, response = request(port, "GET", "/")
    assert status == 200
    assert b"Make TTML" in body
    assert "default-src 'none'" in response.getheader("Content-Security-Policy")


def test_api_needs_the_token_and_the_local_host_name(running):
    app, port = running
    assert request(port, "GET", "/api/info")[0] == 403
    assert request(port, "GET", "/api/info", token="wrong")[0] == 403
    assert request(port, "GET", "/api/info", token=app.token, headers={"Host": "evil.example:80"})[0] == 403
    assert request(port, "POST", "/api/upload", token=app.token, body=b"x",
                   headers={"Origin": "http://evil.example", "X-File-Name": "a.wav"})[0] == 403  # fmt: skip
    status, data, _ = request(port, "GET", "/api/info", token=app.token)
    assert status == 200
    info = json.loads(data)
    assert info["work_dir"] == str(app.work_dir)
    assert any("model folder" in p for p in info["problems"])


def test_only_audio_files_are_accepted(running):
    app, port = running
    status, data, _ = request(port, "POST", "/api/upload", token=app.token, body=b"x", headers={"X-File-Name": "notes.txt"})
    assert status == 400
    assert b"audio file" in data


def test_song_and_lyrics_give_a_ttml_file_with_the_chosen_offset(running):
    app, port = running
    up = upload(port, app.token)

    status, data, _ = start(port, app.token, upload=up, offset_ms=40, title="Dry", artist="X")
    assert status == 200, data
    info = wait_done(port, app.token, json.loads(data)["id"])

    assert info["state"] == "done", info
    assert info["counts"] == {"lines": 1, "chunks": 2, "uncertain": 0}
    assert info["ttml_name"] == "ドライ フラワー.ttml"
    folder = Path(info["folder"])
    assert folder.parent == app.work_dir
    assert (folder / "ドライ フラワー.ttml").is_file()
    assert not any(app._upload_dir.iterdir())  # the uploaded copy is removed

    status, body, response = request(port, "GET", f"/api/jobs/{info['id']}/ttml?offset_ms=-100", token=app.token)
    assert status == 200
    assert "filename*=UTF-8''" in response.getheader("Content-Disposition")
    root = ET.fromstring(body.decode("utf-8"))
    spans = [(s.text, s.get("begin"), s.get("end")) for s in root.iter(f"{NS}span")]
    assert spans[0][:2] == ("あ", "00:00:00.400")
    assert abs(_sec(spans[0][2]) - 1.1) <= 0.02  # voice stops at 1.2 s, minus 100 ms
    assert spans[1][1] == "00:00:01.400"
    assert b'value="Dry"' in body


def test_the_download_can_use_the_fixes_saved_in_the_tagger(running):
    app, port = running
    status, data, _ = start(port, app.token, upload=upload(port, app.token))
    info = wait_done(port, app.token, json.loads(data)["id"])
    assert info["tagger_saved"] is None
    project = {"format": "kizami-tagger", "version": 1,
               "lines": [{"units": [{"text": "あい", "start_ms": 600, "end_ms": 900}]}]}  # fmt: skip
    (Path(info["folder"]) / "tagger.json").write_text(json.dumps(project), encoding="utf-8")

    info = json.loads(request(port, "GET", f"/api/jobs/{info['id']}", token=app.token)[1])
    status, body, _ = request(port, "GET", f"/api/jobs/{info['id']}/ttml?source=tagger", token=app.token)

    assert info["tagger_saved"]
    assert status == 200
    root = ET.fromstring(body.decode("utf-8"))
    assert [(s.text, s.get("begin"), s.get("end")) for s in root.iter(f"{NS}span")] == [
        ("あい", "00:00:00.600", "00:00:00.900")
    ]


def test_the_download_can_drop_the_chunk_spaces(running):
    app, port = running
    status, data, _ = start(port, app.token, upload=upload(port, app.token))
    info = wait_done(port, app.token, json.loads(data)["id"])

    spaced = request(port, "GET", f"/api/jobs/{info['id']}/ttml", token=app.token)[1].decode("utf-8")
    plain = request(port, "GET", f"/api/jobs/{info['id']}/ttml?spaces=0", token=app.token)[1].decode("utf-8")

    assert "</span> <span" in spaced and "</span><span" not in spaced
    assert "</span><span" in plain and "</span> <span" not in plain


def test_uploads_wait_in_the_work_folder_and_leftovers_are_removed(tmp_path):
    work = tmp_path / "work"
    (work / "_uploads").mkdir(parents=True)
    old = work / "_uploads" / "old.m4a"
    old.write_bytes(b"x")
    os.utime(old, (time.time() - 7200, time.time() - 7200))
    fresh = work / "_uploads" / "fresh.m4a"  # another app may be receiving it
    fresh.write_bytes(b"y")

    app = App(work, None)
    upload_id = app.save_upload("song.wav", _Stream(b"abc"), 3)
    waiting = app.uploads[upload_id].path

    assert not old.exists() and fresh.exists()
    assert waiting.parent == work / "_uploads"
    app.close()
    assert not waiting.exists() and fresh.exists()


def test_the_tagger_is_started_once_per_song(running):
    app, port = running
    status, data, _ = start(port, app.token, upload=upload(port, app.token))
    job_id = wait_done(port, app.token, json.loads(data)["id"])["id"]

    first = request(port, "POST", f"/api/jobs/{job_id}/tagger", token=app.token)
    second = request(port, "POST", f"/api/jobs/{job_id}/tagger", token=app.token)

    assert first[0] == second[0] == 200
    assert json.loads(first[1])["url"].startswith("http://127.0.0.1:")
    assert len(app.started_taggers) == 1


def test_a_job_needs_lyrics_and_a_received_song(running):
    app, port = running
    assert start(port, app.token, upload=upload(port, app.token), lyrics="  \n")[0] == 409
    status, data, _ = start(port, app.token, upload="nope")
    assert status == 409
    assert b"not received" in data
    assert start(port, app.token, upload=upload(port, app.token), offset_ms=99999)[0] == 409
    assert start(port, app.token, upload=upload(port, app.token), latin="fr")[0] == 409


def test_one_song_at_a_time(tmp_path):
    gate = threading.Event()

    def slow(job, upload, options, app):
        gate.wait(5)
        raise AppError("stopped")

    app = App(tmp_path / "work", None, pipeline=slow)
    first = app.save_upload("a.wav", _Stream(b"1"), 1)
    second = app.save_upload("b.wav", _Stream(b"2"), 1)
    job = app.start_job({"upload": first, "lyrics": "あ"})
    with pytest.raises(AppError, match="still being processed"):
        app.start_job({"upload": second, "lyrics": "あ"})
    gate.set()
    for _ in range(100):
        if job.state != "running":
            break
        time.sleep(0.01)
    assert job.state == "failed"
    assert job.error == "stopped"
    app.close()


class _Stream:
    def __init__(self, data):
        self.data = data

    def read(self, n):
        out, self.data = self.data[:n], self.data[n:]
        return out


@pytest.mark.parametrize(
    "name, expected",
    [
        ("ドライフラワー (152kbit_Opus).opus", "ドライフラワー _152kbit_Opus_"),
        ("../../etc/passwd", "passwd"),
        ("a/b\\c.wav", "c"),
        ("....wav", "song"),  # Path("....wav").stem is "....wav" in Python 3.14
        (".hidden.m4a", "hidden"),
        ("a.b.flac", "a.b"),
        ("", "song"),
    ],
)
def test_safe_name(name, expected):
    assert safe_name(name) == expected.strip(" ._")


# -- the real pipeline, with separation and generation replaced ---------------


@pytest.fixture
def fakes(monkeypatch):
    calls = {"separate": 0, "convert": 0, "generate": 0}

    def separate_vocals(src, out, progress=None):
        calls["separate"] += 1
        if progress:
            progress(50)
        write_tone(out)

    def convert_to_wav(src, out):
        calls["convert"] += 1
        write_tone(out)

    def generate(folder, *, model_dir, cache, progress, latin="auto"):
        from kizami.generate import GenerateResult, LevelResult

        calls["generate"] += 1
        calls["latin"] = latin
        progress("aligning the lyrics (kana model)")
        doc = TimingDocument(lines=(Line(chunks=(Chunk("あ", 500), Chunk("い", 1500, uncertain=True))),))
        save_document(doc, Path(folder) / "auto_chu.json")
        return GenerateResult(lines=1, units=2, with_attack=1, from_second_aligner=0, kanji_kept_whole=0,
                              used_second_aligner=False, start_rules={},
                              levels=[LevelResult("chu", Path(folder) / "auto_chu.json", Path(folder) / "x.txt", 2, 1)])  # fmt: skip

    import kizami.generate
    import kizami.separate

    monkeypatch.setattr(kizami.separate, "separate_vocals", separate_vocals)
    monkeypatch.setattr(kizami.separate, "convert_to_wav", convert_to_wav)
    monkeypatch.setattr(kizami.generate, "generate", generate)
    return calls


def _job(app, name="song.m4a", data=b"abc", lyrics="あい\n", is_vocals=False, latin="auto"):
    upload_id = app.save_upload(name, _Stream(data), len(data))
    up = app.uploads.pop(upload_id)
    job = Job(id="j" * 12, name=safe_name(name))
    run_pipeline(job, up, {"lyrics": lyrics, "is_vocals": is_vocals, "latin": latin}, app)
    return job


def test_pipeline_passes_the_latin_language(tmp_path, fakes):
    app = App(tmp_path / "work", tmp_path / "models")
    _job(app, lyrics="te quiero\n", latin="es")
    assert fakes["latin"] == "es"
    app.close()


def test_pipeline_separates_once_per_song_and_writes_the_files(tmp_path, fakes):
    app = App(tmp_path / "work", tmp_path / "models")

    job = _job(app)
    again = _job(app)

    folder = job.folder
    assert folder == again.folder
    assert folder.name.startswith("song-")
    for name in ("vocals.wav", "lyrics.txt", "auto_chu.json", "source.json", "song.ttml"):
        assert (folder / name).is_file(), name
    assert fakes["separate"] == 1  # the second run reused vocals.wav
    assert fakes["generate"] == 2
    assert job.counts == {"lines": 1, "chunks": 2, "uncertain": 1}
    assert (folder / "lyrics.txt").read_text(encoding="utf-8") == "あい\n"
    app.close()


def test_pipeline_converts_a_vocal_track_instead_of_separating(tmp_path, fakes):
    app = App(tmp_path / "work", tmp_path / "models")
    _job(app, is_vocals=True)
    _job(app, is_vocals=False)  # same file, other choice: separate it
    assert fakes["convert"] == 1
    assert fakes["separate"] == 1
    app.close()


def test_pipeline_sets_old_tagger_fixes_aside_when_the_lyrics_change(tmp_path, fakes):
    app = App(tmp_path / "work", tmp_path / "models")
    folder = _job(app).folder
    (folder / "tagger.json").write_text("{}", encoding="utf-8")

    _job(app)  # same lyrics: the fixes still match
    assert (folder / "tagger.json").is_file()

    _job(app, lyrics="あいう\n")
    assert not (folder / "tagger.json").exists()
    assert (folder / "tagger.json.prev").is_file()
    app.close()


def test_pipeline_needs_a_model_folder(tmp_path, fakes):
    app = App(tmp_path / "work", None)
    with pytest.raises(AppError, match="model folder"):
        _job(app)
    app.close()


def test_cli_app_reports_a_work_folder_that_cannot_be_made(tmp_path, capsys):
    from kizami.cli import main

    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    code = main(["app", "--work-dir", str(blocker / "sub"), "--no-browser"])
    assert code == 2
    assert "work folder" in capsys.readouterr().err


def _sec(value):
    h, m, s = value.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


# -- one-click start -----------------------------------------------------------


def test_launcher_starts_this_kizami_with_the_folders_kept(tmp_path):
    import subprocess

    from kizami.app import make_launcher

    models = tmp_path / "my models"
    models.mkdir()
    log = tmp_path / "args.txt"
    fake = tmp_path / "py thon"
    fake.write_text(f"#!/bin/bash\nprintf '%s\\n' \"$@\" > '{log}'\n", encoding="utf-8")
    fake.chmod(0o755)

    path = make_launcher(tmp_path / "Kizami.command", model_dir=models, work_dir=tmp_path / "songs", python=str(fake))

    assert os.access(path, os.X_OK)
    assert subprocess.run(["bash", "-n", str(path)]).returncode == 0
    subprocess.run(["bash", str(path)], check=True, timeout=10)
    assert log.read_text(encoding="utf-8").splitlines() == [
        "-m", "kizami", "app", "--model-dir", str(models.resolve()), "--work-dir", str((tmp_path / "songs").resolve())
    ]
    make_launcher(path, model_dir=models, work_dir=tmp_path, port=9000)  # its own launcher may be replaced
    assert "--port 9000" in path.read_text(encoding="utf-8")


def test_launcher_never_replaces_another_file_and_needs_the_model_folder(tmp_path):
    from kizami.app import make_launcher

    other = tmp_path / "notes.command"
    other.write_text("echo mine\n", encoding="utf-8")
    with pytest.raises(AppError, match="not a Kizami launcher"):
        make_launcher(other, model_dir=tmp_path, work_dir=tmp_path)
    with pytest.raises(AppError, match="model folder"):
        make_launcher(tmp_path / "k.command", model_dir=tmp_path / "none", work_dir=tmp_path)
    assert other.read_text(encoding="utf-8") == "echo mine\n"


def test_cli_makes_the_launcher_instead_of_starting(tmp_path, capsys, monkeypatch):
    from kizami.cli import main

    monkeypatch.delenv("KIZAMI_MODEL_DIR", raising=False)
    target = tmp_path / "Kizami.command"
    assert main(["app", "--make-launcher", str(target)]) == 2
    assert "--model-dir" in capsys.readouterr().err

    assert main(["app", "--make-launcher", str(target), "--model-dir", str(tmp_path)]) == 0
    assert target.is_file()
    assert "Double-click it" in capsys.readouterr().out


def test_a_second_start_opens_the_page_of_the_app_that_runs(running, monkeypatch):
    from kizami.app import RUNNING, _write_running, running_url, serve

    app, port = running
    url = f"http://127.0.0.1:{port}/?t={app.token}"
    assert running_url(app.work_dir) is None
    _write_running(app.work_dir, url)
    opened = []
    monkeypatch.setattr(app_module.webbrowser, "open", opened.append)

    assert running_url(app.work_dir) == url
    assert (app.work_dir / RUNNING).stat().st_mode & 0o777 == 0o600
    serve(work_dir=app.work_dir, open_browser=True)  # returns at once: no second server
    assert opened == [url]

    _write_running(app.work_dir, f"http://127.0.0.1:{port}/?t=wrong")
    assert running_url(app.work_dir) is None


def test_the_app_tells_where_it_runs_while_it_runs(tmp_path, monkeypatch):
    from kizami.app import RUNNING, running_url, serve

    servers = []
    real = app_module.create_server

    def keep(app, port=0):
        servers.append(real(app, 0))
        return servers[-1]

    monkeypatch.setattr(app_module, "create_server", keep)
    work = tmp_path / "work"
    thread = threading.Thread(target=serve, kwargs={"work_dir": work, "port": 0, "open_browser": False}, daemon=True)
    thread.start()
    for _ in range(200):
        if (work / RUNNING).exists():
            break
        time.sleep(0.01)
    assert running_url(work) is not None

    servers[0].shutdown()
    thread.join(5)
    assert not (work / RUNNING).exists()
