import http.client
import json
import threading

import pytest

from kizami.cli import main
from kizami.tagger import (
    DEFAULT_OUTPUT,
    PROJECT_NAME,
    TOKEN_HEADER,
    Session,
    TaggerError,
    create_server,
)

LABELS = "1.000000\t1.400000\t夜\n1.400000\t1.900000\t?空\n"
PROJECT = json.dumps({"format": "kizami-tagger", "version": 1, "lines": []})


@pytest.fixture
def song(tmp_path):
    folder = tmp_path / "song1"
    folder.mkdir()
    (folder / "vocals.wav").write_bytes(b"RIFFdata")
    (folder / "lyrics.txt").write_text("夜空\n", encoding="utf-8")
    (folder / "auto_labels.txt").write_text(LABELS, encoding="utf-8")
    (folder / "notes.md").write_text("private", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside", encoding="utf-8")
    return folder


@pytest.fixture
def running(song):
    session = Session(song)
    server = create_server(session)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield session, server.server_address[1]
    server.shutdown()
    server.server_close()


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


def test_session_lists_the_files_of_the_song_folder(song):
    info = Session(song, labels="auto_labels.txt").describe()

    assert info["folder"] == "song1"
    assert info["audio"] == ["vocals.wav"]
    assert info["texts"] == ["auto_labels.txt", "lyrics.txt"]
    assert info["lyrics"] == "lyrics.txt"
    assert info["project"] is None
    assert info["labels"] == "auto_labels.txt"
    assert info["output"] == DEFAULT_OUTPUT


@pytest.mark.parametrize(
    "name", ["../secret.txt", "/etc/passwd", "notes.md", ".hidden.txt", "missing.txt", "", "a\\b.txt"]
)
def test_files_outside_the_folder_or_of_other_types_cannot_be_read(song, name):
    (song / ".hidden.txt").write_text("x", encoding="utf-8")
    with pytest.raises(TaggerError):
        Session(song).readable(name)


def test_a_link_that_leaves_the_folder_cannot_be_read(song, tmp_path):
    (song / "link.txt").symlink_to(tmp_path / "secret.txt")
    with pytest.raises(TaggerError):
        Session(song).readable("link.txt")


@pytest.mark.parametrize("output", ["../x.txt", "lyrics.txt", "labels.json", ".x.txt", "a/b.txt"])
def test_unsafe_output_names_are_refused(song, output):
    with pytest.raises(TaggerError):
        Session(song, output=output)


def test_a_missing_folder_or_label_file_is_refused(song):
    with pytest.raises(TaggerError):
        Session(song / "nope")
    with pytest.raises(TaggerError):
        Session(song, labels="nope.txt")


def test_first_write_keeps_one_backup_of_the_older_file(song):
    (song / "labels.txt").write_text("0.5\t0.6\told\n", encoding="utf-8")
    session = Session(song, output="labels.txt")

    session.write_labels(LABELS.encode())
    session.write_labels(LABELS.replace("夜", "世").encode())

    assert (song / "labels.txt").read_text(encoding="utf-8").startswith("1.000000\t1.400000\t世")
    assert (song / "labels.txt.bak").read_text(encoding="utf-8") == "0.5\t0.6\told\n"
    assert not list(song.glob(".tagger-*"))


def test_bad_labels_and_projects_are_not_written(song):
    session = Session(song)
    with pytest.raises(TaggerError):
        session.write_labels("1.0 1.4 夜\n".encode())
    with pytest.raises(TaggerError):
        session.write_project(b'{"format": "other"}')
    with pytest.raises(TaggerError):
        session.write_project(b"not json")
    assert not (song / DEFAULT_OUTPUT).exists()
    assert not (song / PROJECT_NAME).exists()


def test_page_is_served_without_a_token_but_data_is_not(running):
    session, port = running

    status, body, response = request(port, "GET", "/")
    assert status == 200
    assert b"Kizami Tagger" in body
    assert "default-src 'none'" in response.getheader("Content-Security-Policy")

    assert request(port, "GET", "/api/session")[0] == 403
    assert request(port, "GET", "/api/session", token="wrong")[0] == 403
    assert request(port, "GET", "/api/file?name=lyrics.txt")[0] == 403

    status, body, _ = request(port, "GET", "/api/session", token=session.token)
    assert status == 200
    assert json.loads(body)["audio"] == ["vocals.wav"]


def test_files_are_read_through_the_server(running):
    session, port = running

    status, body, _ = request(port, "GET", "/api/file?name=lyrics.txt", token=session.token)
    assert (status, body.decode("utf-8")) == (200, "夜空\n")
    assert request(port, "GET", "/api/file?name=..%2Fsecret.txt", token=session.token)[0] == 404
    assert request(port, "GET", "/api/file?name=notes.md", token=session.token)[0] == 404


def test_requests_from_another_host_or_site_are_refused(running):
    session, port = running

    other_host = {"Host": "evil.example"}
    assert request(port, "GET", "/api/session", token=session.token, headers=other_host)[0] == 403
    other_site = {"Origin": "https://evil.example"}
    status, _, _ = request(
        port, "PUT", "/api/labels", token=session.token, body=LABELS.encode(), headers=other_site
    )
    assert status == 403
    assert not (session.folder / DEFAULT_OUTPUT).exists()


def test_labels_and_project_are_saved_through_the_server(running):
    session, port = running

    status, _, _ = request(port, "PUT", "/api/labels", token=session.token, body=LABELS.encode())
    assert status == 200
    assert (session.folder / DEFAULT_OUTPUT).read_text(encoding="utf-8") == LABELS

    status, _, _ = request(port, "PUT", "/api/project", token=session.token, body=PROJECT.encode())
    assert status == 200
    assert json.loads((session.folder / PROJECT_NAME).read_text(encoding="utf-8"))["version"] == 1

    assert request(port, "PUT", "/api/labels", body=LABELS.encode())[0] == 403
    assert request(port, "PUT", "/api/labels", token=session.token, body=b"bad")[0] == 400
    assert request(port, "PUT", "/api/other", token=session.token, body=b"x")[0] == 404


def test_saved_labels_can_be_imported(running, tmp_path, capsys):
    session, port = running
    request(port, "PUT", "/api/labels", token=session.token, body=LABELS.encode())

    code = main(["import-labels", str(session.folder / DEFAULT_OUTPUT), "-o", str(tmp_path / "r.json")])

    assert code == 0
    assert "2 chunks (1 marked uncertain)" in capsys.readouterr().out


def test_tagger_command_reports_a_missing_folder(tmp_path, capsys):
    code = main(["tagger", str(tmp_path / "nope"), "--no-browser"])

    assert code == 2
    assert "not a folder" in capsys.readouterr().err
